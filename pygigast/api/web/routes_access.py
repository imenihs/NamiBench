"""認証・接続のしかた・ダウンロード・アップロードの受け口（設計A §4.3.2 の 2・3）。

- `POST /api/v1/auth`: `{"key": …}`（QR・URL の鍵）か `{"code": "123456"}`（認証番号）。通ればブラウザごとの札を
  クッキー（HttpOnly・SameSite=Strict・この起動の間だけ）で渡す
- `POST /api/v1/access_info`: 操作できる接続のブラウザが「接続のしかた」を出す（QR 2つ・URL・新しい認証番号）
- `GET /api/v1/files/{file_id}`: 名前を付けて作らせたファイル（web_files の中だけ）をダウンロード
- `PUT /api/v1/uploads?name=…`: 本文がファイルそのもの（application/octet-stream）。2GB まで → `upload_id`

入口の確かめ（Host・Origin・送る形・札・見るだけ）は Guard が済ませている。
"""
from __future__ import annotations

from typing import Callable

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse

from . import qr
from .access import KIND_OPERATE, KINDS, Access, cookie_name, urls
from .webfiles import MAX_UPLOAD_BYTES, WebFiles

COOKIE_MAX_AGE_S = 30 * 24 * 3600  # 札の無効はアプリの側で決める（起動し直し・作り直し・切断・LAN を OFF）


def connection_info(access: Access, port: int, new_code: bool = True, addresses=None) -> dict:
    """接続のしかたの中身（Qt の窓・Web の ≡・serve のコンソールで同じもの）。new_code で新しい認証番号を出す。"""
    if new_code:
        access.new_code()
    cur = access.current_code()
    u = urls(port, access.keys, addresses)
    out = {"lan": access.lan, "address": u["address"], "plain": u["plain"],
           "code": cur[0] if cur else None, "valid_s": int(cur[1]) if cur else 0,
           "urls": {k: u[k] for k in KINDS}, "qr_svg": {}}
    for k in KINDS:
        if u[k]:
            out["qr_svg"][k] = qr.to_svg(qr.encode(u[k], "M"), 5)
    return out


def add_access_routes(app: FastAPI, access: Access, files: WebFiles, gs: Callable, fail: Callable) -> None:
    @app.post("/api/v1/auth")
    async def auth(request: Request) -> JSONResponse:
        who = gs(request)
        if who.get("local"):
            return JSONResponse({"kind": KIND_OPERATE, "local": True})
        try:
            body = await request.json()
        except ValueError:
            body = None
        if not isinstance(body, dict):
            return fail("bad_json")
        ip = who.get("ip", "")
        if "key" in body:
            sess = access.login_key(body.get("key"), ip)
            if sess is None:
                return fail("bad_key")
        else:
            sess, result = access.login_code(body.get("code"), ip)
            if sess is None:
                return fail(result, "code", {"retry_after_s": int(access.blocked_for(ip) + 0.999)})
        res = JSONResponse({"kind": sess.kind, "local": False})
        port = int(request.scope["server"][1])
        # 同じサイトにだけ送る・スクリプトから読めない。札はアプリの起動の間だけ有効（次の起動では知らない札）
        # なので、ブラウザを閉じても同じ起動の間は聞き直さないよう、クッキーは残しておく（A §4.3.2 の 2）
        res.set_cookie(cookie_name(port), sess.token, httponly=True, samesite="strict", path="/",
                       max_age=COOKIE_MAX_AGE_S)
        return res

    @app.post("/api/v1/access_info")
    async def access_info(request: Request) -> JSONResponse:
        if not access.lan:
            return fail("lan_off")
        return JSONResponse(connection_info(access, int(request.scope["server"][1])))

    @app.get("/api/v1/files/{file_id}")
    async def download(file_id: str):
        f = files.get(file_id)
        if f is None:
            return fail("unknown_file", "file_id")
        if not f.ready:
            return fail("file_not_ready", "file_id")
        return FileResponse(str(f.path), filename=f.name, media_type="application/octet-stream")

    @app.put("/api/v1/uploads")
    async def upload(request: Request) -> JSONResponse:
        size_hdr = request.headers.get("content-length")
        if size_hdr and size_hdr.isdigit() and int(size_hdr) > MAX_UPLOAD_BYTES:
            return fail("upload_too_large", None, {"max_bytes": MAX_UPLOAD_BYTES})
        uid, path = files.new_upload(request.query_params.get("name"))
        n = 0
        try:
            with open(path, "wb") as fp:
                async for chunk in request.stream():
                    n += len(chunk)
                    if n > MAX_UPLOAD_BYTES:
                        raise OverflowError
                    fp.write(chunk)
        except OverflowError:
            files.upload_failed(path)
            return fail("upload_too_large", None, {"max_bytes": MAX_UPLOAD_BYTES})
        except Exception:  # noqa: BLE001 — 途中で切れた
            files.upload_failed(path)
            raise
        files.upload_done(uid, path)
        return JSONResponse({"upload_id": uid, "name": path.name, "size": n})
