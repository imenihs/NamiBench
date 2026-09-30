"""`python -m pygigast` の起動（設計A §2.1）。

- `pygigast`: Qt 画面＋コア。`--web` を付けると Web の口も同時に開く（同じ機器を別のブラウザからも見られる）
- `pygigast serve`: 画面なしでコアと Web の口だけ（Raspberry Pi などに繋いでブラウザから使う）

Web の口は既定で自分の PC（127.0.0.1）だけに開く。LAN へ開くのは `--lan` を付けたときだけ
（Web の口に認証が無いため: 設計A §4.3 の未決）。
"""
from __future__ import annotations

import argparse
import sys
from typing import Optional


def _add_web_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--host", default=None, help="Web の口を開くアドレス（既定 127.0.0.1。--lan のときは 0.0.0.0）")
    p.add_argument("--port", type=int, default=8080, help="Web の口のポート（既定 8080）")
    p.add_argument("--lan", action="store_true", help="LAN の他の PC からも繋げるようにする（認証はありません）")


def _host(args) -> str:
    return args.host or ("0.0.0.0" if args.lan else "127.0.0.1")


def _print_url(host: str, port: int) -> None:
    shown = "127.0.0.1" if host in ("0.0.0.0", "::") else host
    print(f"pyGigaSt の Web 画面: http://{shown}:{port}/", flush=True)
    if host in ("0.0.0.0", "::"):
        print("LAN の他の PC からは、この PC のアドレスで開いてください（認証はありません）", flush=True)


def serve_main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(prog="pygigast serve", description="画面なしでコアと Web の口だけを開く")
    _add_web_args(p)
    p.add_argument("--traffic-log", metavar="DIR", help="機器との送受信を記録するフォルダ（既定は記録しない）")
    p.add_argument("--simulator", action="store_true", help="起動したらシミュレータに繋ぐ（実機が無いときの確認用）")
    args = p.parse_args(argv)
    host = _host(args)
    from .api.local import create_default
    from .api.web.server import check_host, serve

    try:
        check_host(host, args.lan)
    except ValueError as e:
        p.error(str(e))
    api = create_default(traffic_log_dir=args.traffic_log)
    try:
        # 画面が無いので、起動したら繋ぐ（Qt の画面と同じ: 覚えた機器が見つかれば自動で繋ぐ。設計B §7.3）
        if args.simulator:
            api.call("connect", backend="simulator", serial="SIM00001")
        else:
            api.call("connect_last")
        _print_url(host, args.port)
        serve(api, host=host, port=args.port, lan=args.lan)
    finally:
        api.close()
    return 0


def qt_with_web(rest: list[str], traffic_log_dir: Optional[str], host: str, port: int, lan: bool) -> int:
    """Qt と同時に Web の口を開く。機器を持つのは1つのコアで、Qt と Web はその利用者（設計A §1）。"""
    from PySide6.QtWidgets import QApplication

    from .api.local import create_default
    from .api.web.server import run_in_thread
    from .ui_qt.main_window import MainWindow

    app = QApplication([sys.argv[0], *rest])
    api = create_default(traffic_log_dir=traffic_log_dir)
    server, thread = run_in_thread(api, host=host, port=port, lan=lan)
    _print_url(host, port)
    win = MainWindow(api)
    win.resize(1600, 1000)
    win.show()
    try:
        return app.exec()
    finally:
        server.should_exit = True
        thread.join(5)
        api.close()


def main(argv: Optional[list[str]] = None) -> int:
    argv = sys.argv[1:] if argv is None else list(argv)
    if argv and argv[0] == "serve":
        return serve_main(argv[1:])
    p = argparse.ArgumentParser(prog="pygigast")
    p.add_argument("--traffic-log", metavar="DIR", help="機器との送受信を記録するフォルダ（既定は記録しない）")
    p.add_argument("--web", action="store_true", help="Qt 画面と同時に Web の口も開く")
    _add_web_args(p)
    args, rest = p.parse_known_args(argv)
    if args.web:
        from .api.web.server import check_host

        host = _host(args)
        try:
            check_host(host, args.lan)
        except ValueError as e:
            p.error(str(e))
        return qt_with_web(rest, args.traffic_log, host, args.port, args.lan)
    from .ui_qt.app import main as qt_main

    return qt_main([sys.argv[0], *rest], traffic_log_dir=args.traffic_log)


if __name__ == "__main__":
    sys.exit(main())
