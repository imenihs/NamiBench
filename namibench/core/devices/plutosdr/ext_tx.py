"""PlutoSDR の外部 IQ の送信（設計S §1 §4〜§6.1）: 押すスレッド・待ち行列・0 埋め・途切れの時計・クレジットの知らせ・
始める順と止める順、と標本化の速さの切り替え。

外のアプリ（GPS のエミュレータなど）が送った IQ（ci16le）を、非循環の送信バッファで DAC へ押し続ける。IQ はこの層の外に
出さない決まり（設計Q §3）の、rtl_tcp の出口に続く2つ目の例外（入る向き）。

スレッドの決まり（設計S §1 の P1）
- 送信バッファを壊すのは押すスレッドだけ（押している途中のバッファを別のスレッドが壊すと libiio の中で壊れたものを使う）
- 押すスレッドが生きている間は、libiio のコンテキストを誰も閉じない。閉じる側（機器の口の _drop・close）は
  hand_ctx でコンテキストを渡し、押すスレッドが止める順（減衰を最小 → LO を止める → バッファを壊す）を通った後に自分で閉じる
- 止めたい側は request_stop で印を付けるだけで待たない（機器スレッドが条件の錠を持ったまま来る道があるため）
- 送信の属性の書き込みは送信の錠（機器の口の _tx_lock）を持って行う。掃引の錠（_lock）は使わない（掃引の間ずっと
  握られるので、減衰の変更を掃引の終わりまで待たせないため）。push は錠を持たずに行う（待つのは DAC の速さの分だけ）
"""
from __future__ import annotations

import collections
import math
import threading
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable, Optional

import numpy as np

from ...types import DeviceDisconnected, DeviceError
from ..rtlsdr.driver import fft_at_rate
from .iio import IioError

if TYPE_CHECKING:
    from .driver import PlutoSdrDevice

# 止めた理由（プロトコルの stopped.reason と同じ語）
REASON_CLIENT = "client"
REASON_USER = "user"
REASON_DEVICE_LOST = "device_lost"
REASON_APP_EXIT = "app_exit"
REASON_STARVED = "starved"
REASON_PROTOCOL = "protocol_error"
REASON_DISABLED = "disabled"

STATE_WAITING = "waiting_data"
STATE_ON = "on"
STATE_ENDED = "ended"

# 状態（送った量・途切れ・強さ）を本体へ知らせる間隔（秒）
STATS_INTERVAL_S = 1.0


@dataclass(frozen=True)
class ExtTxSpec:
    """プロファイルの ext_tx（設計S §1 §3.1・§4。仮の値）。"""

    rate_min_hz: int
    rate_max_hz: int
    block_samples: int
    queue_blocks: int
    prefill_blocks: int
    starve_s: float
    first_data_s: float

    @classmethod
    def from_profile(cls, profile: dict) -> "ExtTxSpec":
        d = profile.get("ext_tx") or {}
        rng = d.get("sample_rate_hz") or [2_083_334, 4_096_000]
        return cls(int(rng[0]), int(rng[1]), int(d.get("block_samples", 65536)), int(d.get("queue_blocks", 8)),
                   int(d.get("prefill_blocks", 4)), float(d.get("starve_s", 1.0)), float(d.get("first_data_s", 10.0)))

    @property
    def queue_samples(self) -> int:
        return self.block_samples * self.queue_blocks


def _readback_rate(text: str) -> int:
    return int(round(float(str(text).split()[0])))


# ---------------------------------------------------------------------- 標本化の速さ（設計S §1 §3.3）

def prepare_rate(dev: "PlutoSdrDevice", fs: int) -> int:
    """標本化の速さを fs にして読み戻す（制御スレッドとは別のスレッドから呼ぶ: 掃引の錠を待つ）。

    読み戻しが fs と同じときだけ、機器の口のプロファイルの fft を fs のもの（格子は元のまま）に替え、アナログの帯域
    （受信・送信）も速さと同じにする。違えば元の速さ・帯域へ戻して読み戻した値を返す（呼ぶ側が断る）。戻り値は読み戻した値。
    このアプリが出していた送信（SG・TG）は先に止める。"""
    with dev._lock:
        if dev._ctx is None:
            raise DeviceDisconnected("開いていない")
        dev._check_tx_allowed()
        try:
            dev._tx_off()
            base = dev._ext_base_fft or dev._profile["fft"]
            dev._rx.set_attr("sampling_frequency", int(fs))
            got = _readback_rate(dev._rx.attr("sampling_frequency"))
            if got != int(fs):
                _write_rate(dev, base)
                return got
            dev._rx.set_attr("rf_bandwidth", int(fs))
            if dev._tx_rf_bw is not None:
                dev._txc.set_attr("rf_bandwidth", int(fs))
            dev._ext_base_fft = base
            dev._profile["fft"] = fft_at_rate(base, int(fs))
            return got
        except IioError as e:
            if dev._is_value_error(e):
                try:
                    _write_rate(dev, dev._ext_base_fft or dev._profile["fft"])
                except IioError:
                    pass
                raise DeviceError(str(e)) from e
            dev._drop()
            raise DeviceDisconnected(str(e)) from e


