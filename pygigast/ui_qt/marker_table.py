"""グラフの下のマーカー表とピーク・マーカーのボタン（設計J §4.3 案B・G §3.3・§3.4・§3.5）。

- 行: P・M のマーカー（親）と、その下の子の行（オフセットのカーソル・機能の結果）、XY カーソル
- 表の中でスクロールする（行が見切れたまま隠れない: J §4.3.2）
- 基準の欄: 行ごとにデルタの相手を選ぶ（なし／他の P・M）
- 行の「操作」: Center に・REF に・デルタの2点を Start・Stop に・消す（右クリックでも同じメニュー: G §3.4）
- 「コピー」: 表をタブ区切りで（子の行と機能の結果を含む）
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Callable, Optional

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QHeaderView,
    QLineEdit,
    QMenu,
    QPushButton,
    QStyledItemDelegate,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QWidget,
)

from ..core.units import ParseError
from . import marker_rows, menus, sizes, texts, widget_sync
from .theme import number_font

if TYPE_CHECKING:
    from .main_window import MainWindow

COL_REF = 4
COL_OPS = len(marker_rows.COLUMNS)
COL_NOTE = COL_OPS - 1
ID_ROLE = Qt.ItemDataRole.UserRole


XY_KEYS = marker_rows.XY_KEYS
COL_FREQ, COL_VALUE, COL_DFREQ, COL_DVALUE = (marker_rows.COL_FREQ, marker_rows.COL_VALUE, marker_rows.COL_DFREQ,
                                             marker_rows.COL_DVALUE)


class _CellEdit(QLineEdit):
    """表のセルの上に重ねて打つ欄。Enter・Tab・欄を離れるで確定、Esc で取り消す（数値の欄と同じ: J §4.6）。"""

    def __init__(self, text: str, on_done: Callable[[Optional[str]], None]) -> None:
        super().__init__(text)
        self._on_done = on_done
        self._done = False
        self.editingFinished.connect(lambda: self._finish(self.text()))

    def keyPressEvent(self, e) -> None:
        if e.key() == Qt.Key.Key_Escape:
            self._finish(None)
            return
        super().keyPressEvent(e)

    def _finish(self, text: Optional[str]) -> None:
        if not self._done:
            self._done = True
            self._on_done(text)


class _RowHeight(QStyledItemDelegate):
    """行の高さを表の行の値にする（中に置く基準のドロップダウンと「操作」が標準の段の高さで収まる: J §4.11）。
    列の幅には左右に部品の間の余白を足す（右寄せの数の列どうしが詰まって「500 -76.05 dBm」が1つの値に見えた）。"""

    def sizeHint(self, option, index) -> QSize:
        s = super().sizeHint(option, index)
        return QSize(s.width() + 2 * sizes.GAP, max(s.height(), sizes.ROW_H))


class MarkerPanel(QWidget):
    def __init__(self, win: "MainWindow") -> None:
        super().__init__()
        self.win = win
        self.rows: list[dict] = []
        self._items: dict[str, QTreeWidgetItem] = {}
        self._structure: tuple = ()
        self._ref_combos: dict[str, QComboBox] = {}

        bar = sizes.hbox()
        a = win.actions
        L = texts.LABELS
        self.btn_peak = self._button(texts.DIRECTIONS["highest"], a["marker_peak"], L["btn_peak_tip"])
        self.btn_left = self._button(L["btn_left"], a["marker_peak_left"], L["btn_left_tip"])
        self.btn_right = self._button(L["btn_right"], a["marker_peak_right"], L["btn_right_tip"])
        self.btn_lower = self._button(L["btn_lower"], a["marker_peak_lower"], L["btn_lower_tip"])
        self.btn_add = self._button(L["btn_add"], a["marker_add"], L["btn_add_tip"])
        self.btn_clear = self._button(L["clear_all"], a["marker_clear"], texts.MENU_ITEMS["marker_clear"])
        self.btn_copy = self._button(L["copy_table"], a["marker_copy"], L["btn_copy_tip"])
        for b in (self.btn_peak, self.btn_left, self.btn_right, self.btn_lower, self.btn_add):
            bar.addWidget(b)
        bar.addStretch(1)
        bar.addWidget(self.btn_clear)
        bar.addWidget(self.btn_copy)

        self.tree = QTreeWidget()
        self.tree.setColumnCount(len(marker_rows.COLUMNS) + 1)
        self.tree.setHeaderLabels(list(marker_rows.COLUMNS) + [texts.LABELS["row_ops"]])
        self.tree.setRootIsDecorated(True)
        self.tree.setAlternatingRowColors(True)
        self.tree.setUniformRowHeights(True)
        self.tree.setItemDelegate(_RowHeight(self.tree))
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._context)
        self.tree.currentItemChanged.connect(self._current_changed)
        self.tree.itemDoubleClicked.connect(self._double_clicked)
        self.editing: Optional[_CellEdit] = None
        hdr = self.tree.header()
        hdr.setStretchLastSection(False)
        for i, right in enumerate(marker_rows.RIGHT):
            hdr.setSectionResizeMode(i, QHeaderView.ResizeMode.ResizeToContents)
            item = self.tree.headerItem()
            item.setTextAlignment(i, (Qt.AlignmentFlag.AlignRight if right else Qt.AlignmentFlag.AlignLeft)
                                  | Qt.AlignmentFlag.AlignVCenter)
        # 注意の列も中身の幅を取り、入らなければ表を横にスクロールする（文字を切らない: C-01）
        hdr.setSectionResizeMode(COL_NOTE, QHeaderView.ResizeMode.Interactive)
        hdr.setSectionResizeMode(COL_OPS, QHeaderView.ResizeMode.ResizeToContents)
        self.tree.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._num_font = number_font(self.tree.font())

        lay = sizes.vbox(self)
        lay.addLayout(bar)
        lay.addWidget(self.tree, 1)

    def _button(self, text: str, act, what: str) -> QPushButton:
        b = QPushButton(text)
        b.clicked.connect(act.trigger)
        b.setToolTip(menus.tooltip_for(act, what))
        return b

    # ---------------------------------------------------------------- 行

    def set_rows(self, rows: list[dict], selected: Optional[str]) -> None:
        self.rows = rows
        structure = tuple((r["id"], r.get("parent")) for r in rows)
        ids = [r["id"] for r in rows if r.get("kind") == "marker"]
        if structure != self._structure:
            self._rebuild(rows)
            self._structure = structure
        texts_ = marker_rows.format_rows(rows, self.win.display_unit(), texts.freq_offset_of(self.win.state))
        for r, cells in zip(rows, texts_):
            it = self._items[r["id"]]
            for i, c in enumerate(cells):
                if i == COL_REF and r["id"] in self._ref_combos:
                    continue
                it.setText(i, c.strip() if i == 0 else c)
                if i == len(cells) - 1:
                    it.setToolTip(i, c)
                elif self.cell_setter(r, i) is not None:
                    it.setToolTip(i, texts.LABELS["cell_edit_tip"])
        for mid, cb in self._ref_combos.items():
            row = next(r for r in rows if r["id"] == mid)
            want = [None] + [i for i in ids if i != mid]
            # 一覧を開いている間は組み替えない・選び直さない（閉じたときに最新の状態で入れる）
            widget_sync.set_items(cb, [(texts.LABELS["none"] if i is None else i, i) for i in want])
            widget_sync.set_data(cb, row.get("ref") if row.get("ref") else None)
        # 表の外に隠れた行も含めて、注意の列の幅を一番長い文字に合わせる（表は横にスクロールする）
        fm = self.tree.fontMetrics()
        widest = max([fm.horizontalAdvance(c[COL_NOTE]) for c in texts_] + [fm.horizontalAdvance(marker_rows.COLUMNS[COL_NOTE])])
        # 字形の置き換え（和文の書体）で測った幅より広く描かれることがあるので、1割の余白を足す
        self.tree.header().resizeSection(COL_NOTE, int(widest * 1.1) + 24)
        self.select(selected)

    def _rebuild(self, rows: list[dict]) -> None:
        self.tree.blockSignals(True)
        self.tree.clear()
        self._items = {}
        self._ref_combos = {}
        for r in rows:
            parent = self._items.get(r.get("parent")) if r.get("parent") else None
            it = QTreeWidgetItem(parent) if parent is not None else QTreeWidgetItem(self.tree)
            it.setData(0, ID_ROLE, r["id"])
            for i, right in enumerate(marker_rows.RIGHT):
                it.setTextAlignment(i, (Qt.AlignmentFlag.AlignRight if right else Qt.AlignmentFlag.AlignLeft)
                                    | Qt.AlignmentFlag.AlignVCenter)
                if marker_rows.NUMERIC[i]:
                    it.setFont(i, self._num_font)
            self._items[r["id"]] = it
        self.tree.expandAll()
        for r in rows:
            it = self._items[r["id"]]
            if r.get("kind") == "marker":
                cb = widget_sync.fit_combo(QComboBox())
                cb.setToolTip(texts.LABELS["ref_combo_tip"])
                cb.activated.connect(lambda _i, mid=r["id"], c=cb: self._set_ref(mid, c.currentData()))
                self.tree.setItemWidget(it, COL_REF, cb)
                self._ref_combos[r["id"]] = cb
            ops = QToolButton()
            ops.setText(texts.LABELS["row_ops"])
            ops.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
            ops.setMenu(self._row_menu(r))
            self.tree.setItemWidget(it, COL_OPS, ops)
        self.tree.blockSignals(False)

    def select(self, mid: Optional[str]) -> None:
        it = self._items.get(mid) if mid else None
        cur = self.tree.currentItem()
        if it is cur:
            return
        if it is not None and cur is not None and cur.parent() is it:
            return  # そのマーカーの子の行を選んでいる: 選んだ行のまま（Delete でその行だけを消すため）
        self.tree.blockSignals(True)
        if it is None:
            self.tree.setCurrentItem(None)
            self.tree.clearSelection()
        else:
            self.tree.setCurrentItem(it)
        self.tree.blockSignals(False)

    def _current_changed(self, cur: Optional[QTreeWidgetItem], _prev) -> None:
        if cur is None:
            return
        rid = cur.data(0, ID_ROLE)
        row = next((r for r in self.rows if r["id"] == rid), None)
        if row is None:
            return
        # 子の行を選んだら親のマーカーを選ぶ（キーの ←→・ピークは親を動かす）
        target = row.get("parent") or (rid if row.get("kind") == "marker" else None)
        if target:
            self.win.select_marker(target)

    def _set_ref(self, mid: str, ref: Optional[str]) -> None:
        self.win.call("marker_set", id=mid, ref=ref)

    # ---------------------------------------------------------------- 行の操作（G §3.4）

    def _row_menu(self, r: dict) -> QMenu:
        m = QMenu(self)
        rid = r["id"]
        if r.get("kind") == "marker":
            for text, target, name in ((texts.LABELS["row_center"], "center", "row_center"),
                                       (texts.LABELS["row_ref"], "ref", "row_ref"),
                                       (texts.MENU_ITEMS["marker_span"], "span", "row_span")):
                act = m.addAction(text)
                act.setObjectName(name)
                act.triggered.connect(lambda _=False, t=target: self.win.marker_to(rid, t))
            m.addSeparator()
            act = m.addAction(texts.w("row_remove", m=rid))
            act.setObjectName("row_remove")
            act.triggered.connect(lambda: self.win.call("marker_remove", id=rid))
        else:
            text = (texts.LABELS["row_remove_cursor"] if r.get("offset")
                    else texts.w("row_unset", m=rid) if rid in XY_KEYS else texts.LABELS["row_remove_function"])
            act = m.addAction(text)
            act.setObjectName("row_remove")
            act.triggered.connect(lambda: self.remove_row(r))
        return m

    def remove_row(self, r: dict) -> None:
        """その行だけを消す（決まりは marker_rows.remove_command: Web と同じ）。"""
        c = marker_rows.remove_command(r, self.win.marker_snap)
        if c is not None:
            self.win.call(c[0], **c[1])

    # ---------------------------------------------------------------- セルを直す（G §3.6）

    def cell_setter(self, r: dict, col: int) -> Optional[Callable[[str], None]]:
        """そのセルを打って直せるなら、打った文字を命令にして送る関数（決まりは marker_rows.edit_spec: Web と同じ）。"""
        spec = marker_rows.edit_spec(r, col, self.rows, self.win.marker_snap)
        if spec is None:
            return None

        def send(text: str) -> None:
            cmd, args = marker_rows.edit_command(spec, text, texts.freq_offset_of(self.win.state))
            self.win.call(cmd, **args)
        return send

    def _double_clicked(self, it: QTreeWidgetItem, col: int) -> None:
        rid = it.data(0, ID_ROLE)
        row = next((r for r in self.rows if r["id"] == rid), None)
        setter = self.cell_setter(row, col) if row is not None else None
        if setter is None:
            return

        def done(text: Optional[str]) -> None:
            # 打つ欄はこの関数の中で外す（確定の合図の途中で消さない）
            QTimer.singleShot(0, lambda: self.tree.removeItemWidget(it, col))
            if text is None or not text.strip():
                return
            try:
                setter(text)
            except ParseError:
                self.win.status_note(texts.LABELS["bad_value"])

        edit = _CellEdit(it.text(col).strip(), done)
        edit.setFont(it.font(col))
        self.tree.setItemWidget(it, col, edit)
        edit.setFocus()
        edit.selectAll()
        self.editing = edit

    def current_row(self) -> Optional[dict]:
        """表で選んでいる行（表にフォーカスがあるときだけ。無ければ None）。"""
        if not self.tree.hasFocus():
            return None
        it = self.tree.currentItem()
        if it is None:
            return None
        rid = it.data(0, ID_ROLE)
        return next((r for r in self.rows if r["id"] == rid), None)

    def _context(self, pos) -> None:
        it = self.tree.itemAt(pos)
        if it is None:
            return
        rid = it.data(0, ID_ROLE)
        row = next((r for r in self.rows if r["id"] == rid), None)
        if row is not None:
            self._row_menu(row).exec(self.tree.viewport().mapToGlobal(pos))

    def copy_tsv(self) -> str:
        text = marker_rows.to_tsv(self.rows, self.win.display_unit(), texts.freq_offset_of(self.win.state))
        QGuiApplication.clipboard().setText(text)
        return text
