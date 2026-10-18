"""イメージ除去の区間ごとの生の値の持ち方（設計F §7.3・設計H §7.1 の raw_layout "image_reject_segments"）。

区間の並び（regions）は [{"point0", "points", "passes", "b3"}, …]。point0 は保存した点（Start〜Stop で切った点）の
番号。保存ファイル（HDF5）では区間ごとのデータセット raw_segments/<番号> [掃引, 回数, 点数] に持ち、属性に
point0・points・passes・b3 を書く。メモリと記録中の作業ファイル（SQLite）では、1掃引の残りの回を区間の順に
[回数, 点数] を平らにして続けた1本（raw2 [掃引, 全部の回 × 点の和]）で持つ。

純粋な計算だけ。
"""
from __future__ import annotations

from typing import Optional, Sequence

import numpy as np

from ..types import RawSegment


def region(point0: int, points: int, b3: Sequence[int]) -> dict:
    return {"point0": int(point0), "points": int(points), "passes": len(b3), "b3": [int(x) for x in b3]}


def regions_of(segments: Sequence[RawSegment], n: int) -> list[dict]:
    """RawSweep の区間を、保存する点数 n（Stop まで）で切った並び。点の無くなった区間は落とす。"""
    out = []
    for s in segments:
        pts = min(s.points, n - s.point0)
        if pts > 0:
            out.append(region(s.point0, pts, s.b3))
    return out


def width(regions: Sequence[dict]) -> int:
    """1掃引の残りの回の値の数（全部の区間の 回数 × 点数 の和）。"""
    return sum(int(r["passes"]) * int(r["points"]) for r in regions)


def flatten(segments: Sequence[RawSegment], regions: Sequence[dict]) -> np.ndarray:
    """区間の生の値を、regions（regions_of で切ったもの）の順と点数で平らに続けた1本。"""
    by_p0 = {s.point0: s for s in segments}
    parts = [np.asarray(by_p0[r["point0"]].values)[:, : r["points"]].reshape(-1) for r in regions]
    return np.concatenate(parts) if parts else np.zeros(0, dtype=np.uint16)


def split(flat: np.ndarray, regions: Sequence[dict]) -> list[np.ndarray]:
    """平らな1本（[..., width]）を区間ごとの [..., 回数, 点数] に分ける。"""
    a = np.asarray(flat)
    lead = a.shape[:-1]
    if a.shape[-1] != width(regions):
        raise ValueError(f"区間の生の値の数 {a.shape[-1]} が区間の並び {width(regions)} と一致しません")
    out, pos = [], 0
    for r in regions:
        k, n = int(r["passes"]), int(r["points"])
        out.append(a[..., pos: pos + k * n].reshape(*lead, k, n))
        pos += k * n
    return out


def join(parts: Sequence[np.ndarray]) -> np.ndarray:
    """split の逆: 区間ごとの [掃引, 回数, 点数] を [掃引, width] に続ける。"""
    if not parts:
        raise ValueError("区間がありません")
    rows = parts[0].shape[0]
    return np.concatenate([np.asarray(p).reshape(rows, -1) for p in parts], axis=1)


def clip(regions: Sequence[dict], i0: int, i1: int) -> list[dict]:
    """点の番号 [i0, i1) に入る部分の区間の並び（point0 は i0 からの番号に付け直す）。"""
    out = []
    for r in regions:
        a = max(int(r["point0"]), i0)
        b = min(int(r["point0"]) + int(r["points"]), i1)
        if b > a:
            out.append(region(a - i0, b - a, r["b3"]))
    return out


def clip_flat(flat: np.ndarray, regions: Sequence[dict], i0: int, i1: int) -> tuple[np.ndarray, list[dict]]:
    """平らな残りの回 [掃引, width] のうち、点の番号 [i0, i1) の部分（と、その部分の区間の並び）。"""
    a2 = np.asarray(flat)
    keep = []
    for r, p in zip(regions, split(a2, regions)):
        a = max(int(r["point0"]), i0)
        b = min(int(r["point0"]) + int(r["points"]), i1)
        if b > a:
            keep.append(p[:, :, a - int(r["point0"]): b - int(r["point0"])])
    sub = clip(regions, i0, i1)
    if not keep:
        return np.zeros((a2.shape[0], 0), dtype=a2.dtype), sub
    return join(keep), sub


def to_segments(flat_row: np.ndarray, regions: Sequence[dict]) -> tuple[RawSegment, ...]:
    """1掃引の平らな残りの回を RawSegment の並びに戻す（読み戻し）。"""
    return tuple(RawSegment(point0=int(r["point0"]), b3=tuple(int(x) for x in r["b3"]), values=v)
                 for r, v in zip(regions, split(flat_row, regions)))


def check(regions: Optional[Sequence[dict]], points: int) -> None:
    """区間の並びが点 [0, points) を順に重ならずに覆うか（保存・読み戻しの検査）。"""
    if not regions:
        raise ValueError("区間がありません")
    pos = 0
    for r in regions:
        if int(r["point0"]) != pos or int(r["points"]) < 1 or int(r["passes"]) != len(r["b3"]):
            raise ValueError(f"区間の並びが点を順に覆いません: {list(regions)}")
        pos += int(r["points"])
    if pos != points:
        raise ValueError(f"区間の点数の和 {pos} が点数 {points} と一致しません")
