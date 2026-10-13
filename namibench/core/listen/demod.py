"""マーカーの位置を聞く: 複素のベースバンドを 48kHz の音にする（設計Q §6.8）。

入力は IQ（complex64。サンプリング速度が分かっていること）と、聞きたい信号の位置
（チューナーの中心からのずれ Hz。聞いている間はチューナーの中心をマーカーから 300kHz ずらすので、
その分をここで数値で戻す）。出力は 48kHz の float32 の音（振り切れを ±1.0 とする）。鳴らすのは画面の側。

処理の順（どれも状態を持ち、塊の境目で音が途切れたり鳴ったりしない）
1. 数値の発振器で聞きたい信号を 0Hz へ動かす（位相は塊をまたいで続ける）
2. 間引きのフィルタ（窓関数の sinc の FIR）で中間の速さ（狭帯域は約 48kHz 以上、FM 放送は約 240kHz 以上）へ落とす
3. 通過帯のフィルタ（チャンネルの幅。SSB は片側だけを通す複素のフィルタ）
4. 復調: AM は包絡線、FM は隣り合う標本の位相差、USB・LSB は実部
5. 音の帯域のフィルタと間引き。FM 放送はディエンファシス 50µs、AM は直流を除く
6. 48kHz へ速さを変える（窓関数の sinc の補間）

音の大きさ（自動の利得は持たない。信号の強さがそのまま音の大きさになる）
- AM: 搬送波の振幅 A・変調度 m の正弦の変調で、音の振幅 ≒ A·m
- FM: 最大の周波数偏移（狭帯域 2.5kHz・放送 75kHz）で音の振幅 1.0
- USB・LSB: 振幅 A の正弦（搬送波から 300〜3000Hz 離れた位置）で音の振幅 ≒ A

窓関数・FIR の設計の式（Blackman 窓の遷移幅 ≒ 5.5/N、阻止域 約 74dB）は一般知識。
"""
from __future__ import annotations

import enum
import math
from dataclasses import dataclass

import numpy as np

AUDIO_RATE = 48_000
# 聞いている間のチューナーの中心とマーカーの周波数のずれ（設計Q §6.8。DC の突起と局発の漏れを避ける）
LISTEN_TUNER_OFFSET_HZ = 300_000
# FM 放送のディエンファシスの時定数（設計Q §6.8）
DEEMPHASIS_TAU_S = 50e-6


class Mode(str, enum.Enum):
    AM = "am"
    FM_NARROW = "fm_narrow"
    FM_BROADCAST = "fm_broadcast"
    USB = "usb"
    LSB = "lsb"


@dataclass(frozen=True)
class ModeSpec:
    """復調の方式ごとの幅（設計Q §6.8）。"""

    channel_hz: float  # 通過帯の片側の幅（0Hz から）
    audio_hz: float  # 音の帯域
    min_if_rate: float  # 中間の速さの下限
    deviation_hz: float = 0.0  # FM の最大の周波数偏移
    deemphasis: bool = False
    ssb_low_hz: float = 0.0  # SSB の通過帯（搬送波からの離れ）
    ssb_high_hz: float = 0.0


MODE_SPECS: dict[Mode, ModeSpec] = {
    Mode.AM: ModeSpec(channel_hz=5_000, audio_hz=5_000, min_if_rate=48_000),
    # 狭帯域 FM: チャンネルの幅 12.5kHz（片側 6.25kHz）・偏移 2.5kHz（12.5kHz 間隔の一般的な値。一般知識）
    Mode.FM_NARROW: ModeSpec(channel_hz=6_250, audio_hz=5_000, min_if_rate=48_000, deviation_hz=2_500),
    # FM 放送: チャンネルの幅 200kHz（片側 100kHz）・偏移 75kHz（一般知識）
    Mode.FM_BROADCAST: ModeSpec(
        channel_hz=100_000, audio_hz=15_000, min_if_rate=240_000, deviation_hz=75_000, deemphasis=True
    ),
    Mode.USB: ModeSpec(channel_hz=3_000, audio_hz=3_000, min_if_rate=48_000, ssb_low_hz=300, ssb_high_hz=3_000),
    Mode.LSB: ModeSpec(channel_hz=3_000, audio_hz=3_000, min_if_rate=48_000, ssb_low_hz=300, ssb_high_hz=3_000),
}

