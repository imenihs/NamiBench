"""画面の部品の大きさとそろいを数える（設計J §4.11・docs/uxui_rule.md S-01〜S-07）。試験は持たない。

見た目の崩れを人の目で1つずつ見つけない（2026-10-03 ユーザー指摘「高さがガタガタずれている。当たり前の品質を
壊していて、指摘を1つ1つしたくない」）。全部の欄・ドロップダウン・ボタンについて、次を数えて返す:

- 高さが段の値（上段 40px・標準 26px・状態バー 20px）でない
- 同じ行に並ぶ同じ段の部品の、上端か高さが 1px より違う（縦にずれている）
- ボタンの幅が文字の幅より狭い（文字が切れている）
- ドロップダウンの幅が一番長い選択肢より狭い
- 欄の幅が中の文字より狭い（値が切れている）
- 名前を左に置く形（QFormLayout）で、名前と欄の縦の真ん中が 2px より違う
- ボタンが文字の幅より広い（横に伸びている: S-03）
- 標準の段の入力欄の幅が、値の種類の幅（整数・値・名前・色）のどれでもない（S-02）
- 並び（QGridLayout）の同じ列の欄・ドロップダウンの幅か左端が 1px より違う（列がガタガタで、行を移るたびにマウスを
  横へ探しに行くことになる: 2026-10-03 ユーザー指摘）
"""
from __future__ import annotations

from PySide6.QtCore import QPoint, QRect
from PySide6.QtWidgets import (
    QAbstractButton,
    QAbstractSpinBox,
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGridLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QRadioButton,
    QScrollBar,
    QTabBar,
    QToolButton,
    QWidget,
)

from pygigast.ui_qt import sizes

TIER_H = {"L": sizes.H_L, "S": sizes.H_S, None: sizes.H_M}
# 値の種類の幅（S-02）。入力欄はこのどれか
KIND_WIDTHS = {sizes.width_px(k) for k in sizes.WIDTH_EM}


def _typed_field(w: QWidget) -> bool:
    """値の種類で幅を決める入力欄か（数を打つ欄・1行の文字の欄・dB の打てるドロップダウン）。

    一覧から選ぶだけのドロップダウンと、選択肢の幅で決める打てるドロップダウン（トレースの回数）は一番長い
    選択肢の幅なので除く（幅を決めて固定した打てるドロップダウンだけ数える）。伸ばしてよい部品（読むだけの長い文字）と、
    表のセルを直す欄も除く。
    """
    if w.property("grow") or w.property("column"):
        return False  # 伸ばしてよい部品・幅を列でそろえる部品（列のそろいは _grid_columns が数える）
    if isinstance(w, QComboBox):
        return w.isEditable() and w.minimumWidth() == w.maximumWidth()
    if not isinstance(w, (QLineEdit, QAbstractSpinBox)):
        return False
    p = w.parent()
    if isinstance(w, QLineEdit) and (isinstance(p, QComboBox) or (p is not None and p.objectName() == "qt_scrollarea_viewport")):
        return False
    return True


def _shown(w: QWidget, root: QWidget) -> bool:
    # 実際に出ている部品（重ねたタブの裏・隠したドックの中は数えない。スクロールで外に出ている部品は数える）
    return w.isVisible() and w.width() > 0 and w.height() > 0


def _area(w: QWidget):
    """部品が載っている場所（一番近いスクロールの中身か窓）。行のそろいは同じ場所の中だけで比べる。"""
    from PySide6.QtWidgets import QAbstractScrollArea

    p = w.parentWidget()
    while p is not None:
        if isinstance(p.parentWidget(), QAbstractScrollArea) or p.isWindow():
            return p
        p = p.parentWidget()
    return None


def _skip(w: QWidget) -> bool:
    """Qt が中で作る部品（タブの送り・ドックの題のボタン・スクロールバー・ドロップダウンや欄の中の部品）は数えない。"""
    if isinstance(w, QScrollBar) or w.objectName().startswith("qt_"):
        return True
    p = w.parent()
    while p is not None:
        if isinstance(p, (QTabBar, QComboBox, QAbstractSpinBox, QScrollBar)):
            return True
        p = p.parent()
    return isinstance(w, QToolButton) and isinstance(w.parent(), QTabBar)


def _tier(w: QWidget):
    t = w.property("tier")
    if t:
        return t
    # 上段の欄は名前と値を持つ部品の中（ValueField）。段の印は中の欄に付く
    return None


