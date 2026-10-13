"""イメージ除去の回の合わせ方（設計F §7.1・設計F §6 の 2・設計B §7.2）。AD の値の段階で、区間ごとに行う。

- 1回（なし。b3=1）: そのまま
- 2回（b3=0・1）: 点ごとの小さい方（イメージと機器の中の決まった偽の信号を消す。GS5 の Im-Cancel と同じ）
- 4回（b3=0・1・0・1。正確の帯の中）:
  1. 同じ b3 の2回: 低い方が雑音の床の近く（床 +3dB 以内）で、高い方がそれより 6dB を超えて高いときだけ低い方。
     それ以外は電力の平均（AD → リニアの電力で平均し、AD の値に戻す。換算は単調なので AD のまま比べてよい）。
     本体が掃引ごとにばらばらの位置に出す山（T16）は信号の無い雑音の床にだけ乗り、床より約 10dB 高いので、
     床の近くに限って低い方を採れば山を消せ、1回ごとに揺れる実在の信号は平均で低く偏らない
  2. そのあと b3=0 側と b3=1 側の小さい方（イメージを消す）

雑音の床（設計F §7.1）:
- RBW がプロファイルの image_reject.local_floor_rbw_hz（GigaSt は 180kHz）のときは「その場の床」: その画面の
  4回の点ごとの小さい方を、±local_floor_half_points 点の中央値でならした値。信号の近く（位相雑音で床が上がる所）
  でも山を見分けるため（T17）
- それ以外の RBW（30kHz・15kHz）はプロファイルの image_reject.noise_floor_ad（RBW ごとの AD の値）。30kHz は
  信号そのものが揺れ、その場の床では信号を 3〜4dB 低く読むため（T17）。床の無い RBW は常に電力の平均
- その回の値の全体の中央値は使わない（Span の大半を信号が占めると中央値が信号のレベルになり、床の条件が
  信号に当たって低く偏るため）

純粋な計算だけ。送受信・スレッドを持たない。
"""
from __future__ import annotations

from typing import Optional, Union

import numpy as np

from ...sweep.image_reject import RejectProfile

# 同じ b3 の2回で低い方を採る条件（設計F §6 の 2）
NEAR_FLOOR_DB = 3.0  # 低い方が床からこの幅の中
BURST_EXCESS_DB = 6.0  # 高い方が低い方よりこの幅を超えて高い

_AD_MAX = 0xFFFF

Floor = Union[None, float, np.ndarray]


def counts_per_db(profile: dict) -> float:
    """AD の値の 1dB あたりの count（GigaSt: 1986 count = 80dB）。"""
    return float(profile["raw"]["ad_counts_per_80db"]) / 80.0


def noise_floor_ad(profile: dict, rbw_hz: int) -> Optional[float]:
    """プロファイルにある、その RBW の雑音の床（AD の値）。無ければ None。"""
    table = (profile.get("image_reject") or {}).get("noise_floor_ad") or {}
    v = table.get(str(int(rbw_hz)))
    return None if v is None else float(v)


def _ad_to_power(ad: np.ndarray, cpd: float) -> np.ndarray:
    # AD の値をリニアの電力に（基準の 0dB はどこでもよい: 平均して戻すだけなので打ち消し合う）
    return np.power(10.0, ad / cpd / 10.0)


def _power_to_ad(p: np.ndarray, cpd: float) -> np.ndarray:
    return 10.0 * np.log10(p) * cpd


def local_floor(passes: np.ndarray, half_points: int) -> np.ndarray:
    """その場の床（設計F §7.1）: 回の点ごとの小さい方を ±half_points 点の中央値でならした値（float の AD の値）。

    区間の両端は、区間の中にある点だけで中央値を取る。
    """
    low = np.min(np.asarray(passes, dtype=np.float64), axis=0)
    n = low.size
    if n == 0 or half_points <= 0:
        return low
    w = 2 * half_points + 1
    padded = np.concatenate([np.full(half_points, np.nan), low, np.full(half_points, np.nan)])
    windows = np.lib.stride_tricks.sliding_window_view(padded, w)
    return np.nanmedian(windows, axis=1)


def combine_same_b3(a: np.ndarray, b: np.ndarray, floor_ad: Floor, cpd: float) -> np.ndarray:
    """同じ b3 の2回を点ごとに合わせる（float の AD の値で返す）。

    floor_ad は床の AD の値（数か点ごとの配列）。None（床が分からない RBW）のときは、低い方を採る条件を使わず
    常に電力の平均。
    """
    a = np.asarray(a, dtype=np.float64)
    b = np.asarray(b, dtype=np.float64)
    lo = np.minimum(a, b)
    hi = np.maximum(a, b)
    mean = _power_to_ad((_ad_to_power(a, cpd) + _ad_to_power(b, cpd)) / 2.0, cpd)
    if floor_ad is None:
        return mean
    take_low = (lo <= floor_ad + NEAR_FLOOR_DB * cpd) & (hi - lo > BURST_EXCESS_DB * cpd)
    return np.where(take_low, lo, mean)


def _to_ad(x: np.ndarray) -> np.ndarray:
    return np.clip(np.rint(x), 0, _AD_MAX).astype(np.uint16)


def combine4(passes, floor_ad: Floor, cpd: float) -> np.ndarray:
    """4回（b3=0・1・0・1 の順）を合わせた採用する AD の値（uint16）。"""
    if len(passes) != 4:
        raise ValueError(f"4回の合わせ方に {len(passes)} 回が来た")
    p0a, p1a, p0b, p1b = passes
    side0 = combine_same_b3(p0a, p0b, floor_ad, cpd)
    side1 = combine_same_b3(p1a, p1b, floor_ad, cpd)
    return _to_ad(np.minimum(side0, side1))


def floor_for(passes: np.ndarray, rbw_hz: int, profile: dict) -> Floor:
    """4回の区間の床: RBW がその場の床を使う RBW ならその場の床、それ以外はプロファイルの床（設計F §7.1）。"""
    rp = RejectProfile(profile)
    if int(rbw_hz) in rp.local_floor_rbw_hz:
        return local_floor(passes, rp.local_floor_half_points)
    return noise_floor_ad(profile, rbw_hz)


def combine(values: np.ndarray, b3: tuple[int, ...], rbw_hz: int, profile: dict) -> np.ndarray:
    """1つの区間の回 values [回, 点]（b3 の並びの順）を合わせた採用する AD の値（uint16）。"""
    v = np.asarray(values)
    if v.ndim != 2 or v.shape[0] != len(b3):
        raise ValueError(f"区間の生の値は [{len(b3)}, 点]: {v.shape}")
    if tuple(b3) == (1,):
        return v[0].astype(np.uint16)
    if tuple(b3) == (0, 1):
        return np.minimum(v[0], v[1]).astype(np.uint16)
    if tuple(b3) == (0, 1, 0, 1):
        return combine4(v, floor_for(v, rbw_hz, profile), counts_per_db(profile))
    raise ValueError(f"合わせ方の無い b3 の並び: {b3}")
