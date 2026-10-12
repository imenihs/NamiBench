"""Web の設計P（ウォーターフォール・密度・再生・バンドプラン）: バイナリの形・Web の口・waterfall.js の計算・画面。

- イベント waterfall・density と命令 waterfall_rows はバイナリ（P §8）。Python で読み戻して確かめる
- 読み値の文は Qt と同じ display_view.waterfall_reading（Web の口 waterfall_reading）
- バンドプランの読み込みはアップロード、書き出しはダウンロード（Web の口はパスを受け取らない: 設計A §4.3.2 の 3）
- waterfall.js の行を横の列に移す計算が Qt の waterfall_view.resample と同じ（Node）
- 画面（ヘッドレスの Edge）: 表示の組み合わせ・ウォーターフォールの行・密度・再生の帯・ブックマーク
"""
from __future__ import annotations

import json
import shutil
import struct
import subprocess
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

import numpy as np

from pygigast.api.web import server as web
from pygigast.ui_qt import display_view, texts
from tests.test_web_server import UI_WEB, WebServerTest

MHZ = 1_000_000


def decode(data: bytes) -> dict:
    hlen = struct.unpack("<I", data[:4])[0]
    h = json.loads(data[4:4 + hlen].decode("utf-8"))
    dt = np.uint8 if h["value_type"] == "uint8" else np.dtype("<f4")
    h["values"] = np.frombuffer(data[4 + hlen:], dtype=dt)
    return h


class EncodeTest(unittest.TestCase):
    def test_waterfall_round_trip(self):
        rows = [{"t": 1.0, "t_end": 1.0, "start_hz": 1, "step_hz": 2, "n": 3, "seg": 1, "sweeps": 1, "pos": None,
                 "cond": {}, "zero_span": False, "values": np.array([1, 2, 3], dtype=np.float32)},
                {"t": 2.0, "t_end": 2.0, "start_hz": 1, "step_hz": 2, "n": 2, "seg": 1, "sweeps": 1, "pos": [0, 1],
                 "cond": {}, "zero_span": False, "values": np.array([np.nan, 5], dtype=np.float32)}]
        h = decode(web.encode_waterfall({"epoch": 3, "seq": 9, "range": {"lo": -1, "hi": 1}, "rows": rows}))
        self.assertEqual((h["kind"], h["epoch"], h["seq"], h["value_count"]), ("waterfall", 3, 9, 5))
        self.assertEqual([r["n"] for r in h["rows"]], [3, 2])
        self.assertNotIn("values", h["rows"][0])
        np.testing.assert_array_equal(h["values"][:3], [1, 2, 3])
        self.assertTrue(np.isnan(h["values"][3]))

    def test_density_round_trip(self):
        img = (np.arange(12, dtype=np.uint8).reshape(3, 4))
        h = decode(web.encode_density({"image": img, "bins": 3, "cols": 4, "seconds": None}))
        self.assertEqual((h["kind"], h["value_type"], h["bins"]), ("density", "uint8", 3))
        np.testing.assert_array_equal(h["values"].reshape(3, 4), img)