# 通過帯のフィルタの遷移幅（Hz）。SSB は反対側の側波帯を落とすため狭くする
_CHANNEL_TRANSITION_HZ = {
    Mode.AM: 1_500.0,
    Mode.FM_NARROW: 1_500.0,
    Mode.FM_BROADCAST: 20_000.0,
    Mode.USB: 400.0,
    Mode.LSB: 400.0,
}


# ---- フィルタの設計 ----


def lowpass_taps(cutoff_hz: float, transition_hz: float, fs: float) -> np.ndarray:
    """窓関数の sinc（Blackman）の低域通過。直流の利得 1。タップ数は奇数。"""
    n = int(math.ceil(5.5 * fs / max(transition_hz, 1e-9)))
    n = max(n | 1, 3)
    m = np.arange(n) - (n - 1) / 2
    fc = cutoff_hz / fs
    h = 2 * fc * np.sinc(2 * fc * m) * np.blackman(n)
    return h / h.sum()


def bandpass_complex_taps(low_hz: float, high_hz: float, transition_hz: float, fs: float) -> np.ndarray:
    """low_hz〜high_hz（負の周波数も可）だけを通す複素の FIR。低域通過を中心へずらして作る。"""
    center = (low_hz + high_hz) / 2
    half = abs(high_hz - low_hz) / 2
    lp = lowpass_taps(half, transition_hz, fs)
    m = np.arange(lp.size) - (lp.size - 1) / 2
    return lp * np.exp(2j * np.pi * center / fs * m)


# ---- 状態を持つ部品 ----


class _Nco:
    """数値の発振器で周波数をずらす。位相は塊をまたいで続ける。"""

    def __init__(self, shift_hz: float, fs: float) -> None:
        self.fs = fs
        self.shift_hz = shift_hz
        self._phase = 0.0

    def process(self, x: np.ndarray) -> np.ndarray:
        n = x.size
        w = 2 * np.pi * self.shift_hz / self.fs
        ph = self._phase + w * np.arange(n)
        self._phase = float((self._phase + w * n) % (2 * np.pi))
        return x * np.exp(1j * ph).astype(np.complex64)


class _FirDecimator:
    """FIR を掛けて D 個に1個を取り出す。出す標本だけを計算する。塊の境目は前の塊の末尾で続ける。"""

    def __init__(self, taps: np.ndarray, decim: int, dtype) -> None:
        self.h = np.asarray(taps)[::-1].astype(np.complex64 if np.iscomplexobj(taps) else np.float32)
        self.decim = int(decim)
        self.dtype = dtype
        self._hist = np.zeros(self.h.size - 1, dtype=dtype)
        self._skip = 0

    def process(self, x: np.ndarray) -> np.ndarray:
        buf = np.concatenate((self._hist, x.astype(self.dtype, copy=False)))
        L = self.h.size
        w_count = buf.size - L + 1
        if w_count <= self._skip:
            self._hist = buf[-(L - 1):] if L > 1 else buf[:0]
            self._skip -= w_count
            return np.zeros(0, dtype=np.result_type(self.dtype, self.h.dtype))
        D = self.decim
        s = self._skip
        n_out = (w_count - 1 - s) // D + 1
        # 多相に分けて計算する: y[m] = Σ_r Σ_q h'[qD+r]·buf[s + r + (m+q)D]（h' は反転したタップ）
        y = np.zeros(n_out, dtype=np.result_type(self.dtype, self.h.dtype))
        for r in range(min(D, L)):
            g = self.h[r::D]
            u = buf[s + r::D][: n_out + g.size - 1]
            y += np.convolve(u, g[::-1], mode="valid")
        self._skip = self._skip + n_out * self.decim - w_count
        self._hist = buf[-(L - 1):] if L > 1 else buf[:0]
        return y


class _Fir:
    """間引かない FIR（np.convolve）。塊の境目は前の塊の末尾で続ける。"""

    def __init__(self, taps: np.ndarray, dtype) -> None:
        self.h = np.asarray(taps).astype(np.complex64 if np.iscomplexobj(taps) else np.float32)
        self._hist = np.zeros(self.h.size - 1, dtype=dtype)
        self.dtype = dtype

    def process(self, x: np.ndarray) -> np.ndarray:
        if x.size == 0:
            # np.convolve の valid は短い方に合わせて入れ替えるので、空の塊を渡さない
            return np.zeros(0, dtype=np.result_type(self.dtype, self.h.dtype))
        buf = np.concatenate((self._hist, x.astype(self.dtype, copy=False)))
        y = np.convolve(buf, self.h, mode="valid")
        self._hist = buf[buf.size - (self.h.size - 1):]
        return y


