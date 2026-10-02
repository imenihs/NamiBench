"""スクロールする場所の欄を、ホイールで黙って変えない（設計J §4.10 の 1）。

右のパネルのタブ・マーカー表など、スクロールする場所にある一覧・数値の欄・打てるドロップダウンは、クリックして
フォーカスがあるときだけホイールで値を変える。フォーカスが無ければ、ホイールはその場所のスクロールに回す
（場所をスクロールしているつもりで、ポインタの下に来た欄の値が変わるのを防ぐ）。スクロールしない場所
（上段の Center・Span・Start・Stop・RBW・REF・dB/div）は今まで通り、ポインタを置くだけでホイールで変わる。

欄ごとに書くと付け忘れが出るので、アプリ全体に1つの event filter を付けて決める（install）。

- 対象の欄: QComboBox・QAbstractSpinBox・QLineEdit（数値の欄と、打てるドロップダウン・数の上下の欄の中の行）・スライダー（音量など。スクロールバーは除く）。
  ホイールで値が変わらない行（ただの QLineEdit）も対象に入れるが、その行はもともとホイールを親へ回すので結果は同じ
- スクロールする場所の中か: 親をたどり、QAbstractScrollArea の表示域（viewport）を通るか。スクロールバーの横に
  付けた部品のような表示域の外は数えない。窓（ダイアログ・切り離した窓）の境で止める（窓の親の中には数えない）。
  スクロールの範囲がいまあるかどうか（窓の高さ）では変えない。窓の大きさで操作の決まりが変わらないように
- フォーカスがあるか: 欄の hasFocus（打てるドロップダウン・数の上下の欄の中の行は、外側の欄のフォーカスで見る）
- スクロールへ回す: 内側の場所の表示域へ同じホイールを送り、端で動けなければ外側の場所へ送る
  （Qt が欄の無い所のホイールを親へ回すのと同じ順）
- ホイールでフォーカスを取らない: Qt はホイールを欄へ届ける前に、WheelFocus の欄（QComboBox・QAbstractSpinBox の
  既定）へフォーカスを渡す。event filter では止められないので、欄が見えるようになったとき（Show）に、
  スクロールする場所の中なら WheelFocus を StrongFocus（クリックと Tab）にする。外へ出たら（切り離した窓など）戻す
- 開いた一覧（ポップアップ）とグラフ（QGraphicsView）は変えない（中の部品が対象の欄でない）
"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import (
    QAbstractScrollArea,
    QAbstractSlider,
    QAbstractSpinBox,
    QApplication,
    QComboBox,
    QLineEdit,
    QScrollBar,
    QWidget,
)

# ホイールで値が変わる欄（とその中の行）
# スライダー（音量など）もフォーカスが無いのにホイールで値が変わるので含める。スクロールバーは場所を動かす部品なので除く
_FIELDS = (QComboBox, QAbstractSpinBox, QLineEdit, QAbstractSlider)
# ホイールでフォーカスを取る欄（Qt の既定が WheelFocus）
_WHEEL_FOCUS = (QComboBox, QAbstractSpinBox, QAbstractSlider)
# フォーカスの決まりを WheelFocus から変えた印（外へ出たら戻すため。動的プロパティ）
_CHANGED = "_wheel_guard_changed"


def scroll_areas(w: QWidget) -> list[QAbstractScrollArea]:
    """w を表示域の中に持つスクロールする場所。内側から順に。w の窓の外はたどらない。"""
    out: list[QAbstractScrollArea] = []
    child = w
    while not child.isWindow():
        parent = child.parentWidget()
        if parent is None:
            break
        if isinstance(parent, QAbstractScrollArea) and parent.viewport() is child:
            out.append(parent)
        child = parent
    return out


def in_scroll_area(w: QWidget) -> bool:
    return bool(scroll_areas(w))


def fit_focus_policy(w: QWidget) -> None:
    """スクロールする場所の中の欄は、ホイールでフォーカスを取らない。外へ出たら元（WheelFocus）に戻す。"""
    if not isinstance(w, _WHEEL_FOCUS) or isinstance(w, QScrollBar):
        return
    if in_scroll_area(w):
        if w.focusPolicy() == Qt.FocusPolicy.WheelFocus:
            w.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
            w.setProperty(_CHANGED, True)
    elif w.property(_CHANGED):
        w.setProperty(_CHANGED, None)
        if w.focusPolicy() == Qt.FocusPolicy.StrongFocus:
            w.setFocusPolicy(Qt.FocusPolicy.WheelFocus)


def _forward(e: QWheelEvent, areas: list[QAbstractScrollArea]) -> None:
    """ホイールをスクロールする場所へ回す。内側が端で動けなければ外側へ。"""
    for sa in areas:
        vp = sa.viewport()
        fwd = QWheelEvent(vp.mapFromGlobal(e.globalPosition()), e.globalPosition(), e.pixelDelta(), e.angleDelta(),
                          e.buttons(), e.modifiers(), e.phase(), e.inverted(), e.source(), e.pointingDevice())
        QApplication.sendEvent(vp, fwd)
        if fwd.isAccepted():
            return


class WheelGuard(QObject):
    """アプリ全体の event filter。ホイールと、欄が見えるようになったときだけを見る。"""

    def eventFilter(self, obj, e) -> bool:  # noqa: N802 — Qt の名前
        t = e.type()
        if t == QEvent.Type.Wheel:
            return self._wheel(obj, e)
        if t == QEvent.Type.Show and isinstance(obj, _WHEEL_FOCUS) and not isinstance(obj, QScrollBar):
            fit_focus_policy(obj)
        return False

    @staticmethod
    def _wheel(obj, e: QWheelEvent) -> bool:
        if not isinstance(obj, _FIELDS) or isinstance(obj, QScrollBar) or obj.hasFocus():
            return False
        areas = scroll_areas(obj)
        if not areas:
            return False  # スクロールしない場所: 今まで通り欄が受ける
        fit_focus_policy(obj)  # 見えるようになったときに合わせ損ねていても、次のホイールからはフォーカスを取らない
        _forward(e, areas)
        e.accept()
        return True  # 欄には渡さない（値を変えない）


_guard: Optional[WheelGuard] = None


def install(app: QApplication) -> WheelGuard:
    """アプリに1回だけ付ける（QApplication を作った直後）。付けた時点で見えている欄にも決まりを当てる。"""
    global _guard
    if _guard is None:
        _guard = WheelGuard(app)
        app.installEventFilter(_guard)
        for w in app.allWidgets():
            if w.isVisible():
                fit_focus_policy(w)
    return _guard
