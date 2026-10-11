"""数値の欄（設計J §3）。

- 単位付きの自由入力（`1.2G`・`300k`・単位を省いたら欄の既定単位）
- 確定は Enter・Tab・欄を離れる のどれでも。Esc は打つ前の値に戻す（J §4.6）
- ホイールと ↑↓ はカーソルのある桁を1つ増減（実機のノブの感覚）。スクロールする場所（右のパネルなど）の中の欄は、
  フォーカスがあるときだけホイールが届く（アプリ全体の決まり: wheel_guard.py）
- 名前を上、値を下に置き、欄の下に一度きりの知らせ（「12100MHz に合わせました」）を出す（M-02）
- 知らせは欄の下に重ねて出す（並びを押し広げない）。知らせにボタンを添えられる（設計C §5.11 の 3）。
  ボタンは知らせと一緒に消える
"""
from __future__ import annotations

import math
from decimal import Decimal
from typing import Callable, Optional

from PySide6.QtCore import QEvent, QPoint, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QIcon, QKeyEvent, QKeySequence, QPainter, QPen, QPixmap, QWheelEvent
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

from ..core.units import (MINUS, ParseError, format_time_us, level_text, minus_sign, parse_db, parse_freq_hz,
                          parse_time_us)
from . import sizes
from .texts_labels import LABELS
from .texts_zerospan import ZERO_SPAN_TEXTS

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
        # この欄で確定した・桁を増減した値の戻りを待っている。フォーカスがあっても戻りの値は映す
        # （Enter で確定した値・↑↓ で送った値がその場で見える）。利用者が次に触ると解く
        self._echo = False
        # ホイールで増減する桁を決めておく欄（REF レベル: 1目盛で 1dB）。None はカーソルのある桁
        self.wheel_exp: Optional[int] = None
        self.editingFinished.connect(self._on_finished)

    def show_value(self, text: str) -> None:
        """状態の値を映す。フォーカスがある間は書かない（選んだ範囲・カーソル・打っている途中の文字を消さない）。

        書かなかった値はフォーカスが外れたときに入れる（focusOutEvent）。表示が同じなら書かない。
        """
        self._shown = text
        if self.hasFocus() and not self._echo:
            return
        if self.isModified() or self.text() == text:
            return
        pos = self.cursorPosition()
        self.setText(text)
        self.setModified(False)
        self.setCursorPosition(min(pos, len(text)))

    def _on_finished(self) -> None:
        if self.isModified():
            self.setModified(False)
            self._echo = True
            self.committed.emit(self.text())

    def focusInEvent(self, e) -> None:
        self._echo = False
        super().focusInEvent(e)

    def focusOutEvent(self, e) -> None:
        committing = self.isModified()
        super().focusOutEvent(e)  # 打った値はここで確定する（editingFinished）
        self._echo = False
        if not committing and not self.isModified() and self.text() != self._shown:
            # フォーカスがある間に届いた最新の値を入れる
            self.setText(self._shown)
            self.setModified(False)

    def mousePressEvent(self, e) -> None:
        self._echo = False  # 範囲を選び始めた。以後は書き直さない
        super().mousePressEvent(e)

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
        if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            # 確定のキーは戻りを映す印を消さない（打てるドロップダウンでは同じ Enter が2回届く）
            super().keyPressEvent(e)
            return
        self._echo = False  # 打つ・範囲を選ぶ・カーソルを動かす。以後は書き直さない（Enter は確定で戻る）
        super().keyPressEvent(e)

    def wheelEvent(self, e: QWheelEvent) -> None:
        dy = e.angleDelta().y()
        if dy and self.wheel_exp is not None:
            self._echo = True
            self.stepped.emit(self.wheel_exp, +1 if dy > 0 else -1)
        elif dy:
            self._step(+1 if dy > 0 else -1)
        e.accept()

    def _step(self, direction: int) -> None:
        exp = self._digit_exponent()
        if exp is not None:
            self._echo = True
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
    """値の欄。一度きりの知らせを欄の下に重ねて出す。大きさは段で決まる（設計J §4.11・S-01・S-05）。

    - 上段（big=True）: 名前を値の上に小さく置き、欄は上段の段（高さ 40px・文字 20px）。名前の行の右には、欄に付く
      小さな部品（Span 固定・REF の自動など）を置ける（U-14: 付属するものを離さない）
    - 標準の段（big=False）: 名前は欄の左（呼ぶ側が QFormLayout の名前の列に `field.name` を置く）。欄に付く部品は
      欄の右に並べる。幅は値の種類で決める（周波数・レベルは「値」の幅）
    """

    def __init__(self, name: str, big: bool = True) -> None:
        super().__init__()
        self.name = name
        self.big = big
        self.name_label = QLabel(name)
        self.edit = _Edit()
        self.header = QHBoxLayout()
        self.header.setContentsMargins(0, 0, 0, 0)
        self.header.setSpacing(sizes.GAP)
        if big:
            # 名前の行は小の段の高さ（見た目の決まりで固定。行に部品を足した欄と足さない欄で、値の欄の位置が変わらない）
            sizes.set_tier(self.name_label, "S")
            sizes.set_tier(self.edit, "L")
            self.header.addWidget(self.name_label)
            self.header.addStretch(1)
            lay = QVBoxLayout(self)
            lay.setContentsMargins(0, 0, 0, 0)
            lay.setSpacing(2)
            lay.addLayout(self.header)
            lay.addWidget(self.edit)
        else:
            self.name_label.hide()
            sizes.set_width(self.edit, "value")
            lay = QHBoxLayout(self)
            lay.setContentsMargins(0, 0, 0, 0)
            lay.setSpacing(sizes.GAP)
            lay.addWidget(self.edit)
            lay.addLayout(self.header)
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

    def set_colors(self, on_color: str, off_color: str) -> None:
        """配色を変えたとき（設計J §4.4）。"""
        self._icons = {True: _lock_icon(on_color, True), False: _lock_icon(off_color, False)}
        self.setIcon(self._icons[self.isChecked()])

    def show_lock(self, lock_hz: Optional[int], span_hz: Optional[int], fmt: Callable[[int], str] = str) -> None:
        on = lock_hz is not None
        self.blockSignals(True)
        self.setChecked(on)
        self.blockSignals(False)
        self.setIcon(self._icons[on])
        if not on:
            self.setText(LABELS["span_lock"])
        elif span_hz is not None and lock_hz != span_hz:
            # ボタンで反対の結果を選んだ後など、いまの Span と固定 Span が違うときは固定 Span も出す
            self.setText(LABELS["span_locked_at"].replace("{span}", fmt(lock_hz)))
        else:
            self.setText(LABELS["span_locked"])


