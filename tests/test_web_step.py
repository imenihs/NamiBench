"""Web の画面: SG のステップ掃引（設計L §1.3）と内部の漏れの印（設計L §1.4）。Qt と同じ中身・同じ語（J §5）。

- Web の口: 段ごとの線をトレースのバイナリの後ろに続けて送る・設定を直した知らせの文（Qt と同じ source_view）・
  状態の ui に欄と印・マウスの位置の読み値に漏れの説明（1画素の幅で拾う）
- 画面（ヘッドレスの Edge）: ステップ掃引の欄を押して回す → 結果のトレースに段ごとの線・SG の欄はしている間だけ
  理由付きで変えられない・漏れの印（表示のタブのチェックで消せる）・ピークの一覧の列・崩れを数えて 0 件
  （1280×720・1920×1080）
"""
from __future__ import annotations

import json
import struct
import tempfile
import time
import unittest
from pathlib import Path

import numpy as np
from websockets.sync.client import connect as ws_connect

from pygigast.api.local import LocalApi, _SimulatorBackend
from pygigast.api.web import server as web
from pygigast.api.web import view
from pygigast.core.devices.base import load_profile
from pygigast.core.instrument import GIGAST_PROFILE_PATH, Instrument
from pygigast.ui_qt import texts
from tests.test_web_layout import App, EdgePage, edge_available, open_app
from tests.test_web_server import _Server

MHZ = 1_000_000


def decode_with_steps(data: bytes) -> tuple[dict, np.ndarray, np.ndarray]:
    n = struct.unpack("<I", data[:4])[0]
    header = json.loads(data[4:4 + n].decode("utf-8"))
    count = header["value_count"]
    values = np.frombuffer(data, dtype="<f4", offset=4 + n, count=count)
    st = header.get("steps")
    rows = np.frombuffer(data, dtype="<f4", offset=4 + n + 4 * count).reshape(st["count"], st["points"]) \
        if st else np.zeros((0, 0))
    return header, values, rows


class EncodeTest(unittest.TestCase):
    def test_steps_follow_values(self):
        vals = np.array([1, 2, 3], np.float32)
        rows = np.array([[1, np.nan, 3], [4, 5, 6]], np.float32)
        data = web.encode_trace({"trace": 2, "values": vals, "axis": {"start_hz": 0, "step_hz": 10, "points": 3},
                                 "steps": {"x0": 0.0, "dx": 10.0, "values": rows}})
        header, v, r = decode_with_steps(data)
        np.testing.assert_array_equal(v, vals)
        np.testing.assert_array_equal(r, rows)
        self.assertEqual(header["steps"], {"x0": 0.0, "dx": 10.0, "count": 2, "points": 3})
        # 段ごとの線が無ければ今までと同じ形
        plain = web.encode_trace({"trace": 1, "values": vals})
        header, v, r = decode_with_steps(plain)
        self.assertNotIn("steps", header)
        hlen = struct.unpack("<I", plain[:4])[0]
        self.assertEqual(len(plain), 4 + hlen + 4 * 3)
        # JSON に写すとき（命令 get_trace の戻り値）は入れ子の並び。値の無い点は null
        self.assertEqual(web.to_jsonable(rows)[0], [1.0, None, 3.0])

    def test_line_alpha_same_as_core(self):
        # 段ごとの線の濃さは Qt の画面・PNG・Web の canvas で同じ（core/step_sweep.LINE_ALPHA）
        import re

        from pygigast.core import step_sweep as ss

        js = (Path(web.UI_WEB_DIR) / "graph.js").read_text(encoding="utf-8")
        self.assertEqual(float(re.search(r"STEP_LINE_ALPHA = ([\d.]+);", js).group(1)), ss.LINE_ALPHA)

    def test_result_and_state_texts(self):
        r = view.result_view("set_step_sweep", {}, {"adjusted": [
            {"field": "stop_hz", "requested": 5000 * MHZ, "adopted": 4000 * MHZ, "reason": "sg_range"}]})
        self.assertEqual(r["texts"]["step_notes"], [{"field": "step_stop", "text": "4000 MHz に合わせました（SG の範囲）"}])
        self.assertNotIn("texts", view.result_view("set_step_sweep", {}, {"adjusted": []}))


class WebStepServerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        profile = load_profile(GIGAST_PROFILE_PATH)
        sim = _SimulatorBackend(profile)
        sim.speed = 0.05
        self.api = LocalApi(Instrument({"simulator": sim}, profile=profile, data_root=root / "d", cal_root=root / "c"))
        self.srv = _Server(self.api)
        self.call("connect", backend="simulator", serial="SIM00001")
        self.call("set_sweep", start_hz=300 * MHZ, stop_hz=2100 * MHZ)
        self.call("set_mode", mode="sg", sg_hz=500 * MHZ)

    def tearDown(self):
        self.srv.stop()
        self.api.close()
        self.tmp.cleanup()

    def call(self, name, **kw):
        return self.api.call(name, **kw).result(20)

    def post(self, name, body=None):
        import urllib.error
        import urllib.request

        req = urllib.request.Request(f"{self.srv.base}/api/v1/{name}", data=json.dumps(body or {}).encode(),
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def wait_idle(self):
        end = time.monotonic() + 30
        while self.call("get_state")["step_sweep"]["running"]:
            self.assertLess(time.monotonic(), end, "ステップ掃引が終わらない")
            time.sleep(0.05)

    def test_state_and_notes(self):
        code, r = self.post("set_step_sweep", {"start_hz": 500, "combine": "all"})
        self.assertEqual(code, 200)
        self.assertEqual(r["texts"]["step_notes"][0]["field"], "step_start")
        import urllib.request

        with urllib.request.urlopen(self.srv.base + "/api/v1/state", timeout=20) as resp:
            st = json.loads(resp.read())
        self.assertEqual(st["ui"]["step"]["combine"], "all")
        self.assertEqual([m["freq_hz"] for m in st["ui"]["leaks"]], [956 * MHZ, 1480 * MHZ, 1980 * MHZ])
        code, r = self.post("step_sweep_start", {})
        self.assertEqual(code, 200)
        self.wait_idle()

    def test_mouse_reading_names_the_leak(self):
        self.call("run", mode="single")
        time.sleep(0.5)
        code, r = self.post("mouse_reading", {"x": 1483 * MHZ, "y": -30.0, "px_hz": 1 * MHZ})
        self.assertEqual(code, 200)
        self.assertIn("内部の漏れの可能性 1480 MHz", r["lines"])
        code, r = self.post("mouse_reading", {"x": 1483 * MHZ, "y": -30.0})
        self.assertNotIn("内部の漏れの可能性 1480 MHz", r["lines"])
        code, r = self.post("mouse_reading", {"x": 1483 * MHZ, "y": -30.0, "px_hz": "x"})
        self.assertEqual(code, 400)

    def test_ws_sends_step_lines(self):
        self.call("set_step_sweep", start_hz=400 * MHZ, stop_hz=700 * MHZ, step_hz=100 * MHZ, combine="all", trace=2)
        with ws_connect(f"ws://127.0.0.1:{self.srv.port}/api/v1/events", open_timeout=10, max_size=None) as ws:
            self.call("step_sweep_start")
            end = time.monotonic() + 30
            got = None
            while time.monotonic() < end:
                m = ws.recv(timeout=end - time.monotonic())
                if not isinstance(m, bytes):
                    continue
                header, values, rows = decode_with_steps(m)
                if header.get("trace") == 2 and header.get("steps") and header["steps"]["count"] == 4:
                    got = (header, values, rows)
                    break
        self.assertIsNotNone(got, "段ごとの線の付いたトレース 2 が来ない")
        header, values, rows = got
        self.assertEqual(rows.shape, (4, header["axis"]["points"]))
        self.assertEqual(header["step_sweep"]["combine"], "all")


@unittest.skipUnless(edge_available(), "Microsoft Edge が無い")
class WebStepPageTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = App()
        try:
            cls.page = EdgePage()
        except Exception:
            cls.app.close()
            raise

    @classmethod
    def tearDownClass(cls):
        cls.page.close()
        cls.app.close()

    def setUp(self):
        self.app.call("set_mode", mode="sa")
        self.app.call("set_sweep", start_hz=400 * MHZ, stop_hz=600 * MHZ)
        self.app.call("set_view", leak_marks=True)

    def wait_idle(self):
        end = time.monotonic() + 30
        while self.app.call("get_state")["step_sweep"]["running"]:
            self.assertLess(time.monotonic(), end, "ステップ掃引が終わらない")
            time.sleep(0.05)

    def check_size(self, w: int, h: int) -> None:
        page = self.page
        open_app(page, self.app, w, h)
        found = []
        page.ev("gsApp.tab = 'source'; 1")
        page.settle()
        # SA では押せない（理由付き）
        self.assertTrue(page.ev("document.querySelector('[data-field=step_run]').disabled"))
        self.assertIn(texts.ERRORS["step_sweep_needs_sg"], page.ev("document.querySelector('[data-field=step_run]').title"))
        self.app.call("set_sweep", start_hz=300 * MHZ, stop_hz=2100 * MHZ)
        self.app.call("set_mode", mode="sg", sg_hz=500 * MHZ)
        self.app.call("set_step_sweep", start_hz=1000 * MHZ, stop_hz=1500 * MHZ, step_hz=5 * MHZ, combine="all",
                      trace=2, per_step=1)
        page.wait("!document.querySelector('[data-field=step_run]').disabled", "SG モードで押せる")
        page.settle()
        found += page.problems(".app", f"{w}×{h} 信号源（ステップ掃引）")
        page.ev("document.querySelector('[data-field=step_run]').click(); 1")
        page.wait("!!(gsApp.st.step_sweep.running && document.querySelector('.tasks .task'))", "している間と処理の帯")
        page.settle()
        found += page.problems(".app", f"{w}×{h} 信号源（している間）")
        self.assertTrue(page.ev("document.querySelector('[data-field=step_combine]').disabled"))
        self.assertEqual(page.ev("gsApp.ui.image_reject.reason"), texts.REASON_TEXTS["image_reject_step"])
        tid = self.app.call("get_state")["step_sweep"]["task_id"]
        self.app.call("cancel_task", task_id=tid)
        self.wait_idle()
        page.wait("!gsApp.st.step_sweep.running", "やめた")
        # 小さい段の数で最後まで回して、段ごとの線を確かめる
        self.app.call("set_step_sweep", start_hz=400 * MHZ, stop_hz=700 * MHZ, step_hz=100 * MHZ)
        page.ev("document.querySelector('[data-field=step_run]').click(); 1")
        page.wait("!!(gsApp.$traces.get('2') && gsApp.$traces.get('2').steps && gsApp.$traces.get('2').steps.rows.length === 4"
                  " && !gsApp.st.step_sweep.running)", "段ごとの線")
        page.wait("gsApp.status.includes('ステップ掃引が終わりました（4/4 段）')", "終わりの知らせ")
        self.assertEqual(page.ev("gsApp.graphTraces(gsApp.palette).filter((t) => t.steps).length"), 1)
        # 漏れの印（3つ）と消すチェック
        page.wait("gsApp.$graph.leaks.length === 3", "漏れの印")
        page.ev("gsApp.tab = 'view'; 1")
        page.settle()
        found += page.problems(".app", f"{w}×{h} 表示")
        page.ev("document.querySelector('[data-field=leak_marks]').click(); 1")
        page.wait("gsApp.$graph.leaks.length === 0", "印を消す")
        page.ev("document.querySelector('[data-field=leak_marks]').click(); 1")
        page.wait("gsApp.$graph.leaks.length === 3", "印を戻す")
        # ピークの一覧の列
        self.app.call("run", mode="single")
        time.sleep(0.8)
        page.ev("gsApp.tab = 'marker'; gsApp.peakTrace = 1; gsApp.getPeakList(); 1")
        page.wait("document.querySelectorAll('[data-field=peak_leak]').length >= 3", "ピークの一覧")
        page.settle()
        cells = page.ev("[...document.querySelectorAll('.mtable.peaks tbody tr')].map((tr) => [tr.cells[0].textContent.trim(), tr.cells[4].textContent.trim(), tr.cells[4].title])")
        by = {c[0]: c for c in cells}
        self.assertEqual(by["1480"][1], texts.LABELS["peak_leak"])
        self.assertIn("SG + 1480 MHz", by["1980"][2])
        self.assertEqual(by["500"][1], "")
        found += page.problems(".app", f"{w}×{h} マーカー（漏れの印）")
        self.assertEqual(page.errors, [])
        self.assertEqual(sorted(set(found)), [])

    def test_1280x720(self):
        self.check_size(1280, 720)

    def test_1920x1080(self):
        self.check_size(1920, 1080)
