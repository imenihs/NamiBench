"""右のタブ「表示」（設計J §4.5・P §2・O・I §3）。

- 表示の組み合わせ: スペクトルのみ／＋ウォーターフォール／＋密度（設計P §2: 1操作で切り替える。コアの表示の設定）。
  ゼロスパン（横軸が時間）の間は＋ウォーターフォール・＋密度を理由付きで選べない（display_view.display_tab_view）
- ウォーターフォール・密度とバンドプラン・ブックマークの欄は display_groups.py（設計P §9.2・§9.3・§9.5）
- 描き方: 線／塗りつぶし（旧 Line/Dot。測定値は変わらない）。コアの表示の設定（Qt と Web で同じ・プリセットに入る）。
  内部の漏れの印を出すか（設計L §1.4。同じくコアの表示の設定）
- ゼロスパン（設計N §1）: 表示する長さ（掃引1回・1/10/60 秒）。コアの表示の設定 view.zero_span_s。ゼロスパンでない
  間は理由付きで選べない（U-08）
- 振幅の詳細（設計O）: 単位 dBm／dBμV／dBmV と外部補正 dB。換算はコア（グラフ・マーカー・CSV・PNG・REF が
  同じ単位になる）。「初期値に戻す」
  - 単位の欄は実際に使っている単位を出す（J §4.10 の 2）。基準との比を出している間は「dB（基準との比）」で、
    電力の単位は理由付きで選べない。中身は screen_model.unit_view（Web と同じ）
  - 単位の設定が変わったら（どこから変わっても）欄の下に一度知らせる（J §4.10 の 4。知らせはコアが出す）
  - 電力 → 比に変わったら窓で知らせる。「次から出さない」はこの画面の覚え書き（J §4.10 の 3・設計O §1.1）
  - 単位の下に「S11 の表示」（縦軸の表示の形式: リターンロス（dB）・|Γ|・SWR。設計R §3）。単位と同じく縦軸に1つで、
    S11 で測ったトレースは全部この形式で出る（2026-10-09 ユーザー決定）。中身は screen_s11.axis_format_view（Web と同じ）
- 配色（J §4.4）: ライト・ダークの初期値にするボタン（変えた色の数を言ってから聞く）と、編集の画面への入口
- PNG のメモ1行（大きさ・配色・保存先は設定の画面の「保存」: 同じ値の入口を2つ作らない U-09）
- 大きさと余白は sizes.py の決まり（J §4.11）。名前は欄の左（S-05）、ボタンは文字の幅で左から詰める（S-03）
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from PySide6.QtCore import QTimer
from PySide6.QtGui import QStandardItemModel
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QGroupBox,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QWidget,
)

from ..core.report_layout import PNG_PALETTE_CHOICES, PNG_PALETTE_DEFAULT
from ..core.view_units import LEVEL_UNITS
from . import display_view, screen_model, screen_s11, sizes, texts, widget_sync
from .display_groups import BandplanGroup, WaterfallGroup
from .fields import DbField
from .tab_traces import SameWidth, body, outer, set_enabled

if TYPE_CHECKING:
    from .main_window import MainWindow

V = texts.VIEW_TEXTS
S = texts.SETTINGS_TEXTS
# 単位の欄の下の一度きりの知らせを出しておく長さ（トレースの欄の知らせと同じ）
UNIT_NOTE_MS = 6000
# 画面の覚え書き: 比の表示に変わったら窓で知らせるか（設定の画面の「表示」で戻せる。初期値: 知らせる）
PREF_NOTIFY_RATIO = "notify_ratio_display"


def notify_ratio_on(win: "MainWindow") -> bool:
    return win.prefs.get(PREF_NOTIFY_RATIO, True) is not False


class DisplayTab(QWidget):
    def __init__(self, win: "MainWindow") -> None:
        super().__init__()
        self.win = win
        inner, lay = body()

        L = texts.LABELS
        g = QGroupBox(L["view_combo"])
        v = sizes.vbox(g)
        self.view_group = QButtonGroup(self)
        self.view_spectrum = QRadioButton(L["spectrum_only"])
        self.view_waterfall = QRadioButton(L["plus_waterfall"])
        self.view_density = QRadioButton(L["plus_density"])
        self.view_spectrum.setChecked(True)
        for code, b in (("spectrum", self.view_spectrum), ("waterfall", self.view_waterfall),
                        ("density", self.view_density)):
            self.view_group.addButton(b)
            v.addWidget(b)
            b.clicked.connect(lambda _=False, c=code: win.call("set_view", layout=c))
        lay.addWidget(g)
        self.wf_group = WaterfallGroup(win)
        lay.addWidget(self.wf_group)

        g = QGroupBox(L["draw_style"])
        v = sizes.vbox(g)
        self.style_group = QButtonGroup(self)
        self.style_line = QRadioButton(L["draw_line"])
        self.style_fill = QRadioButton(L["draw_fill"])
        self.style_line.setChecked(True)
        for b in (self.style_line, self.style_fill):
            self.style_group.addButton(b)
            v.addWidget(b)
        self.style_fill.toggled.connect(self._style_changed)
        # 内部の漏れの印（設計L §1.4。コアの表示の設定 view.leak_marks で Qt と Web が共有）
        self.leak_marks = QCheckBox(L["leak_marks"])
        self.leak_marks.setToolTip(L["leak_marks_tip"])
        self.leak_marks.setChecked(True)
        self.leak_marks.toggled.connect(lambda on: win.call("set_view", leak_marks=bool(on)))
        v.addWidget(self.leak_marks)
        lay.addWidget(g)

        Z = texts.ZERO_SPAN_TEXTS
        g = QGroupBox(Z["group"])
        form = sizes.form(g)
        self.zs_length = QComboBox()
        for it in screen_model.zero_span_view({})["items"]:
            self.zs_length.addItem(it["text"], it["value"])
        self.zs_length.activated.connect(
            lambda i: win.call("set_view", field=None, zero_span_s=int(self.zs_length.itemData(i))))
        form.addRow(Z["length"], self.zs_length)
        lay.addWidget(g)

        g = QGroupBox(L["amplitude_detail"])
        form = sizes.form(g)
        self.unit = QComboBox()
        for code, name in LEVEL_UNITS.items():
            self.unit.addItem(name, code)
        # NF の dB を出している間に欄に出す値（設計M。選ぶものではないので、いつも選べない）
        self.unit.addItem(texts.NF_TEXTS["level_unit_nf_item"], screen_model.NF_UNIT)
        # 基準との比を出している間に欄に出す値（同じく選ぶものではない）
        self.unit.addItem(V["level_unit_ratio_item"], screen_model.RATIO_UNIT)
        self.unit.setToolTip(V["level_unit_tip"])
        self.unit.activated.connect(self._unit_chosen)
        form.addRow(V["level_unit"], self.unit)
        self.unit_note = QLabel("")
        self.unit_note.setObjectName("warn")
        self.unit_note.setWordWrap(True)
        self.unit_note.hide()
        self._unit_note_timer = QTimer(self)
        self._unit_note_timer.setSingleShot(True)
        self._unit_note_timer.timeout.connect(lambda: (self.unit_note.setText(""), self.unit_note.hide()))
        form.addRow(self.unit_note)
        # 縦軸の表示の形式（S11 の比: 設計R §3）。位相系は位相を測れない機器では無効にして理由（R §5.1）
        self.axis_format = QComboBox()
        for code in screen_s11.formats.FORMATS:
            self.axis_format.addItem(screen_s11.format_name(code), code)
        self.axis_format.activated.connect(self._axis_format_chosen)
        self._axis_format_key: tuple = ()
        form.addRow(L["axis_format"], self.axis_format)
        # 単位と同じ列の欄は同じ幅（S-02）。出しうる一番長い中身の幅
        self._unit_col = SameWidth([self.unit, self.axis_format], texts.UNIT_COLUMN_SAMPLES)
        self.ext = DbField(V["ext_db"], "dB", lambda v: win.call("set_view", field=self.ext, ext_db=float(v)),
                           big=False)
        self.ext.setToolTip(V["ext_db_tip"])
        self.ext.edit.setPlaceholderText(V["ext_db_placeholder"])
        form.addRow(self.ext.name, self.ext)
        reset = QPushButton(V["reset_amp"])
        reset.clicked.connect(lambda: win.call("set_view", level_unit=None, ext_db=None))
        form.addRow(sizes.buttons_row(reset))
        lay.addWidget(g)

        self.bandplan_group = BandplanGroup(win)
        lay.addWidget(self.bandplan_group)

        g = QGroupBox(L["colors"])
        v = sizes.vbox(g)
        self.reset_light = QPushButton(S["palette_reset_light"])
        self.reset_light.clicked.connect(lambda: win.reset_palette("light"))
        self.reset_dark = QPushButton(S["palette_reset_dark"])
        self.reset_dark.clicked.connect(lambda: win.reset_palette("dark"))
        v.addLayout(sizes.buttons_row(self.reset_light, self.reset_dark))
        self.colors = QPushButton(texts.MENU_ITEMS["view_colors"])
        self.colors.clicked.connect(lambda: win.actions["view_colors"].trigger())
        v.addLayout(sizes.buttons_row(self.colors))
        lay.addWidget(g)

        g = QGroupBox(L["png_group"])
        form = sizes.form(g)
        self.png_note = QLineEdit("")
        self.png_note.setPlaceholderText(L["png_memo_placeholder"])
        sizes.set_width(self.png_note, "name")
        form.addRow(L["memo"], self.png_note)
        lay.addWidget(g)
        lay.addStretch(1)
        outer(self, inner)
        # 比の表示に変わったときの窓（開いている間は同じ窓の文を書き直す。試験が中身を見る）
        self.ratio_box: Optional[QMessageBox] = None

    # ---------------------------------------------------------------- 単位（J §4.10）

    def _unit_chosen(self, i: int) -> None:
        code = self.unit.itemData(i)
        if code in LEVEL_UNITS:
            self.win.call("set_view", level_unit=code)

    def _apply_unit(self, st: dict) -> None:
        """単位の欄に実際に使っている単位を出し、いま選べない単位は理由付きで選べなくする（C §5.14 と同じ決まり）。"""
        u = screen_model.unit_view(st)
        model = self.unit.model()
        if isinstance(model, QStandardItemModel):
            for c in u["choices"]:
                it = model.item(self.unit.findData(c["code"]))
                if it is not None:
                    it.setEnabled(c["enabled"])
                    it.setToolTip(c["tip"])
        widget_sync.set_data(self.unit, u["current"])
        self.unit.setToolTip(u["tip"])

    def _axis_format_chosen(self, i: int) -> None:
        code = self.axis_format.itemData(i)
        if code is not None and code != (self.win.state.get("view") or {}).get("s11_format"):
            self.win.call("set_view", s11_format=code)

    def _apply_axis_format(self, st: dict) -> None:
        """S11 の表示の欄（screen_s11.axis_format_view）: 選んでいる形式・使えない形式（理由付き）・欄ごとの理由。"""
        v = screen_s11.axis_format_view(st)
        key = tuple((it["code"], it["enabled"], it["tip"]) for it in v["items"])
        if key != self._axis_format_key:
            self._axis_format_key = key
            model = self.axis_format.model()
            for it in v["items"]:
                item = model.item(self.axis_format.findData(it["code"])) if isinstance(model, QStandardItemModel) \
                    else None
                if item is not None:
                    # 使えない形式は一覧に残して無効にし、理由をツールチップに（U-08）
                    item.setEnabled(bool(it["enabled"]))
                    item.setToolTip(it["tip"])
        widget_sync.set_data(self.axis_format, v["value"])
        self.axis_format.setEnabled(v["reason"] is None)
        self.axis_format.setToolTip(v["tip"])

    def flash_unit_note(self, text: str) -> None:
        self.unit_note.setText(text)
        self.unit_note.show()
        self._unit_note_timer.start(UNIT_NOTE_MS)

    def on_notice(self, v: dict) -> None:
        """知らせのうち、単位の欄の下に出すもの（単位が変わった）と窓で出すもの（比の表示に変わった）。
        v は texts.notice_view の形（Web と同じ）。状態バーへは呼ぶ側が出す。"""
        if v.get("field") == "unit":
            self.flash_unit_note(v["text"])
        modal = v.get("modal")
        if modal and modal.get("key") == "ratio_display" and notify_ratio_on(self.win):
            self.show_ratio_box(modal)

    def show_ratio_box(self, modal: dict) -> None:
        """［閉じる］と「次から出さない」の窓。open() で出す（画面を止めずに、この窓の上に重ねる）。
        窓は1つを使い回す。開いている間にまた来たら文だけ書き直す。"""
        box = self.ratio_box
        if box is None:
            box = QMessageBox(QMessageBox.Icon.Information, "", "", QMessageBox.StandardButton.Close, self.win)
            box.button(QMessageBox.StandardButton.Close).setText(texts.SETTINGS_LABELS["close"])
            # 親を渡して Qt に持たせる（Python の側の参照が消えても窓の中の欄が消えないように）
            box.setCheckBox(QCheckBox(modal["check"], box))
            box.finished.connect(self._ratio_box_closed)
            self.ratio_box = box
        box.setWindowTitle(modal["title"])
        box.setText(modal["text"])
        if not box.isVisible():
            box.checkBox().setChecked(False)
            box.open()

    def _ratio_box_closed(self, _result: int) -> None:
        box = self.ratio_box
        if box is not None and box.checkBox().isChecked():
            self.win.prefs.set(PREF_NOTIFY_RATIO, False)
            settings = getattr(self.win, "_settings", None)
            if settings is not None:
                settings.apply_notify()

    def apply_state(self, st: dict) -> None:
        """コアの表示の設定（単位・外部補正・描き方）と配色を映す。"""
        v = st.get("view") or {}
        self._apply_unit(st)
        self._apply_axis_format(st)
        lay = v.get("layout") or "spectrum"
        reasons = display_view.display_tab_view(st)["layout_reasons"]
        for code, b in (("spectrum", self.view_spectrum), ("waterfall", self.view_waterfall),
                        ("density", self.view_density)):
            widget_sync.set_checked(b, lay == code)
            set_enabled(b, reasons.get(code))  # ゼロスパンの間は＋ウォーターフォール・＋密度を選べない（理由付き）
        self.wf_group.apply_state(st)
        self.bandplan_group.apply_state(st)
        zv = screen_model.zero_span_view(st)
        widget_sync.set_data(self.zs_length, zv["value"])
        set_enabled(self.zs_length, zv["reason"], texts.ZERO_SPAN_TEXTS["length_tip"])
        widget_sync.set_checked(self.style_fill, v.get("draw_style") == "fill")
        widget_sync.set_checked(self.style_line, v.get("draw_style") != "fill")
        widget_sync.set_checked(self.leak_marks, v.get("leak_marks", True) is not False)
        self.ext.set_value(float(v.get("ext_db") or 0.0))  # 打っている間は欄が書き直さない（fields._Edit）

    def fill_on(self) -> bool:
        return self.style_fill.isChecked()

    def _style_changed(self, fill: bool) -> None:
        self.win.call("set_view", draw_style="fill" if fill else "line")

    # PNG の配色・大きさは設定の画面の「保存」で選ぶ（画面ごとの覚え書き）
    def png_palette_choice(self) -> str:
        """ユーザー指定（"screen"）／ライト／ダーク（I §3.6）。選んでいない・読めない覚え書きは既定のユーザー指定。"""
        code = self.win.prefs.get("png_palette", PNG_PALETTE_DEFAULT)
        return code if code in PNG_PALETTE_CHOICES else PNG_PALETTE_DEFAULT

    def png_size_choice(self) -> tuple[int, int]:
        text = str(self.win.prefs.get("png_size", "1600×1000"))
        try:
            w, h = text.split("×")
            return int(w), int(h)
        except ValueError:
            return 1600, 1000
