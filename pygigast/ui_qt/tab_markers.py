"""右のタブ「マーカー」（設計J §4.5・G §3.5）。

- 選んでいるマーカー: 周波数の数値入力・載るトレース・デルタの基準・ピーク追従（P だけ）・条件へ（G §3.4）
- オフセット: 周波数／レベル、+・−・±（親について動くカーソル）
- マーカーの機能: N dB 帯域幅（N を2つでシェープファクタ）・しきい値との交点
- XY カーソル: X1・X2（周波数）・Y1・Y2（レベル）。トレースに載らない
- ピークの一覧と「P に登録」・「上位 N 個に P を置く」
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..core.units import ParseError, format_freq, parse_db, parse_freq_hz
from . import texts, widget_sync
from .fields import DbField, FreqField
from .tab_traces import scroll, set_enabled
from .theme import number_font

if TYPE_CHECKING:
    from .main_window import MainWindow

REASON_NO_MARKER = texts.REASON_TEXTS["select_marker"]
REASON_TRACK_M = texts.REASON_TEXTS["track_p_only"]
L = texts.LABELS
SIGNS = (("+", "+Δ"), ("-", "−Δ"), ("±", "±Δ"))


class MarkerTab(QWidget):
    def __init__(self, win: "MainWindow") -> None:
        super().__init__()
        self.win = win
        self._marker: Optional[dict] = None
        inner = QWidget()
        lay = QVBoxLayout(inner)
        self.body = lay  # 他の欄（聞く: listen.py）を差し込む
        lay.setContentsMargins(4, 4, 4, 4)

        # ---- 選んでいるマーカー
        g = QGroupBox(L["selected_marker"])
        form = QFormLayout(g)
        self.sel_label = QLabel("")
        self.sel_label.setWordWrap(True)
        form.addRow(self.sel_label)
        self.freq = FreqField(L["freq"], self._set_freq, big=False)
        form.addRow(self.freq)
        self.trace = QComboBox()
        for n in (1, 2, 3, 4):
            self.trace.addItem(texts.w("trace_n", n=n), n)
        self.trace.activated.connect(lambda _i: self._marker_set(trace=self.trace.currentData()))
        form.addRow(L["trace"], self.trace)
        self.ref = QComboBox()
        self.ref.activated.connect(lambda _i: self._marker_set(ref=self.ref.currentData()))
        form.addRow(L["delta_ref"], self.ref)
        self.track = QCheckBox(L["peak_track_long"])
        self.track.toggled.connect(lambda on: self._marker_set(peak_track=bool(on)))
        form.addRow(self.track)
        btns = QGridLayout()
        self.to_center = QPushButton(L["to_center"])
        self.to_ref = QPushButton(L["to_ref_level"])
        self.to_span = QPushButton(texts.MENU_ITEMS["marker_span"])
        self.remove = QPushButton(L["erase"])
        for b in (self.to_center, self.to_ref, self.to_span, self.remove):
            b.setObjectName("small")
        self.to_center.clicked.connect(lambda: self._to("center"))
        self.to_ref.clicked.connect(lambda: self._to("ref"))
        self.to_span.clicked.connect(lambda: self._to("span"))
        self.remove.clicked.connect(lambda: self._marker and win.call("marker_remove", id=self._marker["id"]))
        btns.addWidget(self.to_center, 0, 0)
        btns.addWidget(self.to_ref, 0, 1)
        btns.addWidget(self.to_span, 1, 0)
        btns.addWidget(self.remove, 1, 1)
        form.addRow(btns)
        lay.addWidget(g)

        # ---- オフセット
        g = QGroupBox(L["offsets_long"])
        row = QHBoxLayout(g)
        self.off_type = QComboBox()
        self.off_type.addItem(L["freq"], "freq")
        self.off_type.addItem(L["level"], "level")
        self.off_type.currentIndexChanged.connect(self._off_type_changed)
        self.off_sign = QComboBox()
        for code, name in SIGNS:
            self.off_sign.addItem(name, code)
        self.off_value = QLineEdit("10M")
        self.off_value.setToolTip(L["offset_value_tip"])
        self.off_value.returnPressed.connect(self._add_offset)
        self.off_add = QPushButton(L["add"])
        self.off_add.setObjectName("small")
        self.off_add.clicked.connect(self._add_offset)
        for w in (self.off_type, self.off_sign):
            row.addWidget(w)
        row.addWidget(self.off_value, 1)
        row.addWidget(self.off_add)
        self.g_offset = g
        lay.addWidget(g)

        # ---- マーカーの機能
        g = QGroupBox(L["functions"])
        grid = QGridLayout(g)
        self.bw1 = QCheckBox(L["n_db_bw"])
        self.bw1_n = self._db_spin(3.0, 0.1, 200.0)
        self.bw2 = QCheckBox(L["second_n"])
        self.bw2_n = self._db_spin(60.0, 0.1, 200.0)
        self.th = QCheckBox(L["threshold_cross"])
        self.th_level = self._db_spin(-20.0, -300.0, 300.0)
        self.th_side = QComboBox()
        for code, name in texts.THRESHOLD_SIDES.items():
            self.th_side.addItem(name, code)
        for w in (self.bw1, self.bw2):
            w.toggled.connect(self._send_bandwidth)
        for w in (self.bw1_n, self.bw2_n):
            w.editingFinished.connect(self._send_bandwidth)
        self.th.toggled.connect(self._send_threshold)
        self.th_level.editingFinished.connect(self._send_threshold)
        self.th_side.activated.connect(lambda _i: self._send_threshold())
        grid.addWidget(self.bw1, 0, 0)
        grid.addWidget(self.bw1_n, 0, 1)
        grid.addWidget(self.bw2, 1, 0)
        grid.addWidget(self.bw2_n, 1, 1)
        grid.addWidget(self.th, 2, 0)
        grid.addWidget(self.th_level, 2, 1)
        grid.addWidget(self.th_side, 2, 2)
        self.g_func = g
        lay.addWidget(g)

        # ---- XY カーソル
        g = QGroupBox(L["xy_cursor_long"])
        grid = QGridLayout(g)
        self.xy_checks: dict[str, QCheckBox] = {}
        self.xy_fields: dict[str, object] = {}
        for i, (key, name) in enumerate((("x1_hz", "X1"), ("x2_hz", "X2"), ("y1", "Y1"), ("y2", "Y2"))):
            cb = QCheckBox(name)
            cb.toggled.connect(lambda on, k=key: self._xy_toggled(k, on))
            if key.endswith("_hz"):
                f = FreqField(name, lambda hz, k=key: win.call("set_xy", **{k: int(hz)}), big=False)
            else:
                f = DbField(name, "", lambda v, k=key: win.call("set_xy", **{k: float(v)}), big=False)
            f.name_label.hide()
            grid.addWidget(cb, i, 0)
            grid.addWidget(f, i, 1)
            self.xy_checks[key] = cb
            self.xy_fields[key] = f
        self.dx = QLabel("")
        self.dy = QLabel("")
        grid.addWidget(self.dx, 4, 0, 1, 2)
        grid.addWidget(self.dy, 5, 0, 1, 2)
        lay.addWidget(g)

        # ---- ピークの一覧
        g = QGroupBox(L["peak_list"])
        v = QVBoxLayout(g)
        row = QHBoxLayout()
        self.pk_trace = QComboBox()
        for n in (1, 2, 3, 4):
            self.pk_trace.addItem(texts.w("trace_n", n=n), n)
        self.pk_margin = self._db_spin(10.0, 0.0, 100.0)
        self.pk_margin.setPrefix(L["floor_prefix"])
        self.pk_margin.setToolTip(L["floor_tip"])
        self.pk_find = QPushButton(L["find"])
        self.pk_find.setObjectName("small")
        self.pk_find.clicked.connect(self._find_peaks)
        row.addWidget(self.pk_trace)
        row.addWidget(self.pk_margin, 1)
        row.addWidget(self.pk_find)
        # ピークの一覧だけを別の窓に出す（J §4.3.2。押したときの動きは main_window が docks につなぐ）
        self.pk_detach = QPushButton(L["detach"])
        self.pk_detach.setObjectName("small")
        self.pk_detach.setToolTip(texts.w("detach_item", part=L["part_peaks"]))
        row.addWidget(self.pk_detach)
        v.addLayout(row)
        self.pk_table = QTableWidget(0, 4)
        self.pk_table.setHorizontalHeaderLabels([L["col_freq_mhz"], L["level"], L["col_height_db"], ""])
        self.pk_table.verticalHeader().hide()
        self.pk_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.pk_table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.pk_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.pk_table.horizontalHeader().setStretchLastSection(True)
        self.pk_table.setMinimumHeight(120)
        v.addWidget(self.pk_table)
        self.pk_note = QLabel("")
        self.pk_note.setObjectName("dim")
        v.addWidget(self.pk_note)
        row = QHBoxLayout()
        row.addWidget(QLabel(L["top"]))
        self.top_n = QSpinBox()
        self.top_n.setRange(1, 8)
        self.top_n.setValue(3)
        self.top_n.setSuffix(L["count_suffix"])
        row.addWidget(self.top_n)
        self.top_btn = QPushButton(L["place_p_after"])
        self.top_btn.setObjectName("small")
        self.top_btn.clicked.connect(
            lambda: win.call("marker_top_peaks", trace=self.pk_trace.currentData(), n=self.top_n.value()))
        row.addWidget(self.top_btn)
        row.addStretch(1)
        v.addLayout(row)
        lay.addWidget(g)
        self.peak_group = g
        lay.addStretch(1)
        self._num_font = number_font(self.pk_table.font())

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll(inner))
        self.apply(None, {})

    @staticmethod
    def _db_spin(value: float, lo: float, hi: float) -> QDoubleSpinBox:
        s = QDoubleSpinBox()
        s.setRange(lo, hi)
        s.setDecimals(1)
        s.setValue(value)
        s.setSuffix(" dB")
        s.setKeyboardTracking(False)
        return s

    # ---------------------------------------------------------------- 状態を映す

    def apply(self, marker: Optional[dict], snap: dict) -> None:
        """選んでいるマーカー（コアのマーカー表の1行）とマーカー表全体を映す。"""
        self._marker = marker
        reason = None if marker else REASON_NO_MARKER
        for w in (self.freq, self.trace, self.ref, self.to_center, self.to_ref, self.to_span, self.remove,
                  self.g_offset, self.g_func):
            set_enabled(w, reason)
        unit = self.win.display_unit()
        off = texts.freq_offset_of(self.win.state)  # 周波数の欄は換算後の周波数（設計O）
        self.freq.set_offset(off)
        for k in ("x1_hz", "x2_hz"):
            self.xy_fields[k].set_offset(off)
        if marker is None:
            self.sel_label.setText(L["no_marker_selected"])
            set_enabled(self.track, reason)
        else:
            kind = texts.MARKER_KINDS.get(marker["kind"], marker["kind"])
            val = f"{marker['level']:.2f} {marker.get('unit') or unit}" if marker.get("level") is not None else \
                texts.MARKER_STATUS.get(marker.get("status"), "")
            self.sel_label.setText(texts.w("selected_text", id=marker["id"], kind=kind, trace=marker["trace"], value=val))
            if marker.get("freq_hz") is not None:
                self.freq.set_hz(int(marker["freq_hz"]))
            widget_sync.set_data(self.trace, marker["trace"])
            ids = [None] + [m["id"] for m in snap.get("markers", []) if m["id"] != marker["id"]]
            # 一覧を開いている間は組み替えない（閉じたときに最新の状態で入れる）
            widget_sync.set_items(self.ref, [(L["none"] if i is None else i, i) for i in ids])
            widget_sync.set_data(self.ref, marker.get("ref") if marker.get("ref") else None)
            set_enabled(self.to_span, None if marker.get("ref") else texts.REASON_TEXTS["delta_ref"])
            set_enabled(self.track, None if marker["kind"] == "P" else REASON_TRACK_M)
            widget_sync.set_checked(self.track, bool(marker.get("peak_track")))
            bws = marker.get("bandwidth") or []
            for cb, spin, i in ((self.bw1, self.bw1_n, 0), (self.bw2, self.bw2_n, 1)):
                widget_sync.set_checked(cb, len(bws) > i)
                if len(bws) > i:
                    widget_sync.set_value(spin, float(bws[i]["n_db"]))
            th = marker.get("threshold")
            widget_sync.set_checked(self.th, th is not None)
            if th is not None:
                widget_sync.set_value(self.th_level, float(th["level"]))
                widget_sync.set_data(self.th_side, th["side"])
        xy = snap.get("xy") or {}
        for key, cb in self.xy_checks.items():
            v = xy.get(key)
            widget_sync.set_checked(cb, v is not None)
            f = self.xy_fields[key]
            f.setEnabled(v is not None)
            if isinstance(f, DbField):
                f.set_unit(unit)
            if v is not None:
                f.set_hz(int(v)) if isinstance(f, FreqField) else f.set_value(float(v))
        self.dx.setText(f"ΔX = {format_freq(int(xy['dx_hz']))}" if xy.get("dx_hz") is not None else "")
        self.dy.setText(f"ΔY = {xy['dy']:+.2f} dB" if xy.get("dy") is not None else "")

    # ---------------------------------------------------------------- 操作

    def _marker_set(self, **kw) -> None:
        if self._marker:
            self.win.call("marker_set", id=self._marker["id"], **kw)

    def _set_freq(self, hz: int) -> None:
        if not self._marker:
            return

        def done(r: dict) -> None:
            # 最も近い点に移したら、入れた値と違うことを一度だけ知らせる（G §3.1・M-02）
            if r.get("snapped") and r.get("freq_hz") is not None:
                self.freq.flash_note(texts.w("marker_snapped", freq=format_freq(
                    int(r["freq_hz"]) + texts.freq_offset_of(self.win.state))))

        self.win.call("marker_set", on_ok=done, field=self.freq, id=self._marker["id"], freq_hz=int(hz))

    def _to(self, target: str) -> None:
        if self._marker:
            self.win.marker_to(self._marker["id"], target)

    def _off_type_changed(self, _i: int) -> None:
        self.off_value.setText("10M" if self.off_type.currentData() == "freq" else "3")

    def _add_offset(self) -> None:
        if not self._marker:
            return
        typ = self.off_type.currentData()
        text = self.off_value.text()
        try:
            delta = parse_freq_hz(text) if typ == "freq" else parse_db(text)
        except ParseError:
            self.win.status_note(L["bad_offset_value"])
            return
        self.win.call("marker_offset_add", id=self._marker["id"], type=typ, sign=self.off_sign.currentData(),
                      delta=delta)

    def _send_bandwidth(self) -> None:
        ns = []
        if self.bw1.isChecked():
            ns.append(self.bw1_n.value())
        if self.bw2.isChecked():
            ns.append(self.bw2_n.value())
        if self._marker:
            self.win.call("marker_function", id=self._marker["id"], bandwidth_db=ns)

    def _send_threshold(self) -> None:
        th = {"level": self.th_level.value(), "side": self.th_side.currentData()} if self.th.isChecked() else None
        if self._marker:
            self.win.call("marker_function", id=self._marker["id"], threshold=th)

    def _xy_toggled(self, key: str, on: bool) -> None:
        if not on:
            self.win.call("set_xy", **{key: None})
            return
        sw = self.win.state.get("sweep") or {}
        if key.endswith("_hz"):
            # 置く位置の既定: 表示の 1/4 と 3/4（掴んで動かせる）
            start, span = sw.get("start_hz", 0), sw.get("span_hz", 0)
            v = int(start + span * (1 if key == "x1_hz" else 3) // 4)
        else:
            ref, div = self.win.ref_level(), self.win.db_div()
            v = float(ref - div * (3 if key == "y1" else 7))
        self.win.call("set_xy", **{key: v})

    def _find_peaks(self) -> None:
        trace = self.pk_trace.currentData()

        def show(r: dict) -> None:
            peaks = r.get("peaks") or []
            self.pk_table.setRowCount(len(peaks))
            for i, p in enumerate(peaks):
                cells = (f"{(p['freq_hz'] + texts.freq_offset_of(self.win.state)) / 1e6:.6f}".rstrip("0").rstrip("."), f"{p['level']:.2f} {r.get('unit', '')}",
                         f"{p['height_db']:.1f}" if p.get("height_db") is not None else "")
                for c, text in enumerate(cells):
                    it = QTableWidgetItem(text)
                    it.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                    it.setFont(self._num_font)
                    self.pk_table.setItem(i, c, it)
                b = QPushButton(L["register_p"])
                b.setObjectName("small")
                b.clicked.connect(lambda _=False, f=p["freq_hz"]: self.win.call(
                    "marker_register_peak", trace=trace, freq_hz=int(f)))
                self.pk_table.setCellWidget(i, 3, b)
            floor = r.get("floor")
            self.pk_note.setText(texts.w("peaks_found_floor", n=len(peaks), floor=f"{floor:.1f}", unit=r.get("unit", ""))
                                 if floor is not None else texts.w("peaks_found", n=len(peaks)))

        self.win.call("peak_list", on_ok=show, trace=trace, floor_margin_db=self.pk_margin.value())
