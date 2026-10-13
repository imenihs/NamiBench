"""上段の連続・単発・停止と、その真上の行の掃引の進み具合（設計J §4.13）。"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QFontMetrics
from PySide6.QtWidgets import QHBoxLayout, QLabel, QProgressBar, QPushButton, QSizePolicy, QVBoxLayout, QWidget

from . import sizes, texts

# 名前の行と値（ボタン）の間。上段の値の欄（fields.ValueField）と同じにして、欄の名前の行と同じ高さに並べる
NAME_ROW_SPACING = 2


class RunButtons(QWidget):
    """連続・単発・停止のボタンの並びと、その真上の名前の行（小の段 20px）に置く細い棒と割合（「37%」）。

    棒と割合は、コアが出すと決めた間（1画面の見積もりが 1 秒を超える掃引をしている間）だけ見せる。出さないときも
    場所は空けたまま（ボタンを上下に動かさない）。行の幅はボタンの並びの幅で、棒は割合の文字の左の残りに伸ばす。
    """

    def __init__(self) -> None:
        super().__init__()
        self.progress_row = QWidget()
        self.progress_row.setFixedHeight(sizes.H_S)
        row = QHBoxLayout(self.progress_row)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(sizes.GAP)
        tip = texts.LABELS["sweep_progress_tip"]
        self.bar = QProgressBar()
        self.bar.setObjectName("sweepProgress")
        self.bar.setRange(0, 100)
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(sizes.PROGRESS_BAR_H)
        self.bar.setMinimumWidth(0)
        self.bar.setToolTip(tip)
        # 行の幅を決めない（ボタンの並びの幅に従う）。隠しても場所を空けたまま
        pol = QSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        pol.setRetainSizeWhenHidden(True)
        self.bar.setSizePolicy(pol)
        self.text = QLabel("")
        self.text.setObjectName("dim")
        sizes.set_tier(self.text, "S")
        self.text.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.text.setToolTip(tip)
        # 割合の文字の幅は「100%」が入る幅で固定する（桁が変わっても棒の長さが揺れない）。字は小の段の大きさで測る
        f = QFont(self.text.font())
        f.setPixelSize(sizes.FONT_S)
        self.text.setFixedWidth(QFontMetrics(f).horizontalAdvance("0") * sizes.PROGRESS_TEXT_CH)
        tpol = self.text.sizePolicy()
        tpol.setRetainSizeWhenHidden(True)
        self.text.setSizePolicy(tpol)
        row.addWidget(self.bar, 1, Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(self.text, 0, Qt.AlignmentFlag.AlignVCenter)
        self.buttons = QHBoxLayout()
        self.buttons.setContentsMargins(0, 0, 0, 0)
        self.buttons.setSpacing(sizes.GAP)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(NAME_ROW_SPACING)
        lay.addWidget(self.progress_row)
        lay.addLayout(self.buttons)
        self.set_progress({"visible": False})

    def add_button(self, b: QPushButton) -> None:
        self.buttons.addWidget(b)

    def set_progress(self, v: dict) -> None:
        """screen_model.sweep_progress_view の形を映す（Web の上段と同じ）。"""
        shown = bool(v.get("visible"))
        self.bar.setValue(int(v.get("percent") or 0))
        self.text.setText(v.get("text") or "")
        self.bar.setVisible(shown)
        self.text.setVisible(shown)
