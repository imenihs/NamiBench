"""プリセットの窓（設計O §1.2。メニュー「ファイル → プリセット…」）。

一覧（名前・中身の要約）と、呼び出す・名前を変える・消す・いまの状態を新しく保存・上書き。
呼び出しは1回の命令でいまの状態を置き換え、元に戻す（Ctrl+Z）で戻せる。中身はコアの presets フォルダ
（Qt と Web で共有）。一覧の行の文は screen_model.preset_rows（Web と同じ）。
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from . import screen_model, texts

if TYPE_CHECKING:
    from .main_window import MainWindow

P = texts.PRESET_TEXTS


def save_new(win: "MainWindow", after=None) -> None:
    """いまの状態を新しいプリセットに（名前を聞く）。"""
    name = win.ask_text(P["title"], P["name_prompt"])
    if not name:
        return

    def done(r: dict) -> None:
        win.status_note(P["saved"].replace("{name}", r.get("name", "")))
        if after:
            after()

    win.call("preset_save", on_ok=done, name=name)


class PresetsDialog(QDialog):
    def __init__(self, win: "MainWindow") -> None:
        super().__init__(win)
        self.win = win
        self.setWindowTitle(P["title"])
        lay = QVBoxLayout(self)
        note = QLabel(P["note"])
        note.setObjectName("dim")
        note.setWordWrap(True)
        lay.addWidget(note)
        self.table = QTableWidget(0, 2)
        self.table.horizontalHeader().hide()
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.doubleClicked.connect(lambda _i: self.load())
        lay.addWidget(self.table, 1)
        self.empty = QLabel(P["none"])
        self.empty.setObjectName("dim")
        lay.addWidget(self.empty)
        self.broken = QLabel("")
        self.broken.setObjectName("warn")
        self.broken.setWordWrap(True)
        lay.addWidget(self.broken)
        row = QHBoxLayout()
        self.btn_load = QPushButton(P["load"])
        self.btn_load.setToolTip(P["load_tip"])
        self.btn_load.clicked.connect(self.load)
        self.btn_rename = QPushButton(P["rename"])
        self.btn_rename.clicked.connect(self.rename)
        self.btn_delete = QPushButton(P["delete"])
        self.btn_delete.clicked.connect(self.delete)
        self.btn_overwrite = QPushButton(P["overwrite"])
        self.btn_overwrite.clicked.connect(self.overwrite)
        for b in (self.btn_load, self.btn_rename, self.btn_delete, self.btn_overwrite):
            row.addWidget(b)
        row.addStretch(1)
        lay.addLayout(row)
        self.btn_new = QPushButton(P["save_new"])
        self.btn_new.clicked.connect(lambda: save_new(win, self.reload))
        lay.addWidget(self.btn_new)
        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        btns.button(QDialogButtonBox.StandardButton.Close).setText(texts.SETTINGS_LABELS["close"])
        btns.rejected.connect(self.close)
        lay.addWidget(btns)
        self.rows: list[dict] = []
        self.resize(560, 420)
        self.table.itemSelectionChanged.connect(self._enable)
        self.reload()

    def reload(self) -> None:
        self.win.call("preset_list", on_ok=self._show)

    def _show(self, r: dict) -> None:
        self.rows = screen_model.preset_rows(r.get("presets") or [])
        self.table.setRowCount(len(self.rows))
        for i, p in enumerate(self.rows):
            self.table.setItem(i, 0, QTableWidgetItem(p["name"]))
            self.table.setItem(i, 1, QTableWidgetItem(p["summary"]))
        self.table.setVisible(bool(self.rows))
        self.empty.setVisible(not self.rows)
        n = int(r.get("broken") or 0)
        self.broken.setText(P["broken"].replace("{n}", str(n)) if n else "")
        self.broken.setVisible(bool(n))
        if self.rows:
            self.table.selectRow(0)
        self._enable()

    def _enable(self) -> None:
        on = self.current() is not None
        for b in (self.btn_load, self.btn_rename, self.btn_delete, self.btn_overwrite):
            b.setEnabled(on)

    def current(self) -> Optional[dict]:
        i = self.table.currentRow()
        return self.rows[i] if 0 <= i < len(self.rows) else None

    def select(self, name: str) -> None:
        for i, p in enumerate(self.rows):
            if p["name"] == name:
                self.table.selectRow(i)

    def load(self) -> None:
        p = self.current()
        if p is None:
            return
        self.win.call("preset_load", on_ok=lambda r: self.win.status_note(P["loaded"].replace("{name}", r["name"])),
                      id=p["id"])

    def rename(self) -> None:
        p = self.current()
        if p is None:
            return
        name = self.win.ask_text(P["title"], P["name_prompt"], p["name"])
        if name and name != p["name"]:
            self.win.call("preset_rename", on_ok=lambda _r: self.reload(), id=p["id"], name=name)

    def delete(self) -> None:
        p = self.current()
        if p is None or not self.win.confirm(P["title"], P["delete_confirm"].replace("{name}", p["name"]),
                                             texts.LABELS["do_delete"]):
            return
        self.win.call("preset_delete", on_ok=lambda _r: self.reload(), id=p["id"])

    def overwrite(self) -> None:
        p = self.current()
        if p is None or not self.win.confirm(P["title"], P["overwrite_confirm"].replace("{name}", p["name"]),
                                             texts.LABELS["do_overwrite"]):
            return
        self.win.call("preset_save", on_ok=lambda _r: self.reload(), name=p["name"], overwrite_id=p["id"])
