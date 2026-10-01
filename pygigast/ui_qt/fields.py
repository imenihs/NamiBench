"""数値の欄（設計J §3）。

- 単位付きの自由入力（`1.2G`・`300k`・単位を省いたら欄の既定単位）
- 確定は Enter・Tab・欄を離れる のどれでも。Esc は打つ前の値に戻す（J §4.6）
- ホイールと ↑↓ はカーソルのある桁を1つ増減（実機のノブの感覚）
- 名前を上、値を下に置き、欄の下に一度きりの知らせ（「12100MHz に合わせました」）を出す（M-02）
- 知らせは欄の下に重ねて出す（並びを押し広げない）。知らせにボタンを添えられる（設計C §5.11 の 3）。
  ボタンは知らせと一緒に消える
"""
from __future__ import annotations

from decimal import Decimal
from typing import Callable, Optional

from PySide6.QtCore import QEvent, QPoint, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QIcon, QKeyEvent, QKeySequence, QPainter, QPen, QPixmap, QWheelEvent
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..core.units import MINUS, ParseError, minus_sign, parse_db, parse_freq_hz

NOTE_SHOW_MS = 4000
# ボタンを添えた知らせは、読んで押すまでの時間を取る。マウスが知らせの上にある間は消さない
NOTE_ACTION_SHOW_MS = 10000


class _Edit(QLineEdit):
    """確定・取り消し・桁の増減を持つ入力の行。"""

    committed = Signal(str)
    stepped = Signal(int, int)  # (桁の位置: 小数点からの 10 の指数, 向き ±1)

    def __init__(self) -> None:
        super().__init__()
        self._shown = ""
        # ホイールで増減する桁を決めておく欄（REF レベル: 1目盛で 1dB）。None はカーソルのある桁
        self.wheel_exp: Optional[int] = None
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

    def event(self, e) -> bool:
        # 打っている途中の Ctrl+Z / Ctrl+Y は欄の文字を戻す（キーは欄に渡す: J §4.6）。
        # 打っていないときは欄に戻すものが無いので、条件の変更を戻す（アプリの Ctrl+Z）へ回す
        if e.type() == QEvent.Type.ShortcutOverride and not self.isModified():
            if e.matches(QKeySequence.StandardKey.Undo) or e.matches(QKeySequence.StandardKey.Redo):
                e.ignore()
                return True
        return super().event(e)

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
        if dy and self.wheel_exp is not None:
            self.stepped.emit(self.wheel_exp, +1 if dy > 0 else -1)
        elif dy:
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
        while num_end < len(text) and (text[num_end].isdigit() or text[num_end] in "+-." + MINUS):
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


class _NotePopup(QFrame):
    """欄の下に重ねて出す一度きりの知らせ。ボタンを1つ添えられる。"""

    def __init__(self, owner: QWidget) -> None:
        super().__init__(owner)
        self.setObjectName("notePopup")
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.label = QLabel("")
        self.label.setObjectName("note")
        self.button = QPushButton("")
        self.button.setObjectName("noteAction")
        self.button.hide()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 3, 6, 4)
        lay.setSpacing(3)
        lay.addWidget(self.label)
        lay.addWidget(self.button)
        self.hide()
        self._hovered = False

    def enterEvent(self, e) -> None:
        self._hovered = True
        super().enterEvent(e)

    def leaveEvent(self, e) -> None:
        self._hovered = False
        super().leaveEvent(e)


