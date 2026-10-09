"""ウォーターフォール（設計P §2・§3・§5・§8・§9.2）。Qt の画面の部品（描くのは pyqtgraph の画像）。

- 1掃引＝1行。新しい行を上に足して下へ流す（P §9.1）。最新に付いていく間は1行を ROW_PX 画素で描く。時間の範囲を選んだ・
  再生中は、選んだ範囲（再生は記録の全体）を縦いっぱいに描く（行が画素より多い分はコアが最大値でまとめて返す: P §8）
- 横軸（周波数）はスペクトルと同じ範囲（P §2）。周波数軸の上のホイール・Shift を押しながらのドラッグはスペクトルと同じ操作
  （シグナルを main_window が同じ命令にする）。各行はその行の周波数の位置に描き、範囲の外は空白（P §3）
- 色はコアが決めた色の範囲とカラーマップ（Qt と Web で同じ: P §8）。値の無い点・範囲の外は描かない（背景が見える）
- 縦軸は時刻の目盛。行の上にマウスを置くと時刻・周波数・レベル、クリックでその時刻の掃引を出す（過去のトレース／再生の
  カーソル: P §3）。条件の変わった境目に線と条件を出す（P §3）。再生中はカーソルの行に線
- 時刻の軸の上をドラッグ → その時間の範囲を出す（時間方向の拡大: P §5）。時刻の軸の上のホイール → 前後へずらす
ここは描くことと操作を読み取ることだけで、コアへの命令は呼ぶ側（display_control.py）が送る。
"""
from __future__ import annotations

import datetime
import math
from typing import Callable, Optional

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QObject, QPoint, QPointF, Qt, QTimer, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QLabel, QPushButton, QToolTip, QWidget

from ..core import colormaps
from . import display_view, sizes, texts
from .graph import MIN_DRAG_PX, GraphSignals, _FreqAxis

# 最新に付いていく間の1行の高さ（画素）。掃引が遅い（1秒に 0.1〜12 行: P §1）ので2画素にして流れを見やすくする
ROW_PX = 2
# 時刻の目盛の間隔の候補（秒）と、目盛の間の最小の画素
TICK_STEPS_S = (1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600, 7200, 10800, 21600, 43200, 86400)
TICK_MIN_PX = 36
# 縦軸（時刻）と、スペクトルの縦軸（レベル）の幅。横軸の画素をスペクトルとそろえるため同じ幅にする
LEFT_AXIS_W = 72


def resample(row: dict, x0: float, x1: float, width: int) -> np.ndarray:
    """行の値を、横軸 [x0, x1] の width 列に移す（列に点が多いときは最大値: P §8。少ないときは近い点）。範囲の外は NaN。"""
    out = np.full(width, np.nan, dtype=np.float32)
    vals = row["values"]
    n = int(vals.shape[0])
    if n == 0 or width <= 0 or x1 <= x0:
        return out
    start = float(row["start_hz"])
    step = float(row["step_hz"]) or 1.0
    freqs = start + step * np.arange(n)
    scale = width / (x1 - x0)
    cols = np.floor((freqs - x0) * scale).astype(np.int64)
    ok = (cols >= 0) & (cols < width) & np.isfinite(vals)
    if ok.any():
        np.fmax.at(out, cols[ok], vals[ok])
    # 点より列が多い（拡大した）ときは、点の幅の中の列を近い点で埋める
    if step * scale > 1.0:
        centers = x0 + (np.arange(width) + 0.5) / scale
        # 近い点（半分ちょうどは上の点: Web の Math.round と同じ）
        j = np.floor((centers - start) / step + 0.5).astype(np.int64)
        inside = (j >= 0) & (j < n) & np.isnan(out)
        jj = j[inside]
        out[inside] = vals[jj]
    return out


def colorize(grid: np.ndarray, lut: np.ndarray, lo: float, hi: float) -> np.ndarray:
    """値の格子 → RGBA（値の無い所は透明）。"""
    span = max(1e-6, hi - lo)
    with np.errstate(invalid="ignore"):
        idx = np.clip((grid - lo) / span * 255.0, 0, 255)
    finite = np.isfinite(idx)
    idx = np.where(finite, idx, 0).astype(np.uint8)
    rgba = np.empty(grid.shape + (4,), dtype=np.uint8)
    rgba[..., :3] = lut[idx]
    rgba[..., 3] = np.where(finite, 255, 0)
    return rgba


