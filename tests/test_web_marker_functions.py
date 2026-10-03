"""マーカーの機能の dB の欄（打てるドロップダウン）とグラフの機能の線（設計G §3.6。Web と、Qt と共有する行）。

- 機能の線の形（marker_rows.function_line）: 交点が無い側・値なしの区間に当たった側は縦線を出さない、
  マーカーに値が無ければ線は無い
- サーバー: マーカー表の行に line（横線のレベルと縦線の周波数）が付く・set_view で view.function_lines を切り替える
- fields.js の dB の欄（db-field）: 一覧・範囲・丸め・知らせの文が Qt の ui_qt/fields.py と同じ。一覧から選ぶ・打つ・
  ホイールで 1dB・↑↓ はカーソルの桁・範囲の外と読めない値は知らせて送らない
- graph.js: 機能の線をマーカーの色（選んでいるマーカーは強調の色）の不透明度 約 35% の点線で描く・ゼロスパンでは描かない
- 画面のファイル: マーカーの機能とピークの一覧の dB の欄が db-field、切り替えが set_view
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
from pygigast.api.web import server as web
from pygigast.core.devices.base import load_profile
from pygigast.core.instrument import GIGAST_PROFILE_PATH, Instrument
from pygigast.ui_qt import marker_rows
from pygigast.ui_qt import texts as qt_texts
from pygigast.ui_qt.fields import (BANDWIDTH_CHOICES, BANDWIDTH_RANGE, FLOOR_CHOICES, FLOOR_RANGE, REF_CHOICES,
                                   THRESHOLD_RANGE, db_choice_text, range_note, round_to)
from tests.test_web_server import UI_WEB, _Server

MHZ = 1_000_000


class FunctionLineShapeTest(unittest.TestCase):
    def test_sides_without_crossing_have_no_vertical_line(self):
        ok = {"left_status": "ok", "left_hz": 100, "right_status": "ok", "right_hz": 200}
        self.assertEqual(marker_rows.function_line(-33.0, ok), {"level": -33.0, "freqs_hz": [100, 200]})
        edge = {"left_status": "ok", "left_hz": 100, "right_status": "edge", "right_hz": None}
        self.assertEqual(marker_rows.function_line(-33.0, edge), {"level": -33.0, "freqs_hz": [100]})
        gap = {"left_status": "no_value_region", "left_hz": None, "right_status": "edge", "right_hz": None}
        self.assertEqual(marker_rows.function_line(-50.0, gap), {"level": -50.0, "freqs_hz": []})
        self.assertIsNone(marker_rows.function_line(None, ok))

    def test_rows_carry_lines_only_while_marker_has_value(self):
        band = {"n_db": 3.0, "status": "ok", "left_status": "ok", "left_hz": 10, "right_status": "ok", "right_hz": 30,
                "width_hz": 20, "center_hz": 20, "q": 1.0}
        th = {"level": -40.0, "side": "above", "status": "ok", "left_status": "ok", "left_hz": 5,
              "right_status": "edge", "right_hz": None, "width_hz": None, "center_hz": None, "q": None}
        m = {"id": "M1", "kind": "M", "trace": 1, "freq_hz": 20, "level": -30.0, "unit": "dBm", "status": "ok",
             "bandwidth": [band, {**band, "n_db": 60.0}], "shape_factor": 1.0, "threshold": th}
        rows = {r["id"]: r for r in marker_rows.rows_from_snapshot({"markers": [m]})}
        self.assertEqual(rows["M1.bw3"]["line"], {"level": -33.0, "freqs_hz": [10, 30]})
        self.assertEqual(rows["M1.bw60"]["line"], {"level": -90.0, "freqs_hz": [10, 30]})
        self.assertEqual(rows["M1.th"]["line"], {"level": -40.0, "freqs_hz": [5]})
        self.assertIsNone(rows["M1.sf"].get("line"))
        # マーカーが範囲の外・値なし: 機能は結果を出していないので線は無い
        rows = marker_rows.rows_from_snapshot({"markers": [{**m, "status": "out_of_range", "level": None}]})
        self.assertEqual([r["id"] for r in rows if r.get("line")], [])

    def test_toggle_reads_view(self):
        self.assertTrue(marker_rows.function_lines_on({}))
        self.assertTrue(marker_rows.function_lines_on({"view": {"function_lines": True}}))
        self.assertFalse(marker_rows.function_lines_on({"view": {"function_lines": False}}))


class WebFunctionLinesServerTest(unittest.TestCase):
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

    def sweep_once(self):
        n0 = self.get_state()["run"]["sweep_count"]
        self.post("run", {"mode": "single"})
        end = time.monotonic() + 20
        while True:
            st = self.get_state()
            if st["run"]["state"] == "stopped" and st["run"]["sweep_count"] > n0:
                return st
            self.assertLess(time.monotonic(), end)
            time.sleep(0.05)

    def test_rows_have_lines_and_toggle(self):
        self.post("connect", {"backend": "simulator", "serial": "SIM00001"})
        self.post("set_sweep", {"start_hz": 1400 * MHZ, "stop_hz": 1600 * MHZ})
        self.sweep_once()
        self.post("marker_add", {"kind": "M", "trace": 1, "freq_hz": 1500 * MHZ})
        self.post("marker_function", {"id": "M1", "bandwidth_db": [3], "threshold": {"level": -50, "side": "above"}})
        st = self.sweep_once()
        m = st["markers"]["markers"][0]
        rows = {r["id"]: r for r in st["markers"]["rows"]}
        b = m["bandwidth"][0]
        self.assertEqual(rows["M1.bw3"]["line"], {"level": m["level"] - 3, "freqs_hz": [b["left_hz"], b["right_hz"]]})
        self.assertEqual(rows["M1.th"]["line"]["level"], -50)
        self.assertEqual(len(rows["M1.th"]["line"]["freqs_hz"]), 2)
        self.assertIsNone(rows["M1"]["line"])  # マーカーの行は線を持たない
        self.assertIs(st["view"]["function_lines"], True)
        self.post("set_view", {"function_lines": False})
        self.assertIs(self.get_state()["view"]["function_lines"], False)


class WebMarkupTest(unittest.TestCase):
    def test_db_fields_and_toggle_in_markup(self):
        html = (UI_WEB / "index.html").read_text(encoding="utf-8")
        start = html.index("W('functions')")
        sec = html[start:html.index("W('xy_cursor')")]
        self.assertEqual(sec.count("<db-field"), 3)
        self.assertIn(":choices=\"dbLists.bw\"", sec)
        # しきい値の欄は、選んでいるマーカーのトレースの単位の決まり（dB は REF と同じ一覧・符号付き、|Γ|・SWR はその値:
        # texts.THRESHOLD_SPECS。設計R §3）
        self.assertIn(':unit="thSpec.unit" :signed="!!thSpec.signed" :choices="thSpec.choices"', sec)
        self.assertIn("setView({function_lines: $event.target.checked}", sec)
        self.assertIn("viewOpts.function_lines !== false", sec)
        start = html.index("W('peak_list')")
        peaks = html[start:html.index("W('register_p')", start)]
        self.assertIn("<db-field", peaks)
        self.assertIn(":choices=\"dbLists.floor\"", peaks)
        # 欄の名前は基準で変わる（ノイズフロアから／最大から: P §9.7。サーバーの peak_view.list_view）
        self.assertIn("{{ pkView.label }}", peaks)
        app = (UI_WEB / "app.js").read_text(encoding="utf-8")
        self.assertIn('app.component("db-field", GSFields.DbField)', app)
        self.assertIn("setFunctionLines(", app)
        self.assertNotIn("setFunction()", app)

    def test_words_reach_the_web(self):
        labels = web.texts_json()["LABELS"]
        for key in ("db_range_note", "function_lines", "function_lines_tip", "second_n", "threshold_cross",
                    "from_floor", "from_max", "floor_tip", "max_tip_peak", "max_tip_dip", "bad_value"):
            self.assertEqual(labels[key], qt_texts.LABELS[key], key)


@unittest.skipUnless(shutil.which("node"), "Node が無い")
class WebDbFieldRulesTest(unittest.TestCase):
    """fields.js の dB の欄を、Vue を使わずに中の処理（NumField と db-field）だけ動かして確かめる。"""

    SCRIPT = r"""
