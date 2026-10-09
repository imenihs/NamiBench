"""PlutoSDR の送信で出す波形（numpy だけの純粋な計算）。

- TG（設計Q §7 の TG）: 受信の FFT の格子の全部にトーンを1本ずつ置いた多トーン（循環バッファで繰り返す）。
  長さは FFT の点数 N にするので、各トーンは格子の上にちょうど乗り、受信の K 回の FFT のどの区間でも同じ電力になる。
  位相は Schroeder の式（φ_k = −π k² / K）にして、山と谷の比（ピークの大きさ）を小さくする
- 使う範囲は受信の使える幅（中心から ±h）で、DC（中心から δ 未満）は外す（送信の LO の漏れもそこに来る）
- 値は I・Q の int16 が交互（送信のバッファの形）
"""
from __future__ import annotations

import numpy as np


def multitone_bins(n: int, grid: int, half: int, delta: int) -> np.ndarray:
    """トーンを置く格子の番号（−n/2 … n/2−1。中心からの離れ方が δ 以上 h 以下）。"""
    k = np.arange(-(n // 2), n - n // 2, dtype=np.int64)
    d = np.abs(k) * grid
    return k[(d >= delta) & (d <= half)]


def multitone(n: int, grid: int, half: int, delta: int, peak: float) -> np.ndarray:
    """複素数の多トーン（長さ n、山の大きさ peak。振り切れを 1.0 とする）。"""
    bins = multitone_bins(n, grid, half, delta)
    spec = np.zeros(n, dtype=np.complex128)
    if bins.size:
        idx = np.arange(bins.size, dtype=np.float64)
        phase = -np.pi * idx * idx / bins.size
        spec[np.mod(bins, n)] = np.exp(1j * phase)
    x = np.fft.ifft(spec)
    m = float(np.max(np.abs(x))) if x.size else 0.0
    if m > 0:
        x = x * (peak / m)
    return x


def to_int16_iq(x: np.ndarray, full_scale: int) -> np.ndarray:
    """複素数（振り切れ 1.0）を I・Q の int16 の交互の並びへ。"""
    out = np.empty(2 * x.size, dtype=np.int16)
    out[0::2] = np.clip(np.round(x.real * full_scale), -32768, 32767).astype(np.int16)
    out[1::2] = np.clip(np.round(x.imag * full_scale), -32768, 32767).astype(np.int16)
    return out
