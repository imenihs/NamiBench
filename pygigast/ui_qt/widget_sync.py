"""状態を欄へ映すときの決まりと、ドロップダウンの幅。

状態を映す（掃引のたびにも届く）:
- 文字を打てる欄（行・数値の欄・打てるドロップダウン）は、フォーカスがある間は書き直さない。
  書き直すと、選んだ範囲・カーソル・打っている途中の文字が消え、直すたびに消して打ち直すことになる
- ドロップダウンは一覧を開いている間は書き直さない（開いた一覧が組み替わる・閉じる）
- 打てないドロップダウンはフォーカスがあっても書き直す。選んだ範囲もカーソルも無く、
  書かずにおくと機器が受け付けなかった選択が残って見える
- 表示が変わらないときは書かない
- 書かなかった値は、フォーカスが外れた・一覧を閉じたときに、そのときの最新の状態で入れる

ドロップダウンの幅（設計J §4.3: 値が全部見える幅を取る）:
- 欄は一番長い選択肢に合わせる（選択肢が後から変わっても合わせ直す）
- 開いた一覧は一番長い行（選べない見出しの行も）が切れない幅にする
"""
from __future__ import annotations

from typing import Any, Callable

from PySide6.QtCore import QCoreApplication, QEvent, QObject, QTimer
from PySide6.QtWidgets import QAbstractSpinBox, QComboBox, QLineEdit, QWidget

# 開いた一覧の行の左右の余白（行の字の外側。字の幅に足す）
_POPUP_PAD = 28


def busy(w: QWidget) -> bool:
    """利用者がいま触っている欄か（書き直すと触っているものが消える）。"""
    if isinstance(w, QComboBox):
        if w.view().isVisible():
            return True
        return w.isEditable() and w.hasFocus()
    return w.hasFocus()


class _Watch(QObject):
    """欄のフォーカスが外れた・一覧を閉じたときに、書かずにおいた最新の値を入れる。"""

    def __init__(self, w: QWidget) -> None:
        super().__init__(w)
        self.w = w
        self.pending: dict[str, Callable[[], None]] = {}
        w.installEventFilter(self)
        if isinstance(w, QComboBox):
            w.view().installEventFilter(self)

    def eventFilter(self, obj, e) -> bool:
        if e.type() in (QEvent.Type.FocusOut, QEvent.Type.Hide) and self.pending:
            # フォーカスの移り終わり・一覧の閉じ終わりを待ってから入れる
            QTimer.singleShot(0, self.flush)
        return False

    def flush(self) -> None:
        if not self.pending or busy(self.w):
            return
        todo = list(self.pending.values())
        self.pending.clear()
        for fn in todo:
            fn()


def _watch(w: QWidget) -> _Watch:
    watch = getattr(w, "_sync_watch", None)
    if watch is None:
        watch = _Watch(w)
        w._sync_watch = watch
    return watch


def when_idle(w: QWidget, key: str, fn: Callable[[], None]) -> bool:
    """欄に触っていなければ fn を今すぐ、触っていれば触り終えたときに1回だけ（同じ key は最後の1つ）。今すぐ実行したら True。"""
    if busy(w):
        pending = _watch(w).pending
        pending.pop(key, None)  # 後で入れる順は、最後に呼ばれた順（選択肢を入れ替えてから選ぶ）
        pending[key] = fn
        return False
    watch = getattr(w, "_sync_watch", None)
    if watch is not None:
        watch.pending.pop(key, None)
    fn()
    return True


def _quiet(w: QWidget, fn: Callable[[], None]) -> None:
    # 状態を映すだけ。利用者の操作として送り返さない
    was = w.blockSignals(True)
    try:
        fn()
    finally:
        w.blockSignals(was)


def set_index(combo: QComboBox, i: int) -> None:
    def run() -> None:
        if combo.currentIndex() != i:
            _quiet(combo, lambda: combo.setCurrentIndex(i))
    when_idle(combo, "index", run)


def set_data(combo: QComboBox, data: Any, default: int = 0) -> None:
    """data を持つ行を選ぶ。無ければ default の行。"""
    def run() -> None:
        i = combo.findData(data)
        i = default if i < 0 else i
        if combo.currentIndex() != i:
            _quiet(combo, lambda: combo.setCurrentIndex(i))
    when_idle(combo, "index", run)


def set_items(combo: QComboBox, items: list[tuple[str, Any]]) -> bool:
    """選択肢（文字, 値）を入れ替える。同じなら何もしない。入れ替えたら True（一覧を開いている間は後で入れる）。"""
    have = [(combo.itemText(i), combo.itemData(i)) for i in range(combo.count())]
    if have == list(items):
        watch = getattr(combo, "_sync_watch", None)
        if watch is not None:
            watch.pending.pop("items", None)
        return False

    def run() -> None:
        def fill() -> None:
            combo.clear()
            for text, data in items:
                combo.addItem(text, data)
        _quiet(combo, fill)
    return when_idle(combo, "items", run)