class ValueField(QWidget):
    """名前・値を縦に並べ、一度きりの知らせを欄の下に重ねて出す欄。

    名前の行の右には、欄に付く小さな部品（Span 固定・REF の自動など）を置ける（U-14: 付属するものを離さない）。
    """

    def __init__(self, name: str, big: bool = True) -> None:
        super().__init__()
        self.name_label = QLabel(name)
        self.edit = _Edit()
        f = QFont(self.edit.font())
        f.setPointSize(15 if big else 11)
        self.edit.setFont(f)
        self.edit.setMinimumWidth(128 if big else 84)
        self.header = QHBoxLayout()
        self.header.setContentsMargins(0, 0, 0, 0)
        self.header.setSpacing(4)
        self.header.addWidget(self.name_label)
        self.header.addStretch(1)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 2, 4, 2)
        lay.setSpacing(1)
        lay.addLayout(self.header)
        lay.addWidget(self.edit)
        self._popup = _NotePopup(self)
        self.note = self._popup.label
        self.note_action = self._popup.button
        self._action_cb: Optional[Callable[[], None]] = None
        self.note_action.clicked.connect(self._action_clicked)
        self._note_timer = QTimer(self)
        self._note_timer.setSingleShot(True)
        self._note_timer.timeout.connect(self._note_timeout)

    def add_header_widget(self, w: QWidget) -> None:
        self.header.addWidget(w)

    def flash_note(self, text: str, action: Optional[tuple[str, Callable[[], None]]] = None) -> None:
        """一度きりの知らせ。状態としては欄の値が示す（M-02）。action は（ボタンの文字, 押したときの処理）。"""
        self.note.setText(text)
        self.note.setToolTip(text)
        self._action_cb = action[1] if action else None
        if action:
            self.note_action.setText(action[0])
            self.note_action.show()
        else:
            self.note_action.hide()
        self._show_popup()
        self._note_timer.start(NOTE_ACTION_SHOW_MS if action else NOTE_SHOW_MS)

    def clear_note(self) -> None:
        self._note_timer.stop()
        self.note.setText("")
        self.note_action.hide()
        self._action_cb = None
        self._popup.hide()

    def _note_timeout(self) -> None:
        if self._popup._hovered:
            # 読んでいる・押そうとしている間は消さない
            self._note_timer.start(1000)
            return
        self.clear_note()

    def _action_clicked(self) -> None:
        cb = self._action_cb
        self.clear_note()
        if cb:
            cb()

    def _show_popup(self) -> None:
        # 窓の上に重ねる（上段の並びを押し広げない）。窓の端からはみ出さない位置へ置く
        host = self.window()
        pop = self._popup
        if pop.parent() is not host:
            pop.setParent(host)
        pop.adjustSize()
        pos = self.mapTo(host, QPoint(0, self.height()))
        x = max(0, min(pos.x(), host.width() - pop.width()))
        pop.move(x, pos.y())
        pop.show()
        pop.raise_()


def _lock_icon(color: str, closed: bool) -> QIcon:
    """鍵の印（閉じた鍵＝固定中、開いた鍵＝固定していない）。文字の字形に頼らず描く。"""
    pm = QPixmap(16, 16)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    c = QColor(color)
    p.setPen(QPen(c, 1.6))
    p.setBrush(Qt.BrushStyle.NoBrush)
    shackle = QRectF(4.5, 1.5, 7, 9) if closed else QRectF(7.5, 0.5, 7, 9)
    p.drawArc(shackle, 0, 180 * 16)
    if closed:
        p.drawLine(QPointF(4.5, 6), QPointF(4.5, 8))
    p.drawLine(QPointF(11.5 if closed else 14.5, 6), QPointF(11.5 if closed else 14.5, 8))
    p.setBrush(c)
    p.drawRoundedRect(QRectF(2.5, 7.5, 11, 7.5), 1.5, 1.5)
    p.end()
    return QIcon(pm)


class SpanLockButton(QToolButton):
    """Span の欄の「Span 固定」の切り替え（設計C §5.11 の 2）。状態は欄の上で文字と鍵の印で見せる（M-04）。"""

    def __init__(self, on_color: str, off_color: str) -> None:
        super().__init__()
        self.setCheckable(True)
        self.setObjectName("spanLock")
        self.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self._icons = {True: _lock_icon(on_color, True), False: _lock_icon(off_color, False)}
        self.show_lock(None, None)

    def show_lock(self, lock_hz: Optional[int], span_hz: Optional[int], fmt: Callable[[int], str] = str) -> None:
        on = lock_hz is not None
        self.blockSignals(True)
        self.setChecked(on)
        self.blockSignals(False)
        self.setIcon(self._icons[on])
        if not on:
            self.setText("Span 固定")
        elif span_hz is not None and lock_hz != span_hz:
            # ボタンで反対の結果を選んだ後など、いまの Span と固定 Span が違うときは固定 Span も出す
            self.setText(f"固定 {fmt(lock_hz)}")
        else:
            self.setText("固定")


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

    def hz(self) -> int:
        return self._hz

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
    """dB の欄（REF レベルなど）。値は float。確定した値を on_commit へ渡す（どこへ送るかは呼び出し側）。"""

    def __init__(self, name: str, unit: str, on_commit: Callable[[float], None], big: bool = True) -> None:
        super().__init__(name, big)
        self._value = 0.0
        self._unit = unit
        self._on_commit = on_commit
        self._base_width = self.edit.minimumWidth()
        self.edit.committed.connect(self._commit_text)
        self.edit.stepped.connect(self._stepped)

    def set_value(self, v: float) -> None:
        self._value = v
        self.edit.show_value(f"{v:.1f} {self._unit}".rstrip())

    def set_unit(self, unit: str) -> None:
        if unit != self._unit:
            self._unit = unit
            # 値と単位が全部見える幅を取る（「dB（未校正）」のような長い単位でも切らない: J §4.3）
            widest = self.edit.fontMetrics().horizontalAdvance(f"-000.0 {unit}")
            self.edit.setMinimumWidth(max(self._base_width, widest + 16))
            self.set_value(self._value)

    def value(self) -> float:
        return self._value

    def _commit_text(self, text: str) -> None:
        try:
            v = parse_db(text.replace(self._unit, "") if self._unit else text)
        except ParseError:
            self.flash_note("読めない値です")
            self.set_value(self._value)
            return
        self._on_commit(v)

    def _stepped(self, exp: int, direction: int) -> None:
        self._on_commit(self._value + direction * (10.0 ** exp))


