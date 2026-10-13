"""Web の画面: 設定を黙って変えない（設計J §4.10 の 2〜4）と、SG の周波数を変えたときの保持の選択（設計F §8）。

- 状態の ui.level（単位の欄の中身。Qt と同じ screen_model.unit_view）が、電力の間は設定の単位、基準との比の間は
  「dB（基準との比）」と選べない電力の単位を出す
- 知らせの view（Qt と同じ texts.notice_view）が、単位の変更は欄の下（field）、比の表示は窓（modal）を言う
- 命令 set_trace_options が Web の口から使え、状態の trace_options に出る
- app_settings.js の決まり（Node があるときだけ）: 欄の下の知らせ・窓と「次から出さない」・ブラウザに覚える・
  設定の画面で戻す・比の値は送らない
- 画面のファイルに単位の欄・信号源のチェック・窓のチェック・設定の画面のチェックがある

本物の uvicorn とシミュレータの機器で通す（tests/test_web_settings.py と同じやり方）。
"""
import json
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from namibench.api.web import server as web
from namibench.api.web import view
from namibench.ui_qt import texts as qt_texts
from tests import test_web_settings as tws_settings

UI_WEB = tws_settings.UI_WEB
V = qt_texts.VIEW_TEXTS
MHZ = 1_000_000


class LevelStateTest(tws_settings._Base):
    def wait_state(self, cond, what, timeout=20):
        end = time.monotonic() + timeout
        while True:
            st = self.state()
            if cond(st):
                return st
            self.assertLess(time.monotonic(), end, what)
            time.sleep(0.05)

    def test_unit_view_follows_display(self):
        st = self.connect_and_sweep(100 * MHZ, 200 * MHZ)
        lv = st["ui"]["level"]
        self.assertEqual((lv["ratio"], lv["current"]), (False, "dBm"))
        self.assertEqual([c["code"] for c in lv["choices"]], ["dBm", "dBuV", "dBmV", "nf", "ratio"])
        self.assertEqual(self.post("set_mode", {"mode": "tg"})[0], 200)
        code, r = self.post("reference_take", {"kind": "wide", "name": "直結 A"})
        self.assertEqual(code, 200, r)
        self.wait_state(lambda s: not s["tasks"] and (s["normalization"] or {}).get("selected"), "基準を取り終える")
        n0 = self.state()["run"]["sweep_count"]
        self.assertEqual(self.post("run", {"mode": "single"})[0], 200)
        st = self.wait_state(lambda s: s["run"]["state"] == "stopped" and s["run"]["sweep_count"] > n0, "掃引")
        lv = st["ui"]["level"]
        self.assertEqual((lv["ratio"], lv["current"], lv["unit"]), (True, "ratio", "dBm"))
        reason = V["level_unit_ratio_reason"].replace("{unit}", "dBm")
        self.assertEqual([(c["enabled"], c["tip"]) for c in lv["choices"][:3]], [(False, reason)] * 3)

    def test_trace_options_command(self):
        self.assertIs(self.state()["trace_options"]["sg_clears_holds"], True)
        code, r = self.post("set_trace_options", {"sg_clears_holds": False})
        self.assertEqual((code, r), (200, {"trace_options": {"sg_clears_holds": False}}))
        self.assertIs(self.state()["trace_options"]["sg_clears_holds"], False)
        code, r = self.post("set_trace_options", {"sg_clears_holds": 1})
        self.assertEqual((code, r["error"]["code"]), (400, "bad_value"))

    def test_notice_view_says_where(self):
        v = view.notice_view({"code": "level_unit_changed", "detail": {"unit": "dBuV"}}, None)["view"]
        self.assertEqual((v["field"], v["text"]), ("unit", V["level_unit_changed"].replace("{unit}", "dBμV")))
        v = view.notice_view({"code": "ratio_display", "detail": {"name": "A", "unit": "dBm"}}, None)["view"]
        self.assertEqual(v["modal"]["key"], "ratio_display")


class StaticHooksTest(unittest.TestCase):
    def test_files_have_the_new_parts(self):
        html = (UI_WEB / "index.html").read_text(encoding="utf-8")
        for hook in ("unitView.choices", "unitView.current", "setUnit(", "notes.unit", ":disabled=\"!o.enabled\"",
                     "setTraceOptions({sg_clears_holds", "traceOpts.sg_clears_holds", "W('sg_clear_holds')"):
            self.assertIn(hook, html, hook)
        # 出し続けていた「比は単位を換えません」の文は、欄が比を出すので消した（C §5.14 の 4 と同じ）
        self.assertNotIn("level_unit_ratio')", html)
        self.assertNotIn("level_unit_ratio", qt_texts.VIEW_TEXTS)
        windows = (UI_WEB / "app_windows.js").read_text(encoding="utf-8")
        for hook in ("v-model=\"b.checked\"", "b.info", "setNotify('ratio_display'", "a.V('notify_ratio')"):
            self.assertIn(hook, windows, hook)
        self.assertIn("this.onViewNotice(v)", (UI_WEB / "app.js").read_text(encoding="utf-8"))