class FreqField(ValueField):
    """周波数の欄。値は Hz の int。表示は MHz の固定小数（桁を落とさない）。

    ゼロスパンの横軸の位置を打つ欄（マーカー・XY カーソル）は set_time_axis(True) で時間の欄になる: 値はマイクロ秒の
    int、表示と打つ値は ms（設計N §1）。時間には周波数オフセットを当てない。
    """

    def __init__(self, name: str, on_commit: Callable[[int], None], big: bool = True) -> None:
        super().__init__(name, big)
        # 幅は一番長い値が全部見える幅で止め、伸ばさない（J §4.11 の S-02。上段の幅を分け合って伸ばすと、2段に
        # 折り返したときに4つの欄が横いっぱいに伸び、RBW 以降が次の行に押し出された: 2026-10-03 ユーザー指摘）。
        # 上段は上段の字で 100Hz 単位の最長の形を測る（J §4.3）。標準の段は「値」の幅（ValueField が決める）
        if big:
            f = QFont(self.edit.font())
            f.setPixelSize(sizes.FONT_L_VALUE)
            fm = QFontMetrics(f)
            text = max(fm.horizontalAdvance(t) for t in sizes.TOP_FREQ_SAMPLES)
            self.edit.setFixedWidth(text + 2 * 6 + 2 + 2 * sizes.EDIT_INNER + 1)
        self._hz = 0
        # 周波数オフセット（設計O）: 欄は「測った周波数 + これ」を出し、打った値からこれを引いて渡す
        self.offset_hz = 0
        self.time_axis = False
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

    def _shown(self) -> str:
        return format_time_us(self._hz) if self.time_axis else self._to_text(self._hz + self.offset_hz)

    def set_hz(self, hz: int) -> None:
        self._hz = hz
        self.edit.show_value(self._shown())

    def set_offset(self, offset_hz: int) -> None:
        if offset_hz != self.offset_hz:
            self.offset_hz = int(offset_hz)
            self.edit.show_value(self._shown())

    def set_time_axis(self, on: bool) -> None:
        """ゼロスパンの時間の欄にする・周波数の欄に戻す（値の意味が変わるので、呼ぶ側が新しい値を入れ直す）。"""
        if bool(on) != self.time_axis:
            self.time_axis = bool(on)
            self.edit.show_value(self._shown())

    def hz(self) -> int:
        return self._hz

    def _commit_text(self, text: str) -> None:
        if self.time_axis:
            try:
                us = parse_time_us(text)
            except ParseError:
                self.flash_note(ZERO_SPAN_TEXTS["bad_time"])
                self.edit.show_value(self._shown())
                return
            self._on_commit(us)
            return
        try:
            hz = parse_freq_hz(text)
        except ParseError:
            self.flash_note(LABELS["bad_freq"])
            self.edit.show_value(self._shown())
            return
        self._on_commit(hz - self.offset_hz)

    def _stepped(self, exp: int, direction: int) -> None:
        # 欄の数字は MHz なので、10^exp MHz = 10^(exp+6) Hz。時間の欄は ms なので 10^(exp+3) μs
        k = 3 if self.time_axis else 6
        step = 10 ** (exp + k) if exp + k >= 0 else 1
        self._on_commit(self._hz + direction * step)


