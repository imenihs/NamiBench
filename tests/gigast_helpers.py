"""GigaSt の機器の層の試験で共通に使う部品（試験のモジュールではない）。"""
from __future__ import annotations

from typing import Optional

from pygigast.core.devices.gigast import protocol
from pygigast.core.sweep import image_reject as ir
from pygigast.core.types import AdoptedSweep, FreqAxis, Mode, SweepSegment


def adopted(
    start_hz: int = 495_000_000,
    step_hz: int = 20_000,
    points: int = 500,
    rbw_hz: int = 180_000,
    mode: Mode = Mode.SA,
    sg_hz: Optional[int] = None,
    image_reject: str = "off",
    preroll: Optional[int] = 0,
    transient_head_points: int = 0,
    stop_hz: Optional[int] = None,
) -> AdoptedSweep:
    """掃引条件モデルの出力を手で作る（試験用）。preroll=None は設計C §5.6 の式で決める。

    区間は掃引条件モデルと同じ分け方（設計F §7.2: 正確は帯の境目で分ける）。前置きはどの区間も preroll、
    過渡の印は先頭の区間だけ transient_head_points。
    """
    profile = protocol.default_profile()
    if preroll is None:
        preroll = protocol.preroll_points_for(profile, step_hz, rbw_hz)
    axis = FreqAxis(start_hz, step_hz, points)
    first, per = protocol.timing(profile, step_hz, rbw_hz)
    choice = ir.OFF if mode == Mode.NF or step_hz == 0 else image_reject
    segs = tuple(
        SweepSegment(point0=p0, points=n, b3=ir.b3_order(k), preroll_points=preroll,
                     transient_head_points=transient_head_points if p0 == 0 else 0, in_band=inside)
        for p0, n, k, inside in ir.passes_by_region(choice, axis, ir.RejectProfile(profile))
    )
    # 1画面の回数: NF は HOT/COLD の2回、イメージ除去は区間ごとの回数（設計F §7.3）
    est = sum(s.passes * (first + (s.points + preroll) * per) for s in segs) * (2 if mode == Mode.NF else 1)
    return AdoptedSweep(
        start_hz=start_hz,
        stop_hz=stop_hz if stop_hz is not None else axis.last_hz,
        axis=axis,
        rbw_hz=rbw_hz,
        rbw_preference_hz=rbw_hz,
        mode=mode,
        sg_hz=sg_hz,
        image_reject=image_reject,
        preroll_points=preroll,
        transient_head_points=transient_head_points,
        estimated_sweep_s=est,
        segments=segs,
    )
