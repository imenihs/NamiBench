"""画面の配色と部品の見た目（設計J §4.4）。

色の値は png_export の初期値の組を使う（同じ名前の色を2か所に書かない: U-09）。
配色の編集（ユーザー設定の1つの組）は 6-6。いまはダークの初期値で描く。
"""
from __future__ import annotations

from PySide6.QtGui import QFont, QFontDatabase

from .png_export import DARK_DEFAULT

DARK: dict[str, str] = dict(DARK_DEFAULT)

TRACE_COLORS = ("trace1", "trace2", "trace3", "trace4")


def trace_color(number: int, pal: dict[str, str] = DARK) -> str:
    return pal[TRACE_COLORS[(int(number) - 1) % 4]]


def number_font(base: QFont) -> QFont:
    """数える列の字（桁を縦にそろえる: U-17）。大きさは元の字に合わせる。"""
    f = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
    f.setPointSizeF(base.pointSizeF())
    return f


def stylesheet(c: dict[str, str] = DARK) -> str:
    return f"""
        QWidget {{ background: {c['panel_bg']}; color: {c['text']}; }}
        QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {{ background: {c['graph_bg']}; border: 1px solid {c['axis']}; padding: 2px; }}
        QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled,
        QCheckBox:disabled, QRadioButton:disabled, QLabel:disabled {{ color: {c['axis']}; }}
        QPushButton, QToolButton {{ background: {c['graph_bg']}; border: 1px solid {c['axis']}; padding: 4px 10px; }}
        QPushButton:checked {{ background: {c['accent']}; color: {c['graph_bg']}; }}
        QPushButton:disabled, QToolButton:disabled {{ color: {c['axis']}; }}
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
        QTabBar::tab:disabled {{ color: {c['axis']}; }}
        QSplitter::handle {{ background: {c['axis']}; }}
        QProgressBar {{ border: 1px solid {c['axis']}; text-align: center; background: {c['graph_bg']}; }}
        QProgressBar::chunk {{ background: {c['accent']}; }}
        QFrame#taskPanel {{ border-top: 1px solid {c['warn']}; }}
    """
