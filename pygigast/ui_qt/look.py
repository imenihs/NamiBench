"""Qt の画面の見た目と操作の設定を映す（配色: 設計J §4.4・キー: J §4.6・表示の設定: 設計O）。

main_window.py から呼ぶ（1ファイル 1000 行の決まりで分けた）。
- 配色: コアの状態の palette（Qt と Web で共有）を、画面の配色の辞書（win.pal）へ写して全部の部品を描き直す。
  win.pal は部品（トレース・帯・マーカー）が同じ辞書を見ているので、中身を入れ替える
- キー: 画面の覚え書きの "keys" を重ねた割り当てを、メニューの項目（QAction）へ付け直す。項目の横と
  ツールチップの表示も同じに変わる（U-12）
- 表示の設定: 周波数オフセット（横軸の目盛と名前・欄）・描き方（線・塗りつぶし）・表示のタブ・
  マーカーの機能の線を出すか（G §3.6）
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtWidgets import QApplication

from . import keymap, marker_rows, menus, screen_model, source_view, texts, theme
from .theme import stylesheet

if TYPE_CHECKING:
    from .main_window import MainWindow

# 周波数オフセットで換算する欄（Span・SG は換算しない: Span は幅、SG は機器が出す周波数）
OFFSET_FIELDS = ("f_center", "f_start", "f_stop")


def apply_palette(win: "MainWindow", colors: dict) -> None:
    """配色を入れ替えて描き直す（変えた結果はその場で画面に出る: J §4.4）。"""
    if not colors or colors == win.pal:
        return
    win.pal.clear()
    win.pal.update(colors)
    theme.install(QApplication.instance(), win.pal)
    sheet = stylesheet(win.pal)
    win.setStyleSheet(sheet)
    for det in (win.parts.graph, win.parts.peaks):
        if det.window is not None:
            det.window.setStyleSheet(sheet)
    win.plot.setBackground(win.pal["graph_bg"])
    win._style_axes()
    win._update_y_axis()
    win.traces.restyle()
    win.mark_bands.restyle()
    win.marker_layer.restyle()
    win._show_markers()
    win.span_lock.set_colors(win.pal["accent"], win.pal["text_dim"])
    win.trace_tab.restyle()


def reset_palette(win: "MainWindow", base: str) -> None:
    """ライト・ダークのボタン: 変えた色があるときだけ、何個失われるかを言ってから聞く（J §4.4・U-18）。"""
    q = screen_model.palette_reset_question(win.state, base)
    if q is not None and not win.confirm(texts.SETTINGS_TEXTS["palette_reset_title"], q, texts.LABELS["do_restore"]):
        return
    win.call("palette_reset", base=base)


def apply_keymap(win: "MainWindow") -> None:
    """割り当てをメニューの項目へ付け直す（ボタンのツールチップも）。"""
    km = keymap.effective(keymap.load_overrides(win.prefs.get("keys")))
    for code, act in win.actions.items():
        if code in km:
            menus.set_keys(act, keymap.sequences(code, km[code]))
    win._wire_buttons()
    if win.state:
        win._apply_state(dict(win.state))


def apply_view(win: "MainWindow", st: dict) -> None:
    """表示の設定（周波数オフセット・描き方）と配色を状態から映す。"""
    apply_palette(win, (st.get("palette") or {}).get("colors") or {})
    off = texts.freq_offset_of(st)
    for name in OFFSET_FIELDS:
        getattr(win, name).set_offset(off)
    win.plot.getPlotItem().getAxis("bottom").set_offset(off)
    win.mouse_reading.offset_fn = lambda: texts.freq_offset_of(win.state)
    # 内部の漏れの印（設計L §1.4）: グラフの上端の ▼ と、マウスの位置の読み値の説明（Web と同じ source_view）
    leaks = source_view.leak_marks_view(st)
    win.mark_bands.set_leaks(leaks)
    win.mouse_reading.leaks_fn = lambda: source_view.leak_marks_view(win.state)
    win.display_tab.apply_state(st)
    fill = (st.get("view") or {}).get("draw_style") == "fill"
    if fill != win.traces.fill:
        win.redraw_style()
    # マーカーの機能の線（G §3.6）。切り替えはマーカーのタブにあり、掃引していなくてもその場で描き直す
    win.marker_tab.apply_view(st)
    lines = marker_rows.function_lines_on(st)
    if lines != win.marker_layer.function_lines:
        win.marker_layer.function_lines = lines
        win._show_markers()


def freq_axis_label(st: dict) -> str:
    off = texts.freq_offset_of(st)
    if not off:
        return texts.LABELS["freq"]
    return texts.VIEW_TEXTS["axis_freq_offset"].replace("{off}", texts.offset_text(off))

