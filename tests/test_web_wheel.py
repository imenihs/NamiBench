"""Web の欄も、スクロールする場所の中ではフォーカスがあるときだけホイールで変わる（設計J §4.10 の 1）。"""
import json
import shutil
import subprocess
import unittest
from pathlib import Path

UI_WEB = Path(__file__).resolve().parents[1] / "pygigast" / "ui_web"


@unittest.skipUnless(shutil.which("node"), "Node が無い")
class WheelRuleTest(unittest.TestCase):
    SCRIPT = r"""
globalThis.window = globalThis;
const doc = { activeElement: null };
globalThis.document = doc;
const styles = new Map();
globalThis.getComputedStyle = (el) => ({ overflowY: styles.get(el) || "visible" });
doc.defaultView = globalThis;
require(process.argv[1]);
const F = GSFields;
function node(parent) {
  const n = { parentElement: parent, ownerDocument: doc, kids: [] };
  n.contains = (x) => { for (let p = x; p; p = p.parentElement) if (p === n) return true; return false; };
  return n;
}
const body = node(null);
const panel = node(body); styles.set(panel, "auto");   // 右のパネルのタブの中身（スクロールする）
const field = node(panel); const input = node(field);
const top = node(body); const topInput = node(top);     // 上段（スクロールしない）
const out = {};
out.panelUnfocused = F.wheelIgnored(field);
doc.activeElement = input;
out.panelFocused = F.wheelIgnored(field);
doc.activeElement = null;
out.topUnfocused = F.wheelIgnored(top);
console.log(JSON.stringify(out));
"""

    def test_rule(self):
        out = subprocess.run(["node", "-e", self.SCRIPT, str(UI_WEB / "fields.js")],
                             capture_output=True, text=True, encoding="utf-8", timeout=30)
        self.assertEqual(out.returncode, 0, out.stderr)
        r = json.loads(out.stdout.strip().splitlines()[-1])
        self.assertTrue(r["panelUnfocused"])   # パネルの中・フォーカス無し → 値を変えずスクロールに任せる
        self.assertFalse(r["panelFocused"])    # クリックしてフォーカスがあれば変える
        self.assertFalse(r["topUnfocused"])    # 上段は今まで通り

    def test_fields_use_rule(self):
        js = (UI_WEB / "fields.js").read_text(encoding="utf-8")
        self.assertEqual(js.count("wheelIgnored(this.$el)"), js.count("onWheel(e) {"))


if __name__ == "__main__":
    unittest.main()
