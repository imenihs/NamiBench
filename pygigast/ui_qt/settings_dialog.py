"""アプリ設定の画面（設計O §1.3。メニュー「設定 → アプリ設定…」から開く別の画面: M-10）。

いまあるのは rtl_tcp の送出（設計Q §4.3・§6.7）と RTL-SDR の DLL のフォルダ（設計Q §6.2）。
変えたらすぐ効く（「適用」を置かない）。状態はコアが正本で、開いている間は state を映し続ける。
"""
from __future__ import annotations

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
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)

from . import screen_model, texts

if TYPE_CHECKING:
    from .main_window import MainWindow

DEFAULT_PORT = 1234


class SettingsDialog(QDialog):
    def __init__(self, win: "MainWindow") -> None:
        super().__init__(win)
        S = texts.SETTINGS_LABELS
        self.win = win
        self.setWindowTitle(S["title"])
        lay = QVBoxLayout(self)
        note = QLabel(S["note"])
        note.setObjectName("dim")
        lay.addWidget(note)

        # ---- rtl_tcp
        g = QGroupBox(S["rtltcp_group"])
        form = QFormLayout(g)
        self.enable = QCheckBox(S["rtltcp_enable"])
        self.enable.toggled.connect(lambda on: win.call("rtltcp_set", enabled=bool(on)))
        form.addRow(self.enable)
        self.port = QSpinBox()
        self.port.setRange(1, 65535)
        self.port.setKeyboardTracking(False)
        self.port.valueChanged.connect(lambda v: win.call("rtltcp_set", port=int(v)))
        form.addRow(S["rtltcp_port"], self.port)
        self.bind = QComboBox()
        for code, name in texts.RTLTCP_BINDS.items():
            self.bind.addItem(name, code)
        self.bind.activated.connect(lambda _i: win.call("rtltcp_set", bind=self.bind.currentData()))
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
        reset.clicked.connect(lambda: win.call("rtltcp_set", enabled=False, port=DEFAULT_PORT, bind="local"))
        row.addWidget(reset)
        row.addStretch(1)
        form.addRow(row)
        rt_note = QLabel(S["rtltcp_note"])
        rt_note.setWordWrap(True)
        rt_note.setObjectName("dim")
        form.addRow(rt_note)
        lay.addWidget(g)

        # ---- RTL-SDR の DLL
        g = QGroupBox(S["rtlsdr_group"])
        form = QFormLayout(g)
        self.dll_state = QLabel("")
        self.dll_folder = QLabel("")
        self.dll_folder.setWordWrap(True)
        form.addRow(self.dll_state)
        form.addRow(self.dll_folder)
        row = QHBoxLayout()
        choose = QPushButton(S["rtlsdr_choose"])
        choose.setObjectName("small")
        choose.clicked.connect(self._choose_dll)
        default = QPushButton(S["rtlsdr_default"])
        default.setObjectName("small")
        default.clicked.connect(lambda: win.call("rtlsdr_set", dll_folder=None))
        row.addWidget(choose)
        row.addWidget(default)
        row.addStretch(1)
        form.addRow(row)
        lay.addWidget(g)

        g = QGroupBox(S["other_group"])
        v = QVBoxLayout(g)
        other = QLabel(S["other_note"])
        other.setWordWrap(True)
        other.setObjectName("dim")
        v.addWidget(other)
        lay.addWidget(g)

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        btns.button(QDialogButtonBox.StandardButton.Close).setText(S["close"])
        btns.rejected.connect(self.close)
        lay.addWidget(btns)
        self.resize(560, 520)
        self.apply(win.state)

    def apply(self, st: dict) -> None:
        if not st:
            return
        r = screen_model.rtltcp_view(st)
        for w, fn in ((self.enable, lambda: self.enable.setChecked(r["enabled"])),
                      (self.port, lambda: self.port.setValue(int(r["port"] or DEFAULT_PORT))),
                      (self.bind, lambda: self.bind.setCurrentIndex(max(0, self.bind.findData(r["bind"]))))):
            w.blockSignals(True)
            fn()
            w.blockSignals(False)
        self.rt_state.setText(r["text"] + (f"\n{r['dropped_text']}" if r["dropped_text"] else ""))
        self.rt_error.setText(r["error_text"])
        self.rt_error.setVisible(bool(r["error_text"]))
        rtl = screen_model.rtl_view(st)
        S = texts.SETTINGS_LABELS
        self.dll_state.setText(S["rtlsdr_found"] if rtl["dll_found"] else S["rtlsdr_missing"])
        self.dll_state.setObjectName("" if rtl["dll_found"] else "warn")
        self.dll_state.style().unpolish(self.dll_state)
        self.dll_state.style().polish(self.dll_state)
        self.dll_folder.setText(rtl["dll_text"])

    def _choose_dll(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, texts.SETTINGS_LABELS["rtlsdr_group"])
        if folder:
            self.win.call("rtlsdr_set", dll_folder=folder)
