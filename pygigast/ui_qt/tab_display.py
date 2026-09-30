"""右のタブ「表示」（設計J §4.5・P §2・I §3）。

- 表示の組み合わせ: スペクトルのみ／＋ウォーターフォール／＋密度（後の2つは次の回。消さずに無効にし理由: U-08）
- 描き方: 線／塗りつぶし（旧 Line/Dot。画面だけの設定で、測定値は変わらない）
- 振幅の詳細: 単位（いまは dBm・dB。dBμV・dBmV の換算と外部補正は次の回）
- 画像（PNG）: 配色（既定はライトの初期値: J §4.4）・大きさ・メモ1行・保存先
- 配色の編集（J §4.4）は次の回
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QFileDialog,
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

from .tab_traces import disable_item, scroll, set_enabled

if TYPE_CHECKING:
    from .main_window import MainWindow

REASON_WATERFALL = "ウォーターフォールは次の回で実装します（設計P §3）"
REASON_DENSITY = "密度表示は次の回で実装します（設計P §4）"
REASON_UNIT = "dBμV・dBmV への換算は次の回で実装します（設計O。値の換算はコアで行い、マーカー・書き出しとそろえる）"
REASON_EXT = "外部補正（アッテネータ・アンプ）は次の回で実装します（設計O）"
REASON_COLORS = "配色の編集は次の回で実装します（設計J §4.4）"
REASON_BANDPLAN = "バンドプラン・ブックマークは次の回で実装します（設計P §6）"

PNG_PALETTES = (("light", "ライトの初期値"), ("dark", "ダークの初期値"), ("screen", "画面と同じ"))
PNG_SIZES = ((1600, 1000), (1920, 1080), (1280, 800), (2560, 1600))


class DisplayTab(QWidget):
    def __init__(self, win: "MainWindow") -> None:
        super().__init__()
        self.win = win
        inner = QWidget()
        lay = QVBoxLayout(inner)
        lay.setContentsMargins(4, 4, 4, 4)

        g = QGroupBox("表示の組み合わせ")
        v = QVBoxLayout(g)
        self.view_group = QButtonGroup(self)
        self.view_spectrum = QRadioButton("スペクトルのみ")
        self.view_waterfall = QRadioButton("スペクトル＋ウォーターフォール")
        self.view_density = QRadioButton("スペクトル＋密度")
        self.view_spectrum.setChecked(True)
        set_enabled(self.view_waterfall, REASON_WATERFALL)
        set_enabled(self.view_density, REASON_DENSITY)
        for b in (self.view_spectrum, self.view_waterfall, self.view_density):
            self.view_group.addButton(b)
            v.addWidget(b)
        lay.addWidget(g)

        g = QGroupBox("描き方")
        h = QHBoxLayout(g)
        self.style_group = QButtonGroup(self)
        self.style_line = QRadioButton("線")
        self.style_fill = QRadioButton("塗りつぶし")
        fill = str(win.prefs.get("draw_style", "line")) == "fill"
        (self.style_fill if fill else self.style_line).setChecked(True)
        for b in (self.style_line, self.style_fill):
            self.style_group.addButton(b)
            h.addWidget(b)
        h.addStretch(1)
        self.style_fill.toggled.connect(self._style_changed)
        lay.addWidget(g)

        g = QGroupBox("振幅の詳細")
        form = QFormLayout(g)
        self.unit = QComboBox()
        self.unit.addItem("自動（電力は dBm・比は dB）", None)
        self.unit.addItem("dBμV", "dBuV")
        self.unit.addItem("dBmV", "dBmV")
        disable_item(self.unit, 1, REASON_UNIT)
        disable_item(self.unit, 2, REASON_UNIT)
        self.unit.setToolTip("縦軸の単位")
        form.addRow("単位", self.unit)
        self.ext = QLineEdit("0 dB")
        set_enabled(self.ext, REASON_EXT)
        form.addRow("外部補正", self.ext)
        lay.addWidget(g)

        g = QGroupBox("画像（PNG）")
        form = QFormLayout(g)
        self.png_palette = QComboBox()
        for code, name in PNG_PALETTES:
            self.png_palette.addItem(name, code)
        cur = str(win.prefs.get("png_palette", "light"))
        self.png_palette.setCurrentIndex(max(0, self.png_palette.findData(cur)))
        self.png_palette.activated.connect(lambda _i: win.prefs.set("png_palette", self.png_palette.currentData()))
        form.addRow("配色", self.png_palette)
        self.png_size = QComboBox()
        for w, hh in PNG_SIZES:
            self.png_size.addItem(f"{w}×{hh}", (w, hh))
        self.png_size.setCurrentIndex(max(0, self.png_size.findText(str(win.prefs.get("png_size", "1600×1000")))))
        self.png_size.activated.connect(lambda _i: win.prefs.set("png_size", self.png_size.currentText()))
        form.addRow("大きさ", self.png_size)
        self.png_note = QLineEdit("")
        self.png_note.setPlaceholderText("画像の下端に焼き込む1行（空なら出さない）")
        form.addRow("メモ", self.png_note)
        row = QHBoxLayout()
        self.png_dir = QLabel("")
        self.png_dir.setObjectName("dim")
        self.png_dir.setWordWrap(True)
        row.addWidget(self.png_dir, 1)
        b = QPushButton("変える…")
        b.setObjectName("small")
        b.clicked.connect(self._choose_png_dir)
        row.addWidget(b)
        form.addRow("保存先", row)
        lay.addWidget(g)

        g = QGroupBox("配色・バンドプラン")
        v = QVBoxLayout(g)
        self.colors = QPushButton("ライト・ダークの初期値に戻す…")
        set_enabled(self.colors, REASON_COLORS)
        self.bandplan = QPushButton("バンドプランを表示")
        set_enabled(self.bandplan, REASON_BANDPLAN)
        v.addWidget(self.colors)
        v.addWidget(self.bandplan)
        lay.addWidget(g)
        lay.addStretch(1)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll(inner))
        self.show_png_dir()

    def fill_on(self) -> bool:
        return self.style_fill.isChecked()

    def _style_changed(self, fill: bool) -> None:
        self.win.prefs.set("draw_style", "fill" if fill else "line")
        self.win.redraw_style()

    def png_palette_choice(self) -> str:
        return self.png_palette.currentData()

    def png_size_choice(self) -> tuple[int, int]:
        return tuple(self.png_size.currentData())

    def show_png_dir(self) -> None:
        self.png_dir.setText(str(self.win.files.png_dir()))

    def _choose_png_dir(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "PNG の保存先", str(self.win.files.png_dir()))
        if d:
            self.win.prefs.set("png_dir", str(Path(d)))
            self.show_png_dir()
