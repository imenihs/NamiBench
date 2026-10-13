"""右のタブ「表示」のうち、設計P の欄（J §4.5）。

- ウォーターフォール・密度: 行にするトレース・色の範囲（自動・下限・上限）・カラーマップ・保持する行数・密度の
  半分になる回数（P §9.2・§9.3）。色の範囲とカラーマップは Qt と Web で同じ（コアの表示の設定と配色: P §8）
- バンドプラン・ブックマーク: 出すか・読み込む・書き出す・ブックマークの一覧（Center に・消す）と追加（P §6・§9.5）
中身（選択肢の文字・いまの値・使えない理由）は display_view（Web と同じ）。大きさと余白は sizes.py の決まり（J §4.11）。
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtWidgets import QCheckBox, QComboBox, QGridLayout, QGroupBox, QLabel, QLineEdit, QPushButton, QWidget

from ..core.units import ParseError, parse_freq_hz
from . import display_view, sizes, texts, widget_sync
from .fields import DbField, FreqField
from .tab_traces import set_enabled

if TYPE_CHECKING:
    from .main_window import MainWindow

D = texts.DISPLAY_TEXTS
L = texts.LABELS


class WaterfallGroup(QGroupBox):
    """ウォーターフォール・密度の欄。"""

    def __init__(self, win: "MainWindow") -> None:
        super().__init__(D["wf_group"])
        self.win = win
        form = sizes.form(self)
        self.trace = QComboBox()
        for n in (1, 2, 3, 4):
            self.trace.addItem(texts.w("trace_n", n=n), n)
        self.trace.activated.connect(lambda i: win.call("set_view", wf_trace=int(self.trace.itemData(i))))
        form.addRow(D["wf_trace"], self.trace)
        self.auto = QCheckBox(D["auto"])
        self.auto.toggled.connect(lambda on: win.call("set_view", wf_color_auto=bool(on)))
        form.addRow(D["wf_color"], self.auto)
        self.lo = DbField(D["lower"], "dBm", lambda v: win.call("set_view", field=self.lo, wf_color_lo=float(v)),
                          big=False)
        form.addRow(self.lo.name, self.lo)
        self.hi = DbField(D["upper"], "dBm", lambda v: win.call("set_view", field=self.hi, wf_color_hi=float(v)),
                          big=False)
        form.addRow(self.hi.name, self.hi)
        self.colormap = QComboBox()
        self.colormap.activated.connect(lambda i: win.call("palette_set", colormap=self.colormap.itemData(i)))
        form.addRow(D["colormap"], self.colormap)
        self.rows = QComboBox()
        self.rows.activated.connect(lambda i: win.call("set_view", wf_rows=int(self.rows.itemData(i))))
        form.addRow(D["wf_rows"], self.rows)
        self.half = QComboBox()
        self.half.activated.connect(lambda i: win.call("set_view", density_half=int(self.half.itemData(i))))
        form.addRow(D["density_half"], self.half)
        v = display_view.display_tab_view({})
        widget_sync.set_items(self.colormap, [(c["text"], c["code"]) for c in v["colormaps"]])
        widget_sync.set_items(self.rows, [(c["text"], c["code"]) for c in v["rows_choices"]])
        widget_sync.set_items(self.half, [(c["text"], c["code"]) for c in v["half_choices"]])

    def apply_state(self, st: dict) -> None:
        v = display_view.display_tab_view(st)
        widget_sync.set_data(self.trace, v["trace"])
        widget_sync.set_checked(self.auto, v["auto"])
        set_enabled(self.auto, v["auto_reason"])  # |Γ|・SWR は縦軸と同じ範囲（設計R §3）
        for f, val in ((self.lo, v["lo"]), (self.hi, v["hi"])):
            f.set_unit(v["unit"])
            f.set_value(val)
            set_enabled(f.edit, v["range_reason"])
        widget_sync.set_data(self.colormap, v["colormap"])
        widget_sync.set_data(self.rows, v["rows"])
        widget_sync.set_data(self.half, v["half"])


class BandplanGroup(QGroupBox):
    """バンドプラン・ブックマークの欄。"""

    def __init__(self, win: "MainWindow") -> None:
        super().__init__(D["bandplan_group"])
        self.win = win
        v = sizes.vbox(self)
        self.show_bands = QCheckBox(L["show_bandplan"])
        self.show_bands.toggled.connect(lambda on: win.call("set_view", bandplan_shown=bool(on)))
        self.show_marks = QCheckBox(D["show_bookmarks"])
        self.show_marks.toggled.connect(lambda on: win.call("set_view", bookmarks_shown=bool(on)))
        row = sizes.hbox()
        row.addWidget(self.show_bands)
        row.addWidget(self.show_marks)
        row.addStretch(1)
        v.addLayout(row)
        self.load = QPushButton(D["bandplan_load"])
        self.load.clicked.connect(self._load)
        self.save = QPushButton(D["bandplan_save"])
        self.save.clicked.connect(self._save)
        self.count = QLabel("")
        self.count.setObjectName("dim")
        row = sizes.buttons_row(self.load, self.save)
        row.insertWidget(2, self.count)
        v.addLayout(row)
        self.list_box = QWidget()
        self.grid = QGridLayout(self.list_box)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setHorizontalSpacing(sizes.GAP)
        self.grid.setVerticalSpacing(sizes.GAP)
        v.addWidget(self.list_box)
        self.empty = QLabel(D["no_bookmarks"])
        self.empty.setObjectName("dim")
        v.addWidget(self.empty)
        add = sizes.hbox()
        self.name = QLineEdit()
        self.name.setPlaceholderText(D["bookmark_name"])
        sizes.set_width(self.name, "name")
        add.addWidget(self.name)
        # 周波数は空なら いまの Center（見本に出す: Web と同じ）。打つのは表示の周波数（周波数オフセット込み: 設計O）
        self.freq = QLineEdit()
        sizes.set_width(self.freq, "value")
        self.freq.returnPressed.connect(self._add)
        add.addWidget(self.freq)
        self.add_btn = QPushButton(L["add"])
        self.add_btn.clicked.connect(self._add)
        add.addWidget(self.add_btn)
        add.addStretch(1)
        v.addLayout(add)
        self._key: tuple = ()

    def _load(self) -> None:
        paths = self.win.ask_open_paths(D["bandplan_load_title"], str(self.win.files.last_dir()), D["filter_json"])
        if paths:
            self.win.call("bandplan_load", path=str(paths[0]))

    def _save(self) -> None:
        path = self.win.ask_save_path(D["bandplan_save_title"], str(self.win.files.last_dir() / "bandplan.json"),
                                      D["filter_json"])
        if path:
            self.win.call("bandplan_save", path=str(path),
                          on_ok=lambda r: self.win.status_note(texts.tpl("saved", name=r.get("path", ""))))

    def _add(self) -> None:
        name = self.name.text().strip()
        if not name:
            self.win.status_note(texts.ERRORS["bandplan_bad_name"])
            return
        text = self.freq.text().strip() or self.freq.placeholderText()
        try:
            hz = parse_freq_hz(text) - texts.freq_offset_of(self.win.state)
        except ParseError:
            self.win.status_note(texts.LABELS["bad_freq"])
            return
        self.win.call("bookmark_add", name=name, hz=int(hz), on_ok=lambda _r: self.name.clear())

    def apply_state(self, st: dict) -> None:
        dv = display_view.display_tab_view(st)
        bv = display_view.bandplan_view(st)
        widget_sync.set_checked(self.show_bands, dv["bandplan_shown"])
        widget_sync.set_checked(self.show_marks, dv["bookmarks_shown"])
        self.count.setText("・".join(t for t in (bv["count"], bv["file"]) if t))
        fv = display_view.freq_view(st)
        if fv:
            self.freq.setPlaceholderText(FreqField._to_text(int(fv["center_hz"]) + texts.freq_offset_of(st)))
        key = tuple((m["name"], m["hz"], m["freq"]) for m in bv["list"])
        if key == self._key:
            return
        self._key = key
        while self.grid.count():
            it = self.grid.takeAt(0)
            if it.widget() is not None:
                it.widget().deleteLater()
        for i, m in enumerate(bv["list"]):
            self.grid.addWidget(QLabel(m["name"]), i, 0)
            self.grid.addWidget(QLabel(m["freq"]), i, 1)
            c = QPushButton(D["to_center"])
            c.clicked.connect(lambda _=False, hz=m["hz"]: self.win.display.center_on(int(hz)))
            self.grid.addWidget(c, i, 2)
            r = QPushButton(L["remove"])
            r.clicked.connect(lambda _=False, hz=m["hz"], name=m["name"]: self.win.call(
                "bookmark_remove", hz=int(hz), name=name))
            self.grid.addWidget(r, i, 3)
        self.grid.setColumnStretch(4, 1)
        self.empty.setVisible(not bv["list"])
        self.list_box.setVisible(bool(bv["list"]))
