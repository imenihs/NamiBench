"""NF 測定のつなぎ方の窓（設計M: 必要な外部機器とつなぎ方を、NF モードに初めて入ったときに1回だけ出す）。

信号源のタブの「NF 測定のつなぎ方…」からも開く（窓の題はそのボタンの名前: U-24）。中身は手順だけ（要る機器・
つなぎ方の図と1行。校正の画面の準備と同じ形: K §4.1）で、説明の文は置かない（U-06・U-26）。
中身は nf_view.guide_view（Web の app_nf.js と同じ）。
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QLabel, QPushButton, QVBoxLayout, QWidget

from . import nf_view, sizes
from .cal_dialog import _Diagram

if TYPE_CHECKING:
    from .main_window import MainWindow


class NfGuideDialog(QDialog):
    def __init__(self, win: "MainWindow") -> None:
        super().__init__(win)
        self.win = win
        v = nf_view.guide_view()
        self.setWindowTitle(v["title"])
        lay = sizes.vbox(self, inset=False)
        sizes.window(lay)
        body = QWidget()
        f = sizes.form(body)
        f.setContentsMargins(0, 0, 0, 0)
        self.need = QLabel(v["need"])
        f.addRow(v["need_label"], self.need)
        wiring = QVBoxLayout()
        wiring.setContentsMargins(0, 0, 0, 0)
        wiring.setSpacing(sizes.GAP)
        self.diagram = _Diagram(win)
        self.diagram.set_blocks(v["diagram"])
        self.wiring = QLabel(v["wiring"])
        wiring.addWidget(self.diagram, 0, Qt.AlignmentFlag.AlignLeft)
        wiring.addWidget(self.wiring)
        box = QWidget()
        box.setLayout(wiring)
        f.addRow(v["wiring_label"], box)
        lay.addWidget(body)
        lay.addStretch(1)
        self.btn_close = QPushButton(v["close"])
        self.btn_close.clicked.connect(self.close)
        self.btn_close.setDefault(True)
        lay.addLayout(sizes.buttons_row(self.btn_close, right=True))


def open_guide(win: "MainWindow") -> NfGuideDialog:
    """窓は1つだけ。開いていれば前に出す。主の窓を止めない（測りながら見られる）。"""
    d = getattr(win, "nf_guide", None)
    if d is None:
        d = NfGuideDialog(win)
        win.nf_guide = d
    d.show()
    d.raise_()
    d.activateWindow()
    return d
