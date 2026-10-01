"""右のタブ「表示」（設計J §4.5・P §2・O・I §3）。

- 表示の組み合わせ: スペクトルのみ／＋ウォーターフォール／＋密度（後の2つは設計P。消さずに無効にし理由: U-08）
- 描き方: 線／塗りつぶし（旧 Line/Dot。測定値は変わらない）。コアの表示の設定（Qt と Web で同じ・プリセットに入る）
- 振幅の詳細（設計O）: 単位 dBm／dBμV／dBmV と外部補正 dB。換算はコア（グラフ・マーカー・CSV・PNG・REF が
  同じ単位になる）。「初期値に戻す」
- 配色（J §4.4）: ライト・ダークの初期値にするボタン（変えた色の数を言ってから聞く）と、編集の画面への入口
- 画像（PNG）のメモ1行（大きさ・配色・保存先は設定の画面の「保存」: 同じ値の入口を2つ作らない U-09）
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from . import texts, widget_sync
from .fields import DbField
from .tab_traces import scroll, set_enabled

if TYPE_CHECKING:
    from .main_window import MainWindow

# まだ作っていない欄の理由（設計P §3・§4・§6）
REASON_WATERFALL = texts.NOT_YET["waterfall"]
REASON_DENSITY = texts.NOT_YET["density"]
REASON_BANDPLAN = texts.NOT_YET["bandplan"]

V = texts.VIEW_TEXTS
S = texts.SETTINGS_TEXTS


class DisplayTab(QWidget):
    def __init__(self, win: "MainWindow") -> None:
        super().__init__()
        self.win = win
        inner = QWidget()
        lay = QVBoxLayout(inner)
        lay.setContentsMargins(4, 4, 4, 4)

        L = texts.LABELS
        g = QGroupBox(L["view_combo"])
        v = QVBoxLayout(g)
        self.view_group = QButtonGroup(self)
        self.view_spectrum = QRadioButton(L["spectrum_only"])
        self.view_waterfall = QRadioButton(L["plus_waterfall"])
        self.view_density = QRadioButton(L["plus_density"])
        self.view_spectrum.setChecked(True)
        set_enabled(self.view_waterfall, REASON_WATERFALL)
        set_enabled(self.view_density, REASON_DENSITY)
        for b in (self.view_spectrum, self.view_waterfall, self.view_density):
            self.view_group.addButton(b)
            v.addWidget(b)
        lay.addWidget(g)

        g = QGroupBox(L["draw_style"])
        h = QHBoxLayout(g)
        self.style_group = QButtonGroup(self)
        self.style_line = QRadioButton(L["draw_line"])
        self.style_fill = QRadioButton(L["draw_fill"])
        self.style_line.setChecked(True)
        for b in (self.style_line, self.style_fill):
            self.style_group.addButton(b)
            h.addWidget(b)
        h.addStretch(1)
        self.style_fill.toggled.connect(self._style_changed)
        lay.addWidget(g)

        g = QGroupBox(L["amplitude_detail"])
        form = QFormLayout(g)
        self.unit = QComboBox()
        for code, name in (("dBm", "dBm"), ("dBuV", "dBμV"), ("dBmV", "dBmV")):
            self.unit.addItem(name, code)
        self.unit.setToolTip(V["level_unit_tip"])
        self.unit.activated.connect(lambda _i: win.call("set_view", level_unit=self.unit.currentData()))
        form.addRow(V["level_unit"], self.unit)
        self.ext = DbField(V["ext_db"], "dB", lambda v: win.call("set_view", field=self.ext, ext_db=float(v)),
                           big=False)
        self.ext.setToolTip(V["ext_db_tip"])
        self.ext.edit.setPlaceholderText(V["ext_db_placeholder"])
        form.addRow(self.ext)
        self.amp_note = QLabel(V["level_unit_ratio"])
        self.amp_note.setObjectName("dim")
        self.amp_note.setWordWrap(True)
        form.addRow(self.amp_note)
        reset = QPushButton(V["reset_amp"])
        reset.setObjectName("small")
        reset.clicked.connect(lambda: win.call("set_view", level_unit=None, ext_db=None))
        form.addRow(reset)
        lay.addWidget(g)

        g = QGroupBox(L["colors"])
        v = QVBoxLayout(g)
        row = QHBoxLayout()
        self.reset_light = QPushButton(S["palette_reset_light"])
        self.reset_light.clicked.connect(lambda: win.reset_palette("light"))
        self.reset_dark = QPushButton(S["palette_reset_dark"])
        self.reset_dark.clicked.connect(lambda: win.reset_palette("dark"))
        row.addWidget(self.reset_light)
        row.addWidget(self.reset_dark)
        row.addStretch(1)
        v.addLayout(row)
        self.palette_state = QLabel("")
        self.palette_state.setObjectName("dim")
        self.palette_state.setWordWrap(True)
        v.addWidget(self.palette_state)
        self.colors = QPushButton(texts.MENU_ITEMS["view_colors"])
        self.colors.clicked.connect(lambda: win.actions["view_colors"].trigger())
        v.addWidget(self.colors)
        self.bandplan = QPushButton(L["show_bandplan"])
        set_enabled(self.bandplan, REASON_BANDPLAN)
        v.addWidget(self.bandplan)
        lay.addWidget(g)

        g = QGroupBox(L["png_group"])
        form = QFormLayout(g)
        self.png_note = QLineEdit("")
        self.png_note.setPlaceholderText(L["png_memo_placeholder"])
        form.addRow(L["memo"], self.png_note)
        lay.addWidget(g)
        lay.addStretch(1)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll(inner))

    def apply_state(self, st: dict) -> None:
        """コアの表示の設定（単位・外部補正・描き方）と配色を映す。"""
        v = st.get("view") or {}
        widget_sync.set_data(self.unit, v.get("level_unit"))
        widget_sync.set_checked(self.style_fill, v.get("draw_style") == "fill")
        widget_sync.set_checked(self.style_line, v.get("draw_style") != "fill")
        self.ext.set_value(float(v.get("ext_db") or 0.0))  # 打っている間は欄が書き直さない（fields._Edit）
        from .screen_model import palette_view

        self.palette_state.setText(palette_view(st)["base_text"])

    def fill_on(self) -> bool:
        return self.style_fill.isChecked()

    def _style_changed(self, fill: bool) -> None:
        self.win.call("set_view", draw_style="fill" if fill else "line")

    # PNG の配色・大きさは設定の画面の「保存」で選ぶ（画面ごとの覚え書き）
    def png_palette_choice(self) -> str:
        return str(self.win.prefs.get("png_palette", "light"))

    def png_size_choice(self) -> tuple[int, int]:
        text = str(self.win.prefs.get("png_size", "1600×1000"))
        try:
            w, h = text.split("×")
            return int(w), int(h)
        except ValueError:
            return 1600, 1000
