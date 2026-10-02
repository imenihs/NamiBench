"""設定の画面の「キー操作」（設計J §4.6）。

- 命令ごとに1行（命令の名前・いまのキー・初期値）。行を選んでキーを押すと、そのキーに割り当てる
- ぶつかるキーはその場で知らせて聞く（黙って上書きしない: M-02）。割り当てられないキーは理由を出す
- 「初期値に戻す」は1行ごとと全部。全部を戻すときは、変えた数を言ってから聞く
- 割り当てはこの画面（Qt）の覚え書き（設計O §1.1）。変えたらメニューの項目の横とツールチップも変わる
  （main_window.apply_keymap）
- 決まりそのもの（ぶつかる・割り当てられない・初期値）は keymap.py
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from . import keymap, texts

if TYPE_CHECKING:
    from .main_window import MainWindow

S = texts.SETTINGS_TEXTS
_MODIFIER_KEYS = {Qt.Key.Key_Control, Qt.Key.Key_Shift, Qt.Key.Key_Alt, Qt.Key.Key_Meta, Qt.Key.Key_AltGr}


class KeyCaptureDialog(QDialog):
    """押したキーを1つ受け取る窓。アプリのキー（F5 など）を動かさずに受け取る。Esc でやめる。"""

    def __init__(self, parent: QWidget, title: str) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        lay = QVBoxLayout(self)
        lab = QLabel(S["keys_waiting"])
        lab.setWordWrap(True)
        lay.addWidget(lab)
        self.key: Optional[str] = None

    def event(self, e) -> bool:
        if e.type() == QEvent.Type.ShortcutOverride:
            e.accept()  # メニューのキーより先にこの窓で受ける
            return True
        return super().event(e)

    def keyPressEvent(self, e) -> None:
        k = e.key()
        if k == Qt.Key.Key_Escape and e.modifiers() == Qt.KeyboardModifier.NoModifier:
            self.reject()
            return
        if k in _MODIFIER_KEYS:
            return
        self.key = QKeySequence(e.keyCombination()).toString(QKeySequence.SequenceFormat.PortableText)
        self.accept()


class KeysPage(QWidget):
    def __init__(self, win: "MainWindow") -> None:
        super().__init__()
        self.win = win
        lay = QVBoxLayout(self)
        note = QLabel(S["keys_note"] + "\n" + S["keys_shift_note"])
        note.setObjectName("dim")
        note.setWordWrap(True)
        lay.addWidget(note)
        self.codes = keymap.commands()
        self.table = QTableWidget(len(self.codes), 3)
        self.table.setHorizontalHeaderLabels([S["keys_col_command"], S["keys_col_key"], S["keys_col_default"]])
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.table.doubleClicked.connect(lambda _i: self.assign())
        lay.addWidget(self.table, 1)
        row = QHBoxLayout()
        self.btn_assign = QPushButton(S["keys_assign"])
        self.btn_assign.clicked.connect(self.assign)
        self.btn_clear = QPushButton(S["keys_clear"])
        self.btn_clear.clicked.connect(self.clear_key)
        self.btn_reset = QPushButton(S["keys_reset_row"])
        self.btn_reset.clicked.connect(self.reset_row)
        self.btn_reset_all = QPushButton(S["keys_reset_all"])
        self.btn_reset_all.clicked.connect(self.reset_all)
        for b in (self.btn_assign, self.btn_clear, self.btn_reset, self.btn_reset_all):
            b.setObjectName("small")
            row.addWidget(b)
        row.addStretch(1)
        lay.addLayout(row)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        lay.addWidget(self.status)
        self.refresh()
        self.table.selectRow(0)

    # ---------------------------------------------------------------- 表

    def overrides(self) -> dict[str, str]:
        return keymap.load_overrides(self.win.prefs.get("keys"))

    def refresh(self) -> None:
        ov = self.overrides()
        km = keymap.effective(ov)
        for i, code in enumerate(self.codes):
            name = keymap.command_name(code)
            if code in texts.GRAPH_ONLY_KEYS:
                name += texts.tpl("paren", text=S["keys_graph_only"])
            changed = code in ov
            items = (name, keymap.key_text(km[code]), keymap.key_text(texts.QT_KEY_DEFAULTS.get(code, "")))
            for col, text in enumerate(items):
                it = self.table.item(i, col)
                if it is None:
                    it = QTableWidgetItem("")
                    self.table.setItem(i, col, it)
                it.setText(("● " if changed and col == 1 else "") + text)
                it.setToolTip(S["keys_changed_tip"] if changed else "")

    def current_code(self) -> Optional[str]:
        r = self.table.currentRow()
        return self.codes[r] if 0 <= r < len(self.codes) else None

    def select(self, code: str) -> None:
        self.table.selectRow(self.codes.index(code))

    # ---------------------------------------------------------------- 操作

    def _save(self, ov: dict[str, str]) -> None:
        self.win.prefs.set("keys", keymap.dump_overrides(ov))
        self.win.apply_keymap()
        self.refresh()

    def assign(self) -> None:
        code = self.current_code()
        if code is None:
            return
        key = self.win.ask_key(keymap.command_name(code))
        if not key:
            return
        self.set_key(code, key)

    def set_key(self, code: str, key: str) -> bool:
        """code に key を付ける。割り当てられないキー・ぶつかって聞いて断られたら False。"""
        why = keymap.reserved_reason(key)
        if why:
            self._say(S["keys_reserved"].replace("{key}", key).replace("{why}", why), warn=True)
            return False
        ov = self.overrides()
        other = keymap.conflict(keymap.effective(ov), code, key)
        if other is not None:
            q = S["keys_conflict"].replace("{key}", key).replace("{other}", keymap.command_name(other)) \
                .replace("{command}", keymap.command_name(code))
            if not self.win.confirm(S["keys_conflict_title"], q, texts.LABELS["do_reassign"]):
                return False
        self._save(keymap.assign(ov, code, key, take_from=other))
        self._say(S["keys_assigned"].replace("{command}", keymap.command_name(code)).replace("{key}", key))
        return True

    def clear_key(self) -> None:
        code = self.current_code()
        if code is not None:
            self._save(keymap.assign(self.overrides(), code, ""))

    def reset_row(self) -> None:
        code = self.current_code()
        if code is not None:
            ov = self.overrides()
            ov.pop(code, None)
            default = texts.QT_KEY_DEFAULTS.get(code, "")
            other = keymap.conflict(keymap.effective(ov), code, default) if default else None
            if other is not None:
                # 初期値のキーをほかの命令に付け替えていたら、そちらから外す（黙って2つに付けない）
                q = S["keys_conflict"].replace("{key}", default).replace("{other}", keymap.command_name(other)) \
                    .replace("{command}", keymap.command_name(code))
                if not self.win.confirm(S["keys_conflict_title"], q, texts.LABELS["do_reassign"]):
                    return
                ov = keymap.assign(ov, other, "")
            self._save(ov)

    def reset_all(self) -> None:
        n = len(keymap.changed(self.overrides()))
        if n == 0:
            return
        if not self.win.confirm(S["keys_reset_all_title"], S["keys_reset_all_confirm"].replace("{n}", str(n)),
                                texts.LABELS["do_reset_all"]):
            return
        self._save({})

    def _say(self, text: str, warn: bool = False) -> None:
        self.status.setText(text)
        self.status.setObjectName("warn" if warn else "")
        self.status.style().unpolish(self.status)
        self.status.style().polish(self.status)
