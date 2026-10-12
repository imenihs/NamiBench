"""libiio の偽物（PlutoSDR のシミュレータ。設計Q §7: 実機が無くても全部の試験を回す）。

iio.py の IioLib と同じ口（scan・open → 文脈・機器・通路・バッファ）を持ち、受信のバッファには
「入力の信号（周波数と dBm）」と、このアプリが出した送信（SG のトーン・TG の多トーン）を混ぜた IQ を作る。
レベルの決まりは単純にしてある: 振り切れに対する dBFS = 入力の dBm ＋ 受信の利得。雑音は全帯域で −95 ＋ 利得 dBFS。
送信は「送信の口と受信の口を減衰 loss_db で直結した」とみなす（SG・TG の試験用。dut があればその通過特性を掛ける）。
数値は試験と画面の確認のためのもので、実機の値ではない。

受信の利得の誤差（設計Q §7.6 の解く問題）: gain_error(受信の LO, 設定の利得) → dB を渡すと、受信の本当の利得を
「設定の利得 + その値」にする（入力の信号・送信・前段の雑音の全部に効く）。measured_like_gain_error は実測
（2026-10-11: ref/capture/2026-10-11/pluto_gain/）の段差の形をまねたもの。adc_noise_dbfs は利得に依らない
振り切れに対する雑音（A/D の雑音のまね。低い利得でトーンが雑音に近づく形を作る）。

TG の高調波の戻り（設計Q §7.7 の解く問題）: relock_ripple に大きさ ε（本来の信号に対する振幅の比）を渡すと、受信と送信の
LO が relock_below_hz より下の間、送信を受信へ通すときに格子ごとに 1 + ε·e^{jφ} を掛ける。φ は格子ごとの無作為の位相で、
LO の周波数を書くたびに（合わせ直すたびに）引き直す。実機の「合わせ直すたびに使う幅の中の特性が無作為に変わり、電力の
平均で 1/√回数 に減る」形をまねたもの（ε 0.07 で 1回の読みの揺れ 約 0.45dB）。reads は読んだときの受信の LO の並び。

S11 のブリッジ（設計Q §7.8 の試験用）: bridge=True にすると、送信の口から受信の口までを「方向性ブリッジ」とみなす。
受信へ戻るのは、送信 × 損失 loss_db ×（被測定物の反射 Γ ＋ 方向性の漏れ）。漏れの大きさは directivity_db（反射に対する
dB）、位相は周波数で回る（leak_delay_s）。Γ は繋いだ標準器で決まる: set_standard("open") +1・"short" −1・"load" 0、
None（標準器を外した）は dut_gamma(周波数の配列) → 複素の反射（無ければオープンのまま: 何も繋いでいない口）。
本体は S11 の基準を取る間 on_reference_standard で標準器を伝え、機器の口がここへ渡す（実機は何もしない）。

外部 IQ の送信（設計S §1 §9 の試験用）: 非循環の送信バッファの push は DAC の速さで押し返す（機器の中のバッファ
kernel_buffers 個。いっぱいなら一番古いバッファが流れ終わるまで待つ）。押した標本は pushed に全部残す。
push_fail を立てると push は push_hang_s 待ってから失敗する（抜けた・時間切れ）。fail_reads は受信の読みだけを失敗させる
（属性の書き込みは通る）。sampling_frequency は rate_reject の値を断り（EINVAL）、rate_readback で読み戻す値を変えられる。
"""
from __future__ import annotations

import collections
import threading
import time
from typing import Callable, Optional

import numpy as np

from .iio import IioError

FAKE_SERIAL = "FAKEPLUTO0001"
FAKE_URI = "ip:192.168.2.1"
FAKE_USB_URI = "usb:9.9.9"

_GAIN_BANDS = ((1_300_000_000, -1, 73), (4_000_000_000, -3, 71), (6_000_000_001, -10, 62))
_EINVAL = -22
_EIO = -5