class DbField(ValueField):
    """dB の欄（REF レベルなど）。値は float。確定した値を on_commit へ渡す（どこへ送るかは呼び出し側）。"""

    def __init__(self, name: str, unit: str, on_commit: Callable[[float], None], big: bool = True) -> None:
        super().__init__(name, big)
        self._value = 0.0
        self._unit = unit
        self._on_commit = on_commit
        self.edit.committed.connect(self._commit_text)
        self.edit.stepped.connect(self._stepped)

    def set_value(self, v: float) -> None:
        self._value = v
        self.edit.show_value(f"{v:.1f} {self._unit}".rstrip())

    def set_unit(self, unit: str) -> None:
        if unit != self._unit:
            self._unit = unit
            # 幅は「値」の幅で、一番長い単位「dB（未校正）」も入る（J §4.11。試験で測る）
            self.set_value(self._value)

    def value(self) -> float:
        return self._value

    def _commit_text(self, text: str) -> None:
        try:
            v = parse_db(text.replace(self._unit, "") if self._unit else text)
        except ParseError:
            self.flash_note(LABELS["bad_value"])
            self.set_value(self._value)
            return
        self._on_commit(v)

    def _stepped(self, exp: int, direction: int) -> None:
        self._on_commit(self._value + direction * (10.0 ** exp))