def controls(root: QWidget) -> list[QWidget]:
    kinds = (QLineEdit, QComboBox, QAbstractSpinBox, QPushButton, QToolButton)
    out = []
    for w in root.findChildren(QWidget):
        if isinstance(w, kinds) and not isinstance(w, (QCheckBox, QRadioButton)) and _shown(w, root) and not _skip(w):
            if isinstance(w, QLineEdit) and isinstance(w.parent(), (QComboBox, QAbstractSpinBox)):
                continue
            if w.objectName() == "noteAction" or w.window() is not root.window():
                continue
            out.append(w)
    return out


def _grect(w: QWidget) -> QRect:
    return QRect(w.mapToGlobal(QPoint(0, 0)), w.size())


def _name(w: QWidget) -> str:
    text = ""
    if isinstance(w, QAbstractButton):
        text = w.text()
    elif isinstance(w, QComboBox):
        text = w.currentText()
    elif isinstance(w, QLineEdit):
        text = w.text() or w.placeholderText()
    return f"{type(w).__name__}「{text[:20]}」"


def problems(root: QWidget, where: str = "") -> list[str]:
    out: list[str] = []
    ws = controls(root)
    for w in ws:
        h = TIER_H.get(_tier(w), sizes.H_M)
        if w.height() != h:
            out.append(f"{where}: {_name(w)} の高さ {w.height()}px（段の値 {h}px）")
        if isinstance(w, QPushButton) and w.text() and w.width() < w.sizeHint().width():
            out.append(f"{where}: {_name(w)} の幅 {w.width()}px が文字の幅 {w.sizeHint().width()}px より狭い")
        if isinstance(w, QComboBox) and not w.isEditable() and w.count():
            need = w.minimumSizeHint().width() if w.sizeAdjustPolicy() == QComboBox.SizeAdjustPolicy.AdjustToContents \
                else 0
            if need and w.width() < need:
                out.append(f"{where}: {_name(w)} の幅 {w.width()}px が一番長い選択肢 {need}px より狭い")
        if isinstance(w, QLineEdit) and w.text():
            fm = w.fontMetrics()
            m = w.textMargins()
            room = w.width() - 2 - 12 - 2 * sizes.EDIT_INNER - m.left() - m.right()
            if fm.horizontalAdvance(w.text()) > room + 1:
                out.append(f"{where}: {_name(w)} の文字 {fm.horizontalAdvance(w.text())}px が欄の中 {room}px に入らない")
        # ボタンは文字の幅（短い語は最小幅）。横に伸ばさない（S-03）
        if isinstance(w, QPushButton) and w.text() and not w.property("grow"):
            floor = sizes.RUN_MIN_W if w.property("run") else sizes.BTN_MIN_W
            want = max(floor, w.sizeHint().width())
            if w.width() > want + 1:
                out.append(f"{where}: {_name(w)} の幅 {w.width()}px が文字の幅 {want}px より広い（伸びている）")
        # 標準の段の入力欄の幅は、値の種類の幅のどれか（S-02）。伸ばしてよい部品・表のセルの欄・上段は除く
        if _typed_field(w) and _tier(w) is None and w.width() not in KIND_WIDTHS:
            out.append(f"{where}: {_name(w)} の幅 {w.width()}px が値の種類の幅 {sorted(KIND_WIDTHS)} のどれでもない")
    # 打てるドロップダウン: 中の欄が ▼ に重ならない（重なると ▼ を押しても一覧が開かない）・中の欄の文字が切れない
    for c in root.findChildren(QComboBox):
        if not (c.isEditable() and _shown(c, root) and c.lineEdit() is not None):
            continue
        le = c.lineEdit()
        if le.geometry().right() > c.width() - 12:
            out.append(f"{where}: {_name(c)} の中の欄（右端 {le.geometry().right()}）が ▼（幅 {c.width()}）に重なっている")
        if le.text():
            m = le.textMargins()
            room = le.width() - 2 * sizes.EDIT_INNER - m.left() - m.right()
            if le.fontMetrics().horizontalAdvance(le.text()) > room:
                out.append(f"{where}: {_name(c)} の文字 {le.fontMetrics().horizontalAdvance(le.text())}px が"
                           f"中の欄 {room}px に入らない")
    # 同じ行（縦に重なる）の同じ段の部品は、上端と高さがそろう
    rects = [(w, _grect(w), _tier(w), _area(w)) for w in ws]
    for i, (a, ra, ta, aa) in enumerate(rects):
        for b, rb, tb, ab in rects[i + 1:]:
            if ta != tb or aa is not ab:
                continue
            overlap = ra.top() < rb.bottom() and rb.top() < ra.bottom()
            if overlap and (abs(ra.top() - rb.top()) > 1 or abs(ra.height() - rb.height()) > 1):
                out.append(f"{where}: {_name(a)}（上端 {ra.top()}・高さ {ra.height()}）と {_name(b)}（上端 {rb.top()}・"
                           f"高さ {rb.height()}）が同じ行でずれている")
    # 1行の文字（名前・状態）とチェックボックス・ラジオは、同じ行の欄・ボタンと縦の真ん中がそろう
    singles = [w for w in root.findChildren(QWidget)
               if isinstance(w, (QLabel, QCheckBox, QRadioButton)) and _shown(w, root) and not _skip(w)
               and w.height() <= sizes.ROW_H and not (isinstance(w, QLabel) and (w.wordWrap() and "\n" in w.text()))
               and (not isinstance(w, QLabel) or w.text().strip())]
    for s in singles:
        rs, sa = _grect(s), _area(s)
        for w, rw, tw, aw in rects:
            if aw is not sa or not (rs.top() < rw.bottom() and rw.top() < rs.bottom()):
                continue
            if abs(rs.center().y() - rw.center().y()) > 2:
                text = s.text()[:16]
                out.append(f"{where}: {type(s).__name__}「{text}」の縦の真ん中が {_name(w)} と "
                           f"{rs.center().y() - rw.center().y()}px ずれている")
                break
    for s in singles:
        if isinstance(s, (QCheckBox, QRadioButton)) and s.height() != sizes.H_M:
            out.append(f"{where}: {type(s).__name__}「{s.text()[:16]}」の高さ {s.height()}px（段の値 {sizes.H_M}px）")
    out += _grid_columns(root, where)
    # 名前を左に置く形: 名前と欄の縦の真ん中
    for form in root.findChildren(QFormLayout):
        for r in range(form.rowCount()):
            li = form.itemAt(r, QFormLayout.ItemRole.LabelRole)
            fi = form.itemAt(r, QFormLayout.ItemRole.FieldRole)
            lab = li.widget() if li else None
            if not isinstance(lab, QLabel) or not _shown(lab, root) or fi is None:
                continue
            fg = fi.geometry()
            if fg.height() <= 0:
                continue
            lg = li.geometry()
            if abs(lg.center().y() - fg.center().y()) > 2 and fg.height() <= sizes.ROW_H + 4:
                out.append(f"{where}: 名前「{lab.text()[:16]}」の縦の真ん中が欄と {lg.center().y() - fg.center().y()}px ずれている")
    return out