def tick_step(seconds_per_px: float) -> float:
    for s in TICK_STEPS_S:
        if s / max(1e-9, seconds_per_px) >= TICK_MIN_PX:
            return float(s)
    return float(TICK_STEPS_S[-1])


class WaterfallSignals(QObject):
    picked = Signal(float, object)  # 行の時刻・記録の中の位置（ライブの行は None）
    time_range = Signal(float, float)  # 時刻の軸で選んだ範囲（古い時刻・新しい時刻）
    time_scroll = Signal(int)  # 時刻の軸の上のホイールの段数（+ は新しい方へ）
    resized = Signal()


class _TimeAxis(pg.AxisItem):
    """縦軸。行の番号（0 が一番上の新しい行）に時刻の目盛を付ける。"""

    def __init__(self, view: "WaterfallView") -> None:
        super().__init__("left")
        self._view = view
        self._drag0: Optional[float] = None

    def tickValues(self, minVal, maxVal, size):  # noqa: N802 — pyqtgraph の名前
        rows = self._view.shown_rows
        if not rows:
            return []
        h = self._view.row_height_px()
        times = [r["t"] for r in rows]
        if len(times) < 2:
            return [(1.0, [0.5])]
        span = abs(times[0] - times[-1])
        spp = span / max(1.0, len(times) * h)
        step = tick_step(spp)
        out = []
        prev = None
        for i, t in enumerate(times):
            k = math.floor(t / step)
            if prev is not None and k != prev:
                out.append(i + 0.0)
            prev = k
        # 区切りが1つも無い（短い時間の行だけ）ときは一番上の行の時刻を出す（Web と同じ）
        return [(1.0, out or [0.0])]

    def tickStrings(self, values, scale, spacing):  # noqa: N802
        rows = self._view.shown_rows
        out = []
        for v in values:
            i = int(round(v))
            if 0 <= i < len(rows):
                out.append(datetime.datetime.fromtimestamp(rows[i]["t"]).strftime("%H:%M:%S"))
            else:
                out.append("")
        return out

    def _inside(self, ev) -> bool:
        lv = self.linkedView()
        return lv is not None and lv.sceneBoundingRect().contains(ev.scenePos())

    def wheelEvent(self, ev) -> None:
        if self._inside(ev):
            ev.ignore()
            return
        d = ev.delta() if hasattr(ev, "delta") else ev.angleDelta().y()
        if d:
            self._view.sig.time_scroll.emit(1 if d > 0 else -1)
        ev.accept()

    def mouseDragEvent(self, ev) -> None:
        lv = self.linkedView()
        if ev.button() != Qt.MouseButton.LeftButton or lv is None or lv.sceneBoundingRect().contains(
                ev.buttonDownScenePos()):
            ev.ignore()
            return
        ev.accept()
        y0 = lv.mapSceneToView(ev.buttonDownScenePos()).y()
        y1 = lv.mapSceneToView(ev.scenePos()).y()
        lo, hi = sorted((y0, y1))
        if ev.isFinish():
            self._view.band.hide()
            if abs(ev.scenePos().y() - ev.buttonDownScenePos().y()) >= MIN_DRAG_PX:
                self._view.select_rows(lo, hi)
            return
        self._view.band.setRegion((lo, hi))
        self._view.band.show()


