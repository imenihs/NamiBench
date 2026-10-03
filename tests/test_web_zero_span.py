"""Web: ゼロスパンの時間軸と流す表示（設計N §1）。本物の uvicorn と、graph.js・app_zerospan.js を Node で動かす。

- 状態の ui.x_axis（範囲・名前・目盛の単位）と ui.zero_span（表示する長さの欄・時間軸で使わない欄の理由）は
  Qt と同じ screen_model の関数の結果
- マーカー表の列は「時間 (ms)」、区間の最大・最小・平均の行が出る。PNG の配置も時間
- graph.js は時間軸の名前と、目盛の数字を軸の単位（ms・s）で出す。範囲のドラッグ・横軸のホイールは使わない
- app_zerospan.js のトレースの横軸の位置と最も近い点の番号は screen_model.trace_x と同じ
"""
import json
import shutil
import subprocess
import tempfile
import time
import unittest
import urllib.request
from pathlib import Path

from pygigast.api.local import LocalApi, _SimulatorBackend
from pygigast.core.devices.base import load_profile
from pygigast.core.instrument import GIGAST_PROFILE_PATH, Instrument
from pygigast.ui_qt import screen_model, texts
from tests.test_web_server import UI_WEB, _Server

MHZ = 1_000_000
Z = texts.ZERO_SPAN_TEXTS


class WebZeroSpanTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        profile = load_profile(GIGAST_PROFILE_PATH)
        sim = _SimulatorBackend(profile)
        sim.speed = 0.05
        self.api = LocalApi(Instrument({"simulator": sim}, profile=profile, data_root=root / "d", cal_root=root / "c"))
        self.srv = _Server(self.api)

    def tearDown(self):
        self.srv.stop()
        self.api.close()
        self.tmp.cleanup()

    def post(self, name, body=None):
        req = urllib.request.Request(f"{self.srv.base}/api/v1/{name}", data=json.dumps(body or {}).encode(),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read())

    def get_state(self):
        with urllib.request.urlopen(self.srv.base + "/api/v1/state", timeout=20) as r:
            return json.loads(r.read())

    def single(self):
        n0 = self.get_state()["run"]["sweep_count"]
        self.post("run", {"mode": "single"})
        end = time.monotonic() + 20
        while True:
            st = self.get_state()
            if st["run"]["state"] == "stopped" and st["run"]["sweep_count"] > n0:
                return st
            self.assertLess(time.monotonic(), end)
            time.sleep(0.05)

    def test_state_markers_and_png(self):
        self.post("connect", {"backend": "simulator", "serial": "SIM00001"})
        st = self.get_state()
        self.assertEqual(st["ui"]["x_axis"]["kind"], "freq")
        self.assertEqual(st["ui"]["zero_span"]["reason"], Z["not_zero_span"])
        self.post("set_sweep", {"center_hz": 100 * MHZ, "span_hz": 0})
        self.post("set_view", {"zero_span_s": 10})
        self.single()
        st = self.single()
        xa = st["ui"]["x_axis"]
        self.assertEqual((xa["kind"], xa["x0"], xa["x1"], xa["unit"]), ("time", -10_000_000, 0, "s"))
        self.assertEqual(xa["label"], Z["axis_time"].replace("{unit}", "s"))
        self.assertEqual(st["ui"]["zero_span"], screen_model.zero_span_view(
            {k: v for k, v in st.items() if k != "ui"}))
        self.assertEqual(st["ui"]["zero_span"]["freq_only"], Z["freq_only"])
        self.assertEqual(st["ui"]["peaks"]["peak"]["reason"], Z["freq_only"])
        self.post("marker_add", {"kind": "M", "trace": 1, "freq_hz": -30_000})
        st = self.get_state()
        mk = st["markers"]
        self.assertEqual(mk["columns"][2], Z["col_time"])
        rows = {r["id"]: r for r in mk["rows"]}
        self.assertIn("I1.max", rows)
        self.assertEqual(rows["I1.avg"]["cells"][0], Z["row_avg"])
        self.assertEqual((rows["I1.avg"]["cells"][4], rows["I1.avg"]["cells"][7]), (Z["range_window"], ""))
        self.assertEqual(rows["M1"]["edits"]["2"]["parse"], "time")
        ops = {o["name"]: o for o in rows["M1"]["ops"] if o}
        self.assertEqual(ops["row_center"]["why"], Z["freq_only"])
        lay = self.post("png_layout", {"width": 1600, "height": 1000})
        self.assertEqual(lay["table"]["header"][2], "時間 (ms)")
        self.assertEqual(lay["x_title"], "時間（推定） (s)")
        # マウスの位置の読み値も時間（Qt と同じ screen_model.mouse_reading）
        r = self.post("mouse_reading", {"x": -1_000_000, "y": -40})
        self.assertTrue(r["lines"][0].endswith(" ms"))


