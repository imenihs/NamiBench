"""グラフの上のマウスの位置の読み値をツールチップで出す（設計G §2: マウスの縦の位置ではなく波形の値）。

- マウスがグラフの中（軸の上ではない）にある間だけ、カーソルの近くに出す。グラフの外へ出たら消す
- 中身（周波数・最も近いトレースの最も近い点の値・印の付いた点の注意）は screen_model.mouse_reading
  （Web の口も同じ関数で作る）。ここは位置を拾って出すだけ
- 更新は 1 秒に 30 回まで（マウスを速く動かしても描き直しを詰まらせない）
- ボタンを押している間（範囲のドラッグ・XY カーソルを動かしている間）は出さない。ツールチップは
  カーソルから離して出すので、クリック・ドラッグ・ホイールはグラフに届く
"""
from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QEvent, QObject, QPoint, QPointF, QTimer, Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QToolTip

from . import screen_model

# 更新の間隔（ミリ秒）。34 ミリ秒ごとなら 1 秒に 30 回を超えない
INTERVAL_MS = 34
# ツールチップをカーソルからずらす量（画素）。カーソルの真下に出すとクリックを奪うため
OFFSET = QPoint(16, 16)


class MouseReading(QObject):
    def __init__(self, plot, payloads: Callable[[], list[dict]], zero_span: Callable[[], bool]) -> None:
        super().__init__(plot)
        self.plot = plot
        self._payloads = payloads
        self._zero_span = zero_span
        self._scene_pos: QPointF | None = None
        self.text = ""  # いま出している文（出していなければ空）
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(INTERVAL_MS)
        self._timer.timeout.connect(self.refresh)
        plot.scene().sigMouseMoved.connect(self._on_moved)
        plot.viewport().installEventFilter(self)

    def _on_moved(self, scene_pos) -> None:
        self._scene_pos = QPointF(scene_pos)
        if not self._timer.isActive():
            self._timer.start()

    def eventFilter(self, obj, ev) -> bool:  # noqa: N802 — Qt の名前
        if ev.type() == QEvent.Type.Leave:
            self.hide()
        return False

    def hide(self) -> None:
        self._scene_pos = None
        self._timer.stop()
        if self.text:
            self.text = ""
            QToolTip.hideText()

    def refresh(self) -> None:
        """いまのマウスの位置で出し直す（位置が無い・グラフの外・ボタンを押している間は消す）。"""
        pos = self._scene_pos
        vb = self.plot.getPlotItem().getViewBox()
        pressed = QGuiApplication.mouseButtons() != Qt.MouseButton.NoButton
        if pos is None or pressed or not vb.sceneBoundingRect().contains(pos):
            if self.text:
                self.text = ""
                QToolTip.hideText()
            return
        p = vb.mapSceneToView(pos)
        r = screen_model.mouse_reading(self._payloads(), p.x(), p.y(), self._zero_span())
        self.text = r["text"]
        at = self.plot.mapToGlobal(self.plot.mapFromScene(pos)) + OFFSET
        QToolTip.showText(at, self.text, self.plot)
