"""setup.bat の最後の動作確認。venv の Python で呼ぶ。

1. 使うパッケージとアプリが読める
2. Qt の画面の部品が作れる（画面は出さない）
3. 画面なしで起動し（シミュレータに繋ぐ）、ブラウザの口が応答する。利用者の設定に触らないよう、APPDATA と HOME を
   一時のフォルダに向けて起動する
4. libiio（PlutoSDR）が読める。無い・読めないときは注意だけ（GigaSt・RTL-SDR は使える）

終了コード: 0 = 使える、1 = 使えない。
"""
from __future__ import annotations

import ctypes
import importlib
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MODULES = ("numpy", "h5py", "pyqtgraph", "serial", "fastapi", "uvicorn", "PySide6.QtWidgets", "namibench",
           "namibench.ui_qt.main_window", "namibench.api.web.service")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def check_imports() -> bool:
    for m in MODULES:
        try:
            importlib.import_module(m)
        except Exception as e:  # noqa: BLE001 — どの失敗でも、何が読めないかを出す
            print(f"NG  import {m}: {e!r}")
            return False
    print("ok  packages")
    return True


def check_qt() -> bool:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    try:
        from PySide6.QtWidgets import QApplication, QWidget

        app = QApplication.instance() or QApplication([])
        QWidget().resize(100, 100)
        app.processEvents()
    except Exception as e:  # noqa: BLE001
        print(f"NG  Qt: {e!r}")
        return False
    print("ok  Qt")
    return True


def check_serve(timeout_s: float = 60.0) -> bool:
    port = _free_port()
    with tempfile.TemporaryDirectory() as tmp:
        env = dict(os.environ, APPDATA=tmp, HOME=tmp, USERPROFILE=tmp, QT_QPA_PLATFORM="offscreen")
        log = Path(tmp) / "serve.log"
        with open(log, "wb") as out:
            p = subprocess.Popen([sys.executable, "-m", "namibench", "serve", "--simulator", "--port", str(port)],
                                 cwd=ROOT, env=env, stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT)
            try:
                end = time.monotonic() + timeout_s
                while time.monotonic() < end:
                    if p.poll() is not None:
                        break
                    try:
                        with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=2) as r:
                            if r.status == 200:
                                print(f"ok  serve (browser page on port {port})")
                                return True
                    except OSError:
                        time.sleep(0.5)
            finally:
                p.kill()
                p.wait(10)
        print("NG  serve did not answer. Its output:")
        print(log.read_text(encoding="utf-8", errors="replace")[-3000:])
        return False


def check_libiio() -> None:
    dll = ROOT / "libiio" / "libiio.dll"
    if sys.platform != "win32":
        return
    if not dll.exists():
        print("note libiio is missing: PlutoSDR cannot be used (run setup.bat again with internet access)")
        return
    try:
        with os.add_dll_directory(str(dll.parent)):
            ctypes.CDLL(str(dll))
    except OSError as e:
        print(f"note libiio could not be loaded ({e}): PlutoSDR cannot be used")
        return
    print("ok  libiio")


def main() -> int:
    ok = check_imports() and check_qt() and check_serve()
    check_libiio()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