class WebDisplayServerTest(unittest.TestCase):
    # 土台（シミュレータと本物の uvicorn・post・get・ws）は test_web_server と同じものを借りる（試験そのものは借りない）
    setUp = WebServerTest.setUp
    tearDown = WebServerTest.tearDown
    post = WebServerTest.post
    get = WebServerTest.get
    ws = WebServerTest.ws

    def post_bin(self, name, body):
        req = urllib.request.Request(f"{self.srv.base}/api/v1/{name}", data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.headers.get("content-type"), r.read()

    def put(self, name, data: bytes):
        req = urllib.request.Request(f"{self.srv.base}/api/v1/uploads?name={urllib.request.quote(name)}", data=data,
                                     method="PUT", headers={"Content-Type": "application/octet-stream"})
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read())

    def single(self):
        n0 = self.api.get_state().result(5)["run"]["sweep_count"]
        self.post("run", {"mode": "single"})
        end = time.monotonic() + 20
        while True:
            st = self.api.get_state().result(5)
            if st["run"]["state"] == "stopped" and st["run"]["sweep_count"] > n0:
                return st
            self.assertLess(time.monotonic(), end)
            time.sleep(0.05)

    def test_rows_binary_events_and_reading(self):
        self.post("connect", {"backend": "simulator", "serial": "SIM00001"})
        self.post("set_sweep", {"start_hz": 50 * MHZ, "stop_hz": 200 * MHZ})
        self.post("set_view", {"layout": "density"})
        with self.ws() as ws:
            self.single()
            got = {}
            end = time.monotonic() + 20
            while len(got) < 2 and time.monotonic() < end:
                m = ws.recv(timeout=20)
                if isinstance(m, bytes):
                    h = decode(m)
                    if h["kind"] in ("waterfall", "density"):
                        got[h["kind"]] = h
            self.assertEqual(sorted(got), ["density", "waterfall"])
            self.assertEqual(got["waterfall"]["rows"][0]["start_hz"], 50 * MHZ)
            self.assertEqual(got["density"]["values"].size, got["density"]["bins"] * got["density"]["cols"])
        ctype, data = self.post_bin("waterfall_rows", {"max_rows": 10})
        self.assertEqual(ctype, "application/octet-stream")
        h = decode(data)
        self.assertEqual(len(h["rows"]), 1)
        self.assertEqual(h["value_count"], h["rows"][0]["n"])
        st, r = self.post("waterfall_reading", {"t": h["rows"][0]["t"], "freq_hz": 60 * MHZ, "level": -50.25,
                                                "unit": "dBm", "sweeps": 3})
        self.assertEqual(st, 200)
        want = display_view.waterfall_reading(h["rows"][0]["t"], 3, 60 * MHZ, -50.25, "dBm", 0)
        self.assertEqual(r["lines"], want)  # Qt のツールチップと同じ文
        st, r = self.post("waterfall_rows", {"max_rows": 0})
        self.assertEqual((st, r["error"]["code"]), (400, "bad_value"))
        st, info = self.get("/api/v1/info")
        self.assertEqual(len(json.loads(info)["colormaps"]["viridis"]), 768)

    def test_state_view_has_display_ui(self):
        st, body = self.get("/api/v1/state")
        ui = json.loads(body)["ui"]
        d = ui["display"]
        self.assertEqual(d["tab"]["layout"], "spectrum")
        self.assertEqual(d["parts"]["waterfall"], texts.REASON_TEXTS["layout_waterfall"])
        self.assertFalse(d["playback"]["visible"])
        self.assertEqual([c["code"] for c in d["tab"]["colormaps"]][0], "viridis")
        self.assertEqual(ui["run_reason"], texts.ERRORS["not_connected"])

    def test_bandplan_upload_and_download(self):
        plan = {"format": "pygigast-bandplan", "version": 1,
                "bands": [{"name": "FM", "start_hz": 76 * MHZ, "stop_hz": 95 * MHZ, "color": "#4fa3ff"}],
                "bookmarks": [{"name": "A", "hz": 80 * MHZ}]}
        up = self.put("plan.json", json.dumps(plan).encode("utf-8"))
        st, r = self.post("bandplan_load", {"upload_id": up["upload_id"]})
        self.assertEqual(st, 200, r)
        state = self.api.get_state().result(5)
        self.assertEqual(state["bandplan"]["bands"][0]["name"], "FM")
        st, r = self.post("bandplan_load", {"path": "C:/x.json"})
        self.assertEqual(r["error"]["code"], "path_not_allowed")
        st, r = self.post("bandplan_save", {"name": "mine"})
        self.assertEqual(st, 200, r)
        self.assertEqual(r["name"], "mine.json")
        st, data = self.get(f"/api/v1/files/{r['file_id']}")
        self.assertEqual(json.loads(data.decode("utf-8"))["bookmarks"], [{"name": "A", "hz": 80 * MHZ}])

    def test_playback_outputs_take_a_name(self):
        # 切り出し・記録の CSV は名前で頼み、サーバーが置き場所を決める（パスは受け取らない）
        for cmd, ext in (("playback_extract", ".h5"), ("playback_export_csv", ".csv")):
            st, r = self.post(cmd, {"name": "../cut", "t_lo": 0, "t_hi": 1})
            self.assertEqual((st, r["error"]["code"]), (400, "not_playing"), cmd)
            st, r = self.post(cmd, {"path": "C:/x" + ext})
            self.assertEqual(r["error"]["code"], "path_not_allowed", cmd)