def measured_like_gain_error(lo_hz: float, gain_db: float) -> float:
    """実測の段差の形（2026-10-11）: 100MHz で 53dB 以上が約 −4dB、3.5GHz で 55dB 以上が約 +2dB。4GHz 以上は
    測っていないので、試験のために 45dB 以上を −1.5dB とした（実機の値ではない）。"""
    if lo_hz < 1_300_000_000:
        return -4.0 if gain_db >= 53 else 0.0
    if lo_hz < 4_000_000_000:
        return 2.0 if gain_db >= 55 else 0.0
    return -1.5 if gain_db >= 45 else 0.0


def _band(lo_hz: float) -> tuple[int, int]:
    for top, mn, mx in _GAIN_BANDS:
        if lo_hz < top:
            return mn, mx
    return _GAIN_BANDS[-1][1], _GAIN_BANDS[-1][2]


class FakePluto:
    """機器1台の中身（属性と送受信）。試験はこれを触って状態を作り、書き込みの記録を見る。"""

    def __init__(self, signals=None, serial: str = FAKE_SERIAL, seed: int = 1, loss_db: float = 30.0,
                 dut: Optional[Callable[[np.ndarray], np.ndarray]] = None,
                 gain_error: Optional[Callable[[float, float], float]] = None,
                 adc_noise_dbfs: Optional[float] = None, relock_ripple: Optional[float] = None,
                 relock_below_hz: float = 1_000_000_000, bridge: bool = False,
                 dut_gamma: Optional[Callable[[np.ndarray], np.ndarray]] = None, directivity_db: float = 40.0,
                 leak_delay_s: float = 1.3e-9) -> None:
        self.serial = serial
        self.bridge = bool(bridge)
        self.dut_gamma = dut_gamma  # 標準器を外したときに繋がっている被測定物の反射（周波数の配列 → 複素）
        self.directivity_db = float(directivity_db)
        self.leak_delay_s = float(leak_delay_s)
        self.standard: Optional[str] = None  # いま繋いでいる標準器（open・short・load。None は被測定物）
        self.standards: list[Optional[str]] = []  # 伝えられた標準器の並び（試験が見る）
        self.relock_ripple = relock_ripple
        self.relock_below_hz = float(relock_below_hz)
        self._ripple_phase: Optional[np.ndarray] = None  # いまの LO の組での格子ごとの位相（合わせ直すと引き直す）
        self.reads: list[float] = []
        self.gain_error = gain_error
        self.adc_noise_dbfs = adc_noise_dbfs
        self.signals: list[tuple[int, float]] = list(signals or [])
        self.loss_db = float(loss_db)
        self.dut = dut  # 周波数の配列（Hz）→ 通過の振幅比（送信を受信へ通すとき掛ける）
        self.rng = np.random.default_rng(seed)
        self.unplugged = False
        self.fail_reads = False
        # 外部 IQ の送信（非循環の push）の偽物の決まり
        self.kernel_buffers = 4
        self.push_fail = False
        self.push_hang_s = 0.0
        self.pushed: list[np.ndarray] = []  # 非循環で押した int16 の I・Q の交互（押した順）
        self.push_times: list[float] = []
        self._dac_ends: collections.deque = collections.deque()
        self.rate_reject: set[int] = set()
        self.rate_readback: dict[int, int] = {}
        self.log: list[tuple[str, str, str]] = []  # (通路, 属性, 値) の書き込み
        self.lock = threading.Lock()
        self.attrs: dict[tuple[str, str, bool, str], str] = {}
        p = ("ad9361-phy",)
        self._set_raw(p[0], "voltage0", False, "sampling_frequency", "4000000")
        self._set_raw(p[0], "voltage0", False, "rf_bandwidth", "2333332")
        self._set_raw(p[0], "voltage0", False, "gain_control_mode", "manual")
        self._set_raw(p[0], "voltage0", False, "hardwaregain", "54.000000 dB")
        self._set_raw(p[0], "voltage0", True, "hardwaregain", "-10.000000 dB")
        self._set_raw(p[0], "voltage0", True, "hardwaregain_available", "[-89.750000 0.250000 0.000000]")
        self._set_raw(p[0], "voltage0", True, "rf_bandwidth", "18000000")
        self._set_raw(p[0], "voltage0", True, "sampling_frequency", "4000000")
        self._set_raw(p[0], "altvoltage0", True, "frequency", "84699998")
        self._set_raw(p[0], "altvoltage0", True, "frequency_available", "[70000000 1 6000000000]")
        self._set_raw(p[0], "altvoltage1", True, "frequency", "2450000000")
        self._set_raw(p[0], "altvoltage1", True, "frequency_available", "[46875001 1 6000000000]")
        self._set_raw(p[0], "altvoltage1", True, "powerdown", "0")
        for i, ph in enumerate(("90000", "90000", "0", "0")):
            for k, v in (("raw", "1"), ("frequency", "3999511"), ("scale", "0.000000"), ("phase", ph)):
                self._set_raw("cf-ad9361-dds-core-lpc", f"altvoltage{i}", True, k, v)
        self.tx_buffer: Optional[np.ndarray] = None  # 送っている循環バッファ（複素数・振り切れ 1.0）

    # ---- 属性 ----
    def _set_raw(self, dev, ch, out, name, value) -> None:
        self.attrs[(dev, ch, out, name)] = str(value)

    def get(self, dev, ch, out, name) -> str:
        if (dev, ch, out, name) == ("ad9361-phy", "voltage0", False, "hardwaregain_available"):
            mn, mx = _band(self.rx_lo)
            return f"[{mn} 1 {mx}]"
        if (dev, ch, out, name) not in self.attrs:
            raise IioError("channel_attr_read", -2, f"{dev}/{ch}/{name}")
        return self.attrs[(dev, ch, out, name)]

    def put(self, dev, ch, out, name, value) -> None:
        if self.unplugged:
            raise IioError("channel_attr_write", _EIO, "unplugged")
        v = str(value)
        if dev == "ad9361-phy" and ch == "voltage0" and not out and name == "hardwaregain":
            mn, mx = _band(self.rx_lo)
            g = float(v)
            if not mn <= g <= mx:
                raise IioError("channel_attr_write", _EINVAL, "Invalid argument")
            v = f"{g:.6f} dB"
        if dev == "ad9361-phy" and ch == "voltage0" and name == "sampling_frequency":
            fs = int(float(v))
            if fs in self.rate_reject:
                raise IioError("channel_attr_write", _EINVAL, "Invalid argument")
            v = str(self.rate_readback.get(fs, fs))
            # 受信と送信は1つの速さを共有する（AD9364）
            self.attrs[(dev, ch, not out, name)] = v
        self.log.append((f"{dev}/{ch}{'_out' if out else ''}", name, str(value)))
        self.attrs[(dev, ch, out, name)] = v
        if dev == "ad9361-phy" and ch in ("altvoltage0", "altvoltage1") and out and name == "frequency":
            self._ripple_phase = None  # LO を合わせ直した: 戻る高調波の位相が変わる

    def _num(self, dev, ch, out, name) -> float:
        return float(self.attrs[(dev, ch, out, name)].split()[0])

    @property
    def rx_lo(self) -> float:
        return self._num("ad9361-phy", "altvoltage0", True, "frequency")

    @property
    def fs(self) -> float:
        return self._num("ad9361-phy", "voltage0", False, "sampling_frequency")

    def tx_writes(self) -> list[tuple[str, str, str]]:
        """送信に関わる書き込み（送信の LO・送信の減衰・DDS・送信のバッファ）。"""
        return [w for w in self.log if w[0].startswith("cf-ad9361-dds-core-lpc") or w[0] == "ad9361-phy/altvoltage1_out"
                or w[0] == "ad9361-phy/voltage0_out"]

    # ---- 外部 IQ の送信（非循環の push）----
    def dac_push(self, samples: int, iq: np.ndarray) -> None:
        """非循環の push: DAC の速さで押し返す。いっぱいなら一番古いバッファが流れ終わるまで待つ。"""
        if self.push_fail or self.unplugged:
            if self.push_hang_s > 0:
                time.sleep(self.push_hang_s)
            raise IioError("buffer_push", -110, "timed out")
        now = time.monotonic()
        q = self._dac_ends
        while q and q[0] <= now:
            q.popleft()
        if len(q) >= self.kernel_buffers:
            time.sleep(max(0.0, q[0] - now))
            q.popleft()
            now = time.monotonic()
        start = max(now, q[-1] if q else now)
        q.append(start + samples / max(self.fs, 1.0))
        self.pushed.append(np.array(iq, dtype=np.int16, copy=True))
        self.push_times.append(time.monotonic())

    def pushed_samples(self) -> np.ndarray:
        """押した標本（int16 の I・Q の交互）をつないだもの。"""
        return np.concatenate(self.pushed) if self.pushed else np.zeros(0, dtype=np.int16)

    @property
    def tx_lo_powered(self) -> bool:
        return self.attrs[("ad9361-phy", "altvoltage1", True, "powerdown")] == "0"

    @property
    def tx_atten(self) -> float:
        return self._num("ad9361-phy", "voltage0", True, "hardwaregain")

    # ---- S11 のブリッジ ----
    def set_standard(self, standard: Optional[str]) -> None:
        self.standard = standard if standard in ("open", "short", "load") else None
        self.standards.append(self.standard)

    def gamma(self, freqs: np.ndarray) -> np.ndarray:
        """いま繋いでいるものの反射（複素）。"""
        f = np.asarray(freqs, dtype=np.float64)
        one = np.ones(f.shape, dtype=np.complex128)
        if self.standard == "open":
            return one
        if self.standard == "short":
            return -one
        if self.standard == "load":
            return 0 * one
        if self.dut_gamma is not None:
            return one * np.asarray(self.dut_gamma(f), dtype=np.complex128)
        return one

    def _path(self, freqs: np.ndarray) -> Optional[np.ndarray]:
        """送信から受信までの通り方（損失 loss_db を除く複素の比）。ブリッジなら反射＋漏れ、被測定物なら dut、
        どちらも無ければ None（直結）。"""
        f = np.asarray(freqs, dtype=np.float64)
        if self.bridge:
            leak = 10 ** (-self.directivity_db / 20.0) * np.exp(2j * np.pi * f * self.leak_delay_s)
            return self.gamma(f) + leak
        if self.dut is not None:
            return np.asarray(self.dut(f))
        return None

    # ---- 受信の標本 ----
    def rx_gain(self) -> float:
        mode = self.attrs[("ad9361-phy", "voltage0", False, "gain_control_mode")]
        if mode != "manual":
            return 40.0
        return self._num("ad9361-phy", "voltage0", False, "hardwaregain")

    def samples(self, n: int) -> np.ndarray:
        """受信の n 標本（int16 の I・Q の交互）。"""
        if self.unplugged or self.fail_reads:
            raise IioError("buffer_refill", _EIO, "unplugged")
        fs = self.fs
        lo = self.rx_lo
        self.reads.append(lo)
        g = self.rx_gain()
        if self.gain_error is not None:
            g = g + float(self.gain_error(lo, g))
        t = np.arange(n) / fs
        z = np.zeros(n, dtype=np.complex128)
        for f, dbm in self.signals:
            off = f - lo
            if abs(off) < fs / 2:
                a = 10 ** ((dbm + g) / 20.0)
                z += a * np.exp(2j * np.pi * off * t + 1j * self.rng.uniform(0, 2 * np.pi))
        z += self._tx_into_rx(n, t, lo, g)
        nz = 10 ** ((-95.0 + g) / 20.0) / np.sqrt(2)
        if self.adc_noise_dbfs is not None:
            nz = float(np.hypot(nz, 10 ** (self.adc_noise_dbfs / 20.0) / np.sqrt(2)))
        z += nz * (self.rng.standard_normal(n) + 1j * self.rng.standard_normal(n))
        x = np.clip(np.round(z * 2048.0), -2048, 2047)
        out = np.empty(2 * n, dtype=np.int16)
        out[0::2] = x.real.astype(np.int16)
        out[1::2] = x.imag.astype(np.int16)
        return out

    def _tx_level_scale(self) -> float:
        atten = self._num("ad9361-phy", "voltage0", True, "hardwaregain")
        return 10 ** ((atten - self.loss_db) / 20.0)

    def _relock_response(self, m: int, rx_lo: float, tx_lo: float) -> Optional[np.ndarray]:
        """戻る高調波を足した格子ごとの通り方（relock_ripple が無い・LO が relock_below_hz 以上なら None）。"""
        if not self.relock_ripple or rx_lo >= self.relock_below_hz or tx_lo >= self.relock_below_hz:
            return None
        if self._ripple_phase is None or self._ripple_phase.size != m:
            self._ripple_phase = self.rng.uniform(0, 2 * np.pi, m)
        return 1.0 + float(self.relock_ripple) * np.exp(1j * self._ripple_phase)

    def _tx_into_rx(self, n: int, t: np.ndarray, rx_lo: float, gain: float) -> np.ndarray:
        if self.attrs[("ad9361-phy", "altvoltage1", True, "powerdown")] != "0":
            return np.zeros(n, dtype=np.complex128)
        tx_lo = self._num("ad9361-phy", "altvoltage1", True, "frequency")
        k = self._tx_level_scale() * 10 ** (gain / 20.0)
        d = "cf-ad9361-dds-core-lpc"
        if self.tx_buffer is not None:
            x = self.tx_buffer
            m = x.size
            base = np.resize(x, n)
            h = self._relock_response(m, rx_lo, tx_lo)
            path = self._path(tx_lo + np.fft.fftfreq(m, 1.0 / self.fs))
            if path is not None or h is not None:
                spec = np.fft.fft(x)
                if path is not None:
                    spec = spec * path
                if h is not None:
                    spec = spec * h
                base = np.resize(np.fft.ifft(spec), n)
            return k * base * np.exp(2j * np.pi * (tx_lo - rx_lo) * t)
        s = float(self.attrs[(d, "altvoltage0", True, "scale")])
        if s <= 0:
            return np.zeros(n, dtype=np.complex128)
        f = float(self.attrs[(d, "altvoltage0", True, "frequency")])
        off = tx_lo + f - rx_lo
        if abs(off) >= self.fs / 2:
            return np.zeros(n, dtype=np.complex128)
        a = k * s
        path = self._path(np.array([tx_lo + f]))
        if path is not None:
            a = a * float(np.abs(path[0]))
        return a * np.exp(2j * np.pi * off * t)


