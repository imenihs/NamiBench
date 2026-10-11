"""PlutoSDR の受信の利得の補正を測る手順（設計Q §7.6）。機器の口（driver.PlutoSdrDevice）が口を握った中で呼ぶ。

つなぎ方は TX → 外付けの減衰器 → RX（利用者）。周波数ごとに:
1. 送信を SG と同じ固定の順で出す（driver._tx_tone: 減衰を最小 → LO を止める → 波形を消す → LO → 波形 → LO を動かす
   → 最後に減衰）。受信の LO はトーンから TONE_OFFSET_HZ ずらす（トーンを DC の突起から離す。送信の LO の漏れは
   トーンから 1MHz 離れている）
2. 送信の減衰を選ぶ: 帯のいちばん大きい利得でトーンが TOP_TARGET_DBFS 付近（振り切れない）になる値
3. 利得を大きい方から 1dB ずつ下げてトーンを読む（読み = dBFS − 利得）。トーンが雑音から SNR_KEEP_DB 離れなくなったら、
   送信を強める（減衰を小さくする）。強める前後の差は1つ前の利得で両方を読んで測り、読みを最初の送信のレベルにそろえる
   （送信の減衰の刻みの誤差を補正に混ぜない: 実測 −60→−40dB で +20.2dB）。設計Q §7.6 の「振り切れず雑音から 20dB 以上
   離れる送信の減衰を周波数ごとに選ぶ」を、1つの値で全部の利得をまかなえないときにも守る形
4. 雑音から SNR_MIN_DB（設計の 20dB）離れない読み（送信をいちばん強くしても）は使わない（その利得は列の端・隣の値で
   補う）。SNR_KEEP_DB（30dB）は読みの揺れを小さくするための余裕: 20dB の読みは雑音で ±0.5dB ほど揺れる（試験で確認）

送信の後片付け（止める・利用者の減衰と利得へ戻す）は呼ぶ側（driver.gain_calibration）の finally が必ず行う。
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Callable, Optional

import numpy as np

from ...types import DeviceError, SweepDiscarded

# 1回の読み: FFT 16384 点（4.096MS/s で 250Hz の格子）を 4 回の電力の平均。トーンは ±TONE_BINS の格子の和（Hann の
# 等価雑音帯域 1.5 格子で割って、格子の間に落ちても同じ値にする）
FFT_N = 16384
AVERAGES = 4
TONE_OFFSET_HZ = 500_000
TONE_BINS = 3
# 雑音の中央値を取る範囲（受信の LO からの距離。DC の突起の外・使える幅の中）と、除くトーン・漏れのまわり
FLOOR_MIN_HZ = 200_000
FLOOR_MAX_HZ = 1_350_000
FLOOR_EXCLUDE_HZ = 20_000
HANN_ENBW_BINS = 1.5
# 4 回の平均の電力（自由度 8 の χ² / 8）の中央値は平均の 0.918 倍。雑音の平均 = 中央値 / これ
MEDIAN_TO_MEAN = 0.918
# 帯のいちばん大きい利得でのトーンの狙い（dBFS）と、これを超えたら振り切れとみなす値（12 ビットの端の手前）
TOP_TARGET_DBFS = -12.0
TOP_TOLERANCE_DB = 3.0
CLIP_DBFS = -6.0
# 雑音からの距離（dB）。SNR_MIN_DB（設計Q §7.6 の 20dB）未満の読みは使わない。SNR_KEEP_DB 未満になったら送信を
# 強めて SNR_KEEP_DB + SNR_RAISE_MARGIN_DB へ戻す。トーンが見えるとみなすのは FIND_SNR_DB 以上
SNR_MIN_DB = 20.0
SNR_KEEP_DB = 30.0
SNR_RAISE_MARGIN_DB = 6.0
FIND_SNR_DB = 10.0
# 減衰を選ぶ試しの回数の上限と、見つからないときに強める刻み
ADJUST_TRIES = 8
SEARCH_STEP_DB = 20.0


class NoSignal(DeviceError):
    """送信のトーンが受信に見えない（つなぎ方・減衰器を確かめる）。"""

    def __init__(self, freq_hz: int) -> None:
        super().__init__(f"{freq_hz} Hz のトーンが見えない")
        self.freq_hz = int(freq_hz)


class TooStrong(DeviceError):
    """送信をいちばん弱くしても受信が振り切れる（減衰器が無い・小さい、または外から強い信号が入っている）。"""

    def __init__(self, freq_hz: int) -> None:
        super().__init__(f"{freq_hz} Hz で受信が振り切れる")
        self.freq_hz = int(freq_hz)


@dataclass(frozen=True)
class Reading:
    tone_dbfs: float
    peak_dbfs: float
    floor_dbfs: float
    overload: bool

    @property
    def snr_db(self) -> float:
        return self.peak_dbfs - self.floor_dbfs

    def clipped(self) -> bool:
        return self.overload or self.tone_dbfs > CLIP_DBFS


def rx_lo_for(sg_hz: int, rx_max_hz: int) -> int:
    """トーンを読む受信の LO: トーンの上（範囲の上端を超えるときは下）に TONE_OFFSET_HZ。"""
    lo = int(sg_hz) + TONE_OFFSET_HZ
    return lo if lo <= rx_max_hz else int(sg_hz) - TONE_OFFSET_HZ


def tone_reading(a: np.ndarray, full_scale: int, fs: int, tone_offset_hz: int) -> Reading:
    """int16 の I・Q の交互から、受信の LO から tone_offset_hz にあるトーンの電力（dBFS）と雑音の中央値（格子あたり）。"""
    from .driver import iq_from_i16, overload_ratio_i16

    over = overload_ratio_i16(a, full_scale) > 0.0
    iq = iq_from_i16(a, full_scale)
    k = iq.size // FFT_N
    if k < 1:
        raise SweepDiscarded("short_read", {"got": int(iq.size), "want": FFT_N})
    w = np.hanning(FFT_N + 1)[:-1].astype(np.float32)  # 周期の Hann（spectrum.py の窓と同じ形）
    x = iq[: k * FFT_N].reshape(k, FFT_N) * w
    p = np.mean(np.abs(np.fft.fft(x, axis=1)) ** 2, axis=0) / float(np.sum(w)) ** 2
    f = np.fft.fftfreq(FFT_N, 1.0 / fs)
    bin_hz = fs / FFT_N
    k0 = int(round(tone_offset_hz / bin_hz)) % FFT_N
    idx = [(k0 + d) % FFT_N for d in range(-TONE_BINS, TONE_BINS + 1)]
    af = np.abs(f)
    m = (af >= FLOOR_MIN_HZ) & (af <= FLOOR_MAX_HZ)
    for c in (tone_offset_hz, -tone_offset_hz, tone_offset_hz - 1_000_000):  # トーン・その像・送信の LO の漏れ
        m &= np.abs(f - c) > FLOOR_EXCLUDE_HZ
    floor = float(np.median(p[m])) if np.any(m) else float(np.median(p))
    # 12 ビットの量子化の雑音より下の雑音は無い（全部 0 の標本で雑音から離れていると取り違えない）
    floor = max(floor, (1.0 / full_scale) ** 2 / 6.0 * HANN_ENBW_BINS / FFT_N)
    # トーンの格子の和から、同じ数の格子に乗る雑音の分を引く（雑音に近い読みで高く出ない）
    tone = max(float(np.sum(p[idx])) - len(idx) * floor / MEDIAN_TO_MEAN, 1e-30) / HANN_ENBW_BINS
    peak = float(np.max(p[idx]))
    db = lambda v: 10.0 * np.log10(max(v, 1e-30))  # noqa: E731
    return Reading(tone_dbfs=db(tone), peak_dbfs=db(peak), floor_dbfs=db(floor), overload=over)


@dataclass
class FreqResult:
    freq_hz: int
    rx_lo_hz: int
    gain_lo_db: int
    gain_hi_db: int
    levels: dict  # 利得 → 読み（dBFS − 利得。最初の送信のレベルにそろえたもの）
    tx_atten_db: list  # 使った送信の減衰（区間ごと）
    dropped: list  # 雑音に近すぎて使わなかった利得


class _Run:
    """1回の校正の手順（機器の口を握った中）。dev は driver.PlutoSdrDevice。"""

    def __init__(self, dev, cancel: Optional[threading.Event], tick: Callable[[], None]) -> None:
        self.dev = dev
        self.cancel = cancel
        self.tick = tick
        prof = dev._profile
        self.full = int(prof["fft"].get("full_scale", 2048))
        self.fs = int(prof["fft"]["sample_rate_hz"])
        a = prof["source"]["tx_atten_db"]
        self.amin, self.amax, self.astep = float(a["min"]), float(a["max"]), float(a.get("step") or 0.25)

    def _check(self) -> None:
        if self.cancel is not None and self.cancel.is_set():
            raise SweepDiscarded("cancelled", {})
        if self.dev._tx_forbidden:
            raise SweepDiscarded("closing", {})

    def _atten(self, v: float) -> float:
        v = min(max(float(v), self.amin), self.amax)
        return round(round(v / self.astep) * self.astep, 2)

    def set_atten(self, sg_hz: int, a: float) -> None:
        self.dev._tx_atten = float(a)
        self.dev._tx_tone(int(sg_hz))  # 出していなければ固定の順で始める。出していれば減衰（と LO）だけ変える

    def read(self, rx_lo: int, gain: int, tone_offset: int) -> Reading:
        self._check()
        d = self.dev
        if d._gain_applied != int(gain) or d._auto_applied:
            if d._auto_applied or d._gain_applied is None:
                d._rx.set_attr("gain_control_mode", "manual")
                d._auto_applied = False
            d._rx.set_attr("hardwaregain", int(gain))
            d._gain_applied = int(gain)
        a = d._read_position(rx_lo, None, FFT_N * AVERAGES)
        return tone_reading(a, self.full, self.fs, tone_offset)

    def measure(self, sg_hz: int, first_atten: float) -> FreqResult:
        from .driver import gain_limits

        d = self.dev
        rx_lo = rx_lo_for(sg_hz, int(d._profile["freq"]["max_hz"]))
        tone_offset = int(sg_hz) - rx_lo
        # 利得は受信の LO の帯で受け付けられる値だけ（機器は LO の帯の外の利得を断る）: 先に LO を動かす
        if d._tuned_hz != rx_lo:
            d._rxlo.set_attr("frequency", int(rx_lo))
            d._tuned_hz = int(rx_lo)
        lo, hi = gain_limits(d._profile, [rx_lo])
        # ---- 送信の減衰を選ぶ（いちばん大きい利得で狙いの大きさ）
        atten = self._atten(first_atten)
        r = None
        for _ in range(ADJUST_TRIES):
            self.set_atten(sg_hz, atten)
            r = self.read(rx_lo, hi, tone_offset)
            if r.clipped():
                if atten <= self.amin:
                    break
                atten = self._atten(atten - SEARCH_STEP_DB)
                continue
            if r.snr_db < FIND_SNR_DB:
                if atten >= self.amax:
                    raise NoSignal(sg_hz)
                atten = self._atten(atten + SEARCH_STEP_DB)
                continue
            delta = TOP_TARGET_DBFS - r.tone_dbfs
            if abs(delta) <= TOP_TOLERANCE_DB or (delta > 0 and atten >= self.amax):
                break
            atten = self._atten(atten + delta)
        if r is not None and r.clipped():
            raise TooStrong(sg_hz)  # いちばん弱い送信でも振り切れる
        if r is None or r.snr_db < FIND_SNR_DB:
            raise NoSignal(sg_hz)
        # ---- 利得を大きい方から
        levels: dict = {}
        seg: dict[int, Reading] = {}  # いまの区間の送信で読んだ利得 → 読み
        offset = 0.0  # いまの区間の読み − 最初の区間の読み
        attens = [atten]
        dropped = []
        for g in range(int(hi), int(lo) - 1, -1):
            r = self.read(rx_lo, g, tone_offset)
            if r.snr_db < SNR_KEEP_DB and seg and atten < self.amax:
                # 強める量: 雑音からの距離を余裕込みで戻す分
                want = SNR_KEEP_DB + SNR_RAISE_MARGIN_DB - r.snr_db
                # 差を取る利得: 強めても振り切れない（狙いの大きさまで）利得のうち、いちばん大きいもの（雑音からいちばん
                # 離れた読みで差を取る。差の誤差が補正に積み重なる）。無ければ区間のいちばん下で、強める量を減らす
                fits = [x for x in seg if seg[x].tone_dbfs + want <= TOP_TARGET_DBFS]
                gb = max(fits) if fits else min(seg)
                pr = seg[gb]
                up = min(want, TOP_TARGET_DBFS - pr.tone_dbfs)
                new = self._atten(atten + up)
                if new > atten:
                    self.set_atten(sg_hz, new)
                    rb = self.read(rx_lo, gb, tone_offset)
                    if not rb.clipped() and rb.snr_db >= SNR_KEEP_DB:
                        offset += rb.tone_dbfs - pr.tone_dbfs
                        atten = new
                        attens.append(atten)
                        seg = {gb: rb}
                    else:
                        self.set_atten(sg_hz, atten)  # 強めた形が使えない: 元の送信のまま続ける
                    r = self.read(rx_lo, g, tone_offset)
            if r.snr_db >= SNR_MIN_DB and not r.clipped():
                levels[g] = (r.tone_dbfs - g) - offset
            else:
                dropped.append(g)
            seg[g] = r
            self.tick()
        return FreqResult(freq_hz=int(sg_hz), rx_lo_hz=int(rx_lo), gain_lo_db=int(lo), gain_hi_db=int(hi),
                          levels=levels, tx_atten_db=attens, dropped=dropped)


def first_atten_for(ext_atten_db: float) -> float:
    """最初に試す送信の減衰: 外付けの減衰器と合わせて 100dB 下げた弱い送信から始める（実測で送信の減衰 0dB の
    トーンは減衰器 40dB を通して −42〜−65 dB（未校正）。強すぎる送信で受信を振り切らせない。足りなければ強めていく）。"""
    return -max(0.0, 100.0 - float(ext_atten_db))