globalThis.window = globalThis;
globalThis.document = { activeElement: null };
const fs = require("fs");
require(process.argv[1]);
GSTexts.load(JSON.parse(fs.readFileSync(process.argv[2], "utf-8")));
globalThis.GSUnits = require(process.argv[4]);
require(process.argv[3]);
const F = GSFields;
// db-field の中身を Vue の代わりの小さな入れ物で動かす（emit を記録する）
function db(props) {
  const c = F.DbField;
  const self = { value: 3, unit: "dB", choices: [], signed: false, lo: null, hi: null, decimals: 1, emitted: [], note: null,
                 ...props };
  self.$emit = (n, v) => self.emitted.push([n, v]);
  for (const [k, f] of Object.entries(c.methods)) self[k] = f.bind(self);
  for (const [k, f] of Object.entries(c.computed)) Object.defineProperty(self, k, { get: () => f.call(self) });
  return self;
}
// NumField（Enter・一覧・ホイール・↑↓）の出すものを db-field へつなぐ
function num(d) {
  const c = F.NumField;
  const input = { value: d.text, selectionStart: 0 };
  const self = { text: d.text, choices: d.items, wheelExp: 0, disabled: false, editing: false, draft: "", note: null,
                 $refs: { input } };
  self.$emit = (n, a, b) => { if (n === "commit") d.onCommit(a); else if (n === "step") d.onStep(a, b); };
  for (const [k, f] of Object.entries(c.methods)) self[k] = f.bind(self);
  self.type = (t) => { self.onInput({ target: { value: t } }); self.commit(); };
  return self;
}
const pd = () => {};
const out = {};
out.lists = { bw: F.BANDWIDTH_CHOICES, floor: F.FLOOR_CHOICES, ref: F.REF_CHOICES, ranges: F.DB_RANGES };
const bw = (v) => db({ value: v, choices: F.BANDWIDTH_CHOICES, lo: F.DB_RANGES.bw[0], hi: F.DB_RANGES.bw[1] });
const th = (v) => db({ value: v, unit: "dBm", signed: true, choices: F.REF_CHOICES, lo: F.DB_RANGES.th[0], hi: F.DB_RANGES.th[1] });
out.items = { bw: bw(3).items.map((i) => i.text), th: th(-20).items.map((i) => i.text) };
out.text = [bw(3).text, th(-20).text, th(0).text];
let d = bw(3); let n = num(d);
n.type("10.04"); n.type("500"); out.typed = d.emitted; out.rangeNote = d.note && d.note.text;
d = bw(3); n = num(d); n.type("abc"); out.bad = [d.emitted, d.note && d.note.text];
d = th(-20); n = num(d); n.type("−47.5 dBm"); n.type("-400"); out.thTyped = [d.emitted, d.note && d.note.text];
d = bw(3); n = num(d); n.pick({ target: { value: "6 dB" } }); out.picked = d.emitted;
d = th(-20); n = num(d); n.pick({ target: { value: "+20 dBm" } }); out.pickedTh = d.emitted;
d = bw(10); n = num(d); n.onWheel({ deltaY: -100, preventDefault: pd }); out.wheelUp = d.emitted;
d = bw(0.1); n = num(d); n.onWheel({ deltaY: 100, preventDefault: pd }); out.wheelAtEnd = d.emitted;
d = bw(10); n = num(d); n.onKey({ key: "ArrowUp", preventDefault: pd, target: { value: "10.0 dB", selectionStart: 1 } });
out.upTens = d.emitted;
out.round = [2.25, 0.05, -0.05, 10.04, -47.55, 199.96].map((v) => F.roundTo(v, 1));
out.notes = [F.rangeNote(0.1, 200, "dB"), F.rangeNote(-300, 300, "dBμV"), F.rangeNote(0, 100, "")];
console.log(JSON.stringify(out));
"""

    def test_same_rules_as_qt(self):
        with tempfile.TemporaryDirectory() as d:
            tj = Path(d) / "texts.json"
            tj.write_text(json.dumps(web.texts_json(), ensure_ascii=False), encoding="utf-8")
            out = subprocess.run(["node", "-e", self.SCRIPT, str(UI_WEB / "texts.js"), str(tj), str(UI_WEB / "fields.js"),
                                  str(UI_WEB / "units.js")],
                                 capture_output=True, text=True, timeout=30, check=True, encoding="utf-8").stdout
        got = json.loads(out)
        L = qt_texts.LABELS
        self.assertEqual(got["lists"]["bw"], list(BANDWIDTH_CHOICES))
        self.assertEqual(got["lists"]["floor"], list(FLOOR_CHOICES))
        self.assertEqual(got["lists"]["ref"], list(REF_CHOICES))
        self.assertEqual(got["lists"]["ranges"], {"bw": list(BANDWIDTH_RANGE), "th": list(THRESHOLD_RANGE),
                                                  "floor": list(FLOOR_RANGE)})
        self.assertEqual(got["items"]["bw"], [db_choice_text(v, "dB", False) for v in BANDWIDTH_CHOICES])
        self.assertEqual(got["items"]["th"], [db_choice_text(v, "dBm", True) for v in REF_CHOICES])
        self.assertEqual(got["text"], ["3.0 dB", "−20.0 dBm", "0.0 dBm"])
        self.assertEqual(got["typed"], [["commit", 10.0]])  # 範囲の外（500）は送らない
        self.assertEqual(got["rangeNote"], range_note(*BANDWIDTH_RANGE, "dB"))
        self.assertEqual(got["bad"], [[], L["bad_value"]])
        self.assertEqual(got["thTyped"], [[["commit", -47.5]], range_note(*THRESHOLD_RANGE, "dBm")])
        self.assertEqual(got["picked"], [["commit", 6]])
        self.assertEqual(got["pickedTh"], [["commit", 20]])
        self.assertEqual(got["wheelUp"], [["commit", 11]])
        self.assertEqual(got["wheelAtEnd"], [["commit", 0.1]])  # 下の端で止める
        self.assertEqual(got["upTens"], [["commit", 20]])
        self.assertEqual(got["round"], [round_to(v, 1) for v in (2.25, 0.05, -0.05, 10.04, -47.55, 199.96)])
        self.assertEqual(got["notes"], [range_note(0.1, 200, "dB"), range_note(-300, 300, "dBμV"),
                                        range_note(0, 100, "")])


@unittest.skipUnless(shutil.which("node"), "Node が無い")
class GraphJsFunctionLinesTest(unittest.TestCase):
    """graph.js が機能の線を描くこと（uPlot は描画の記録だけの代わりを置く）。"""

    SCRIPT = r"""
