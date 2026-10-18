"""S11（反射）のスカラーの基準と、リターンロス・SWR（設計E §6.5）。

基準 = オープンとショートの振幅（√電力）の平均の2乗。オープンとショートは反射の位相が逆なので、
平均すると方向性結合器の方向性による波打ちが打ち消し合う（スカラーの反射測定で一般的な方法）。
当てるのは TG と同じ apply_reference で、比（振幅）がそのまま |Γ| になる。
"""
from __future__ import annotations

from typing import Sequence

import numpy as np

from ..types import Frame, Mode
from .reference import (
    KIND_FINE,
    ReferenceBuildError,
    ReferenceSegment,
    _frame_conditions,
    average_power,
    frames_gain_table,
)


def s11_segment_from_frames(
    open_frames: Sequence[Frame],
    short_frames: Sequence[Frame],
    *,
    connected_at: float,
    taken_at: float,
    kind: str = KIND_FINE,
) -> ReferenceSegment:
    """オープンとショートを測ったフレームから S11 の基準の区間を作る。

    それぞれ W のまま平均してから、振幅の平均の2乗にする。どちらかが使えない点は NaN。
    """
    if not open_frames or not short_frames:
        raise ReferenceBuildError("オープンとショートの両方が必要です")
    if open_frames[0].axis != short_frames[0].axis:
        raise ReferenceBuildError("オープンとショートの周波数軸が一致しません")
    cond = _frame_conditions(open_frames[0])
    if _frame_conditions(short_frames[0]) != cond:
        raise ReferenceBuildError("オープンとショートの条件（モード・イメージ除去・RBW）が一致しません")
    mode, image_reject, rbw_hz = cond
    if mode != Mode.S11:
        raise ReferenceBuildError(f"モードが s11 ではありません（{mode.value}）")
    gain_table = frames_gain_table(list(open_frames) + list(short_frames))
    p_open = average_power(open_frames)
    p_short = average_power(short_frames)
    with np.errstate(invalid="ignore"):
        ref = ((np.sqrt(p_open) + np.sqrt(p_short)) / 2.0) ** 2
    return ReferenceSegment(
        axis=open_frames[0].axis,
        power_w=ref,
        image_reject=image_reject,
        rbw_hz=rbw_hz,
        connected_at=connected_at,
        taken_at=taken_at,
        kind=kind,
        averages=min(len(open_frames), len(short_frames)),
        gain_table=gain_table,
    )


def ratio_db(gamma: np.ndarray) -> np.ndarray:
    """振幅比の dB（20·log10）。基準の 0dB に対する反射の大きさで、ふつうは負の値。"""
    g = np.asarray(gamma, dtype=np.float64)
    with np.errstate(divide="ignore", invalid="ignore"):
        return 20.0 * np.log10(g)


def return_loss_db(gamma: np.ndarray) -> np.ndarray:
    """リターンロス（dB）。一般的な表し方に合わせ正の値（−20·log10|Γ|）で返す。"""
    return -ratio_db(gamma)


def swr(gamma: np.ndarray) -> np.ndarray:
    """SWR = (1+|Γ|)/(1−|Γ|)。|Γ| が 1 以上（ばらつきで基準を超えた点を含む）は inf、NaN は NaN。"""
    g = np.abs(np.asarray(gamma, dtype=np.float64))
    out = np.full(g.shape, np.nan)
    finite = np.isfinite(g)
    under = finite & (g < 1.0)
    out[under] = (1.0 + g[under]) / (1.0 - g[under])
    out[finite & (g >= 1.0)] = np.inf
    return out
