"""右のタブ「トレース・掃引」（設計J §4.5・F §5・C §5.3）。

並びは用事と頻度の順（J §4.5・U-10。よく変える掃引の設定をスクロールせずに見える一番上に置く）:
- ① 掃引: 点数（自動・手動）・Step・掃引時間の見積もり・イメージキャンセル
- ② トレース 1〜4: 色・表示するか・動作・回数・平均の種類・N 回の後・いまの回数・一度きりの知らせ（F §3）
- ③ 参照トレース: 開いたファイルのトレース（表示するか・外す）（F §5.4・H §6）
- ④ 機器に固有の欄（RTL-SDR の受信: rtl_panel.py）。その機器に繋いでいるときだけ出す（add_device_section）
- 周波数オフセット（設計O）は次の回。消さずに無効にして理由を出す（U-08）
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QStandardItemModel
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ..core.units import format_freq
from . import screen_model, texts
from .theme import trace_color

if TYPE_CHECKING:
    from .main_window import MainWindow

COUNTED = ("average", "max_hold", "min_hold")
DEFAULT_AVERAGE_COUNT = 16
TRACE_NOTE_MS = 6000
REASON_COUNT = texts.REASON_TEXTS["count_only"]
REASON_AVG_TYPE = texts.REASON_TEXTS["average_type_only"]
REASON_AFTER = texts.REASON_TEXTS["after_only"]
# ベクトル平均は位相（複素数の値）を持つ機器だけ
REASON_VECTOR = texts.REASON_TEXTS["vector_only"]
# 周波数オフセットは設計O。まだ作っていない
REASON_FREQ_OFFSET = texts.NOT_YET["freq_offset"]
L = texts.LABELS


def scroll(w: QWidget) -> QScrollArea:
    """タブの中身はタブの中でスクロールする（窓が低くても切れない: J §4.3.3）。"""
    sa = QScrollArea()
    sa.setWidgetResizable(True)
    sa.setFrameShape(QScrollArea.Shape.NoFrame)
    sa.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    sa.setWidget(w)
    # 横は切らずに中身の最小の幅を取る（縦だけスクロール）。分割の境目はこの幅より狭くならない
    sa.setMinimumWidth(w.minimumSizeHint().width() + sa.verticalScrollBar().sizeHint().width() + 2)
    return sa


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
        self.swatch.setStyleSheet(f"color: {trace_color(number)}; font-weight: bold;")
        self.visible = QCheckBox(L["visible"])
        self.visible.toggled.connect(lambda on: win.call("set_trace", number=number, visible=bool(on)))
        self.action = QComboBox()
        for code, name in texts.TRACE_ACTIONS.items():
            self.action.addItem(name, code)
        self.action.activated.connect(self._action_chosen)
        self.count = QSpinBox()
        self.count.setRange(0, 10000)
        self.count.setSpecialValueText(L["no_count"])
        self.count.setKeyboardTracking(False)
        self.count.setSuffix(L["times_suffix"])
        self.count.valueChanged.connect(self._count_changed)
        self.clear = QPushButton(L["clear"])
        self.clear.setObjectName("small")
        self.clear.setToolTip(L["clear_tip"])
        self.clear.clicked.connect(lambda: win.call("clear_trace", number=number))
        self.avg = QComboBox()
        self.avg.addItem(L["default"], None)
        for code, name in texts.AVERAGE_TYPES.items():
            self.avg.addItem(name, code)
        # 選択肢の文字が切れないよう、欄の幅を中身に合わせる（C-01）
        self.avg.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        disable_item(self.avg, self.avg.findData("vector"), REASON_VECTOR)
        self.avg.activated.connect(lambda _i: win.call("set_trace", number=number,
                                                       average_type=self.avg.currentData()))
        self.after = QComboBox()
        self.after.activated.connect(self._after_chosen)
        self.status = QLabel("")
        self.status.setObjectName("dim")
        self.note = QLabel("")
        self.note.setObjectName("warn")
        self.note.setWordWrap(True)
        self.note.hide()
        self._note_timer = QTimer(tab)
        self._note_timer.setSingleShot(True)
        self._note_timer.timeout.connect(lambda: (self.note.setText(""), self.note.hide()))
        self._state: dict = {}

        grid.addWidget(self.swatch, row, 0)
        grid.addWidget(self.visible, row, 1)
        grid.addWidget(self.action, row, 2)
        grid.addWidget(self.count, row, 3)
        grid.addWidget(self.clear, row, 4)
        grid.addWidget(self.status, row + 1, 0, 1, 2)
        grid.addWidget(self.avg, row + 1, 2)
        grid.addWidget(self.after, row + 1, 3, 1, 2)
        grid.addWidget(self.note, row + 2, 0, 1, 5)

    def _action_chosen(self, _i: int) -> None:
        a = self.action.currentData()
        kw = {"action": a}
        if a == "average" and not self._state.get("count"):
            # 平均は回数を持つ（設計F §2）。回数が無ければ既定の回数で始める
            kw["count"] = DEFAULT_AVERAGE_COUNT
        self.tab.win.call("set_trace", number=self.n, **kw)

    def _count_changed(self, v: int) -> None:
        if self._state.get("action") == "average" and v == 0:
            self.count.blockSignals(True)
            self.count.setValue(self._state.get("count") or DEFAULT_AVERAGE_COUNT)
            self.count.blockSignals(False)
            return
        self.tab.win.call("set_trace", number=self.n, count=v or None)

    def _after_chosen(self, _i: int) -> None:
        code = self.after.currentData()
        if self._state.get("action") == "average":
            self.tab.win.call("set_trace", number=self.n, average_after=code)
        else:
            self.tab.win.call("set_trace", number=self.n, hold_after=code)

    def apply(self, s: dict) -> None:
        self._state = s
        a = s["action"]
        for w, v in ((self.visible, bool(s["visible"])),):
            w.blockSignals(True)
            w.setChecked(v)
            w.blockSignals(False)
        self.action.setCurrentIndex(max(0, self.action.findData(a)))
        self.count.blockSignals(True)
        self.count.setMinimum(1 if a == "average" else 0)
        self.count.setValue(s.get("count") or 0)
        self.count.blockSignals(False)
        set_enabled(self.count, None if a in COUNTED else REASON_COUNT, L["count_n"])
        self.avg.setCurrentIndex(max(0, self.avg.findData(s.get("average_type"))))
        eff = s.get("average_type_effective")
        self.avg.setItemText(0, texts.w("default_with", type=texts.AVERAGE_TYPES.get(eff, eff)) if eff else L["default"])
        set_enabled(self.avg, None if a == "average" else REASON_AVG_TYPE, L["average_type"])
        choices = texts.AVERAGE_AFTER if a == "average" else texts.HOLD_AFTER
        cur = s.get("average_after") if a == "average" else s.get("hold_after")
        if [self.after.itemData(i) for i in range(self.after.count())] != list(choices):
            self.after.clear()
            for code, name in choices.items():
                self.after.addItem(name, code)
        self.after.setCurrentIndex(max(0, self.after.findData(cur)))
        counted = a in COUNTED and s.get("count")
        set_enabled(self.after, None if counted else REASON_AFTER, L["after_n"])
        st = texts.TRACE_ACTIONS.get(a, a)
        if a in COUNTED:
            tgt = s.get("count_target")
            st += f" {s.get('current_count', 0)}" + (f"/{tgt}" if tgt else "")
        if not s.get("has_values"):
            st += L["no_value_paren"]
        if s.get("axis_differs"):
            st += texts.TEMPLATES["axis_differs"]
        self.status.setText(st)

    def flash(self, text: str) -> None:
        self.note.setText(text)
        self.note.show()
        self._note_timer.start(TRACE_NOTE_MS)


class TraceTab(QWidget):
    def __init__(self, win: "MainWindow") -> None:
        super().__init__()
        self.win = win
        inner = QWidget()
        lay = QVBoxLayout(inner)
        lay.setContentsMargins(4, 4, 4, 4)

        g_sweep = QGroupBox(L["sweep"])
        form = QFormLayout(g_sweep)
        self.points_auto = QCheckBox(texts.TEMPLATES["points_auto"])
        self.points_auto.setChecked(True)
        self.points_spin = QSpinBox()
        self.points_spin.setRange(2, 12000)
        self.points_spin.setEnabled(False)
        self.points_spin.setKeyboardTracking(False)
        self.points_auto.toggled.connect(self._points_auto_toggled)
        self.points_spin.valueChanged.connect(
            lambda v: None if self.points_auto.isChecked() else win.call("set_sweep", points=v))
        row = QHBoxLayout()
        row.addWidget(self.points_auto)
        row.addWidget(self.points_spin, 1)
        form.addRow(L["points"], row)
        self.step_label = QLabel("")
        form.addRow("Step", self.step_label)
        self.est_label = QLabel("")
        self.est_label.setWordWrap(True)
        form.addRow(L["estimate"], self.est_label)
        self.image_cancel = QCheckBox(L["image_cancel"])
        self.image_cancel.toggled.connect(lambda on: win.call("set_sweep", image_cancel=on))
        form.addRow(self.image_cancel)
        # この機器で使えない欄の理由（FFT の機器: 点数は RBW と Span で決まる・ゼロスパンを持たない: 設計Q §6.9）
        self.sweep_note = QLabel("")
        self.sweep_note.setObjectName("dim")
        self.sweep_note.setWordWrap(True)
        self.sweep_note.hide()
        form.addRow(self.sweep_note)
        self.freq_offset = QCheckBox(L["freq_offset"])
        set_enabled(self.freq_offset, REASON_FREQ_OFFSET)
        form.addRow(self.freq_offset)
        lay.addWidget(g_sweep)

        g_traces = QGroupBox(L["traces"])
        grid = QGridLayout(g_traces)
        grid.setHorizontalSpacing(4)
        grid.setVerticalSpacing(2)
        self.rows = {n: _TraceRow(self, n, grid, (n - 1) * 3) for n in (1, 2, 3, 4)}
        lay.addWidget(g_traces)

        g_files = QGroupBox(L["files"])
        self.files_lay = QVBoxLayout(g_files)
        self.files_lay.setSpacing(2)
        self.files_empty = QLabel(L["no_files_open"])
        self.files_empty.setObjectName("dim")
        self.files_lay.addWidget(self.files_empty)
        self._file_rows: dict[str, QWidget] = {}
        self._file_checks: dict[str, QCheckBox] = {}
        self._files_key: tuple = ()
        open_btn = QPushButton(texts.MENU_ITEMS["open"])
        open_btn.setObjectName("small")
        open_btn.clicked.connect(win.actions["open"].trigger)
        self.files_lay.addWidget(open_btn, 0, Qt.AlignmentFlag.AlignLeft)
        lay.addWidget(g_files)
        lay.addStretch(1)
        self._body = lay
        self.g_sweep, self.g_traces, self.g_files = g_sweep, g_traces, g_files

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll(inner))

    def add_device_section(self, w: QWidget) -> None:
        """機器に固有の欄（④）を一番下（余白の手前）に足す。出し入れは欄の側が決める（J §4.5）。"""
        self._body.insertWidget(self._body.count() - 1, w)

    def _points_auto_toggled(self, auto: bool) -> None:
        self.points_spin.setEnabled(not auto)
        if auto:
            self.win.call("set_sweep", points_auto=True)
        else:
            self.win.call("set_sweep", points=self.points_spin.value())

    def apply_state(self, st: dict) -> None:
        sw = st["sweep"]
        for s in st.get("traces") or []:
            r = self.rows.get(int(s["number"]))
            if r is not None:
                r.apply(s)
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
        self.points_spin.blockSignals(True)
        self.points_spin.setValue(sw["points"])
        self.points_spin.blockSignals(False)
        self.step_label.setText(format_freq(sw["step_hz"]) + (L["zero_span_mark"] if sw["step_hz"] == 0 else ""))
        self.est_label.setText(texts.w("estimate_value", s=f"{sw['estimated_sweep_s']:.2f}", n=sw["preroll_points"]))
        self.image_cancel.blockSignals(True)
        self.image_cancel.setChecked(sw["image_cancel"])
        self.image_cancel.blockSignals(False)
        set_enabled(self.image_cancel, why["image_cancel"])
        notes = [w for w in (why["points"], why["zero_span"]) if w]
        self.sweep_note.setText("\n".join(notes))
        self.sweep_note.setVisible(bool(notes))
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
            v = QVBoxLayout(w)
            v.setContentsMargins(0, 0, 0, 0)
            v.setSpacing(0)
            head = QHBoxLayout()
            name = QLabel(f["name"])
            name.setToolTip(f"{f['path']}\n{f.get('created_at') or ''}  {f.get('device_model') or ''} "
                            f"{f.get('device_serial') or ''}\n{f.get('note') or ''}")
            head.addWidget(name, 1)
            close = QPushButton(L["close_file"])
            close.setObjectName("small")
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

    def update_file_checks(self) -> None:
        """単位がいまの縦軸と違う参照トレースは重ねない。消さずに無効にして理由を出す（U-08・E §6.4）。"""
        layer = self.win.traces
        for tid, cb in self._file_checks.items():
            e = layer.files.get(tid)
            ok = e is None or layer.file_unit_ok(tid)
            unit = (e or {}).get("payload", {}) or {}
            set_enabled(cb, None if ok else
                        texts.w("file_unit_differs", axis=layer.unit, unit=unit.get("unit", "")))