# REF レベルの一覧（J §4.1: +20〜−120 を 10dB 刻み）。しきい値との交点の一覧も同じ（G §3.6）
REF_CHOICES = tuple(range(20, -121, -10))
# ピークの一覧の「床から」と、N dB 帯域幅・2つ目の N の一覧（G §3.6）
FLOOR_CHOICES = (3, 6, 10, 15, 20, 30)
BANDWIDTH_CHOICES = (3, 6, 10, 20, 30, 40, 60)
# 打てる範囲（前の数の上下の欄と同じ。小数は1桁: G §3.6）。Web の fields.js の DB_RANGES と同じ値
BANDWIDTH_RANGE = (0.1, 200.0)
THRESHOLD_RANGE = (-300.0, 300.0)
FLOOR_RANGE = (0.0, 100.0)


def db_choice_text(v: float, unit: str, signed: bool = True) -> str:
    """一覧の行の文字（「+20 dBm」「−40 dBm」「3 dB」）。signed はレベル（REF・しきい値）で、正の数にも + を付ける。"""
    s = f"{v:+g}" if signed and v else f"{v:g}"
    return minus_sign(s) + (f" {unit}" if unit else "")


def db_value_text(v: float, decimals: Optional[int]) -> str:
    """欄の値の文字（単位は付けない。Web の fields.js の dbText と同じ）。小数2桁以上の欄（|Γ|・SWR のしきい値:
    設計R §3）はその桁、整数の欄（PlutoSDR の受信の利得: 設計Q §7）は整数、ほかは 0.1 まで（REF の欄と同じ）。"""
    if decimals == 0:
        s = f"{float(v):.0f}"
        return minus_sign("0" if s == "-0" else s)
    if (decimals or 1) <= 1:
        return level_text(v)
    return minus_sign(f"{float(v):.{decimals}f}")


def round_to(v: float, decimals: Optional[int]) -> float:
    """小数 decimals 桁へ四捨五入する（ちょうど半分は大きい方へ。Web の fields.js の roundTo と同じ）。None は丸めない。"""
    if decimals is None:
        return v
    k = 10 ** decimals
    return math.floor(v * k + 0.5) / k


def range_note(lo: float, hi: float, unit: str) -> str:
    """範囲の外の値を打ったときの知らせ（G §3.6。Web の fields.js の rangeNote と同じ文）。"""
    text = LABELS["db_range_note"]
    for k, v in (("lo", minus_sign(f"{lo:g}")), ("hi", minus_sign(f"{hi:g}")), ("unit", unit)):
        text = text.replace("{" + k + "}", v)
    return " ".join(text.split())  # 単位の無い欄で空白が2つ続かない


class _DbCombo(QComboBox):
    """一覧の矢印の上のホイールも、欄の上と同じく 1dB ずつにする（一覧の行を送らない）。"""

    def __init__(self, edit: "_Edit") -> None:
        super().__init__()
        self._edit = edit

    def wheelEvent(self, e: QWheelEvent) -> None:
        dy = e.angleDelta().y()
        if dy:
            self._edit._echo = True
            self._edit.stepped.emit(0, +1 if dy > 0 else -1)
        e.accept()


