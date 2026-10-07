"""部品の大きさと余白（設計J §4.11。規則は docs/uxui_rule.md の S-01〜S-07）。

大きさは、部品が載っている段（上段は大・それ以外は標準・状態バーは小）と、入れる値の種類だけで決める。
画面ごと・欄ごとに大きさを決めない（ばらばらになって見苦しかった: 2026-10-03 ユーザー指摘）。
Web の style.css の :root にも同じ値を持つ（試験で一致を確かめる: U-09）。値の表は Qt を使わずに読める。
"""
from __future__ import annotations

from ..core.devices.rtlsdr.levels import UNIT_UNCALIBRATED

# ---------------------------------------------------------------- 値（Web の style.css と同じ）

# 段ごとの部品の高さ（欄・ドロップダウン・ボタンが同じ高さ）
H_L = 40
H_M = 26
H_S = 20
# 表の行（中に置く標準の部品が収まる高さ）
ROW_H = 28
# 文字の大きさ（px）。標準の 13px は Web の本文・Qt の既定（Yu Gothic UI 9.75pt）と同じ
# 上段の値の字は 18px（20px では1行に要る幅が増え、SA の 1600 幅・TG の 1920 幅で RBW 以降が2行目へ落ちた:
# 設計の PDCA 2巡目）
FONT_L_VALUE = 18
FONT_L_BUTTON = 16
FONT_RUN = 17
FONT_M = 13
FONT_S = 12
# 連続・単発・停止の最小幅（上段で最も大きいボタン: J §4.3）
RUN_MIN_W = 78
# ボタンの左右の余白と最小幅（短い語のボタンが小さく見えないように）
BTN_PAD_X = 12
BTN_MIN_W = 64
# 余白（S-04）
GAP = 6
LABEL_GAP = 8
SECTION_GAP = 12
INSET = 8
WINDOW_MARGIN = 12
# チェックボックス・ラジオの印の大きさ（S-06）
INDICATOR = 14
# 上段の掃引の進み具合（設計J §4.13）: 細い棒の高さと、割合の文字の幅（「100%」が入る「0」の字の数）
PROGRESS_BAR_H = 6
PROGRESS_TEXT_CH = 5

# 入力欄の幅（S-02）。標準の文字の em。値の種類ごとに一番長い値が全部見える幅
# 周波数とレベルは同じ幅（どちらも単位付きの値。レベルの一番長い単位「dB（未校正）」が入る幅: 設計の PDCA 1巡目）
WIDTH_EM = {
    "int": 6,      # 65535・COM255・-120（ポート・ppm・点数・回数・上位 N）
    "value": 13,   # -12100.000000 MHz・-120.0 dB（未校正）（周波数・レベル・dB の欄。打てるドロップダウンも同じ幅）
    "name": 16,    # 基準の名前・メモ
    "color": 6,    # #rrggbb
}
# 種類ごとの一番長い値の見本（幅が足りるかを試験で測る。打てるドロップダウンの ▼ の分も入れて測る）
WIDTH_SAMPLES = {
    "int": ("65535", "COM255", "-120"),
    "value": ("-12100.000000 MHz", "-120.00 dBμV", "-120.00 dBmV", f"-120.0 {UNIT_UNCALIBRATED}"),
    "color": ("#rrggbb",),
}
# 上段の周波数の欄の一番長い値（上段の字で測った一番広いものの幅で止める: S-02）。GigaSt は 100Hz 単位で負の Start も
# ある。RTL-SDR は 1Hz 単位で小数 6 桁（1420.405752 MHz など。100Hz 単位の見本だけでは切れた: 設計の PDCA 2巡目）
TOP_FREQ_SAMPLES = ("-12100.0000 MHz", "0000.000000 MHz")
# QLineEdit が文字の左右に持つ内側の余白（見た目の決まりの余白・枠とは別。幅の式に入れないと文字が切れた）
EDIT_INNER = 2
# 打てるドロップダウンの ▼ の幅（見本を測るときに足す）
CHOICE_ARROW = 22


def width_px(kind: str) -> int:
    return round(WIDTH_EM[kind] * FONT_M)


# ---------------------------------------------------------------- 部品に当てる（Qt）


def set_width(w, kind: str) -> None:
    """入力欄の幅を値の種類で決める（窓の幅に合わせて伸ばさない: S-02）。"""
    w.setFixedWidth(width_px(kind))


def grow(w) -> None:
    """窓の幅に合わせて伸ばしてよい部品（読むだけの長い文字・上段の周波数: S-02）。"""
    from PySide6.QtWidgets import QSizePolicy

    w.setProperty("grow", True)
    w.setSizePolicy(QSizePolicy.Policy.Expanding, w.sizePolicy().verticalPolicy())


def set_tier(w, tier: str) -> None:
    """段を付ける（"L" 上段・"S" 状態バー。標準は付けない）。高さと文字は見た目の決まり（theme.py）が段で決める。"""
    w.setProperty("tier", tier)
    w.style().unpolish(w)
    w.style().polish(w)


def form(parent=None):
    """名前を左・欄を右に並べる形（S-05）。欄は伸ばさない（伸ばしてよい部品だけ伸びる）。"""
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QFormLayout

    f = QFormLayout(parent) if parent is not None else QFormLayout()
    f.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
    f.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
    f.setFormAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
    f.setHorizontalSpacing(LABEL_GAP)
    f.setVerticalSpacing(GAP)
    f.setContentsMargins(INSET, INSET, INSET, INSET) if parent is not None else f.setContentsMargins(0, 0, 0, 0)
    return f