const strokes = [];
let path = [];
const ctx = {
  globalAlpha: 1, strokeStyle: "", fillStyle: "", lineWidth: 1, dash: [],
  save() {}, restore() {}, rect() {}, clip() {}, fill() {}, fillRect() {}, strokeRect() {}, fillText() {}, closePath() {},
  translate() {}, rotate() {}, measureText: (s) => ({ width: 7 * String(s).length }),
  beginPath() { path = []; }, moveTo(x, y) { path.push([x, y]); }, lineTo(x, y) { path.push([x, y]); },
  setLineDash(d) { this.dash = d; },
  stroke() { strokes.push({ color: this.strokeStyle, alpha: this.globalAlpha, dash: this.dash.slice(), path: path.slice() }); },
};
globalThis.window = globalThis;
globalThis.GSTexts = { w: (k) => k, get: (d, k) => k, fmtText: (t) => t };
globalThis.GSUnits = { levelText: (v) => String(v) };
globalThis.uPlot = class {
  constructor(opts) { this.o = opts; this.ctx = ctx; this.bbox = { left: 0, top: 0, width: 1000, height: 400 }; this.axes = [{}, {}]; }
  valToPos(v, s) {
    if (s === "x") { const [a, b] = this.o.scales.x.range(); return (v - a) / (b - a) * 1000; }
    const [lo, hi] = this.o.scales.y.range(); return 400 - (v - lo) / (hi - lo) * 400;
  }
  setScale() {} redraw() {} setSize() {}
};
require(process.argv[1]);
const g = new GSGraph({ clientWidth: 1000, clientHeight: 400 }, { colors: { marker: "#m", accent: "#a", zero_db: "#z" }, static: true });
g.setX(1400e6, 1600e6, false);
g.setY(0, 10, "dBm");
g.setMarkers([], "M1");
g.setFunctionLines(JSON.parse(process.argv[2]));
const out = {};
g._draw(g.u); out.drawn = strokes.splice(0);
g.setX(0, 100, true); g._draw(g.u); out.zeroSpan = strokes.splice(0);
g.setX(1400e6, 1600e6, false); g.setFunctionLines([]); g._draw(g.u); out.hidden = strokes.splice(0);
console.log(JSON.stringify(out));
"""

    def test_lines_drawn(self):
        rows = [{"id": "M1", "parent": None, "line": None},
                {"id": "M1.bw3", "parent": "M1", "line": {"level": -30.0, "freqs_hz": [1450 * MHZ, 1550 * MHZ]}},
                {"id": "M1.sf", "parent": "M1", "line": None},
                {"id": "M2.th", "parent": "M2", "line": {"level": -50.0, "freqs_hz": [1500 * MHZ]}}]
        out = subprocess.run(["node", "-e", self.SCRIPT, str(UI_WEB / "graph.js"), json.dumps(rows)],
                             capture_output=True, text=True, encoding="utf-8", timeout=30, check=True).stdout
        got = json.loads(out)
        seg = [(s["color"], round(s["alpha"], 4), s["dash"], [[round(x, 3), round(y, 3)] for x, y in s["path"]])
               for s in got["drawn"]]
        a = round(89 / 255, 4)
        dash = [2, 3]
        self.assertEqual(seg, [
            # 選んでいるマーカー（M1）の線は強調の色: 横線 −30dBm（REF 0・10dB/div で上から 3 目盛）と左右の縦線
            ("#a", a, dash, [[0, 120], [1000, 120]]),
            ("#a", a, dash, [[250, 0], [250, 400]]),
            ("#a", a, dash, [[750, 0], [750, 400]]),
            # ほかのマーカー（M2）はマーカーの色。交点が1つなら縦線は1本
            ("#m", a, dash, [[0, 200], [1000, 200]]),
            ("#m", a, dash, [[500, 0], [500, 400]]),
        ])
        self.assertEqual(got["zeroSpan"], [])  # ゼロスパン（横軸が点の番号）では描かない
        self.assertEqual(got["hidden"], [])    # 切り替えで出さないとき（空を渡す）


if __name__ == "__main__":
    unittest.main()