class DbChoiceField(DbField):
    """dB の打てるドロップダウン（REF レベル: J §4.1。マーカーの機能・ピークの一覧の dB: G §3.6）。

    - 一覧から選ぶ・数を打つ（例 −37.5）。確定は Enter・Tab・欄を離れる・一覧から選ぶ。Esc は打つ前の値に戻す
    - 欄の上（と一覧の矢印の上）のホイールはカーソルの桁によらず1目盛で 1dB（J §3 の桁の増減の例外）。↑↓ はカーソルの桁
    - 打っている間は状態の値で書き直さない（_Edit の決まり）
    - lo・hi を渡すと、その外の値は欄の下に範囲を知らせて元の値に戻す。↑↓・ホイールは端で止める
    - decimals を渡すと、打った値・送った値をその桁に丸める（0 なら整数で出す）
    - 負の数は「−」の字形で出し、打つときは「-」でも「−」でもよい
    - special を渡すと、一覧の先頭に数でない行（PlutoSDR の受信の利得の「自動」: 設計Q §7）を置く。選ぶか同じ文字を
      打って確定すると on_special を呼ぶ。show_special でその文字を欄に出す（↑↓・ホイールは渡した値から送る）
    """

    def __init__(self, name: str, unit: str, on_commit: Callable[[float], None], choices: tuple,
                 signed: bool = True, lo: Optional[float] = None, hi: Optional[float] = None,
                 decimals: Optional[int] = None, big: bool = True, special: Optional[str] = None,
                 on_special: Optional[Callable[[], None]] = None) -> None:
        super().__init__(name, unit, on_commit, big)
        self._choices = tuple(choices)
        self._signed = signed
        self.lo, self.hi, self.decimals = lo, hi, decimals
        self._special, self._on_special = special, on_special
        self.special_shown = False
        self.combo = _DbCombo(self.edit)
        self.combo.setEditable(True)
        self.combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.layout().replaceWidget(self.edit, self.combo)
        self.combo.setLineEdit(self.edit)
        # 欄の幅はドロップダウンが決める（ValueField が付けた固定の幅を外す。外さないと欄が ▼ の上に重なり、▼ を
        # 押しても一覧が開かなかった: 設計の PDCA 2巡目）
        self.edit.setMinimumWidth(0)
        self.edit.setMaximumWidth(16777215)
        self.combo.setCompleter(None)  # 打っている途中に一覧の値で補わない（「+1」が「+10」にならない）
        self.edit.wheel_exp = 0
        if big:
            sizes.set_tier(self.combo, "L")
        else:
            sizes.set_width(self.combo, "value")
        self._fit_width()
        self._fill_choices()
        self.combo.activated.connect(self._chosen)

    def _fill_choices(self) -> None:
        self.combo.blockSignals(True)
        self.combo.clear()
        if self._special:
            self.combo.addItem(self._special, None)
        for v in self._choices:
            self.combo.addItem(db_choice_text(v, self._unit, self._signed), float(v))
        self.combo.setCurrentIndex(-1)
        self.combo.blockSignals(False)
        self.edit.show_value(self.edit._shown)

    def choices(self) -> list[str]:
        return [self.combo.itemText(i) for i in range(self.combo.count())]

    def set_spec(self, choices, signed: bool, lo: Optional[float], hi: Optional[float],
                 decimals: Optional[int]) -> bool:
        """一覧・符号・範囲・桁を替える（しきい値の欄を |Γ|・SWR の値にする: 設計R §3）。替えたら True。"""
        spec = (tuple(float(c) for c in choices), bool(signed), lo, hi, decimals)
        if spec == (tuple(float(c) for c in self._choices), self._signed, self.lo, self.hi, self.decimals):
            return False
        self._choices, self._signed, (self.lo, self.hi, self.decimals) = spec[0], spec[1], spec[2:]
        self._fit_width()
        self._fill_choices()
        return True

    def _chosen(self, i: int) -> None:
        v = self.combo.itemData(i)
        if v is not None:
            self.edit._echo = True  # 選んだ値の戻りはフォーカスがあっても映す
            self._accept(float(v))
        elif self._special and self.combo.itemText(i) == self._special and self._on_special:
            self.edit._echo = True
            self._on_special()

    def _accept(self, v: float) -> None:
        self._on_commit(v)

    def _commit_text(self, text: str) -> None:
        if self._special and text.strip() == self._special:
            if self._on_special:
                self._on_special()
            return
        try:
            v = parse_db(text.replace(self._unit, "") if self._unit else text)
        except ParseError:
            v = math.nan
        if not math.isfinite(v):
            self.flash_note(LABELS["bad_value"])
            self.set_value(self._value)
            return
        v = round_to(v, self.decimals)
        if (self.lo is not None and v < self.lo) or (self.hi is not None and v > self.hi):
            self.flash_note(range_note(self.lo if self.lo is not None else -math.inf,
                                       self.hi if self.hi is not None else math.inf, self._unit))
            self.set_value(self._value)
            return
        self._accept(v)

    def _stepped(self, exp: int, direction: int) -> None:
        v = round_to(self._value + direction * (10.0 ** exp), self.decimals)
        if self.lo is not None:
            v = max(v, self.lo)
        if self.hi is not None:
            v = min(v, self.hi)
        self._accept(v)

    def set_value(self, v: float) -> None:
        self._value = v
        self.special_shown = False
        self.edit.show_value(f"{db_value_text(v, self.decimals)} {self._unit}".rstrip())

    def show_special(self, base: float) -> None:
        """数でない行の文字（「自動」）を欄に出す。base は ↑↓・ホイールで送るときの元の値。"""
        self._value = base
        self.special_shown = True
        self.edit.show_value(self._special or "")

    def set_unit(self, unit: str) -> None:
        if unit != self._unit:
            super().set_unit(unit)
            self._fit_width()
            self._fill_choices()

    def _fit_width(self) -> None:
        # 上段だけ: 値と単位が全部見え、一覧の矢印の分も取る（J §4.3: 欄を切らない）。標準の段は「値」の幅（J §4.11）
        if not self.big:
            return
        # 上段の字で測る（見た目の決まりで字が当たる前に測ると 13px の幅になり、「−10.0 dBm」の m が切れた:
        # 設計の PDCA 2巡目）
        f = QFont(self.combo.font())
        f.setPixelSize(sizes.FONT_L_VALUE)
        fm = QFontMetrics(f)
        ends = [x for x in (self.lo, self.hi) if x is not None] or [-120.0]
        widest = max(fm.horizontalAdvance(f"{level_text(x)} {self._unit}") for x in ends)
        self.combo.setFixedWidth(widest + 2 * 6 + 2 + 2 * sizes.EDIT_INNER + sizes.CHOICE_ARROW + 1)


