"""密度（パーシステンス）表示（設計P §4・§8・§9.3）。Qt の画面の部品（描くのは pyqtgraph の画像）。

- 升目ごとに来た割合の色（0 は描かない）。横軸（周波数）はスペクトルと同じ範囲・縦軸はいまの縦軸（REF・dB/div）
- 何秒分の密度か（N × 掃引の間隔: P §4）を右上に出す
- 周波数軸・レベル軸の上の操作はスペクトルと同じ（シグナルを main_window が同じ命令にする）
中身（升目の数）はコアが作る（instrument_waterfall.py。Qt と Web で同じ絵: P §8）。
"""
from __future__ import annotations

from typing import Optional

import numpy as np
import pyqtgraph as pg
from PySide6.QtWidgets import QLabel, QPushButton, QWidget

from ..core import colormaps
from ..core.units import level_text
from . import sizes, texts
from .graph import GraphSignals, _FreqAxis, _GraphViewBox, _LevelAxis
from .waterfall_view import LEFT_AXIS_W

DIVISIONS = 10


class DensityView(QWidget):
    def __init__(self, pal: dict) -> None:
        super().__init__()
        self.pal = pal
        self.graph_sig = GraphSignals()
        lay = sizes.vbox(self, inset=False)
        lay.setSpacing(0)
        bar = sizes.hbox()
        bar.setContentsMargins(sizes.INSET, 0, sizes.INSET, 0)
        self.title = QLabel(texts.LABELS["part_density"])
        self.title.setObjectName("dim")
        bar.addWidget(self.title)
        bar.addStretch(1)
        self.seconds = QLabel("")
        self.seconds.setObjectName("dim")
        bar.addWidget(self.seconds)
        self.detach_btn = QPushButton(texts.LABELS["detach"])
        self.detach_btn.setToolTip(texts.w("detach_item", part=texts.LABELS["part_density"]))
        bar.addWidget(self.detach_btn)
        lay.addLayout(bar)
        self.box = _GraphViewBox(self.graph_sig)
        self.freq_axis = _FreqAxis(self.graph_sig)
        self.level_axis = _LevelAxis(self.graph_sig)
        self.plot = pg.PlotWidget(background=pal["graph_bg"], viewBox=self.box,
                                  axisItems={"bottom": self.freq_axis, "left": self.level_axis})
        self.plot.setMenuEnabled(False)
        self.plot.setMouseEnabled(x=False, y=False)
        self.plot.hideButtons()
        pi = self.plot.getPlotItem()
        pi.layout.setContentsMargins(0, 2, 8, 0)
        pi.showGrid(x=True, y=False, alpha=0.35)
        self.level_axis.setWidth(LEFT_AXIS_W)
        self.level_axis.enableAutoSIPrefix(False)
        self.level_axis.setStyle(hideOverlappingLabels=False)
        self.level_axis.setToolTip(texts.LABELS["axis_y_tip"])
        self.freq_axis.setToolTip(texts.LABELS["axis_x_tip"])
        pi.setLabel("left", texts.LABELS["level"], units="dBm")
        self.image = pg.ImageItem(axisOrder="row-major")
        self.image.setZValue(-5)
        self.box.addItem(self.image)
        self._hgrid: list = []
        lay.addWidget(self.plot, 1)
        self.colormap = colormaps.DEFAULT
        self._lut = colormaps.lut(self.colormap)
        self._last: Optional[dict] = None
        self.style_axes()

    def style_axes(self) -> None:
        for ax in (self.level_axis, self.freq_axis):
            ax.setPen(pg.mkPen(self.pal["axis"]))
            ax.setTextPen(pg.mkPen(self.pal["text"]))
        self.plot.setBackground(self.pal["graph_bg"])

    def set_colormap(self, name: str) -> None:
        if name != self.colormap:
            self.colormap = name
            self._lut = colormaps.lut(name)
            if self._last is not None:
                self.set_density(self._last)

    def set_x(self, x0: float, x1: float, offset_hz: int, zero_span: bool) -> None:
        self.freq_axis.set_offset(0 if zero_span else offset_hz)
        self.plot.setXRange(x0, max(x1, x0 + 1), padding=0)

    def set_y(self, ref: float, div: float, unit: str) -> None:
        """縦軸はスペクトルと同じ REF・dB/div（J §4.1）。"""
        top = ref
        bottom = top - DIVISIONS * div
        eps = div * 1e-3
        self.plot.setYRange(bottom - eps, top + eps, padding=0)
        ticks = [(top - i * div, level_text(top - i * div)) for i in range(DIVISIONS + 1)]
        self.level_axis.setTicks([ticks, []])
        for line in self._hgrid:
            self.box.removeItem(line)
        pen = pg.mkPen(self.pal["grid"], width=1)
        self._hgrid = [pg.InfiniteLine(pos=v, angle=0, pen=pen, movable=False) for v, _ in ticks]
        for line in self._hgrid:
            line.setZValue(-10)
            self.box.addItem(line, ignoreBounds=True)
        self.plot.getPlotItem().setLabel("left", texts.LABELS["level"], units=unit)

    def set_density(self, d: dict) -> None:
        """コアの密度（イベント density の中身）を描く。image が無ければ消す。"""
        self._last = d
        img = d.get("image")
        if img is None or not getattr(img, "size", 0):
            self.image.clear()
            self.seconds.setText("")
            return
        a = np.asarray(img, dtype=np.uint8)[::-1]  # コアの 0 行目は上（高いレベル）。画像は下から積む
        rgba = np.empty(a.shape + (4,), dtype=np.uint8)
        idx = np.clip((a.astype(np.int32) - 1) * 255 // 254, 0, 255).astype(np.uint8)
        rgba[..., :3] = self._lut[idx]
        rgba[..., 3] = np.where(a > 0, 255, 0)
        self.image.setImage(rgba, autoLevels=False)
        x0, x1 = float(d["start_hz"]), float(d["stop_hz"])
        if d.get("zero_span"):
            x0, x1 = 0.0, float(max(1, d["cols"] - 1))
        if x1 <= x0:
            x1 = x0 + 1.0
        lo, hi = float(d["level_lo"]), float(d["level_hi"])
        self.image.setRect(x0, lo, x1 - x0, hi - lo)
        from .display_view import density_seconds_text

        self.seconds.setText(density_seconds_text(d.get("seconds")))
