"""設定の画面（設計O §1.3。メニュー「設定 → 設定…」から開く別の画面: M-10）。

項目（ページ）: 接続（覚えた機器・COM 番号で繋ぐ）／配色（J §4.4）／通知（比の表示に変わったら窓で知らせるか:
J §4.10）／キー操作（J §4.6）／保存（既定の保存先・PNG の大きさと配色: 設計I）／ブラウザから使う（設計A §4.3.2）／
rtl_tcp の送出（設計Q §4.3・§6.7）／RTL-SDR の DLL（設計Q §6.2）。
変えたらすぐ効く（「適用」を置かない。そのことは画面に書かない: U-24）。戻したいときは各項目の「初期値に戻す」。
大きさと余白は sizes.py の決まり（J §4.11）、文は名前だけ（J §4.12・U-24）。
状態はコアが正本で、開いている間は state を映し続ける。保存の項目とキーの割り当てと Web の口は、この画面の
覚え書き（設計O §1.1: 画面ごと）。
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QGroupBox,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QWidget,
)

from ..core.report_layout import PNG_PALETTE_CHOICES, PNG_PALETTE_DEFAULT
from . import screen_model, sizes, texts, widget_sync
from .settings_colors import PalettePage
from .settings_keys import KeysPage

if TYPE_CHECKING:
    from .main_window import MainWindow

DEFAULT_PORT = 1234
WEB_DEFAULT_PORT = 8080
PNG_SIZES = ((1600, 1000), (1920, 1080), (1280, 800), (2560, 1600))
PNG_SIZE_DEFAULT = "1600×1000"
T = texts.SETTINGS_TEXTS


def _reset_button(fn, tip: str = "") -> QPushButton:
    b = QPushButton(T["reset"])
    b.setToolTip(tip or T["reset_tip"])
    b.clicked.connect(fn)
    return b


def _restyle(lab: QLabel, name: str) -> None:
    lab.setObjectName(name)
    lab.style().unpolish(lab)
    lab.style().polish(lab)


def _path_label() -> QLabel:
    """読むだけの長い文字（フォルダ）。窓の幅に合わせて伸ばしてよい（S-02）。"""
    lab = QLabel("")
    lab.setWordWrap(True)
    lab.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    sizes.grow(lab)
    return lab


def _page() -> tuple[QWidget, object]:
    page = QWidget()
    v = sizes.vbox(page)
    v.setSpacing(sizes.SECTION_GAP)
    return page, v


class SettingsDialog(QDialog):
    def __init__(self, win: "MainWindow") -> None:
        super().__init__(win)
        S = texts.SETTINGS_LABELS
        self.win = win
        self.setWindowTitle(S["title"])
        lay = sizes.vbox(self, inset=False)
        sizes.window(lay)
        self.pages = QTabWidget()
        self.pages.setUsesScrollButtons(True)
        lay.addWidget(self.pages, 1)
        self._page_index: dict[str, int] = {}
        P = texts.SETTINGS_PAGES
        self._add_page("connection", P["connection"], self._build_connection())
        self.palette_page = PalettePage(win)
        self._add_page("display", P["display"], self.palette_page)
        self._add_page("notify", P["notify"], self._build_notify())
        self.keys_page = KeysPage(win)
        self._add_page("keys", P["keys"], self.keys_page)
        self._add_page("save", P["save"], self._build_save())
        self._add_page("web", P["web"], self._build_web())
        self._add_page("rtltcp", P["rtltcp"], self._build_rtltcp())
        self._add_page("rtlsdr", P["rtlsdr"], self._build_rtlsdr())

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        btns.button(QDialogButtonBox.StandardButton.Close).setText(S["close"])
        btns.rejected.connect(self.close)
        lay.addWidget(btns)
        self.resize(720, 640)
        widget_sync.fit_all(self)  # ドロップダウンは一番長い選択肢が切れない幅（J §4.3）
        self.apply(win.state)
        self.apply_notify()
        self.apply_web()
        self.show_save()

    def _add_page(self, key: str, title: str, body: QWidget) -> None:
        self._page_index[key] = self.pages.addTab(body, title)

    def open_page(self, key: str) -> None:
        if key in self._page_index:
            self.pages.setCurrentIndex(self._page_index[key])

    # ---------------------------------------------------------------- 通知（窓で知らせるもの。配色とは用事が違うので別のページ: U-10）

    def _build_notify(self) -> QWidget:
        """比の表示に変わったときの窓を出すか（J §4.10 の 3。「次から出さない」を戻す所。この画面だけの覚え書き:
        設計O §1.1）。窓で知らせるものが増えたらここに並べる。"""
        from .tab_display import PREF_NOTIFY_RATIO

        V = texts.VIEW_TEXTS
        page, v = _page()
        self.notify_ratio = QCheckBox(V["notify_ratio"])
        self.notify_ratio.setToolTip(V["notify_ratio_tip"])
        self.notify_ratio.toggled.connect(lambda on: self.win.prefs.set(PREF_NOTIFY_RATIO, bool(on)))
        v.addWidget(self.notify_ratio)
        v.addStretch(1)
        return page

    def apply_notify(self) -> None:
        from .tab_display import notify_ratio_on

        widget_sync.set_checked(self.notify_ratio, notify_ratio_on(self.win))

    # ---------------------------------------------------------------- 接続（覚えた機器・COM 番号で繋ぐ）

    def _build_connection(self) -> QWidget:
        page, v = _page()
        g = QGroupBox(T["known_group"])
        gv = sizes.vbox(g)
        self.known = QTableWidget(0, 3)
        self.known.horizontalHeader().hide()
        self.known.verticalHeader().hide()
        self.known.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.known.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        hh = self.known.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        gv.addWidget(self.known)
        self.known_none = QLabel(T["known_none"])
        self.known_none.setObjectName("dim")
        gv.addWidget(self.known_none)
        open_list = QPushButton(T["open_devices"])
        open_list.clicked.connect(self.win._connect_dialog)
        gv.addLayout(sizes.buttons_row(open_list))
        v.addWidget(g, 1)
        g = QGroupBox(T["manual_group"])
        form = sizes.form(g)
        self.manual_port = QLineEdit(str(self.win.prefs.get("manual_port", "") or ""))
        self.manual_port.setPlaceholderText(T["manual_placeholder"])
        sizes.set_width(self.manual_port, "int")
        self.manual_port.editingFinished.connect(
            lambda: self.win.prefs.set("manual_port", self.manual_port.text().strip()))
        self.manual_connect = QPushButton(T["manual_connect"])
        self.manual_connect.clicked.connect(self._connect_manual)
        row = sizes.hbox()
        row.addWidget(self.manual_port)
        row.addWidget(self.manual_connect)
        row.addStretch(1)
        form.addRow(T["manual_port"], row)
        v.addWidget(g)
        self._known_key: tuple = ()
        return page

    def _apply_known(self, st: dict) -> None:
        rows = st.get("known_devices") or []
        key = tuple((r["serial"], r.get("name"), r.get("last")) for r in rows)
        if key == self._known_key:
            return
        self._known_key = key
        self.known.setRowCount(len(rows))
        for i, r in enumerate(rows):
            name = r.get("name") or r["serial"]
            self.known.setItem(i, 0, QTableWidgetItem(f"{name}（{r['serial']}）" if r.get("name") else name))
            self.known.setItem(i, 1, QTableWidgetItem(T["known_last"] if r.get("last") else ""))
            b = QPushButton(T["forget"])
            b.clicked.connect(lambda _=False, s=r["serial"], n=name: self._forget(s, n))
            self.known.setCellWidget(i, 2, b)
        self.known.setVisible(bool(rows))
        self.known_none.setVisible(not rows)

    def _forget(self, serial: str, name: str) -> None:
        if self.win.confirm(T["forget_title"], T["forget_confirm"].replace("{name}", name), texts.LABELS["do_forget"]):
            self.win.call("forget_device", serial=serial)

    def _connect_manual(self) -> None:
        port = self.manual_port.text().strip().upper()
        if not port.startswith("COM") or not port[3:].isdigit():
            self.win.status_note(T["manual_bad"])
            return
        self.win.prefs.set("manual_port", port)
        self.manual_port.setText(port)
        self.win.call("connect", backend="gigast", port=port)

    # ---------------------------------------------------------------- 保存

    def _build_save(self) -> QWidget:
        page, v = _page()
        form = sizes.form()
        self.save_dir = _path_label()
        form.addRow(T["save_dir"], self._dir_row(self.save_dir, "last_dir"))
        self.png_dir = _path_label()
        form.addRow(T["png_dir"], self._dir_row(self.png_dir, "png_dir"))
        self.png_size = QComboBox()
        for w, h in PNG_SIZES:
            self.png_size.addItem(f"{w}×{h}", (w, h))
        self.png_size.activated.connect(lambda _i: self._set_pref("png_size", self.png_size.currentText()))
        form.addRow(T["png_size"], self._choice_row(self.png_size, lambda: self._set_pref("png_size", PNG_SIZE_DEFAULT)))
        # ユーザー指定（画面と同じ色）／ライト／ダーク。既定はユーザー指定（I §3.6）。意味は選択肢のツールチップ
        self.png_palette = QComboBox()
        for code in PNG_PALETTE_CHOICES:
            self.png_palette.addItem(texts.PNG_PALETTES[code], code)
            self.png_palette.setItemData(self.png_palette.count() - 1, texts.PNG_PALETTE_TIPS[code],
                                         Qt.ItemDataRole.ToolTipRole)
        self.png_palette.activated.connect(lambda _i: self._set_pref("png_palette", self.png_palette.currentData()))
        form.addRow(T["png_palette"], self._choice_row(
            self.png_palette, lambda: self._set_pref("png_palette", PNG_PALETTE_DEFAULT)))
        v.addLayout(form)
        v.addStretch(1)
        return page

    @staticmethod
    def _choice_row(combo: QComboBox, reset):
        row = sizes.hbox()
        row.addWidget(combo)
        row.addWidget(_reset_button(reset))
        row.addStretch(1)
        return row

    def _dir_row(self, label: QLabel, pref: str):
        row = sizes.hbox()
        row.addWidget(label, 1)
        b = QPushButton(T["choose"])
        b.clicked.connect(lambda: self._choose_dir(pref))
        row.addWidget(b)
        row.addWidget(_reset_button(lambda: self._set_pref(pref, "")))
        return row

    def _choose_dir(self, pref: str) -> None:
        start = str(self.win.files.last_dir() if pref == "last_dir" else self.win.files.png_dir())
        d = QFileDialog.getExistingDirectory(self, T["save_dir"] if pref == "last_dir" else T["png_dir"], start)
        if d:
            self._set_pref(pref, str(Path(d)))

    def _set_pref(self, key: str, value) -> None:
        self.win.prefs.set(key, value)
        self.show_save()

    def show_save(self) -> None:
        f = self.win.files
        self.save_dir.setText(str(f.last_dir()))
        self.png_dir.setText(str(f.png_dir()))
        self.png_size.setCurrentIndex(max(0, self.png_size.findText(str(self.win.prefs.get("png_size", PNG_SIZE_DEFAULT)))))
        self.png_palette.setCurrentIndex(max(0, self.png_palette.findData(self.win.display_tab.png_palette_choice())))

    # ---------------------------------------------------------------- ブラウザから使う（A §4.3.2）

    def _build_web(self) -> QWidget:
        W = texts.WEB_LABELS
        page, v = _page()
        box = sizes.vbox(inset=False)
        self.web_enable = QCheckBox(W["enable"])
        self.web_enable.toggled.connect(lambda on: self.win.web.set_enabled(bool(on)))
        box.addWidget(self.web_enable)
        self.web_lan = QCheckBox(W["lan"])
        self.web_lan.toggled.connect(lambda on: self.win.web.set_lan(bool(on)))
        box.addWidget(self.web_lan)
        # 暗号化していないことは、スマートフォンや PC からも使う間だけ、そのチェックのすぐ下に出す（A §4.3.2 の 7・U-14）
        self.web_lan_warn = QLabel(W["lan_warn"])
        self.web_lan_warn.setObjectName("warn")
        box.addWidget(self.web_lan_warn)
        v.addLayout(box)
        form = sizes.form()
        self.web_port = QSpinBox()
        self.web_port.setRange(1, 65535)
        self.web_port.setKeyboardTracking(False)
        sizes.set_width(self.web_port, "int")
        self.web_port.valueChanged.connect(lambda val: self.win.web.set_port(int(val)))
        form.addRow(W["port"], self.web_port)
        v.addLayout(form)
        self.web_state = QLabel("")
        self.web_state.setWordWrap(True)
        self.web_state.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        v.addWidget(self.web_state)
        self.web_show = QPushButton(W["show_connect"])
        self.web_show.clicked.connect(self.win.web.show_connect)
        self.web_regen = QPushButton(W["regenerate"])
        self.web_regen.clicked.connect(self.win.web.regenerate)
        v.addLayout(sizes.buttons_row(self.web_show, self.web_regen, _reset_button(self._reset_web)))
        v.addStretch(1)
        return page

    def _reset_web(self) -> None:
        self.win.web.set_lan(False)
        self.win.web.set_port(WEB_DEFAULT_PORT)
        self.win.web.set_enabled(False)

    def apply_web(self) -> None:
        """Web の口の欄（状態はコアではなく画面の側の WebControl が持つ）。"""
        from .tab_traces import set_enabled

        v = self.win.web.view()
        widget_sync.set_checked(self.web_enable, v["enabled"])
        widget_sync.set_checked(self.web_lan, v["lan"])
        widget_sync.set_value(self.web_port, int(v["port"]))
        set_enabled(self.web_lan, v["lan_reason"])
        set_enabled(self.web_show, v["connect_reason"])
        set_enabled(self.web_regen, v["connect_reason"], texts.WEB_LABELS["regenerate_tip"])
        self.web_lan_warn.setVisible(bool(v["lan"]))
        self.web_state.setText(v["text"])
        _restyle(self.web_state, "warn" if v["warn"] else "")

    # ---------------------------------------------------------------- rtl_tcp

    def _build_rtltcp(self) -> QWidget:
        S = texts.SETTINGS_LABELS
        page, v = _page()
        self.enable = QCheckBox(S["rtltcp_enable"])
        self.enable.toggled.connect(lambda on: self.win.call("rtltcp_set", enabled=bool(on)))
        v.addWidget(self.enable)
        form = sizes.form()
        self.port = QSpinBox()
        self.port.setRange(1, 65535)
        self.port.setKeyboardTracking(False)
        sizes.set_width(self.port, "int")
        self.port.valueChanged.connect(lambda val: self.win.call("rtltcp_set", port=int(val)))
        form.addRow(S["rtltcp_port"], self.port)
        self.bind = QComboBox()
        for code, name in texts.RTLTCP_BINDS.items():
            self.bind.addItem(name, code)
        self.bind.activated.connect(lambda _i: self.win.call("rtltcp_set", bind=self.bind.currentData()))
        form.addRow(S["rtltcp_bind"], self.bind)
        v.addLayout(form)
        state = sizes.vbox(inset=False)
        self.rt_state = QLabel("")
        self.rt_state.setWordWrap(True)
        state.addWidget(self.rt_state)
        self.rt_error = QLabel("")
        self.rt_error.setWordWrap(True)
        self.rt_error.setObjectName("warn")
        state.addWidget(self.rt_error)
        v.addLayout(state)
        reset = _reset_button(lambda: self.win.call("rtltcp_set", enabled=False, port=DEFAULT_PORT, bind="local"))
        v.addLayout(sizes.buttons_row(reset))
        v.addStretch(1)
        return page

    # ---------------------------------------------------------------- RTL-SDR の DLL

    def _build_rtlsdr(self) -> QWidget:
        """見つけた rtlsdr.dll の場所を出す。無い・32 ビット版なら注意の色で理由（J §4.12: 「読めます」は
        ファイルがあることしか確かめていないのに読めたと言っていた）。"""
        S = texts.SETTINGS_LABELS
        page, v = _page()
        form = sizes.form()
        self.dll_state = _path_label()
        self.dll_reason = QLabel("")
        self.dll_reason.setObjectName("warn")
        field = sizes.vbox(inset=False)
        field.addWidget(self.dll_state)
        field.addWidget(self.dll_reason)
        form.addRow(S["rtlsdr_dll"], field)
        v.addLayout(form)
        choose = QPushButton(S["rtlsdr_choose"])
        choose.clicked.connect(self._choose_dll)
        self.dll_default = _reset_button(lambda: self.win.call("rtlsdr_set", dll_folder=None), S["rtlsdr_default_tip"])
        v.addLayout(sizes.buttons_row(choose, self.dll_default))
        v.addStretch(1)
        return page

    def _choose_dll(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, texts.SETTINGS_LABELS["rtlsdr_choose_title"])
        if folder:
            self.win.call("rtlsdr_set", dll_folder=folder)

    # ---------------------------------------------------------------- 状態を映す

    def apply(self, st: dict) -> None:
        if not st:
            return
        r = screen_model.rtltcp_view(st)
        widget_sync.set_checked(self.enable, r["enabled"])
        widget_sync.set_value(self.port, int(r["port"] or DEFAULT_PORT))
        widget_sync.set_data(self.bind, r["bind"])
        self.rt_state.setText(r["text"] + (f"\n{r['dropped_text']}" if r["dropped_text"] else ""))
        self.rt_error.setText(r["error_text"])
        self.rt_error.setVisible(bool(r["error_text"]))
        rtl = screen_model.rtl_view(st)
        self.dll_state.setText(rtl["dll_line"])
        _restyle(self.dll_state, "warn" if rtl["dll_warn"] else "")
        self.dll_reason.setText(rtl["dll_reason"])
        self.dll_reason.setVisible(bool(rtl["dll_reason"]))
        self.dll_default.setEnabled(bool((st.get("rtlsdr") or {}).get("dll_folder")))
        self._apply_known(st)
        self.palette_page.apply(st)
        self.apply_notify()
