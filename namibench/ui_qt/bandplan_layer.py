"""スペクトルの上のバンドプランの帯とブックマークの印（設計P §6・§9.5）。

- 帯: 周波数軸に沿って、グラフの上の端に帯の色の細い帯と名前（波形を隠さないよう上の端だけ・薄く）
- ブックマーク: グラフの下の端に印と名前。印をクリックするとその周波数を Center にする（P §6。同じことは表示のタブの
  一覧の「Center に」でもできる: U-12）
描くのは画素の位置（グラフの縦の範囲によらない）。中身は display_view.bandplan_view（Web と同じ）。
"""
from __future__ import annotations

from typing import Callable, Optional

import pyqtgraph as pg
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QFont, QFontMetrics, QPen

from ..core.units import format_freq

STRIP_H = 14
MARK_H = 8
LABEL_PX = 11
PAD = 3


class BandplanLayer(pg.GraphicsObject):
    def __init__(self, plot: pg.PlotWidget, pal: dict, on_bookmark: Callable[[int], None]) -> None:
        super().__init__()
        self.pal = pal
        self._vb = plot.getPlotItem().getViewBox()
        self.setParentItem(self._vb)
        self.setZValue(60)
        self._on_bookmark = on_bookmark
        self.bands: list[dict] = []
        self.bookmarks: list[dict] = []
        self._hits: list[tuple[QRectF, int]] = []
        self._font = QFont()
        self._font.setPixelSize(LABEL_PX)
        self._vb.sigRangeChanged.connect(lambda *_a: self.update())
        self._vb.sigResized.connect(self._resized)

    def _resized(self, *_a) -> None:
        self.prepareGeometryChange()
        self.update()

    def set_items(self, bands: list[dict], bookmarks: list[dict]) -> None:
        if bands == self.bands and bookmarks == self.bookmarks:
            return
        self.bands = list(bands)
        self.bookmarks = list(bookmarks)
        self.update()

    def boundingRect(self) -> QRectF:  # noqa: N802 — Qt の名前
        return QRectF(self._vb.rect())

    def _x(self, hz: float) -> float:
        return self._vb.mapFromView(QPointF(float(hz), 0.0)).x()

    def paint(self, p, *_args) -> None:
        r = self._vb.rect()
        p.setFont(self._font)
        fm = QFontMetrics(self._font)
        p.setRenderHint(p.RenderHint.Antialiasing, False)
        for b in self.bands:
            x0 = max(r.left(), self._x(b["start_hz"]))
            x1 = min(r.right(), self._x(b["stop_hz"]))
            if x1 <= x0:
                continue
            c = QColor(b["color"])
            fill = QColor(c)
            fill.setAlpha(110)
            p.fillRect(QRectF(x0, r.top(), x1 - x0, STRIP_H), QBrush(fill))
            p.setPen(QPen(c, 1))
            p.drawLine(QPointF(x0, r.top()), QPointF(x0, r.top() + STRIP_H))
            text = b["name"]
            if fm.horizontalAdvance(text) + 2 * PAD <= x1 - x0:
                p.setPen(QPen(QColor(self.pal["text"])))
                p.drawText(QRectF(x0 + PAD, r.top(), x1 - x0 - PAD, STRIP_H),
                           int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter), text)
        self._hits = []
        mark = QColor(self.pal["accent"])
        bottom = r.bottom()
        for m in self.bookmarks:
            x = self._x(m["hz"])
            if not r.left() <= x <= r.right():
                continue
            p.setPen(QPen(mark, 1, Qt.PenStyle.DotLine))
            p.drawLine(QPointF(x, r.top() + STRIP_H), QPointF(x, bottom - MARK_H))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(mark))
            p.drawPolygon([QPointF(x, bottom - MARK_H), QPointF(x - 5, bottom), QPointF(x + 5, bottom)])
            text = m["name"]
            w = fm.horizontalAdvance(text)
            box = QRectF(x + 6, bottom - MARK_H - LABEL_PX - PAD, w + 2, LABEL_PX + PAD)
            p.setPen(QPen(mark))
            p.drawText(box, int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter), text)
            self._hits.append((box.united(QRectF(x - 6, bottom - MARK_H - 2, 12, MARK_H + 2)), int(m["hz"])))

    def bookmark_at(self, pos: QPointF) -> Optional[int]:
        for rect, hz in self._hits:
            if rect.contains(pos):
                return hz
        return None

    def mouseClickEvent(self, ev) -> None:  # noqa: N802 — pyqtgraph の名前
        if ev.button() == Qt.MouseButton.LeftButton:
            hz = self.bookmark_at(ev.pos())
            if hz is not None:
                ev.accept()
                self._on_bookmark(hz)
                return
        ev.ignore()

    @staticmethod
    def tooltip(m: dict, offset_hz: int) -> str:
        return f"{m['name']}  {format_freq(int(m['hz']) + offset_hz)}"
