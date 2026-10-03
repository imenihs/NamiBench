"""右のタブ「マーカー」（設計J §4.5・G §3.5）。

- 選んでいるマーカー: 周波数の数値入力・載るトレース・デルタの基準・ピーク追従（P だけ）・条件へ（G §3.4）
- オフセット: 周波数／レベル、+・−・±（親について動くカーソル）
- マーカーの機能: N dB 帯域幅（N を2つでシェープファクタ）・しきい値との交点。dB の欄は打てるドロップダウン（G §3.6）。
  結果の線をグラフに出すかの切り替え（コアの表示の設定 view.function_lines）
- XY カーソル: X1・X2（周波数）・Y1・Y2（レベル）。トレースに載らない
- ゼロスパン（設計N §1）: マーカーと X1・X2 の欄は時間（ms）。周波数を前提にする欄（Center へ・Start/Stop へ・
  周波数のオフセット・マーカーの機能）は理由付きで止める（U-08。理由は screen_model.zero_span_view: Web と同じ）
- ピークの一覧と「P に登録」・「上位 N 個に P を置く」。山／谷はマーカー表の上の切り替えと同じ選択（G §3.8）。
  基準は電力でノイズフロア・基準との比で最大（P §9.7。欄の名前・差の列・見つけた数の行は peak_view で Web と同じ）
- 大きさと余白は sizes.py の決まり（J §4.11）。名前は欄の左（S-05。機能と XY カーソルはチェックボックスが名前）
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QGroupBox,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QWidget,
)

from ..core.units import ParseError, format_freq, format_time_us, parse_db, parse_freq_hz
from . import peak_view, screen_model, sizes, texts, widget_sync
from .fields import (BANDWIDTH_CHOICES, BANDWIDTH_RANGE, FLOOR_CHOICES, FLOOR_RANGE, REF_CHOICES, THRESHOLD_RANGE,
                     DbField, FreqField, LocalDbChoiceField)
from .marker_rows import function_lines_on
from .tab_traces import body, outer, set_enabled
from .theme import number_font

if TYPE_CHECKING:
    from .main_window import MainWindow

REASON_NO_MARKER = texts.REASON_TEXTS["select_marker"]
REASON_TRACK_M = texts.REASON_TEXTS["track_p_only"]
L = texts.LABELS
SIGNS = (("+", "+Δ"), ("-", "−Δ"), ("±", "±Δ"))
# ピークの一覧の表の高さの下限（見出しと数行。スクロールの中で潰れて行が見えなくならない）
PEAK_TABLE_MIN_H = 120


class _FailedNote:
    """win.call の失敗の知らせを受ける（欄の代わりに渡す）。戻りの待ちを1つ減らし、知らせは欄の下に出す。"""

    def __init__(self, tab: "MarkerTab", field: DbField) -> None:
        self.tab, self.field = tab, field

    def flash_note(self, text: str) -> None:
        self.field.flash_note(text)
        self.tab._function_done()


class MarkerTab(QWidget):
    def __init__(self, win: "MainWindow") -> None:
        super().__init__()
        self.win = win
        self._marker: Optional[dict] = None
        self._snap: dict = {}
        self._fn_pending = 0  # 送って戻りを待っているマーカーの機能の数（_send_function）
        inner, lay = body()
        self.body = lay  # 他の欄（聞く: listen.py）を差し込む

        # ---- 選んでいるマーカー
        g = QGroupBox(L["selected_marker"])
        form = sizes.form(g)
        self.sel_label = QLabel("")
        self.sel_label.setWordWrap(True)
        form.addRow(self.sel_label)
        self.freq = FreqField(L["freq"], self._set_freq, big=False)
        form.addRow(self.freq.name, self.freq)
        self._sel_form = form
        self.trace = QComboBox()
        for n in (1, 2, 3, 4):
            self.trace.addItem(texts.w("trace_n", n=n), n)
        self.trace.activated.connect(lambda _i: self._marker_set(trace=self.trace.currentData()))
        form.addRow(L["trace"], self.trace)
        self.ref = QComboBox()
        self.ref.activated.connect(lambda _i: self._marker_set(ref=self.ref.currentData()))
        form.addRow(L["delta_ref"], self.ref)
        self.track = QCheckBox(L["peak_track"])
        self.track.toggled.connect(lambda on: self._marker_set(peak_track=bool(on)))
        form.addRow(self.track)
        self.to_center = QPushButton(L["to_center"])
        self.to_ref = QPushButton(L["to_ref_level"])
        self.to_span = QPushButton(texts.MENU_ITEMS["marker_span"])
        self.remove = QPushButton(L["erase"])
        self.to_center.clicked.connect(lambda: self._to("center"))
        self.to_ref.clicked.connect(lambda: self._to("ref"))
        self.to_span.clicked.connect(lambda: self._to("span"))
        self.remove.clicked.connect(lambda: self._marker and win.call("marker_remove", id=self._marker["id"]))
        # 条件へ移すボタンを1行、デルタの2点と消すを次の行に（右のパネルの幅を広げない: J §4.3.3）
        form.addRow(sizes.buttons_row(self.to_center, self.to_ref))
        form.addRow(sizes.buttons_row(self.to_span, self.remove))
        lay.addWidget(g)

        # ---- オフセット
        g = QGroupBox(L["offsets"])
        gv = sizes.vbox(g)
        self.off_type = QComboBox()
        self.off_type.addItem(L["freq"], "freq")
        self.off_type.addItem(L["level"], "level")
        self.off_type.currentIndexChanged.connect(self._off_type_changed)
        self.off_sign = QComboBox()
        for code, name in SIGNS:
            self.off_sign.addItem(name, code)
        self.off_value = QLineEdit("10M")
        self.off_value.setToolTip(L["offset_value_tip"])
        sizes.set_width(self.off_value, "value")
        self.off_value.returnPressed.connect(self._add_offset)
        self.off_add = QPushButton(L["add"])
        self.off_add.clicked.connect(self._add_offset)
        row = sizes.hbox()
        for w in (self.off_type, self.off_sign, self.off_value, self.off_add):
            row.addWidget(w)
        row.addStretch(1)
        gv.addLayout(row)
        self.g_offset = g
        lay.addWidget(g)

        # ---- マーカーの機能（dB の欄は REF レベルと同じ打てるドロップダウン: G §3.6）。名前の列はチェックボックス
        g = QGroupBox(L["functions"])
        gl = sizes.vbox(g)
        self.func_body = QWidget()  # 選んでいるマーカーの機能（マーカーを選んでいないときは使えない）
        form = sizes.form()
        self.func_body.setLayout(form)
        self.bw1 = QCheckBox(L["n_db_bw"])
        self.bw1_n = self._db_field(3.0, BANDWIDTH_CHOICES, False, BANDWIDTH_RANGE, self._send_bandwidth)
        self.bw2 = QCheckBox(L["second_n"])
        self.bw2_n = self._db_field(60.0, BANDWIDTH_CHOICES, False, BANDWIDTH_RANGE, self._send_bandwidth)
        self.th = QCheckBox(L["threshold_cross"])
        self.th_level = self._db_field(-20.0, REF_CHOICES, True, THRESHOLD_RANGE, self._send_threshold)
        self.th_side = QComboBox()
        for code, name in texts.THRESHOLD_SIDES.items():
            self.th_side.addItem(name, code)
        for w in (self.bw1, self.bw2):
            w.toggled.connect(self._send_bandwidth)
        self.th.toggled.connect(self._send_threshold)
        self.th_side.activated.connect(lambda _i: self._send_threshold())
        form.addRow(self.bw1, self.bw1_n)
        form.addRow(self.bw2, self.bw2_n)
        row = sizes.hbox()
        row.addWidget(self.th_level)
        row.addWidget(self.th_side)
        row.addStretch(1)
        form.addRow(self.th, row)
        gl.addWidget(self.func_body)
        # 結果の線を出すかは表示の設定（全部のマーカーに効く。マーカーを選んでいなくても切り替えられる）
        self.function_lines = QCheckBox(L["function_lines"])
        self.function_lines.setToolTip(L["function_lines_tip"])
        self.function_lines.setChecked(True)
        self.function_lines.toggled.connect(lambda on: win.call("set_view", function_lines=bool(on)))
        gl.addWidget(self.function_lines)
        self.func_note = QLabel("")  # ゼロスパンでは使わない理由（U-08）。使えるときは出さない（U-15）
        self.func_note.setObjectName("dim")
        self.func_note.hide()
        gl.addWidget(self.func_note)
        self.g_func = g
        lay.addWidget(g)

        # ---- XY カーソル（名前の列はチェックボックス）
        g = QGroupBox(L["xy_cursor"])
        form = sizes.form(g)
        self.xy_checks: dict[str, QCheckBox] = {}
        self.xy_fields: dict[str, object] = {}
        for key, name in (("x1_hz", "X1"), ("x2_hz", "X2"), ("y1", "Y1"), ("y2", "Y2")):
            cb = QCheckBox(name)
            cb.toggled.connect(lambda on, k=key: self._xy_toggled(k, on))
            if key.endswith("_hz"):
                f = FreqField(name, lambda hz, k=key: win.call("set_xy", **{k: int(hz)}), big=False)
            else:
                f = DbField(name, "", lambda v, k=key: win.call("set_xy", **{k: float(v)}), big=False)
            form.addRow(cb, f)
            self.xy_checks[key] = cb
            self.xy_fields[key] = f
        self.dx = QLabel("")
        self.dy = QLabel("")
        form.addRow(self.dx)
        form.addRow(self.dy)
        lay.addWidget(g)

        # ---- ピークの一覧
        g = QGroupBox(L["peak_list"])
        v = sizes.vbox(g)
        form = sizes.form()
        self.pk_trace = QComboBox()
        for n in (1, 2, 3, 4):
            self.pk_trace.addItem(texts.w("trace_n", n=n), n)
        form.addRow(L["trace"], self.pk_trace)
        # 確定した値は欄が持ち、「探す」で使う。名前は基準で変わる（ノイズフロアから／最大から: P §9.7。apply_peaks）
        self.pk_margin = self._db_field(10.0, FLOOR_CHOICES, False, FLOOR_RANGE, lambda: None)
        self.pk_margin_label = QLabel(L["from_floor"])
        form.addRow(self.pk_margin_label, self.pk_margin)
        v.addLayout(form)
        self.pk_find = QPushButton(L["find"])
        self.pk_find.clicked.connect(self._find_peaks)
        # ピークの一覧だけを別の窓に出す（J §4.3.2。押したときの動きは main_window が docks につなぐ）
        self.pk_detach = QPushButton(L["detach"])
        self.pk_detach.setToolTip(texts.w("detach_item", part=L["part_peaks"]))
        v.addLayout(sizes.buttons_row(self.pk_find, self.pk_detach))
        self.pk_table = QTableWidget(0, 4)
        self.pk_table.setHorizontalHeaderLabels([L["col_freq_mhz"], L["level"], L["col_height_db"], ""])
        self.pk_table.verticalHeader().hide()
        self.pk_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.pk_table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.pk_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.pk_table.horizontalHeader().setStretchLastSection(True)
        self.pk_table.setMinimumHeight(PEAK_TABLE_MIN_H)
        v.addWidget(self.pk_table)
        self.pk_note = QLabel("")
        self.pk_note.setObjectName("dim")
        v.addWidget(self.pk_note)
        row = sizes.hbox()
        row.addWidget(QLabel(L["top"]))
        self.top_n = QSpinBox()
        self.top_n.setRange(1, 8)
        self.top_n.setValue(3)
        self.top_n.setSuffix(L["count_suffix"])
        sizes.set_width(self.top_n, "int")
        row.addWidget(self.top_n)
        self.top_btn = QPushButton(L["place_p_after"])
        self.top_btn.clicked.connect(
            lambda: win.call("marker_top_peaks", trace=self.pk_trace.currentData(), n=self.top_n.value(),
                             polarity=self._polarity()))
        row.addWidget(self.top_btn)
        row.addStretch(1)
        v.addLayout(row)
        lay.addWidget(g)
        self.peak_group = g
        lay.addStretch(1)
        self._num_font = number_font(self.pk_table.font())
        outer(self, inner)
        self.apply(None, {})
        self.apply_peaks()

    @staticmethod
    def _db_field(value: float, choices: tuple, signed: bool, rng: tuple[float, float], on_commit) -> LocalDbChoiceField:
        """dB の打てるドロップダウン（G §3.6）。範囲と小数1桁は前の欄（数の上下の欄）と同じ。名前は横の文字で出す。"""
        f = LocalDbChoiceField("", "dB", lambda v: on_commit(), choices, signed=signed, lo=rng[0], hi=rng[1],
                               decimals=1, big=False)
        f.set_value(value)
        return f

    # ---------------------------------------------------------------- 状態を映す

    def apply(self, marker: Optional[dict], snap: dict) -> None:
        """選んでいるマーカー（コアのマーカー表の1行）とマーカー表全体を映す。"""
        self._marker = marker
        self._snap = snap
        reason = None if marker else REASON_NO_MARKER
        # ゼロスパン（設計N §1）: 位置は時間。周波数を前提にする欄は理由付きで止める
        freq_only = screen_model.zero_span_view(self.win.state)["freq_only"]
        time = freq_only is not None
        for w in (self.freq, self.trace, self.ref, self.to_ref, self.remove, self.g_offset):
            set_enabled(w, reason)
        set_enabled(self.to_center, reason or freq_only)
        set_enabled(self.func_body, reason or freq_only)
        self.func_note.setText(freq_only or "")
        self.func_note.setVisible(time)
        self._apply_time_axis(time, freq_only)
        unit = self.win.display_unit()
        # しきい値の一覧は REF と同じく、いまの縦軸の単位で出す（G §3.6）
        self.th_level.set_unit(unit)
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
            set_enabled(self.to_span, freq_only or (None if marker.get("ref") else texts.REASON_TEXTS["delta_ref"]))
            set_enabled(self.track, None if marker["kind"] == "P" else REASON_TRACK_M)
            widget_sync.set_checked(self.track, bool(marker.get("peak_track")))
            if self._fn_pending == 0:  # 送った機能の戻りを待つ間は書き直さない（_send_function）
                self._apply_functions(marker)
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
        if xy.get("dx_hz") is None:
            self.dx.setText("")
        else:
            self.dx.setText(f"ΔX = {format_time_us(xy['dx_hz']) if time else format_freq(int(xy['dx_hz']))}")
        self.dy.setText(f"ΔY = {xy['dy']:+.2f} dB" if xy.get("dy") is not None else "")
        # 空の行を出さない（U-15）
        self.dx.setVisible(bool(self.dx.text()))
        self.dy.setVisible(bool(self.dy.text()))

    def _apply_time_axis(self, time: bool, freq_only) -> None:
        """マーカー・X1・X2 の欄を時間（ms）か周波数にする。オフセットの「周波数」は時間軸では選べない（U-08）。"""
        self.freq.set_time_axis(time)
        for k in ("x1_hz", "x2_hz"):
            self.xy_fields[k].set_time_axis(time)
        label = self._sel_form.labelForField(self.freq)
        if label is not None:
            label.setText(texts.ZERO_SPAN_TEXTS["time"] if time else L["freq"])
        model = self.off_type.model()
        item = model.item(0) if hasattr(model, "item") else None
        if item is not None:
            item.setEnabled(not time)
            item.setToolTip(freq_only or "")
        if time and self.off_type.currentData() == "freq":
            self.off_type.setCurrentIndex(1)

    def _apply_functions(self, marker: dict) -> None:
        bws = marker.get("bandwidth") or []
        for cb, field, i in ((self.bw1, self.bw1_n, 0), (self.bw2, self.bw2_n, 1)):
            widget_sync.set_checked(cb, len(bws) > i)
            if len(bws) > i:
                field.set_value(float(bws[i]["n_db"]))  # 打っている間は書かない（欄の決まり）
        th = marker.get("threshold")
        widget_sync.set_checked(self.th, th is not None)
        if th is not None:
            self.th_level.set_value(float(th["level"]))
            widget_sync.set_data(self.th_side, th["side"])

    def apply_view(self, st: dict) -> None:
        """表示の設定のうち、このタブにある「交点の線をグラフに出す」（G §3.6。コアの view.function_lines）。"""
        widget_sync.set_checked(self.function_lines, function_lines_on(st))

    # ---------------------------------------------------------------- 操作

    def _marker_set(self, **kw) -> None:
        if self._marker:
            self.win.call("marker_set", id=self._marker["id"], **kw)

    def _set_freq(self, hz: int) -> None:
        if not self._marker:
            return

        def done(r: dict) -> None:
            # 最も近い点に移したら、入れた値と違うことを一度だけ知らせる（G §3.1・M-02）。ゼロスパンは時間
            if r.get("snapped") and r.get("freq_hz") is not None:
                self.freq.flash_note(texts.w("marker_snapped", freq=screen_model.x_text(self.win.state,
                                                                                         int(r["freq_hz"]))))

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
        self._send_function(self.bw1_n, bandwidth_db=ns)

    def _send_threshold(self) -> None:
        th = {"level": self.th_level.value(), "side": self.th_side.currentData()} if self.th.isChecked() else None
        self._send_function(self.th_level, threshold=th)

    def _send_function(self, field: DbField, **kw) -> None:
        """マーカーの機能を送る。戻りを待つ間は機能の欄をコアの値で書き直さない（続けて回したホイールの値が、前に
        送った値の戻りで1つ前へ書き戻されないように。REF レベルの欄の送り方と同じ考え）。失敗は欄の下に出す。"""
        if not self._marker:
            return
        self._fn_pending += 1
        self.win.call("marker_function", on_ok=lambda _r: self._function_done(), field=_FailedNote(self, field),
                      id=self._marker["id"], **kw)

    def _function_done(self) -> None:
        self._fn_pending = max(0, self._fn_pending - 1)
        if self._fn_pending == 0:
            # 送った分の戻りは届いている（コアのイベントは命令の戻りより先に届く）。最新のマーカーの値を映す
            self.apply(self._marker, self._snap)

    def _xy_toggled(self, key: str, on: bool) -> None:
        if not on:
            self.win.call("set_xy", **{key: None})
            return
        if key.endswith("_hz"):
            # 置く位置の既定: 表示の 1/4 と 3/4（掴んで動かせる）。ゼロスパンは時間の軸の 1/4 と 3/4（設計N §1）
            xa = screen_model.x_axis_view(self.win.state)
            start, span = int(xa["x0"]), int(xa["x1"]) - int(xa["x0"])
            v = int(start + span * (1 if key == "x1_hz" else 3) // 4)
        else:
            ref, div = self.win.ref_level(), self.win.db_div()
            v = float(ref - div * (3 if key == "y1" else 7))
        self.win.call("set_xy", **{key: v})

    # ---------------------------------------------------------------- ピークの一覧（P §9.7）

    def _polarity(self) -> str:
        """探すもの（マーカー表の上の山／谷の切り替えと同じ選択: G §3.8）。"""
        return self.win.marker_panel.polarity if hasattr(self.win, "marker_panel") else peak_view.PEAK

    def apply_peaks(self) -> None:
        """一覧の欄を、いまの基準（ノイズフロア・最大）と山／谷の選択に合わせる。電力の谷は探せない（理由: U-08）。"""
        v = peak_view.list_view(self.win.state, self._polarity())
        self.pk_margin_label.setText(v["label"])
        self.pk_margin_label.setToolTip(v["tip"])
        self.pk_margin.setToolTip(v["tip"])
        set_enabled(self.pk_find, v["reason"])
        set_enabled(self.top_btn, v["reason"])
        if self.pk_table.rowCount() == 0:  # 出している結果があれば、その結果の列の名前のまま
            self.pk_table.setHorizontalHeaderItem(2, QTableWidgetItem(v["diff_header"]))

    def _find_peaks(self) -> None:
        trace = self.pk_trace.currentData()

        def show(r: dict) -> None:
            view = peak_view.result_view(r, texts.freq_offset_of(self.win.state))
            polarity = r.get("polarity") or peak_view.PEAK  # 「P に登録」は一覧の向き（山・谷）の P にする
            self.pk_table.setHorizontalHeaderItem(2, QTableWidgetItem(view["diff_header"]))
            self.pk_table.setRowCount(len(view["rows"]))
            for i, p in enumerate(view["rows"]):
                for c, text in enumerate((p["freq"], p["level"], p["diff"])):
                    it = QTableWidgetItem(text)
                    it.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                    it.setFont(self._num_font)
                    self.pk_table.setItem(i, c, it)
                b = QPushButton(L["register_p"])
                b.clicked.connect(lambda _=False, f=p["freq_hz"]: self.win.call(
                    "marker_register_peak", trace=trace, freq_hz=int(f), polarity=polarity))
                self.pk_table.setCellWidget(i, 3, b)
            self.pk_note.setText(view["found"])

        self.win.call("peak_list", on_ok=show, trace=trace, floor_margin_db=self.pk_margin.value(),
                      polarity=self._polarity())