@unittest.skipUnless(shutil.which("node"), "Node が無い")
class WaterfallJsTest(unittest.TestCase):
    """waterfall.js の resample が Qt の waterfall_view.resample と同じ列を作る（Qt と Web で同じ絵: P §8）。"""

    def test_resample_same_as_python(self):
        from pygigast.ui_qt.waterfall_view import resample

        rng = np.random.default_rng(1)
        cases = []
        for n, w, x0, x1, start, step in ((501, 700, 50e6, 200e6, 50e6, 3e5), (20, 700, 50e6, 200e6, 60e6, 1e6),
                                          (4096, 300, 0, 1e9, 1e8, 1e5), (11, 50, 0, 10, 0, 1)):
            v = rng.normal(-80, 5, n).astype(np.float32)
            v[3] = np.nan
            cases.append({"row": {"start_hz": start, "step_hz": step, "values": [None if np.isnan(x) else float(x) for x in v]},
                          "x0": x0, "x1": x1, "w": w})
        script = ("globalThis.window = globalThis; require(process.argv[1]);"
                  "const cases = JSON.parse(require('fs').readFileSync(0, 'utf-8'));"
                  "const out = cases.map((c) => { const r = { ...c.row, values: Float32Array.from(c.row.values.map((x) => x === null ? NaN : x)) };"
                  " return Array.from(GSWaterfallUtil.resample(r, c.x0, c.x1, c.w)).map((x) => Number.isNaN(x) ? null : x); });"
                  "console.log(JSON.stringify(out));")
        out = subprocess.run(["node", "-e", script, str(UI_WEB / "waterfall.js")], input=json.dumps(cases),
                             capture_output=True, text=True, encoding="utf-8", timeout=30, check=True).stdout
        got = json.loads(out)
        for c, g in zip(cases, got):
            row = {"start_hz": c["row"]["start_hz"], "step_hz": c["row"]["step_hz"],
                   "values": np.array([np.nan if x is None else x for x in c["row"]["values"]], dtype=np.float32)}
            want = resample(row, c["x0"], c["x1"], c["w"])
            g = np.array([np.nan if x is None else x for x in g], dtype=np.float32)
            np.testing.assert_allclose(g, want, atol=1e-4, equal_nan=True)


try:
    from tests.test_web_layout import App, EdgePage, edge_available, open_app
except Exception:  # noqa: BLE001
    edge_available = lambda: False  # noqa: E731


