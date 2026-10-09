"""基準の値（設計E §6.7）を直したときに、その基準の比で決めた値を同じだけずらす。

表示 = 基準との比 + 基準の値。値を直すと比のトレースの数字が同じだけ動くので、単位・外部補正を替えたとき
（設計書 O §1.4・core/instrument_view.py）と同じく、縦軸に結び付いた数字も同じだけずらす（絵は変えず、数字だけが
新しい値になる）。ずらすのは、いまの縦軸がその基準の比（TG の dB）のときだけ（呼ぶ側が決める）。そのうえで、
それぞれの値を決めたときの種類に従う（ccf9359 の「値は設定したときの単位にだけ従う」と同じ）:

- REF レベル・ピーク探索のしきい値: 縦軸の値
- XY カーソルの Y: 比の dB の縦軸で置いたもの（y_kind "db"）だけ。電力・|Γ|・SWR・NF で置いたものと、種類の
  分からない前の版のもの（電力として扱う）は動かさない
- マーカーのしきい値: 比の dB で決めたもの（電力のトレースで決めたものでなく、形式が dB）だけ

純粋な計算だけ。Qt を import しない。
"""
from __future__ import annotations

import dataclasses
from typing import Iterable

from .trace import formats


def shift_ratio_levels(amplitude: dict, tables: Iterable, delta_db: float) -> None:
    """amplitude（いまのモードの縦軸）と marker_tables（周波数軸・時間軸のマーカー表）の値を delta_db だけずらす。"""
    if not delta_db:
        return
    ref = amplitude.get("ref_level")
    amplitude["ref_level"] = round((0.0 if ref is None else float(ref)) + delta_db, 2)
    for table in tables:
        xy = table.xy
        for k in ("y1", "y2"):
            if getattr(xy, k) is not None and getattr(xy, k + "_kind") == formats.DB:
                setattr(xy, k, round(getattr(xy, k) + delta_db, 2))
        if table.peak_threshold is not None:
            table.peak_threshold = round(table.peak_threshold + delta_db, 2)
        for m in table.markers():
            th = m.threshold
            if th is not None and not th.power and th.fmt == formats.DB:
                m.threshold = dataclasses.replace(th, level=round(th.level + delta_db, 2))
