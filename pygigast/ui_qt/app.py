"""Qt 画面の起動。"""
from __future__ import annotations

import sys
from typing import Optional

from PySide6.QtWidgets import QApplication

from ..api.local import create_default
from . import wheel_guard
from .main_window import MainWindow
from .prefs import UiPrefs


def main(argv: Optional[list[str]] = None, traffic_log_dir=None, web: Optional[dict] = None) -> int:
    """web: 起動の引数で Web の口を開くとき {"enabled", "port", "lan"}（`--web`・`--lan`: 設計A §4.3.2）。"""
    app = QApplication(argv if argv is not None else sys.argv)
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
