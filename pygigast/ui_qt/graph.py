"""グラフの上の操作（設計J §3・§4.1）。

- グラフの中を横にドラッグ → その範囲を Start/Stop にする（拡大）
- 周波数軸の上でホイール → カーソルの位置を中心に拡大・縮小
- レベル軸の上でホイール → REF レベルを1目盛ずつ、Ctrl+ホイール → dB/div を1段
- レベル軸をドラッグ → REF レベルを連続で動かす（つかんだ目盛がマウスに付いてくる）
- クリック → その位置にマーカーを置く（近いトレースを選ぶのは画面側: 設計G §3.2）
- 右クリック → 名前の付いた操作のメニュー（中身は画面側が決める。U-12）

ここでは操作を読み取ってシグナルにするだけで、コアへは何も送らない（送るのは画面側）。
"""
from __future__ import annotations

import pyqtgraph as pg
from PySide6.QtCore import QObject, QPoint, Qt, Signal

# ドラッグをこの画素数より短く離したら、範囲を選んだことにしない（クリックのぶれ）
MIN_DRAG_PX = 6


class GraphSignals(QObject):
    range_selected = Signal(float, float)       # 選んだ範囲の左端・右端（軸の値）
    freq_wheel = Signal(float, int)             # カーソル位置の周波数、ホイールの段数（+ は奥へ回した）
    level_wheel = Signal(int, bool)             # ホイールの段数、Ctrl を押していたか
    level_drag = Signal(bool, float)            # ドラッグの始まりか、始まりからの移動（グラフの高さに対する割合。下へ +）
    context_requested = Signal(float, float, QPoint)  # 軸の値 x・y と、メニューを出す画面の位置
    clicked = Signal(float, float)              # 左クリックした位置（軸の値 x・y）


def _in_view(axis: pg.AxisItem, ev) -> bool:
    """軸の部品は目盛線の分だけグラフの中へ広がっている。グラフの中で起きたものは軸の操作にしない。"""
    lv = axis.linkedView()
    return lv is None or lv.sceneBoundingRect().contains(ev.scenePos())


def _wheel_steps(ev) -> int:
    d = ev.delta() if hasattr(ev, "delta") else ev.angleDelta().y()
    if d == 0:
        return 0
    # 高分解能のホイールでも1回の通知は最低1段にする
    n = int(d / 120)
    return n if n != 0 else (1 if d > 0 else -1)


class _FreqAxis(pg.AxisItem):
    def __init__(self, sig: GraphSignals, **kw) -> None:
        super().__init__("bottom", **kw)
        self._sig = sig

    def wheelEvent(self, ev) -> None:
        if _in_view(self, ev):
            ev.ignore()
            return
        steps = _wheel_steps(ev)
        if steps:
            x = self.linkedView().mapSceneToView(ev.scenePos()).x()
            self._sig.freq_wheel.emit(x, steps)
        ev.accept()

    def mouseDragEvent(self, ev) -> None:
        ev.ignore()


class _LevelAxis(pg.AxisItem):
    def __init__(self, sig: GraphSignals, **kw) -> None:
        super().__init__("left", **kw)
        self._sig = sig

    def wheelEvent(self, ev) -> None:
        if _in_view(self, ev):
            ev.ignore()
            return
        steps = _wheel_steps(ev)
        if steps:
            ctrl = bool(ev.modifiers() & Qt.KeyboardModifier.ControlModifier)
            self._sig.level_wheel.emit(steps, ctrl)
        ev.accept()

    def mouseDragEvent(self, ev) -> None:
        lv = self.linkedView()
        inside = lv is None or lv.sceneBoundingRect().contains(ev.buttonDownScenePos())
        if ev.button() != Qt.MouseButton.LeftButton or inside:
            ev.ignore()
            return
        ev.accept()
        h = lv.sceneBoundingRect().height()
        if h <= 0:
            return
        dy = ev.scenePos().y() - ev.buttonDownScenePos().y()
        self._sig.level_drag.emit(bool(ev.isStart()), dy / h)


class _GraphViewBox(pg.ViewBox):
    def __init__(self, sig: GraphSignals) -> None:
        super().__init__(enableMenu=False)
        self._sig = sig
        self.freq_ops_enabled = True
        self._band = pg.LinearRegionItem(movable=False, brush=pg.mkBrush(79, 163, 255, 60),
                                         pen=pg.mkPen(79, 163, 255, 200))
        self._band.setZValue(50)
        self._band.hide()
        self.addItem(self._band, ignoreBounds=True)

    def mouseDragEvent(self, ev, axis=None) -> None:
        if ev.button() != Qt.MouseButton.LeftButton or not self.freq_ops_enabled:
            ev.ignore()
            return
        ev.accept()
        x0 = self.mapSceneToView(ev.buttonDownScenePos()).x()
        x1 = self.mapSceneToView(ev.scenePos()).x()
        (xmin, xmax), _ = self.viewRange()
        lo, hi = sorted((min(max(x0, xmin), xmax), min(max(x1, xmin), xmax)))
        if ev.isFinish():
            self._band.hide()
            px = abs(ev.scenePos().x() - ev.buttonDownScenePos().x())
            if px >= MIN_DRAG_PX and hi > lo:
                self._sig.range_selected.emit(lo, hi)
            return
        self._band.setRegion((lo, hi))
        self._band.show()

    def mouseClickEvent(self, ev) -> None:
        if ev.button() == Qt.MouseButton.RightButton:
            ev.accept()
            p = self.mapSceneToView(ev.scenePos())
            self._sig.context_requested.emit(p.x(), p.y(), ev.screenPos().toPoint())
            return
        if ev.button() == Qt.MouseButton.LeftButton and self.freq_ops_enabled:
            ev.accept()
            p = self.mapSceneToView(ev.scenePos())
            self._sig.clicked.emit(p.x(), p.y())
            return
        super().mouseClickEvent(ev)

    def wheelEvent(self, ev, axis=None) -> None:
        # グラフの中のホイールでは何も動かさない（軸の上でだけ動かす: J §3・§4.1）
        ev.ignore()


def make_plot(background: str) -> tuple[pg.PlotWidget, GraphSignals]:
    """操作付きのグラフを作る。戻り値のシグナルを画面側で受けて命令にする。"""
    sig = GraphSignals()
    vb = _GraphViewBox(sig)
    plot = pg.PlotWidget(background=background, viewBox=vb,
                         axisItems={"bottom": _FreqAxis(sig), "left": _LevelAxis(sig)})
    plot.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
    return plot, sig
