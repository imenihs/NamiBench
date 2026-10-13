"""Web の口を LAN に開くとき（設計A §4.3.2・実装リスト ⑪）。

- 入口の確かめ（認証・見るだけ・Host・Origin・送る形）は、LAN の別の機器から来たことにした ASGI の要求で
  Guard と本物のアプリを直接呼んで確かめる（LAN のアドレスが無い PC でも回る）
- パスを受け取らない形（ダウンロード・アップロード）は、本物の uvicorn（127.0.0.1）とシミュレータで通す
- 本当に LAN のアドレスで繋ぐ試験は、この PC に LAN のアドレスがあるときだけ（0.0.0.0 に開いて、そのアドレスで繋ぐ）
"""
from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

from namibench.api.local import LocalApi, _SimulatorBackend
from namibench.api.web import access as acc
from namibench.api.web import routes_access, server as web
from namibench.api.web import webfiles
from namibench.api.web.service import WebService, console_block, console_command
from namibench.core.devices.base import load_profile
from namibench.core.instrument import GIGAST_PROFILE_PATH, Instrument
from namibench.ui_qt import texts
from tests.test_web_server import _Server

LAN_CLIENT = ("192.168.1.50", 50000)
LAN_SERVER = ("192.168.1.10", 8080)
HOST = "192.168.1.10:8080"
PROMPT = "NamiBench の PC に出ている6桁の認証番号を入れてください"


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


def make_api(root: Path) -> LocalApi:
    profile = load_profile(GIGAST_PROFILE_PATH)
    sim = _SimulatorBackend(profile)
    sim.speed = 0.05
    return LocalApi(Instrument({"simulator": sim}, profile=profile, data_root=root / "data", cal_root=root / "cal"))


def asgi(app, method: str, path: str, headers: dict | None = None, body: bytes = b"",
         client=LAN_CLIENT, server=LAN_SERVER, query: bytes = b"") -> tuple[int, dict, bytes]:
    """ASGI の HTTP の要求を1つ送って (状態, 見出し, 本文) を返す。"""
    h = {"host": f"{server[0]}:{server[1]}", **{k.lower(): v for k, v in (headers or {}).items()}}
    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": method, "scheme": "http",
             "path": path, "raw_path": path.encode(), "query_string": query, "root_path": "",
             "headers": [(k.encode(), v.encode()) for k, v in h.items() if v is not None],
             "client": client, "server": server}
    msgs = [{"type": "http.request", "body": body, "more_body": False}]
    out: list[dict] = []

    async def receive():
        return msgs.pop(0) if msgs else {"type": "http.disconnect"}

    async def send(m):
        out.append(m)

    asyncio.run(app(scope, receive, send))
    start = out[0]
    hdrs: dict = {}
    for k, v in start.get("headers", []):
        hdrs.setdefault(k.decode().lower(), []).append(v.decode())
    return start["status"], hdrs, b"".join(m.get("body", b"") for m in out[1:])


def asgi_ws(app, headers: dict | None = None, client=LAN_CLIENT, server=LAN_SERVER) -> list[dict]:
    h = {"host": f"{server[0]}:{server[1]}", **{k.lower(): v for k, v in (headers or {}).items()}}
    scope = {"type": "websocket", "asgi": {"version": "3.0"}, "scheme": "ws", "path": "/api/v1/events",
             "raw_path": b"/api/v1/events", "query_string": b"", "root_path": "",
             "headers": [(k.encode(), v.encode()) for k, v in h.items()], "client": client, "server": server,
             "subprotocols": []}
    msgs = [{"type": "websocket.connect"}]
    out: list[dict] = []

    async def receive():
        if msgs:
            return msgs.pop(0)
        return {"type": "websocket.disconnect", "code": 1000}

    async def send(m):
        out.append(m)

    asyncio.run(app(scope, receive, send))
    return out


def code_of(body: bytes) -> str:
    return json.loads(body)["error"]["code"]