class _Ctx:
    def __init__(self, dev: FakePluto, uri: str) -> None:
        self.dev = dev
        self.uri = uri
        self.closed = False

    @property
    def is_open(self) -> bool:
        return not self.closed

    def close(self) -> None:
        self.closed = True

    def set_timeout(self, ms: int) -> None:
        pass

    def attr(self, name: str) -> Optional[str]:
        return {"hw_serial": self.dev.serial, "hw_model": "Analog Devices PlutoSDR Rev.B (Z7010-AD9364)",
                "fw_version": "v0.39-fake", "uri": self.uri}.get(name)

    def device(self, name: str) -> "_Dev":
        if name not in ("ad9361-phy", "cf-ad9361-lpc", "cf-ad9361-dds-core-lpc"):
            raise IioError("context_find_device", 0, name)
        return _Dev(self, name)


class _Dev:
    def __init__(self, ctx: _Ctx, name: str) -> None:
        self.ctx = ctx
        self.name = name

    def channel(self, name: str, output: bool) -> "_Ch":
        return _Ch(self, name, bool(output))

    def attr(self, name: str) -> str:
        return self.ctx.dev.get(self.name, "", False, name)

    def set_attr(self, name: str, value) -> None:
        self.ctx.dev.put(self.name, "", False, name, value)

    def create_buffer(self, samples: int, cyclic: bool = False) -> "_Buf":
        if self.ctx.dev.unplugged:
            raise IioError("device_create_buffer", 0, "unplugged")
        return _Buf(self, int(samples), bool(cyclic))


