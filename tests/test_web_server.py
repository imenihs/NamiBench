"""Web の口（設計A §4.3・§4.4）を、シミュレータの機器と本物の uvicorn で通す。

httpx が入っていないので FastAPI の TestClient は使わず、空いているポートでサーバーを立て、
HTTP は urllib、WebSocket は uvicorn[standard] が入れた websockets の同期クライアントで話す。
"""
import asyncio
import json
import math
import re
import shutil
import struct
import subprocess
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

import numpy as np
import uvicorn
from websockets.sync.client import connect as ws_connect

from namibench.api.local import LocalApi, _SimulatorBackend
from namibench.api.web import server as web
from namibench.core.devices.base import load_profile
from namibench.core.instrument import GIGAST_PROFILE_PATH, Instrument
from namibench.core.units import drag_ref_level, format_freq, parse_freq_hz

UI_WEB = Path(web.UI_WEB_DIR)


def decode_trace(data: bytes) -> tuple[dict, np.ndarray]:
    n = struct.unpack("<I", data[:4])[0]
    header = json.loads(data[4:4 + n].decode("utf-8"))
    values = np.frombuffer(data, dtype="<f4", offset=4 + n)
    return header, values


class _Server:
    """空いているポートで Web の口を立てる。"""

    def __init__(self, api: LocalApi, trace_rate_hz: float = web.TRACE_RATE_HZ) -> None:
        cfg = uvicorn.Config(web.create_app(api, trace_rate_hz=trace_rate_hz), host="127.0.0.1", port=0,
                             log_level="warning", lifespan="on")
        self.server = uvicorn.Server(cfg)
        self.thread = threading.Thread(target=self.server.run, daemon=True)
        self.thread.start()
        end = time.monotonic() + 10
        while not self.server.started:
            if time.monotonic() > end:
                raise RuntimeError("サーバーが立たない")
            time.sleep(0.02)
        self.port = self.server.servers[0].sockets[0].getsockname()[1]
        self.base = f"http://127.0.0.1:{self.port}"

    def stop(self) -> None:
        self.server.should_exit = True
        self.thread.join(10)


class WebServerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        profile = load_profile(GIGAST_PROFILE_PATH)
        sim = _SimulatorBackend(profile)
        sim.speed = 0.05  # 見積もりの 1/20 の時間で返す
        self.api = LocalApi(Instrument({"simulator": sim}, profile=profile,
                                       data_root=root / "data", cal_root=root / "cal"))
        self.srv = _Server(self.api)

    def tearDown(self):
        self.srv.stop()
        self.api.close()
        self.tmp.cleanup()

    # ---- 道具

    def post(self, name, body=None, raw: bytes = None):
        data = raw if raw is not None else json.dumps(body or {}).encode()
        req = urllib.request.Request(f"{self.srv.base}/api/v1/{name}", data=data,
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def get(self, path):
        with urllib.request.urlopen(self.srv.base + path, timeout=20) as r:
            return r.status, r.read()

    def ws(self):
        return ws_connect(f"ws://127.0.0.1:{self.srv.port}/api/v1/events", open_timeout=10, max_size=None)

    @staticmethod
    def recv_until(ws, pred, timeout=20):
        """pred(種類, 中身) が真になる知らせまで読む。トレースは (header, values) で渡す。"""
        end = time.monotonic() + timeout
        while True:
            left = end - time.monotonic()
            if left <= 0:
                raise AssertionError("イベントが来ない")
            m = ws.recv(timeout=left)
            if isinstance(m, bytes):
                kind, payload = "trace", decode_trace(m)
            else:
                d = json.loads(m)
                kind, payload = d["kind"], d["payload"]
            if pred(kind, payload):
                return payload

    # ---- 命令・状態

    def test_state_texts_info(self):
        st, body = self.get("/api/v1/state")
        state = json.loads(body)
        self.assertEqual(st, 200)
        self.assertIn("version", state)
        self.assertEqual(state["connection"]["state"], "disconnected")
        _, body = self.get("/api/v1/texts")
        texts = json.loads(body)
        # Qt の texts.py の表がそのまま届く（同じ語: J §5）
        self.assertEqual(texts["REASONS"]["upper_limit"], "上限")
        self.assertIsInstance(texts["RULE_REASONS"], list)
        _, body = self.get("/api/v1/info")
        info = json.loads(body)
        self.assertIn("version", info)
        # トレースの線の描き方（設計F §9）は Qt と同じ1か所の値
        from namibench.core import report_layout as rl

        self.assertEqual(info["line_styles"], json.loads(json.dumps(rl.LINE_STYLES)))
        self.assertEqual(list(texts["TRACE_LINES"]), list(rl.TRACE_LINE_STYLES))

    def test_error_shape(self):
        st, body = self.post("run", {"mode": "continuous"})
        self.assertEqual(st, 400)
        # A §4.3 の失敗の形に、画面の文（Qt と同じ texts.error_text）を text として足して返す
        self.assertEqual(body, {"error": {"code": "not_connected", "field": None, "detail": {},
                                          "text": "機器に繋がっていません"}})
        st, body = self.post("no_such_command")
        self.assertEqual((st, body["error"]["code"]), (404, "unknown_command"))
        # 内部の命令（先頭が _）は Web から呼べない
        st, body = self.post("_shutdown")
        self.assertEqual((st, body["error"]["code"]), (404, "unknown_command"))
        st, body = self.post("set_sweep", {"start_hz": 1.5e6})
        self.assertEqual((st, body["error"]["code"], body["error"]["field"]), (400, "not_integer", "start_hz"))
        st, body = self.post("set_sweep", raw=b"{not json")
        self.assertEqual((st, body["error"]["code"]), (400, "bad_json"))
        st, body = self.post("set_sweep", raw=b"[1, 2]")
        self.assertEqual((st, body["error"]["code"]), (400, "bad_json"))
        # 知らない引数は命令の失敗（bad_arguments）
        st, body = self.post("set_sweep", {"nope": 1})
        self.assertEqual((st, body["error"]["code"]), (400, "bad_arguments"))

    def test_set_sweep_adjustments_and_alternative(self):
        st, body = self.post("set_sweep", {"stop_hz": 12_300_000_000})
        self.assertEqual(st, 200)
        self.assertEqual(body["adopted"]["stop_hz"], 12_100_000_000)
        self.assertEqual([(a["field"], a["reason"]) for a in body["adjustments"]], [("stop_hz", "upper_limit")])
        # 範囲いっぱいで Center に 1200MHz → Center を守って Span を縮め、反対の規則の結果を添える（C §5.11）
        self.post("set_sweep", {"start_hz": -100_000_000, "stop_hz": 12_100_000_000})
        st, body = self.post("set_sweep", {"center_hz": 1_200_000_000})
        self.assertEqual(body["adopted"]["center_hz"], 1_200_000_000)
        self.assertEqual(body["adopted"]["span_hz"], 2_600_000_000)
        self.assertIn("keep_center", [a["reason"] for a in body["adjustments"]])
        self.assertEqual(body["alternative"]["rule"], "keep_span")
        # 周波数は Hz の整数のまま（float にしない: A §2.1）
        self.assertIsInstance(body["adopted"]["start_hz"], int)

    def test_set_sweep_span_lock_start_and_keep_typed(self):
        # Span 固定中に Start を打つ: Stop を動かして固定 Span を守る（C §5.12。ユーザーの指摘の例）
        self.post("set_sweep", {"start_hz": 1_000_000_000, "stop_hz": 1_044_571_000})
        self.post("set_sweep", {"span_lock": True})
        st, body = self.post("set_sweep", {"start_hz": 470_000_000})
        self.assertEqual((body["adopted"]["start_hz"], body["adopted"]["stop_hz"]), (470_000_000, 514_571_000))
        self.assertEqual([(a["field"], a["reason"]) for a in body["adjustments"]], [("stop_hz", "span_lock")])
        self.assertEqual(body["texts"]["adjustments"][0]["rule"], False)
        # 上限を超える: Start を下げ、Start の欄に反対の結果（打った Start を守り Span を縮める）のボタン
        st, body = self.post("set_sweep", {"start_hz": 12_080_000_000})
        self.assertEqual(body["adopted"]["start_hz"], 12_100_000_000 - 44_571_000)
        rules = {a["field"]: a["rule"] for a in body["texts"]["adjustments"]}
        self.assertEqual(rules, {"start_hz": True, "stop_hz": False})
        self.assertEqual(body["alternative"]["rule"], "keep_typed")
        self.assertEqual(body["texts"]["alternative_label"], "Span を 20 MHz に縮めて 12.08 GHz〜12.1 GHz にする")
        # ボタン（その1回だけの命令）は固定 Span を変えない
        st, body = self.post("set_sweep", {"start_hz": body["alternative"]["start_hz"],
                                           "stop_hz": body["alternative"]["stop_hz"],
                                           "edited": ["start_hz", "stop_hz"], "one_shot": True})
        self.assertEqual((body["adopted"]["start_hz"], body["adopted"]["span_lock_hz"]), (12_080_000_000, 44_571_000))

    def test_single_sweep_binary_trace(self):
        self.post("set_sweep", {"start_hz": 100_000_000, "stop_hz": 200_000_000})
        self.assertEqual(self.post("connect", {"backend": "simulator", "serial": "SIM00001"})[0], 200)
        with self.ws() as ws:
            first = self.recv_until(ws, lambda k, p: True)
            self.assertIn("sweep", first)  # 繋いだ直後に今の状態が届く
            st, body = self.post("run", {"mode": "single"})
            self.assertEqual((st, body), (200, {"run": "single"}))
            # 同じ回に届いた状態はトレースより先に送られる（server._Client._run）ので、トレースを待つ間に来た
            # 「単発が終わった」状態も覚えておく（後で待つと、もう来ない）
            done = []

            def trace_or_done(k, p):
                if k == "state" and p["run"]["state"] == "stopped" and p["run"]["last_stop_reason"] == "single_done":
                    done.append(p)
                return k == "trace"

            header, values = self.recv_until(ws, trace_or_done)
            self.assertEqual(header["kind"], "trace")
            self.assertEqual(header["trace"], 1)
            self.assertEqual(header["value_type"], "float32")
            ax = header["axis"]
            self.assertEqual((ax["start_hz"], ax["points"]), (100_000_000, len(values)))
            self.assertEqual(header["value_count"], len(values))
            self.assertIsInstance(ax["step_hz"], int)
            self.assertIn("transient", header["flags"]["points"])
            # 値は画面の単位の dBm。シミュレータの 100MHz の信号（−30dBm 付近）が見える
            self.assertGreater(float(np.nanmax(values)), -45.0)
            if not done:
                self.recv_until(ws, lambda k, p: k == "state" and p["run"]["state"] == "stopped"
                                and p["run"]["last_stop_reason"] == "single_done")

    def test_two_clients_see_each_others_changes(self):
        with self.ws() as a, self.ws() as b:
            self.recv_until(a, lambda k, p: k == "state")
            self.recv_until(b, lambda k, p: k == "state")
            st, _ = self.post("set_sweep", {"start_hz": 300_000_000, "stop_hz": 400_000_000})
            self.assertEqual(st, 200)
            for ws in (a, b):
                p = self.recv_until(ws, lambda k, p: k == "state" and p["sweep"]["start_hz"] == 300_000_000)
                self.assertEqual(p["sweep"]["stop_hz"], 400_000_000)
            # 振幅（REF・dB/div）も全部の画面で同じ（A §4.4）
            self.post("set_amplitude", {"ref_level": -20.0, "db_per_div": 5})
            for ws in (a, b):
                self.recv_until(ws, lambda k, p: k == "state" and p["amplitude"]["ref_level"] == -20.0
                                and p["amplitude"]["db_per_div"] == 5)

    def test_late_client_gets_current_trace(self):
        self.post("set_sweep", {"start_hz": 100_000_000, "stop_hz": 150_000_000})
        self.post("connect", {"backend": "simulator", "serial": "SIM00001"})
        with self.ws() as ws:
            self.post("run", {"mode": "single"})
            self.recv_until(ws, lambda k, p: k == "state" and p["run"]["last_stop_reason"] == "single_done")
        # 後から繋いだ画面にも、いま出ているトレースが届く
        with self.ws() as ws:
            # 同じ回に届いた状態はトレースより先に送られる（server._Client._run）ので、トレースを待つ間に来た
            # 「単発が終わった」状態も覚えておく（後で待つと、もう来ない）
            done = []

            def trace_or_done(k, p):
                if k == "state" and p["run"]["state"] == "stopped" and p["run"]["last_stop_reason"] == "single_done":
                    done.append(p)
                return k == "trace"

            header, values = self.recv_until(ws, trace_or_done)
            self.assertEqual(header["trace"], 1)
            self.assertEqual(len(values), header["axis"]["points"])

    def test_static_files(self):
        st, body = self.get("/")
        html = body.decode("utf-8")
        self.assertEqual(st, 200)
        self.assertIn('id="app"', html)
        # 外から読むのは版を固定した Vue と uPlot だけ（A §3）
        ext = re.findall(r'(?:src|href)="(https?://[^"]+)"', html)
        self.assertTrue(ext)
        for u in ext:
            self.assertRegex(u, r"(vue|uplot)@\d+\.\d+\.\d+/")
        # 自前のファイルは全部サーバーから取れる
        local = re.findall(r'(?:src|href)="(?!https?://)([^"]+)"', html)
        self.assertIn("app.js", local)
        for f in local:
            self.assertEqual(self.get("/" + f)[0], 200, f)


class HubTest(unittest.TestCase):
    """トレースの間引き（A §4.3: 1本あたり 30回/秒まで。途中のものは捨て、最後のものは必ず送る）。"""

    def test_throttle_keeps_first_and_last(self):
        async def go():
            hub = web._Hub(api=None, trace_rate_hz=10)  # 間隔 0.1 秒
            hub._loop = asyncio.get_running_loop()
            sent = []

            class C:
                def put_trace(self, key, data):
                    sent.append((key, decode_trace(data)[0]["sweep_no"]))

            hub.clients.add(C())
            for i in range(10):
                hub.dispatch("trace", {"trace": 1, "sweep_no": i, "values": np.zeros(4, np.float32)})
                hub.dispatch("trace", {"trace": 2, "sweep_no": 100 + i, "values": np.zeros(4, np.float32)})
            self.assertEqual(sent, [("1", 0), ("2", 100)])  # 最初の1本はすぐ
            await asyncio.sleep(0.25)
            # 間隔が明けたら最後の1本だけ（途中の 1〜8 は捨てる）
            self.assertEqual(sorted(sent), [("1", 0), ("1", 9), ("2", 100), ("2", 109)])
            hub.stop()

        asyncio.run(go())


class EncodeTest(unittest.TestCase):
    def test_trace_frame(self):
        vals = np.array([1.0, np.nan, -3.5], dtype=np.float32)
        pts = np.array([True, True, False])
        data = web.encode_trace({"trace": "F1.1", "values": vals, "axis": {"freqs_hz": np.array([1, 5, 9], np.int64), "points": 3},
                                 "flags": {"points": {"transient": pts}, "out_of_cal_range": np.array([False, True, True])}})
        n = struct.unpack("<I", data[:4])[0]
        self.assertEqual((4 + n) % 4, 0)  # 値の並びが4バイト境界（JavaScript の Float32Array で読める）
        h, v = decode_trace(data)
        self.assertEqual(h["flags"]["points"]["transient"], [[0, 2]])
        self.assertEqual(h["flags"]["out_of_cal_range"], [[1, 3]])
        self.assertEqual(h["axis"]["freqs_hz"], [1, 5, 9])
        self.assertEqual(v[0], 1.0)
        self.assertTrue(math.isnan(v[1]))

    def test_jsonable(self):
        self.assertEqual(web.to_jsonable({"a": float("nan"), "b": np.int64(3), "c": (1, 2)}), {"a": None, "b": 3, "c": [1, 2]})


@unittest.skipUnless(shutil.which("node"), "Node が無い")
class UnitsJsTest(unittest.TestCase):
    """ui_web/units.js が core/units.py と同じ値を返すか（Node で読む）。"""

    INPUTS = ["1.2G", "300k", "1500", "0.5", "12100", "-100", "1.0000001", "2.5e", "abc", "12.1 GHz", "100 kHz",
              "0.00005", "0.0000005", "0.0000015", "0.0000025", "-0.0000025", ".5", "+7M", "1.234567890123G", "", "1..2"]
    HZ = [0, 1, 999, 1000, 123456, 1_000_000, 1_234_567_890, 12_100_000_000, -100_000_000, 10**10, 5]

    def test_same_as_python(self):
        script = (
            "const u = require(process.argv[1]);"
            "const inp = JSON.parse(process.argv[2]); const hz = JSON.parse(process.argv[3]);"
            "const p = inp.map(t => { try { return u.parseFreqHz(t); } catch (e) { return null; } });"
            "const f = hz.map(h => u.formatFreq(h));"
            "console.log(JSON.stringify({p, f}));"
        )
        out = subprocess.run(["node", "-e", script, str(UI_WEB / "units.js"), json.dumps(self.INPUTS), json.dumps(self.HZ)],
                             capture_output=True, text=True, timeout=30, check=True).stdout
        got = json.loads(out)
        expect_p = []
        for t in self.INPUTS:
            try:
                expect_p.append(parse_freq_hz(t))
            except ValueError:
                expect_p.append(None)
        self.assertEqual(got["p"], expect_p)
        self.assertEqual(got["f"], [format_freq(h) for h in self.HZ])

    # 縦軸のドラッグの REF（J §4.1）: (REF の元, 移動の割合, dB/div)
    DRAGS = [(0.0, -0.05, 10), (0.0, -0.051, 10), (0.0, -0.1, 10), (0.0, -0.11, 10), (0.0, -0.114, 10),
             (0.0, -0.116, 10), (-3.7, -0.05, 10), (-3.7, -0.1, 10), (0.0, 0.2, 5), (0.0, 0.016, 1),
             (12.3, 0.0, 2), (0.0, -0.0149, 10), (0.0, 0.0151, 10), (0.0, 0.25, 10), (-120.0, 1.0, 10),
             (-3.7, -0.07, 10), (-3.7, 0.0, 10), (7.3, 0.012, 5)]

    def test_drag_ref_level_same_as_python(self):
        script = (
            "const u = require(process.argv[1]); const d = JSON.parse(process.argv[2]);"
            "console.log(JSON.stringify(d.map(([r, f, v]) => u.dragRefLevel(r, f, v, 10))));"
        )
        out = subprocess.run(["node", "-e", script, str(UI_WEB / "units.js"), json.dumps(self.DRAGS)],
                             capture_output=True, text=True, timeout=30, check=True).stdout
        self.assertEqual(json.loads(out), [drag_ref_level(r, f, v) for r, f, v in self.DRAGS])


@unittest.skipUnless(shutil.which("node"), "Node が無い")
class AppJsWheelTest(unittest.TestCase):
    """app.js の周波数軸のホイール: Span 固定中は命令を送らず Span の欄に知らせる（C §5.12）。

    Vue と他の部品は形だけの代わりを置き、createApp に渡された methods をそのまま呼ぶ。
    """

    SCRIPT = r"""
globalThis.window = globalThis; window.addEventListener = () => {};
let opts; globalThis.Vue = { createApp: (o) => { opts = o; return { component() {}, directive() {}, mount() {} }; }, markRaw: (x) => x };
globalThis.GSUnits = {}; globalThis.GSTexts = { w: (k) => "W:" + k, get: () => "", dict: () => ({}), fmt: (k) => k };
globalThis.GSApi = { call: async () => ({}) };
for (const n of ["GSAppMenu", "GSAppMarkers", "GSAppFiles", "GSAppRtl", "GSAppDevices", "GSAppDisplay"]) globalThis[n] = {};
globalThis.GSFields = { NumField: {} };
require(process.argv[1]);
const out = {};
for (const lock of [null, 2e9]) {
  const sent = [], notes = [];
  const self = { freqOpsReason: null, sw: { start_hz: 0, stop_hz: 2e9, span_lock_hz: lock }, fv: { start_hz: 0, stop_hz: 2e9, playback: false },
                 sweep: (a, f) => sent.push([a, f]), note: (f, t) => notes.push([f, t]) };
  opts.methods.onFreqWheel.call(self, 5e8, 1);
  out[lock == null ? "off" : "on"] = { sent, notes };
}
console.log(JSON.stringify(out));
"""

    def test_wheel_with_lock(self):
        out = subprocess.run(["node", "-e", self.SCRIPT, str(UI_WEB / "app.js")],
                             capture_output=True, text=True, encoding="utf-8", timeout=30, check=True).stdout
        got = json.loads(out)
        # 固定 ON: 命令を送らず、Span の欄に「Span 固定中」の文（表の span_lock_wheel）を出す
        self.assertEqual(got["on"], {"sent": [], "notes": [["span", "W:span_lock_wheel"]]})
        # 固定 OFF: いままでどおり Center と Span を送る（1段で 0.8 倍）
        self.assertEqual(len(got["off"]["sent"]), 1)
        args, field = got["off"]["sent"][0]
        self.assertEqual((args["span_hz"], args["edited"], field), (1_600_000_000, ["center_hz", "span_hz"], "span"))
        self.assertEqual(got["off"]["notes"], [])


if __name__ == "__main__":
    unittest.main()
