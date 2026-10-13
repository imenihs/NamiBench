"""Web のトレースの表（右のタブ「トレース・掃引」）と回数の欄（fields.js の CountField）。

- 回数の欄の決まり（一覧・打てる範囲・「回数なし」・受け付けない文字・↑↓）が Qt の ui_qt/fields.py の CountField と同じ
- 受け付けない文字の知らせ（LABELS の bad_count）・「回数なし」・「 回」が Web へ配る表に入っている
- 画面のファイルが回数の欄を使い、平均では「回数なし」を出さない
"""
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from namibench.api.web import server as web
from namibench.ui_qt import texts as qt_texts

UI_WEB = Path(__file__).resolve().parent.parent / "namibench" / "ui_web"


class CountTextsTest(unittest.TestCase):
    def test_words_reach_the_web(self):
        labels = web.texts_json()["LABELS"]
        for key in ("bad_count", "no_count", "times_suffix", "count_n", "clear_tip", "no_value_paren"):
            self.assertEqual(labels[key], qt_texts.LABELS[key], key)
        reasons = web.texts_json()["REASON_TEXTS"]
        for key in ("count_only", "average_type_only", "after_only"):
            self.assertIn(key, reasons)


class TraceMarkupTest(unittest.TestCase):
    def test_count_field_is_used(self):
        html = (UI_WEB / "index.html").read_text(encoding="utf-8")
        start = html.index('data-sec="traces"')
        sec = html[start:html.index('data-sec="files"')]
        self.assertIn("<count-field", sec)
        # 平均は回数が要るので「回数なし」を出さない（設計F §2）。受け付けない文字は bad_count を知らせる
        self.assertIn(":allow-none=\"t.action !== 'average'\"", sec)
        self.assertIn("W('bad_count')", sec)
        # 古い表と、自分で数に直していた口は消した
        self.assertNotIn("<table", sec)
        self.assertNotIn("setTraceCount", html)
        app = (UI_WEB / "app.js").read_text(encoding="utf-8")
        self.assertIn('app.component("count-field", GSFields.CountField)', app)
        self.assertNotIn("setTraceCount", app)


class TraceLineMarkupTest(unittest.TestCase):
    """トレースの線（設計F §9）・グラフの中の凡例・参照トレースの［開く…］（Qt と同じ形: J §5）。"""

    def test_line_dropdown_and_open_button(self):
        html = (UI_WEB / "index.html").read_text(encoding="utf-8")
        start = html.index('data-sec="traces"')
        sec = html[start:html.index('data-sec="files"')]
        # 線のドロップダウン: 語は texts の TRACE_LINES、変えたら set_trace の line
        self.assertIn('class="tline"', sec)
        self.assertIn("Td('TRACE_LINES')", sec)
        self.assertIn("setTrace(t.number, 'line', $event.target.value)", sec)
        # 参照トレースのまとまりに［開く…］（メニュー「ファイル → 開く…」と同じ項目を呼ぶ）
        files = html[html.index('data-sec="files"'):html.index('data-sec="rtlsdr"')]
        self.assertIn("trigger('open')", files)
        self.assertIn("T('MENU_ITEMS', 'open')", files)
        # 凡例はグラフの上の帯ではなく、グラフの中の箱（graph.js の setLegend）
        self.assertNotIn('class="legend"', html)
        app = (UI_WEB / "app.js").read_text(encoding="utf-8")
        self.assertIn("setLegend(this.legendItems())", app)

    def test_words_reach_the_web(self):
        tj = web.texts_json()
        self.assertEqual(tj["TRACE_LINES"], qt_texts.TRACE_LINES)
        self.assertEqual(tj["LABELS"]["line"], qt_texts.LABELS["line"])
        self.assertEqual(tj["DETACH_PARTS"], list(qt_texts.DETACH_PARTS))

    def test_state_view_file_legend_and_png_lines(self):
        """参照トレースの凡例の文字は Qt と同じ関数、PNG の凡例の見本はトレースの線（参照トレースは参照トレースの線）。"""
        from namibench.api.web import view
        from namibench.core import report_layout as rl

        f = {"file_id": "F1", "name": "a.h5", "created_at": "2026-10-03T10:20:30", "device_serial": "SIM00001",
             "traces": [{"id": "F1.1", "number": 1}, {"id": "F1.2", "number": 2}]}
        st = {"traces": [{"number": 1, "action": "update", "visible": True, "has_values": True, "line": "thin",
                          "current_count": 1},
                         {"number": 2, "action": "average", "visible": True, "has_values": True, "line": "thick",
                          "current_count": 3, "count_target": 4, "average_type_effective": "power"}],
              "files": [f], "sweep": {"start_hz": 1, "stop_hz": 2}}
        ui = view.state_view(st)["ui"]
        self.assertEqual(ui["file_legend"], {"F1.1": qt_texts.file_trace_label(f, "F1.1"),
                                             "F1.2": qt_texts.file_trace_label(f, "F1.2")})
        self.assertEqual(ui["file_legend"]["F1.1"], "a.h5 #1 2026-10-03 10:20 SIM00001")
        lay = view.png_layout(st, {"files": [{"label": ui["file_legend"]["F1.1"], "color": "trace2"}]})
        self.assertEqual([(it["text"].split(" ")[0], it["line"]) for it in lay["legend"]],
                         [("1", "thin"), ("2", "thick"), ("a.h5", rl.REFERENCE_LINE)])