class _Ch:
    def __init__(self, dev: _Dev, name: str, out: bool) -> None:
        self.dev = dev
        self.name = name
        self.out = out

    def attr(self, name: str) -> str:
        return self.dev.ctx.dev.get(self.dev.name, self.name, self.out, name)

    def set_attr(self, name: str, value) -> None:
        self.dev.ctx.dev.put(self.dev.name, self.name, self.out, name, value)

    def enable(self) -> None:
        pass

    def disable(self) -> None:
        pass


class _Buf:
    def __init__(self, dev: _Dev, samples: int, cyclic: bool) -> None:
        self.dev = dev
        self.samples = samples
        self.cyclic = cyclic
        self._alive = True

    def refill(self) -> np.ndarray:
        return self.dev.ctx.dev.samples(self.samples)

    def push(self, iq: np.ndarray) -> None:
        pl = self.dev.ctx.dev
        if not self.cyclic:
            pl.dac_push(self.samples, iq)
        a = np.asarray(iq, dtype=np.int16).astype(np.float64)
        pl.tx_buffer = (a[0::2] + 1j * a[1::2]) / 32767.0
        pl.log.append(("cf-ad9361-dds-core-lpc/buffer_out", "push", str(self.samples)))

    def destroy(self) -> None:
        if self._alive and self.dev.name == "cf-ad9361-dds-core-lpc":
            self.dev.ctx.dev.tx_buffer = None
            self.dev.ctx.dev.log.append(("cf-ad9361-dds-core-lpc/buffer_out", "destroy", ""))
        self._alive = False