def vbox(parent=None, inset: bool = True):
    """縦の並び。枠の中なら内側の余白を付ける（S-04）。"""
    from PySide6.QtWidgets import QVBoxLayout

    v = QVBoxLayout(parent) if parent is not None else QVBoxLayout()
    m = INSET if inset else 0
    v.setContentsMargins(m, m, m, m)
    v.setSpacing(GAP)
    return v


def hbox(parent=None):
    """横の並び（行）。外の余白なし・部品の間は GAP。"""
    from PySide6.QtWidgets import QHBoxLayout

    h = QHBoxLayout(parent) if parent is not None else QHBoxLayout()
    h.setContentsMargins(0, 0, 0, 0)
    h.setSpacing(GAP)
    return h


def window(layout) -> None:
    """別の窓の外周と、見出しのまとまりの間（S-04）。"""
    layout.setContentsMargins(WINDOW_MARGIN, WINDOW_MARGIN, WINDOW_MARGIN, WINDOW_MARGIN)
    layout.setSpacing(SECTION_GAP)


def buttons_row(*buttons, right: bool = False):
    """ボタンの行。左から詰める（窓を閉じるボタンなどは right=True で右から）。横に伸ばさない（S-03）。"""
    h = hbox()
    if right:
        h.addStretch(1)
    for b in buttons:
        h.addWidget(b)
    if not right:
        h.addStretch(1)
    return h


def same_width_buttons(buttons, font_px: int) -> None:
    """並んだボタンを1つの幅（一番長い文字＋左右の余白）にそろえる（上段のモードの5つ: 短い語に最小幅 64px を当てると
    上段が1行に収まらなかった・設計の PDCA 2巡目）。字は段の字の大きさで測る（見た目の決まりが当たる前でも同じ幅）。"""
    from PySide6.QtGui import QFont, QFontMetrics

    if not buttons:
        return
    f = QFont(buttons[0].font())
    f.setPixelSize(font_px)
    fm = QFontMetrics(f)
    w = max(fm.horizontalAdvance(b.text()) for b in buttons) + 2 * BTN_PAD_X + 2
    for b in buttons:
        b.setProperty("same_width", True)
        b.setFixedWidth(w)


_filter = None


def fit_button(b) -> None:
    """ボタンの最小幅を、いまの文字の幅と BTN_MIN_W（連続・単発・停止は RUN_MIN_W）の大きい方にする（S-03）。

    見た目の決まりの min-width で決めると、Qt はそれを「縮めてよい下限」に使い、並びが狭いとボタンが文字より細く
    縮んだ（2026-10-03 確認）。見た目の決まり・字が当たったときは install の決まりが呼ぶ。文字を後から変えるボタン
    （山／谷で名前が変わる探すボタン: G §3.8）は、変えた後で呼ぶ（前の文字の幅のまま伸びて残らないように）。
    """
    if b.property("same_width"):
        return  # 並びで1つの幅にそろえたボタン（same_width_buttons が決める）
    b.setMinimumWidth(0)
    floor = RUN_MIN_W if b.property("run") else BTN_MIN_W
    b.setMinimumWidth(max(floor, b.sizeHint().width()))


def install(app) -> None:
    """アプリの全部の部品に、置き場所によらない決まりを当てる（1つの定義元: U-09）。

    - ボタンは横に伸ばさない（S-03）。伸ばしてよいもの（grow）だけ伸ばす
    - 入力欄・ドロップダウンも窓の幅に合わせて伸ばさない（S-02）
    - 数値の欄に ▲▼ を付けない（S-07）
    - 表の行の高さ（ROW_H）
    """
    global _filter
    from PySide6.QtCore import QEvent, QObject
    from PySide6.QtWidgets import (QAbstractButton, QAbstractSpinBox, QCheckBox, QComboBox, QLineEdit,
                                   QPushButton, QRadioButton, QSizePolicy, QTableView)

    class _Filter(QObject):
        def eventFilter(self, obj, ev) -> bool:
            t = ev.type()
            if t == QEvent.Type.Polish:
                if isinstance(obj, QPushButton):
                    _button_min(obj)
                if obj.property("grow"):
                    return False
                if isinstance(obj, QAbstractSpinBox):
                    obj.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
                if isinstance(obj, (QAbstractButton, QAbstractSpinBox, QComboBox)) \
                        and not isinstance(obj, (QCheckBox, QRadioButton)):
                    _fix_width(obj)
                elif isinstance(obj, QLineEdit) and not _in_cell(obj):
                    _fix_width(obj)
                elif isinstance(obj, QTableView):
                    obj.verticalHeader().setDefaultSectionSize(ROW_H)
            elif t in (QEvent.Type.FontChange, QEvent.Type.StyleChange, QEvent.Type.DynamicPropertyChange) \
                    and isinstance(obj, QPushButton):
                _button_min(obj)
            return False

    _button_min = fit_button

    def _fix_width(w) -> None:
        sp = w.sizePolicy()
        if sp.horizontalPolicy() != QSizePolicy.Policy.Fixed:
            w.setSizePolicy(QSizePolicy.Policy.Fixed, sp.verticalPolicy())

    def _in_cell(w) -> bool:
        # 表のセルを直す欄・ドロップダウンの中の欄はセルや親の幅に従う
        p = w.parent()
        return isinstance(p, QComboBox) or (p is not None and p.objectName() == "qt_scrollarea_viewport")

    if _filter is None:
        _filter = _Filter()
        app.installEventFilter(_filter)
