"""グラフの上のマウスの位置の読み値（設計G §2・§3.3）。

- 共通の中身（screen_model.mouse_reading）: 最も近いトレースの最も近い点・値の単位・印の付いた点の注意
- Qt: グラフの中でマウスを動かすとカーソルの近くにツールチップ、外へ出ると消える、クリックは今までどおり
- Web: `POST /api/v1/mouse_reading` が Qt と同じ文を返す（本物の uvicorn とシミュレータ）
"""
import json
import os
import shutil
import subprocess
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from namibench.api.local import LocalApi, _SimulatorBackend  # noqa: E402
from namibench.core.devices.base import load_profile  # noqa: E402
from namibench.core.instrument import GIGAST_PROFILE_PATH, Instrument  # noqa: E402
from namibench.core.units import format_freq, format_time_us  # noqa: E402
from namibench.ui_qt import screen_model, texts  # noqa: E402

MHZ = 1_000_000


def payload(n, values, start=100 * MHZ, step=MHZ, unit="dBm", points=None, head=0, oc=None):
    v = np.asarray(values, dtype=np.float32)
    pts = {} if points is None else {k: np.asarray(a, dtype=bool) for k, a in points.items()}
    return {"trace": n, "source": "live", "unit": unit, "values": v,
            "axis": {"start_hz": start, "step_hz": step, "points": v.size},
            "flags": {"transient_head_points": head,
                      "out_of_cal_range": np.zeros(v.size, bool) if oc is None else np.asarray(oc, bool),
                      "points": pts}}


class SharedReadingTest(unittest.TestCase):
    def test_nearest_trace_and_point(self):
        a = payload(1, [-50, -40, -30, -20])
        b = payload(2, [-80, -70, -60, -10])
        r = screen_model.mouse_reading([a, b], 101.4 * MHZ, -45.0)
        self.assertEqual((r["trace"], r["freq_hz"], r["level"]), (1, 101 * MHZ, -40.0))
        self.assertEqual(r["lines"], [format_freq(101 * MHZ), texts.tpl("trace_note", n=1, text="-40.00 dBm")])
        self.assertEqual(r["text"], "\n".join(r["lines"]))
        # 縦に近い方（トレース 2）。横はちょうど中間なら低い周波数の点（マーカーと同じ）
        r = screen_model.mouse_reading([a, b], 101.5 * MHZ, -68.0)
        self.assertEqual((r["trace"], r["freq_hz"], r["level"]), (2, 101 * MHZ, -70.0))

    def test_notes_of_marked_points_and_units(self):
        p = payload(1, [-50, np.nan, -30, -20], unit="dB（未校正）", head=1, oc=[0, 0, 1, 0],
                    points={"lo_leak": [0, 0, 0, 1], "transient": [1, 0, 0, 0]})
        r = screen_model.mouse_reading([p], 103 * MHZ, -20.0)
        self.assertEqual(r["lines"], [format_freq(103 * MHZ), texts.tpl("trace_note", n=1, text="-20.00 dB（未校正）"),
                                      texts.DISPLAY_MARKS["lo_leak"]])
        self.assertEqual(screen_model.mouse_reading([p], 102 * MHZ, 0.0)["lines"][2], texts.MARKER_NOTES["out_of_cal_range"])
        self.assertEqual(screen_model.mouse_reading([p], 100 * MHZ, 0.0)["notes"], ["transient"])
        # 値の無い点は数値を作らない
        r = screen_model.mouse_reading([p], 101 * MHZ, -40.0)
        self.assertIsNone(r["level"])
        self.assertEqual(r["lines"][1], texts.tpl("trace_note", n=1, text=texts.MARKER_STATUS["no_value"]))

    def test_value_less_point_falls_back_to_other_trace(self):
        a = payload(1, [-50, np.nan])
        b = payload(2, [-90, -90])
        r = screen_model.mouse_reading([a, b], 101 * MHZ, -40.0)
        self.assertEqual((r["trace"], r["level"]), (2, -90.0))

    def test_no_trace_and_zero_span(self):
        r = screen_model.mouse_reading([], 123_456_789.4, -40.0)
        self.assertEqual(r["lines"], [format_freq(123_456_789)])
        self.assertIsNone(r["trace"])
        # ゼロスパンの横軸は時間（推定。位置はマイクロ秒: 設計N §1）。読み値の位置は ms
        z = payload(1, [-50, -40, -30], start=100 * MHZ, step=0)
        z["axis"] = {"kind": "time", "start_us": 0.0, "step_us": 140.0, "points": 3}
        r = screen_model.mouse_reading([z], 150.0, -40.0, zero_span=True)
        self.assertEqual(r["lines"][0], format_time_us(140))
        self.assertEqual((r["time_us"], r["level"]), (140, -40.0))
        self.assertIsNone(r["freq_hz"])
        self.assertEqual(screen_model.mouse_reading([], -2500.0, 0.0, zero_span=True)["lines"], ["-2.500 ms"])


