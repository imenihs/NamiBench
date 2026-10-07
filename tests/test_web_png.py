"""Web の PNG（ui_web/png.js）: 目盛と単位の置き場所が Qt の png_export と同じ決まり（core/report_layout.graph_axes）。

ヘッドレスの Edge で開いた Web の画面で PNG を作り、canvas に書いた文字（fillText）を記録して確かめる
（道具は tests/test_web_layout.py の EdgePage・App・open_app）。Qt の側は tests/test_png_export.py の AxesRuleTest。

- 横軸の数字は 10 区画の線の上を x_every 目盛ごと、見出し（単位）はその1行下の真ん中
- 縦軸の数字は 10 区画の線の高さで右端をそろえ、単位は縦軸の数字の上（凡例の段の左端）
- 枠の左右と上は axes の値（Qt と同じ位置）
"""
from __future__ import annotations

import base64
import json
import unittest
from pathlib import Path

from tests.test_web_layout import App, EdgePage, edge_available, open_app

RECORD_JS = r"""
(async () => {
  const texts = [];
  const proto = CanvasRenderingContext2D.prototype;
  const orig = proto.fillText;
  const rects = [];
  const origRect = proto.strokeRect;
  proto.fillText = function (t, x, y) { texts.push([String(t), x, y, this.textAlign]); return orig.apply(this, arguments); };
  proto.strokeRect = function (x, y, w, h) { rects.push([x, y, w, h]); return origRect.apply(this, arguments); };
  let lay = null;
  const call = GSApi.call;
  GSApi.call = async (name, args) => { const r = await call(name, args); if (name === "png_layout") lay = r; return r; };
  let png = "";
  try {
    const blob = await gsApp.pngBlob("");
    const buf = new Uint8Array(await blob.arrayBuffer());
    let s = ""; for (let i = 0; i < buf.length; i++) s += String.fromCharCode(buf[i]);
    png = btoa(s);
  } finally { proto.fillText = orig; proto.strokeRect = origRect; GSApi.call = call; }
  return { texts, rects, lay, png };
})()
"""


@unittest.skipUnless(edge_available(), "Microsoft Edge が無い")
class WebPngAxesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
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

    def render(self, size: str) -> dict:
        self.page.ev(f"gsApp.setPng('size', {json.dumps(size)}); 1")
        return self.page.ev(RECORD_JS)

    def test_axes_follow_report_layout(self):
        open_app(self.page, self.app, 1280, 720)
        for size in ("1600x1000", "1280x800"):
            with self.subTest(size=size):
                r = self.render(size)
                lay = r["lay"]
                ax = lay["axes"]
                lh = lay["line_px"]
                margin = lay["margin_px"]
                texts = r["texts"]
                # 枠（Qt と同じ位置）
                frame = [x for x in r["rects"] if abs(x[0] - ax["plot_left"]) < 0.01 and abs(x[1] - ax["plot_top"]) < 0.01]
                self.assertEqual(len(frame), 1, r["rects"])
                self.assertAlmostEqual(frame[0][0] + frame[0][2], ax["plot_right"])
                plot_b = frame[0][1] + frame[0][3]
                # 横軸の数字: x_every 目盛ごと・同じ段。見出しはその1行下の真ん中
                row = [t for t in texts if abs(t[2] - (plot_b + ax["x_label_top"] + lh / 2)) < 0.01]
                self.assertEqual([t[0] for t in row], lay["x_labels"][::ax["x_every"]])
                title = [t for t in texts if abs(t[2] - (plot_b + ax["x_title_top"] + lh / 2)) < 0.01]
                self.assertEqual([t[0] for t in title], [lay["x_title"]])
                # 縦軸の数字: 右端をそろえ、10 区画の線の高さ
                ylab = [t for t in texts if t[0] in lay["y_labels"] and t[3] == "right"]
                self.assertEqual([t[0] for t in ylab], lay["y_labels"])
                self.assertTrue(all(abs(t[1] - ax["y_label_right"]) < 0.01 for t in ylab))
                self.assertAlmostEqual(ylab[0][2], ax["plot_top"])
                self.assertAlmostEqual(ylab[-1][2], plot_b)
                # 縦軸の単位は凡例の段の左端
                unit = [t for t in texts if t[0] == lay["unit"] and abs(t[1] - ax["unit_left"]) < 0.01]
                self.assertEqual(len(unit), 1)
                self.assertAlmostEqual(unit[0][2], margin + lh / 2)
                self.assertEqual(ax["legend_left"], ax["plot_left"])
                out = Path(self.app.tmp.name) / f"web_{size}.png"
                out.write_bytes(base64.b64decode(r["png"]))
                self.assertGreater(out.stat().st_size, 1000)
