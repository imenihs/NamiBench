"""GigaSt の機器の層の試験で共通に使う部品（試験のモジュールではない）。"""
from __future__ import annotations

from typing import Optional

from pygigast.core.devices.gigast import protocol
from pygigast.core.types import AdoptedSweep, FreqAxis, Mode


def adopted(
    start_hz: int = 495_000_000,
    step_hz: int = 20_000,
    points: int = 500,
    rbw_hz: int = 180_000,
    mode: Mode = Mode.SA,
    sg_hz: Optional[int] = None,
    image_cancel: bool = False,
    preroll: Optional[int] = 0,
    transient_head_points: int = 0,
    stop_hz: Optional[int] = None,
) -> AdoptedSweep:
    """掃引条件モデルの出力を手で作る（試験用）。preroll=None は設計C §5.6 の式で決める。"""
    profile = protocol.default_profile()
    if preroll is None:
        preroll = protocol.preroll_points_for(profile, step_hz, rbw_hz)
    axis = FreqAxis(start_hz, step_hz, points)
    first, per = protocol.timing(profile, step_hz, rbw_hz)
    # 1画面の回数: NF は HOT/COLD の2回、イメージキャンセル ON は4回（設計F §6）
    passes = 2 if mode == Mode.NF else 4 if image_cancel else 1
    return AdoptedSweep(
        start_hz=start_hz,
        stop_hz=stop_hz if stop_hz is not None else axis.last_hz,
        axis=axis,
        rbw_hz=rbw_hz,
        rbw_preference_hz=rbw_hz,
        mode=mode,
        sg_hz=sg_hz,
        image_cancel=image_cancel,
        preroll_points=preroll,
        transient_head_points=transient_head_points,
        estimated_sweep_s=passes * (first + (points + preroll) * per),
    )