def _single(api, start=-100 * MHZ, stop=100 * MHZ):
    api.call("connect", backend="simulator", serial="SIM00001").result(10)
    api.call("set_sweep", start_hz=start, stop_hz=stop).result(10)
    n0 = api.get_state().result(5)["run"]["sweep_count"]
    api.call("run", mode="single").result(10)
    end = time.monotonic() + 20
    while True:
        st = api.get_state().result(5)
        if st["run"]["state"] == "stopped" and st["run"]["sweep_count"] > n0:
            return st
        if time.monotonic() > end:
            raise AssertionError("単発の掃引が終わらない")
        time.sleep(0.05)


class QtMouseReadingTest(unittest.TestCase):
    def setUp(self):
        from tests.test_ui_qt_support import UiBase

        class _Ui(UiBase):
            size = (1280, 720)

            def runTest(self):  # noqa: N802
                pass

        self.ui = _Ui()
        self.ui.setUp()
        self.win = self.ui.win

    def tearDown(self):
        self.ui.tearDown()

    def move(self, x, y):
        from PySide6.QtCore import QPointF
        from PySide6.QtTest import QTest

        vb = self.win.plot.getPlotItem().getViewBox()
        at = self.win.plot.mapFromScene(vb.mapViewToScene(QPointF(x, y)))
        QTest.mouseMove(self.win.plot.viewport(), at)
        self.ui.pump(0.15)
        return at

    def test_tooltip_follows_mouse_and_hides_on_leave(self):
        from PySide6.QtCore import QEvent, QPoint
        from PySide6.QtTest import QTest
        from PySide6.QtWidgets import QApplication, QToolTip

        _single(self.win.api)
        self.ui.wait(lambda: self.win.traces.live_payload(1) is not None, what="画面に届く")
        self.ui.pump(0.3)
        mr = self.win.mouse_reading
        # 0Hz（局発の漏れの帯の中）
        self.move(0.0, -30.0)
        p = self.win.traces.live_payload(1)
        expect = screen_model.mouse_reading([p], 0.0, -30.0)
        self.assertEqual(mr.text, expect["text"])
        self.assertIn(format_freq(expect["freq_hz"]), mr.text)
        self.assertIn(texts.tpl("trace_note", n=1, text=f"{expect['level']:.2f} dBm"), mr.text)
        self.assertIn(texts.DISPLAY_MARKS["lo_leak"], mr.text)
        self.assertTrue(QToolTip.isVisible())
        self.assertEqual(QToolTip.text(), mr.text)
        # 帯の外（+50MHz）では印の注意は無い
        self.move(50 * MHZ, -60.0)
        self.assertIn(format_freq(50 * MHZ), mr.text)
        self.assertNotIn(texts.DISPLAY_MARKS["lo_leak"], mr.text)
        # 軸の上（グラフの外）では出さない
        QTest.mouseMove(self.win.plot.viewport(), QPoint(3, self.win.plot.viewport().height() // 2))
        self.ui.pump(0.15)
        self.assertEqual(mr.text, "")
        # グラフから出たら消す
        self.move(50 * MHZ, -60.0)
        self.assertNotEqual(mr.text, "")
        QApplication.sendEvent(self.win.plot.viewport(), QEvent(QEvent.Type.Leave))
        self.ui.pump(0.1)
        self.assertEqual(mr.text, "")
        self.assertFalse(QToolTip.isVisible())

    def test_double_click_still_places_marker(self):
        from PySide6.QtCore import Qt

        _single(self.win.api)
        self.ui.wait(lambda: self.win.traces.live_payload(1) is not None, what="画面に届く")
        at = self.move(50 * MHZ, -60.0)
        from PySide6.QtCore import QEvent, QPointF
        from PySide6.QtGui import QMouseEvent
        from PySide6.QtWidgets import QApplication

        vp = self.win.plot.viewport()
        L, N = Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton
        for kind, button, buttons in ((QEvent.Type.MouseButtonPress, L, L), (QEvent.Type.MouseButtonRelease, L, N),
                                      (QEvent.Type.MouseButtonDblClick, L, L), (QEvent.Type.MouseButtonRelease, L, N)):
            QApplication.sendEvent(vp, QMouseEvent(kind, QPointF(at), QPointF(vp.mapToGlobal(at)), button, buttons,
                                                   Qt.KeyboardModifier.NoModifier))
        self.ui.wait(lambda: len(self.win.marker_snap.get("markers", [])) == 1, what="マーカーが置かれる")
        self.assertEqual(self.win.marker_snap["markers"][0]["freq_hz"], 50 * MHZ)

    def test_throttled(self):
        from PySide6.QtCore import QPointF

        calls = []
        orig = screen_model.mouse_reading
        try:
            screen_model.mouse_reading = lambda *a, **k: (calls.append(1), orig(*a, **k))[1]
            vb = self.win.plot.getPlotItem().getViewBox()
            sig = self.win.plot.scene().sigMouseMoved
            t0 = time.monotonic()
            for i in range(200):
                sig.emit(vb.mapViewToScene(QPointF(i * 1e6, -50.0)))
                self.ui.pump(0.002)
            self.ui.pump(0.1)
            elapsed = time.monotonic() - t0
        finally:
            screen_model.mouse_reading = orig
        self.assertGreater(len(calls), 0)
        self.assertLessEqual(len(calls), elapsed * 30 + 1)


class WebMouseReadingTest(unittest.TestCase):
    def setUp(self):
        from tests.test_web_server import _Server

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

    def post(self, body):
        req = urllib.request.Request(f"{self.srv.base}/api/v1/mouse_reading", data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read())

    def test_same_text_as_qt_and_notes(self):
        _single(self.api)
        time.sleep(0.3)
        p = self.api.call("get_trace", id=1).result(5)
        r = self.post({"x": 0, "y": -30})
        self.assertEqual(r["lines"], screen_model.mouse_reading([p], 0.0, -30.0)["lines"])
        self.assertIn(texts.DISPLAY_MARKS["lo_leak"], r["lines"])
        r = self.post({"x": 50 * MHZ, "y": -60})
        self.assertEqual(r["lines"][0], format_freq(50 * MHZ))
        self.assertTrue(r["lines"][1].startswith(texts.tpl("trace_note", n=1, text="")))
        # 隠したトレースは読まない（マウスの位置の周波数だけ）
        self.api.call("set_trace", number=1, visible=False).result(5)
        time.sleep(0.3)
        r = self.post({"x": 50 * MHZ, "y": -60})
        self.assertIsNone(r["trace"])
        self.assertEqual(r["lines"], [format_freq(50 * MHZ)])

    def test_bad_value(self):
        for body in ({}, {"x": "a", "y": 0}, {"x": float("nan"), "y": 0}):
            with self.assertRaises(urllib.error.HTTPError) as cm:
                self.post(body)
            self.assertEqual(cm.exception.code, 400)


@unittest.skipUnless(shutil.which("node"), "Node が無い")
class WebHoverJsTest(unittest.TestCase):
    """app.js の読み値の取り方: 1つずつ・34ms より詰めない・動かした最後の位置を取る・ボタンを押している間は出さない。"""

    SCRIPT = r"""
globalThis.window = globalThis; window.addEventListener = () => {}; window.innerWidth = 1280; window.innerHeight = 720;
let opts; globalThis.Vue = { createApp: (o) => { opts = o; return { component() {}, directive() {}, mount() {} }; }, markRaw: (x) => x };
globalThis.GSUnits = {}; globalThis.GSTexts = { w: (k) => "W:" + k, get: () => "", dict: () => ({}), fmt: (k) => k };
const sent = [];
globalThis.GSApi = { call: async (name, args) => { sent.push([name, args]); return { lines: ["L" + sent.length] }; } };
for (const n of ["GSAppMenu", "GSAppMarkers", "GSAppFiles", "GSAppRtl", "GSAppDevices", "GSAppDisplay"]) globalThis[n] = {};
globalThis.GSFields = { NumField: {} };
require(process.argv[1]);
const self = { hover: null, hoverLines: null };
for (const k of ["onGraphHover", "hoverFetch", "hoverStyle"]) self[k] = opts.methods[k].bind(self);
(async () => {
  for (let i = 0; i < 20; i++) self.onGraphHover({ x: i, y: -50, cx: 10, cy: 10, buttons: 0 });
  await new Promise((r) => setTimeout(r, 150));
  const afterMoves = sent.length;
  const last = sent[sent.length - 1][1].x;
  const lines = self.hoverLines;
  const style = self.hoverStyle();
  self.onGraphHover({ x: 1270, y: 0, cx: 1270, cy: 710, buttons: 0 });
  const edge = self.hoverStyle();
  self.onGraphHover({ x: 5, y: 0, cx: 10, cy: 10, buttons: 1 });
  const pressed = self.hoverLines;
  self.onGraphHover(null);
  await new Promise((r) => setTimeout(r, 80));
  console.log(JSON.stringify({ afterMoves, last, lines, style, edge, pressed, left: self.hoverLines, name: sent[0][0] }));
})();
"""

    def test_hover_requests(self):
        from tests.test_web_server import UI_WEB

        out = subprocess.run(["node", "-e", self.SCRIPT, str(UI_WEB / "app.js")], capture_output=True, text=True,
                             encoding="utf-8", timeout=30)
        self.assertEqual(out.returncode, 0, out.stderr)
        r = json.loads(out.stdout.strip().splitlines()[-1])
        self.assertEqual(r["name"], "mouse_reading")
        self.assertEqual(r["afterMoves"], 2)  # 最初の1回と、動かし終えた最後の位置の1回
        self.assertEqual(r["last"], 19)
        self.assertEqual(r["lines"], ["L2"])
        self.assertEqual(r["style"], {"left": "26px", "top": "26px"})  # カーソルの右下に離して出す
        self.assertEqual(r["edge"], {"right": "22px", "bottom": "22px"})  # 右下の端では左上へ回す
        self.assertIsNone(r["pressed"])
        self.assertIsNone(r["left"])


if __name__ == "__main__":
    unittest.main()