def set_value(spin: QAbstractSpinBox, v) -> None:
    def run() -> None:
        if spin.value() != v:
            _quiet(spin, lambda: spin.setValue(v))
    when_idle(spin, "value", run)


def set_text(edit: QLineEdit, text: str) -> None:
    def run() -> None:
        if edit.text() != text:
            _quiet(edit, lambda: edit.setText(text))
    when_idle(edit, "text", run)


def set_checked(button, on: bool) -> None:
    """チェックの欄。選んだ範囲を持たないので、変わるときだけ黙って入れる。"""
    if button.isChecked() != bool(on):
        _quiet(button, lambda: button.setChecked(bool(on)))


# ---------------------------------------------------------------- ドロップダウンの幅


def popup_width_needed(combo: QComboBox) -> int:
    """開いた一覧で一番長い行が切れない幅。"""
    view = combo.view()
    fm = view.fontMetrics()
    widest = max((fm.horizontalAdvance(combo.itemText(i)) for i in range(combo.count())), default=0)
    w = widest + _POPUP_PAD + 2 * view.frameWidth()
    if combo.count() > combo.maxVisibleItems():
        w += view.verticalScrollBar().sizeHint().width()
    return w


class _Fit(QObject):
    def __init__(self, combo: QComboBox) -> None:
        super().__init__(combo)
        self.combo = combo
        m = combo.model()
        for sig in (m.rowsInserted, m.rowsRemoved, m.dataChanged, m.modelReset, m.layoutChanged):
            sig.connect(self.update)
        combo.installEventFilter(self)
        self.update()

    def eventFilter(self, obj, e) -> bool:
        if e.type() == QEvent.Type.FontChange:
            self.update()
        return False

    def update(self, *_a) -> None:
        c = self.combo
        # Qt の QComboBox は、選択肢が変わると大きさの見積もり（sizeHint）を測り直すが、最小の大きさの見積もり
        # （minimumSizeHint）は最初に聞かれたときの値を持ち続ける（測り直すのは見た目の決まりが変わったときだけ）。
        # 空で作って状態が届いてから選択肢を入れるドロップダウンは、選択肢が入る前に並びが最小の大きさを聞くと、
        # 「選択肢なし」の幅（x の字 7 つ分）が残り、並びと崩れの試験が一番長い選択肢と違う幅を見た（PC が重いと
        # 並びが状態より先に回るので、ときどきだけ起きた）。2つの見積もりは同じ求め方なので、違えば見た目の決まりが
        # 変わった知らせを送って、両方を今の選択肢で測り直させる
        if c.sizeAdjustPolicy() == QComboBox.SizeAdjustPolicy.AdjustToContents and c.minimumContentsLength() == 0 \
                and c.minimumSizeHint() != c.sizeHint():
            QCoreApplication.sendEvent(c, QEvent(QEvent.Type.StyleChange))
        c.view().setMinimumWidth(popup_width_needed(c))
        c.updateGeometry()


def combo_width(combo: QComboBox, items) -> int:
    """選択肢 items を全部持ったときのドロップダウンの幅（Qt が中身に合わせて求める幅と同じ求め方: 一番長い文字を
    中身にして、見た目の決まりで外側の大きさにする）。ドロップダウンが覚えている大きさの見積もりは使わない
    （見た目の決まりが当たる前の値が残っていることがあり、狭く測って試験がときどき落ちた）。"""
    from PySide6.QtCore import QSize
    from PySide6.QtWidgets import QStyle, QStyleOptionComboBox

    fm = combo.fontMetrics()
    opt = QStyleOptionComboBox()
    combo.initStyleOption(opt)
    inner = QSize(max((fm.horizontalAdvance(t) for t in items), default=0), fm.height())
    return combo.style().sizeFromContents(QStyle.ContentsType.CT_ComboBox, opt, inner, combo).width()


def fit_combo(combo: QComboBox) -> QComboBox:
    """欄の幅を一番長い選択肢に、開いた一覧の幅を一番長い行に合わせる（選択肢が変わるたびに合わせ直す）。"""
    combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
    if getattr(combo, "_sync_fit", None) is None:
        combo._sync_fit = _Fit(combo)
    return combo


def fit_all(root: QWidget) -> None:
    """root の中のドロップダウンを全部 fit_combo する（作り忘れを残さない）。"""
    for c in root.findChildren(QComboBox):
        fit_combo(c)
