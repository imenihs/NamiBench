"""メニューバーの中身（設計J §4.3.1）。並びと語は texts の MENU_LAYOUT・MENU_ITEMS（Web の「≡」と同じ）。

ここに持つのは項目を押したときに呼ぶ画面の処理だけ。キーは texts の QT_KEY_DEFAULTS（J §4.6 の初期値）に
利用者が変えた分（画面の覚え書きの "keys"）を重ねたもの（keymap.py）。
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Callable, Optional

from . import keymap, menus, texts

if TYPE_CHECKING:
    from .main_window import MainWindow

# グラフにいるときだけ効くキー
GRAPH_ONLY = set(texts.GRAPH_ONLY_KEYS)


def handlers(win: "MainWindow") -> dict[str, Callable[[], None]]:
    f = win.files
    return {
        "open": lambda: f.open_files(), "save": f.save, "export": f.export,
        "png_save": f.png_save, "png_copy": f.png_copy,
        "presets": win.open_presets, "preset_save": win.save_preset,
        "record_start": f.record_start, "record_stop": f.record_stop, "record_finish": f.record_finish,
        "quit": win.close,
        "undo": lambda: win._undo_redo("undo"), "redo": lambda: win._undo_redo("redo"),
        "connect": win._connect_dialog,
        "disconnect": win._disconnect,
        "run_cont": lambda: win.call("run", mode="continuous"),
        "run_single": lambda: win.call("run", mode="single"),
        "stop": lambda: win.call("stop"),
        "pause": win._toggle_run,
        "ref_auto": win._ref_auto,
        "marker_polarity": lambda: win.marker_panel.toggle_polarity(),
        "marker_peak": lambda: win._peak("highest"),
        "marker_peak_left": lambda: win._peak("left"),
        "marker_peak_right": lambda: win._peak("right"),
        "marker_peak_lower": lambda: win._peak("lower"),
        "marker_left": lambda: win._marker_step(-1),
        "marker_right": lambda: win._marker_step(+1),
        "marker_here": win._marker_at_mouse,
        "marker_add": win._marker_add_peak,
        "marker_center": lambda: win._selected_to("center"),
        "marker_ref": lambda: win._selected_to("ref"),
        "marker_span": lambda: win._selected_to("span"),
        "marker_listen": lambda: win.listen.start_selected(),
        "listen_stop": lambda: win.listen.stop(),
        "marker_remove": win._marker_remove,
        "marker_clear": win._marker_clear,
        "marker_copy": win._marker_copy,
        "cal_freq": lambda: win.open_calibration("freq"),
        "cal_rbw": lambda: win.open_calibration("rbw"),
        "cal_nf": lambda: win.open_calibration("nf"),
        "cal_pluto_gain": lambda: win.open_calibration("pluto_gain"),
        "cal_legacy": lambda: win.open_calibration("legacy"),
        "view_colors": lambda: win._settings_dialog("display"),
        "playback_extract": lambda: win.display.extract(),
        "playback_export_csv": lambda: win.display.export_record(),
        "view_waterfall": lambda: win.display.toggle_layout("waterfall"),
        "view_density": lambda: win.display.toggle_layout("density"),
        "layout_reset": lambda: win.parts.reset(),
        "settings": win._settings_dialog,
        "settings_keys": lambda: win._settings_dialog("keys"),
        "web_connect": lambda: win.web.show_connect(),
        "help_keys": win._show_key_table,
        "help_about": win._show_about,
    }


def table(win: "MainWindow") -> list[tuple[str, list[Optional[menus.Item]]]]:
    """menus.build へ渡す並び。まだ作っていない項目は理由付きで無効（U-08）。"""
    h = handlers(win)
    km = keymap.effective(keymap.load_overrides(win.prefs.get("keys")))
    out = []
    for sec, codes in texts.MENU_LAYOUT:
        items: list[Optional[menus.Item]] = []
        if sec == "mode":
            for code, label, name, why in texts.MODES:
                c = f"mode_{code}"
                items.append(menus.Item(c, f"{label}（{name}）", keymap.sequences(c, km.get(c, "")),
                                        (lambda m=code: win.call("set_mode", mode=m)), why))
        for code in codes:
            if code is None:
                items.append(None)
                continue
            why = texts.NOT_YET.get(texts.MENU_NOT_YET[code]) if code in texts.MENU_NOT_YET else None
            items.append(menus.Item(code, texts.MENU_ITEMS[code], keymap.sequences(code, km.get(code, "")),
                                    None if why else h.get(code), why, code in GRAPH_ONLY))
        out.append((f"{texts.MENU_TITLES[sec]}(&{texts.MENU_MNEMONICS[sec]})", items))
    return out