def _write_rate(dev: "PlutoSdrDevice", base_fft: dict) -> None:
    fs = int(base_fft["sample_rate_hz"])
    dev._rx.set_attr("sampling_frequency", fs)
    dev._rx.set_attr("rf_bandwidth", int(base_fft.get("rf_bandwidth_hz", fs)))
    if dev._tx_rf_bw is not None:
        dev._txc.set_attr("rf_bandwidth", int(dev._tx_rf_bw))


def restore_rate(dev: "PlutoSdrDevice") -> None:
    """元の速さ（4.096MS/s）と帯域へ戻す。機器の口のプロファイルは書き込みの成否によらず必ず元へ戻す（設計S §1 の P2:
    機器が抜けていたら、次の接続の open が元の速さを書く）。"""
    with dev._lock:
        base, dev._ext_base_fft = dev._ext_base_fft, None
        if base is None:
            return
        dev._profile["fft"] = base
        if dev._ctx is None:
            return
        try:
            _write_rate(dev, base)
        except IioError as e:
            if not dev._is_value_error(e):
                dev._drop()


# ---------------------------------------------------------------------- 押すスレッド

class ExtTxStream:
    """外部 IQ の送信1回分（設計S §1 §4・§5）。

    send(obj) はクライアントへの制御のメッセージ（credit・on）を送る口（待たない: サーバーの送りの待ち行列）。
    on_event(kind, detail) は本体への知らせ（"on"・"stats"・"end"）。押すスレッドから呼ぶので、本体は制御スレッドへ渡す。
    """

    def __init__(self, dev: "PlutoSdrDevice", lo_hz: int, atten_db: float, spec: ExtTxSpec,
                 send: Callable[[dict], None], on_event: Callable[[str, dict], None], full_scale: int = 32767) -> None:
        self.dev = dev
        self.lo_hz = int(lo_hz)
        self.spec = spec
        self.fs = int(dev._profile["fft"]["sample_rate_hz"])
        self._send = send
        self._on_event = on_event
        self.full_scale = int(full_scale)
        # 押すスレッドが使う通路（コンテキストに結びついたもの。機器の口が開き直しても、これは古い方のまま）
        self._txc, self._txlo, self._dds = dev._txc, dev._txlo, dict(dev._dds)
        self._dac, self._dac_iq = dev._dac, list(dev._dac_iq)
        self._tx_lock = dev._tx_lock
        self._cv = threading.Condition()
        self._chunks: collections.deque = collections.deque()  # int16 の I・Q の交互
        self._queued = 0  # 待ち行列の標本の数
        self._received = 0
        self._credit_total = spec.queue_samples
        self._atten = float(atten_db)
        self._atten_pending: Optional[float] = None
        self._stop_reason: Optional[str] = None
        self._stop_detail: Optional[str] = None
        self.state = STATE_WAITING
        self.consumed = 0
        self.underrun = 0
        self.last_underrun_at: Optional[float] = None
        self.power_dbfs: Optional[float] = None
        self.ready_at = time.monotonic()
        self._last_rx: Optional[float] = None
        self._buf = None
        self.close_ctx = None  # 閉じる側から渡されたコンテキスト（止める順の後に、このスレッドが閉じる）
        self.end_reason: Optional[str] = None
        self.end_detail: Optional[str] = None
        self._thread = threading.Thread(target=self._run, name="pygigast-ext-tx", daemon=True)

    # ---- ほかのスレッドから ----
    @property
    def credit_initial(self) -> int:
        return self.spec.queue_samples

    @property
    def atten_db(self) -> float:
        """利用者の減衰（入れる前の値があればその値）。"""
        p = self._atten_pending
        return float(self._atten if p is None else p)

    @property
    def alive(self) -> bool:
        return self._thread.is_alive()

    def start(self) -> None:
        self.ready_at = time.monotonic()
        self._thread.start()

    def join(self, timeout: Optional[float] = None) -> bool:
        if self._thread.ident is None:
            return True
        self._thread.join(timeout)
        return not self._thread.is_alive()

    def request_stop(self, reason: str, detail: Optional[str] = None) -> None:
        """止める印を付けるだけ（待たない）。最初に付いた理由を残す。"""
        with self._cv:
            if self._stop_reason is None:
                self._stop_reason, self._stop_detail = reason, detail
            self._cv.notify_all()

    def set_atten(self, atten_db: float) -> None:
        """利用者の減衰（押すスレッドが次の塊の前に入れる: 掃引の終わりを待たない）。"""
        with self._cv:
            self._atten_pending = float(atten_db)
            self._cv.notify_all()

    def feed(self, iq) -> bool:
        """クライアントから届いた IQ（int16 の I・Q の交互）。送ってよい標本の数を超えたら False（止める印も付ける）。"""
        a = np.frombuffer(iq, dtype="<i2") if isinstance(iq, (bytes, bytearray, memoryview)) else \
            np.asarray(iq, dtype=np.int16).ravel()
        n = a.size // 2
        with self._cv:
            if self._stop_reason is not None:
                return True  # 止める途中に届いた分は捨てる（相手の誤りではない）
            if self._received + n > self._credit_total:
                self._stop_reason, self._stop_detail = REASON_PROTOCOL, "credit_exceeded"
                self._cv.notify_all()
                return False
            self._received += n
            if n:
                self._chunks.append(a[: 2 * n])
                self._queued += n
                self._last_rx = time.monotonic()
                self._cv.notify_all()
        return True

    def stats(self) -> dict:
        return {"state": self.state, "consumed": self.consumed, "underrun": self.underrun,
                "last_underrun_at": self.last_underrun_at, "power_dbfs": self.power_dbfs}

    # ---- 押すスレッド ----
    def _atten_text(self, v: float) -> str:
        return self.dev._atten_value_text(v)

    def _write(self, ch, name: str, value) -> None:
        with self._tx_lock:
            ch.set_attr(name, value)

    def _starved_locked(self, now: float) -> bool:
        """クライアントが送ってよいのに送ってこない時間が決まりを超えた（cv を持って呼ぶ）。"""
        if self._last_rx is None:
            return now - self.ready_at >= self.spec.first_data_s
        has_credit = self._credit_total - self._received > 0
        return has_credit and now - self._last_rx >= self.spec.starve_s

    def _take(self, n: int, out: np.ndarray) -> int:
        """待ち行列の先頭から n 標本まで out へ写す（cv を持って呼ぶ）。写した標本の数。"""
        got = 0
        while got < n and self._chunks:
            c = self._chunks[0]
            k = min(c.size // 2, n - got)
            out[2 * got: 2 * (got + k)] = c[: 2 * k]
            got += k
            if k * 2 == c.size:
                self._chunks.popleft()
            else:
                self._chunks[0] = c[2 * k:]
        self._queued -= got
        return got

    def _wait_prefill(self) -> bool:
        """待ち行列に始める分の塊がたまるまで待つ。止める・途切れなら False。"""
        want = self.spec.block_samples * self.spec.prefill_blocks
        with self._cv:
            while True:
                if self._stop_reason is not None:
                    return False
                if self._queued >= want:
                    return True
                now = time.monotonic()
                if self._starved_locked(now):
                    self._stop_reason = REASON_STARVED
                    return False
                self._cv.wait(0.05)

    def _next_block(self, deadline: float, block: np.ndarray) -> Optional[int]:
        """次の塊を作る。deadline までにそろわなければ足りない分を 0 で埋める。止めるなら None。戻り値は届いた標本の数。"""
        n = self.spec.block_samples
        with self._cv:
            while True:
                if self._stop_reason is not None:
                    return None
                if self._atten_pending is not None:
                    break
                now = time.monotonic()
                if self._queued >= n or now >= deadline:
                    break
                if self._starved_locked(now):
                    self._stop_reason = REASON_STARVED
                    return None
                self._cv.wait(min(0.05, max(0.0, deadline - now)))
            if self._atten_pending is not None:
                return -1  # 先に減衰を入れる（呼ぶ側がもう一度呼ぶ）
            got = self._take(n, block)
        if got < n:
            block[2 * got:] = 0
        return got

    def _push(self, block: np.ndarray, real: int) -> None:
        self._buf.push(block)
        n = self.spec.block_samples
        self.consumed += n
        if real < n:
            self.underrun += n - real
            self.last_underrun_at = time.time()
        if real > 0:
            x = block[: 2 * real].astype(np.float64)
            p = float(np.mean(x[0::2] ** 2 + x[1::2] ** 2)) / float(self.full_scale) ** 2
            self.power_dbfs = 10.0 * math.log10(p) if p > 0 else None
        with self._cv:
            self._credit_total += real
        self._send({"type": "credit", "add": int(real), "consumed": int(self.consumed), "underrun": int(self.underrun)})

    def _run(self) -> None:
        reason: Optional[str] = None
        detail: Optional[str] = None
        try:
            self._safe_start_first_half()
            if self._wait_prefill():
                self._stream()
        except IioError as e:
            reason, detail = REASON_DEVICE_LOST, str(e)
        except Exception as e:  # noqa: BLE001 — どんな失敗でも止める順は通す
            reason, detail = REASON_DEVICE_LOST, repr(e)
        finally:
            self._stop_order()
            with self._cv:
                # 先に付いた止める理由を残す（無ければ失敗の理由: 押す・書き込みの失敗は機器が抜けた）
                if self._stop_reason is None:
                    self._stop_reason, self._stop_detail = reason or REASON_DEVICE_LOST, detail
                self.end_reason, self.end_detail = self._stop_reason, self._stop_detail
            self.state = STATE_ENDED
            self.dev._ext_finished(self)
            try:
                self._on_event("end", {"reason": self.end_reason, "detail": self.end_detail, **self.stats()})
            except Exception:  # noqa: BLE001
                pass

    def _safe_start_first_half(self) -> None:
        """始める順の前半（設計S §1 §5・設計Q §7.5）: 減衰を最小 → 送信の LO を止める → 前の波形を消す（DDS を 0）→
        送信の LO を出したい周波数に → DAC の通路を有効にし非循環のバッファを作る。"""
        amin = self.dev._atten_min_text()
        self._write(self._txc, "hardwaregain", amin)
        self._write(self._txlo, "powerdown", 1)
        with self._tx_lock:
            for ch in self._dds.values():
                ch.set_attr("scale", "0")
        self._write(self._txlo, "frequency", self.lo_hz)
        for ch in self._dac_iq:
            ch.enable()
        self._buf = self._dac.create_buffer(self.spec.block_samples, cyclic=False)

    def _stream(self) -> None:
        n = self.spec.block_samples
        t_block = n / float(self.fs)
        block = np.zeros(2 * n, dtype=np.int16)
        dac_end = time.monotonic()
        last_stats = time.monotonic()
        first = True
        while True:
            # 機器の中のバッファが空になる前（あと 1 塊分の時刻）までに次の塊をそろえる
            deadline = dac_end - t_block if not first else math.inf
            got = self._next_block(deadline, block)
            if got is None:
                return
            if got < 0:
                with self._cv:
                    v, self._atten_pending = self._atten_pending, None
                self._atten = float(v)
                if self.state == STATE_ON:
                    self._write(self._txc, "hardwaregain", self._atten_text(self._atten))
                continue
            now = time.monotonic()
            dac_end = max(dac_end, now) + t_block
            self._push(block, got)
            if first:
                # 先頭の塊を押した（DMA が流れ始める。LO は止まり減衰は最小なので出ない）→ LO を動かす → 利用者の減衰 → on
                first = False
                self._write(self._txlo, "powerdown", 0)
                self._write(self._txc, "hardwaregain", self._atten_text(self._atten))
                self.state = STATE_ON
                self._send({"type": "on", "consumed": int(self.consumed)})
                self._on_event("on", {"consumed": int(self.consumed)})
            if time.monotonic() - last_stats >= STATS_INTERVAL_S:
                last_stats = time.monotonic()
                self._on_event("stats", self.stats())

    def _stop_order(self) -> None:
        """止める順（設計S §1 §5）: 減衰を最小 → LO を止める → バッファを壊す。どれかが失敗しても残りを試す。"""
        for ch, name, value in ((self._txc, "hardwaregain", self.dev._atten_min_text()), (self._txlo, "powerdown", 1)):
            try:
                self._write(ch, name, value)
            except Exception:  # noqa: BLE001 — 抜けた機器でも残りを試す
                pass
        b, self._buf = self._buf, None
        if b is not None:
            try:
                b.destroy()
            except Exception:  # noqa: BLE001
                pass