def _input_of(w: QWidget):
    """並びの1つの升に置いた部品の中の欄（欄そのもの、または値の欄 ValueField のような入れ物の中の最初の欄）。"""
    kinds = (QComboBox, QLineEdit, QAbstractSpinBox)
    if isinstance(w, kinds):
        return w
    for c in w.findChildren(QWidget):
        if isinstance(c, kinds) and not _skip(c) and not isinstance(c.parent(), (QComboBox, QAbstractSpinBox)):
            return c
    return None


def _grid_columns(root: QWidget, where: str) -> list[str]:
    """並び（QGridLayout）の同じ列（始まりの列）に置いた欄・ドロップダウンは、幅と左端がそろう。"""
    out = []
    for grid in root.findChildren(QGridLayout):
        cols: dict[int, list[QWidget]] = {}
        for i in range(grid.count()):
            item = grid.itemAt(i)
            w = item.widget() if item is not None else None
            if w is None or not _shown(w, root):
                continue
            f = _input_of(w)
            if f is None or not _shown(f, root) or f.window() is not root.window():
                continue
            _r, c, _rs, _cs = grid.getItemPosition(i)
            cols.setdefault(c, []).append(f)
        for c, fs in cols.items():
            first = fs[0]
            g0 = _grect(first)
            for f in fs[1:]:
                g = _grect(f)
                if abs(g.width() - g0.width()) > 1 or abs(g.left() - g0.left()) > 1:
                    out.append(f"{where}: 並びの {c} 列目の {_name(f)}（左端 {g.left()}・幅 {g.width()}）が {_name(first)}"
                               f"（左端 {g0.left()}・幅 {g0.width()}）とそろわない")
    return out