@unittest.skipUnless(edge_available(), "Microsoft Edge が無い")
class WebDisplayUiTest(unittest.TestCase):
    """Web の画面: 表示の組み合わせ・ウォーターフォールの行・密度の絵・再生の帯・ブックマーク（Qt と同じ動き）。"""

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

    def sweep(self, n=1):
        for _ in range(n):
            n0 = self.app.call("get_state")["run"]["sweep_count"]
            self.app.call("run", mode="single")
            end = time.monotonic() + 20
            while True:
                st = self.app.call("get_state")
                if st["run"]["state"] == "stopped" and st["run"]["sweep_count"] > n0:
                    break
                self.assertLess(time.monotonic(), end)
                time.sleep(0.05)

    def test_waterfall_density_bookmark_playback(self):
        p = self.page
        open_app(p, self.app, 1280, 720)
        p.ev("gsApp.tab = 'view'; 1")
        # 表示の組み合わせ（ラジオ）→ ウォーターフォール
        p.ev("document.querySelector('input[name=layout][value=waterfall]').click(); 1")
        p.wait("gsApp.layoutCode === 'waterfall' && !!gsApp.$wf", "＋ウォーターフォール")
        self.app.call("record_start")
        self.sweep(3)
        p.wait("gsApp.$wf.shown.length >= 3", "1掃引＝1行")
        p.settle()
        self.assertTrue(p.ev("gsApp.$wf.follow"))
        self.assertEqual(p.ev("document.querySelector('[data-field=wf_follow]').disabled"), True)
        # 行のクリック → 過去のトレース
        p.ev("gsApp.$wf.click(10, 1); 1")
        p.wait("gsApp.files.some((f) => f.kind === 'past')", "過去のトレース")
        # 時刻の範囲
        p.ev("(() => { const s = gsApp.$wf.shown; gsApp.wfTimeRange(s[s.length - 1].t, s[0].t); return 1; })()")
        p.wait("!gsApp.wfFollow && !document.querySelector('[data-field=wf_follow]').disabled", "時間の範囲")
        p.ev("document.querySelector('[data-field=wf_follow]').click(); 1")
        p.wait("gsApp.wfFollow", "最新へ")
        # 密度
        p.ev("document.querySelector('input[name=layout][value=density]').click(); 1")
        p.wait("gsApp.layoutCode === 'density'", "＋密度")
        self.sweep(1)
        p.wait("!!(gsApp.$dens.d && gsApp.$dens.d.bins)", "密度の絵")
        p.wait("gsApp.densitySeconds.startsWith('約')", "何秒分の密度か")
        # ブックマーク（表示のタブで足す → グラフの下の端の印）
        p.ev("gsApp.bmName = 'ビーコン'; gsApp.bmFreq = '120 MHz'; gsApp.bookmarkAdd(); 1")
        p.wait("gsApp.bpView.list.length === 1", "ブックマークを足す")
        p.settle()
        self.assertEqual(p.ev("gsApp.$graph.planMarks.length"), 1)
        # 再生
        self.app.call("record_stop")
        h5 = Path(self.app.tmp.name) / "web_rec.h5"
        r = self.app.call("record_finish", path=str(h5))
        end = time.monotonic() + 20
        while not h5.exists():
            self.assertLess(time.monotonic(), end)
            time.sleep(0.1)
        self.app.call("open", paths=[str(h5)])
        p.wait("gsApp.pbView.visible && gsApp.pbActive", "再生の帯")
        p.wait("!!document.querySelector('.pbbar [data-field=pb_play]')", "再生の帯の部品")
        self.assertTrue(p.ev("document.querySelector('.run-btn').disabled"))
        p.wait("gsApp.statusItems.some((s) => s.key === 'view' && s.text.startsWith('再生'))", "状態バーの再生")
        p.ev("gsApp.pbSlide(2); 1")
        p.wait("gsApp.pbView.index === 2", "つまみでカーソル")
        # ライブの条件を変える欄は理由付きで使えない（RBW・Span 固定: 設計P §5。Qt と同じ）
        why = texts.REASON_TEXTS["playback_active"]
        r = p.ev("(() => { const s = document.querySelector('.field-select'); const l = document.querySelector('button.lock');"
                 " return [s.disabled, s.title, l.disabled, l.title]; })()")
        self.assertTrue(r[0] and r[2], r)
        self.assertIn(why, r[1])
        self.assertIn(why, r[3])
        # 時間の範囲を選んだまま別の記録を開く → 新しい記録は全体を出す（記録ごとの見方は捨てる: P §5）
        p.ev("(() => { const s = gsApp.$wf.shown; gsApp.wfTimeRange(s[s.length - 1].t, s[s.length - 1].t + 0.001); return 1; })()")
        p.wait("!!gsApp.wfWindow", "時間の範囲")
        h5b = Path(self.app.tmp.name) / "web_rec2.h5"
        h5b.write_bytes(h5.read_bytes())
        self.app.call("open", paths=[str(h5b)])
        p.wait("gsApp.pbView.name === 'web_rec2.h5' && gsApp.wfWindow === null && gsApp.$wf.shown.length >= 3",
               "新しい記録の全体")
        p.ev("document.querySelector('[data-field=pb_live]').click(); 1")
        p.wait("!gsApp.pbView.visible", "ライブに戻る")
        self.assertEqual(p.errors, [])

    def test_zero_span_hides_lower_parts_with_reason(self):
        # ゼロスパン（横軸が時間: 設計N §1）の間は下の部分を出さず、＋ウォーターフォール・＋密度を理由付きで選べない（Qt と同じ）
        p = self.page
        open_app(p, self.app, 1280, 720)
        p.ev("gsApp.tab = 'view'; 1")
        self.app.call("set_view", layout="waterfall")
        p.wait("gsApp.lowerVisible('waterfall')", "ウォーターフォールを出す")
        self.app.call("set_sweep", center_hz=100 * MHZ, span_hz=0)
        p.wait("!gsApp.lowerVisible('waterfall') && gsApp.shownLayout === 'spectrum'", "ゼロスパンで隠す")
        why = texts.REASON_TEXTS["layout_zero_span"]
        r = p.ev("(() => { const b = document.querySelector('input[name=layout][value=waterfall]');"
                 " return [b.checked, b.disabled, b.title]; })()")
        self.assertEqual(r, [True, True, why])  # 設定は変えず（印は付いたまま）、選べない理由
        self.assertEqual(p.ev("gsApp.menuItem('view_density').why"), why)
        self.app.call("set_sweep", start_hz=50 * MHZ, stop_hz=200 * MHZ)
        p.wait("gsApp.lowerVisible('waterfall')", "ゼロスパンを抜けたら出す")
        self.app.call("set_view", layout="spectrum")
        self.assertEqual(p.errors, [])


