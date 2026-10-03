"""Qt のグラフの横軸（周波数・ゼロスパンの時間: 設計N §1）。

範囲・名前・目盛の単位は screen_model.x_axis_view（Web と同じ）。時間軸の位置はマイクロ秒で、目盛の数字は
軸の倍率（setScale）で ms・s にする。周波数軸は Hz のまま SI の接頭辞を付ける（今までどおり）。
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from . import look, screen_model, texts

if TYPE_CHECKING:
    from .main_window import MainWindow


def apply_x_axis(win: "MainWindow") -> None:
    st = win.state
    if not st.get("sweep"):
        return
    xa = screen_model.x_axis_view(st)
    pi = win.plot.getPlotItem()
    axis = pi.getAxis("bottom")
    if xa["kind"] == "time":
        axis.enableAutoSIPrefix(False)
        axis.setScale(1.0 / float(xa["factor"]))
        pi.setLabel("bottom", xa["label"], units="")
        axis.setToolTip(texts.ZERO_SPAN_TEXTS["axis_tip"])
    else:
        axis.setScale(1.0)
        axis.enableAutoSIPrefix(True)
        # 周波数オフセットで換算中なら、軸の名前で示す（設計O）
        pi.setLabel("bottom", look.freq_axis_label(st), units="Hz")
        axis.setToolTip(texts.LABELS["axis_x_tip"])
    win.plot.setXRange(xa["x0"], xa["x1"], padding=0)
    # マーカーはどちらの軸でも置ける（ゼロスパンは時間の位置）。範囲のドラッグは周波数だけ
    vb = pi.getViewBox()
    vb.freq_ops_enabled = xa["kind"] == "freq"
    vb.marker_ops_enabled = True