class LocalDbChoiceField(DbChoiceField):
    """値を欄が持つ dB の打てるドロップダウン（G §3.6: マーカーの機能の N・しきい値、ピークの一覧の「床から」）。

    確定した値をまず欄に入れてから on_commit を呼ぶ（REF のように状態から戻ってくるのを待たない。機能を切っている
    間の値は欄にだけある）。
    """

    def _accept(self, v: float) -> None:
        self.edit._echo = True
        self.set_value(v)
        self._on_commit(v)


class RefLevelField(DbChoiceField):
    """REF レベルの欄（J §4.1）: 一覧は +20〜−120 を 10dB 刻み。範囲は持たず、丸めない（打った値のまま）。"""

    def __init__(self, name: str, unit: str, on_commit: Callable[[float], None], big: bool = True) -> None:
        super().__init__(name, unit, on_commit, REF_CHOICES, signed=True, big=big)


# トレースの回数の一覧（設計F §2。打てば 1〜COUNT_MAX のどれでも）
COUNT_CHOICES = (2, 4, 8, 16, 32, 64, 128, 256, 512, 1000)
COUNT_MAX = 10000
_NO_COUNT = -1  # 一覧の「回数なし」の行の印（itemData の None は「印なし」と見分けられないため）


class CountField(QComboBox):
    """トレースの回数の欄: 打てるドロップダウン（一覧から選ぶ・数を打つ）。

    - 一覧は「回数なし」（回数を持てる動作のときだけ）と COUNT_CHOICES。打てば 1〜COUNT_MAX
    - 「回数なし」は一覧から選ぶか、欄を空にして確定する
    - ↑↓・ホイールは 1 ずつ（カーソルのある桁: 数値の欄と同じ）。確定は Enter・Tab・欄を離れる。Esc は打つ前に戻す
    - 状態の値は打っている間は書かない（_Edit と同じ。フォーカスが外れたら入れる）
    """

    invalid = Signal()

    def __init__(self, on_commit: Callable[[Optional[int]], None], no_count_text: str, suffix: str) -> None:
        super().__init__()
        self._on_commit = on_commit
        self._no_count = no_count_text
        self._suffix = suffix
        self._value: Optional[int] = None
        self._allow_none: Optional[bool] = None
        self.edit = _Edit()
        self.setEditable(True)
        self.setLineEdit(self.edit)
        self.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.setCompleter(None)  # 打っている途中に一覧の値で補わない（「1」が「16」にならない）
        self.edit.committed.connect(self._commit_text)
        self.edit.stepped.connect(self._stepped)
        # 一覧から選んだときだけ受ける。欄を離れたときにも、欄の文字と同じ行があると QComboBox が activated を出すので
        # （古い表示の値を送り返してしまう）、一覧を開いていた間の選択に限る
        self._from_list = False
        self.activated.connect(self._activated)
        self.set_state(None, True)

    def showPopup(self) -> None:
        self._from_list = True
        super().showPopup()

    def hidePopup(self) -> None:
        super().hidePopup()
        QTimer.singleShot(0, self._list_closed)  # 一覧の行を選んだ activated は、閉じたのと同じ流れの中で届く

    def _list_closed(self) -> None:
        self._from_list = False

    def _activated(self, i: int) -> None:
        if self._from_list:
            self._from_list = False
            self.choose(i)

    def text_of(self, v: Optional[int]) -> str:
        return self._no_count if v is None else f"{v}{self._suffix}"

    def value(self) -> Optional[int]:
        return self._value

    def set_state(self, value: Optional[int], allow_none: bool) -> None:
        """状態の値と、「回数なし」を選べるか（平均は回数が要る: 設計F §2）。"""
        if allow_none != self._allow_none:
            self._allow_none = allow_none
            self.blockSignals(True)
            self.clear()
            if allow_none:
                self.addItem(self._no_count, _NO_COUNT)
            for v in COUNT_CHOICES:
                self.addItem(self.text_of(v), v)
            self.setCurrentIndex(-1)
            self.blockSignals(False)
            self.edit._shown = ""  # 一覧を入れ替えると欄の文字が消えるので、次の show_value で必ず書く
        self._value = value
        self.edit.show_value(self.text_of(value))

    def _send(self, v: Optional[int]) -> None:
        self.edit._echo = True  # 確定した値の戻りはフォーカスがあっても映す
        if v == self._value:
            self.edit.setText(self.text_of(v))
            self.edit.setModified(False)
            return
        self._on_commit(v)

    def choose(self, i: int) -> None:
        """一覧の i 行目を選ぶ（一覧から選んだのと同じ）。"""
        d = self.itemData(i)
        if d is None:
            return
        self._send(None if d == _NO_COUNT else int(d))

    def _commit_text(self, text: str) -> None:
        t = text.strip()
        # 「回数なし」は単位の字（回）を含むので、単位を取り除く前に比べる
        if t != self._no_count and self._suffix.strip():
            t = t.replace(self._suffix.strip(), "").strip()
        if t in ("", self._no_count):
            if self._allow_none:
                self._send(None)
                return
        elif t.isdigit() and 1 <= int(t) <= COUNT_MAX:
            self._send(int(t))
            return
        self.edit.setText(self.text_of(self._value))
        self.edit.setModified(False)
        self.invalid.emit()

    def _stepped(self, _exp: int, direction: int) -> None:
        exp = max(_exp, 0)
        lo = 0 if self._allow_none else 1
        v = min(max((self._value or 0) + direction * 10 ** exp, lo), COUNT_MAX)
        self._send(v or None)

    def wheelEvent(self, e: QWheelEvent) -> None:
        # 一覧の矢印の上のホイールも 1 ずつ（一覧の行を送らない）
        dy = e.angleDelta().y()
        if dy:
            self._stepped(0, +1 if dy > 0 else -1)
        e.accept()
