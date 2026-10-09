"""IQ からスペクトルを作り、位置ごとの値を継ぎ合わせる（設計Q §6.4）。numpy だけの純粋な計算。

- 窓は周期形（DFT 用）の Hann と Flat top。RBW = ENBW × fs / N
- 正規化: 格子の電力 = |X_k|² / (Σw)²。IQ は (u8 − 127.5) / 127.5 にしてから使うので、
  振り切れの正弦波（振幅 127.5）が 0dBFS（= 1.0）。CW のレベルは窓・N を変えても変わらない
- 1つの位置の値は K 回の FFT の電力の平均（重ねない K 個の区間）
- DC（チューナーの中心）から δ 未満の格子は使わない。使える幅（中心から ±h）の外も使わない
- チューナーの通過帯域の形（中心から離れるほど落ちる）を、機種のプロファイルの表で割り戻す（Passband）
- 周波数は Hz の整数の格子のまま扱う（設計A §2.1）
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

import numpy as np

# 振り切れの正弦波の振幅（8bit の符号なしで 127.5 を 0 とする）
FULL_SCALE = 127.5
# 振り切れの知らせ: 標本の I か Q が端（0 か 255）に当たる割合がこれを超えたら（設計Q §6.4）
OVERLOAD_RATIO = 0.001

# Flat top の係数（5項。一般に使われる形: SciPy の flattop と同じ）
_FLATTOP = (0.21557895, 0.41663158, 0.277263158, 0.083578947, 0.006947368)

WINDOWS = ("hann", "flattop")
# 利用者は選ばない窓（RBW の選択肢に無い）
RECT = "rect"


def window(name: str, n: int) -> np.ndarray:
    """周期形の窓（長さ n）。"""
    k = np.arange(n, dtype=np.float64)
    x = 2.0 * np.pi * k / n
    if name == "hann":
        return 0.5 - 0.5 * np.cos(x)
    if name == RECT:
        # 方形（PlutoSDR の TG だけで使う。送る多トーンは N 標本で繰り返すので、格子の上にちょうど乗り漏れない）
        return np.ones(n)
    if name == "flattop":
        a = _FLATTOP
        return a[0] - a[1] * np.cos(x) + a[2] * np.cos(2 * x) - a[3] * np.cos(3 * x) + a[4] * np.cos(4 * x)
    raise ValueError(f"知らない窓: {name}")


def enbw_bins(name: str, n: int) -> float:
    """等価雑音帯域幅（格子の数）= N·Σw² / (Σw)²。"""
    w = window(name, n)
    return float(n * np.sum(w * w) / np.sum(w) ** 2)


def rbw_hz(name: str, n: int, fs: int) -> int:
    """RBW の欄に出す値（Hz の整数に丸める）。"""
    return int(round(enbw_bins(name, n) * fs / n))


def iq_from_u8(buf: np.ndarray) -> np.ndarray:
    """8bit の符号なし（I, Q の順）を複素数へ。振り切れが振幅 1.0。"""
    b = np.asarray(buf, dtype=np.uint8)
    if b.size % 2:
        b = b[:-1]
    x = (b.astype(np.float64) - FULL_SCALE) / FULL_SCALE
    return x[0::2] + 1j * x[1::2]


def overload_ratio(buf: np.ndarray) -> float:
    """I か Q が端（0 か 255 = ±127.5）に当たった標本の割合。"""
    b = np.asarray(buf, dtype=np.uint8)
    n = b.size // 2
    if n == 0:
        return 0.0
    i = b[0 : 2 * n : 2]
    q = b[1 : 2 * n : 2]
    hit = (i == 0) | (i == 255) | (q == 0) | (q == 255)
    return float(np.count_nonzero(hit)) / n


def averaged_power(iq: np.ndarray, n: int, win: str, k: int) -> np.ndarray:
    """K 個の区間の FFT の電力の平均（振り切れの正弦波 = 1.0）。

    返す配列は長さ n で、格子 −n/2 … n/2−1 の順（中心 = 添字 n/2）。
    """
    iq = np.asarray(iq)
    if iq.size < n * k:
        raise ValueError(f"標本が足りない（{iq.size} < {n}×{k}）")
    w = window(win, n)
    seg = iq[: n * k].reshape(k, n) * w
    x = np.fft.fft(seg, axis=1)
    p = (x.real ** 2 + x.imag ** 2).mean(axis=0) / (np.sum(w) ** 2)
    return np.fft.fftshift(p)


# ---------------------------------------------------------------------- 位置（設計Q §6.4）

def half_usable_hz(fs: int, fraction: float, grid: int) -> int:
    """使える幅の半分 h（格子の倍数へ切り捨て。使える幅の外へ出さないため）。"""
    return int(math.floor(fs * fraction / 2 / grid)) * grid


def dc_delta_hz(grid: int, min_bins: int, min_hz: int) -> int:
    """DC を外す幅 δ = max(min_bins 格子, min_hz を格子へ切り上げ)。中心から δ 未満の格子を使わない。"""
    return max(min_bins * grid, -(-min_hz // grid) * grid)


def _round_to_grid(v: float, grid: int) -> int:
    return int(math.floor(v / grid + 0.5)) * grid


@dataclass(frozen=True)
class Plan:
    """1回の測定で合わせるチューナーの中心の並び。realtime は中心を動かさない形（1位置）。"""

    centers: tuple[int, ...]
    realtime: bool
    grid_hz: int
    half_hz: int
    delta_hz: int


def plan(start_hz: int, last_hz: int, grid: int, half: int, delta: int,
         fmin: int, fmax: int, fixed_center_hz: Optional[int] = None) -> Plan:
    """中心の位置を決める（設計Q §6.4）。

    - 測る点は start_hz 〜 last_hz（格子 grid。last_hz は Stop を超える最初の格子点。掃引条件モデルが
      機器の上限を超えないようにしている）。広がり E = last_hz − start_hz
    - E ≤ h − δ: リアルタイム。中心 = Start − δ。下限を下回るときは last + δ
    - それより広い: n = ceil(E/(h − 格子)) + 1 個。i 番目 = Start + round_to_grid(i·E/(n−1))。
      最後は last_hz（格子の上なので出力の格子とそろう）。丸めの誤差は位置ごとに持ち、ためない
      → 間隔は h 以下・約 h/2 以上。どの点も、どれかの中心から δ 以上 h 以下にある
    - fixed_center_hz: 中心を動かせないとき（rtl_tcp・聞く機能: 設計Q §6.4）。その位置1つだけ
    """
    if fixed_center_hz is not None:
        if (fixed_center_hz - start_hz) % grid:
            raise ValueError("中心は出力の格子に乗せる")
        return Plan((int(fixed_center_hz),), True, grid, half, delta)
    extent = last_hz - start_hz
    if extent <= half - delta:
        c = start_hz - delta
        if c < fmin:
            c = last_hz + delta
        if c < fmin or c > fmax:
            raise ValueError(f"リアルタイムの中心を範囲に置けない: {c}")
        return Plan((c,), True, grid, half, delta)
    n = -(-extent // (half - grid)) + 1
    centers = tuple(start_hz + _round_to_grid(i * extent / (n - 1), grid) for i in range(n))
    return Plan(centers, False, grid, half, delta)


class Passband:
    """チューナーの通過帯域の形: 中心からの離れ方（Hz）→ 中心に対する利得（dB。上側・下側別）。

    表の点の間は dB で直線補間し、表の外は端の値のまま。値は中心の近くで 0dB に合わせてあるので、
    中心の近くの CW のレベルは割り戻しても変わらない。
    位置ごとの電力をこの利得で割ると、中心から離れた所（特に使える幅の端）の落ち込みが戻り、
    継ぎ合わせの継ぎ目で、隣の位置の端から採った点との段差が小さくなる（設計Q §6.9 の実測で継ぎ目の段差が
    1dB を超えたため）。
    """

    def __init__(self, offset_hz, upper_db, lower_db) -> None:
        o = np.asarray(offset_hz, dtype=np.float64)
        u = np.asarray(upper_db, dtype=np.float64)
        lo = np.asarray(lower_db, dtype=np.float64)
        if o.ndim != 1 or o.size < 2 or u.shape != o.shape or lo.shape != o.shape:
            raise ValueError("通過帯域の表は同じ長さの offset_hz・upper_db・lower_db（2点以上）")
        if o[0] != 0 or np.any(np.diff(o) <= 0):
            raise ValueError("通過帯域の offset_hz は 0 から始めて増やす")
        self._o, self._u, self._l = o, u, lo
        self._cache: dict[tuple[int, int], np.ndarray] = {}

    @classmethod
    def from_profile(cls, profile: dict) -> Optional["Passband"]:
        """機種のプロファイルの fft.passband から作る。無ければ None（割り戻さない）。"""
        pb = profile.get("fft", {}).get("passband")
        if not pb:
            return None
        return cls(pb["offset_hz"], pb["upper_db"], pb["lower_db"])

    def db(self, offset_hz) -> np.ndarray:
        """符号付きの離れ方（正 = 中心より上）での利得（dB）。"""
        f = np.asarray(offset_hz, dtype=np.float64)
        a = np.abs(f)
        return np.where(f >= 0, np.interp(a, self._o, self._u), np.interp(a, self._o, self._l))

    def gain_for_bins(self, n: int, grid: int) -> np.ndarray:
        """averaged_power の並び（格子 −n/2 … n/2−1）に対するリニアの利得。"""
        key = (n, grid)
        g = self._cache.get(key)
        if g is None:
            k = np.arange(-(n // 2), n - n // 2, dtype=np.float64)
            g = 10.0 ** (self.db(k * grid) / 10.0)
            self._cache[key] = g
        return g


class Assembler:
    """位置ごとの電力を出力の格子へ継ぎ合わせる。

    各点は、中心からの離れ方が δ 以上・h 以下の位置のうち、中心に近い（帯域の端から遠い）位置の値を採る。
    採れる位置が無い点は NaN のまま（値の無い点）。passband があれば、採る前に各格子をその利得で割り戻す。
    """

    def __init__(self, start_hz: int, grid: int, points: int, half: int, delta: int,
                 passband: Optional[Passband] = None) -> None:
        self.start_hz = start_hz
        self.grid = grid
        self.points = points
        self.half = half
        self.delta = delta
        self.passband = passband
        self.values = np.full(points, np.nan, dtype=np.float64)
        self._best = np.full(points, np.inf, dtype=np.float64)
        self.source = np.full(points, -1, dtype=np.int32)
        self._pos = 0

    def add(self, center_hz: int, power: np.ndarray) -> None:
        """power は averaged_power の並び（格子 −n/2 … n/2−1）。"""
        n = power.size
        if self.passband is not None:
            power = power / self.passband.gain_for_bins(n, self.grid)
        off = center_hz - self.start_hz
        if off % self.grid:
            raise ValueError("中心が出力の格子に乗っていない")
        k = np.arange(-(n // 2), n - n // 2, dtype=np.int64)
        idx = off // self.grid + k
        d = np.abs(k) * self.grid
        ok = (idx >= 0) & (idx < self.points) & (d >= self.delta) & (d <= self.half)
        idx, d, p = idx[ok], d[ok].astype(np.float64), power[ok]
        better = d < self._best[idx]
        idx, d, p = idx[better], d[better], p[better]
        self._best[idx] = d
        self.values[idx] = p
        self.source[idx] = self._pos
        self._pos += 1

    @property
    def no_value(self) -> np.ndarray:
        return np.isnan(self.values)