class AccessUnitTest(unittest.TestCase):
    """鍵・認証番号・止める・切る（access.py）。"""

    def setUp(self):
        self.clock = Clock()
        self.a = acc.Access(lan=True, now=self.clock)

    def test_keys_change_every_start_and_kinds(self):
        b = acc.Access(lan=True)
        self.assertNotEqual(self.a.keys["operate"], b.keys["operate"])
        self.assertNotEqual(self.a.keys["operate"], self.a.keys["view"])
        self.assertGreaterEqual(len(self.a.keys["operate"]), 32)
        self.assertEqual(self.a.login_key(self.a.keys["view"], "ip").kind, "view")
        self.assertEqual(self.a.login_key(self.a.keys["operate"], "ip").kind, "operate")
        self.assertIsNone(self.a.login_key("x" * 32, "ip"))

    def test_code_six_digits_valid_two_minutes(self):
        code = self.a.new_code()
        self.assertRegex(code, r"^\d{6}$")
        self.clock.t += 119
        s, r = self.a.login_code(code, "1.1.1.1")
        self.assertEqual((r, s.kind), ("ok", "operate"))
        self.clock.t += 2  # 2 分を過ぎた
        self.assertEqual(self.a.login_code(code, "1.1.1.2")[1], "wrong_code")

    def test_new_code_invalidates_old(self):
        old = self.a.new_code()
        new = self.a.new_code()
        if old != new:
            self.assertEqual(self.a.login_code(old, "ip")[1], "wrong_code")
        self.assertEqual(self.a.login_code(new, "ip")[1], "ok")

    def test_five_wrong_block_one_minute_per_source(self):
        code = self.a.new_code()
        wrong = "000000" if code != "000000" else "111111"
        for i in range(4):
            self.assertEqual(self.a.login_code(wrong, "10.0.0.5")[1], "wrong_code", i)
        self.assertEqual(self.a.login_code(wrong, "10.0.0.5")[1], "blocked")
        # 止まっている間は正しい番号も受けない。ほかの接続元は止まらない
        self.assertEqual(self.a.login_code(code, "10.0.0.5")[1], "blocked")
        self.assertEqual(self.a.login_code(code, "10.0.0.6")[1], "ok")
        self.clock.t += 59
        self.assertEqual(self.a.login_code(code, "10.0.0.5")[1], "blocked")
        self.clock.t += 1.5
        self.assertEqual(self.a.login_code(code, "10.0.0.5")[1], "ok")

    def test_success_resets_wrong_count(self):
        code = self.a.new_code()
        wrong = "000000" if code != "000000" else "111111"
        for _ in range(4):
            self.a.login_code(wrong, "ip")
        self.assertEqual(self.a.login_code(code, "ip")[1], "ok")
        for _ in range(4):
            self.assertEqual(self.a.login_code(wrong, "ip")[1], "wrong_code")

    def test_regenerate_and_disconnect(self):
        s1 = self.a.login_key(self.a.keys["operate"], "ip1")
        s2 = self.a.login_key(self.a.keys["view"], "ip2")
        closed = []
        c1 = self.a.add_conn("operate", "ip1", s1.token, False, lambda: closed.append(1))
        self.a.add_conn("view", "ip2", s2.token, False, lambda: closed.append(2))
        self.a.add_conn("operate", "127.0.0.1", None, True, lambda: closed.append(3))
        self.assertEqual(self.a.counts(), {"total": 2, "operate": 1, "view": 1})
        # 行の［切断］はそのブラウザの札も無効にする（繋ぎ直しても入れない）
        self.a.disconnect(c1)
        self.assertEqual(closed, [1])
        self.assertIsNone(self.a.session(s1.token))
        self.assertIsNotNone(self.a.session(s2.token))
        old = dict(self.a.keys)
        self.a.regenerate()
        self.assertNotEqual(old["view"], self.a.keys["view"])
        self.assertIsNone(self.a.session(s2.token))
        self.assertIsNone(self.a.login_key(old["operate"], "ip"))
        self.assertIn(2, closed)
        self.assertNotIn(3, closed)  # 自分の PC の画面は切らない

    def test_lan_off_forgets_browsers(self):
        s = self.a.login_key(self.a.keys["operate"], "ip")
        self.a.set_lan(False)
        self.assertIsNone(self.a.session(s.token))

    def test_host_and_origin(self):
        srv = ("127.0.0.1", 8080)
        self.assertTrue(acc.host_allowed("127.0.0.1:8080", srv, False))
        self.assertTrue(acc.host_allowed("localhost:8080", srv, False))
        self.assertTrue(acc.host_allowed("[::1]:8080", ("::1", 8080), False))
        self.assertFalse(acc.host_allowed("evil.example:8080", srv, False))  # DNS のリバインディング
        self.assertFalse(acc.host_allowed("127.0.0.1:9999", srv, False))
        self.assertFalse(acc.host_allowed(None, srv, False))
        lan = ("192.168.1.10", 8080)
        self.assertTrue(acc.host_allowed("192.168.1.10:8080", lan, True))
        self.assertFalse(acc.host_allowed("192.168.1.10:8080", lan, False))
        self.assertFalse(acc.host_allowed("192.168.1.11:8080", lan, True))
        self.assertTrue(acc.origin_allowed(None, "127.0.0.1:8080"))
        self.assertTrue(acc.origin_allowed("http://127.0.0.1:8080", "127.0.0.1:8080"))
        self.assertFalse(acc.origin_allowed("http://evil.example", "127.0.0.1:8080"))
        self.assertFalse(acc.origin_allowed("null", "127.0.0.1:8080"))

    def test_urls(self):
        u = acc.urls(8080, self.a.keys, ["192.168.1.10"])
        self.assertEqual(u["operate"], f"http://192.168.1.10:8080/#k={self.a.keys['operate']}")
        self.assertEqual(u["plain"], "http://192.168.1.10:8080/")
        self.assertIsNone(acc.urls(8080, self.a.keys, [])["operate"])


