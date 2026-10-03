"""PNG に描く中身（設計I §3.2 の帯とグラフ）を画面の状態から作る。形は core/report_layout の冒頭の説明のとおり。

帯の中身（機器・条件・補正・注意）は report_layout.build_data（Web の口も同じ関数を使う: I §3.1）。
ここで足すのは Qt が描いているトレースの値と凡例、マーカー表の行だけ。
"""
from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np

from .. import __version__ as APP_VERSION
from ..core import report_layout as rl
from . import marker_rows, screen_model, texts
from .plot_layers import marked_points

if TYPE_CHECKING:
    from .main_window import MainWindow


def build(win: "MainWindow") -> dict:
    """画面に出ているもの（状態・描いているトレース・マーカー表・注意）から画像の中身を作る（I §3.2）。"""
    st = win.state
    traces = []
    for _curve, label, p in win.traces.legend_entries():
        ax = p["axis"]
        if "freqs_hz" in ax:
            continue  # 不等間隔の軸は画像の描き方（Start・Step）に載らない
        item = {"start_hz": ax["start_hz"], "step_hz": ax["step_hz"], "points": ax["points"],
                "values_db": np.asarray(p["values"], dtype=np.float64), "visible": True,
                "flags": None}
        if p.get("source") == "file":
            # 色は名前で渡す（画面の色の値を焼き込むと、ライト・ダークを選んでもこの線だけ画面の色になる: I §3.6）。
            # 線は画面と同じ参照トレースの線（設計F §9）
            item.update({"number": "", "action": "", "label": label, "line": rl.REFERENCE_LINE,
                         "color": win.traces.files[str(p["trace"])]["color_key"], "count": None})
        else:
            # 線は画面で描いている線（設計F §9）
            item.update({**texts.legend_parts(p), "color": f"trace{p['trace']}",
                         "line": win.traces.line_of(int(p["trace"]))})
            item["flags"] = marked_points(p, ax["points"]).tolist()
        traces.append(item)
    sw = st.get("sweep") or {}
    return rl.build_data(
        st, ref_level=win.ref_level(), db_per_div=win.db_div(), unit=win.display_unit(),
        mode_name=texts.MODE_NAMES.get(sw.get("mode"), sw.get("mode")), traces=traces,
        markers=marker_rows.png_rows(win.marker_row_list, lines=marker_rows.function_lines_on(st),
                                     axis_unit=win.display_unit()), warnings=screen_model.png_warnings(st),
        app_version=APP_VERSION, norm_reasons=texts.NORM_REASONS)