@unittest.skipUnless(shutil.which("node"), "Node が無い")
class GraphJsTimeAxisTest(unittest.TestCase):
    """graph.js の時間軸: 軸の名前・目盛の数字（μs を軸の単位で割る）・範囲のドラッグをしない。"""

    SCRIPT = r"""
globalThis.window = globalThis;
globalThis.GSTexts = { w: (k) => k, get: () => '', fmtText: (s) => s };
globalThis.GSUnits = { levelText: (v) => String(v) };
const ctx = new Proxy({}, { get: (t, k) => (k in t ? t[k] : () => ({ width: 0 })), set: (t, k, v) => { t[k] = v; return true; } });
globalThis.uPlot = class {
  constructor(opts) { this.o = opts; this.ctx = ctx; this.bbox = { left: 0, top: 0, width: 1000, height: 400 };
                      this.axes = [{}, {}]; this.select = { left: 100, width: 300, top: 0, height: 0 }; }
  valToPos(v) { const [a, b] = this.o.scales.x.range(); return (v - a) / (b - a) * 1000; }
  posToVal(p) { const [a, b] = this.o.scales.x.range(); return a + p / 1000 * (b - a); }
  setScale() {} redraw() {} setSize() {} setSelect() {}
};
require(process.argv[1]);
const picked = [];
const g = new GSGraph({ clientWidth: 1000, clientHeight: 400 }, { colors: {}, static: true, onRangeSelected: (a, b) => picked.push([a, b]) });
const xa = JSON.parse(process.argv[2]);
g.setX(xa.x0, xa.x1, xa);
const ticks = g.u.o.axes[0].values(g.u, [-10e6, -5e6, 0]);
const label = g.u.axes[0].label;
g._onSelect(g.u);
g.setX(0, 1e6, null);
const freqLabel = g.u.axes[0].label;
console.log(JSON.stringify({ ticks, label, picked, zero: g.zeroSpan, freqLabel }));
"""

    def test_time_axis(self):
        xa = screen_model.x_axis_view({"sweep": {"step_hz": 0, "points": 500, "point_time_s": 0.00014},
                                       "view": {"zero_span_s": 10}})
        out = subprocess.run(["node", "-e", self.SCRIPT, str(UI_WEB / "graph.js"), json.dumps(xa)],
                             capture_output=True, text=True, encoding="utf-8", timeout=30, check=True).stdout
        got = json.loads(out)
        self.assertEqual(got["ticks"], ["-10", "-5", "0"])
        self.assertEqual(got["label"], Z["axis_time"].replace("{unit}", "s"))
        self.assertEqual(got["picked"], [])  # 時間軸では範囲を Start/Stop にしない
        self.assertEqual(got["zero"], False)
        self.assertNotEqual(got["freqLabel"], got["label"])


@unittest.skipUnless(shutil.which("node"), "Node が無い")
class AppZeroSpanJsTest(unittest.TestCase):
    SCRIPT = r"""
globalThis.window = globalThis;
globalThis.GSUnits = { formatTimeUs: (v) => 't' + v, formatFreq: (v) => 'f' + v };
require(process.argv[1]);
const axes = JSON.parse(process.argv[2]);
const xs = axes.map((a) => [0, 1, 2].map((i) => GSZeroSpan.traceX(a)(i)));
const idx = axes.map((a) => GSZeroSpan.indexAt(a, 290));
const self = { ui: { zero_span: { time: true } }, off: { type: 'freq' }, shownHz: (h) => h };
Object.defineProperty(self, 'zsView', { get: () => GSAppZeroSpan.computed.zsView.call(self) });
const m = GSAppZeroSpan.methods;
console.log(JSON.stringify({ xs, idx, text: m.xText.call(self, -1500), off: m.offType.call(self) }));
"""

    def test_trace_x_same_as_python(self):
        axes = [{"kind": "time", "start_us": 0.0, "step_us": 140.0, "points": 3},
                {"start_hz": 1000, "step_hz": 100, "points": 3}]
        out = subprocess.run(["node", "-e", self.SCRIPT, str(UI_WEB / "app_zerospan.js"), json.dumps(axes)],
                             capture_output=True, text=True, encoding="utf-8", timeout=30, check=True).stdout
        got = json.loads(out)
        self.assertEqual(got["xs"], [screen_model.trace_x({"axis": a}).tolist() for a in axes])
        self.assertEqual(got["idx"], [2, -7])
        self.assertEqual(got["text"], "t-1500")
        self.assertEqual(got["off"], "level")  # 時間軸では周波数のオフセットを選べない


if __name__ == "__main__":
    unittest.main()