class GuardTest(unittest.TestCase):
    """LAN の別の機器から来た要求（ASGI で直接）。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.api = make_api(Path(self.tmp.name))
        self.clock = Clock()
        self.access = acc.Access(lan=True, now=self.clock)
        self.app = web.create_app(self.api, access=self.access)

    def tearDown(self):
        self.api.close()
        self.tmp.cleanup()

    def login(self, body: dict, client=LAN_CLIENT) -> str:
        st, h, b = asgi(self.app, "POST", "/api/v1/auth", {"content-type": "application/json"},
                        json.dumps(body).encode(), client=client)
        self.assertEqual(st, 200, b)
        cookie = h["set-cookie"][0]
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=strict", cookie.replace("Strict", "strict"))
        return cookie.split(";")[0]

    def test_without_key_only_the_auth_page(self):
        st, _, body = asgi(self.app, "GET", "/")
        self.assertEqual(st, 200)
        page = body.decode("utf-8")
        self.assertIn(PROMPT, page)
        self.assertIn("auth.js", page)
        self.assertNotIn("app.js", page)
        self.assertEqual(asgi(self.app, "GET", "/auth.js")[0], 200)
        # 認証の画面のアイコン（Qt の窓と同じ絵）も札なしで出す
        self.assertIn('href="favicon.png"', page)
        st, h, body = asgi(self.app, "GET", "/favicon.png")
        self.assertEqual((st, h["content-type"][0]), (200, "image/png"))
        self.assertTrue(body.startswith(b"\x89PNG"))
        for path in ("/api/v1/state", "/api/v1/texts", "/api/v1/info", "/app.js", "/index.html?x"):
            st, _, body = asgi(self.app, "GET", path.split("?")[0])
            if path.startswith("/index.html"):
                self.assertNotIn(b"app.js", body)
                continue
            self.assertEqual(st, 401, path)
            self.assertEqual(code_of(body), "auth_required")
        st, _, body = asgi(self.app, "POST", "/api/v1/run", {"content-type": "application/json"}, b"{}")
        self.assertEqual((st, code_of(body)), (401, "auth_required"))
        out = asgi_ws(self.app)
        self.assertEqual(out[0]["type"], "websocket.close")
        self.assertEqual(out[0]["code"], 4401)

    def test_code_login_then_state(self):
        code = self.access.new_code()
        cookie = self.login({"code": code})
        st, _, body = asgi(self.app, "GET", "/api/v1/state", {"cookie": cookie})
        self.assertEqual(st, 200)
        self.assertIn("version", json.loads(body))
        st, _, body = asgi(self.app, "GET", "/api/v1/info", {"cookie": cookie})
        self.assertEqual(json.loads(body)["access"], {"kind": "operate", "local": False})
        # 札は別のポートのアプリには渡らない名前（クッキーはポートを区別しないため）
        self.assertTrue(cookie.startswith("namibench_s_8080="))

    def test_wrong_codes_block(self):
        code = self.access.new_code()
        wrong = "000000" if code != "000000" else "111111"
        for _ in range(4):
            st, _, b = asgi(self.app, "POST", "/api/v1/auth", {"content-type": "application/json"},
                            json.dumps({"code": wrong}).encode())
            self.assertEqual((st, code_of(b)), (401, "wrong_code"))
            self.assertEqual(json.loads(b)["error"]["text"], texts.ERRORS["wrong_code"])
        st, _, b = asgi(self.app, "POST", "/api/v1/auth", {"content-type": "application/json"},
                        json.dumps({"code": wrong}).encode())
        self.assertEqual((st, code_of(b)), (429, "blocked"))
        self.assertEqual(json.loads(b)["error"]["detail"]["retry_after_s"], 60)
        st, _, b = asgi(self.app, "POST", "/api/v1/auth", {"content-type": "application/json"},
                        json.dumps({"code": code}).encode())
        self.assertEqual((st, code_of(b)), (429, "blocked"))
        self.clock.t += 61
        self.login({"code": code})

    def test_view_only_refuses_commands(self):
        cookie = self.login({"key": self.access.keys["view"]})
        h = {"cookie": cookie, "content-type": "application/json"}
        for name, args in (("set_sweep", {"start_hz": 1}), ("run", {"mode": "single"}), ("save", {"name": "x"}),
                           ("access_info", {}), ("marker_add", {"kind": "M"})):
            st, _, b = asgi(self.app, "POST", f"/api/v1/{name}", h, json.dumps(args).encode())
            self.assertEqual((st, code_of(b)), (403, "read_only"), name)
            self.assertEqual(json.loads(b)["error"]["text"], "見るだけの接続です")
        st, _, b = asgi(self.app, "PUT", "/api/v1/uploads", {"cookie": cookie,
                                                               "content-type": "application/octet-stream"}, b"x")
        self.assertEqual((st, code_of(b)), (403, "read_only"))
        # 読むだけは通る（get_trace はまだ値が無いので命令の失敗。断られてはいない）
        st, _, b = asgi(self.app, "POST", "/api/v1/get_trace", h, b'{"id": 1}')
        self.assertNotEqual(st, 403)
        self.assertEqual(asgi(self.app, "GET", "/api/v1/state", {"cookie": cookie})[0], 200)
        st, _, b = asgi(self.app, "GET", "/api/v1/info", {"cookie": cookie})
        self.assertEqual(json.loads(b)["access"]["kind"], "view")

    def test_bad_key_and_regenerate(self):
        cookie = self.login({"key": self.access.keys["operate"]})
        old_key = self.access.keys["operate"]
        self.access.regenerate()
        self.assertEqual(asgi(self.app, "GET", "/api/v1/state", {"cookie": cookie})[0], 401)
        st, _, b = asgi(self.app, "POST", "/api/v1/auth", {"content-type": "application/json"},
                        json.dumps({"key": old_key}).encode())
        self.assertEqual((st, code_of(b)), (401, "bad_key"))

    def test_host_origin_content_type_also_for_local(self):
        local = ("127.0.0.1", 50001)
        srv = ("127.0.0.1", 8080)
        ok = asgi(self.app, "GET", "/api/v1/state", client=local, server=srv)
        self.assertEqual(ok[0], 200)
        st, _, b = asgi(self.app, "GET", "/api/v1/state", {"host": "evil.example:8080"}, client=local, server=srv)
        self.assertEqual((st, code_of(b)), (403, "bad_host"))
        st, _, b = asgi(self.app, "POST", "/api/v1/stop", {"origin": "http://evil.example",
                                                            "content-type": "application/json"}, b"{}",
                        client=local, server=srv)
        self.assertEqual((st, code_of(b)), (403, "bad_origin"))
        st, _, b = asgi(self.app, "POST", "/api/v1/stop", {"content-type": "text/plain"}, b"{}",
                        client=local, server=srv)
        self.assertEqual((st, code_of(b)), (415, "bad_content_type"))
        st, _, b = asgi(self.app, "POST", "/api/v1/stop", {"content-type": "application/x-www-form-urlencoded"},
                        b"a=1", client=local, server=srv)
        self.assertEqual(st, 415)
        st, _, b = asgi(self.app, "PUT", "/api/v1/uploads", {"content-type": "application/json"}, b"{}",
                        client=local, server=srv)
        self.assertEqual((st, code_of(b)), (415, "bad_content_type"))
        st, _, b = asgi(self.app, "GET", "/api/v1/files/abc", {"host": "evil.example:8080"}, client=local, server=srv)
        self.assertEqual(st, 403)
        st, _, b = asgi(self.app, "POST", "/api/v1/stop", {"origin": "http://127.0.0.1:8080",
                                                            "content-type": "application/json; charset=utf-8"},
                        b"{}", client=local, server=srv)
        self.assertEqual(st, 200)
        out = asgi_ws(self.app, {"origin": "http://evil.example"}, client=local, server=srv)
        self.assertEqual(out[0]["type"], "websocket.close")
        out = asgi_ws(self.app, {"host": "evil.example:8080"}, client=local, server=srv)
        self.assertEqual(out[0]["type"], "websocket.close")

    def test_lan_off_refuses_outside(self):
        self.access.set_lan(False)
        st, _, b = asgi(self.app, "GET", "/")
        self.assertEqual((st, code_of(b)), (403, "lan_closed"))

    def test_access_info(self):
        cookie = self.login({"key": self.access.keys["operate"]})
        with mock.patch.object(acc, "lan_addresses", return_value=["192.168.1.10"]):
            st, _, b = asgi(self.app, "POST", "/api/v1/access_info",
                            {"cookie": cookie, "content-type": "application/json"}, b"{}")
        self.assertEqual(st, 200)
        info = json.loads(b)
        self.assertEqual(info["urls"]["view"], f"http://192.168.1.10:8080/#k={self.access.keys['view']}")
        self.assertTrue(info["qr_svg"]["operate"].startswith("<svg"))
        self.assertRegex(info["code"], r"^\d{6}$")
        self.assertEqual(info["valid_s"], 120)
        self.assertEqual(self.access.current_code()[0], info["code"])  # 新しい番号を出した


class WebFilesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.files = webfiles.WebFiles(Path(self.tmp.name) / "web_files")
        self.files.reset()

    def tearDown(self):
        self.tmp.cleanup()

    def test_path_args_refused(self):
        for cmd, args in (("save", {"path": "C:/x.h5"}), ("open", {"paths": ["a"]}),
                          ("export_csv", {"path": "a.csv"}), ("record_finish", {"path": "a"}),
                          ("finish_unfinished", {"path": "a", "h5_path": "b"}), ("discard_unfinished", {"path": "a"}),
                          ("import_legacy_calibration", {"path": "a"}), ("rtlsdr_set", {"dll_folder": "C:/"}),
                          ("finish_unfinished", {"id": "x", "h5_path": "b"})):
            with self.assertRaises(webfiles.WebArgError) as cm:
                webfiles.translate(self.files, cmd, args)
            self.assertEqual(cm.exception.code, "path_not_allowed", cmd)

    def test_names_stay_inside(self):
        for name in ("../../evil.h5", "C:\\Windows\\x.h5", "a/b/c", "con", "", None, "x" * 500, 'a<b>:"|?*.h5'):
            tr = webfiles.translate(self.files, "save", {"name": name, "note": ""})
            p = Path(tr.args["path"]).resolve()
            self.assertTrue(str(p).startswith(str(self.files.root.resolve())), (name, p))
            self.assertTrue(p.name.endswith(".h5"))
            self.assertLessEqual(len(p.name), webfiles.MAX_NAME_CHARS)

    def test_unfinished_by_id(self):
        tr = webfiles.translate(self.files, "list_unfinished", {})
        out = tr.after({"recordings": [{"path": "D:/rec/active_1.sqlite", "n_sweeps": 3}]})
        row = out["recordings"][0]
        self.assertNotIn("path", row)
        tr = webfiles.translate(self.files, "discard_unfinished", {"id": row["id"]})
        self.assertEqual(tr.args["path"], "D:/rec/active_1.sqlite")
        tr = webfiles.translate(self.files, "finish_unfinished", {"id": row["id"], "name": "r"})
        self.assertEqual(tr.args["path"], "D:/rec/active_1.sqlite")
        self.assertTrue(tr.args["h5_path"].endswith("r.h5"))
        with self.assertRaises(webfiles.WebArgError) as cm:
            webfiles.translate(self.files, "discard_unfinished", {"id": "nope"})
        self.assertEqual(cm.exception.code, "unknown_recording")

    def test_reset_empties(self):
        stale = self.files.root / "out" / "old" / "x.h5"
        stale.parent.mkdir(parents=True)
        stale.write_bytes(b"x")
        webfiles.WebFiles(self.files.root).reset()
        self.assertEqual(list(self.files.root.iterdir()), [])


class WebFilesServerTest(unittest.TestCase):
    """本物の uvicorn（127.0.0.1）で、名前 → file_id → ダウンロード、アップロード → upload_id → 開く。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        stale = root / "data" / "web_files" / "out" / "old" / "stale.h5"
        stale.parent.mkdir(parents=True)
        stale.write_bytes(b"old")
        self.stale = stale
        self.api = make_api(root)
        self.srv = _Server(self.api)

    def tearDown(self):
        self.srv.stop()
        self.api.close()
        self.tmp.cleanup()

    def post(self, name, body=None, headers=None):
        req = urllib.request.Request(f"{self.srv.base}/api/v1/{name}", data=json.dumps(body or {}).encode(),
                                     headers={"Content-Type": "application/json", **(headers or {})})
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def put(self, name, data: bytes):
        req = urllib.request.Request(f"{self.srv.base}/api/v1/uploads?name={urllib.request.quote(name)}", data=data,
                                     method="PUT", headers={"Content-Type": "application/octet-stream"})
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def get(self, path, headers=None):
        req = urllib.request.Request(self.srv.base + path, headers=headers or {})
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                return r.status, dict(r.headers), r.read()
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers), e.read()

    def wait_task(self, task_id, timeout=30):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            st = json.loads(self.get("/api/v1/state")[2])
            if not any(t["task_id"] == task_id for t in st.get("tasks", [])):
                return st
            time.sleep(0.05)
        self.fail("処理が終わらない")

    def sweep_once(self):
        self.post("connect", {"backend": "simulator", "serial": "SIM00001"})
        self.post("set_sweep", {"start_hz": 90_000_000, "stop_hz": 110_000_000, "points": 501})
        self.post("run", {"mode": "single"})
        end = time.monotonic() + 30
        while time.monotonic() < end:
            st = json.loads(self.get("/api/v1/state")[2])
            if st["run"]["state"] == "stopped" and any(t["has_values"] for t in st["traces"]):
                return
            time.sleep(0.05)
        self.fail("掃引が終わらない")

    def test_web_files_emptied_at_start(self):
        self.assertFalse(self.stale.exists())

    def test_path_not_allowed(self):
        for name, args in (("save", {"path": "C:/x.h5"}), ("open", {"paths": ["C:/x.h5"]}),
                           ("rtlsdr_set", {"dll_folder": "C:/"}), ("export_csv", {"path": "x.csv"}),
                           ("record_finish", {"path": "x.h5"}),
                           ("finish_unfinished", {"path": "a", "h5_path": "b"})):
            st, b = self.post(name, args)
            self.assertEqual((st, b["error"]["code"]), (400, "path_not_allowed"), name)
            self.assertEqual(b["error"]["text"], texts.ERRORS["path_not_allowed"])

    def test_save_download_upload_open_roundtrip(self):
        self.sweep_once()
        st, r = self.post("save", {"name": "../my run", "note": "web"})
        self.assertEqual(st, 200, r)
        self.assertEqual(r["name"], "my run.h5")
        self.assertNotIn("path", r)
        # 終わる前は作っている途中（file_not_ready）か、もう出来ている
        self.wait_task(r["task_id"])
        st, h, data = self.get(f"/api/v1/files/{r['file_id']}")
        self.assertEqual(st, 200)
        self.assertTrue(data.startswith(b"\x89HDF"))
        self.assertIn("my%20run.h5", h.get("content-disposition", "") + h.get("Content-Disposition", ""))
        made = list((Path(self.tmp.name) / "data" / "web_files").rglob("my run.h5"))
        self.assertEqual(len(made), 1)  # サーバーが書くのは web_files の中だけ
        st, up = self.put("my run.h5", data)
        self.assertEqual(st, 200, up)
        self.assertEqual(up["size"], len(data))
        st, r2 = self.post("open", {"uploads": [up["upload_id"]]})
        self.assertEqual(st, 200, r2)
        state = self.wait_task(r2["task_id"])
        self.assertEqual(len(state["files"]), 1)
        st, b = self.post("open", {"uploads": ["nope"]})
        self.assertEqual(b["error"]["code"], "unknown_upload")
        self.assertEqual(self.get("/api/v1/files/nope")[0], 404)

    def test_export_csv_download_and_notice_file_id(self):
        from websockets.sync.client import connect as ws_connect

        self.sweep_once()
        ws = ws_connect(f"ws://127.0.0.1:{self.srv.port}/api/v1/events", open_timeout=10, max_size=None)
        try:
            # 最初の state が届いてから書き出す（購読の前に終わりの知らせが出ると受け取れない: 全体の試験の負荷で時々起きた）
            ws.recv(timeout=10)
            st, r = self.post("export_csv", {"name": "trace", "traces": [1]})
            self.assertEqual(st, 200, r)
            end = time.monotonic() + 20
            fid = None
            while time.monotonic() < end and fid is None:
                m = ws.recv(timeout=end - time.monotonic())
                if isinstance(m, str):
                    d = json.loads(m)
                    if d["kind"] == "notice" and d["payload"]["code"] == "task_done":
                        fid = d["payload"]["detail"].get("file_id")
            self.assertEqual(fid, r["file_id"])
        finally:
            ws.close()
        st, h, data = self.get(f"/api/v1/files/{fid}")
        self.assertEqual(st, 200)
        self.assertIn(b"frequency_hz,trace1_dBm", data[:4000])

    def test_task_end_notice_before_command_returns_has_file_id(self):
        """書き出しが、Web の口が命令の戻りを受け取るより先に終わっても、終わりの知らせに file_id が付く。

        全体の試験の負荷で、戻りを受け取る前に終わりの知らせが配られ、file_id の無い知らせになることがあった
        （ダウンロードが始まらない）。戻りを受け取るのを遅らせて、その順を毎回作る。
        """
        from websockets.sync.client import connect as ws_connect

        self.sweep_once()
        real_await = web._await

        async def late(fut):
            r = await real_await(fut)
            await asyncio.sleep(0.5)  # この間に、終わった書き出しの知らせが配られる
            return r

        ws = ws_connect(f"ws://127.0.0.1:{self.srv.port}/api/v1/events", open_timeout=10, max_size=None)
        try:
            ws.recv(timeout=10)
            with mock.patch.object(web, "_await", late):
                st, r = self.post("export_csv", {"name": "early", "traces": [1]})
            self.assertEqual(st, 200, r)
            end = time.monotonic() + 20
            done = None
            while time.monotonic() < end and done is None:
                m = ws.recv(timeout=end - time.monotonic())
                if isinstance(m, str):
                    d = json.loads(m)
                    if d["kind"] == "notice" and d["payload"]["code"] == "task_done":
                        done = d["payload"]["detail"]
            self.assertEqual(done["task_id"], r["task_id"])
            self.assertEqual(done.get("file_id"), r["file_id"])
        finally:
            ws.close()
        st, h, data = self.get(f"/api/v1/files/{r['file_id']}")
        self.assertEqual(st, 200)

    def test_legacy_calibration_by_upload(self):
        from tests.test_level import build_legacy_bytes

        st, up = self.put("gigast.txt", build_legacy_bytes())
        self.assertEqual(st, 200)
        st, r = self.post("import_legacy_calibration", {"upload_id": up["upload_id"], "serial": "SIM00001"})
        self.assertEqual(st, 200, r)
        st, up2 = self.put("broken.txt", b"not a calibration")
        st, r = self.post("import_legacy_calibration", {"upload_id": up2["upload_id"], "serial": "SIM00001"})
        self.assertEqual(r["error"]["code"], "legacy_unreadable")

    def test_upload_too_large(self):
        with mock.patch.object(routes_access, "MAX_UPLOAD_BYTES", 10):
            st, b = self.put("big.bin", b"x" * 11)
            self.assertEqual((st, b["error"]["code"]), (413, "upload_too_large"))
            st, b = self.put("ok.bin", b"x" * 10)
            self.assertEqual(st, 200)
        self.assertEqual(webfiles.MAX_UPLOAD_BYTES, 2 * 1024 ** 3)

    def test_bad_host_and_origin_on_real_server(self):
        st, _, b = self.get("/api/v1/state", {"Host": f"evil.example:{self.srv.port}"})
        self.assertEqual(st, 403)
        st, b = self.post("stop", {}, {"Origin": "http://evil.example"})
        self.assertEqual((st, b["error"]["code"]), (403, "bad_origin"))
        from websockets.exceptions import InvalidStatus
        from websockets.sync.client import connect as ws_connect

        with self.assertRaises(InvalidStatus):
            ws_connect(f"ws://127.0.0.1:{self.srv.port}/api/v1/events", origin="http://evil.example",
                       open_timeout=10)
        # 自分の PC（127.0.0.1）からは鍵なしで操作できる（A §4.3.2 の 8）
        st, _, b = self.get("/api/v1/info")
        self.assertEqual(json.loads(b)["access"], {"kind": "operate", "local": True})


