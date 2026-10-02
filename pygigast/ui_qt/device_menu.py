"""メニューの「機器」「校正」「ヘルプ」の画面（設計B §7.3・D §5.6・J §4.6・J §4.7）。"""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from PySide6.QtWidgets import QFileDialog, QMessageBox

from ..core.units import format_freq
from . import menus, texts
from .device_list import DeviceListWindow

if TYPE_CHECKING:
    from .main_window import MainWindow


def show_key_table(win: "MainWindow") -> None:
    rows = menus.key_table(win.actions)
    rows += list(texts.WEB_KEY_HELP)  # 数値の欄のキー（Web の一覧と同じ行: J §4.6）
    text = "\n".join(f"{k}\t{v}" for k, v in rows)
    QMessageBox.information(win, texts.MENU_ITEMS["help_keys"], text + "\n\n" + texts.LABELS["keys_note"])


def show_about(win: "MainWindow") -> None:
    from .. import __version__

    QMessageBox.about(win, texts.MENU_ITEMS["help_about"], texts.w("about", version=__version__))


def open_list(win: "MainWindow") -> DeviceListWindow:
    """機器の一覧を開く（メニュー「機器 → 機器に接続…」・繋がっていないとき: J §4.7）。窓は1つだけ。
    窓の題は開いたメニューの項目の名前と同じ（U-24）。"""
    w = win.device_window
    if w is None:
        w = DeviceListWindow(win)
        win.device_window = w
    w.show()
    w.raise_()
    w.activateWindow()
    return w


def on_devices(win: "MainWindow", payload: dict) -> None:
    """コアのイベント `devices`（挿し抜きで一覧が変わった）。開いている一覧へ渡す。"""
    w = win.device_window
    if w is not None and w.isVisible():
        w.set_devices(payload.get("devices") or [])


def on_state(win: "MainWindow", st: dict, before: Optional[str]) -> None:
    """繋がったら、繋がっていないから開いていた一覧を閉じる（Web など別の画面から繋いだときも）。"""
    w = win.device_window
    now = (st.get("connection") or {}).get("state")
    if w is not None and w.isVisible() and w.busy is None and now == "connected" and before != "connected":
        w.close()


def connect_last(win: "MainWindow") -> None:
    """起動時: 覚えた機器が見つかれば自動で繋ぐ（設計B §7.3）。繋げなければ一覧を開く（J §4.7 の 1）。

    繋いだことはコアの知らせ（auto_connected）が状態バーに出す。
    """
    win.status_note(texts.LABELS["connecting"])
    fut = win.api.call("connect_last")

    def done(f) -> None:
        def in_gui() -> None:
            exc = f.exception()
            if exc is not None:
                win.status_note(texts.error_text(exc))
            elif not (f.result() or {}).get("identity"):
                win.status_note("")
            if (win.state.get("connection") or {}).get("state") != "connected" or exc is not None:
                if (win.state.get("connection") or {}).get("state") != "reconnecting":
                    open_list(win)

        win._bridge.call.emit(in_gui)

    fut.add_done_callback(done)


def disconnect(win: "MainWindow") -> None:
    """利用者が切断したら一覧を開く（次に繋ぐ機器を選べるように: J §4.7 の 1）。"""
    win.call("disconnect", on_ok=lambda r: open_list(win))


def import_legacy(win: "MainWindow") -> None:
    conn = win.state.get("connection", {})
    serial = (conn.get("identity") or {}).get("serial")
    if not serial:
        QMessageBox.information(win, texts.LABELS["legacy_short"], texts.LABELS["legacy_need_device"])
        return
    path, _ = QFileDialog.getOpenFileName(win, texts.LABELS["legacy_title"], "", texts.LABELS["filter_txt"])
    if not path:
        return
    if not win.confirm(texts.LABELS["legacy_short"], texts.w("legacy_confirm", serial=serial), texts.LABELS["do_import"]):
        return
    win.call("import_legacy_calibration", on_ok=lambda r: show_cal_summary(win, r), path=path, serial=serial)


def show_cal_summary(win: "MainWindow", result: dict) -> None:
    s = result["summary"]
    bands = s["freq_response_db"]["bands"]
    lines = [
        texts.w("legacy_sum_file", name=s["source"].get("file_name")),
        texts.w("legacy_sum_level", db=f"{s['level_offset_db']:+.1f}"),
        texts.w("legacy_sum_rbw", list="・".join(f"{format_freq(int(k))} {v:+.1f} dB"
                                                 for k, v in s["rbw_offset_db"].items())),
        texts.LABELS["legacy_sum_freq"],
    ]
    for b in bands:
        lines.append(f"  {format_freq(b['start_hz'])}〜: {min(b['values']):+g} 〜 {max(b['values']):+g} dB")
    QMessageBox.information(win, texts.LABELS["legacy_done"], "\n".join(lines))