class _OnePole:
    """y[n] = b·y[n−1] + u[n] を塊ごとに閉じた式で計算する（Python の1標本ずつの繰り返しを避ける）。"""

    CHUNK = 64

    def __init__(self, b: float) -> None:
        self.b = float(b)
        self._y = 0.0
        k = np.arange(self.CHUNK)
        self._bpow = self.b ** (k + 1)  # b^(n+1)
        self._binv = self.b ** (-k)  # b^(-n)

    def process(self, u: np.ndarray) -> np.ndarray:
        u = np.asarray(u, dtype=np.float64)
        y = np.empty_like(u)
        for s in range(0, u.size, self.CHUNK):
            c = u[s:s + self.CHUNK]
            n = c.size
            # y[n] = b^(n+1)·y_prev + Σ_{k≤n} b^(n−k)·u[k]
            acc = np.cumsum(c * self._binv[:n]) * (self._bpow[:n] / self.b)
            yc = self._bpow[:n] * self._y + acc
            y[s:s + n] = yc
            self._y = float(yc[-1])
        return y


class _DcBlock:
    """直流を除く: y[n] = x[n] − x[n−1] + R·y[n−1]（遮断 約 fc Hz）。"""

    def __init__(self, fs: float, fc_hz: float = 20.0) -> None:
        self._pole = _OnePole(math.exp(-2 * math.pi * fc_hz / fs))
        self._prev = 0.0

    def process(self, x: np.ndarray) -> np.ndarray:
        x = np.asarray(x, dtype=np.float64)
        if x.size == 0:
            return x
        d = np.diff(np.concatenate(([self._prev], x)))
        self._prev = float(x[-1])
        return self._pole.process(d)


class _Deemphasis:
    """1 次の低域通過（時定数 tau）。直流の利得 1。"""

    def __init__(self, fs: float, tau_s: float) -> None:
        b = math.exp(-1.0 / (fs * tau_s))
        self._g = 1.0 - b
        self._pole = _OnePole(b)

    def process(self, x: np.ndarray) -> np.ndarray:
        return self._pole.process(np.asarray(x, dtype=np.float64) * self._g)


class _Resampler:
    """任意の比の速さの変換（窓関数の sinc の補間）。出力の時刻は塊をまたいで続ける。"""

    HALF = 16

    def __init__(self, in_rate: float, out_rate: float) -> None:
        self.step = in_rate / out_rate
        # 遮断（入力の標本の単位）。下げるときは出力の Nyquist の 0.9 倍
        self.fc = 0.5 * min(1.0, out_rate / in_rate) * 0.9
        H = self.HALF
        self._offsets = np.arange(-H + 1, H + 1)
        self._buf = np.zeros(2 * H, dtype=np.float64)
        self._t = float(H)  # 次に出す時刻（_buf の先頭からの位置）

    def _kernel(self, d: np.ndarray) -> np.ndarray:
        H = self.HALF
        win = 0.42 + 0.5 * np.cos(np.pi * d / H) + 0.08 * np.cos(2 * np.pi * d / H)  # Blackman（中心 0）
        win = np.where(np.abs(d) < H, win, 0.0)
        return 2 * self.fc * np.sinc(2 * self.fc * d) * win

    def process(self, x: np.ndarray) -> np.ndarray:
        H = self.HALF
        buf = np.concatenate((self._buf, np.asarray(x, dtype=np.float64)))
        max_t = buf.size - H - 1
        if max_t < self._t:
            self._buf = buf
            return np.zeros(0, dtype=np.float64)
        n = int(math.floor((max_t - self._t) / self.step)) + 1
        t = self._t + np.arange(n) * self.step
        i0 = np.floor(t).astype(np.int64)
        frac = t - i0
        idx = i0[:, None] + self._offsets[None, :]
        w = self._kernel(frac[:, None] - self._offsets[None, :])
        w /= w.sum(axis=1, keepdims=True)
        y = (buf[idx] * w).sum(axis=1)
        next_t = self._t + n * self.step
        drop = max(0, int(math.floor(next_t)) - H + 1)
        self._buf = buf[drop:]
        self._t = next_t - drop
        return y