@unittest.skipUnless(shutil.which("node"), "Node が無い")
class LineStrokeJsTest(unittest.TestCase):
    """graph.js の線の幅と切れ目が core/report_layout.stroke と同じ（Qt と Web が同じ線を描く: 設計F §9）。"""

    def test_same_as_python(self):
        from namibench.core import report_layout as rl

        script = (
            "globalThis.window = globalThis; require(process.argv[1]);"
            "const st = JSON.parse(process.argv[2]); const out = {};"
            "for (const base of [1, 1.25, 1.3333]) for (const [k, s] of Object.entries(st))"
            "  out[k + '@' + base] = GSGraph.lineStroke(s, base);"
            "console.log(JSON.stringify([out, GSGraph.SCREEN_LINE_BASE]));"
        )
        out = subprocess.run(["node", "-e", script, str(UI_WEB / "graph.js"), json.dumps(rl.LINE_STYLES)],
                             capture_output=True, text=True, timeout=30, check=True).stdout
        got, screen = json.loads(out)
        self.assertEqual(screen, 1.25)  # 画面の実線の幅は今までの線（Qt は 1 画素: plot_layers.SCREEN_LINE_BASE）
        for base in (1, 1.25, 1.3333):
            for code in rl.LINE_STYLES:
                want = rl.stroke(code, base)
                g = got[f"{code}@{base}"]
                self.assertAlmostEqual(g["width"], want["width"])
                self.assertEqual(len(g["dash"]), len(want["dash"] or []))
                for a, b in zip(g["dash"], want["dash"] or []):
                    self.assertAlmostEqual(a, b)


