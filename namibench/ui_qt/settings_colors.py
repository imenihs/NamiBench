"""設定の画面の「配色」（設計J §4.4・O §1.3）。

- 色を1つずつ変える。変えた結果はその場で画面に出る（コアの配色が変わり、状態で全部の画面へ届く）
- 初期値から変えた色に印（●）。行ごとの「初期値に戻す」
- ライト・ダークのボタンは初期値に戻すボタン。変えた色があるときだけ、何個失われるかを言ってから聞く
- 文字と背景のコントラスト比 7 未満・見分けにくいトレースの組は、変えた色の行に出す（止めはしない: U-25）
- 使い方の説明は画面に書かない（U-24・U-26）
- 中身（行・知らせの文）は screen_model.palette_view（Web と同じ）
- 上に「保存した配色」の一覧（名前を付けて保存・読み込み・書き出し・取り込み: J §4.4.1。settings_palettes.py）
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QWidget,
)

from . import screen_model, sizes, texts, widget_sync
from .settings_palettes import SavedPalettes

if TYPE_CHECKING:
    from .main_window import MainWindow

S = texts.SETTINGS_TEXTS
COL_MARK, COL_NAME, COL_SWATCH, COL_HEX, COL_RESET, COL_WARN = range(6)


class PalettePage(QWidget):
    def __init__(self, win: "MainWindow") -> None:
        super().__init__()
        self.win = win
        page = sizes.vbox(self)
        page.setSpacing(sizes.SECTION_GAP)  # 保存した配色と、いまの配色の編集は別のまとまり（S-04）
        self.saved = SavedPalettes(win)
        page.addWidget(self.saved)
        lay = sizes.vbox(inset=False)
        page.addLayout(lay, 1)
        self.light = QPushButton(S["palette_reset_light"])
        self.light.clicked.connect(lambda: win.reset_palette("light"))
        self.dark = QPushButton(S["palette_reset_dark"])
        self.dark.clicked.connect(lambda: win.reset_palette("dark"))
        lay.addLayout(sizes.buttons_row(self.light, self.dark))
        self.table = QTableWidget(0, 6)
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().hide()
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(COL_WARN, QHeaderView.ResizeMode.Stretch)
        lay.addWidget(self.table, 1)
        self._rows: dict[str, dict] = {}
        self._key: tuple = ()

    def _build_rows(self, rows: list[dict]) -> None:
        self.table.setRowCount(len(rows))
        self._rows = {}
        for i, r in enumerate(rows):
            name = r["name"]
            mark = QTableWidgetItem("")
            self.table.setItem(i, COL_MARK, mark)
            self.table.setItem(i, COL_NAME, QTableWidgetItem(r["label"]))
            swatch = QPushButton("")
            swatch.setToolTip(S["palette_pick_title"].replace("{name}", r["label"]))
            swatch.clicked.connect(lambda _=False, n=name: self._pick(n))
            self.table.setCellWidget(i, COL_SWATCH, swatch)
            edit = QLineEdit("")
            edit.setPlaceholderText(S["palette_hex"])
            sizes.set_width(edit, "color")
            edit.editingFinished.connect(lambda n=name, e=edit: self._typed(n, e))
            self.table.setCellWidget(i, COL_HEX, edit)
            reset = QPushButton(S["reset"])
            reset.setToolTip(S["palette_row_reset_tip"])
            reset.clicked.connect(lambda _=False, n=name: self._reset_row(n))
            self.table.setCellWidget(i, COL_RESET, reset)
            warn = QTableWidgetItem("")
            self.table.setItem(i, COL_WARN, warn)
            self._rows[name] = {"mark": mark, "swatch": swatch, "edit": edit, "reset": reset, "warn": warn}

    def apply(self, st: dict) -> None:
        self.saved.apply(st)
        v = screen_model.palette_view(st)
        key = tuple(r["name"] for r in v["rows"])
        if key != self._key:
            self._key = key
            self._build_rows(v["rows"])
        self._view = v
        warn_color = QColor(self.win.pal["warn"])
        for r in v["rows"]:
            w = self._rows[r["name"]]
            w["mark"].setText(S["palette_changed_mark"] if r["changed"] else "")
            w["mark"].setToolTip(r["tip"])
            w["swatch"].setStyleSheet(f"background: {r['color']}; border: 1px solid {self.win.pal['axis']};")
            widget_sync.set_text(w["edit"], r["color"])  # 打っている間は書き直さない
            w["reset"].setEnabled(r["changed"])
            w["warn"].setText(r["warn"])
            w["warn"].setToolTip(r["warn_tip"])
            w["warn"].setForeground(warn_color)

    @property
    def warnings(self) -> dict[str, str]:
        """行ごとの読みやすさの知らせ（試験用: 色の名前 → 文。無い行は入れない）。"""
        return {n: w["warn"].text() for n, w in self._rows.items() if w["warn"].text()}

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
