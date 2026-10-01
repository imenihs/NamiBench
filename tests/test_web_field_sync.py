"""Web の入力が、利用者が触っている間に状態で書き直されないこと（実装リスト 3-9。fields.js の Steady）。"""
import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

UI_WEB = Path(__file__).resolve().parent.parent / "pygigast" / "ui_web"


@unittest.skipUnless(shutil.which("node"), "Node が無い")
class SteadyDirectiveTest(unittest.TestCase):
    """DOM の代わりに、値・選んだ範囲・フォーカス・イベントだけを持つ欄で v-steady の決まりを確かめる。"""

    SCRIPT = r"""
globalThis.window = globalThis;
const doc = { activeElement: null };
globalThis.document = doc;
require(process.argv[1]);
const S = GSFields.Steady;
function makeEl() {
  const h = {};
  const el = {
    value: "", selectionStart: 0, selectionEnd: 0, ownerDocument: doc,
    addEventListener(t, f) { (h[t] = h[t] || []).push(f); },
    fire(t, e) { (h[t] || []).forEach((f) => f(e || {})); },
    setSelectionRange(a, b) { this.selectionStart = a; this.selectionEnd = b; },
    focus() { doc.activeElement = this; this.fire("focus"); },
    blur() { doc.activeElement = null; this.fire("blur"); },
  };
  return el;
}
const out = {};
// 1) 範囲を選んでいる間に状態が変わっても書き直さない。フォーカスが外れたら最新の値
let el = makeEl();
S.mounted(el, { value: "16" });
out.mounted = el.value;
el.focus(); el.fire("mousedown"); el.setSelectionRange(0, 1);
S.updated(el, { value: "32" });
S.updated(el, { value: "48" });
out.keptValue = el.value; out.keptSel = [el.selectionStart, el.selectionEnd];
el.blur();
out.afterBlur = el.value;
// 2) 打っている途中の文字を消さない。打って離れたら（確定）、離れたときに古い値へ戻さない
el = makeEl();
S.mounted(el, { value: "100" });
el.focus(); el.value = "25"; el.fire("input");
S.updated(el, { value: "100" });
out.typing = el.value;
el.fire("change"); el.blur();
out.afterCommitBlur = el.value;
S.updated(el, { value: "25" });
out.afterEcho = el.value;
// 3) Enter で確定した値の戻りは、フォーカスがあっても映す。次に触ると解く
el = makeEl();
S.mounted(el, { value: "1" });
el.focus(); el.value = "1k"; el.fire("input"); el.fire("keydown", { key: "Enter" }); el.fire("change");
S.updated(el, { value: "1000" });
out.echo = el.value;
el.fire("keydown", { key: "ArrowLeft", shiftKey: true });
S.updated(el, { value: "2000" });
out.afterTouch = el.value;
// 4) 表示が同じなら書かない（選んだ範囲はそのまま）。フォーカスが無ければ映す
el = makeEl();
S.mounted(el, { value: "7" });
el.setSelectionRange(0, 1);
S.updated(el, { value: "7" });
out.sameSel = [el.selectionStart, el.selectionEnd];
S.updated(el, { value: "8" });
out.unfocused = el.value;
console.log(JSON.stringify(out));
"""

    def test_rules(self):
        out = subprocess.run(["node", "-e", self.SCRIPT, str(UI_WEB / "fields.js")],
                             capture_output=True, text=True, timeout=30, check=True, encoding="utf-8").stdout
        got = json.loads(out)
        self.assertEqual(got["mounted"], "16")
        self.assertEqual(got["keptValue"], "16")
        self.assertEqual(got["keptSel"], [0, 1])
        self.assertEqual(got["afterBlur"], "48")
        self.assertEqual(got["typing"], "25")
        self.assertEqual(got["afterCommitBlur"], "25")
        self.assertEqual(got["afterEcho"], "25")
        self.assertEqual(got["echo"], "1000")
        self.assertEqual(got["afterTouch"], "1000")
        self.assertEqual(got["sameSel"], [0, 1])
        self.assertEqual(got["unfocused"], "8")


class SteadyUsedTest(unittest.TestCase):
    def test_state_inputs_use_steady(self):
        """文字を打てる入力は :value ではなく v-steady で状態を映す（チェック・ラジオ・つまみ・色・読むだけは除く）。"""
        offenders = []
        for name in ("index.html", "app_windows.js", "fields.js"):
            text = (UI_WEB / name).read_text(encoding="utf-8")
            for tag in re.findall(r"<input\b[^>]*>", text, flags=re.S):
                if ":value=" not in tag:
                    continue
                if re.search(r'type="(checkbox|radio|range|color)"|readonly', tag):
                    continue
                offenders.append((name, tag[:80]))
        self.assertEqual(offenders, [])
        self.assertIn('app.directive("steady", GSFields.Steady)', (UI_WEB / "app.js").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
