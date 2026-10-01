"""部分ごとに切り離せる並び（設計J §4.3.2）。

- マーカー表と右のパネルのタブ（トレース・掃引／マーカー／表示／信号源）は、ドッキングできる部品にする
  （元の窓に付ける・切り離して別の窓にする・元に戻す）。タブは1つずつ切り離せる（複数同時に）
- グラフは「大きく別の画面に出し、操作は元の画面で行う」形: 別の窓へ移し、元の場所には戻すボタンを置く
- ピークの一覧（マーカーのタブの中）も同じ形で別の窓へ出せる
- 別の窓が閉じられたら元の位置に戻す（なくならない）。ドッキングの部品は閉じるボタンを持たせず、
  切り離した窓を閉じたら元の位置へ戻す
- 並び（切り離した部分と位置）は画面ごとの覚え書き（設計O §1.1）に残し、次の起動で戻す。
  メニュー「表示 → 並びを初期に戻す」で初期の並びへ戻す（切り離した窓を見失ったとき）
- 切り離した窓でもキー（F5 など）が効くよう、メニューの項目（QAction）を窓にも付ける

上段とその下の注意は窓の幅いっぱいに置いたまま（狭い画面で折り返す: J §4.3.3）、その下に入れ子の
QMainWindow を置いて、そこで部分を並べる。
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Callable, Optional

from PySide6.QtCore import QByteArray, Qt
from PySide6.QtWidgets import (
    QDockWidget,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from . import texts

if TYPE_CHECKING:
    from .main_window import MainWindow

# マーカー表に使う高さの既定（グラフとの分割）と、右のパネルの幅の既定
TABLE_RATIO = 0.24
TABLE_MIN = 90
SIDE_WIDTH = 340
TAB_PARTS = ("tab-trace", "tab-marker", "tab-view", "tab-source")


class PartDock(QDockWidget):
    """閉じるボタンを持たないドッキングの部品。切り離した窓を閉じたら元の位置へ戻す。"""

    def __init__(self, key: str, title: str, body: QWidget) -> None:
        super().__init__(title)
        self.setObjectName("dock_" + key)  # 並びを残すときの名前
        self.key = key
        self.setWidget(body)
        self.setFeatures(QDockWidget.DockWidgetFeature.DockWidgetMovable
                         | QDockWidget.DockWidgetFeature.DockWidgetFloatable)
        self.setToolTip(texts.LAYOUT_TEXTS["float_tip"])

    def closeEvent(self, e) -> None:
        if self.isFloating():
            self.setFloating(False)
        e.ignore()


class _PartWindow(QWidget):
    """部分だけを出す別の窓。閉じたら元の位置へ戻す（on_close）。"""

    def __init__(self, title: str, on_close: Callable[[], None]) -> None:
        super().__init__(None, Qt.WindowType.Window)
        self.setWindowTitle(f"pyGigaSt — {title}")
        self._on_close = on_close
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(4, 4, 4, 4)

    def closeEvent(self, e) -> None:
        self._on_close()
        e.accept()


class Detachable:
    """1つの部品を別の窓へ出し入れする（グラフ・ピークの一覧）。元の場所には「ここに戻す」の札を置く。"""

    def __init__(self, win: "MainWindow", key: str, body: QWidget, title: str, note: str) -> None:
        self.win = win
        self.key = key
        self.body = body
        self.title = title
        self.holder = QWidget()
        lay = QVBoxLayout(self.holder)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(body, 1)
        self.placeholder = QWidget()
        row = QHBoxLayout(self.placeholder)
        row.setContentsMargins(8, 8, 8, 8)
        lab = QLabel(note)
        lab.setObjectName("dim")
        lab.setWordWrap(True)
        row.addWidget(lab, 1)
        back = QPushButton(texts.LABELS["reattach"])
        back.setObjectName("small")
        back.clicked.connect(self.attach)
        row.addWidget(back)
        lay.addWidget(self.placeholder)
        self.placeholder.hide()
        self.window: Optional[_PartWindow] = None

    @property
    def detached(self) -> bool:
        return self.window is not None

    def detach(self, geometry: Optional[QByteArray] = None) -> None:
        if self.window is not None:
            self.window.raise_()
            self.window.activateWindow()
            return
        w = _PartWindow(self.title, self.attach)
        w.setStyleSheet(self.win.styleSheet())
        w.lay.addWidget(self.body)
        self.body.show()
        # 別の窓でもキーが効くように、窓のキーの項目を付ける
        w.addActions(self.win.window_actions())
        self.placeholder.show()
        self.window = w
        if geometry is None or not w.restoreGeometry(geometry):
            w.resize(960, 640)
        w.show()

    def attach(self) -> None:
        w = self.window
        if w is None:
            return
        self.window = None
        self.holder.layout().insertWidget(0, self.body, 1)
        self.body.show()
        self.placeholder.hide()
        w._on_close = lambda: None
        w.close()
        w.deleteLater()

    def geometry(self) -> Optional[str]:
        return bytes(self.window.saveGeometry().toBase64()).decode("ascii") if self.window is not None else None


class Parts:
    """部分の並び。inner（入れ子の QMainWindow）を画面の中央に置く。"""

    def __init__(self, win: "MainWindow", graph: QWidget, markers: QWidget, tabs: dict[str, QWidget],
                 peaks: QWidget, peaks_parent_layout) -> None:
        self.win = win
        L = texts.LABELS
        self.inner = QMainWindow()
        self.inner.setWindowFlags(Qt.WindowType.Widget)
        self.inner.setDockNestingEnabled(True)
        self.inner.setTabPosition(Qt.DockWidgetArea.AllDockWidgetAreas, QTabWidget.TabPosition.North)
        self.graph = Detachable(win, "graph", graph, L["part_graph"], texts.LAYOUT_TEXTS["graph_detached"])
        self.inner.setCentralWidget(self.graph.holder)
        self.docks: dict[str, PartDock] = {"markers": PartDock("markers", L["part_markers"], markers)}
        for key, body in tabs.items():
            self.docks[key] = PartDock(key, L["part_" + key], body)
        # ピークの一覧は、マーカーのタブの中の元の位置に札ごと置く
        index = peaks_parent_layout.indexOf(peaks)
        peaks_parent_layout.removeWidget(peaks)
        self.peaks = Detachable(win, "peaks", peaks, L["part_peaks"], texts.LAYOUT_TEXTS["peaks_detached"])
        peaks_parent_layout.insertWidget(index, self.peaks.holder)
        for d in self.docks.values():
            d.addActions(win.window_actions())
        self._sized = False
        self.place_default()

    # ---------------------------------------------------------------- 並び

    def place_default(self) -> None:
        """初期の並び: マーカー表はグラフの下、タブは右に重ねて「トレース・掃引」を前に。切り離した部分は戻す。"""
        self.graph.attach()
        self.peaks.attach()
        for d in self.docks.values():
            d.setFloating(False)
            self.inner.removeDockWidget(d)
        self.inner.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self.docks["markers"])
        first = self.docks[TAB_PARTS[0]]
        self.inner.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, first)
        for key in TAB_PARTS[1:]:
            self.inner.tabifyDockWidget(first, self.docks[key])
        for d in self.docks.values():
            d.show()
        first.raise_()
        self.apply_sizes()

    def apply_sizes(self) -> None:
        h = max(self.inner.height(), 1)
        self.inner.resizeDocks([self.docks["markers"]], [max(TABLE_MIN, int(h * TABLE_RATIO))],
                               Qt.Orientation.Vertical)
        self.inner.resizeDocks([self.docks[TAB_PARTS[0]]], [SIDE_WIDTH], Qt.Orientation.Horizontal)

    def reset(self) -> None:
        self.place_default()
        self.win.status_note(texts.LAYOUT_TEXTS["layout_reset_done"])

    def detach(self, key: str) -> None:
        """部分を別の窓にする（メニュー「表示」から）。"""
        if key == "graph":
            self.graph.detach()
        elif key == "peaks":
            self.win.show_part("tab-marker")
            self.peaks.detach()
        elif key in self.docks:
            d = self.docks[key]
            d.show()
            d.setFloating(True)
            d.raise_()

    def show_part(self, key: str) -> None:
        d = self.docks.get(key)
        if d is not None:
            d.show()
            d.raise_()

    # ---------------------------------------------------------------- 残す・戻す（画面ごとの覚え書き: 設計O §1.1）

    def save(self) -> dict:
        return {"docks": bytes(self.inner.saveState().toBase64()).decode("ascii"),
                "graph": self.graph.geometry(), "peaks": self.peaks.geometry()}

    def restore(self, saved) -> bool:
        if not isinstance(saved, dict) or not isinstance(saved.get("docks"), str):
            return False
        ok = self.inner.restoreState(QByteArray.fromBase64(saved["docks"].encode("ascii")))
        for d in self.docks.values():
            if d.isHidden():
                d.show()  # 閉じる手段を持たせないので、隠れたまま戻らない部分を作らない
        for det, key in ((self.graph, "graph"), (self.peaks, "peaks")):
            g = saved.get(key)
            if isinstance(g, str) and g:
                det.detach(QByteArray.fromBase64(g.encode("ascii")))
        self._sized = bool(ok)
        return bool(ok)

    def first_show(self) -> None:
        """初めて画面に出たとき。残した並びが無ければ、窓の大きさに合わせた初期の割合にする。"""
        if not self._sized:
            self._sized = True
            self.apply_sizes()

    def close_windows(self) -> None:
        for det in (self.graph, self.peaks):
            if det.window is not None:
                det.window._on_close = lambda: None
                det.window.close()
