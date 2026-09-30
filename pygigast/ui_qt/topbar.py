"""上段の並び（設計J §4.3.3）と、機器を選ぶ画面（設計B §7.3）。"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)


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


class ConnectDialog(QDialog):
    """機器を一覧から選ぶ（設計B §7.3: 初めての機器は利用者が選ぶ）。"""

    def __init__(self, devices: list[dict], parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("機器に接続")
        self.list = QListWidget()
        for d in devices:
            mark = "（前に使った機器）" if d.get("known") else ""
            item = QListWidgetItem(f"{d.get('serial') or '（シリアル番号なし）'}  {d.get('port')}  {d.get('description', '')}{mark}")
            item.setData(Qt.ItemDataRole.UserRole, d)
            self.list.addItem(item)
        if devices:
            self.list.setCurrentRow(0)
        note = QLabel(
            "USB ID が FTDI 0403:6001 のポートを並べています。この部品は GigaSt 以外の機器にも使われるので、\n"
            "初めての機器は確認の掃引を1回送り、GigaSt の応答が返るかを確かめてから繋ぎます。"
            if devices else "GigaSt が見つかりません。USB ケーブルを確かめてください。"
        )
        note.setWordWrap(True)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("接続")
        buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(bool(devices))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addWidget(note)
        lay.addWidget(self.list)
        lay.addWidget(buttons)

    def selected(self) -> Optional[dict]:
        item = self.list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None