@unittest.skipUnless(edge_available(), "Microsoft Edge が無い")
class WebPlaybackFollowTest(unittest.TestCase):
    """再生中の「デルタの2点を Start・Stop に」（設計C §5.13）・色の範囲の「自動」（P §9.2）・密度の元（P §9.3）を Web の
    画面で確かめる（Qt の test_ui_qt_display.PlaybackFollowTest と同じ形の記録・同じ確かめ方）。"""

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

    def single(self):
        n0 = self.app.call("get_state")["run"]["sweep_count"]
        self.app.call("run", mode="single")
        end = time.monotonic() + 20
        while True:
            st = self.app.call("get_state")
            if st["run"]["state"] == "stopped" and st["run"]["sweep_count"] > n0:
                return
            self.assertLess(time.monotonic(), end)
            time.sleep(0.05)

    def test_delta_span_colour_range_and_density_in_playback(self):
        from pygigast.core import waterfall as wf

        app, p = self.app, self.page
        app.call("set_sweep", start_hz=50 * MHZ, stop_hz=200 * MHZ)
        app.call("record_start")
        for _ in range(3):
            self.single()
        app.call("set_sweep", start_hz=300 * MHZ, stop_hz=2100 * MHZ)
        app.call("set_mode", mode="sg", sg_hz=500 * MHZ)
        for _ in range(3):
            self.single()
        end = time.monotonic() + 20
        while app.call("get_state")["recording"].get("sweeps", 0) < 6:
            self.assertLess(time.monotonic(), end)
            time.sleep(0.05)
        app.call("record_stop")
        h5 = Path(app.tmp.name) / "follow_rec.h5"
        app.call("record_finish", path=str(h5))
        end = time.monotonic() + 20
        while not h5.exists():
            self.assertLess(time.monotonic(), end)
            time.sleep(0.1)
        app.call("set_mode", mode="sa")
        app.call("set_sweep", span_lock=True)  # ライブの Span 固定（再生では変えない）
        live = dict(app.call("get_state")["sweep"])
        self.assertIsNotNone(live["span_lock_hz"])
        open_app(p, app, 1280, 720)
        app.call("open", paths=[str(h5)])
        p.wait("gsApp.pbView.visible && gsApp.pbActive", "再生の帯")
        # 色の範囲の「自動」は出している行の全体で決める（P §9.2）。カーソルを動かしても変えない
        rows = app.call("waterfall_rows", max_rows=100)["rows"]
        want = wf.range_of(np.concatenate([np.asarray(r["values"], dtype=float) for r in rows]))
        self.assertNotEqual(want, wf.range_of(np.asarray(rows[0]["values"], dtype=float)))
        p.wait(f"gsApp.$wf.lo === {want[0]} && gsApp.$wf.hi === {want[1]}", "記録の全体の色の範囲")
        for index in (5, 0, 3):
            p.ev(f"gsApp.pbSlide({index}); 1")
            p.wait(f"gsApp.pbView.index === {index}", "つまみでカーソル")
            time.sleep(0.3)
            self.assertEqual(p.ev("[gsApp.$wf.lo, gsApp.$wf.hi]"), list(want), index)
        # 密度の元は記録の中のカーソルまでの直近の掃引（P §9.3）: 始め（0）から 2 へ飛ばすと、通過した2掃引でなく
        # カーソルまでの3掃引。何秒分かは記録の時刻から（Qt と同じ文）
        p.ev("gsApp.pbSlide(0); 1")
        p.wait("gsApp.pbView.index === 0", "始め")
        p.ev("gsApp.tab = 'view'; 1")
        p.ev("document.querySelector('input[name=layout][value=density]').click(); 1")
        p.wait("gsApp.layoutCode === 'density' && !!(gsApp.$dens && gsApp.$dens.d && gsApp.$dens.d.sweeps === 1)",
               "カーソルまでの1掃引")
        p.ev("gsApp.pbSlide(2); 1")
        p.wait("!!(gsApp.$dens.d && gsApp.$dens.d.sweeps === 3)", "カーソルまでの3掃引")
        d = app.call("density_get")
        interval = wf.sweep_interval_s([r["t"] for r in rows[:3]])
        self.assertAlmostEqual(d["interval_s"], interval)
        p.wait("gsApp.densitySeconds === %s" % json.dumps(display_view.density_seconds_text(d["half"] * interval)),
               "何秒分か（記録の時刻から）")
        # 「デルタの2点を Start・Stop に」（設計C §5.13）: Span 固定が ON でも表示の範囲を2点にし、固定は変えず
        # 「Span 固定を外しました」も出さない
        app.call("marker_clear")
        app.call("marker_add", kind="M", trace=1, freq_hz=80 * MHZ)
        app.call("marker_add", kind="M", trace=1, freq_hz=150 * MHZ)
        app.call("marker_set", id="M2", ref="M1")
        marks = {m["id"]: m["freq_hz"] for m in app.call("get_state")["markers"]["markers"]}
        lo, hi = sorted((marks["M1"], marks["M2"]))
        p.wait("gsApp.markers.markers.length === 2", "マーカー")
        p.ev("gsApp.selectedMarker = 'M2'; gsApp.handlers().marker_span(); 1")
        p.wait(f"gsApp.pbView && gsApp.st.playback.view.start_hz === {lo} && gsApp.st.playback.view.stop_hz === {hi}",
               "表示の範囲が2点")
        time.sleep(0.3)
        st = app.call("get_state")
        self.assertEqual(st["sweep"]["span_lock_hz"], live["span_lock_hz"])
        self.assertNotEqual(p.ev("(gsApp.notes.span || {}).text || ''"), texts.LABELS["span_lock_released"])
        p.ev("document.querySelector('[data-field=pb_live]').click(); 1")
        p.wait("!gsApp.pbView.visible", "ライブに戻る")
        st = app.call("get_state")
        self.assertEqual((st["sweep"]["start_hz"], st["sweep"]["stop_hz"], st["sweep"]["span_lock_hz"]),
                         (live["start_hz"], live["stop_hz"], live["span_lock_hz"]))
        self.assertEqual(p.errors, [])
