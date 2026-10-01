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

from pygigast.api.web import server as web
from pygigast.ui_qt import texts as qt_texts

UI_WEB = Path(__file__).resolve().parent.parent / "pygigast" / "ui_web"


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
        from pygigast.ui_qt.fields import COUNT_CHOICES, COUNT_MAX

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
