"""イメージキャンセルの4回の測定の合わせ方（設計F §6・設計B §7.2）。

1画面は b3=0 → b3=1 → b3=0 → b3=1 の4回。点ごとに次の順で合わせる（AD の値の段階で行う）:

1. 同じ b3 の2回: 低い方が雑音の床の近く（床 +3dB 以内）で、高い方がそれより 6dB を超えて高いときだけ低い方。
   それ以外は電力の平均（AD → リニアの電力で平均し、AD の値に戻す。換算は単調なので AD のまま比べてよい）。
   本体が掃引ごとにばらばらの位置に出す山（T16）は信号の無い雑音の床にだけ乗り、床より約 10dB 高いので、
   床の近くに限って低い方を採れば山を消せ、1回ごとに揺れる実在の信号は平均で低く偏らない
2. そのあと b3=0 側と b3=1 側の小さい方（イメージを消す。2回のときと同じ）

雑音の床は機種のプロファイル `image_cancel.noise_floor_ad`（RBW ごとの AD の値）。その回の値の中央値は
使わない（Span の大半を信号が占めると中央値が信号のレベルになり、床の条件が信号に当たって低く偏るため）。

純粋な計算だけ。送受信・スレッドを持たない。
"""
from __future__ import annotations

from typing import Optional

import numpy as np

# 1画面の4回の b3 の並び。同じ b3 の2回の間を空け、続けて出る山を同じ組で拾いにくくする
PASS_B3 = (0, 1, 0, 1)
PASS_NAMES = ("b3_0a", "b3_1a", "b3_0b", "b3_1b")

# 同じ b3 の2回で低い方を採る条件（設計F §6 の 2）
NEAR_FLOOR_DB = 3.0  # 低い方が床からこの幅の中
BURST_EXCESS_DB = 6.0  # 高い方が低い方よりこの幅を超えて高い

# マーカーの読み値の注意（設計G §3.3 の点の印）。イメージキャンセル ON（4回）で、採った値が雑音の床の近く。
# 4回の合わせ方では雑音の床が2回の min より約 2dB 低く見えるので、雑音のレベルを読むときは
# イメージキャンセルを OFF にするか平均を使うよう知らせる（設計F §6 の 2 の最後）
NOTE_IC_NOISE_FLOOR = "ic_noise_floor"
NOTE_MARGIN_DB = 3.0  # 床からこの幅の中なら注意を付ける（低い方を採る条件と同じ幅）

_AD_MAX = 0xFFFF


def counts_per_db(profile: dict) -> float:
    """AD の値の 1dB あたりの count（GigaSt: 1986 count = 80dB）。"""
    return float(profile["raw"]["ad_counts_per_80db"]) / 80.0


def noise_floor_ad(profile: dict, rbw_hz: int) -> Optional[float]:
    """プロファイルにある、その RBW の雑音の床（AD の値）。無ければ None。"""
    table = (profile.get("image_cancel") or {}).get("noise_floor_ad") or {}
    v = table.get(str(int(rbw_hz)))
    return None if v is None else float(v)


def _ad_to_power(ad: np.ndarray, cpd: float) -> np.ndarray:
    # AD の値をリニアの電力に（基準の 0dB はどこでもよい: 平均して戻すだけなので打ち消し合う）
    return np.power(10.0, ad / cpd / 10.0)


def _power_to_ad(p: np.ndarray, cpd: float) -> np.ndarray:
    return 10.0 * np.log10(p) * cpd


def combine_same_b3(a: np.ndarray, b: np.ndarray, floor_ad: Optional[float], cpd: float) -> np.ndarray:
    """同じ b3 の2回を点ごとに合わせる（float の AD の値で返す）。

    floor_ad が None（床が分からない RBW）のときは、低い方を採る条件を使わず常に電力の平均。
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


def combine(passes: list[np.ndarray] | np.ndarray, floor_ad: Optional[float], cpd: float) -> np.ndarray:
    """4回（PASS_B3 の順）を合わせた採用する AD の値（uint16）。"""
    if len(passes) != len(PASS_B3):
        raise ValueError(f"イメージキャンセルは {len(PASS_B3)} 回: {len(passes)} 回が来た")
    p0a, p1a, p0b, p1b = passes
    side0 = combine_same_b3(p0a, p0b, floor_ad, cpd)
    side1 = combine_same_b3(p1a, p1b, floor_ad, cpd)
    out = np.minimum(side0, side1)
    return np.clip(np.rint(out), 0, _AD_MAX).astype(np.uint16)


def near_floor(values_ad: np.ndarray, floor_ad: Optional[float], cpd: float) -> Optional[np.ndarray]:
    """採った値が雑音の床の近く（床 + NOTE_MARGIN_DB 以下）の点。床が分からなければ None。"""
    if floor_ad is None:
        return None
    return np.asarray(values_ad, dtype=np.float64) <= floor_ad + NOTE_MARGIN_DB * cpd


def note_flags(raw, profile: dict) -> dict[str, np.ndarray]:
    """Frame の点の印に足すもの（読み値の注意: 設計G §3.3）。4回のイメージキャンセルの GigaSt の値だけ。

    raw は RawSweep。raw_passes（4回の生の値）が無い掃引（OFF・NF・前の2回の形のファイル）は何も付けない。
    """
    if getattr(raw, "raw_passes", None) is None:
        return {}
    floor = noise_floor_ad(profile, raw.adopted.rbw_hz)
    if floor is None:
        return {}
    return {NOTE_IC_NOISE_FLOOR: near_floor(raw.raw, floor, counts_per_db(profile))}
