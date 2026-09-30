"""メニューの「機器」「校正」「ヘルプ」の画面（設計B §7.3・D §5.6・J §4.6）。"""
from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtWidgets import QDialog, QFileDialog, QMessageBox

from ..core.units import format_freq
from . import menus
from .topbar import ConnectDialog

if TYPE_CHECKING:
    from .main_window import MainWindow


def show_key_table(win: "MainWindow") -> None:
    rows = menus.key_table(win.actions)
    rows += [("↑ / ↓・ホイール（数値の欄）", "カーソルのある桁を増減"),
             ("Enter / Tab / 欄を離れる", "確定"), ("Esc（数値の欄）", "打つ前の値に戻す")]
    text = "\n".join(f"{k}\t{v}" for k, v in rows)
    QMessageBox.information(win, "キー操作の一覧",
                            text + "\n\n入力の欄にいる間は、文字・数字・矢印などのキーは欄に渡します。")


def show_about(win: "MainWindow") -> None:
    QMessageBox.about(win, "pyGigaSt について", "pyGigaSt — GigaSt v5 を PC から操作・表示するアプリ")


def connect_dialog(win: "MainWindow") -> None:
    def show(result: dict) -> None:
        dlg = ConnectDialog(result["devices"], win)
        if dlg.exec() == QDialog.DialogCode.Accepted and dlg.selected():
            d = dlg.selected()
            win.status_note("接続しています…")
            win.call("connect", backend="gigast", serial=d.get("serial"), port=d.get("port"))

    win.call("list_devices", on_ok=show, backend="gigast")


def import_legacy(win: "MainWindow") -> None:
    conn = win.state.get("connection", {})
    serial = (conn.get("identity") or {}).get("serial")
    if not serial:
        QMessageBox.information(win, "旧校正ファイル", "校正を当てる機器を決めるため、先に機器に接続してください。")
        return
    path, _ = QFileDialog.getOpenFileName(win, "旧校正ファイル（gigast.txt / ba205.txt）", "", "テキスト (*.txt);;すべて (*)")
    if not path:
        return
    if not win.confirm("旧校正ファイル", f"このファイルを、接続中の機器（{serial}）の校正として取り込みますか？"):
        return
    win.call("import_legacy_calibration", on_ok=lambda r: show_cal_summary(win, r), path=path, serial=serial)


def show_cal_summary(win: "MainWindow", result: dict) -> None:
    s = result["summary"]
    bands = s["freq_response_db"]["bands"]
    lines = [
        f"元のファイル: {s['source'].get('file_name')}",
        f"全体のレベル: {s['level_offset_db']:+.1f} dB",
        "RBW ごと: " + "・".join(f"{format_freq(int(k))} {v:+.1f} dB" for k, v in s["rbw_offset_db"].items()),
        "周波数特性（band ごとの最小〜最大）:",
    ]
    for b in bands:
        lines.append(f"  {format_freq(b['start_hz'])}〜: {min(b['values']):+g} 〜 {max(b['values']):+g} dB")
    QMessageBox.information(win, "旧校正ファイルを取り込みました", "\n".join(lines))