@unittest.skipUnless(shutil.which("node"), "Node が無い")
class FileTasksJsTest(unittest.TestCase):
    """画面（app_files.js）は、終わりの知らせが命令の戻りより先に届いても、作らせたファイルをダウンロードする。"""

    SCRIPT = r"""
globalThis.window = globalThis;
require(process.argv[1]);
const out = {};
const done = (tid, fid) => ({ code: "task_done", detail: { task_id: tid, file_id: fid } });
// 戻り → 知らせ（いつもの順）
let t = GSFileTasks();
out.normal = [t.started("A", { name: "a.csv" }), t.ended(done("A", "F1"))];
// 知らせ → 戻り（WebSocket が先に着いた）
t = GSFileTasks();
out.early = [t.ended(done("B", "F2")), t.started("B", { name: "b.csv" })];
// 作れなかった・やめた処理はダウンロードしない
t = GSFileTasks();
out.failed = [t.ended({ code: "task_failed", detail: { task_id: "C" } }), t.started("C", { name: "c.csv" })];
// 終わりでない知らせ・他の画面の処理は、待っている処理を消さない
t = GSFileTasks();
out.other = [t.started("D", { name: "d.h5" }), t.ended({ code: "saved", detail: { task_id: "D" } }),
             t.ended(done("X", "FX")), t.ended(done("D", "F4"))];
console.log(JSON.stringify(out));
"""

    def test_order(self):
        js = Path(__file__).resolve().parent.parent / "namibench" / "ui_web" / "app_files.js"
        got = json.loads(subprocess.run(["node", "-e", self.SCRIPT, str(js)], capture_output=True, text=True,
                                        timeout=30, check=True, encoding="utf-8").stdout)
        self.assertEqual(got["normal"], [None, {"id": "F1", "name": "a.csv"}])
        self.assertEqual(got["early"], [None, {"id": "F2", "name": "b.csv"}])
        self.assertEqual(got["failed"], [None, None])
        self.assertEqual(got["other"], [None, None, None, {"id": "F4", "name": "d.h5"}])