@unittest.skipUnless(shutil.which("node"), "Node が無い")
class NoticeRulesTest(unittest.TestCase):
    """app_settings.js の知らせの決まり（Qt の tab_display と同じ: 欄の下・窓・次から出さない・設定の画面で戻す）。"""

    SCRIPT = r"""
globalThis.window = globalThis; const store = {};
globalThis.localStorage = { getItem: (k) => (k in store ? store[k] : null), setItem: (k, v) => { store[k] = String(v); },
                            removeItem: (k) => { delete store[k]; } };
const fs = require("fs");
require(process.argv[1]);
GSTexts.load(JSON.parse(fs.readFileSync(process.argv[2], "utf-8")));
require(process.argv[3]);
const A = GSAppSettings;
const P = JSON.parse(process.argv[4]);
function vm() {
  const o = { ...A.data.call({}), notes: {}, ui: {}, st: null, sent: [], status: "" };
  for (const [k, f] of Object.entries(A.methods)) o[k] = f;
  o.note = function (field, text) { if (field) this.notes = { ...this.notes, [field]: { text } }; else this.status = text; };
  o.clearNote = function (f) { const n = { ...this.notes }; delete n[f]; this.notes = n; };
  o.cmd = function (name, args, opts) { this.sent.push([name, args, opts && opts.field]); return Promise.resolve({}); };
  Object.defineProperty(o, "notifyRatio", { get() { return A.computed.notifyRatio.call(o); } });
  return o;
}
(async () => {
  const out = {};
  const o = vm();
  o.onViewNotice(P.unit);
  out.unitNote = o.notes.unit;
  out.noBoxForUnit = o.confirmBox;
  o.onViewNotice(P.ratio);
  const b = o.confirmBox;
  out.box = { title: b.title, text: b.text, check: b.check, info: b.info, checked: b.checked };
  o.onViewNotice(P.ratio2);  // 開いている間にまた来たら文だけ書き直す
  out.sameBox = o.confirmBox === b;
  out.text2 = o.confirmBox.text;
  o.confirmBox.checked = true;
  o.answerConfirm(false);
  await new Promise((r) => setTimeout(r, 0));
  out.stored = JSON.parse(store["pygigast.notify"]);
  out.notifyAfter = o.notifyRatio;
  o.onViewNotice(P.ratio);
  out.boxAfter = o.confirmBox;
  const o2 = vm();
  out.freshNotify = o2.notifyRatio;
  o2.setNotify("ratio_display", true);
  out.storeAfterOn = store["pygigast.notify"] === undefined ? null : store["pygigast.notify"];
  out.freshOn = vm().notifyRatio;
  o2.setUnit("ratio");
  o2.setUnit("dBmV");
  o2.setTraceOptions({ sg_clears_holds: false });
  out.sent = o2.sent;
  // ほかの確かめる窓は今まで通り OK・やめるの真偽で返し、知らせの窓を重ねない
  const o3 = vm();
  const pr = o3.askConfirm("t", "x");
  o3.onViewNotice(P.ratio);
  out.keptConfirm = o3.confirmBox.text;
  o3.answerConfirm(true);
  out.confirm = await pr;
  // 別ウィンドウにした部分は窓を出さない（元の画面だけ）。欄の下の知らせは出す
  const o4 = vm();
  o4.part = "tab-view";
  o4.notifyPrefs = {};
  o4.onViewNotice(P.ratio);
  o4.onViewNotice(P.unit);
  out.partBox = o4.confirmBox;
  out.partNote = !!o4.notes.unit;
  console.log(JSON.stringify(out));
  process.exit(0);
})();
"""

    def test_same_rules_as_qt(self):
        from namibench.ui_qt import texts

        p = {
            "unit": texts.notice_view({"code": "level_unit_changed", "detail": {"unit": "dBuV"}}),
            "ratio": texts.notice_view({"code": "ratio_display", "detail": {"name": "直結 A", "unit": "dBm"}}),
            "ratio2": texts.notice_view({"code": "ratio_display", "detail": {"name": "直結 B", "unit": "dBm"}}),
        }
        with tempfile.TemporaryDirectory() as d:
            tj = Path(d) / "texts.json"
            tj.write_text(json.dumps(web.texts_json(), ensure_ascii=False), encoding="utf-8")
            out = subprocess.run(["node", "-e", self.SCRIPT, str(UI_WEB / "texts.js"), str(tj),
                                  str(UI_WEB / "app_settings.js"), json.dumps(p, ensure_ascii=False)],
                                 capture_output=True, text=True, timeout=30, check=True, encoding="utf-8").stdout
        got = json.loads(out)
        self.assertEqual(got["unitNote"], {"text": V["level_unit_changed"].replace("{unit}", "dBμV")})
        self.assertIsNone(got["noBoxForUnit"])
        m = p["ratio"]["modal"]
        self.assertEqual(got["box"], {"title": m["title"], "text": m["text"], "check": V["dont_show_again"],
                                      "info": True, "checked": False})
        self.assertTrue(got["sameBox"])
        self.assertEqual(got["text2"], p["ratio2"]["modal"]["text"])
        self.assertEqual(got["stored"], {"ratio_display": False})
        self.assertFalse(got["notifyAfter"])
        self.assertIsNone(got["boxAfter"])
        self.assertFalse(got["freshNotify"])  # ブラウザに覚える
        self.assertIsNone(got["storeAfterOn"])  # 設定の画面で戻すと覚え書きを消す（初期値: 知らせる）
        self.assertTrue(got["freshOn"])
        self.assertEqual(got["sent"], [["set_view", {"level_unit": "dBmV"}, "unit"],
                                       ["set_trace_options", {"sg_clears_holds": False}, "sg"]])
        self.assertEqual(got["keptConfirm"], "x")
        self.assertIs(got["confirm"], True)
        self.assertIsNone(got["partBox"])
        self.assertTrue(got["partNote"])


if __name__ == "__main__":
    unittest.main()