@unittest.skipUnless(shutil.which("node"), "Node が無い")
class CountRulesTest(unittest.TestCase):
    """fields.js の回数の欄を、Vue を使わずに中の処理だけ動かして確かめる。"""

    SCRIPT = r"""
globalThis.window = globalThis;
globalThis.document = { activeElement: null };
const fs = require("fs");
require(process.argv[1]);
GSTexts.load(JSON.parse(fs.readFileSync(process.argv[2], "utf-8")));
globalThis.GSUnits = require(process.argv[4]);
require(process.argv[3]);
const F = GSFields;
const P = (t, a) => { const r = F.parseCount(t, a); return r ? [r.value] : null; };
// 欄の処理を、Vue の代わりの小さな入れ物で動かす（emit を記録する）
function field(value, allowNone) {
  const c = F.CountField;
  const self = { value, allowNone, disabled: false, editing: false, draft: "", emitted: [],
                 $refs: { input: { value: "", addEventListener() {} } } };
  self.$emit = (n, v) => self.emitted.push(v === undefined ? [n] : [n, v]);
  for (const [k, f] of Object.entries(c.methods)) self[k] = f.bind(self);
  Object.defineProperty(self, "shown", { get: () => c.computed.shown.call(self) });
  Object.defineProperty(self, "items", { get: () => c.computed.items.call(self) });
  return self;
}
const out = {};
out.choices = F.COUNT_CHOICES; out.max = F.COUNT_MAX;
out.text = [F.countText(null), F.countText(64)];
out.parse = {
  plain: P("64", true), suffix: P("64" + GSTexts.w("times_suffix"), true), wide: P("\uff11\uff12", true), max: P("10000", true),
  over: P("10001", true), zero: P("0", true), minus: P("-1", true), dec: P("2.5", true), word: P("abc", true),
  emptyHold: P("", true), emptyAvg: P(" ", false), noneHold: P(GSTexts.w("no_count"), true), noneAvg: P(GSTexts.w("no_count"), false),
};
out.step = [F.stepCount(null, 0, 1, true), F.stepCount(1, 0, -1, true), F.stepCount(1, 0, -1, false),
            F.stepCount(64, 1, 1, true), F.stepCount(9995, 1, 1, true)];
// 一覧: 平均には「回数なし」が無い
out.itemsHold = field(null, true).items.map((i) => i.text);
out.itemsAvg = field(16, false).items.map((i) => i.text);
// 打って確定 → commit、受け付けない → 今の値に戻して invalid、同じ値 → 送らない
let f = field(64, false); f.onInput({ target: { value: "abc" } }); f.commit();
out.bad = { emitted: f.emitted, shown: f.$refs.input.value };
f = field(64, false); f.onInput({ target: { value: "" } }); f.commit();
out.emptyAvg = f.emitted;
f = field(4, true); f.onInput({ target: { value: "" } }); f.commit();
out.emptyHold = f.emitted;
f = field(64, false); f.onInput({ target: { value: "64" + GSTexts.w("times_suffix") } }); f.commit();
out.same = f.emitted;
f = field(64, false); f.onInput({ target: { value: "100" } }); f.commit(); f.commit();
out.typed = f.emitted;
// 一覧から選ぶ
f = field(8, true); f.pick({ target: { value: "none" } });
out.pickNone = f.emitted;
f = field(8, true); f.pick({ target: { value: "256" } });
out.pick256 = f.emitted;
// ↑↓ はカーソルのある桁、ホイールは 1 ずつ
f = field(64, true); f.onKey({ key: "ArrowUp", preventDefault() {}, target: { value: "64" + GSTexts.w("times_suffix"), selectionStart: 1 } });
out.upTens = f.emitted;
f = field(64, true); f.onWheel({ deltaY: 100, preventDefault() {} });
out.wheelDown = f.emitted;
console.log(JSON.stringify(out));
"""

    def test_same_rules_as_qt(self):
        from namibench.ui_qt.fields import COUNT_CHOICES, COUNT_MAX

        with tempfile.TemporaryDirectory() as d:
            tj = Path(d) / "texts.json"
            tj.write_text(json.dumps(web.texts_json(), ensure_ascii=False), encoding="utf-8")
            out = subprocess.run(["node", "-e", self.SCRIPT, str(UI_WEB / "texts.js"), str(tj), str(UI_WEB / "fields.js"),
                                  str(UI_WEB / "units.js")],
                                 capture_output=True, text=True, timeout=30, check=True, encoding="utf-8").stdout
        got = json.loads(out)
        L = qt_texts.LABELS
        self.assertEqual(got["choices"], list(COUNT_CHOICES))
        self.assertEqual(got["max"], COUNT_MAX)
        self.assertEqual(got["text"], [L["no_count"], "64" + L["times_suffix"]])
        p = got["parse"]
        self.assertEqual([p["plain"], p["suffix"], p["wide"], p["max"]], [[64], [64], [12], [10000]])
        for k in ("over", "zero", "minus", "dec", "word", "emptyAvg", "noneAvg"):
            self.assertIsNone(p[k], k)
        self.assertEqual([p["emptyHold"], p["noneHold"]], [[None], [None]])
        self.assertEqual(got["step"], [1, None, 1, 74, 10000])
        self.assertEqual(got["itemsHold"], [L["no_count"]] + [f"{v}{L['times_suffix']}" for v in COUNT_CHOICES])
        self.assertEqual(got["itemsAvg"], [f"{v}{L['times_suffix']}" for v in COUNT_CHOICES])
        self.assertEqual(got["bad"], {"emitted": [["invalid"]], "shown": "64" + L["times_suffix"]})
        self.assertEqual(got["emptyAvg"], [["invalid"]])
        self.assertEqual(got["emptyHold"], [["commit", None]])
        self.assertEqual(got["same"], [])
        self.assertEqual(got["typed"], [["commit", 100]])  # 2回目の確定（Enter の後に欄を離れた）は送らない
        self.assertEqual(got["pickNone"], [["commit", None]])
        self.assertEqual(got["pick256"], [["commit", 256]])
        self.assertEqual(got["upTens"], [["commit", 74]])
        self.assertEqual(got["wheelDown"], [["commit", 63]])


