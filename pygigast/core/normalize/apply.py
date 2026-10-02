"""基準を当てる（設計E §6.3・§6.4）。

測った電力のフレームを、基準で割った比のフレームにする。
- 条件（イメージ除去・RBW）が合う区間だけを使う。同じ周波数を複数の区間が持つときは
  Step が最も細かい区間（同じ Step なら新しい方）を使う
- 基準点の間は dB で線形補間する。出力の帯の境目（機種のプロファイル）をまたいで補間しない。
  境目ちょうどの点は上の帯に属する
- 基準の無い点は値なし（NaN と flags["no_reference"]）。1本のトレースに比と電力を混ぜない
- 全点に当たらないときは比にせず、受けた電力のフレームをそのまま返し、理由をコードで返す
- 値の表し方: Frame の比はリニアの振幅比（types.Frame）なので √(測った電力 ÷ 基準の電力) を入れる。
  dB は 20·log10 で、10·log10(電力比) と同じ値になる
文言は画面側が決める（U-22）。ここはコードと数値だけを返す。
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Optional

import numpy as np

from ..types import Frame, Quantity, ValueKind
from .reference import Reference, ReferenceSegment

# 当てなかった理由のコード（全点に当たらないとき。設計E §6.4）
NO_REFERENCE_SELECTED = "no_reference_selected"  # 基準を選んでいない
REFERENCE_EMPTY = "reference_empty"  # 基準に区間が無い
NOT_POWER = "not_power"  # 測った値が電力でない（既に比など）
UNKNOWN_CONDITIONS = "unknown_conditions"  # フレームに掃引条件が無い
SERIAL_MISMATCH = "serial_mismatch"  # 別の機器で取った基準
MODE_MISMATCH = "mode_mismatch"  # TG の基準を S11 に当てる など
IMAGE_REJECT_MISMATCH = "image_reject_mismatch"  # イメージ除去（正確・スピード・なし）が違う（設計F §7.3）
RBW_MISMATCH = "rbw_mismatch"  # RBW が違う
OUT_OF_RANGE = "out_of_range"  # 条件は合うが、表示範囲のどの点にも基準が無い

# 点ごとの印（Frame.flags のキー。bool の配列）
FLAG_NO_REFERENCE = "no_reference"  # 基準が無い → 値なし（範囲外・帯の境目の手前など）
FLAG_REF_BAND_EDGE = "no_reference_band_edge"  # 値なしの理由が「帯の境目をまたぐ補間になる」
FLAG_REF_FINE = "ref_fine"  # 細かい: 基準の Step と格子がいまの掃引と同じ（補間なし）
FLAG_REF_COARSE = "ref_coarse"  # 粗い: 補間した・Step が違う（T13 で ±0.5dB 程度）
FLAG_REF_POWER_CYCLED = "ref_power_cycled"  # 電源の入れ直し前に取った基準点を使った

# 画面全体の精度（状態表示用）
ACCURACY_FINE = "fine"
ACCURACY_COARSE = "coarse"
ACCURACY_MIXED = "mixed"
ACCURACY_NONE = "none"


@dataclass
class NormalizeResult:
    """当てた結果。

    applied が False のとき frame は元の電力のフレーム（量の種類は電力のまま）で、reason に理由のコード。
    applied が True のとき frame は比のフレーム。out_of_range_hz に表示範囲の中の値なしの区間
    [(開始 Hz, 終了 Hz), ...] を並べる（「基準の範囲外: 4000〜4500MHz」の表示用）。
    oldest_taken_at は使った基準点のうち最も古い取得日時、age_s はそこから now までの秒。
    """

    frame: Frame
    applied: bool
    reason: Optional[str] = None
    detail: dict = field(default_factory=dict)
    reference_id: Optional[str] = None
    reference_name: Optional[str] = None
    accuracy: str = ACCURACY_NONE
    out_of_range_hz: list[tuple[int, int]] = field(default_factory=list)
    power_cycled: bool = False
    oldest_taken_at: Optional[float] = None
    age_s: Optional[float] = None

    @property
    def partial(self) -> bool:
        return self.applied and bool(self.out_of_range_hz)


def _not_applied(frame: Frame, reason: str, ref: Optional[Reference], **detail) -> NormalizeResult:
    return NormalizeResult(
        frame=frame,
        applied=False,
        reason=reason,
        detail=detail,
        reference_id=ref.ref_id if ref else None,
        reference_name=ref.name if ref else None,
    )


def _band_index(freqs: np.ndarray, edges: np.ndarray) -> np.ndarray:
    """帯の番号（境目ちょうどは上の帯。設計E §6.3）。"""
    return np.searchsorted(edges, freqs, side="right")


@dataclass
class _Merged:
    """条件の合う区間を1本の点列にまとめたもの（周波数の昇順）。"""

    f: np.ndarray  # int64
    db: np.ndarray  # 基準の電力 dB（10·log10 W）
    step: np.ndarray  # その点の区間の Step（int64）
    taken_at: np.ndarray
    connected_at: np.ndarray


def _merge_segments(segments: list[ReferenceSegment]) -> _Merged:
    """Step が細かい区間を優先して1本の点列にする（設計E §6.3）。

    優先の高い区間の範囲 [start, last] に入る、優先の低い区間の点は使わない。
    優先の高い区間の中の使えない点（NaN）も、優先の低い区間で埋めない（取り直した範囲に古い値を混ぜない）。
    """
    order = sorted(segments, key=lambda s: (s.axis.step_hz, -s.taken_at))
    covered: list[tuple[int, int]] = []
    parts_f, parts_db, parts_step, parts_t, parts_c = [], [], [], [], []
    for s in order:
        f = s.freqs()
        keep = np.ones(f.shape, dtype=bool)
        for lo, hi in covered:
            keep &= ~((f >= lo) & (f <= hi))
        covered.append((s.axis.start_hz, s.axis.last_hz))
        p = s.power_w
        with np.errstate(divide="ignore", invalid="ignore"):
            usable = keep & np.isfinite(p) & (p > 0)
            db = 10.0 * np.log10(np.where(usable, p, 1.0))
        n = int(usable.sum())
        parts_f.append(f[usable])
        parts_db.append(db[usable])
        parts_step.append(np.full(n, s.axis.step_hz, dtype=np.int64))
        parts_t.append(np.full(n, s.taken_at, dtype=np.float64))
        parts_c.append(np.full(n, s.connected_at, dtype=np.float64))
    if not parts_f:
        e = np.zeros(0)
        return _Merged(e.astype(np.int64), e, e.astype(np.int64), e, e)
    f = np.concatenate(parts_f).astype(np.int64)
    idx = np.argsort(f, kind="stable")
    return _Merged(
        f[idx],
        np.concatenate(parts_db)[idx],
        np.concatenate(parts_step)[idx],
        np.concatenate(parts_t)[idx],
        np.concatenate(parts_c)[idx],
    )


def _runs(freqs: np.ndarray, mask: np.ndarray) -> list[tuple[int, int]]:
    """mask が True の連続した点の区間を (開始 Hz, 終了 Hz) で並べる。"""
    out: list[tuple[int, int]] = []
    i, n = 0, len(mask)
    while i < n:
        if mask[i]:
            j = i
            while j + 1 < n and mask[j + 1]:
                j += 1
            out.append((int(freqs[i]), int(freqs[j])))
            i = j + 1
        else:
            i += 1
    return out


def apply_reference(
    frame: Frame,
    reference: Optional[Reference],
    *,
    serial: str,
    connected_at: float,
    now: float,
) -> NormalizeResult:
    """測った電力のフレームに基準を当てる（設計E §6.3・§6.4）。

    serial・connected_at は、いまつながっている機器のシリアル番号と接続（＝電源投入）の時刻。
    基準の区間の接続の時刻と違えば、電源の入れ直し前の基準として印を付ける（当てることはやめない）。
    """
    if reference is None:
        return _not_applied(frame, NO_REFERENCE_SELECTED, None)
    if not reference.segments:
        return _not_applied(frame, REFERENCE_EMPTY, reference)
    if frame.quantity != Quantity.POWER:
        return _not_applied(frame, NOT_POWER, reference)
    if frame.adopted is None:
        return _not_applied(frame, UNKNOWN_CONDITIONS, reference)
    if reference.serial != serial:
        return _not_applied(frame, SERIAL_MISMATCH, reference, reference_serial=reference.serial)
    a = frame.adopted
    if reference.mode != a.mode:
        return _not_applied(frame, MODE_MISMATCH, reference, reference_mode=reference.mode.value)
    used = a.image_reject_used  # ゼロスパンでは選択によらず "off"（設計F §7.1）
    segs = [s for s in reference.segments if s.image_reject == used]
    if not segs:
        have = sorted({s.image_reject for s in reference.segments})
        return _not_applied(frame, IMAGE_REJECT_MISMATCH, reference, reference_image_reject=have)
    segs = [s for s in segs if s.rbw_hz == a.rbw_hz]
    if not segs:
        have = sorted({s.rbw_hz for s in reference.segments if s.image_reject == used})
        return _not_applied(frame, RBW_MISMATCH, reference, reference_rbw_hz=have)

    m = _merge_segments(segs)
    f = frame.axis.freqs()
    npts = f.size
    edges = np.asarray(reference.band_edges_hz, dtype=np.int64)
    ref_db = np.full(npts, np.nan)
    fine = np.zeros(npts, dtype=bool)
    coarse = np.zeros(npts, dtype=bool)
    band_gap = np.zeros(npts, dtype=bool)
    cycled = np.zeros(npts, dtype=bool)
    used_t = np.full(npts, np.nan)
    n = m.f.size
    if n:
        idx = np.searchsorted(m.f, f, side="left")
        # 基準点にちょうど当たる点
        ic = np.minimum(idx, n - 1)
        exact = (idx < n) & (m.f[ic] == f)
        ref_db[exact] = m.db[ic[exact]]
        fine |= exact & (m.step[ic] == frame.axis.step_hz)
        coarse |= exact & ~fine
        cycled |= exact & (m.connected_at[ic] != connected_at)
        used_t[exact] = m.taken_at[ic[exact]]
        # 両隣の基準点の間（dB で線形補間）
        lo = idx - 1
        between = ~exact & (lo >= 0) & (idx < n)
        lo_c = np.clip(lo, 0, n - 1)
        hi_c = np.clip(idx, 0, n - 1)
        gap = m.f[hi_c] - m.f[lo_c]
        # 両隣が同じ区間の隣どうし、または区間のつなぎ目（隙間が両側の Step の大きい方以内）だけ。
        # それより離れていれば基準の無い範囲（取っていない・使えない点）
        near = gap <= np.maximum(m.step[lo_c], m.step[hi_c])
        if edges.size:
            same_band = _band_index(m.f[lo_c], edges) == _band_index(m.f[hi_c], edges)
        else:
            same_band = np.ones(npts, dtype=bool)
        ok = between & near & same_band
        band_gap = between & near & ~same_band
        with np.errstate(divide="ignore", invalid="ignore"):
            t = (f - m.f[lo_c]).astype(np.float64) / np.where(gap > 0, gap, 1).astype(np.float64)
        interp = m.db[lo_c] + t * (m.db[hi_c] - m.db[lo_c])
        ref_db[ok] = interp[ok]
        coarse |= ok
        cycled |= ok & ((m.connected_at[lo_c] != connected_at) | (m.connected_at[hi_c] != connected_at))
        used_t[ok] = np.minimum(m.taken_at[lo_c], m.taken_at[hi_c])[ok]

    has_ref = ~np.isnan(ref_db)
    in_disp = (f >= frame.display_start_hz) & (f <= frame.display_stop_hz)
    if not in_disp.any():
        in_disp = np.ones(npts, dtype=bool)
    if not (has_ref & in_disp).any():
        return _not_applied(frame, OUT_OF_RANGE, reference)

    meas = np.array(np.real(frame.values), dtype=np.float64)
    with np.errstate(divide="ignore", invalid="ignore"):
        ratio = np.sqrt(meas / np.power(10.0, np.where(has_ref, ref_db, 0.0) / 10.0))
    ratio[~has_ref] = np.nan

    flags = dict(frame.flags)
    flags[FLAG_NO_REFERENCE] = ~has_ref
    flags[FLAG_REF_BAND_EDGE] = band_gap
    flags[FLAG_REF_FINE] = fine
    flags[FLAG_REF_COARSE] = coarse
    flags[FLAG_REF_POWER_CYCLED] = cycled
    out = replace(frame, values=ratio, quantity=Quantity.RATIO, value_kind=ValueKind.REAL, flags=flags)

    used = has_ref & in_disp
    n_fine = int((fine & used).sum())
    n_coarse = int((coarse & used).sum())
    accuracy = (
        ACCURACY_FINE if n_coarse == 0 else ACCURACY_COARSE if n_fine == 0 else ACCURACY_MIXED
    )
    oldest = float(np.nanmin(used_t[used]))
    return NormalizeResult(
        frame=out,
        applied=True,
        reference_id=reference.ref_id,
        reference_name=reference.name,
        accuracy=accuracy,
        out_of_range_hz=_runs(f, ~has_ref & in_disp),
        power_cycled=bool((cycled & used).any()),
        oldest_taken_at=oldest,
        age_s=now - oldest,
    )
