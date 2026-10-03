"""設定の画面の「配色」の上の「保存した配色」（独自の配色の保存と読み込み: 設計J §4.4.1）。

プリセットの窓（presets_dialog.py）と同じ形の一覧: 名前（と読みやすさの知らせ）の表、選んだ配色への操作の行
（読み込む・名前…・消す…・いまの配色で上書き…・ファイルに書き出す…）、新しく作る行（いまの配色に名前を付けて
保存…・ファイルから取り込む…）。中身はコアの palettes フォルダ（Qt と Web で共有）。行の文は screen_model.palette_rows
（Web と同じ）。
- 読み込むと、いまの配色をその配色に置き換える。保存していない変更があるときだけ、何個消えるかを言ってから聞く（U-18）。
  元に戻す（Ctrl+Z）でも戻せる
- 書き出し・取り込みは JSON のファイル（別の PC・人に渡せる）。コアとは中身の文字でやりとりする（パスを渡さない:
  Web と同じ命令）。取り込んだ配色に読みにくい組があれば、その行と知らせにその場で出す
- 一覧はコアの状態に入っていないので、開いたとき・操作の後・いまの配色を始めた配色が変わったとき（ほかの画面で
  保存・読み込みしたとき）に読み直す
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Optional

from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QGroupBox,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
)

from ..core.palette import PaletteStore
from . import screen_model, sizes, texts

if TYPE_CHECKING:
    from .main_window import MainWindow

S = texts.SETTINGS_TEXTS
# 一覧に見せておく行の数（それより多ければ表の中でスクロール。下の色の表に場所を残す）
VISIBLE_ROWS = 4


class SavedPalettes(QGroupBox):
    def __init__(self, win: "MainWindow") -> None:
        super().__init__(S["palettes_group"])
        self.win = win
        lay = sizes.vbox(self)
        self.table = QTableWidget(0, 2)
        self.table.horizontalHeader().hide()
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        hh.setStretchLastSection(True)
        self.table.doubleClicked.connect(lambda _i: self.load())
        self.table.itemSelectionChanged.connect(self._enable)
        lay.addWidget(self.table)
        self.empty = QLabel(S["palettes_none"])
        self.empty.setObjectName("dim")
        lay.addWidget(self.empty)
        self.broken = QLabel("")
        self.broken.setObjectName("warn")
        self.broken.setWordWrap(True)
        lay.addWidget(self.broken)
        for w in (self.table, self.empty, self.broken):
            w.hide()  # 一覧が届くまで出さない（_show が決める）
        self.btn_load = QPushButton(S["palette_load"])
        self.btn_load.setToolTip(S["palette_load_tip"])
        self.btn_load.clicked.connect(self.load)
        self.btn_rename = QPushButton(S["palette_rename"])
        self.btn_rename.clicked.connect(self.rename)
        self.btn_delete = QPushButton(S["palette_delete"])
        self.btn_delete.clicked.connect(self.delete)
        self.btn_overwrite = QPushButton(S["palette_overwrite"])
        self.btn_overwrite.clicked.connect(self.overwrite)
        self.btn_export = QPushButton(S["palette_export"])
        self.btn_export.clicked.connect(self.export)
        self.btn_new = QPushButton(S["palette_save_new"])
        self.btn_new.clicked.connect(self.save_new)
        self.btn_import = QPushButton(S["palette_import"])
        self.btn_import.clicked.connect(self.import_files)
        # 選んだ配色への操作の行と、新しく作る行（どちらも左から詰める: S-03。プリセットの窓と同じ並び）
        lay.addLayout(sizes.buttons_row(self.btn_load, self.btn_rename, self.btn_delete, self.btn_overwrite,
                                        self.btn_export))
        lay.addLayout(sizes.buttons_row(self.btn_new, self.btn_import))
        self.rows: list[dict] = []
        self._origin: Optional[str] = None
        self._want: Optional[str] = None  # 読み直した後に選ぶ配色（保存・取り込みした配色）
        self._enable()

    # ---------------------------------------------------------------- 一覧

    def reload(self, select: Optional[str] = None) -> None:
        if select is not None:
            self._want = select
        self.win.call("palette_list", on_ok=self._show)

    def _show(self, r: dict) -> None:
        keep = self.current()
        want = self._want or (keep["id"] if keep else None) or r.get("origin_id")
        self._want = None
        self.rows = screen_model.palette_rows(r)
        self.table.setRowCount(len(self.rows))
        warn = QColor(self.win.pal["warn"])
        for i, p in enumerate(self.rows):
            self.table.setItem(i, 0, QTableWidgetItem(p["name"]))
            w = QTableWidgetItem(p["warn"])
            w.setToolTip(p["warn_tip"])
            w.setForeground(warn)
            self.table.setItem(i, 1, w)
        # 空なら表を出さず「ありません」だけ（設定の「接続」の覚えた機器と同じ）。表の高さは行の数（VISIBLE_ROWS まで。
        # それより多ければ表の中でスクロール）。Web も同じ
        self.table.setFixedHeight(min(len(self.rows), VISIBLE_ROWS) * sizes.ROW_H + 2 * self.table.frameWidth())
        self.table.setVisible(bool(self.rows))
        self.empty.setVisible(not self.rows)
        n = int(r.get("broken") or 0)
        self.broken.setText(S["palettes_broken"].replace("{n}", str(n)) if n else "")
        self.broken.setVisible(bool(n))
        self.table.clearSelection()
        for i, p in enumerate(self.rows):
            if p["id"] == want:
                self.table.selectRow(i)
        self._enable()

    def apply(self, st: dict) -> None:
        """状態を映す。いまの配色を始めた配色が変わった（ほかの画面で保存・読み込みした）ら一覧を読み直す。"""
        origin = ((st.get("palette") or {}).get("origin") or {}).get("id")
        if origin != self._origin:
            self._origin = origin
            self.reload()

    def showEvent(self, ev) -> None:  # noqa: N802 — Qt の名前
        super().showEvent(ev)
        self.reload()

    def _enable(self) -> None:
        on = self.current() is not None
        for b in (self.btn_load, self.btn_rename, self.btn_delete, self.btn_overwrite, self.btn_export):
            b.setEnabled(on)

    def current(self) -> Optional[dict]:
        sel = self.table.selectionModel().selectedRows() if self.table.selectionModel() else []
        i = sel[0].row() if sel else -1
        return self.rows[i] if 0 <= i < len(self.rows) else None

    # ---------------------------------------------------------------- 操作

    def load(self) -> None:
        p = self.current()
        if p is None:
            return
        q = screen_model.palette_load_question(self.win.state, p["name"])
        if q is not None and not self.win.confirm(S["palette_load_title"], q, S["palette_load"]):
            return
        self.win.call("palette_load", id=p["id"],
                      on_ok=lambda r: self.win.status_note(S["palette_loaded"].replace("{name}", r["name"])))

    def save_new(self) -> None:
        name = self.win.ask_text(S["palettes_group"], S["palette_name_prompt"])
        if not name:
            return

        def done(r: dict) -> None:
            self.win.status_note(S["palette_saved"].replace("{name}", r.get("name", "")))
            self.reload(select=r.get("id"))

        self.win.call("palette_save", on_ok=done, name=name)

    def rename(self) -> None:
        p = self.current()
        if p is None:
            return
        name = self.win.ask_text(S["palettes_group"], S["palette_name_prompt"], p["name"])
        if name and name != p["name"]:
            self.win.call("palette_rename", on_ok=lambda _r: self.reload(select=p["id"]), id=p["id"], name=name)

    def delete(self) -> None:
        p = self.current()
        if p is None or not self.win.confirm(S["palettes_group"], S["palette_delete_confirm"].replace("{name}", p["name"]),
                                             texts.LABELS["do_delete"]):
            return
        self.win.call("palette_delete", on_ok=lambda _r: self.reload(), id=p["id"])

    def overwrite(self) -> None:
        p = self.current()
        if p is None or not self.win.confirm(
                S["palettes_group"], S["palette_overwrite_confirm"].replace("{name}", p["name"]),
                texts.LABELS["do_overwrite"]):
            return

        def done(r: dict) -> None:
            self.win.status_note(S["palette_saved"].replace("{name}", r.get("name", "")))
            self.reload(select=p["id"])

        self.win.call("palette_save", on_ok=done, name=p["name"], overwrite_id=p["id"])

    def export(self) -> None:
        """選んだ配色を JSON のファイルに書き出す（場所は保存・開くと同じ最初のフォルダ）。"""
        p = self.current()
        if p is None:
            return
        files = self.win.files
        path = self.win.ask_save_path(S["palette_export_title"], str(files.last_dir() / PaletteStore.file_name(p["name"])),
                                      S["palette_file_filter"])
        if not path:
            return

        def done(r: dict) -> None:
            try:
                Path(path).write_text(r["text"], encoding="utf-8")
            except OSError as e:
                self.win.status_note(f"{texts.ERRORS['palette_unwritable']}: {e}")
                return
            files._remember(path)
            self.win.status_note(S["palette_exported"].replace("{name}", r["name"]).replace("{file}", str(path)),
                                 Path(path).parent)

        self.win.call("palette_export", on_ok=done, id=p["id"])

    def import_files(self) -> None:
        """JSON のファイルから配色を取り込み、一覧に足す（いまの配色は変えない）。"""
        files = self.win.files
        paths = self.win.ask_open_paths(S["palette_import_title"], str(files.last_dir()), S["palette_file_filter"])
        for path in paths or []:
            try:
                text = Path(path).read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                self.win.status_note(f"{Path(path).name}: {texts.ERRORS['palette_file_unreadable']}")
                continue
            files._remember(path)

            def done(r: dict) -> None:
                self.win.status_note(screen_model.palette_import_text(r))
                self.reload(select=r.get("id"))

            self.win.call("palette_import", on_ok=done, text=text, file_name=Path(path).name)
