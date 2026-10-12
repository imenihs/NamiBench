"""RTL-SDR の機器の口（設計B §7.1・設計Q §6.2〜§6.6）。

measure() の中で、チューナーの中心を位置ごとに合わせ（設計Q §6.4）、IQ を読んでスペクトルにし、
出力の格子へ継ぎ合わせる。外へは「振り切れを 1.0 とするリニアの電力」（encoding "rtlsdr_power_w"）だけを返す。
IQ はこの層の外に出さない（設計Q §3）。レベルへの換算は levels.py。

実機の手順の決まり（設計Q §6.9 の実測）:
- 利得は最初の中心周波数の設定の後に入れる（前に入れると効かない）
- ppm に今と同じ値を入れて −2 が返るのは失敗ではない（dll.py で扱う）
- 中心を変えたら、バッファを初期化して最初の 16384 バイトを捨てる（捨てないと古い周波数のデータが来る）
- 読みの大きさは 16384 バイトの倍数
- USB の文字列は、開いた後は開いた機器から取る（開く前用の関数は開いている間 −3 で失敗する）
"""
from __future__ import annotations

import copy
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional, Union

import numpy as np

from ...types import (
    AdoptedSweep,
    DeviceDisconnected,
    DeviceError,
    DeviceIdentity,
    FreqAxis,
    InvalidSweep,
    Mode,
    RawSweep,
    SweepDiscarded,
)
from ..base import Device, load_profile
from . import spectrum
from .dll import READ_BLOCK, RtlSdrLib
from .levels import ENCODING

PROFILE_PATH = Path(__file__).with_name("profile.json")
PORT_PREFIX = "rtlsdr:"
RECONNECT_POLL_S = 0.5  # 設計B §7.3 と同じ間隔
# 開いた直後に合わせる周波数（利得を効かせるため、利得より先に一度合わせる: 設計Q §6.9 落とし穴①）
_FIRST_TUNE_HZ = 100_000_000
# open() の利得を省略したことを表す印（None はチューナーの自動利得なので別に要る）
_DEFAULT_GAIN = object()


def default_profile() -> dict:
    return load_profile(PROFILE_PATH)


def port_name(index: int) -> str:
    """番号付きのポートの名前。シリアル番号が全製品で共通の値でも区別できるように番号で持つ（設計Q §6.6）。"""
    return f"{PORT_PREFIX}{index}"


def parse_port(port: str) -> int:
    if not port.startswith(PORT_PREFIX):
        raise DeviceError(f"RTL-SDR のポートの名前ではない: {port}")
    return int(port[len(PORT_PREFIX):])


# ---------------------------------------------------------------------- FFT の条件

