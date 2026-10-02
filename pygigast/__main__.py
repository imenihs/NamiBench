"""`python -m pygigast` の起動（設計A §2.1）。

- `pygigast`: Qt 画面＋コア。`--web` を付けると Web の口も同時に開く（設定の「Web の口」でも開ける）
- `pygigast serve`: 画面なしでコアと Web の口だけ（Raspberry Pi などに繋いでブラウザから使う）

Web の口は既定で自分の PC（127.0.0.1）だけに開く。LAN へ開くのは `--lan` か Qt の設定で明示したときだけで、
そのときは認証が要る（設計A §4.3.2）。`serve --lan` はコンソールに URL・文字の QR・認証番号を出し、
Enter で新しい認証番号、`r` で認証を作り直す。
"""
from __future__ import annotations

import argparse
import sys
from typing import Optional


def _add_web_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--port", type=int, default=None,
                   help="ブラウザから使うときのポート番号（既定: 画面では前回の番号、serve では 8080）")
    p.add_argument("--lan", action="store_true",
                   help="同じネットワークのスマートフォンや PC からも使えるようにする（QR コードか認証番号で入る）")


def serve_main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(prog="pygigast serve", description="画面を出さずに起動し、ブラウザから使う")
    _add_web_args(p)
    p.add_argument("--traffic-log", metavar="DIR", help="機器との送受信を記録するフォルダ（既定は記録しない）")
    p.add_argument("--simulator", action="store_true", help="起動したらシミュレータに繋ぐ（実機が無いときの確認用）")
    args = p.parse_args(argv)
    from .api.local import create_default
    from .api.web.service import WebService, run_console
    from .ui_qt import texts

    api = create_default(traffic_log_dir=args.traffic_log)
    port = args.port or 8080
    service = WebService(api, port=port, lan=args.lan)
    try:
        # 画面が無いので、起動したら繋ぐ（Qt の画面と同じ: 覚えた機器が見つかれば自動で繋ぐ。設計B §7.3）
        if args.simulator:
            api.call("connect", backend="simulator", serial="SIM00001")
        else:
            api.call("connect_last")
        if not service.start():
            print(texts.tpl_web("state_error", reason=texts.ERRORS.get(service.error, service.error)) + f" :{port}",
                  file=sys.stderr)
            return 1
        run_console(service)
    finally:
        service.stop()
        api.close()
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    argv = sys.argv[1:] if argv is None else list(argv)
    if argv and argv[0] == "serve":
        return serve_main(argv[1:])
    p = argparse.ArgumentParser(prog="pygigast")
    p.add_argument("--traffic-log", metavar="DIR", help="機器との送受信を記録するフォルダ（既定は記録しない）")
    p.add_argument("--web", action="store_true", help="デスクトップの画面と一緒に、ブラウザからも使えるようにする")
    _add_web_args(p)
    args, rest = p.parse_known_args(argv)
    from .ui_qt.app import main as qt_main

    web = {"enabled": True, "port": args.port, "lan": args.lan} if (args.web or args.lan) else None
    return qt_main([sys.argv[0], *rest], traffic_log_dir=args.traffic_log, web=web)


if __name__ == "__main__":
    sys.exit(main())
