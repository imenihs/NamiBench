"""画面の配色と部品の見た目（設計J §4.4）。

色の値は core/report_layout の初期値の組を使う（同じ名前の色を2か所に書かない: U-09）。
使う配色はコアのユーザー設定の1つ（core/palette.py）。画面はそれを写した辞書で描く（look.apply_palette）。
"""
from __future__ import annotations

from PySide6.QtGui import QFont, QFontDatabase

from ..core.report_layout import DARK_DEFAULT

DARK: dict[str, str] = dict(DARK_DEFAULT)

TRACE_COLORS = ("trace1", "trace2", "trace3", "trace4")


def trace_color(number: int, pal: dict[str, str] = DARK) -> str:
    return pal[TRACE_COLORS[(int(number) - 1) % 4]]


def number_font(base: QFont) -> QFont:
    """数える列の字（桁を縦にそろえる: U-17）。大きさは元の字に合わせる。"""
    f = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
    f.setPointSizeF(base.pointSizeF())
    return f


def mix(a: str, b: str, t: float) -> str:
    """2色の間の色（t=0 で a、1 で b）。"""
    ca, cb = (tuple(int(x.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4)) for x in (a, b))
    return "#" + "".join(f"{round(p + (q - p) * t):02x}" for p, q in zip(ca, cb))


def stylesheet(c: dict[str, str] = DARK) -> str:
    # 使えない欄の文字。軸の色を使うと、ライトの初期値（濃い軸）では使える欄と見分けが付かない。
    # 控えめな文字をパネルの背景へ寄せた色にする（どちらの配色でも薄く見える）
    c = {**c, "axis_text": mix(c["text_dim"], c["panel_bg"], 0.6)}
    # メニューの項目は余白まで決める（QMenu::item）。決めないと Windows では幅を Windows の部品の決まりで測り、
    # 描くのはこの決まりなので、幅が足りず文字とキーの表示が切れる
    return f"""
        QWidget {{ background: {c['panel_bg']}; color: {c['text']}; }}
        QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {{ background: {c['graph_bg']}; border: 1px solid {c['axis']}; padding: 2px; }}
        QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled,
        QCheckBox:disabled, QRadioButton:disabled, QLabel:disabled {{ color: {c['axis_text']}; }}
        QPushButton, QToolButton {{ background: {c['graph_bg']}; border: 1px solid {c['axis']}; padding: 4px 10px; }}
        QPushButton:checked {{ background: {c['accent']}; color: {c['graph_bg']}; }}
        QPushButton:disabled, QToolButton:disabled {{ color: {c['axis_text']}; }}
        QPushButton[running="true"] {{ background: {c['running']}; }}
        QLabel#note {{ color: {c['warn']}; }}
        QFrame#notePopup {{ background: {c['panel_bg']}; border: 1px solid {c['warn']}; }}
        QFrame#notePopup QLabel {{ background: transparent; }}
        QPushButton#noteAction {{ color: {c['accent']}; border: 1px solid {c['accent']}; padding: 2px 8px; }}
        QPushButton#small, QToolButton#small {{ padding: 1px 6px; }}
        QToolButton#spanLock {{ background: transparent; border: 1px solid {c['axis']}; padding: 0 4px;
                                color: {c['text_dim']}; }}
        QToolButton#spanLock:checked {{ border: 1px solid {c['accent']}; color: {c['accent']}; }}
        QLabel#warn {{ color: {c['warn']}; }}
        QLabel#dim {{ color: {c['text_dim']}; }}
        QStatusBar QLabel {{ padding: 0 6px; }}
        QGroupBox {{ border: 1px solid {c['axis']}; margin-top: 10px; padding-top: 4px; }}
        QGroupBox::title {{ subcontrol-origin: margin; left: 6px; padding: 0 3px; }}
        QTreeWidget, QTableWidget, QListWidget {{ background: {c['graph_bg']}; alternate-background-color: {c['panel_bg']};
                                                  border: 1px solid {c['axis']}; }}
        QTreeWidget::item:selected, QTableWidget::item:selected, QListWidget::item:selected {{
            background: {c['accent']}; color: {c['graph_bg']}; }}
        QHeaderView::section {{ background: {c['panel_bg']}; color: {c['text_dim']}; border: 0;
                                border-bottom: 1px solid {c['axis']}; padding: 2px 4px; }}
        QTabBar::tab {{ background: {c['panel_bg']}; border: 1px solid {c['axis']}; padding: 4px 8px; }}
        QTabBar::tab:selected {{ background: {c['graph_bg']}; border-bottom: 2px solid {c['accent']}; }}
        QTabBar::tab:disabled {{ color: {c['axis_text']}; }}
        QSplitter::handle {{ background: {c['axis']}; }}
        QProgressBar {{ border: 1px solid {c['axis']}; text-align: center; background: {c['graph_bg']}; }}
        QProgressBar::chunk {{ background: {c['accent']}; }}
        QFrame#taskPanel {{ border-top: 1px solid {c['warn']}; }}
        QDockWidget::title {{ background: {c['graph_bg']}; border: 1px solid {c['axis']}; padding: 4px 8px; text-align: left; }}
        QMenu {{ border: 1px solid {c['axis']}; padding: 4px 0; }}
        QMenu::item {{ padding: 4px 28px 4px 20px; background: transparent; }}
        QMenu::item:selected {{ background: {c['accent']}; color: {c['graph_bg']}; }}
        QMenu::item:disabled {{ color: {c['axis_text']}; }}
        QComboBox QAbstractItemView::item:disabled {{ color: {c['axis_text']}; }}
        QMenu::separator {{ height: 1px; background: {c['axis']}; margin: 4px 8px; }}
        QMenu::indicator {{ width: 14px; height: 14px; left: 4px; }}
    """
