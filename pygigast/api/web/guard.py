"""Web の口の入口の確かめ（設計A §4.3.2 の 2・4・8・9）。全部の HTTP と WebSocket がここを通る。

順に確かめる:
1. LAN に開いていないのに自分の PC の外から来た → 断る（`lan_closed`）
2. Host が自分の宛先でない（DNS のリバインディング）→ 断る（`bad_host`）。自分の PC からでも同じ
3. Origin があって Host と同じ宛先でない（ほかのサイトのページから）→ 断る（`bad_origin`）
4. 送る形: 命令（POST /api/v1/…）は `application/json`、アップロード（PUT）は `application/octet-stream` だけ
   （ほかのサイトからの単純な送信を通さない）→ 違えば `bad_content_type`
5. 認証: 自分の PC（ループバック）は操作できる。それ以外はクッキーの札が要る。札が無ければ認証の画面
   （と、その画面が使う auth.js・style.css・POST /api/v1/auth）だけを出し、状態も画面の中身も出さない
6. 見るだけの接続: 読むだけの命令のほかは断る（`read_only`）。アップロードも断る

確かめた結果（種類・自分の PC か・IP・札）は scope["state"]["gs"] に入れ、後ろの受け口が使う。
"""
from __future__ import annotations

import html
import json
from typing import Optional

from .access import KIND_OPERATE, Access, cookie_name, host_allowed, is_loopback, origin_allowed

# 見るだけの接続でも使える命令（状態・トレース・表を読むだけで、何も変えない）
READ_COMMANDS = {"get_state", "get_trace", "peak_list", "reference_list", "png_layout", "mouse_reading"}
# 札の無い LAN の接続に出してよいもの（認証の画面だけ）
_PUBLIC_STATIC = {"/auth.js", "/style.css"}
_AUTH_PATH = "/api/v1/auth"


def _headers(scope) -> dict[str, str]:
    out: dict[str, str] = {}
    for k, v in scope.get("headers") or []:
        out.setdefault(k.decode("latin-1").lower(), v.decode("latin-1"))
    return out


def parse_cookies(header: Optional[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for part in (header or "").split(";"):
        k, sep, v = part.strip().partition("=")
        if sep:
            out.setdefault(k.strip(), v.strip())
    return out


def _error_body(code: str) -> bytes:
    from . import view

    return json.dumps(view.error_view({"error": {"code": code, "field": None, "detail": {}}}),
                      ensure_ascii=False).encode("utf-8")


def auth_page() -> bytes:
    """認証の画面（鍵の無い LAN の接続に出す唯一の画面）。文言は texts の表から（J §5）。"""
    from ...ui_qt import texts

    L = texts.WEB_LABELS
    # 失敗の文はサーバーの応答（error.text）にあるので、画面に置くのは「確かめています」だけ
    words = json.dumps({"checking": L["auth_checking"]}, ensure_ascii=False).replace("</", "<\\/")
    e = html.escape
    return f"""<!doctype html>
<html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{e(L["auth_title"])}</title>
<link rel="stylesheet" href="style.css">
</head><body class="auth-body">
<noscript>{e(L["auth_need_js"])}</noscript>
<form class="auth" id="auth" autocomplete="off">
  <h1>pyGigaSt</h1>
  <label for="code">{e(L["auth_prompt"])}</label>
  <input id="code" name="code" inputmode="numeric" pattern="[0-9]*" maxlength="6" autofocus>
  <button type="submit" class="primary">{e(L["auth_button"])}</button>
  <div id="msg" class="warn" role="status"></div>
</form>
<script id="words" type="application/json">{words}</script>
<script src="auth.js"></script>
</body></html>""".encode("utf-8")


class Guard:
    """ASGI の包み。access は起動の間ずっと同じもの（サーバーを開き直しても札は Access が持つ）。"""

    def __init__(self, app, access: Access) -> None:
        self.app = app
        self.access = access

    async def __call__(self, scope, receive, send) -> None:
        kind = scope.get("type")
        if kind not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        h = _headers(scope)
        client = scope.get("client") or ("", 0)
        ip = str(client[0])
        local = is_loopback(ip)
        lan = self.access.lan
        if not local and not lan:
            await self._reject(scope, receive, send, 403, "lan_closed")
            return
        host = h.get("host")
        if not host_allowed(host, scope.get("server"), lan):
            await self._reject(scope, receive, send, 403, "bad_host")
            return
        if not origin_allowed(h.get("origin"), host):
            await self._reject(scope, receive, send, 403, "bad_origin")
            return
        method = scope.get("method", "GET")
        path = scope.get("path", "")
        if kind == "http" and path.startswith("/api/v1/") and method in ("POST", "PUT"):
            ctype = h.get("content-type", "").split(";")[0].strip().lower()
            want = "application/octet-stream" if method == "PUT" else "application/json"
            if ctype != want:
                await self._reject(scope, receive, send, 415, "bad_content_type")
                return

        token = parse_cookies(h.get("cookie")).get(cookie_name(int((scope.get("server") or ("", 0))[1])))
        sess = None if local else self.access.session(token)
        who = KIND_OPERATE if local else (sess.kind if sess else None)
        scope.setdefault("state", {})["gs"] = {"kind": who, "local": local, "ip": ip,
                                               "session": sess.token if sess else None}
        if who is None:
            if kind == "http" and method == "GET" and path in ("/", "/index.html"):
                await self._send(send, 200, auth_page(), "text/html; charset=utf-8")
                return
            if kind == "http" and ((method == "GET" and path in _PUBLIC_STATIC)
                                   or (method == "POST" and path == _AUTH_PATH)):
                await self.app(scope, receive, send)
                return
            await self._reject(scope, receive, send, 401, "auth_required")
            return
        if who != KIND_OPERATE and kind == "http":
            if method == "PUT" or (method == "POST" and path != _AUTH_PATH
                                   and path.removeprefix("/api/v1/") not in READ_COMMANDS):
                await self._reject(scope, receive, send, 403, "read_only")
                return
        await self.app(scope, receive, send)

    async def _reject(self, scope, receive, send, status: int, code: str) -> None:
        if scope.get("type") == "websocket":
            msg = await receive()
            if msg.get("type") == "websocket.connect":
                await send({"type": "websocket.close", "code": 4000 + status, "reason": code})
            return
        await self._send(send, status, _error_body(code), "application/json")

    @staticmethod
    async def _send(send, status: int, body: bytes, ctype: str) -> None:
        await send({"type": "http.response.start", "status": status,
                    "headers": [(b"content-type", ctype.encode()), (b"content-length", str(len(body)).encode()),
                                (b"cache-control", b"no-store")]})
        await send({"type": "http.response.body", "body": body})