class _WaterfallBox(pg.ViewBox):
    """行のクリック（その時刻の掃引を出す）・Shift を押しながらの横のドラッグ（周波数の範囲: スペクトルと同じ）。"""

    def __init__(self, view: "WaterfallView", graph_sig: GraphSignals) -> None:
        super().__init__(enableMenu=False)
        self._view = view
        self._gsig = graph_sig
        self.freq_ops_enabled = True
        self._range_drag = False
        self._fband = pg.LinearRegionItem(movable=False, brush=pg.mkBrush(79, 163, 255, 60),
                                          pen=pg.mkPen(79, 163, 255, 200))
        self._fband.setZValue(50)
        self._fband.hide()
        self.addItem(self._fband, ignoreBounds=True)

    def mouseDragEvent(self, ev, axis=None) -> None:
        if ev.isStart():
            self._range_drag = bool(ev.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        if ev.button() != Qt.MouseButton.LeftButton or not self.freq_ops_enabled or not self._range_drag:
            ev.ignore()
            return
        ev.accept()
        x0 = self.mapSceneToView(ev.buttonDownScenePos()).x()
        x1 = self.mapSceneToView(ev.scenePos()).x()
        (xmin, xmax), _ = self.viewRange()
        lo, hi = sorted((min(max(x0, xmin), xmax), min(max(x1, xmin), xmax)))
        if ev.isFinish():
            self._fband.hide()
            if abs(ev.scenePos().x() - ev.buttonDownScenePos().x()) >= MIN_DRAG_PX and hi > lo:
                self._gsig.range_selected.emit(lo, hi)
            return
        self._fband.setRegion((lo, hi))
        self._fband.show()

    def mouseClickEvent(self, ev) -> None:
        if ev.button() == Qt.MouseButton.LeftButton:
            ev.accept()
            p = self.mapSceneToView(ev.scenePos())
            self._view.click_at(p.x(), p.y())
            return
        ev.ignore()

    def wheelEvent(self, ev, axis=None) -> None:
        ev.ignore()


class WaterfallView(QWidget):
    """ウォーターフォールの部分（上の行の「最新へ／全体」と、描く所）。"""

    def __init__(self, pal: dict, reading: Callable[[dict, float, Optional[float]], list[str]]) -> None:
        super().__init__()
        self.pal = pal
        self._reading = reading
        self.sig = WaterfallSignals()
        self.graph_sig = GraphSignals()
        lay = sizes.vbox(self, inset=False)
        lay.setSpacing(0)
        bar = sizes.hbox()
        bar.setContentsMargins(sizes.INSET, 0, sizes.INSET, 0)
        self.title = QLabel(texts.LABELS["part_waterfall"])
        self.title.setObjectName("dim")
        bar.addWidget(self.title)
        bar.addStretch(1)
        self.follow_btn = QPushButton(texts.DISPLAY_TEXTS["latest"])
        bar.addWidget(self.follow_btn)
        self.detach_btn = QPushButton(texts.LABELS["detach"])
        self.detach_btn.setToolTip(texts.w("detach_item", part=texts.LABELS["part_waterfall"]))
        bar.addWidget(self.detach_btn)
        lay.addLayout(bar)
        self.box = _WaterfallBox(self, self.graph_sig)
        self.freq_axis = _FreqAxis(self.graph_sig)
        self.time_axis = _TimeAxis(self)
        self.plot = pg.PlotWidget(background=pal["graph_bg"], viewBox=self.box,
                                  axisItems={"bottom": self.freq_axis, "left": self.time_axis})
        self.plot.setMenuEnabled(False)
        self.plot.setMouseEnabled(x=False, y=False)
        self.plot.hideButtons()
        pi = self.plot.getPlotItem()
        pi.layout.setContentsMargins(0, 2, 8, 0)
        self.box.invertY(True)
        self.time_axis.setWidth(LEFT_AXIS_W)
        self.time_axis.setToolTip(texts.LABELS.get("axis_time_tip", ""))
        self.freq_axis.setToolTip(texts.LABELS["axis_x_tip"])
        pi.setLabel("left", texts.LABELS.get("axis_time", ""))
        self.image = pg.ImageItem(axisOrder="row-major")
        self.box.addItem(self.image)
        self.band = pg.LinearRegionItem(orientation="horizontal", movable=False, brush=pg.mkBrush(79, 163, 255, 60),
                                        pen=pg.mkPen(79, 163, 255, 200))
        self.band.setZValue(50)
        self.band.hide()
        self.box.addItem(self.band, ignoreBounds=True)
        self.cursor_line = pg.InfiniteLine(angle=0, movable=False, pen=pg.mkPen(pal["accent"], width=1.5))
        self.cursor_line.setZValue(40)
        self.cursor_line.hide()
        self.box.addItem(self.cursor_line, ignoreBounds=True)
        self._bounds: list = []
        lay.addWidget(self.plot, 1)
        # ---- 描く中身
        self.rows: list[dict] = []  # 新しい順（0 が一番上）
        self.shown_rows: list[dict] = []
        self.follow = True
        self.x0, self.x1, self.zero_span, self.offset = 0.0, 1.0, False, 0
        self.lo, self.hi = -110.0, -20.0
        self.colormap = colormaps.DEFAULT
        self._lut = colormaps.lut(self.colormap)
        self._grid: Optional[np.ndarray] = None
        self._grid_key: tuple = ()
        self.cursor_t: Optional[float] = None
        self.box.sigResized.connect(self._on_resized)
        self._resize_timer = QTimer(self)
        self._resize_timer.setSingleShot(True)
        self._resize_timer.setInterval(150)
        self._resize_timer.timeout.connect(self.sig.resized.emit)
        self._hover_pos: Optional[QPointF] = None
        self.hover_text = ""
        self._hover_timer = QTimer(self)
        self._hover_timer.setSingleShot(True)
        self._hover_timer.setInterval(34)
        self._hover_timer.timeout.connect(self.refresh_hover)
        self.plot.scene().sigMouseMoved.connect(self._hover)
        self.style_axes()

    # ---------------------------------------------------------------- 見た目

    def style_axes(self) -> None:
        for ax in (self.time_axis, self.freq_axis):
            ax.setPen(pg.mkPen(self.pal["axis"]))
            ax.setTextPen(pg.mkPen(self.pal["text"]))
        self.plot.setBackground(self.pal["graph_bg"])
        self.cursor_line.setPen(pg.mkPen(self.pal["accent"], width=1.5))
        self._draw_bounds()

    def set_colormap(self, name: str) -> None:
        if name != self.colormap:
            self.colormap = name
            self._lut = colormaps.lut(name)
            self._paint()

    def set_range(self, lo: float, hi: float) -> None:
        if (lo, hi) != (self.lo, self.hi):
            self.lo, self.hi = float(lo), float(hi)
            self._paint()

    def set_x(self, x0: float, x1: float, zero_span: bool, offset_hz: int) -> None:
        self.freq_axis.set_offset(0 if zero_span else offset_hz)
        if (x0, x1, zero_span) != (self.x0, self.x1, self.zero_span):
            self.x0, self.x1, self.zero_span = float(x0), float(max(x1, x0 + 1)), bool(zero_span)
            self.offset = offset_hz
            self.plot.setXRange(self.x0, self.x1, padding=0)
            self._rebuild()

    # ---------------------------------------------------------------- 行

    def visible_rows(self) -> int:
        """最新に付いていく間に描ける行の数（縦の画素 ÷ ROW_PX）。"""
        h = max(1.0, self.box.sceneBoundingRect().height())
        return max(1, int(h // ROW_PX))

    def pixel_rows(self) -> int:
        return max(1, int(self.box.sceneBoundingRect().height()))

    def row_height_px(self) -> float:
        n = max(1, len(self.shown_rows))
        return ROW_PX if self.follow else max(1.0, self.box.sceneBoundingRect().height() / n)

    def set_rows(self, rows_oldest_first: list[dict], follow: bool) -> None:
        self.follow = follow
        self.rows = list(reversed(rows_oldest_first))
        self._rebuild()

    def append(self, rows_oldest_first: list[dict]) -> None:
        """最新に付いていく間に届いた行を上に足す（下へ流す）。"""
        for r in rows_oldest_first:
            self.rows.insert(0, r)
        keep = max(self.visible_rows(), 1) * 2
        del self.rows[keep:]
        if not self.follow:
            return
        n = self.visible_rows()
        width = self._width()
        key = (n, width, self.x0, self.x1)
        if self._grid is None or self._grid_key != key or len(rows_oldest_first) > 4:
            self._rebuild()
            return
        new = np.stack([resample(r, self.x0, self.x1, width) for r in reversed(rows_oldest_first)])
        k = new.shape[0]
        self._grid = np.vstack([new, self._grid[:-k]]) if k < n else new[:n]
        self.shown_rows = self.rows[:n]
        self._paint()

    def _width(self) -> int:
        return max(16, int(self.box.sceneBoundingRect().width()))

    def _rebuild(self) -> None:
        width = self._width()
        if self.follow:
            n = self.visible_rows()
            self.shown_rows = self.rows[:n]
        else:
            n = max(1, len(self.rows))
            self.shown_rows = self.rows
        grid = np.full((n, width), np.nan, dtype=np.float32)
        for i, r in enumerate(self.shown_rows[:n]):
            grid[i] = resample(r, self.x0, self.x1, width)
        self._grid = grid
        self._grid_key = (n, width, self.x0, self.x1)
        self._paint()

    def _paint(self) -> None:
        g = self._grid
        if g is None:
            return
        self.image.setImage(colorize(g, self._lut, self.lo, self.hi), autoLevels=False)
        n = g.shape[0]
        self.image.setRect(self.x0, 0.0, self.x1 - self.x0, float(n))
        self.box.setYRange(0.0, float(n), padding=0)
        self.time_axis.picture = None
        self.time_axis.update()
        self._draw_bounds()
        self._place_cursor()

    def _draw_bounds(self) -> None:
        """条件の変わった境目（P §3）: 行の間に点線と、新しい側の条件。"""
        for it in self._bounds:
            self.box.removeItem(it)
        self._bounds = []
        rows = self.shown_rows
        pen = pg.mkPen(self.pal["text_dim"], width=1, style=Qt.PenStyle.DashLine)
        for i in range(len(rows) - 1):
            if rows[i].get("cond") != rows[i + 1].get("cond") and rows[i].get("cond"):
                line = pg.InfiniteLine(pos=i + 1.0, angle=0, movable=False, pen=pen)
                line.setZValue(30)
                self.box.addItem(line, ignoreBounds=True)
                label = pg.TextItem(display_view.boundary_label(rows[i]["cond"], self.offset),
                                    color=self.pal["text"], anchor=(0, 1), fill=pg.mkBrush(self.pal["graph_bg"]))
                label.setPos(self.x0, i + 1.0)
                label.setZValue(31)
                self.box.addItem(label, ignoreBounds=True)
                self._bounds += [line, label]
                if len(self._bounds) >= 16:
                    break

    def set_cursor(self, t: Optional[float]) -> None:
        self.cursor_t = t
        self._place_cursor()

    def _place_cursor(self) -> None:
        t = self.cursor_t
        rows = self.shown_rows
        if t is None or not rows:
            self.cursor_line.hide()
            return
        for i, r in enumerate(rows):
            if r["t"] <= t + 1e-6:
                self.cursor_line.setPos(i + 0.5)
                self.cursor_line.show()
                return
        self.cursor_line.hide()

    def select_rows(self, y_lo: float, y_hi: float) -> None:
        """時刻の軸で選んだ行の範囲 → 時刻の範囲（古い・新しい）。"""
        rows = self.shown_rows
        if not rows:
            return
        a = min(max(int(math.floor(y_lo)), 0), len(rows) - 1)
        b = min(max(int(math.floor(y_hi)), 0), len(rows) - 1)
        newest, oldest = rows[a], rows[b]
        self.sig.time_range.emit(float(oldest["t"]), float(newest.get("t_end", newest["t"])))

    def row_at(self, y: float) -> Optional[dict]:
        i = int(math.floor(y))
        return self.shown_rows[i] if 0 <= i < len(self.shown_rows) else None

    def click_at(self, x: float, y: float) -> None:
        r = self.row_at(y)
        if r is not None:
            self.sig.picked.emit(float(r["t"]), r.get("pos"))

    def value_at(self, r: dict, x: float) -> tuple[Optional[float], Optional[int]]:
        vals = r["values"]
        n = int(vals.shape[0])
        step = float(r["step_hz"]) or 1.0
        j = int(math.floor((x - float(r["start_hz"])) / step + 0.5))
        if not 0 <= j < n:
            return None, None
        v = float(vals[j])
        return (v if math.isfinite(v) else None), j

    def _hover(self, pos) -> None:
        self._hover_pos = QPointF(pos)
        if not self._hover_timer.isActive():
            self._hover_timer.start()

    def refresh_hover(self) -> None:
        """マウスの位置の行の時刻・周波数・レベルをツールチップで出す（1 秒に 30 回まで。スペクトルと同じ出し方）。"""
        pos = self._hover_pos
        pressed = QGuiApplication.mouseButtons() != Qt.MouseButton.NoButton
        r = None
        p = None
        if pos is not None and not pressed and self.box.sceneBoundingRect().contains(pos):
            p = self.box.mapSceneToView(pos)
            r = self.row_at(p.y())
        if r is None or p is None:
            if self.hover_text:
                self.hover_text = ""
                QToolTip.hideText()
            return
        level, _j = self.value_at(r, p.x())
        self.hover_text = "\n".join(self._reading(r, p.x(), level))
        at = self.plot.mapToGlobal(self.plot.mapFromScene(pos)) + QPoint(16, 16)
        QToolTip.showText(at, self.hover_text, self.plot)

    def _on_resized(self, *_a) -> None:
        self._rebuild()
        self._resize_timer.start()

    def set_follow_button(self, label: str, reason: Optional[str]) -> None:
        self.follow_btn.setText(label)
        self.follow_btn.setEnabled(reason is None)
        self.follow_btn.setToolTip(reason or "")
