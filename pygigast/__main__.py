"""`python -m pygigast` で Qt 画面を起動する（設計A §2.1）。"""
from __future__ import annotations

import argparse
import sys


def main() -> int:
    p = argparse.ArgumentParser(prog="pygigast")
    p.add_argument("--traffic-log", metavar="DIR", help="機器との送受信を記録するフォルダ（既定は記録しない）")
    args, rest = p.parse_known_args()
    from .ui_qt.app import main as qt_main

    return qt_main([sys.argv[0], *rest], traffic_log_dir=args.traffic_log)


if __name__ == "__main__":
    sys.exit(main())
