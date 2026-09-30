"""Qt 画面の起動。"""
from __future__ import annotations

import sys
from typing import Optional

from PySide6.QtWidgets import QApplication

from ..api.local import create_default
from .main_window import MainWindow


def main(argv: Optional[list[str]] = None, traffic_log_dir=None) -> int:
    app = QApplication(argv if argv is not None else sys.argv)
    api = create_default(traffic_log_dir=traffic_log_dir)
    win = MainWindow(api)
    win.resize(1600, 1000)
    win.show()
    try:
        return app.exec()
    finally:
        api.close()
