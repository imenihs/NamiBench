"""基準の値（設計E §6.7）を直したときに、その基準の比で決めた値を同じだけずらす。

表示 = 基準との比 + 基準の値。値を直すと比のトレースの数字が同じだけ動くので、単位・外部補正を替えたとき
（設計書 O §1.4・core/instrument_view.py）と同じく、それに結び付いた数字も同じだけずらす（絵は変えず、数字だけが
新しい値になる）。決まりは1つ: **その基準の比で表した値だけを動かす**（ccf9359 の「値は設定したときの単位にだけ従う」と
同じ）。どの基準の比かは値ごとに持つ:

- XY カーソルの Y: 種類 "tg"（TG の基準との比の dB）で、置いたときの基準の id（y1_ref・y2_ref）が直した基準のものだけ。
  S11 のリターンロス（"db"）・|Γ|・SWR・NF・電力で置いたものと、種類の分からない前の版のもの（"db" で id の無いものを
  含む）は動かさない
- マーカーのしきい値: TG の基準との比のトレースで決めたもの（ThresholdSetting.ref）が直した基準のものだけ
- REF レベル・ピーク探索のしきい値: 縦軸の値（種類を持たない）。いまの縦軸がその基準の比のときだけ（呼ぶ側が決めて
  amplitude を渡す）

純粋な計算だけ。Qt を import しない。
"""
from __future__ import annotations

import dataclasses
from typing import Iterable, Optional

from .marker.markers import Y_KIND_TG


def shift_ratio_levels(tables: Iterable, ref_id: str, delta_db: float, amplitude: Optional[dict] = None) -> None:
    """マーカー表（周波数軸・時間軸）のうち基準 ref_id の比で決めた値を delta_db だけずらす。amplitude（いまのモードの
    縦軸）を渡したときは、縦軸の値（REF レベル・ピーク探索のしきい値）もずらす。"""
    if not delta_db:
        return
    if amplitude is not None:
        ref = amplitude.get("ref_level")
        amplitude["ref_level"] = round((0.0 if ref is None else float(ref)) + delta_db, 2)
    for table in tables:
        xy = table.xy
        for k in ("y1", "y2"):
            if (getattr(xy, k) is not None and getattr(xy, k + "_kind") == Y_KIND_TG
                    and getattr(xy, k + "_ref") == ref_id):
                setattr(xy, k, round(getattr(xy, k) + delta_db, 2))
        if amplitude is not None and table.peak_threshold is not None:
            table.peak_threshold = round(table.peak_threshold + delta_db, 2)
        for m in table.markers():
            th = m.threshold
            if th is not None and th.ref == ref_id:
                m.threshold = dataclasses.replace(th, level=round(th.level + delta_db, 2))
