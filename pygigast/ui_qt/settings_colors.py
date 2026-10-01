"""設定の画面の「表示（配色）」（設計J §4.4・O §1.3）。

- 色を1つずつ変える。変えた結果はその場で画面に出る（コアの配色が変わり、状態で全部の画面へ届く）
- 初期値から変えた色に印（●）。行ごとの「初期値に戻す」
- ライト・ダークのボタンは初期値に戻すボタン。変えた色があるときだけ、何個失われるかを言ってから聞く
- 文字と背景のコントラスト比 7 未満・見分けにくいトレースの組は、その場で知らせる（止めはしない）
- 中身（行・知らせの文）は screen_model.palette_view（Web と同じ）
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from . import screen_model, texts, widget_sync

if TYPE_CHECKING:
    from .main_window import MainWindow

S = texts.SETTINGS_TEXTS


class PalettePage(QWidget):
    def __init__(self, win: "MainWindow") -> None:
        super().__init__()
        self.win = win
        lay = QVBoxLayout(self)
        self.base_label = QLabel("")
        self.base_label.setWordWrap(True)
        lay.addWidget(self.base_label)
        row = QHBoxLayout()
        self.light = QPushButton(S["palette_reset_light"])
        self.light.clicked.connect(lambda: win.reset_palette("light"))
        self.dark = QPushButton(S["palette_reset_dark"])
        self.dark.clicked.connect(lambda: win.reset_palette("dark"))
        row.addWidget(self.light)
        row.addWidget(self.dark)
        row.addStretch(1)
        lay.addLayout(row)
        note = QLabel(S["palette_note"])
        note.setObjectName("dim")
        note.setWordWrap(True)
        lay.addWidget(note)
        self.table = QTableWidget(0, 5)
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().hide()
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        lay.addWidget(self.table, 1)
        self.warn = QLabel("")
        self.warn.setWordWrap(True)
        lay.addWidget(self.warn)
        self._rows: dict[str, dict] = {}
        self._key: tuple = ()

    def _build_rows(self, rows: list[dict]) -> None:
        self.table.setRowCount(len(rows))
        self._rows = {}
        for i, r in enumerate(rows):
            name = r["name"]
            mark = QTableWidgetItem("")
            self.table.setItem(i, 0, mark)
            self.table.setItem(i, 1, QTableWidgetItem(r["label"]))
            swatch = QPushButton("")
            swatch.setFixedWidth(44)
            swatch.setToolTip(S["palette_pick_title"].replace("{name}", r["label"]))
            swatch.clicked.connect(lambda _=False, n=name: self._pick(n))
            self.table.setCellWidget(i, 2, swatch)
            edit = QLineEdit("")
            edit.setPlaceholderText(S["palette_hex"])
            edit.setMaximumWidth(90)
            edit.editingFinished.connect(lambda n=name, e=edit: self._typed(n, e))
            self.table.setCellWidget(i, 3, edit)
            reset = QPushButton(S["reset"])
            reset.setObjectName("small")
            reset.setToolTip(S["palette_row_reset_tip"])
            reset.clicked.connect(lambda _=False, n=name: self._reset_row(n))
            self.table.setCellWidget(i, 4, reset)
            self._rows[name] = {"mark": mark, "swatch": swatch, "edit": edit, "reset": reset}

    def apply(self, st: dict) -> None:
        v = screen_model.palette_view(st)
        key = tuple(r["name"] for r in v["rows"])
        if key != self._key:
            self._key = key
            self._build_rows(v["rows"])
        self._view = v
        for r in v["rows"]:
            w = self._rows[r["name"]]
            w["mark"].setText(S["palette_changed_mark"] if r["changed"] else "")
            w["mark"].setToolTip(r["tip"])
            w["swatch"].setStyleSheet(f"background: {r['color']}; border: 1px solid {self.win.pal['axis']};")
            widget_sync.set_text(w["edit"], r["color"])  # 打っている間は書き直さない
            w["reset"].setEnabled(r["changed"])
        self.base_label.setText(v["base_text"])
        self.warn.setObjectName("warn" if v["warnings"] else "dim")
        self.warn.setText("\n".join(v["warnings"]) if v["warnings"] else v["ok_text"])
        self.warn.style().unpolish(self.warn)
        self.warn.style().polish(self.warn)

    def _row(self, name: str) -> dict:
        return next(r for r in self._view["rows"] if r["name"] == name)

    def _set(self, name: str, color: str) -> None:
        self.win.call("palette_set", colors={name: color})

    def _pick(self, name: str) -> None:
        r = self._row(name)
        c = self.win.ask_color(S["palette_pick_title"].replace("{name}", r["label"]), r["color"])
        if c:
            self._set(name, c)

    def _typed(self, name: str, edit: QLineEdit) -> None:
        text = edit.text().strip()
        if not text or text.lower() == self._row(name)["color"]:
            return
        if not QColor.isValidColorName(text) or not text.startswith("#"):
            self.win.status_note(texts.ERRORS["bad_color"])
            edit.setText(self._row(name)["color"])
            return
        self._set(name, QColor(text).name())

    def _reset_row(self, name: str) -> None:
        self._set(name, self._row(name)["default"])
