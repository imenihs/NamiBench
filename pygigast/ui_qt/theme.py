"""画面の配色と部品の見た目（設計J §4.4・§4.11）。

色の値は core/report_layout の初期値の組を使う（同じ名前の色を2か所に書かない: U-09）。
使う配色はコアのユーザー設定の1つ（core/palette.py）。画面はそれを写した辞書で描く（look.apply_palette）。
部品の高さ・余白・文字の大きさは sizes.py の段の値（S-01〜S-07）。
"""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QPainter, QPainterPath, QPalette, QPen
from PySide6.QtWidgets import QProxyStyle, QStyle, QStyleFactory

from ..core.report_layout import DARK_DEFAULT
from . import sizes as Z

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


def _dim_color(c: dict[str, str]) -> str:
    # 使えない部品の文字。軸の色を使うと、ライトの初期値（濃い軸）では使える部品と見分けが付かない。
    # 控えめな文字をパネルの背景へ寄せた色にする（どちらの配色でも薄く見える）
    return mix(c["text_dim"], c["panel_bg"], 0.6)


class _Style(QProxyStyle):
    """Fusion の描き方に、チェックボックスとラジオの印だけ自分の描き方を重ねる（S-06）。

    Windows 11 の描き方では、入れたチェックボックスの枠と選んだラジオの丸がダークの背景で消えた（2026-10-03 確認）。
    印は 外した=控えめな文字の枠、入れた=強調の色で塗って中にグラフの背景の色の ✓（ラジオは点）、使えない=薄い色。
    """

    def __init__(self, colors: dict[str, str]) -> None:
        super().__init__(QStyleFactory.create("Fusion"))
        self.colors = dict(colors)

    def drawPrimitive(self, pe, opt, p, w=None) -> None:
        box = pe == QStyle.PrimitiveElement.PE_IndicatorCheckBox
        if not box and pe != QStyle.PrimitiveElement.PE_IndicatorRadioButton:
            super().drawPrimitive(pe, opt, p, w)
            return
        c = self.colors
        on = bool(opt.state & QStyle.StateFlag.State_On)
        enabled = bool(opt.state & QStyle.StateFlag.State_Enabled)
        r = QRectF(opt.rect).adjusted(0.5, 0.5, -0.5, -0.5)
        s = min(r.width(), r.height())
        r = QRectF(r.center().x() - s / 2, r.center().y() - s / 2, s, s)
        edge = QColor(c["text_dim"] if enabled else _dim_color(c))
        fill = QColor(c["accent"] if enabled else _dim_color(c))
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(fill if on else edge, 1))
        if box:
            p.setBrush(fill if on else QColor(c["graph_bg"]))
            p.drawRoundedRect(r, 2, 2)
            if on:
                path = QPainterPath(QPointF(r.left() + s * 0.22, r.top() + s * 0.52))
                path.lineTo(QPointF(r.left() + s * 0.42, r.top() + s * 0.72))
                path.lineTo(QPointF(r.left() + s * 0.78, r.top() + s * 0.30))
                p.setPen(QPen(QColor(c["graph_bg"]), max(1.5, s / 8), Qt.PenStyle.SolidLine,
                              Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawPath(path)
        else:
            p.setBrush(QColor(c["graph_bg"]))
            p.drawEllipse(r)
            if on:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(fill)
                d = s * 0.5
                p.drawEllipse(QRectF(r.center().x() - d / 2, r.center().y() - d / 2, d, d))
        p.restore()

    def pixelMetric(self, m, opt=None, w=None) -> int:
        if m in (QStyle.PixelMetric.PM_IndicatorWidth, QStyle.PixelMetric.PM_IndicatorHeight,
                 QStyle.PixelMetric.PM_ExclusiveIndicatorWidth, QStyle.PixelMetric.PM_ExclusiveIndicatorHeight):
            return Z.INDICATOR
        return super().pixelMetric(m, opt, w)


def _palette(c: dict[str, str]) -> QPalette:
    """Fusion が見た目の決まりの外で描く所（スクロールバー・ツールチップ・選択）の色。"""
    pal = QPalette()
    G = QPalette.ColorGroup
    R = QPalette.ColorRole
    roles = {
        R.Window: c["panel_bg"], R.WindowText: c["text"], R.Base: c["graph_bg"], R.AlternateBase: c["panel_bg"],
        R.Text: c["text"], R.Button: c["graph_bg"], R.ButtonText: c["text"], R.BrightText: c["warn"],
        R.Highlight: c["accent"], R.HighlightedText: c["graph_bg"], R.ToolTipBase: c["graph_bg"],
        R.ToolTipText: c["text"], R.PlaceholderText: c["text_dim"], R.Link: c["accent"],
        R.Light: mix(c["axis"], c["text"], 0.3), R.Midlight: c["axis"], R.Mid: c["axis"],
        R.Dark: c["grid"], R.Shadow: c["graph_bg"],
    }
    for role, color in roles.items():
        pal.setColor(role, QColor(color))
    for role in (R.WindowText, R.Text, R.ButtonText):
        pal.setColor(G.Disabled, role, QColor(_dim_color(c)))
    return pal


def install(app, c: dict[str, str] = DARK) -> None:
    """アプリ全体の描き方（Fusion＋印）・色・大きさの決まりを入れる。配色を変えたときも呼ぶ。"""
    st = app.style()
    if isinstance(st, _Style):
        st.colors = dict(c)
    else:
        app.setStyle(_Style(c))
    app.setPalette(_palette(c))
    Z.install(app)


def stylesheet(c: dict[str, str] = DARK) -> str:
    c = {**c, "axis_text": _dim_color(c)}
    # 段ごとの高さ（S-01）。min-height と max-height は中身の高さなので、上下の余白と枠を引く
    m_in = Z.H_M - 6
    l_in = Z.H_L - 6
    s_in = Z.H_S - 4
    # メニューの項目は余白まで決める（QMenu::item）。決めないと Windows では幅を Windows の部品の決まりで測り、
    # 描くのはこの決まりなので、幅が足りず文字とキーの表示が切れる
    return f"""
        QWidget {{ background: {c['panel_bg']}; color: {c['text']}; }}
        QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {{ background: {c['graph_bg']}; border: 1px solid {c['axis']};
            padding: 2px 6px; min-height: {m_in}px; max-height: {m_in}px; }}
        QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled,
        QCheckBox:disabled, QRadioButton:disabled, QLabel:disabled {{ color: {c['axis_text']}; }}
        QPushButton, QToolButton {{ background: {c['graph_bg']}; border: 1px solid {c['axis']};
            padding: 2px {Z.BTN_PAD_X}px; min-height: {m_in}px; max-height: {m_in}px; }}
        QCheckBox, QRadioButton {{ min-height: {Z.H_M}px; spacing: {Z.GAP}px; }}
        QPushButton:checked {{ background: {c['accent']}; color: {c['graph_bg']}; }}
        QPushButton:disabled, QToolButton:disabled {{ color: {c['axis_text']}; }}
        QPushButton[running="true"] {{ background: {c['running']}; }}
        *[tier="L"] {{ min-height: {l_in}px; max-height: {l_in}px; font-size: {Z.FONT_L_VALUE}px; }}
        QPushButton[tier="L"], QToolButton[tier="L"] {{ font-size: {Z.FONT_L_BUTTON}px; }}
        QPushButton[run="true"] {{ font-size: {Z.FONT_RUN}px; }}
        *[tier="S"] {{ min-height: {s_in}px; max-height: {s_in}px; font-size: {Z.FONT_S}px; padding: 1px 6px; }}
        QLabel[tier="S"] {{ min-height: {Z.H_S}px; max-height: {Z.H_S}px; padding: 0; }}
        QLabel#note {{ color: {c['warn']}; }}
        QFrame#notePopup {{ background: {c['panel_bg']}; border: 1px solid {c['warn']}; }}
        QFrame#notePopup QLabel {{ background: transparent; }}
        QPushButton#noteAction {{ color: {c['accent']}; border: 1px solid {c['accent']}; }}
        QToolButton#spanLock {{ background: transparent; border: 1px solid {c['axis']}; padding: 1px 4px;
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
                                border-bottom: 1px solid {c['axis']}; padding: 2px 6px; }}
        QTabBar::tab {{ background: {c['panel_bg']}; border: 1px solid {c['axis']}; padding: 4px 10px; }}
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
        QToolTip {{ background: {c['graph_bg']}; color: {c['text']}; border: 1px solid {c['axis']}; }}
    """