# ---- 復調器 ----


class Demodulator:
    """IQ の塊を 48kHz の音にする。状態を持ち、塊をいくつに分けても同じ音になる。

    offset_hz は、聞きたい信号の周波数 − チューナーの中心（IQ の 0Hz）。
    """

    def __init__(self, mode: Mode | str, in_rate: float, offset_hz: float = 0.0) -> None:
        self.mode = Mode(mode)
        self.in_rate = float(in_rate)
        spec = MODE_SPECS[self.mode]
        self.spec = spec
        self._nco = _Nco(-offset_hz, self.in_rate)

        # 2. 中間の速さへの間引き
        d1 = max(1, int(self.in_rate // spec.min_if_rate))
        self.if_rate = self.in_rate / d1
        ch = min(spec.channel_hz, 0.45 * self.if_rate)
        if d1 > 1:
            # 通過は ch まで、if_rate − ch から先は折り返してチャンネルに入るので落とす
            tr = self.if_rate - 2 * ch
            taps = lowpass_taps(self.if_rate / 2, tr, self.in_rate)
            self._dec1: _FirDecimator | None = _FirDecimator(taps, d1, np.complex64)
        else:
            self._dec1 = None

        # 3. 通過帯のフィルタ
        tr_ch = _CHANNEL_TRANSITION_HZ[self.mode]
        if self.mode in (Mode.USB, Mode.LSB):
            lo, hi = spec.ssb_low_hz, spec.ssb_high_hz
            if self.mode is Mode.LSB:
                lo, hi = -hi, -lo
            ctaps = bandpass_complex_taps(lo, hi, tr_ch, self.if_rate)
        else:
            ctaps = lowpass_taps(ch, tr_ch, self.if_rate)
        self._chan = _Fir(ctaps, np.complex64)

        # 4. FM の位相差の前の標本
        self._prev = np.complex64(0)

        # 5. 音の帯域と間引き
        d2 = max(1, int(self.if_rate // AUDIO_RATE))
        self.audio_mid_rate = self.if_rate / d2
        audio = min(spec.audio_hz, 0.4 * self.audio_mid_rate)
        stop = min(audio * 1.3, AUDIO_RATE / 2, self.audio_mid_rate - audio)
        self._audio = _FirDecimator(lowpass_taps((audio + stop) / 2, stop - audio, self.if_rate), d2, np.float32)
        self._deemph = _Deemphasis(self.audio_mid_rate, DEEMPHASIS_TAU_S) if spec.deemphasis else None
        self._dc = _DcBlock(self.audio_mid_rate) if self.mode is Mode.AM else None

        # 6. 48kHz へ
        self._resampler = None if self.audio_mid_rate == AUDIO_RATE else _Resampler(self.audio_mid_rate, AUDIO_RATE)

    def set_offset(self, offset_hz: float) -> None:
        """聞く位置を変える（マーカーを動かしたとき）。発振器の位相は続けるので音は途切れない。"""
        self._nco.shift_hz = -offset_hz

    def process(self, iq: np.ndarray) -> np.ndarray:
        x = np.asarray(iq, dtype=np.complex64).ravel()
        if x.size == 0:
            return np.zeros(0, dtype=np.float32)
        x = self._nco.process(x)
        if self._dec1 is not None:
            x = self._dec1.process(x)
        x = self._chan.process(x)
        a = self._demod(x)
        a = self._audio.process(a.astype(np.float32))
        if self._deemph is not None:
            a = self._deemph.process(a)
        if self._dc is not None:
            a = self._dc.process(a)
        if self._resampler is not None:
            a = self._resampler.process(a)
        return np.asarray(a, dtype=np.float32)

    def _demod(self, x: np.ndarray) -> np.ndarray:
        if x.size == 0:
            return np.zeros(0, dtype=np.float32)
        if self.mode is Mode.AM:
            return np.abs(x)
        if self.mode in (Mode.USB, Mode.LSB):
            return x.real
        # FM: 隣り合う標本の位相差 → 周波数 → 最大の偏移で 1.0
        prev = np.concatenate(([self._prev], x[:-1]))
        self._prev = x[-1]
        dphi = np.angle(x * np.conj(prev))
        return dphi * (self.if_rate / (2 * np.pi * self.spec.deviation_hz))
