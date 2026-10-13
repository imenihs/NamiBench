"""Web: 機器の範囲の外・意味の無い区間の帯と REF の自動（設計J §4.8）。本物の uvicorn と、graph.js を Node で描く。"""
import json
import shutil
import subprocess
import tempfile
import time
import unittest
import urllib.request
from pathlib import Path

from namibench.api.local import LocalApi, _SimulatorBackend
from namibench.core.devices.base import load_profile
from namibench.core.instrument import GIGAST_PROFILE_PATH, Instrument
from namibench.ui_qt import texts
from tests.test_web_server import UI_WEB, _Server

MHZ = 1_000_000


class WebMarksTest(unittest.TestCase):
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

    def test_state_has_bands_with_texts_and_png_warnings(self):
        self.post("connect", {"backend": "simulator", "serial": "SIM00001"})
        self.post("set_sweep", {"start_hz": -100 * MHZ, "stop_hz": 100 * MHZ})
        n0 = self.get_state()["run"]["sweep_count"]
        self.post("run", {"mode": "single"})
        end = time.monotonic() + 20
        while True:
            st = self.get_state()
            if st["run"]["state"] == "stopped" and st["run"]["sweep_count"] > n0:
                break
            self.assertLess(time.monotonic(), end)
            time.sleep(0.05)
        marks = st["ui"]["marks"]
        self.assertEqual([m["kind"] for m in marks], ["negative_mirror", "lo_leak"])
        self.assertEqual(marks[1]["label"], texts.DISPLAY_MARKS["lo_leak"])
        self.assertEqual(marks[1]["color"], "warn")
        self.assertLess(st["ref_auto"]["peaks"]["1"]["max"], -20.0)  # 0Hz の漏れを使っていない
        lay = self.post("png_layout", {"width": 1600, "height": 1000})
        self.assertIn(texts.DISPLAY_MARKS["lo_leak"], json.dumps(lay, ensure_ascii=False))


@unittest.skipUnless(shutil.which("node"), "Node が無い")
class GraphJsBandsTest(unittest.TestCase):
    """graph.js が帯を塗り、説明を横（広い帯）・縦（狭い帯）で書くこと（uPlot は描画の記録だけの代わりを置く）。"""

    SCRIPT = r"""
const calls = [];
const ctx = new Proxy({}, { get: (t, k) => (k in t ? t[k] : (...a) => { calls.push([k, ...a]); return { width: 7 * String(a[0] || '').length }; }),
                           set: (t, k, v) => { t[k] = v; return true; } });
ctx.measureText = (s) => ({ width: 7 * s.length });
globalThis.window = globalThis;
globalThis.GSTexts = { w: (k) => k };
globalThis.uPlot = class {
  constructor(opts) { this.o = opts; this.ctx = ctx; this.bbox = { left: 0, top: 0, width: 1000, height: 400 }; this.axes = [{}, {}]; }
  valToPos(v, s) { const [a, b] = this.o.scales.x.range(); return s === 'x' ? (v - a) / (b - a) * 1000 : 0; }
  setScale() {} redraw() {} setSize() {}
};
require(process.argv[1]);
const g = new GSGraph({ clientWidth: 1000, clientHeight: 400 }, { colors: { zero_db: '#00f' }, static: true });
g.setX(-100e6, 100e6, false);
g.setBands(JSON.parse(process.argv[2]));
g._draw(g.u);
console.log(JSON.stringify(calls.filter((c) => ['fillRect', 'fillText', 'rotate'].includes(c[0]))));
"""

    def test_bands_drawn(self):
        bands = [{"start_hz": -100 * MHZ, "stop_hz": -5 * MHZ, "label": "wide-band-label", "fill": "#888"},
                 {"start_hz": -5 * MHZ, "stop_hz": 5 * MHZ, "label": "narrow-band-label-long-enough", "fill": "#fc6"}]
        out = subprocess.run(["node", "-e", self.SCRIPT, str(UI_WEB / "graph.js"), json.dumps(bands)],
                             capture_output=True, text=True, encoding="utf-8", timeout=30, check=True).stdout
        calls = json.loads(out)
        rects = [c for c in calls if c[0] == "fillRect"]
        self.assertEqual(len(rects), 2)
        self.assertAlmostEqual(rects[0][1], 0.0)
        self.assertAlmostEqual(rects[0][3], 475.0)  # −100〜−5MHz は 1000 画素の 47.5%
        texts_drawn = [c[1] for c in calls if c[0] == "fillText"]
        self.assertEqual(texts_drawn, ["wide-band-label", "narrow-band-label-long-enough"])
        self.assertEqual(len([c for c in calls if c[0] == "rotate"]), 1)  # 50 画素の帯は縦書き


if __name__ == "__main__":
    unittest.main()
