"""PlutoSDR の受信の標本（int16 の I・Q の交互）の換算と振り切れの判定。driver.py（1ファイル 1000 行の決まり）から分けた。"""
from __future__ import annotations

import numpy as np


def iq_from_i16(a: np.ndarray, full_scale: float) -> np.ndarray:
    """int16 の I・Q の交互を複素数へ（振り切れが 1.0）。"""
    x = np.asarray(a, dtype=np.int16)
    if x.size % 2:
        x = x[:-1]
    f = x.astype(np.float32) / np.float32(full_scale)
    return f.view(np.complex64).astype(np.complex64)


def overload_ratio_i16(a: np.ndarray, full_scale: int) -> float:
    """I か Q が端（12 ビットの −2048・+2047）に当たった標本の割合。"""
    x = np.asarray(a, dtype=np.int16)
    n = x.size // 2
    if n == 0:
        return 0.0
    i = x[0: 2 * n: 2]
    q = x[1: 2 * n: 2]
    top, bottom = full_scale - 1, -full_scale
    hit = (i >= top) | (i <= bottom) | (q >= top) | (q <= bottom)
    return float(np.count_nonzero(hit)) / n
