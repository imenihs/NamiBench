"""計測器の本体の部品が共有するもの（命令の失敗・採用値の JSON）。

instrument.py・instrument_display.py・instrument_files.py の3つが使う。循環 import を避けるためにここへ置く。
"""
from __future__ import annotations

import dataclasses
from typing import Optional

from .types import AdoptedSweep, SweepSegment


class CommandError(Exception):
    """命令の失敗。code は理由の種類。画面の文言は画面側が決める（設計A §4.3）。"""

    def __init__(self, code: str, field: Optional[str] = None, detail: Optional[dict] = None) -> None:
        super().__init__(code)
        self.code = code
        self.field = field
        self.detail = detail or {}

    def to_dict(self) -> dict:
        return {"error": {"code": self.code, "field": self.field, "detail": self.detail}}


def adopted_to_dict(a: AdoptedSweep) -> dict:
    """採用値を JSON に写せる形にする（周波数は Hz の整数のまま）。"""
    return {
        "start_hz": a.start_hz,
        "stop_hz": a.stop_hz,
        "center_hz": a.center_hz,
        "span_hz": a.span_hz,
        "step_hz": a.axis.step_hz,
        "points": a.axis.points,
        "points_setting": a.points_setting,
        "rbw_hz": a.rbw_hz,
        "rbw_preference_hz": a.rbw_preference_hz,
        "rbw_usable_hz": list(a.rbw_usable_hz),
        "rbw_limit": a.rbw_limit,
        "mode": a.mode.value,
        "sg_hz": a.sg_hz,
        # イメージ除去（設計F §7.3）: 選択・実際に使うもの（NF・ゼロスパンは "off"）・3つを選んだときの1画面の
        # 見積もり秒・正確のときの区間（範囲と回数）
        "image_reject": a.image_reject,
        "image_reject_used": a.image_reject_used,
        "image_reject_estimates": dict(a.image_reject_estimates),
        "accurate_segments": [segment_to_dict(a, s) for s in a.accurate_segments],
        "preroll_points": a.preroll_points,
        "transient_head_points": a.transient_head_points,
        "estimated_sweep_s": a.estimated_sweep_s,
        "tail_gap": a.tail_gap,
        "warnings": list(a.warnings),
        "span_lock_hz": a.span_lock_hz,
        # 1点あたりの時間の見積もり（秒）。ゼロスパンの時間軸（設計N §1）
        "point_time_s": a.point_time_s,
    }


def segment_to_dict(a: AdoptedSweep, s: SweepSegment) -> dict:
    """区間の範囲（最初と最後の点の周波数）と回数。"""
    step = a.axis.step_hz
    return {"start_hz": a.axis.start_hz + s.point0 * step, "stop_hz": a.axis.start_hz + (s.point0 + s.points - 1) * step,
            "points": s.points, "passes": s.passes}


def adjustments_to_list(a: AdoptedSweep) -> list[dict]:
    return [dataclasses.asdict(x) for x in a.adjustments]


def sweep_key(a: AdoptedSweep) -> tuple:
    """機器へ送る中身（これが同じなら測り直さなくてよい）。イメージ除去は実際に使うもの（NF・ゼロスパンでは選択を
    変えても送る中身は変わらない）。"""
    return (a.start_hz, a.stop_hz, a.axis, a.rbw_hz, a.mode, a.sg_hz, a.image_reject_used, a.segments)
