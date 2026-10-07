"""右のタブ「トレース・掃引」（設計J §4.5・F §5・C §5.3）。

並びは用事と頻度の順（J §4.5・U-10。よく変える掃引の設定をスクロールせずに見える一番上に置く）:
- ① 掃引: 点数（自動・手動）・Step・掃引時間の見積もり・イメージ除去（正確・スピード・なしと1画面の見積もり: F §7.4）
- ② トレース 1〜4: 色・表示するか・動作・回数・平均の種類・N 回の後・線（クリアの真下: F §9）・いまの回数・
  一度きりの知らせ（F §3）
- ③ 参照トレース: 開いたファイルのトレース（表示するか・外す）（F §5.4・H §6）
- ④ 機器に固有の欄（RTL-SDR の受信: rtl_panel.py）。その機器に繋いでいるときだけ出す（add_device_section）
- 周波数オフセット（設計O）: 表示する周波数 = 測った周波数 + オフセット
- 大きさと余白は sizes.py の決まり（J §4.11）。名前は欄の左（S-05）、ボタンは文字の幅で左から詰める（S-03）
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from PySide6.QtCore import QEvent, QObject, Qt, QTimer
from PySide6.QtGui import QStandardItemModel
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QGridLayout,
    QGroupBox,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QWidget,
)

from ..core.report_layout import TRACE_LINE_DEFAULT
from ..core.units import format_freq
from . import menus, nf_view, screen_model, screen_s11, sizes, texts, widget_sync
from .fields import COUNT_CHOICES, COUNT_MAX, CountField
from .theme import trace_color

if TYPE_CHECKING:
    from .main_window import MainWindow

COUNTED = ("average", "max_hold", "min_hold")
DEFAULT_AVERAGE_COUNT = 16
TRACE_NOTE_MS = 6000
IC_NOTE_MS = 10000  # イメージ除去（正確・スピード）を選んだときの注意を出しておく長さ
REASON_COUNT = texts.REASON_TEXTS["count_only"]
REASON_AVG_TYPE = texts.REASON_TEXTS["average_type_only"]
REASON_AFTER = texts.REASON_TEXTS["after_only"]
# ベクトル平均は位相（複素数の値）を持つ機器だけ
REASON_VECTOR = texts.REASON_TEXTS["vector_only"]
L = texts.LABELS


def body() -> tuple[QWidget, object]:
    """右のタブの中身（枠の内側の余白・見出しのまとまりの間: S-04）。"""
    inner = QWidget()
    lay = sizes.vbox(inner)
    lay.setSpacing(sizes.SECTION_GAP)
    return inner, lay


def outer(tab: QWidget, inner: QWidget) -> None:
    """タブにスクロールする中身を置く（タブの外周の余白は中身の側が持つ）。"""
    v = sizes.vbox(tab, inset=False)
    v.addWidget(scroll(inner))


def scroll(w: QWidget) -> QScrollArea:
    """タブの中身はタブの中でスクロールする（窓が低くても切れない: J §4.3.3）。"""
    sa = QScrollArea()
    sa.setWidgetResizable(True)
    sa.setFrameShape(QScrollArea.Shape.NoFrame)
    sa.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    sa.setWidget(w)
    # 横は切らずに中身の最小の幅を取る（縦だけスクロール）。分割の境目はこの幅より狭くならない。
    # 中身の並びが変わったら（見た目の決まりが当たった・機器に固有の欄が出た）測り直す
    _FitScroll(sa, w)
    return sa


class _FitScroll(QObject):
    """スクロールの最小の幅を、中身の最小の幅に合わせ続ける。"""

    def __init__(self, sa: QScrollArea, inner: QWidget) -> None:
        super().__init__(sa)
        self.sa, self.inner = sa, inner
        inner.installEventFilter(self)
        self.fit()

    def eventFilter(self, obj, e) -> bool:
        if e.type() == QEvent.Type.LayoutRequest:
            self.fit()
        return False

    def fit(self) -> None:
        w = self.inner.minimumSizeHint().width() + self.sa.verticalScrollBar().sizeHint().width() + 2
        if w != self.sa.minimumWidth():
            self.sa.setMinimumWidth(w)


combo_width = widget_sync.combo_width  # 前の名前（呼ぶ所が残っている）


class SameWidth(QObject):
    """並びの同じ列のドロップダウンを1つの幅にそろえる。幅はその列が出しうる一番長い選択肢の幅で、選択肢が変わっても
    動かさない（行ごとに幅が違うと列がガタガタし、行を移るたびにマウスを横へ探しに行くことになる: 2026-10-03 ユーザー指摘）。

    見た目の決まり・文字が変わったら測り直す。others は同じ列に置くほかの部品（ボタンなど）で、列の幅はその幅も
    下回らない（列の一番広い部品の幅にそろえる: S-02。ボタンは文字の幅のまま: S-03）。
    """

    def __init__(self, combos: list[QComboBox], items, others: tuple[QWidget, ...] = ()) -> None:
        super().__init__(combos[0])
        self.combos = list(combos)
        self.items = list(items)
        self.others = list(others)
        self._pending = False
        for c in self.combos:
            c.setProperty("column", True)  # 幅は値の種類ではなく列で決まる（試験の印）
            c.installEventFilter(self)
        for w in self.others:
            w.installEventFilter(self)
        self.apply()

    def eventFilter(self, obj, e) -> bool:
        if e.type() in (QEvent.Type.Polish, QEvent.Type.StyleChange, QEvent.Type.FontChange) and not self._pending:
            # 見た目の決まりは、この知らせを部品が受けた後に当たる。当たってから測る
            self._pending = True
            QTimer.singleShot(0, self, self.apply)  # 部品が先に消えたら呼ばない
        return False

    def apply(self) -> None:
        self._pending = False
        w = max([combo_width(c, self.items) for c in self.combos]
                + [max(o.sizeHint().width(), o.minimumWidth()) for o in self.others])
        for c in self.combos:
            if c.width() != w or c.minimumWidth() != w or c.maximumWidth() != w:
                c.setFixedWidth(w)


class _ElidedLabel(QLabel):
    """幅に入らない文を「…」で切る1行の文字（幅は並びが決める。文の長さで並びを押し広げない）。"""

    def __init__(self) -> None:
        super().__init__("")
        self._full = ""
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)

    def set_full_text(self, text: str) -> None:
        if text != self._full:
            self._full = text
            self._elide()

    def full_text(self) -> str:
        return self._full

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        self._elide()

    def _elide(self) -> None:
        self.setText(self.fontMetrics().elidedText(self._full, Qt.TextElideMode.ElideRight, max(0, self.width())))


def disable_item(combo: QComboBox, index: int, reason: str) -> None:
    model = combo.model()
    if isinstance(model, QStandardItemModel):
        it = model.item(index)
        it.setEnabled(False)
        it.setToolTip(reason)


def set_enabled(w: QWidget, reason: Optional[str], tip: str = "") -> None:
    w.setEnabled(reason is None)
    w.setToolTip(tip + (f" — {reason}" if reason else "") if tip else (reason or ""))


class _TraceRow:
    def __init__(self, tab: "TraceTab", number: int, grid: QGridLayout, row: int) -> None:
        self.tab = tab
        self.n = number
        win = tab.win
        self.swatch = QLabel(f"■ {number}")
        self.restyle()
        self.visible = QCheckBox(L["visible"])
        self.visible.toggled.connect(lambda on: win.call("set_trace", number=number, visible=bool(on)))
        self.action = QComboBox()
        for code, name in texts.TRACE_ACTIONS.items():
            self.action.addItem(name, code)
        self.action.activated.connect(self._action_chosen)
        # 回数は打てるドロップダウン（一覧から選ぶ・数を打つ。回数なしも一覧から選べる）
        self.count = CountField(lambda v: win.call("set_trace", number=number, count=v), L["no_count"],
                                L["times_suffix"])
        self.count.invalid.connect(lambda: self.flash(L["bad_count"]))
        self.clear = QPushButton(L["clear"])
        self.clear.setToolTip(L["clear_tip"])
        self.clear.clicked.connect(lambda: win.call("clear_trace", number=number))
        self.avg = QComboBox()
        self.avg.addItem(L["default"], None)
        for code, name in texts.AVERAGE_TYPES.items():
            self.avg.addItem(name, code)
        disable_item(self.avg, self.avg.findData("vector"), REASON_VECTOR)
        self.avg.activated.connect(lambda _i: win.call("set_trace", number=number,
                                                       average_type=self.avg.currentData()))
        self.after = QComboBox()
        self.after.activated.connect(self._after_chosen)
        # 線（設計F §9）。描き方だけなので、変えても蓄積は捨てない
        self.line = QComboBox()
        for code, name in texts.TRACE_LINES.items():
            self.line.addItem(name, code)
        self.line.setToolTip(L["line"])
        self.line.activated.connect(lambda i: win.call("set_trace", number=number, line=self.line.itemData(i)))
        # 表示の形式（設計R §3: リターンロス（dB）・|Γ|・SWR。位相系は位相を測れない機器では無効にして理由: R §5.1）
        self.fmt = QComboBox()
        for code in texts.TRACE_FORMATS:
            self.fmt.addItem(texts.TRACE_FORMATS[code], code)
        self.fmt.setToolTip(L["trace_format"])
        self.fmt.activated.connect(self._format_chosen)
        self._fmt_key: tuple = ()
        # 縦軸と単位が違って描かない理由（その行に出し続ける: U-25）
        self.unit_note = QLabel("")
        self.unit_note.setObjectName("warn")
        self.unit_note.setWordWrap(True)
        self.unit_note.hide()
        # いまの様子（「44/64」）は掃引ごとに長さが変わる。列の幅も行の高さも動かさない（文が延びて欄が右へずれる・
        # 折り返して行が高くなると、掃引のたびに部品が動く）: 1行目の部品で決まる列の幅に収めて切り、全部はツールチップ
        self.status = _ElidedLabel()
        self.status.setObjectName("dim")
        self.note = QLabel("")
        self.note.setObjectName("warn")
        self.note.setWordWrap(True)
        self.note.hide()
        self._note_timer = QTimer(tab)
        self._note_timer.setSingleShot(True)
        self._note_timer.timeout.connect(lambda: (self.note.setText(""), self.note.hide()))
        self._state: dict = {}

        # 2行とも欄は決まった列に置く（トレースを移るときマウスはまっすぐ縦に動く）。列の幅は TraceTab がそろえる
        grid.addWidget(self.swatch, row, 0)
        grid.addWidget(self.visible, row, 1)
        grid.addWidget(self.action, row, 2)
        grid.addWidget(self.count, row, 3)
        grid.addWidget(self.clear, row, 4)
        grid.addWidget(self.status, row + 1, 0, 1, 2)
        grid.addWidget(self.avg, row + 1, 2)
        grid.addWidget(self.after, row + 1, 3)
        grid.addWidget(self.line, row + 1, 4)  # クリアの真下
        # 3行目: 表示の形式（動作・平均の種類の真下。同じ列なので同じ幅: S-02）
        grid.addWidget(self.fmt, row + 2, 2)
        grid.addWidget(self.unit_note, row + 3, 0, 1, 5)
        grid.addWidget(self.note, row + 4, 0, 1, 5)

    def _action_chosen(self, _i: int) -> None:
        a = self.action.currentData()
        kw = {"action": a}
        if a == "average" and not self._state.get("count"):
            # 平均は回数を持つ（設計F §2）。回数が無ければ既定の回数で始める
            kw["count"] = DEFAULT_AVERAGE_COUNT
        self.tab.win.call("set_trace", number=self.n, **kw)

    def _format_chosen(self, i: int) -> None:
        code = self.fmt.itemData(i)
        if code is not None and code != self._state.get("format"):
            self.tab.win.call("set_trace", number=self.n, format=code)

    def apply_format(self, v: dict, drawn_reason: Optional[str]) -> None:
        """表示の形式の欄（screen_s11.trace_format_view）と、縦軸と単位が違って描かない理由。"""
        key = tuple((it["code"], it["text"], it["enabled"], it["tip"]) for it in v["items"])

        def show() -> None:
            if key != self._fmt_key:
                self._fmt_key = key
                widget_sync.set_items(self.fmt, [(it["text"], it["code"]) for it in v["items"]])
                model = self.fmt.model()
                for k, it in enumerate(v["items"]):
                    item = model.item(k) if isinstance(model, QStandardItemModel) else None
                    if item is not None:
                        # 使えない形式は一覧に残して無効にし、理由をツールチップに（U-08）
                        item.setEnabled(bool(it["enabled"]))
                        item.setToolTip(it["tip"])
            widget_sync.set_data(self.fmt, v["value"])
        widget_sync.when_idle(self.fmt, "fmt", show)
        set_enabled(self.fmt, v["reason"], L["trace_format"])
        self.unit_note.setText(drawn_reason or "")
        self.unit_note.setVisible(bool(drawn_reason))

    def _after_chosen(self, _i: int) -> None:
        code = self.after.currentData()
        if self._state.get("action") == "average":
            self.tab.win.call("set_trace", number=self.n, average_after=code)
        else:
            self.tab.win.call("set_trace", number=self.n, hold_after=code)

    def restyle(self) -> None:
        """トレースの色の印（配色を変えたとき: 設計J §4.4）。"""
        self.swatch.setStyleSheet(f"color: {trace_color(self.n, self.tab.win.pal)}; font-weight: bold;")

    def apply(self, s: dict) -> None:
        self._state = s
        a = s["action"]
        widget_sync.set_checked(self.visible, bool(s["visible"]))
        widget_sync.set_data(self.action, a)
        # 打っている途中は欄が自分で書かずに待つ（フォーカスが外れたら入れる。確定した値の戻りはその場で映す）。
        # 平均は回数が要るので「回数なし」を一覧に出さない（設計F §2）
        self.count.set_state(s.get("count"), a != "average")
        set_enabled(self.count, None if a in COUNTED else REASON_COUNT, L["count_n"])
        eff = s.get("average_type_effective")
        default_text = texts.w("default_with", type=texts.AVERAGE_TYPES.get(eff, eff)) if eff else L["default"]

        def show_avg() -> None:
            if self.avg.itemText(0) != default_text:
                self.avg.setItemText(0, default_text)
            widget_sync.set_data(self.avg, s.get("average_type"))
        widget_sync.when_idle(self.avg, "avg", show_avg)
        # NF では HOT と COLD をパワー平均するので種類を選べない（設計M §1。選んだ種類はほかのモードで使う）
        set_enabled(self.avg, (None if a == "average" else REASON_AVG_TYPE)
                    or nf_view.average_type_reason(self.tab.win.state), L["average_type"])
        choices = texts.AVERAGE_AFTER if a == "average" else texts.HOLD_AFTER
        cur = s.get("average_after") if a == "average" else s.get("hold_after")
        widget_sync.set_items(self.after, [(name, code) for code, name in choices.items()])
        widget_sync.set_data(self.after, cur)
        widget_sync.set_data(self.line, s.get("line") or TRACE_LINE_DEFAULT)
        counted = a in COUNTED and s.get("count")
        set_enabled(self.after, None if counted else REASON_AFTER, L["after_n"])
        v = screen_model.trace_status(s)
        self.status.set_full_text(v["text"])
        self.status.setToolTip(v["tip"])

    def flash(self, text: str) -> None:
        self.note.setText(text)
        self.note.show()
        self._note_timer.start(TRACE_NOTE_MS)


class TraceTab(QWidget):
    def __init__(self, win: "MainWindow") -> None:
        super().__init__()
        self.win = win
        inner, lay = body()

        g_sweep = QGroupBox(L["sweep"])
        form = sizes.form(g_sweep)
        self.points_auto = QCheckBox(texts.TEMPLATES["points_auto"])
        self.points_auto.setChecked(True)
        self.points_spin = QSpinBox()
        self.points_spin.setRange(2, 12000)
        self.points_spin.setEnabled(False)
        self.points_spin.setKeyboardTracking(False)
        sizes.set_width(self.points_spin, "int")
        self.points_auto.toggled.connect(self._points_auto_toggled)
        self.points_spin.valueChanged.connect(
            lambda v: None if self.points_auto.isChecked() else win.call("set_sweep", points=v))
        row = sizes.hbox()
        row.addWidget(self.points_spin)
        row.addWidget(self.points_auto)
        row.addStretch(1)
        form.addRow(L["points"], row)
        self.step_label = QLabel("")
        form.addRow("Step", self.step_label)
        self.est_label = QLabel("")
        form.addRow(L["estimate"], self.est_label)
        # イメージ除去の3つ。選択肢の文字に、いまの条件での1画面の見積もりを出す（設計F §7.4）。幅は出しうる一番長い
        # 選択肢（秒の桁が変わっても欄を動かさない: S-02）
        self.image_reject = QComboBox()
        for code in texts.IMAGE_REJECT_CHOICES:
            self.image_reject.addItem(texts.image_reject_item(code), code)
        self.image_reject.activated.connect(self._image_reject_chosen)
        self._reject_width = SameWidth([self.image_reject], screen_model.image_reject_width_samples())
        self._reject_value: Optional[str] = None
        form.addRow(L["image_reject"], self.image_reject)
        # 正確・スピードを選んだときだけ一度出す注意（ずっとは出さない。いつでも読めるようにツールチップにも置く）
        self.ic_note = QLabel(L["image_reject_short"])
        self.ic_note.setObjectName("warn")
        self.ic_note.setWordWrap(True)
        self.ic_note.hide()
        self._ic_note_timer = QTimer(self)
        self._ic_note_timer.setSingleShot(True)
        self._ic_note_timer.timeout.connect(self.ic_note.hide)
        form.addRow(self.ic_note)
        # この機器で使えない欄の理由（FFT の機器: 点数は RBW と Span で決まる・ゼロスパンを持たない: 設計Q §6.9）
        self.sweep_note = QLabel("")
        self.sweep_note.setObjectName("dim")
        self.sweep_note.setWordWrap(True)
        self.sweep_note.hide()
        form.addRow(self.sweep_note)
        # 周波数オフセット（変換器の観測用: 設計O）。表示する周波数 = 測った周波数 + オフセット。0 で使わない
        from .fields import FreqField

        self.freq_offset = FreqField(texts.VIEW_TEXTS["freq_offset"],
                                     lambda hz: win.call("set_view", field=self.freq_offset, freq_offset_hz=int(hz)),
                                     big=False)
        self.freq_offset.setToolTip(texts.VIEW_TEXTS["freq_offset_tip"])
        self.freq_offset.edit.setPlaceholderText(texts.VIEW_TEXTS["freq_offset_placeholder"])
        form.addRow(self.freq_offset.name, self.freq_offset)
        lay.addWidget(g_sweep)

        g_traces = QGroupBox(L["traces"])
        grid = QGridLayout(g_traces)
        grid.setContentsMargins(sizes.INSET, sizes.INSET, sizes.INSET, sizes.INSET)
        grid.setHorizontalSpacing(sizes.GAP)
        grid.setVerticalSpacing(sizes.GAP)
        self.rows = {n: _TraceRow(self, n, grid, (n - 1) * 5) for n in (1, 2, 3, 4)}
        grid.setColumnStretch(5, 1)  # 列は左から詰める（部品を横に伸ばさない: S-02・S-03）
        # 同じ列の欄は1つの幅（その列が出しうる一番長い選択肢）: 動作と平均の種類／回数と N 回の後
        rows = list(self.rows.values())
        kinds = [texts.w("default_with", type=t) for t in texts.AVERAGE_TYPES.values()]
        # 表示の形式（設計R §3）も同じ列（3行目）。出しうる一番長い形式の名前（モードで名前が変わっても幅を動かさない）
        self._col_action = SameWidth([r.action for r in rows] + [r.avg for r in rows] + [r.fmt for r in rows],
                                     [*texts.TRACE_ACTIONS.values(), L["default"], *texts.AVERAGE_TYPES.values(), *kinds,
                                      *screen_s11.format_width_samples()])
        counts = [L["no_count"]] + [f"{v}{L['times_suffix']}" for v in (*COUNT_CHOICES, COUNT_MAX)]
        self._col_count = SameWidth([r.count for r in rows] + [r.after for r in rows],
                                    [*counts, *texts.AVERAGE_AFTER.values(), *texts.HOLD_AFTER.values()])
        # 線（クリアの真下の列）: 一番長い線の名前とクリアのボタンの広い方（Web の --col-line と同じ）
        self._col_line = SameWidth([r.line for r in rows], texts.TRACE_LINES.values(),
                                   others=tuple(r.clear for r in rows))
        lay.addWidget(g_traces)

        g_files = QGroupBox(L["files"])
        self.files_lay = sizes.vbox(g_files)
        self.files_empty = QLabel(L["no_files_open"])
        self.files_empty.setObjectName("dim")
        self.files_lay.addWidget(self.files_empty)
        self._file_rows: dict[str, QWidget] = {}
        self._file_checks: dict[str, QCheckBox] = {}
        self._files_key: tuple = ()
        open_btn = QPushButton(texts.MENU_ITEMS["open"])
        open_btn.setToolTip(menus.tooltip_for(win.actions["open"], texts.MENU_ITEMS["open"]))  # Web と同じ（キーも出す: U-12）
        open_btn.clicked.connect(win.actions["open"].trigger)
        self.open_btn = open_btn
        self.files_lay.addLayout(sizes.buttons_row(open_btn))
        lay.addWidget(g_files)
        lay.addStretch(1)
        self._body = lay
        self.g_sweep, self.g_traces, self.g_files = g_sweep, g_traces, g_files
        outer(self, inner)

    def add_device_section(self, w: QWidget) -> None:
        """機器に固有の欄（④）を一番下（余白の手前）に足す。出し入れは欄の側が決める（J §4.5）。"""
        self._body.insertWidget(self._body.count() - 1, w)

    def _image_reject_chosen(self, i: int) -> None:
        # 利用者が選んだときだけ届く（activated。状態を映すときは知らせを止めている: widget_sync）
        code = self.image_reject.itemData(i)
        if code == self._reject_value:
            return
        self.win.call("set_sweep", image_reject=code)
        if code in ("accurate", "fast"):
            self.ic_note.show()
            self._ic_note_timer.start(IC_NOTE_MS)
        else:
            self._ic_note_timer.stop()
            self.ic_note.hide()

    def _points_auto_toggled(self, auto: bool) -> None:
        self.points_spin.setEnabled(not auto)
        if auto:
            self.win.call("set_sweep", points_auto=True)
        else:
            self.win.call("set_sweep", points=self.points_spin.value())

    def apply_state(self, st: dict) -> None:
        sw = st["sweep"]
        s11 = screen_model.s11_ui(st)
        for s in st.get("traces") or []:
            r = self.rows.get(int(s["number"]))
            if r is not None:
                r.apply(s)
                key = str(s["number"])
                r.apply_format(s11["trace_format"][key], s11["trace_drawn"].get(key))
        why = screen_model.sweep_reasons(st)
        self.points_auto.blockSignals(True)
        self.points_auto.setChecked(sw.get("points_setting") in (None, 0))
        set_enabled(self.points_auto, why["points"])
        if why["points"]:
            set_enabled(self.points_spin, why["points"])
        else:
            self.points_spin.setToolTip("")
            self.points_spin.setEnabled(not self.points_auto.isChecked())
        self.points_auto.blockSignals(False)
        widget_sync.set_value(self.points_spin, sw["points"])
        self.step_label.setText(format_freq(sw["step_hz"]) + (L["zero_span_mark"] if sw["step_hz"] == 0 else ""))
        # 小数1桁（イメージ除去の選択肢の秒と同じ形: 設計F §7.4。0.01 秒の桁は見積もりとして意味が無い）
        self.est_label.setText(texts.w("estimate_value", s=texts.seconds_text(sw["estimated_sweep_s"])))
        rv = screen_model.image_reject_view(st)
        self._reject_value = rv["value"]
        widget_sync.set_items(self.image_reject, [(it["text"], it["code"]) for it in rv["items"]])
        widget_sync.set_data(self.image_reject, rv["value"])
        set_enabled(self.image_reject, rv["reason"], L["image_reject_short"])
        notes = [w for w in (why["points"], why["zero_span"]) if w]
        self.sweep_note.setText("\n".join(notes))
        self.sweep_note.setVisible(bool(notes))
        self.freq_offset.set_hz(texts.freq_offset_of(st))  # 打っている間は欄が書き直さない（fields._Edit）
        self._apply_files(st.get("files") or [])

    def _apply_files(self, files: list[dict]) -> None:
        key = tuple((f["file_id"], tuple(t["id"] for t in f.get("traces") or [])) for f in files)
        if key == self._files_key:
            return
        self._files_key = key
        for w in self._file_rows.values():
            w.deleteLater()
        self._file_rows = {}
        self._file_checks = {}
        layer = self.win.traces
        for i, f in enumerate(files):
            w = QWidget()
            v = sizes.vbox(w, inset=False)
            head = sizes.hbox()
            name = QLabel(f["name"])
            name.setToolTip(f"{f['path']}\n{f.get('created_at') or ''}  {f.get('device_model') or ''} "
                            f"{f.get('device_serial') or ''}\n{f.get('note') or ''}")
            head.addWidget(name, 1)
            close = QPushButton(L["close_file"])
            close.setToolTip(L["close_file_tip"])
            close.clicked.connect(lambda _=False, fid=f["file_id"]: self.win.call("close_file", file_id=fid))
            head.addWidget(close)
            v.addLayout(head)
            for t in f.get("traces") or []:
                cb = QCheckBox(texts.w("file_trace_check", number=t["number"], unit=t["unit"], points=t["points"]))
                cb.setChecked(bool((t.get("info") or {}).get("visible", True)))
                cb.toggled.connect(lambda on, tid=t["id"]: layer.set_file_shown(tid, on))
                v.addWidget(cb)
                self._file_checks[t["id"]] = cb
            self.files_lay.insertWidget(self.files_lay.count() - 1, w)
            self._file_rows[f["file_id"]] = w
        self.files_empty.setVisible(not files)
        self.update_file_checks()

    def restyle(self) -> None:
        for r in self.rows.values():
            r.restyle()

    def update_file_checks(self) -> None:
        """単位がいまの縦軸と違う参照トレースは重ねない。消さずに無効にして理由を出す（U-08・E §6.4）。"""
        layer = self.win.traces
        for tid, cb in self._file_checks.items():
            e = layer.files.get(tid)
            ok = e is None or layer.file_unit_ok(tid)
            unit = (e or {}).get("payload", {}) or {}
            set_enabled(cb, None if ok else
                        texts.w("file_unit_differs", axis=layer.unit, unit=unit.get("unit", "")))