class FakeIioLib:
    """IioLib の偽物。devices は URI → FakePluto（既定は ip:192.168.2.1 と USB の両方に同じ1台）。"""

    def __init__(self, pluto: Optional[FakePluto] = None, uris=(FAKE_URI, FAKE_USB_URI)) -> None:
        self.pluto = pluto or FakePluto(signals=[(84_700_000, -40.0), (90_000_000, -55.0)])
        self.uris = tuple(uris)
        self.opened: list[str] = []

    def version(self) -> tuple[int, int, str]:
        return 0, 26, "fake"

    def strerror(self, code: int) -> str:
        return f"error {code}"

    def scan(self, backend: str = "usb") -> list[tuple[str, str]]:
        if self.pluto.unplugged:
            return []
        return [(u, f"0456:b673 (Analog Devices Inc. PlutoSDR (ADALM-PLUTO)), serial={self.pluto.serial}")
                for u in self.uris if u.startswith("usb:") and backend == "usb"]

    def open(self, uri: str, timeout_ms: int = 3000) -> _Ctx:
        if self.pluto.unplugged or uri not in self.uris:
            raise IioError("create_context_from_uri", 0, uri)
        self.opened.append(uri)
        return _Ctx(self.pluto, uri)

    def set_standard(self, standard: Optional[str]) -> None:
        """S11 の標準器を繋いだことにする（機器の口の on_reference_standard から。実機の libiio には無い口）。"""
        self.pluto.set_standard(standard)

    def reachable(self, uri: str) -> bool:
        return not self.pluto.unplugged and uri in self.uris
