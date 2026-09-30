"""グラフの下のマーカー表とピーク・マーカーのボタン（設計J §4.3 案B・G §3.3・§3.4・§3.5）。

- 行: P・M のマーカー（親）と、その下の子の行（オフセットのカーソル・機能の結果）、XY カーソル
- 表の中でスクロールする（行が見切れたまま隠れない: J §4.3.2）
- 基準の欄: 行ごとにデルタの相手を選ぶ（なし／他の P・M）
- 行の「操作」: Center に・REF に・デルタの2点を Start・Stop に・消す（右クリックでも同じメニュー: G §3.4）
- 「コピー」: 表をタブ区切りで（子の行と機能の結果を含む）
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QMenu,
    QPushButton,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from . import marker_rows, menus
from .theme import number_font

if TYPE_CHECKING:
    from .main_window import MainWindow

COL_REF = 4
COL_OPS = len(marker_rows.COLUMNS)
COL_NOTE = COL_OPS - 1
ID_ROLE = Qt.ItemDataRole.UserRole


class MarkerPanel(QWidget):
    def __init__(self, win: "MainWindow") -> None:
        super().__init__()
        self.win = win
        self.rows: list[dict] = []
        self._items: dict[str, QTreeWidgetItem] = {}
        self._structure: tuple = ()
        self._ref_combos: dict[str, QComboBox] = {}

        bar = QHBoxLayout()
        bar.setContentsMargins(0, 0, 0, 0)
        bar.setSpacing(4)
        a = win.actions
        self.btn_peak = self._button("ピーク", a["marker_peak"], "選んでいるマーカーを最大の点へ（無ければ M を置く）")
        self.btn_left = self._button("◀ 次のピーク", a["marker_peak_left"], "左の次のピークへ")
        self.btn_right = self._button("次のピーク ▶", a["marker_peak_right"], "右の次のピークへ")
        self.btn_lower = self._button("次に高い", a["marker_peak_lower"], "いまより低いピークのうち最も高いものへ")
        self.btn_add = self._button("マーカー追加", a["marker_add"], "新しい M をピークに置く")
        self.btn_clear = self._button("全消去", a["marker_clear"], "マーカーを全部消す")
        self.btn_copy = self._button("コピー", a["marker_copy"], "表をタブ区切りでクリップボードへ")
        for b in (self.btn_peak, self.btn_left, self.btn_right, self.btn_lower, self.btn_add):
            bar.addWidget(b)
        bar.addStretch(1)
        bar.addWidget(self.btn_clear)
        bar.addWidget(self.btn_copy)

        self.tree = QTreeWidget()
        self.tree.setColumnCount(len(marker_rows.COLUMNS) + 1)
        self.tree.setHeaderLabels(list(marker_rows.COLUMNS) + ["操作"])
        self.tree.setRootIsDecorated(True)
        self.tree.setAlternatingRowColors(True)
        self.tree.setUniformRowHeights(True)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._context)
        self.tree.currentItemChanged.connect(self._current_changed)
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

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 2, 0, 0)
        lay.setSpacing(2)
        lay.addLayout(bar)
        lay.addWidget(self.tree, 1)

    def _button(self, text: str, act, what: str) -> QPushButton:
        b = QPushButton(text)
        b.setObjectName("small")
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
        texts_ = marker_rows.format_rows(rows, self.win.display_unit())
        for r, cells in zip(rows, texts_):
            it = self._items[r["id"]]
            for i, c in enumerate(cells):
                if i == COL_REF and r["id"] in self._ref_combos:
                    continue
                it.setText(i, c.strip() if i == 0 else c)
                if i == len(cells) - 1:
                    it.setToolTip(i, c)
        for mid, cb in self._ref_combos.items():
            row = next(r for r in rows if r["id"] == mid)
            want = [None] + [i for i in ids if i != mid]
            have = [cb.itemData(k) for k in range(cb.count())]
            cb.blockSignals(True)
            if want != have:
                cb.clear()
                for i in want:
                    cb.addItem("なし" if i is None else i, i)
            cb.setCurrentIndex(max(0, cb.findData(row.get("ref"))) if row.get("ref") else 0)
            cb.blockSignals(False)
        # 表の外に隠れた行も含めて、注意の列の幅を一番長い文字に合わせる（表は横にスクロールする）
        fm = self.tree.fontMetrics()
        widest = max([fm.horizontalAdvance(c[COL_NOTE]) for c in texts_] + [fm.horizontalAdvance("注意")])
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
                cb = QComboBox()
                cb.setToolTip("デルタの基準（この行に Δ周波数・Δ値を出す相手）")
                cb.activated.connect(lambda _i, mid=r["id"], c=cb: self._set_ref(mid, c.currentData()))
                self.tree.setItemWidget(it, COL_REF, cb)
                self._ref_combos[r["id"]] = cb
            ops = QToolButton()
            ops.setObjectName("small")
            ops.setText("操作")
            ops.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
            ops.setMenu(self._row_menu(r))
            self.tree.setItemWidget(it, COL_OPS, ops)
        self.tree.blockSignals(False)

    def select(self, mid: Optional[str]) -> None:
        it = self._items.get(mid) if mid else None
        cur = self.tree.currentItem()
        if it is cur:
            return
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
            for text, target, name in (("この周波数を Center に", "center", "row_center"),
                                       ("この値を REF レベルに", "ref", "row_ref"),
                                       ("デルタの2点を Start・Stop に", "span", "row_span")):
                act = m.addAction(text)
                act.setObjectName(name)
                act.triggered.connect(lambda _=False, t=target: self.win.marker_to(rid, t))
            m.addSeparator()
            act = m.addAction(f"{rid} を消す")
            act.setObjectName("row_remove")
            act.triggered.connect(lambda: self.win.call("marker_remove", id=rid))
        elif r.get("offset"):
            typ, idx = r["offset"]
            act = m.addAction("このカーソルを消す")
            act.setObjectName("row_remove")
            act.triggered.connect(lambda: self.win.call("marker_offset_remove", id=r["parent"], type=typ, index=idx))
        elif rid in ("X1", "X2", "Y1", "Y2"):
            key = {"X1": "x1_hz", "X2": "x2_hz", "Y1": "y1", "Y2": "y2"}[rid]
            act = m.addAction(f"{rid} を外す")
            act.setObjectName("row_remove")
            act.triggered.connect(lambda: self.win.call("set_xy", **{key: None}))
        else:
            act = m.addAction("マーカーの機能はマーカーのタブで変えます")
            act.setEnabled(False)
        return m

    def _context(self, pos) -> None:
        it = self.tree.itemAt(pos)
        if it is None:
            return
        rid = it.data(0, ID_ROLE)
        row = next((r for r in self.rows if r["id"] == rid), None)
        if row is not None:
            self._row_menu(row).exec(self.tree.viewport().mapToGlobal(pos))

    def copy_tsv(self) -> str:
        text = marker_rows.to_tsv(self.rows, self.win.display_unit())
        QGuiApplication.clipboard().setText(text)
        return text
