"""メニューバーの中身（設計J §4.3.1）。並びと語は texts の MENU_LAYOUT・MENU_ITEMS（Web の「≡」と同じ）。

ここに持つのは Qt のキー（J §4.6）と、項目を押したときに呼ぶ画面の処理だけ。
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Callable, Optional

from . import menus, texts

if TYPE_CHECKING:
    from .main_window import MainWindow

# Qt のキーの初期値（J §4.6）
KEYS = {
    "open": ("Ctrl+O",), "save": ("Ctrl+S",), "export": ("Ctrl+E",), "png_save": ("Ctrl+P",),
    "png_copy": ("Ctrl+Shift+C",), "quit": ("Ctrl+Q",), "undo": ("Ctrl+Z",), "redo": ("Ctrl+Y",),
    "run_cont": ("F5",), "run_single": ("F6",), "stop": ("F7",), "pause": ("Pause",),
    "marker_peak": ("P",), "marker_peak_left": ("[",), "marker_peak_right": ("]",),
    "marker_left": ("Left", "Shift+Left"), "marker_right": ("Right", "Shift+Right"), "marker_here": ("M",),
}
# グラフにいるときだけ効くキー
GRAPH_ONLY = {"marker_peak", "marker_peak_left", "marker_peak_right", "marker_left", "marker_right", "marker_here"}


def handlers(win: "MainWindow") -> dict[str, Callable[[], None]]:
    f = win.files
    return {
        "open": lambda: f.open_files(), "save": f.save, "export": f.export,
        "png_save": f.png_save, "png_copy": f.png_copy,
        "record_start": f.record_start, "record_stop": f.record_stop, "record_finish": f.record_finish,
        "quit": win.close,
        "undo": lambda: win._undo_redo("undo"), "redo": lambda: win._undo_redo("redo"),
        "connect": win._connect_dialog,
        "connect_sim": lambda: win.call("connect", backend="simulator", serial="SIM00001"),
        "disconnect": win._disconnect,
        "run_cont": lambda: win.call("run", mode="continuous"),
        "run_single": lambda: win.call("run", mode="single"),
        "stop": lambda: win.call("stop"),
        "pause": win._toggle_run,
        "ref_auto": win._ref_auto,
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
        "cal_legacy": win._import_legacy,
        "settings": win._settings_dialog,
        "help_keys": win._show_key_table,
        "help_about": win._show_about,
    }


def table(win: "MainWindow") -> list[tuple[str, list[Optional[menus.Item]]]]:
    """menus.build へ渡す並び。まだ作っていない項目は理由付きで無効（U-08）。"""
    h = handlers(win)
    out = []
    for sec, codes in texts.MENU_LAYOUT:
        items: list[Optional[menus.Item]] = []
        if sec == "mode":
            for i, (code, label, name, why) in enumerate(texts.MODES):
                items.append(menus.Item(f"mode_{code}", f"{label}（{name}）", (f"Ctrl+{i + 1}",),
                                        (lambda c=code: win.call("set_mode", mode=c)), why))
        for code in codes:
            if code is None:
                items.append(None)
                continue
            why = texts.NOT_YET.get(texts.MENU_NOT_YET[code]) if code in texts.MENU_NOT_YET else None
            items.append(menus.Item(code, texts.MENU_ITEMS[code], KEYS.get(code, ()),
                                    None if why else h.get(code), why, code in GRAPH_ONLY))
        out.append((f"{texts.MENU_TITLES[sec]}(&{texts.MENU_MNEMONICS[sec]})", items))
    return out
