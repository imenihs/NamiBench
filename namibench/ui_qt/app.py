"""Qt 画面の起動。"""
from __future__ import annotations

import sys
from typing import Optional

from PySide6.QtCore import QSize
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from ..api.local import create_default
from ..resources import ICON_SIZES, icon_png
from . import wheel_guard
from .main_window import MainWindow
from .prefs import UiPrefs

# Windows のタスクバーでアプリを見分ける名前（付けないと python.exe のアイコンでまとめられる）
APP_USER_MODEL_ID = "NamiBench.NamiBench"


def app_icon() -> QIcon:
    """アプリのアイコン。大きさごとの PNG を全部入れ、Qt が表示する大きさに合うものを選ぶ（小さいときは単純な絵）。"""
    icon = QIcon()
    for s in ICON_SIZES:
        icon.addFile(str(icon_png(s)), QSize(s, s))
    return icon


def _set_app_user_model_id() -> None:
    """Windows のタスクバーに、python のアイコンではなくこのアプリのアイコンを出す（窓を作る前に呼ぶ）。"""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)
    except (AttributeError, OSError):
        pass  # 付けられなくても動く（タスクバーのアイコンが python のものになるだけ）


def main(argv: Optional[list[str]] = None, traffic_log_dir=None, web: Optional[dict] = None) -> int:
    """web: 起動の引数で Web の口を開くとき {"enabled", "port", "lan"}（`--web`・`--lan`: 設計A §4.3.2）。"""
    _set_app_user_model_id()
    app = QApplication(argv if argv is not None else sys.argv)
    app.setWindowIcon(app_icon())  # 主の窓・切り離した窓・ダイアログの全部に効く
    # スクロールする場所の欄は、フォーカスがあるときだけホイールで変える（設計J §4.10 の 1）
    wheel_guard.install(app)
    api = create_default(traffic_log_dir=traffic_log_dir)
    win = MainWindow(api, prefs=UiPrefs.for_app(), web=web)
    win.resize(1600, 1000)
    win.show()
    try:
        return app.exec()
    finally:
        api.close()