if __name__ == "__main__":
    unittest.main()


class FileUnitWebTest(unittest.TestCase):
    """単位が縦軸と違う参照トレース（E §6.4）: Web も重ねず、チェックを無効にして理由を出す（Qt の
    tests/test_ui_qt_files.py の test_file_with_other_unit_is_not_overlaid と同じ動き・同じ語）。"""

    @classmethod
    def setUpClass(cls):
        from tests.test_web_layout import App, EdgePage, edge_available

        if not edge_available():
            raise unittest.SkipTest("Microsoft Edge が無い")
        cls.app = App(lan=False)
        try:
            cls.page = EdgePage()
        except Exception:
            cls.app.close()
            raise

    @classmethod
    def tearDownClass(cls):
        cls.page.close()
        cls.app.close()

    def wait_idle(self, what):
        import time

        end = time.monotonic() + 30
        while self.app.call("get_state").get("tasks"):
            self.assertLess(time.monotonic(), end, what)
            time.sleep(0.05)

    def test_other_unit_is_not_overlaid(self):
        import time

        from tests.test_web_layout import open_app

        p = Path(self.app.tmp.name) / "p.h5"
        self.app.call("save", path=str(p))
        end = time.monotonic() + 30
        while not p.exists():
            self.assertLess(time.monotonic(), end, "保存")
            time.sleep(0.05)
        self.wait_idle("保存")
        self.app.call("open", paths=[str(p)])
        self.wait_idle("開く")
        open_app(self.page, self.app, 1280, 720)
        self.page.wait("gsApp.files.length === 1 && gsApp.$traces.has(gsApp.files[0].traces[0].id)", "参照トレース")
        tid = self.page.ev("gsApp.files[0].traces[0].id")
        self.page.wait(f"gsApp.shownFiles(gsApp.palette).some((f) => f.id === '{tid}')", "重ねる")
        sel = "section[data-sec=files] label.inline"
        self.assertFalse(self.page.ev(f"document.querySelector('{sel} input').disabled"))
        # 行の文字は Qt の一覧と同じ（file_trace_check）
        info = self.app.call("get_state")["files"][0]["traces"][0]
        self.assertEqual(self.page.ev(f"document.querySelector('{sel}').textContent.trim()"),
                         qt_texts.w("file_trace_check", number=info["number"], unit=info["unit"], points=info["points"]))
        # 縦軸を比（dB）にすると、電力（dBm）の参照トレースは重ねない（グラフ・凡例・PNG・REF の自動から外れる）。
        # 縦軸の単位は表示している測定中のトレースの単位（Qt と同じ screen_s11.axis_unit）: TG で基準を取って比にする
        self.app.call("set_mode", mode="tg")
        self.app.call("reference_take", kind="fine")
        self.wait_idle("基準を取る")
        self.app.call("run", mode="single")
        self.page.wait("gsApp.axisUnit === 'dB'", "縦軸が dB")
        self.page.settle()
        self.assertEqual(self.page.ev("gsApp.shownFiles(gsApp.palette).length"), 0)
        self.assertFalse(self.page.ev("gsApp.legendItems().some((it) => it.text.startsWith('p.h5'))"))
        self.assertTrue(self.page.ev(f"document.querySelector('{sel} input').disabled"))
        title = self.page.ev(f"document.querySelector('{sel}').title")
        self.assertEqual(title, qt_texts.w("file_unit_differs", axis="dB", unit=info["unit"]))
        self.assertIn("単位が縦軸", title)
        # 電力に戻すと重ねる
        self.app.call("set_mode", mode="sa")
        self.app.call("run", mode="single")
        self.page.wait(f"gsApp.shownFiles(gsApp.palette).some((f) => f.id === '{tid}')", "また重ねる")
