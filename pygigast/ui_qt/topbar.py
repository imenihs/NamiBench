"""上段の並び（設計J §4.3.3）。"""
from __future__ import annotations

from typing import Optional

from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QWidget


class TopBar(QWidget):
    """上段。左（モードと周波数）と右（RBW・振幅・連続/単発/停止）を、幅が足りれば1段、足りなければ2段に並べる。

    段を変えるのは表示だけで、状態は変えない（J §4.3.3）。
    """

    def __init__(self) -> None:
        super().__init__()
        self._left_w = QWidget()
        self._right_w = QWidget()
        self.left = QHBoxLayout(self._left_w)
        self.right = QHBoxLayout(self._right_w)
        for lay in (self.left, self.right):
            lay.setContentsMargins(0, 0, 0, 0)
            lay.setSpacing(2)
        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setHorizontalSpacing(8)
        self._two_rows: Optional[bool] = None
        self._place(False)

    def _place(self, two_rows: bool) -> None:
        if two_rows == self._two_rows:
            return
        self._two_rows = two_rows
        self._grid.removeWidget(self._left_w)
        self._grid.removeWidget(self._right_w)
        self._grid.addWidget(self._left_w, 0, 0)
        if two_rows:
            self._grid.addWidget(self._right_w, 1, 0)
        else:
            self._grid.addWidget(self._right_w, 0, 1)

    def needed_one_row(self) -> int:
        return self._left_w.minimumSizeHint().width() + self._right_w.minimumSizeHint().width() + 8

    def minimumSizeHint(self):
        hint = super().minimumSizeHint()
        w = max(self._left_w.minimumSizeHint().width(), self._right_w.minimumSizeHint().width())
        hint.setWidth(w)
        return hint

    def resizeEvent(self, e) -> None:
        self._place(e.size().width() < self.needed_one_row())
        super().resizeEvent(e)

    def relayout(self) -> None:
        """中身の幅が変わったとき（TG で「直結を 0dB にする」が出た・消えた）に段を決め直す。"""
        if self.width() > 0:
            self._place(self.width() < self.needed_one_row())
