"""GigaSt のゼロスパンの振幅を rtl_tcp の IQ にする（設計Q §4.3）。

GigaSt は IQ を持たないので、ゼロスパンの振幅だけを送る:
- 1点ごとの dBm（LOG 検波の値を換算したもの）をリニアの振幅に戻し、I に置く。Q = 0
- 大きさの基準: ref_dbm の振幅を振り切れ（I = 1.0 → 8bit で 255）とする。それより大きい点は振り切れに切る。
  呼び出し側は ref_dbm に画面の REF レベルを渡す想定（画面で上端に見える強さが 8bit の上端になる）
- GigaSt の点の速さ（約 7〜12k 点/秒。機器が測った実際の速さを渡す）から、外のソフトが求めた
  サンプリング速度へ直線補間で変える
- 掃引の間の途切れ（次の塊の始まりが前の塊の終わりより後）は 0 で埋める。0 は振幅 0 の意味で、
  I = 0（8bit で 128）になる

時刻の扱い
- 塊ごとに「最初の点の時刻」（秒。単調な時計）と点の速さを受け取る
- 出力は out_rate の等間隔の時刻の列で、前回の続きから作る。塊の終わりまでしか作らない
- すでに出力した時刻より前の点は捨てる（時計の揺れで塊が重なったとき）
- 何も届かない間も流れを止めないよう、fill_until(t) で t までを 0 で埋められる。
  遅れて届く塊を捨てないよう、t には「いま − 余裕」（例 0.2 秒）を渡すこと
"""
from __future__ import annotations

from typing import Optional

import numpy as np

# 途切れとみなす隙間（点の間隔の何倍を超えたら 0 で埋めるか）
GAP_FACTOR = 1.5


def dbm_to_amplitude(dbm: np.ndarray, ref_dbm: float) -> np.ndarray:
    """dBm をリニアの振幅にする。ref_dbm で 1.0、それより上は 1.0 に切る。NaN（値の無い点）は 0。"""
    d = np.asarray(dbm, dtype=np.float64)
    a = np.power(10.0, (d - ref_dbm) / 20.0)
    a = np.where(np.isfinite(a), a, 0.0)
    return np.minimum(a, 1.0)


class EnvelopeToIQ:
    """振幅の塊を、決まったサンプリング速度の IQ（I = 振幅、Q = 0）の流れにする。状態を持つ。"""

    def __init__(self, out_rate: int, ref_dbm: float = 0.0) -> None:
        if out_rate <= 0:
            raise ValueError("out_rate must be positive")
        self.out_rate = int(out_rate)
        self.ref_dbm = float(ref_dbm)
        # 次に出す標本の時刻（秒）。最初の塊が来るまでは None
        self._next_t: Optional[float] = None
        # 直前の塊の最後の点（塊をまたぐ補間に使う）: (時刻, 振幅)
        self._last: Optional[tuple[float, float]] = None
        self._last_period: float = 0.0
        # 0 で埋めた標本の合計（記録用）
        self.filled_samples = 0

    def set_out_rate(self, out_rate: int) -> None:
        """外のソフトがサンプリング速度を変えたとき。時刻の続きは保ったまま、以後の間隔を変える。"""
        if out_rate <= 0:
            raise ValueError("out_rate must be positive")
        self.out_rate = int(out_rate)

    def reset(self) -> None:
        self._next_t = None
        self._last = None

    def push(self, dbm: np.ndarray, t_start: float, point_rate: float) -> np.ndarray:
        """ゼロスパンの点の塊（dBm）を受け、出せるところまでの IQ（complex64）を返す。

        t_start は最初の点の時刻（秒）、point_rate は点の速さ（点/秒）。
        """
        if point_rate <= 0:
            raise ValueError("point_rate must be positive")
        amp = dbm_to_amplitude(dbm, self.ref_dbm)
        n = amp.size
        if n == 0:
            return np.zeros(0, dtype=np.complex64)
        period = 1.0 / point_rate
        times = t_start + np.arange(n) * period
        if self._next_t is None:
            self._next_t = float(t_start)

        # 塊をまたぐ補間: 前の塊の最後の点が隙間なく続くときだけ、先頭に足す
        gap_thr = GAP_FACTOR * max(period, self._last_period)
        if self._last is not None:
            lt, la = self._last
            gap = t_start - lt
            if 0 < gap <= gap_thr:
                times = np.concatenate(([lt], times))
                amp = np.concatenate(([la], amp))
        self._last = (float(times[-1]), float(amp[-1]))
        self._last_period = period

        end_t = float(times[-1])
        if end_t < self._next_t:
            return np.zeros(0, dtype=np.complex64)  # 出し終えた時刻より前の塊は捨てる
        k = int(np.floor((end_t - self._next_t) * self.out_rate)) + 1
        out_t = self._next_t + np.arange(k) / self.out_rate
        self._next_t = self._next_t + k / self.out_rate

        # 点のある範囲は直線補間、隙間（どの点の組にも入らない時刻）は 0
        i = np.interp(out_t, times, amp, left=0.0, right=0.0)
        idx = np.searchsorted(times, out_t, side="right")
        inside = (idx > 0) & (idx < times.size)
        # 隣り合う点の間が隙間なら 0 にする
        if times.size > 1:
            dt = np.diff(times)
            gap_after = dt > gap_thr
            in_gap = np.zeros(out_t.size, dtype=bool)
            in_gap[inside] = gap_after[idx[inside] - 1]
            i[in_gap] = 0.0
        # 最後の点ちょうどの時刻は点の値
        i[out_t == times[-1]] = amp[-1]
        before = out_t < times[0]
        self.filled_samples += int(np.count_nonzero(before))
        iq = np.zeros(k, dtype=np.complex64)
        iq.real = i.astype(np.float32)
        return iq

    def fill_until(self, t: float) -> np.ndarray:
        """t（秒）までを 0 で埋めた IQ を返す（点が届かない間も流れを止めないため）。"""
        if self._next_t is None:
            self._next_t = float(t)
            return np.zeros(0, dtype=np.complex64)
        if t <= self._next_t:
            return np.zeros(0, dtype=np.complex64)
        k = int(np.floor((t - self._next_t) * self.out_rate)) + 1
        self._next_t += k / self.out_rate
        self.filled_samples += k
        return np.zeros(k, dtype=np.complex64)
