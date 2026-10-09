"""IQ の標本化を整数分の1に落とす（rtl_tcp の出口: PlutoSDR は 4.096MS/s で読み、相手の求める速さで送る）。

相手の求める速さが読む速さの整数分の1（4.096 → 2.048・1.024MS/s など）のときだけ従う（間引く前に窓付きの sinc の
FIR で帯域を落とす）。整数分の1でない速さは受け付けない（呼ぶ側が知らせる）。numpy だけ。
"""
from __future__ import annotations

from typing import Optional

import numpy as np

# FIR のタップ数（間引きの比 1 あたり。比が大きいほど長くする）
_TAPS_PER_FACTOR = 32


def factor_for(fs_in: int, fs_out: int) -> Optional[int]:
    """fs_in ÷ fs_out が 1 以上の整数ならその値、そうでなければ None。"""
    if fs_out <= 0 or fs_in % fs_out:
        return None
    return fs_in // fs_out


class IqDecimator:
    def __init__(self, fs_in: int, fs_out: int) -> None:
        self.fs_in = int(fs_in)
        self.fs_out = int(fs_in)
        self._taps = np.ones(1)
        self._tail = np.zeros(0, dtype=np.complex64)
        self._phase = 0
        self.set_out_rate(fs_out)

    def set_out_rate(self, fs_out: int) -> bool:
        m = factor_for(self.fs_in, int(fs_out))
        if m is None:
            return False
        self.fs_out = int(fs_out)
        self.factor = m
        if m == 1:
            self._taps = np.ones(1)
        else:
            n = _TAPS_PER_FACTOR * m + 1
            k = np.arange(n) - (n - 1) / 2
            cut = 0.45 / m  # 出力の速さの 0.45 倍（ナイキストの手前）
            h = 2 * cut * np.sinc(2 * cut * k) * np.hamming(n)
            self._taps = h / h.sum()
        self._tail = np.zeros(self._taps.size - 1, dtype=np.complex64)
        self._phase = 0
        return True

    def process(self, iq: np.ndarray) -> np.ndarray:
        x = np.asarray(iq, dtype=np.complex64)
        if self.factor == 1:
            return x
        buf = np.concatenate([self._tail, x])
        y = np.convolve(buf, self._taps, mode="valid").astype(np.complex64)
        self._tail = buf[buf.size - (self._taps.size - 1):] if self._taps.size > 1 else self._tail
        out = y[self._phase::self.factor]
        self._phase = (self._phase - y.size) % self.factor
        return out
