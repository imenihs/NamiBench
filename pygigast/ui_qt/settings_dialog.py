"""アプリ設定の画面（設計O §1.3。メニュー「設定 → アプリ設定…」から開く別の画面: M-10）。

項目（ページ）: 接続（覚えた機器・手動の COM 番号）／表示（配色の編集: J §4.4）／キー操作（J §4.6）／
保存（既定の保存先・PNG の大きさと配色: 設計I）／ブラウザから使う（設計A §4.3.2）／rtl_tcp の送出
（設計Q §4.3・§6.7）／RTL-SDR の DLL のフォルダ（設計Q §6.2）。
変えたらすぐ効く（「適用」を置かない）。戻したいときは各項目の「初期値に戻す」。
状態はコアが正本で、開いている間は state を映し続ける。保存の項目とキーの割り当てと Web の口は、この画面の
覚え書き（設計O §1.1: 画面ごと）。
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..core.report_layout import PNG_PALETTE_CHOICES, PNG_PALETTE_DEFAULT
from . import screen_model, texts, widget_sync
from .settings_colors import PalettePage
from .settings_keys import KeysPage

if TYPE_CHECKING:
    from .main_window import MainWindow

DEFAULT_PORT = 1234
WEB_DEFAULT_PORT = 8080
PNG_SIZES = ((1600, 1000), (1920, 1080), (1280, 800), (2560, 1600))
PNG_SIZE_DEFAULT = "1600×1000"
T = texts.SETTINGS_TEXTS


def _reset_button(fn) -> QPushButton:
    b = QPushButton(T["reset"])
    b.setObjectName("small")
    b.setToolTip(T["reset_tip"])
    b.clicked.connect(fn)
    return b


def _dim(text: str) -> QLabel:
    lab = QLabel(text)
    lab.setObjectName("dim")
    lab.setWordWrap(True)
    return lab


def _restyle(lab: QLabel, name: str) -> None:
    lab.setObjectName(name)
    lab.style().unpolish(lab)
    lab.style().polish(lab)


class SettingsDialog(QDialog):
    def __init__(self, win: "MainWindow") -> None:
        super().__init__(win)
        S = texts.SETTINGS_LABELS
        self.win = win
        self.setWindowTitle(S["title"])
        lay = QVBoxLayout(self)
        lay.addWidget(_dim(S["note"]))
        self.pages = QTabWidget()
        self.pages.setUsesScrollButtons(True)
        lay.addWidget(self.pages, 1)
        self._page_index: dict[str, int] = {}
        P = texts.SETTINGS_PAGES
        self._add_page("connection", P["connection"], self._build_connection())
        self.palette_page = PalettePage(win)
        self._add_page("display", P["display"], self.palette_page)
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
        self.resize(680, 640)
        widget_sync.fit_all(self)  # ドロップダウンは一番長い選択肢が切れない幅（J §4.3）
        self.apply(win.state)
        self.apply_web()
        self.show_save()

    def _add_page(self, key: str, title: str, body: QWidget) -> None:
        self._page_index[key] = self.pages.addTab(body, title)

    def open_page(self, key: str) -> None:
        if key in self._page_index:
            self.pages.setCurrentIndex(self._page_index[key])

    # ---------------------------------------------------------------- 接続（覚えた機器・手動の COM 番号）

    def _build_connection(self) -> QWidget:
        page = QWidget()
        v = QVBoxLayout(page)
        g = QGroupBox(T["known_group"])
        gv = QVBoxLayout(g)
        self.known = QTableWidget(0, 3)
        self.known.horizontalHeader().hide()
        self.known.verticalHeader().hide()
        self.known.horizontalHeader().setStretchLastSection(False)
        self.known.setColumnWidth(0, 300)
        gv.addWidget(self.known)
        self.known_none = _dim(T["known_none"])
        gv.addWidget(self.known_none)
        open_list = QPushButton(T["open_devices"])
        open_list.setObjectName("small")
        open_list.clicked.connect(self.win._connect_dialog)
        gv.addWidget(open_list)
        v.addWidget(g)
        g = QGroupBox(T["manual_group"])
        form = QFormLayout(g)
        row = QHBoxLayout()
        self.manual_port = QLineEdit(str(self.win.prefs.get("manual_port", "") or ""))
        self.manual_port.setPlaceholderText(T["manual_placeholder"])
        self.manual_port.editingFinished.connect(
            lambda: self.win.prefs.set("manual_port", self.manual_port.text().strip()))
        row.addWidget(self.manual_port, 1)
        self.manual_connect = QPushButton(T["manual_connect"])
        self.manual_connect.setObjectName("small")
        self.manual_connect.clicked.connect(self._connect_manual)
        row.addWidget(self.manual_connect)
        row.addWidget(_reset_button(self._reset_manual))
        form.addRow(T["manual_port"], row)
        form.addRow(_dim(T["manual_note"]))
        v.addWidget(g)
        v.addStretch(1)
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
            b.setObjectName("small")
            b.clicked.connect(lambda _=False, s=r["serial"], n=name: self._forget(s, n))
            self.known.setCellWidget(i, 2, b)
        self.known.setVisible(bool(rows))
        self.known_none.setVisible(not rows)

    def _forget(self, serial: str, name: str) -> None:
        if self.win.confirm(T["forget_title"], T["forget_confirm"].replace("{name}", name)):
            self.win.call("forget_device", serial=serial)

    def _connect_manual(self) -> None:
        port = self.manual_port.text().strip().upper()
        if not port.startswith("COM") or not port[3:].isdigit():
            self.win.status_note(T["manual_bad"])
            return
        self.win.prefs.set("manual_port", port)
        self.manual_port.setText(port)
        self.win.call("connect", backend="gigast", port=port)

    def _reset_manual(self) -> None:
        self.manual_port.setText("")
        self.win.prefs.set("manual_port", "")

    # ---------------------------------------------------------------- 保存

    def _build_save(self) -> QWidget:
        page = QWidget()
        v = QVBoxLayout(page)
        g = QGroupBox(T["save_group"])
        form = QFormLayout(g)
        self.save_dir = QLabel("")
        self.save_dir.setWordWrap(True)
        form.addRow(T["save_dir"], self._dir_row(self.save_dir, "last_dir"))
        self.png_dir = QLabel("")
        self.png_dir.setWordWrap(True)
        form.addRow(T["png_dir"], self._dir_row(self.png_dir, "png_dir"))
        row = QHBoxLayout()
        self.png_size = QComboBox()
        for w, h in PNG_SIZES:
            self.png_size.addItem(f"{w}×{h}", (w, h))
        self.png_size.activated.connect(lambda _i: self._set_pref("png_size", self.png_size.currentText()))
        row.addWidget(self.png_size, 1)
        row.addWidget(_reset_button(lambda: self._set_pref("png_size", PNG_SIZE_DEFAULT)))
        form.addRow(T["png_size"], row)
        row = QHBoxLayout()
        # ユーザー指定（画面と同じ色）／ライト／ダーク。既定はユーザー指定（I §3.6）
        self.png_palette = QComboBox()
        for code in PNG_PALETTE_CHOICES:
            self.png_palette.addItem(texts.PNG_PALETTES[code], code)
        self.png_palette.activated.connect(lambda _i: self._set_pref("png_palette", self.png_palette.currentData()))
        row.addWidget(self.png_palette, 1)
        row.addWidget(_reset_button(lambda: self._set_pref("png_palette", PNG_PALETTE_DEFAULT)))
        form.addRow(T["png_palette"], row)
        form.addRow(_dim(T["png_palette_note"]))
        v.addWidget(g)
        v.addStretch(1)
        return page

    def _dir_row(self, label: QLabel, pref: str) -> QHBoxLayout:
        row = QHBoxLayout()
        row.addWidget(label, 1)
        b = QPushButton(T["choose"])
        b.setObjectName("small")
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

    # ---------------------------------------------------------------- ブラウザから使う（A §4.3.2。暗号化していないことを書く: 7）

    def _build_web(self) -> QWidget:
        W = texts.WEB_LABELS
        page = QWidget()
        v = QVBoxLayout(page)
        g = QGroupBox(W["group"])
        form = QFormLayout(g)
        self.web_enable = QCheckBox(W["enable"])
        self.web_enable.toggled.connect(lambda on: self.win.web.set_enabled(bool(on)))
        form.addRow(self.web_enable)
        self.web_lan = QCheckBox(W["lan"])
        self.web_lan.toggled.connect(lambda on: self.win.web.set_lan(bool(on)))
        form.addRow(self.web_lan)
        self.web_port = QSpinBox()
        self.web_port.setRange(1, 65535)
        self.web_port.setKeyboardTracking(False)
        self.web_port.valueChanged.connect(lambda val: self.win.web.set_port(int(val)))
        form.addRow(W["port"], self.web_port)
        self.web_state = QLabel("")
        self.web_state.setWordWrap(True)
        form.addRow(self.web_state)
        row = QHBoxLayout()
        self.web_show = QPushButton(W["show_connect"])
        self.web_show.setObjectName("small")
        self.web_show.clicked.connect(self.win.web.show_connect)
        self.web_regen = QPushButton(W["regenerate"])
        self.web_regen.setObjectName("small")
        self.web_regen.clicked.connect(self.win.web.regenerate)
        row.addWidget(self.web_show)
        row.addWidget(self.web_regen)
        row.addStretch(1)
        row.addWidget(_reset_button(self._reset_web))
        form.addRow(row)
        form.addRow(_dim(W["note"]))
        v.addWidget(g)
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
        self.web_state.setText(v["text"])
        _restyle(self.web_state, "warn" if v["warn"] else "")

    # ---------------------------------------------------------------- rtl_tcp

    def _build_rtltcp(self) -> QWidget:
        S = texts.SETTINGS_LABELS
        page = QWidget()
        v = QVBoxLayout(page)
        g = QGroupBox(S["rtltcp_group"])
        form = QFormLayout(g)
        self.enable = QCheckBox(S["rtltcp_enable"])
        self.enable.toggled.connect(lambda on: self.win.call("rtltcp_set", enabled=bool(on)))
        form.addRow(self.enable)
        self.port = QSpinBox()
        self.port.setRange(1, 65535)
        self.port.setKeyboardTracking(False)
        self.port.valueChanged.connect(lambda val: self.win.call("rtltcp_set", port=int(val)))
        form.addRow(S["rtltcp_port"], self.port)
        self.bind = QComboBox()
        for code, name in texts.RTLTCP_BINDS.items():
            self.bind.addItem(name, code)
        self.bind.activated.connect(lambda _i: self.win.call("rtltcp_set", bind=self.bind.currentData()))
        form.addRow(S["rtltcp_bind"], self.bind)
        self.rt_state = QLabel("")
        self.rt_state.setWordWrap(True)
        form.addRow(self.rt_state)
        self.rt_error = QLabel("")
        self.rt_error.setWordWrap(True)
        self.rt_error.setObjectName("warn")
        form.addRow(self.rt_error)
        row = QHBoxLayout()
        reset = QPushButton(S["rtltcp_default"])
        reset.setObjectName("small")
        reset.clicked.connect(lambda: self.win.call("rtltcp_set", enabled=False, port=DEFAULT_PORT, bind="local"))
        row.addWidget(reset)
        row.addStretch(1)
        form.addRow(row)
        form.addRow(_dim(S["rtltcp_note"]))
        v.addWidget(g)
        v.addStretch(1)
        return page

    # ---------------------------------------------------------------- RTL-SDR の DLL

    def _build_rtlsdr(self) -> QWidget:
        S = texts.SETTINGS_LABELS
        page = QWidget()
        v = QVBoxLayout(page)
        g = QGroupBox(S["rtlsdr_group"])
        form = QFormLayout(g)
        self.dll_state = QLabel("")
        self.dll_folder = QLabel("")
        self.dll_folder.setWordWrap(True)
        form.addRow(self.dll_state)
        form.addRow(self.dll_folder)
        # 何を選ぶフォルダかを選ぶボタンのそばに書く（32 ビット版の DLL は読めない）
        self.dll_help = _dim(S["rtlsdr_choose_note"])
        form.addRow(self.dll_help)
        row = QHBoxLayout()
        choose = QPushButton(S["rtlsdr_choose"])
        choose.setObjectName("small")
        choose.clicked.connect(self._choose_dll)
        default = QPushButton(S["rtlsdr_default"])
        default.setObjectName("small")
        default.clicked.connect(lambda: self.win.call("rtlsdr_set", dll_folder=None))
        row.addWidget(choose)
        row.addWidget(default)
        row.addStretch(1)
        form.addRow(row)
        v.addWidget(g)
        v.addStretch(1)
        return page

    def _choose_dll(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, texts.SETTINGS_LABELS["rtlsdr_choose_note"])
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
        S = texts.SETTINGS_LABELS
        self.dll_state.setText(S["rtlsdr_found"] if rtl["dll_found"] else S["rtlsdr_missing"])
        _restyle(self.dll_state, "" if rtl["dll_found"] else "warn")
        self.dll_folder.setText(rtl["dll_text"])
        self._apply_known(st)
        self.palette_page.apply(st)