@dataclass(frozen=True)
class FftParams:
    """RBW（採用値）と機種のプロファイルから決まる FFT の条件。"""

    fs: int
    n: int
    window: str
    grid: int
    half: int
    delta: int
    averages: int

    @property
    def sample_bytes(self) -> int:
        """K 回分の標本のバイト数（I と Q で 2 バイト）。"""
        return self.averages * self.n * 2

    @property
    def read_bytes(self) -> int:
        """1回の読みの大きさ（16384 バイトの倍数へ切り上げ: 設計Q §6.9）。"""
        return -(-self.sample_bytes // READ_BLOCK) * READ_BLOCK


def grid_rate_hz(fft: dict) -> int:
    """RBW の選択肢の FFT の長さ（fft_n）を決めた標本化の速さ。格子 = この速さ ÷ fft_n（速さを変えても格子と RBW は
    変えない: 設計S §1 §3.2）。無ければいまの速さ（RTL-SDR・速さを変えていない PlutoSDR）。"""
    return int(fft.get("grid_rate_hz") or fft["sample_rate_hz"])


def fft_n_at_rate(fft: dict, base_n: int) -> Optional[int]:
    """いまの速さで、選択肢の格子（grid_rate ÷ base_n）を保つ FFT の長さ。速さが格子で割り切れなければ None
    （その RBW は使えない）。"""
    grid = grid_rate_hz(fft) // int(base_n)
    fs = int(fft["sample_rate_hz"])
    if grid <= 0 or fs % grid:
        return None
    return fs // grid


def fft_at_rate(base_fft: dict, fs: int) -> dict:
    """標本化の速さを fs にした fft の写し（外部 IQ の送信の間: 設計S §1 §3.2）。格子は元の速さのまま、アナログの帯域は
    速さと同じ。通過帯域の表は元の速さ・帯域で測ったものなので、別の速さでは当てない（生の値は「割り戻しなし」）。"""
    out = copy.deepcopy(base_fft)
    out["grid_rate_hz"] = grid_rate_hz(base_fft)
    out["sample_rate_hz"] = int(fs)
    out["rf_bandwidth_hz"] = int(fs)
    if int(fs) != grid_rate_hz(base_fft):
        out.pop("passband", None)
    return out


def fft_params(profile: dict, rbw_hz: int) -> FftParams:
    fft = profile["fft"]
    fs = int(fft["sample_rate_hz"])
    for c in profile["rbw"]["choices"]:
        if int(c["rbw_hz"]) == int(rbw_hz):
            base_n = int(c["fft_n"])
            win = str(c["window"])
            break
    else:
        raise InvalidSweep(f"RBW {rbw_hz}Hz は選択肢に無い")
    n = fft_n_at_rate(fft, base_n)
    if n is None:
        raise InvalidSweep(f"RBW {rbw_hz}Hz は標本化の速さ {fs}Hz で使えない")
    grid = fs // n
    dc = fft["dc_exclusion"]
    return FftParams(
        fs=fs, n=n, window=win, grid=grid,
        half=spectrum.half_usable_hz(fs, float(fft["usable_fraction"]), grid),
        delta=spectrum.dc_delta_hz(grid, int(dc["min_bins"]), int(dc["min_hz"])),
        averages=int(fft["averages"]),
    )


def make_plan(profile: dict, axis: FreqAxis, rbw_hz: int,
              fixed_center_hz: Optional[int] = None) -> tuple[FftParams, spectrum.Plan]:
    p = fft_params(profile, rbw_hz)
    if axis.step_hz != p.grid:
        raise InvalidSweep(f"Step {axis.step_hz}Hz が FFT の格子 {p.grid}Hz と違う")
    f = profile["freq"]
    pl = spectrum.plan(axis.start_hz, axis.last_hz, p.grid, p.half, p.delta,
                       int(f["min_hz"]), int(f["max_hz"]), fixed_center_hz)
    return p, pl


def timing_fn(profile: dict) -> Callable[..., float]:
    """掃引条件モデル（sweep_kind "fft"）の見積もり: 位置の数 ×（中心を変えてから使える値まで ＋ 読み取り）。

    リアルタイム（中心を動かさない）は読み取りだけ。
    """
    settle = float(profile["timing"]["retune_settle_s"])

    def f(axis: FreqAxis, rbw_hz: int, mode=None) -> float:
        p, pl = make_plan(profile, axis, rbw_hz)
        read_s = p.read_bytes / (2.0 * p.fs)
        if pl.realtime:
            return read_s
        return len(pl.centers) * (settle + read_s)

    return f


# ---------------------------------------------------------------------- 見つける

@dataclass(frozen=True)
class RtlSdrPortInfo:
    """一覧の1行。available が False の機器は開けない（別のアプリ・このアプリが開いている）。"""

    index: int
    port: str
    name: str
    manufacturer: str
    product: str
    serial: str
    available: bool
    shared_serial: int  # 同じシリアル番号の機器の数（2 以上なら利用者が番号で選ぶ: 設計Q §6.6）


def discover(dll_folder: Optional[Union[str, Path]] = None, lib: Optional[RtlSdrLib] = None) -> list[RtlSdrPortInfo]:
    """つながっている RTL-SDR を番号順に並べる。DLL が無ければ DllNotFound。"""
    lib = lib or RtlSdrLib.load(dll_folder)
    rows = []
    for i in range(lib.device_count()):
        try:
            m, p, s = lib.device_usb_strings(i)
            ok = True
        except DeviceError:
            m = p = s = ""
            ok = False
        rows.append((i, lib.device_name(i), m, p, s, ok))
    serials = [r[4] for r in rows if r[5]]
    return [
        RtlSdrPortInfo(index=i, port=port_name(i), name=n, manufacturer=m, product=p, serial=s,
                       available=ok, shared_serial=serials.count(s) if ok else 0)
        for i, n, m, p, s, ok in rows
    ]


def _cancelled(cancel: Optional[threading.Event]) -> bool:
    return cancel is not None and cancel.is_set()


# ---------------------------------------------------------------------- 機器の口

class RtlSdrDevice(Device):
    """RTL-SDR（RTL2832U＋R820T）。

    measure は機器スレッドだけが呼ぶ。利得・ppm の変更はほかのスレッドから呼んでよく、
    測っている間は次の measure の始めに効かせる（1回の測定の中で利得を混ぜない）。
    """

    def __init__(self, profile: Optional[dict] = None, dll_folder: Optional[Union[str, Path]] = None,
                 lib: Optional[RtlSdrLib] = None) -> None:
        self._profile = copy.deepcopy(profile if profile is not None else default_profile())
        self._dll_folder = dll_folder
        self._lib = lib
        self._h = None
        self._identity: Optional[DeviceIdentity] = None
        self._lock = threading.Lock()
        self._tuned_hz: Optional[int] = None
        self._gains: list[int] = []
        self._gain_tenth: Optional[int] = None  # None はチューナーの自動利得
        self._ppm = 0
        self._pending: dict = {}
        self.shared_serial = 1
        self.connected_at: Optional[float] = None
        # 中心を動かせないとき（rtl_tcp の相手・聞く機能: 設計Q §6.4）の中心。None なら動かす
        self.fixed_center_hz: Optional[int] = None

    # ---- 繋ぐ ----
    def _get_lib(self) -> RtlSdrLib:
        if self._lib is None:
            self._lib = RtlSdrLib.load(self._dll_folder)
        return self._lib

    def open(self, port: Optional[str] = None, serial: Optional[str] = None, ppm: int = 0,
             gain_db: object = _DEFAULT_GAIN) -> DeviceIdentity:
        """番号付きのポート（一覧から選んだ機器）か、シリアル番号（1本だけのとき）で開く。

        gain_db: 手動の利得（段の中で最も近いもの）。None はチューナーの自動利得。省略はプロファイルの既定
        """
        self.close()
        lib = self._get_lib()
        rows = discover(lib=lib)
        if port is not None:
            index = parse_port(port)
            info = next((r for r in rows if r.index == index), None)
            if info is None:
                raise DeviceDisconnected(f"{port} の機器が見つからない")
        elif serial is not None:
            same = [r for r in rows if r.available and r.serial == serial]
            if not same:
                raise DeviceDisconnected(f"シリアル番号 {serial} の機器が見つからない")
            if len(same) > 1:
                # 同じシリアルが複数: 自動では選ばない（設計Q §6.6）
                raise DeviceError(f"シリアル番号 {serial} の機器が {len(same)} 台ある。番号で選ぶ")
            info = same[0]
        else:
            raise DeviceError("port か serial のどちらかを指定する")
        if gain_db is _DEFAULT_GAIN:
            gain_db = float(self._profile["gain"]["default_db"])

        h = lib.open(info.index)
        try:
            fs = int(self._profile["fft"]["sample_rate_hz"])
            h.set_sample_rate(fs)
            f = self._profile["freq"]
            first = min(max(_FIRST_TUNE_HZ, int(f["min_hz"])), int(f["max_hz"]))
            h.set_center_freq(first)
            h.set_ppm(ppm)
            self._gains = h.tuner_gains_tenth_db()
            self._h = h
            self._apply_gain(gain_db)
            h.reset_buffer()
            _, _, usb_serial = h.usb_strings()
        except BaseException:
            self._h = None
            h.close()
            raise
        self._tuned_hz = first
        self._ppm = int(ppm)
        self.shared_serial = max(info.shared_serial, 1)
        # 機器に問い合わせた値でプロファイルを上書きする（設計Q §2.1）
        self._profile["gain"]["steps_tenth_db"] = list(self._gains)
        self._profile["tuner_type"] = h.tuner_type()
        self._identity = DeviceIdentity(model=self._profile["model"], serial=usb_serial, port=port_name(info.index))
        self.connected_at = time.time()
        return self._identity

    def _nearest_gain(self, gain_db: float) -> int:
        if not self._gains:
            raise DeviceError("利得の段を読めていない")
        want = round(float(gain_db) * 10)
        return min(self._gains, key=lambda g: (abs(g - want), g))

    def _apply_gain(self, gain_db: Optional[float]) -> None:
        if gain_db is None:
            self._h.set_auto_gain()
            self._gain_tenth = None
        else:
            g = self._nearest_gain(gain_db)
            self._h.set_manual_gain(g)
            self._gain_tenth = g

    # ---- 利得・ppm（測っている間は次の measure の始めに効かせる）----
    def set_gain(self, gain_db: Optional[float]) -> None:
        """手動の利得（dB。段の中で最も近いもの）か None（チューナーの自動利得）。"""
        self._pending["gain"] = gain_db
        self._apply_pending_if_idle()

    def set_ppm(self, ppm: int) -> None:
        self._pending["ppm"] = int(ppm)
        self._apply_pending_if_idle()

    def _apply_pending_if_idle(self) -> None:
        if self._lock.acquire(blocking=False):
            try:
                self._apply_pending()
            finally:
                self._lock.release()

    def _apply_pending(self) -> None:
        if self._h is None:
            return
        pend, self._pending = self._pending, {}
        try:
            if "ppm" in pend:
                self._h.set_ppm(pend["ppm"])
                self._ppm = pend["ppm"]
                # ppm を変えると DLL が中心を合わせ直す。次の測定で位置を合わせ直させる
                self._tuned_hz = None
            if "gain" in pend:
                self._apply_gain(pend["gain"])
                self._tuned_hz = None
        except DeviceDisconnected:
            self._drop()
            raise

    @property
    def gain_db(self) -> Optional[float]:
        """いまの手動の利得（dB）。自動利得なら None。"""
        return None if self._gain_tenth is None else self._gain_tenth / 10.0

    @property
    def gain_steps_db(self) -> list[float]:
        return [g / 10.0 for g in self._gains]

    @property
    def ppm(self) -> int:
        return self._ppm

    # ---- 機器の口 ----
    def profile(self) -> dict:
        return self._profile

    def identity(self) -> DeviceIdentity:
        if self._identity is None:
            raise DeviceDisconnected("開いていない")
        return self._identity

    @property
    def is_open(self) -> bool:
        return self._h is not None

    def _drop(self) -> None:
        """切れた: 手元の口を閉じる。再接続は reconnect で同じシリアル番号を待つ。"""
        h, self._h = self._h, None
        self._tuned_hz = None
        if h is not None:
            try:
                h.close()
            except Exception:
                pass

    def measure(self, sweep: AdoptedSweep, cancel: Optional[threading.Event] = None) -> RawSweep:
        """1回測って、出力の格子（sweep.axis）の上のリニアの電力を返す。

        - 取り消しは位置と位置の間で効く（RTL-SDR は途中で止めても機器が乱れない。GigaSt と違い
          「いまの掃引の終わり」を待たない）
        - 読みが足りないときは SweepDiscarded("short_read")、切れたら DeviceDisconnected
        """
        if Mode(sweep.mode) != Mode.SA:
            raise InvalidSweep(f"RTL-SDR はスペアナだけ（{sweep.mode}）")
        if _cancelled(cancel):
            raise SweepDiscarded("cancelled", {"sent": 0})
        with self._lock:
            if self._h is None:
                raise DeviceDisconnected("開いていない")
            self._apply_pending()
            try:
                return self._measure_locked(sweep, cancel)
            except DeviceDisconnected:
                self._drop()
                raise

    def _measure_locked(self, sweep: AdoptedSweep, cancel: Optional[threading.Event]) -> RawSweep:
        axis = sweep.axis
        fixed = self.fixed_center_hz
        p, pl = make_plan(self._profile, axis, sweep.rbw_hz, fixed)
        passband = spectrum.Passband.from_profile(self._profile)
        asm = spectrum.Assembler(axis.start_hz, p.grid, axis.points, p.half, p.delta, passband)
        started_at = time.time()
        t0 = time.monotonic()
        hits = 0.0
        max_ratio = 0.0
        retunes = 0
        # 進み具合は周波数を動かした段（位置）の数で知らせる（設計J §4.13。読み終えた位置だけを数える）
        self.report_progress(0, len(pl.centers))
        for i, c in enumerate(pl.centers):
            if i > 0 and _cancelled(cancel):
                raise SweepDiscarded("cancelled", {"positions_done": i})
            if c != self._tuned_hz:
                self._h.set_center_freq(c)
                self._h.reset_buffer()
                # 中心を変えた直後の最初の 16384 バイトは古い周波数のことがあるので捨てる（1回の読みにまとめる）
                buf = self._h.read(READ_BLOCK + p.read_bytes)[READ_BLOCK:]
                self._tuned_hz = c
                retunes += 1
            else:
                buf = self._h.read(p.read_bytes)
            if buf.size < p.sample_bytes:
                raise SweepDiscarded("short_read", {"position": i, "got": int(buf.size), "want": p.sample_bytes})
            buf = buf[: p.sample_bytes]
            r = spectrum.overload_ratio(buf)
            hits += r
            max_ratio = max(max_ratio, r)
            asm.add(c, spectrum.averaged_power(spectrum.iq_from_u8(buf), p.n, p.window, p.averages))
            self.report_progress(i + 1, len(pl.centers))
        if _cancelled(cancel):
            raise SweepDiscarded("cancelled", {"positions_done": len(pl.centers)})
        duration = time.monotonic() - t0
        no_value = asm.no_value
        extra = {
            "gain_db": self.gain_db,
            "gain_mode": "auto" if self._gain_tenth is None else "manual",
            "ppm": self._ppm,
            "window": p.window,
            "fft_n": p.n,
            "averages": p.averages,
            "sample_rate_hz": p.fs,
            "dc_delta_hz": p.delta,
            # 通過帯域の形を割り戻したか（機種のプロファイルの fft.passband: 設計Q §6.9）
            "passband_corrected": passband is not None,
            "half_usable_hz": p.half,
            "realtime": pl.realtime,
            "positions": len(pl.centers),
            "retunes": retunes,
            "centers_hz": list(pl.centers),
            "fixed_center": fixed is not None,
            # 振り切れ（設計Q §6.4）: どれかの位置で端に当たった標本が 0.1% を超えたら値は正しくない
            "overload": max_ratio > spectrum.OVERLOAD_RATIO,
            "overload_ratio_max": max_ratio,
            # 点ごとの印（Frame.flags に写す）。no_value は値の無い点（中心を動かせないときの DC ±δ など）
            "flags": {"no_value": no_value},
        }
        return RawSweep(
            adopted=sweep, raw=asm.values, encoding=ENCODING, started_at=started_at,
            duration_s=duration, transient_head_points=0, extra=extra,
        )

    # ---- 利得を合わせる（設計Q §6.5 の「利得を合わせる」）----
    def fit_gain(self, sweep: AdoptedSweep, cancel: Optional[threading.Event] = None,
                 progress: Optional[Callable[[int, int], None]] = None) -> dict:
        """いまの条件の全部の位置で振り切れない、いちばん大きい利得の段を選んで入れる。

        段を上から試し、どれかの位置で振り切れ（spectrum.OVERLOAD_RATIO を超える: 設計Q §6.4）たら1段下げる。
        1つの位置の読みは短い（バッファを初期化して最初の 16384 バイトを捨て、次の 16384 バイトだけを見る）。
        振り切れた位置を次の段で最初に見る（振り切れる段では早く次へ進める。選ぶ段は順番に依らない）。
        いちばん下の段でも振り切れるときは、いちばん下の段にして overload=True を返す。
        測っている間は機器の口を握るので、掃引はその間待つ。
        """
        if Mode(sweep.mode) != Mode.SA:
            raise InvalidSweep(f"RTL-SDR はスペアナだけ（{sweep.mode}）")
        with self._lock:
            if self._h is None:
                raise DeviceDisconnected("開いていない")
            self._apply_pending()
            try:
                return self._fit_gain_locked(sweep, cancel, progress)
            except DeviceDisconnected:
                self._drop()
                raise

    def _fit_gain_locked(self, sweep: AdoptedSweep, cancel, progress) -> dict:
        _, pl = make_plan(self._profile, sweep.axis, sweep.rbw_hz, self.fixed_center_hz)
        centers = list(pl.centers)
        steps = sorted(self._gains, reverse=True)
        if not steps:
            raise DeviceError("利得の段を読めていない")
        before = self._gain_tenth
        hot = 0
        chosen: Optional[int] = None
        ratio_at_chosen = 0.0
        tried = 0
        try:
            for k, g in enumerate(steps):
                if _cancelled(cancel):
                    raise SweepDiscarded("cancelled", {"steps_done": k})
                self._h.set_manual_gain(g)
                self._gain_tenth = g
                tried = k + 1
                worst = 0.0
                over_at: Optional[int] = None
                for j in range(len(centers)):
                    i = (hot + j) % len(centers)
                    c = centers[i]
                    if c != self._tuned_hz:
                        self._h.set_center_freq(c)
                        self._tuned_hz = c
                    # 利得を変えた後・中心を変えた後の古いデータを使わない
                    self._h.reset_buffer()
                    buf = self._h.read(2 * READ_BLOCK)[READ_BLOCK:]
                    r = spectrum.overload_ratio(buf)
                    worst = max(worst, r)
                    if r > spectrum.OVERLOAD_RATIO:
                        over_at = i
                        break
                if progress is not None:
                    progress(k + 1, len(steps))
                if over_at is None:
                    chosen = g
                    ratio_at_chosen = worst
                    break
                hot = over_at
                ratio_at_chosen = worst
        except BaseException:
            # やめた・失敗したときは試す前の利得へ戻す（黙って変えたままにしない）
            if self._h is not None:
                try:
                    self._apply_gain(None if before is None else before / 10.0)
                except Exception:  # noqa: BLE001
                    pass
            self._tuned_hz = None
            raise
        overload = chosen is None
        if chosen is None:
            chosen = steps[-1]
            self._h.set_manual_gain(chosen)
            self._gain_tenth = chosen
        # 次の測定は中心を合わせ直してから読む（試した段の残りのデータを使わない）
        self._tuned_hz = None
        return {"gain_db": chosen / 10.0, "overload": overload, "overload_ratio": ratio_at_chosen,
                "steps_tried": tried, "positions": len(centers)}

    def close(self) -> None:
        with self._lock:
            self._drop()

    # ---- 切れたら戻る（設計B §7.3。同じシリアル番号が1本だけのときに限る: 設計Q §6.6）----
    def reconnect(self, stop: Optional[threading.Event] = None, timeout_s: Optional[float] = None) -> bool:
        if self._identity is None or not self._identity.serial:
            raise DeviceError("シリアル番号が分からない機器は自動で繋ぎ直さない")
        serial = self._identity.serial
        gain = self.gain_db
        deadline = None if timeout_s is None else time.monotonic() + timeout_s
        lib = self._get_lib()
        while True:
            rows = [r for r in discover(lib=lib) if r.available and r.serial == serial]
            if len(rows) == 1:
                self.open(serial=serial, ppm=self._ppm, gain_db=gain)
                return True
            if len(rows) > 1:
                raise DeviceError(f"シリアル番号 {serial} の機器が {len(rows)} 台ある。番号で選ぶ")
            if (stop is not None and stop.wait(RECONNECT_POLL_S)) or (stop is None and time.sleep(RECONNECT_POLL_S)):
                return False
            if deadline is not None and time.monotonic() > deadline:
                return False