class ConsoleTest(unittest.TestCase):
    """`serve` のコンソール（Enter で新しい認証番号・r で作り直し）。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.api = make_api(Path(self.tmp.name))
        self.svc = WebService(self.api, port=8123, lan=True)

    def tearDown(self):
        self.api.close()
        self.tmp.cleanup()

    def test_enter_new_code_and_r_regenerates(self):
        addrs = ["192.168.1.10"]
        info = self.svc.connection_info(new_code=True, addresses=addrs)
        block = console_block(self.svc, info, unicode_blocks=True)
        self.assertIn(info["urls"]["operate"], block)
        self.assertIn(info["urls"]["view"], block)
        self.assertIn(info["code"], block)
        self.assertIn("▀", block)
        self.assertIn("http://127.0.0.1:8123/", block)
        seen = {info["code"]}
        for _ in range(3):
            out = console_command(self.svc, "\n", addresses=addrs)
            code = self.svc.access.current_code()[0]
            self.assertIn(code, out)
            seen.add(code)
        self.assertGreater(len(seen), 1)
        old = dict(self.svc.access.keys)
        out = console_command(self.svc, "r\n", addresses=addrs)
        self.assertNotEqual(old["operate"], self.svc.access.keys["operate"])
        self.assertIn(self.svc.access.keys["operate"], out)
        self.assertIn(texts.WEB_LABELS["console_regenerated"], out)
        self.assertIsNone(self.svc.access.login_key(old["operate"], "ip"))
        self.assertEqual(console_command(self.svc, "?\n", addresses=addrs), texts.WEB_LABELS["console_keys"])

    def test_ascii_fallback_and_local_only(self):
        info = self.svc.connection_info(new_code=True, addresses=["192.168.1.10"])
        block = console_block(self.svc, info, unicode_blocks=False)
        self.assertIn("##", block)
        local = WebService(self.api, port=8124, lan=False)
        self.assertIn("http://127.0.0.1:8124/", console_command(local, ""))
        nolan = console_block(self.svc, self.svc.connection_info(addresses=[]))
        self.assertIn(texts.WEB_LABELS["console_no_address"], nolan)


LAN_IPS = acc.lan_addresses()


@unittest.skipUnless(LAN_IPS, "この PC に LAN のアドレスが無い")
class RealLanTest(unittest.TestCase):
    """0.0.0.0 に開き、この PC の LAN のアドレスで繋ぐ（ループバックでない接続として認証が要る）。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.api = make_api(Path(self.tmp.name))
        self.svc = WebService(self.api, port=0, lan=True)
        self.assertTrue(self.svc.start(), self.svc.error)
        self.ip = LAN_IPS[0]
        self.base = f"http://{self.ip}:{self.svc.bound_port}"

    def tearDown(self):
        self.svc.stop()
        self.api.close()
        self.tmp.cleanup()

    def req(self, method, path, body=None, headers=None, ctype="application/json"):
        data = None if body is None else json.dumps(body).encode()
        h = dict(headers or {})
        if data is not None:
            h["Content-Type"] = ctype
        r = urllib.request.Request(self.base + path, data=data, method=method, headers=h)
        try:
            with urllib.request.urlopen(r, timeout=10) as res:
                return res.status, dict(res.headers), res.read()
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers), e.read()

    def test_lan_flow(self):
        from websockets.sync.client import connect as ws_connect

        st, _, page = self.req("GET", "/")
        self.assertEqual(st, 200)
        self.assertIn(PROMPT, page.decode("utf-8"))
        self.assertEqual(self.req("GET", "/api/v1/state")[0], 401)
        code = self.svc.connection_info(new_code=True, addresses=[self.ip])["code"]
        st, h, b = self.req("POST", "/api/v1/auth", {"code": code})
        self.assertEqual(st, 200, b)
        cookie = (h.get("set-cookie") or h.get("Set-Cookie")).split(";")[0]
        st, _, b = self.req("GET", "/api/v1/state", headers={"Cookie": cookie})
        self.assertEqual(st, 200)
        ws = ws_connect(f"ws://{self.ip}:{self.svc.bound_port}/api/v1/events",
                        additional_headers={"Cookie": cookie}, open_timeout=10, max_size=None)
        try:
            ws.recv(timeout=10)  # 最初の state
            end = time.monotonic() + 5
            while not self.svc.access.conns() and time.monotonic() < end:
                time.sleep(0.02)
            conns = self.svc.access.conns()
            self.assertEqual(len(conns), 1)
            self.assertEqual((conns[0].ip, conns[0].kind), (self.ip, "operate"))
            self.assertEqual(self.svc.access.counts()["operate"], 1)
            # 一覧の［切断］: 画面が切れ、同じ札ではもう入れない
            self.svc.access.disconnect(conns[0].conn_id)
            from websockets.exceptions import ConnectionClosed

            with self.assertRaises(ConnectionClosed):
                while True:
                    ws.recv(timeout=10)
        finally:
            ws.close()
        self.assertEqual(self.req("GET", "/api/v1/state", headers={"Cookie": cookie})[0], 401)
        # LAN を OFF にすると自分の PC だけに戻る（LAN のアドレスでは繋がらない）
        self.svc.set_lan(False)
        self.assertTrue(self.svc.running)
        with self.assertRaises(OSError):
            urllib.request.urlopen(f"http://{self.ip}:{self.svc.bound_port}/", timeout=3)
        with urllib.request.urlopen(f"http://127.0.0.1:{self.svc.bound_port}/api/v1/info", timeout=5) as r:
            self.assertEqual(json.loads(r.read())["lan"], False)


if __name__ == "__main__":
    unittest.main()


class WebWordsTest(unittest.TestCase):
    def test_web_labels_used_by_web_exist(self):
        import re

        ui = Path(web.UI_WEB_DIR)
        src = "".join(f.read_text(encoding="utf-8") for f in ui.glob("*.js")) + (ui / "index.html").read_text(encoding="utf-8")
        for key in set(re.findall(r"\bWL\(\s*['\"]([a-z0-9_]+)['\"]", src)):
            if key.endswith("_"):
                continue  # 後ろを足して引く名前（"connect_" + 種類）
            self.assertIn(key, texts.WEB_LABELS, key)
        for kind in ("operate", "view"):
            self.assertIn("connect_" + kind, texts.WEB_LABELS)
        self.assertEqual(texts.WEB_LABELS["auth_prompt"], PROMPT)