# REF レベルの一覧（J §4.1: +20〜−120 を 10dB 刻み）
REF_CHOICES = tuple(range(20, -121, -10))


def ref_choice_text(v: int, unit: str) -> str:
    return minus_sign(f"{v:+d}" if v else "0") + (f" {unit}" if unit else "")


class _RefCombo(QComboBox):
    """一覧の矢印の上のホイールも、欄の上と同じく 1dB ずつにする（一覧の行を送らない）。"""

    def __init__(self, edit: "_Edit") -> None:
        super().__init__()
        self._edit = edit

    def wheelEvent(self, e: QWheelEvent) -> None:
        dy = e.angleDelta().y()
        if dy:
            self._edit.stepped.emit(0, +1 if dy > 0 else -1)
        e.accept()


class RefLevelField(DbField):
    """REF レベルの欄（J §4.1）: 打てるドロップダウン（+20〜−120 を 10dB 刻み）と、欄の上のホイールで 1dB。

    ホイールはカーソルの桁によらず1目盛で 1dB（J §3 の桁の増減の例外）。↑↓ と直接入力（例 −37.5）は
    ほかの数値の欄と同じ。負の数は「−」の字形で出し、打つときは「-」でも「−」でもよい。
    """

    def __init__(self, name: str, unit: str, on_commit: Callable[[float], None], big: bool = True) -> None:
        super().__init__(name, unit, on_commit, big)
        self.combo = _RefCombo(self.edit)
        self.combo.setEditable(True)
        self.combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.layout().replaceWidget(self.edit, self.combo)
        self.combo.setLineEdit(self.edit)
        self.combo.setCompleter(None)  # 打っている途中に一覧の値で補わない（「+1」が「+10」にならない）
        self.edit.wheel_exp = 0
        self.combo.setFont(self.edit.font())
        self._fit_width()
        self._fill_choices()
        self.combo.activated.connect(self._chosen)

    def _fill_choices(self) -> None:
        self.combo.blockSignals(True)
        self.combo.clear()
        for v in REF_CHOICES:
            self.combo.addItem(ref_choice_text(v, self._unit), float(v))
        self.combo.setCurrentIndex(-1)
        self.combo.blockSignals(False)
        self.edit.show_value(self.edit._shown)

    def _chosen(self, i: int) -> None:
        v = self.combo.itemData(i)
        if v is not None:
            self._on_commit(float(v))

    def set_value(self, v: float) -> None:
        self._value = v
        self.edit.show_value(minus_sign(f"{v:.1f} {self._unit}".rstrip()))

    def set_unit(self, unit: str) -> None:
        if unit != self._unit:
            super().set_unit(unit)
            self._fit_width()
            self._fill_choices()

    def _fit_width(self) -> None:
        # 値と単位が全部見え、一覧の矢印の分も取る（J §4.3: 欄を切らない）
        widest = self.edit.fontMetrics().horizontalAdvance(f"{MINUS}120.0 {self._unit}")
        self.combo.setMinimumWidth(max(self.edit.minimumWidth(), widest + 16) + 24)
