"""数値の欄（設計J §3）。

- 単位付きの自由入力（`1.2G`・`300k`・単位を省いたら欄の既定単位）
- 確定は Enter・Tab・欄を離れる のどれでも。Esc は打つ前の値に戻す（J §4.6）
- ホイールと ↑↓ はカーソルのある桁を1つ増減（実機のノブの感覚）
- 名前を上、値を下に置き、欄の下に一度きりの知らせ（「12100MHz に合わせました」）を出す（M-02）
"""
from __future__ import annotations

from decimal import Decimal
from typing import Callable, Optional

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QFont, QKeyEvent, QWheelEvent
from PySide6.QtWidgets import QLabel, QLineEdit, QVBoxLayout, QWidget

from ..core.units import ParseError, parse_db, parse_freq_hz

NOTE_SHOW_MS = 4000


class _Edit(QLineEdit):
    """確定・取り消し・桁の増減を持つ入力の行。"""

    committed = Signal(str)
    stepped = Signal(int, int)  # (桁の位置: 小数点からの 10 の指数, 向き ±1)

    def __init__(self) -> None:
        super().__init__()
        self._shown = ""
        self.editingFinished.connect(self._on_finished)

    def show_value(self, text: str) -> None:
        self._shown = text
        if not self.hasFocus() or not self.isModified():
            pos = self.cursorPosition()
            self.setText(text)
            self.setModified(False)
            self.setCursorPosition(min(pos, len(text)))

    def _on_finished(self) -> None:
        if self.isModified():
            self.setModified(False)
            self.committed.emit(self.text())

    def keyPressEvent(self, e: QKeyEvent) -> None:
        if e.key() == Qt.Key.Key_Escape:
            self.setText(self._shown)
            self.setModified(False)
            return
        if e.key() in (Qt.Key.Key_Up, Qt.Key.Key_Down):
            self._step(+1 if e.key() == Qt.Key.Key_Up else -1)
            return
        super().keyPressEvent(e)

    def wheelEvent(self, e: QWheelEvent) -> None:
        dy = e.angleDelta().y()
        if dy:
            self._step(+1 if dy > 0 else -1)
        e.accept()

    def _step(self, direction: int) -> None:
        exp = self._digit_exponent()
        if exp is not None:
            self.stepped.emit(exp, direction)

    def _digit_exponent(self) -> Optional[int]:
        """カーソルの左の桁が、小数点から見て 10 の何乗の位か。数字でなければ None。"""
        text = self.text()
        pos = self.cursorPosition()
        num_end = 0
        while num_end < len(text) and (text[num_end].isdigit() or text[num_end] in "+-."):
            num_end += 1
        idx = min(pos, num_end) - 1
        if idx < 0 or not text[idx].isdigit():
            # 先頭にいるときは最上位の桁
            idx = next((i for i in range(num_end) if text[i].isdigit()), -1)
            if idx < 0:
                return None
        number = text[:num_end]
        dot = number.find(".")
        if dot < 0:
            dot = len(number)
        return dot - idx - 1 if idx < dot else dot - idx


class ValueField(QWidget):
    """名前・値・知らせを縦に並べた欄。"""

    def __init__(self, name: str, big: bool = True) -> None:
        super().__init__()
        self.name_label = QLabel(name)
        self.edit = _Edit()
        self.note = QLabel("")
        self.note.setObjectName("note")
        self.note.setWordWrap(False)
        f = QFont(self.edit.font())
        f.setPointSize(15 if big else 11)
        self.edit.setFont(f)
        self.edit.setMinimumWidth(128 if big else 84)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 2, 4, 2)
        lay.setSpacing(1)
        lay.addWidget(self.name_label)
        lay.addWidget(self.edit)
        lay.addWidget(self.note)
        self._note_timer = QTimer(self)
        self._note_timer.setSingleShot(True)
        self._note_timer.timeout.connect(lambda: self.note.setText(""))

    def flash_note(self, text: str) -> None:
        """一度きりの知らせ。状態としては欄の値が示す（M-02）。"""
        self.note.setText(text)
        self.note.setToolTip(text)
        self._note_timer.start(NOTE_SHOW_MS)


class FreqField(ValueField):
    """周波数の欄。値は Hz の int。表示は MHz の固定小数（桁を落とさない）。"""

    def __init__(self, name: str, on_commit: Callable[[int], None], big: bool = True) -> None:
        super().__init__(name, big)
        # 値が全部見える幅を取る（J §4.3: 欄が切れて値の一部しか見えないのを作らない）。100Hz 単位の最長の形で測る
        widest = self.edit.fontMetrics().horizontalAdvance("-12100.0000 MHz")
        self.edit.setMinimumWidth(widest + 16)
        self._hz = 0
        self._on_commit = on_commit
        self.edit.committed.connect(self._commit_text)
        self.edit.stepped.connect(self._stepped)

    @staticmethod
    def _to_text(hz: int) -> str:
        s = format(Decimal(hz).scaleb(-6), "f")
        if "." in s:
            s = s.rstrip("0").rstrip(".")
        if "." not in s:
            s += ".000"
        else:
            whole, frac = s.split(".")
            s = whole + "." + frac.ljust(3, "0")
        return s + " MHz"

    def set_hz(self, hz: int) -> None:
        self._hz = hz
        self.edit.show_value(self._to_text(hz))

    def _commit_text(self, text: str) -> None:
        try:
            hz = parse_freq_hz(text)
        except ParseError:
            self.flash_note("読めない値です（例: 1.2G・300k・1500）")
            self.edit.show_value(self._to_text(self._hz))
            return
        self._on_commit(hz)

    def _stepped(self, exp: int, direction: int) -> None:
        # 欄の数字は MHz なので、10^exp MHz = 10^(exp+6) Hz
        step = 10 ** (exp + 6) if exp + 6 >= 0 else 1
        self._on_commit(self._hz + direction * step)


class DbField(ValueField):
    """dB の欄（REF レベルなど）。値は float。表示だけの値なのでコアへ送らない。"""

    def __init__(self, name: str, unit: str, on_commit: Callable[[float], None], big: bool = True) -> None:
        super().__init__(name, big)
        self._value = 0.0
        self._unit = unit
        self._on_commit = on_commit
        self.edit.committed.connect(self._commit_text)
        self.edit.stepped.connect(self._stepped)

    def set_value(self, v: float) -> None:
        self._value = v
        self.edit.show_value(f"{v:.1f} {self._unit}")

    def _commit_text(self, text: str) -> None:
        try:
            v = parse_db(text.replace(self._unit, ""))
        except ParseError:
            self.flash_note("読めない値です")
            self.set_value(self._value)
            return
        self._on_commit(v)

    def _stepped(self, exp: int, direction: int) -> None:
        self._on_commit(self._value + direction * (10.0 ** exp))
