"""Web の口を開く・閉じる・LAN に開く（設計A §4.3.2 の 1・5・6）。Qt の画面と `serve` の両方が使う。

- 既定は自分の PC（127.0.0.1）だけ。LAN に開くと 0.0.0.0 で開き直す（LAN を OFF にしたら 127.0.0.1 で開き直す）。
  開き直すと、つながっている全部の画面が切れる（OFF のときは「全部を切って自分の PC だけに戻す」: 6）
- 鍵・認証番号・覚えたブラウザ（Access）と web_files（WebFiles）は、開き直しても同じものを使う（起動の間ずっと）
- `serve` のコンソール: 起動したら URL と文字の QR と認証番号を出す。Enter で新しい認証番号、`r` で認証を作り直す
  （console_command。試験できるように関数にしてある）
"""
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path
from typing import Callable, Optional

from ..local import LocalApi
from . import qr
from .access import KIND_OPERATE, KIND_VIEW, Access
from .routes_access import connection_info
from .server import TRACE_RATE_HZ, create_app
from .webfiles import WebFiles

DEFAULT_PORT = 8080
START_TIMEOUT_S = 10.0


class WebService:
    def __init__(self, api: LocalApi, port: int = DEFAULT_PORT, lan: bool = False,
                 trace_rate_hz: float = TRACE_RATE_HZ, files_root: Optional[Path] = None) -> None:
        self.api = api
        self.port = int(port)
        self.trace_rate_hz = trace_rate_hz
        self.access = Access(lan=lan)
        self.files = WebFiles(Path(files_root) if files_root else Path(api.data_root) / "web_files")
        # 起動のたびに空にする（A §4.3.2 の 3: 1回の起動の間だけ置く）
        self.files.reset()
        self._server = None
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.RLock()
        self.error: Optional[str] = None
        self.bound_port: Optional[int] = None

    # ---- 状態
    @property
    def lan(self) -> bool:
        return self.access.lan

    @property
    def running(self) -> bool:
        return self._server is not None and self._thread is not None and self._thread.is_alive()

    def local_url(self) -> str:
        return f"http://127.0.0.1:{self.bound_port or self.port}/"

    def on_change(self, fn: Callable[[], None]) -> Callable[[], None]:
        return self.access.on_change(fn)

    # ---- 開く・閉じる
    def start(self) -> bool:
        """開く。開けなければ error に理由を入れて False（ポートが使われているなど）。"""
        import uvicorn

        with self._lock:
            if self.running:
                return True
            host = "0.0.0.0" if self.access.lan else "127.0.0.1"
            app = create_app(self.api, trace_rate_hz=self.trace_rate_hz, access=self.access, files=self.files)
            cfg = uvicorn.Config(app, host=host, port=self.port, log_level="critical", lifespan="on", ws="auto")
            server = uvicorn.Server(cfg)
            th = threading.Thread(target=server.run, name="namibench-web", daemon=True)
            th.start()
            end = time.monotonic() + START_TIMEOUT_S
            while not server.started and th.is_alive() and time.monotonic() < end:
                time.sleep(0.02)
            if not server.started:
                server.should_exit = True
                th.join(2)
                self.error = "web_port_in_use"
                self._server = self._thread = None
                self.access._changed()
                return False
            self.error = None
            self._server, self._thread = server, th
            try:
                self.bound_port = server.servers[0].sockets[0].getsockname()[1]
            except Exception:  # noqa: BLE001
                self.bound_port = self.port
        self.access._changed()
        return True

    def stop(self) -> None:
        with self._lock:
            server, th = self._server, self._thread
            self._server = self._thread = None
        if server is not None:
            self.access.disconnect_all()
            server.should_exit = True
            if th is not None:
                th.join(10)
        self.access._changed()

    def set_lan(self, on: bool) -> bool:
        """LAN に開く・自分の PC だけに戻す。開いていれば開き直す（つながっている全部が切れる）。"""
        on = bool(on)
        if on == self.access.lan:
            return self.running
        was = self.running
        self.stop()
        self.access.set_lan(on)
        return self.start() if was or on else True

    def set_port(self, port: int) -> bool:
        port = int(port)
        if port == self.port and self.running:
            return True
        was = self.running
        self.stop()
        self.port = port
        self.bound_port = None
        return self.start() if was else True

    def regenerate(self) -> None:
        self.access.regenerate()

    def connection_info(self, new_code: bool = True, addresses=None) -> dict:
        return connection_info(self.access, self.bound_port or self.port, new_code, addresses)

    def status(self) -> dict:
        return {"running": self.running, "lan": self.lan, "port": self.bound_port or self.port,
                "error": self.error, "counts": self.access.counts(), "conns": self.access.conns()}


# ---------------------------------------------------------------------------
# serve のコンソール
# ---------------------------------------------------------------------------


def _can_write(text: str, stream=None) -> bool:
    enc = getattr(stream or sys.stdout, "encoding", None) or "ascii"
    try:
        text.encode(enc)
        return True
    except (UnicodeEncodeError, LookupError):
        return False


def console_block(service: WebService, info: Optional[dict] = None, unicode_blocks: Optional[bool] = None) -> str:
    """起動したとき・作り直したときに出す: URL・文字の QR（操作できる・見るだけ）・認証番号。"""
    from ...ui_qt import texts

    L = texts.WEB_LABELS
    if not service.lan:
        return texts.tpl_web("console_local_only", url=service.local_url())
    info = info or service.connection_info()
    if unicode_blocks is None:
        unicode_blocks = _can_write("▀▄█")
    lines = [L["console_title"], texts.tpl_web("console_local", url=service.local_url())]
    if not info.get("address"):
        lines.append(L["console_no_address"])
        return "\n".join(lines)
    for kind, key in ((KIND_OPERATE, "console_operate"), (KIND_VIEW, "console_view")):
        url = info["urls"][kind]
        lines.append("")
        lines.append(texts.tpl_web(key, url=url))
        lines.append(qr.to_text(qr.encode(url, "M"), unicode_blocks))
    lines.append("")
    lines.append(code_line(info))
    lines.append(L["console_keys"])
    return "\n".join(lines)


def code_line(info: dict) -> str:
    from ...ui_qt import texts

    return texts.tpl_web("console_code", code=info.get("code") or "", s=info.get("valid_s", 0),
                         plain=info.get("plain") or "")


def console_command(service: WebService, line: str, addresses=None) -> str:
    """コンソールの1行: 空（Enter）なら新しい認証番号、`r` なら認証を作り直して全部を出し直す。"""
    from ...ui_qt import texts

    cmd = line.strip().lower()
    if not service.lan:
        return texts.tpl_web("console_local_only", url=service.local_url())
    if cmd == "":
        return code_line(service.connection_info(new_code=True, addresses=addresses))
    if cmd == "r":
        service.regenerate()
        info = service.connection_info(new_code=False, addresses=addresses)
        return texts.WEB_LABELS["console_regenerated"] + "\n" + console_block(service, info)
    return texts.WEB_LABELS["console_keys"]


def run_console(service: WebService, stdin=None, out: Callable[[str], None] = None) -> None:
    """`serve` の前で回す。止めるのは Ctrl+C。標準入力が無い（閉じている）ときは待つだけ。"""
    stdin = stdin or sys.stdin
    out = out or (lambda s: print(s, flush=True))
    out(console_block(service))
    try:
        while True:
            line = stdin.readline() if stdin is not None else ""
            if line == "":
                while service.running:
                    time.sleep(0.5)
                return
            out(console_command(service, line))
    except KeyboardInterrupt:
        return
