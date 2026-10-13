"""機器の範囲の外・意味の無い区間の帯をグラフに描く（設計J §4.8）。

- 区間と説明は screen_model.marks_view（コアの display_marks に文言と色を足したもの。Web も同じ）
- 帯は薄い色でトレースの下に敷き、トレースを隠さない。説明は帯の左上に控えめな字で出し、帯が狭くて
  横書きが入らなければ縦に出す。字も入らないほど狭い帯（全スパンの ±5MHz など）は説明を出さない
  （説明はツールチップ・PNG の注意・マーカーの読み値の注意にも出る）
- ツールチップ: 帯の上にマウスを置くと説明と区間（「局発の漏れ（…）: −5MHz〜5MHz」）
- 内部の漏れの印（設計L §1.4）: SG・TG のとき、機種のプロファイルの漏れの周波数にグラフの上端で小さな ▼（配色の
  「注意」）。印と説明は source_view.leak_marks_view（Web と同じ）。説明はマウスの位置の読み値に出る

描くだけで、コアへは何も送らない。
"""
from __future__ import annotations

import pyqtgraph as pg
from PySide6.QtGui import QColor, QFont, QFontMetricsF

# 帯の塗りの濃さ（0〜255）。基準の範囲外の帯（plot_layers.set_bands）と同じ薄さ
_FILL_ALPHA = 35
_EDGE_ALPHA = 80
# 説明を出す最小の帯の幅（画素）。これより狭い帯は字が隣へはみ出して読めない
_MIN_LABEL_PX = 14
_PAD_PX = 4
# 内部の漏れの印の形（グラフの上端から下を指す。Web の graph.js も同じ形の三角を描く: 設計L §1.4）
LEAK_SYMBOL = "▼"


def _alpha(color: str, a: int) -> QColor:
    c = QColor(color)
    c.setAlpha(a)
    return c


class MarkBands:
    def __init__(self, plot: pg.PlotWidget, pal: dict[str, str]) -> None:
        self.pi = plot.getPlotItem()
        self.vb = self.pi.getViewBox()
        self.pal = pal
        self.items: list[tuple[pg.LinearRegionItem, pg.TextItem, dict]] = []
        self._key: tuple = ()
        self.font = QFont()
        self.font.setPointSizeF(8.5)
        # 内部の漏れの印（設計L §1.4）: グラフの上端に小さな ▼。説明はマウスの位置の読み値（mouse_reading）に出る
        self.leaks: list[tuple[pg.TextItem, dict]] = []
        self._leak_key: tuple = ()
        self.vb.sigRangeChanged.connect(lambda *_: self._place_labels())
        self.vb.sigResized.connect(lambda *_: self._place_labels())

    def set_marks(self, marks: list[dict]) -> None:
        key = tuple((m["kind"], m["start_hz"], m["stop_hz"], m["label"]) for m in marks or [])
        if key == self._key:
            return
        self._key = key
        for region, label, _m in self.items:
            self.pi.removeItem(region)
            self.pi.removeItem(label)
        self.items = []
        for m in marks or []:
            col = self.pal.get(m.get("color", "warn"), self.pal["warn"])
            region = pg.LinearRegionItem(values=(m["start_hz"], m["stop_hz"]), movable=False,
                                         brush=pg.mkBrush(_alpha(col, _FILL_ALPHA)),
                                         pen=pg.mkPen(_alpha(col, _EDGE_ALPHA)))
            region.setZValue(-9)  # 目盛線より上、トレース（10 以上）・基準の範囲外の帯（−8）より下
            region.setToolTip(m["tip"])
            self.pi.addItem(region, ignoreBounds=True)
            label = pg.TextItem(m["label"], color=col)
            label.setFont(self.font)
            label.setZValue(-7)
            self.pi.addItem(label, ignoreBounds=True)
            self.items.append((region, label, m))
        self._place_labels()

    def set_leaks(self, marks: list[dict]) -> None:
        """内部の漏れの印（source_view.leak_marks_view の [{freq_hz, tip, near_hz}]）を描き直す。"""
        key = tuple(m["freq_hz"] for m in marks or [])
        if key == self._leak_key:
            return
        self._leak_key = key
        for item, _m in self.leaks:
            self.pi.removeItem(item)
        self.leaks = []
        for m in marks or []:
            item = pg.TextItem(LEAK_SYMBOL, color=self.pal["warn"], anchor=(0.5, 0))
            item.setFont(self.font)
            item.setZValue(-6)  # 帯の説明より上、トレース（10 以上）より下
            self.pi.addItem(item, ignoreBounds=True)
            self.leaks.append((item, m))
        self._place_labels()

    def restyle(self) -> None:
        """配色を変えたとき（帯の色は配色の「注意」「控えめな文字」: J §4.8）。"""
        marks = [m for _r, _l, m in self.items]
        self._key = ()
        self.set_marks(marks)
        leaks = [m for _i, m in self.leaks]
        self._leak_key = ()
        self.set_leaks(leaks)

    def labels(self) -> list[pg.TextItem]:
        return [lb for _r, lb, _m in self.items]

    def _place_labels(self) -> None:
        """説明を帯の左上に置く。帯が狭ければ縦書き、字も入らなければ出さない。漏れの印は上端に置く。"""
        if not self.items and not self.leaks:
            return
        (x0, x1), (_y0, y1) = self.vb.viewRange()
        for item, m in self.leaks:
            item.setPos(float(m["freq_hz"]), y1)
        if not self.items:
            return
        px_w, px_h = self.vb.viewPixelSize()
        if px_w <= 0 or px_h <= 0:
            return
        fm = QFontMetricsF(self.font)
        for _region, label, m in self.items:
            lo, hi = max(m["start_hz"], x0), min(m["stop_hz"], x1)
            width_px = (hi - lo) / px_w
            text_w = fm.horizontalAdvance(m["label"]) + 2 * _PAD_PX
            if width_px < _MIN_LABEL_PX:
                label.hide()
                continue
            label.show()
            if width_px >= text_w:
                label.setAngle(0)
                label.setAnchor((0, 0))
            else:
                # 右へ90度回して、帯の左端に沿って上から下へ読む（字の下側が帯の左端に付く。Web と同じ向き）
                label.setAngle(-90)
                label.setAnchor((0, 1))
            label.setPos(lo + _PAD_PX * px_w, y1 - _PAD_PX * px_h)
