"""イメージ除去の3つの選択と、帯で分けた区間（設計F §7.1・§7.2）。純粋な計算だけ。

- 正確: 場所の変わる山の出る帯（機種のプロファイル image_reject.burst_bands_hz）の中は
  image_reject.accurate_in_band_passes 回（GigaSt は 4回）、帯の外は 2回。表示範囲の点を帯の境目で区間に分ける
  （点はどれか1つの区間に入る。境目の点は右の区間）
- スピード: どこも 2回（1つの区間）
- なし: b3=1 の1回（1つの区間）
- 送る b3 の並びは 0,1,0,1 の頭から（2回 = 0,1）。1回は b3=1（校正ファイルのレベルに合う: 設計B §7.2）

前の版の覚え書き・プリセット・ファイル・基準の image_cancel（真偽）は読むときに置き換える（設計F §7.3）:
true → "fast"、false → "off"。
"""
from __future__ import annotations

from typing import Optional

import numpy as np

from ..types import FreqAxis

ACCURATE = "accurate"
FAST = "fast"
OFF = "off"
# 画面に並べる順（正確 → スピード → なし: 設計F §7.4）
CHOICES = (ACCURATE, FAST, OFF)

# 帯の外の回数と、スピードの回数（設計F §7.1: 2 固定）
TWO_PASSES = 2
# 送る b3 の並びの元（設計F §7.1。同じ b3 の2回の間を空け、続けて出る山を同じ組で拾いにくくする）
B3_ORDER = (0, 1, 0, 1)
# 1回のときの b3（いまの OFF と同じ: 設計B §7.2）
ONE_PASS_B3 = (1,)


def check(choice: object) -> str:
    """選択の値を確かめる（3つのどれかでなければ ValueError）。"""
    if choice not in CHOICES:
        raise ValueError(f"image_reject は {CHOICES} のどれか: {choice!r}")
    return str(choice)


def from_legacy(image_cancel: object) -> str:
    """前の版の image_cancel（真偽）の読み替え（設計F §7.3）。"""
    return FAST if bool(image_cancel) else OFF


def read_choice(d: Optional[dict], key: str = "image_reject", legacy_key: str = "image_cancel") -> Optional[str]:
    """覚え書き・プリセット・ファイルの辞書からイメージ除去の選択を読む。

    新しいキーがあればその値（知らない値は ValueError）。無ければ前の版の image_cancel を読み替える。
    どちらも無ければ None（呼ぶ側が初期値を使う）。
    """
    if not d:
        return None
    if d.get(key) is not None:
        return check(d[key])
    if legacy_key in d and d[legacy_key] is not None:
        return from_legacy(d[legacy_key])
    return None


def b3_order(passes: int) -> tuple[int, ...]:
    """回数から送る b3 の並び（1回は b3=1、2回・4回は 0,1,0,1 の頭から）。"""
    if passes == 1:
        return ONE_PASS_B3
    if passes in (2, 4):
        return B3_ORDER[:passes]
    raise ValueError(f"回数は 1・2・4 のどれか: {passes}")


class RejectProfile:
    """機種のプロファイルの image_reject（設計F §7.1）。無い機種は帯なし・初期値 "off"。"""

    def __init__(self, profile: dict) -> None:
        r = profile.get("image_reject") or {}
        self.default = check(r.get("default", OFF))
        bands = []
        for lo, hi in r.get("burst_bands_hz", []):
            lo, hi = int(lo), int(hi)
            if not lo < hi:
                raise ValueError(f"image_reject.burst_bands_hz の帯は 下限 < 上限: {lo}, {hi}")
            bands.append((lo, hi))
        self.bands: tuple[tuple[int, int], ...] = tuple(sorted(bands))
        self.in_band_passes = int(r.get("accurate_in_band_passes", 4))
        b3_order(self.in_band_passes)  # 送れる回数か
        self.local_floor_rbw_hz = frozenset(int(x) for x in r.get("local_floor_rbw_hz", []))
        self.local_floor_half_points = int(r.get("local_floor_half_points", 10))


def in_bands(freqs: np.ndarray, bands) -> np.ndarray:
    """各点が帯の中か（下限を含み上限を含まない: 境目の点は右の区間）。"""
    f = np.asarray(freqs, dtype=np.int64)
    inside = np.zeros(f.shape, dtype=bool)
    for lo, hi in bands:
        inside |= (f >= lo) & (f < hi)
    return inside


def split_points(axis: FreqAxis, bands) -> list[tuple[int, int, bool]]:
    """表示範囲の点を帯の境目で区間に分ける: [(先頭の点, 点数, 帯の中か), …]（周波数の順）。"""
    inside = in_bands(axis.freqs(), bands)
    # 帯の中・外が変わる点（区間の先頭）
    starts = [0] + (np.flatnonzero(inside[1:] != inside[:-1]) + 1).tolist()
    ends = starts[1:] + [axis.points]
    return [(s, e - s, bool(inside[s])) for s, e in zip(starts, ends)]


def passes_by_region(choice: str, axis: FreqAxis, rp: RejectProfile) -> list[tuple[int, int, int, Optional[bool]]]:
    """選択ごとの区間と回数: [(先頭の点, 点数, 回数, 帯の中か), …]。

    正確は帯で分ける（帯の外 2回・帯の中 rp.in_band_passes 回）。スピード・なしは1つの区間（帯の中かは None）。
    """
    if choice == OFF:
        return [(0, axis.points, 1, None)]
    if choice == FAST:
        return [(0, axis.points, TWO_PASSES, None)]
    check(choice)
    return [(p0, n, rp.in_band_passes if inside else TWO_PASSES, inside)
            for p0, n, inside in split_points(axis, rp.bands)]
