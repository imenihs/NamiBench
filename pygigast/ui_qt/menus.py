"""メニューバーとキー操作（設計J §4.3.1・§4.6）。

- メニューに全部の操作を並べ、キーは項目の横に出す（U-12: 画面に出ない操作を作らない）
- 上段のボタンは、ここで作った同じ項目（QAction）を呼ぶ（ボタンとメニューは同じ命令）
- まだ作っていない機能は、消さずに無効にして理由をツールチップと状態バーに出す（U-08）
- グラフにいるときだけのキー（P・[・]・←/→・M）は、グラフに付けてグラフにいるときだけ効かせる
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import QMainWindow, QMenu, QWidget

NOT_YET_FILE = "保存・読込・書き出しは次の回で実装します"
NOT_YET_PNG = "PNG の出力は次の回で実装します"
NOT_YET_MARKER = "マーカーの表と選択は次の回で実装します"
NOT_YET_CAL = "校正の画面は次の回で実装します"
NOT_YET_VIEW = "表示の切り替えは次の回で実装します"
NOT_YET_SETTINGS = "設定の画面は次の回で実装します"
NOT_YET_DIAG = "自己診断の結果を見る画面は次の回で実装します"


@dataclass
class Item:
    code: str
    label: str
    keys: tuple[str, ...] = ()
    handler: Optional[Callable[[], None]] = None
    disabled_reason: Optional[str] = None
    graph_only: bool = False  # グラフにいるときだけ効くキー


def build(win: QMainWindow, graph: QWidget, menus: list[tuple[str, list[Optional[Item]]]]) -> dict[str, QAction]:
    """メニューバーを作り、項目の QAction を code で引ける辞書にして返す。None は区切り線。"""
    actions: dict[str, QAction] = {}
    mb = win.menuBar()
    for title, items in menus:
        m: QMenu = mb.addMenu(title)
        # 無効の理由をツールチップで読めるようにする
        m.setToolTipsVisible(True)
        for it in items:
            if it is None:
                m.addSeparator()
                continue
            act = QAction(it.label, win)
            if it.keys:
                act.setShortcuts([QKeySequence(k) for k in it.keys])
            if it.graph_only:
                # グラフにいるときだけ効く。グラフにも付けないとキーが届かない
                act.setShortcutContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
                graph.addAction(act)
            else:
                act.setShortcutContext(Qt.ShortcutContext.WindowShortcut)
            if it.handler is not None:
                act.triggered.connect(lambda _=False, h=it.handler: h())
            set_reason(act, it.disabled_reason if it.handler is not None else (it.disabled_reason or "次の回で実装します"))
            m.addAction(act)
            actions[it.code] = act
    return actions


def set_reason(act: QAction, reason: Optional[str]) -> None:
    """使えない理由を付けて無効にする（None で有効に戻す）。理由はツールチップと状態バーに出す。"""
    act.setEnabled(reason is None)
    tip = act.text().replace("&", "")
    keys = shortcut_text(act)
    if keys:
        tip += f"（{keys}）"
    if reason:
        tip += f" — {reason}"
    act.setToolTip(tip)
    act.setStatusTip(reason or "")


def shortcut_text(act: QAction) -> str:
    return "・".join(s.toString(QKeySequence.SequenceFormat.NativeText) for s in act.shortcuts())


def tooltip_for(act: QAction, what: str) -> str:
    """ボタンのツールチップ。メニューと同じキーを出す（U-12）。"""
    keys = shortcut_text(act)
    return f"{what}（{keys}）" if keys else what


def key_table(actions: dict[str, QAction]) -> list[tuple[str, str]]:
    """キー操作の一覧（ヘルプに出す）。キーの付いた項目だけ。"""
    rows = []
    for act in actions.values():
        keys = shortcut_text(act)
        if keys:
            rows.append((keys, act.text().replace("&", "")))
    return rows
