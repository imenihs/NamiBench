"""Web の S11（反射。設計E §6.5・設計R §3・§5.1・設計J §4.2・§4.5）。Qt と同じ部品・語・動き（J §5）。

- 語と決まりの表が Web へ届く（表示の形式・S11 の手順・しきい値の決まり）
- 状態に付けて送る画面の中身（縦軸・表示の形式の欄・描かない理由・S11 の手順・上段のボタン）が Qt と同じ関数
- 画面のファイルが同じ部品を持つ（トレースの行の表示の形式・信号源のタブのオープン/ショート・1目盛の欄）
- PNG の縦軸の数字と帯の条件（|Γ|・SWR）・描いていないトレースのマーカーを描かない
- ヘッドレスの Edge で、S11 の画面を通す（Edge と CDN が無いときは飛ばす）
"""
from __future__ import annotations

import time
import unittest
from pathlib import Path

from pygigast.api.web import server as web
from pygigast.api.web import view
from pygigast.ui_qt import screen_model, screen_s11
from pygigast.ui_qt import texts as qt_texts

UI_WEB = Path(__file__).resolve().parent.parent / "pygigast" / "ui_web"
MHZ = 1_000_000


def s11_state(fmt1="swr", visible2=True) -> dict:
    """S11 で基準を使っているときの状態の形（試験用の小さな形）。"""
    unit1 = {"swr": "SWR", "gamma": "|Γ|", "db": "dB"}[fmt1]
    return {
        "sweep": {"mode": "s11"}, "connection": {"state": "connected", "identity": {"serial": "SIM00001"}},
        "amplitude": {"ref_level": 0.0, "db_per_div": 10, "unit": "dB", "swr_per_div": 0.5},
        "display_quantity": "ratio", "tasks": [],
        "normalization": {"used": True, "applied": True, "selected": {"name": "S11 12:00", "taken_at": 1.0},
                          "s11_open": {"kind": "fine", "taken_at": 1.0}},
        "traces": [
            {"number": 1, "visible": True, "has_values": True, "unit": unit1, "quantity": "ratio", "format": fmt1,
             "format_effective": fmt1, "measurement": "s11", "value_kind": "real"},
            {"number": 2, "visible": visible2, "has_values": True, "unit": "dB", "quantity": "ratio", "format": "db",
             "format_effective": "db", "measurement": "s11", "value_kind": "real"},
        ],
    }


class TextsReachTheWebTest(unittest.TestCase):
    def test_tables(self):
        tj = web.texts_json()
        self.assertEqual(tj["TRACE_FORMATS"], qt_texts.TRACE_FORMATS)
        self.assertEqual(tj["S11_FORMAT_NAMES"], qt_texts.S11_FORMAT_NAMES)
        self.assertEqual(tj["S11_QUICK_LABELS"], qt_texts.S11_QUICK_LABELS)
        self.assertEqual(tj["THRESHOLD_SPECS"]["SWR"]["choices"][:3], [1.2, 1.5, 2.0])
        for key in ("s11_range", "s11_range_wide", "s11_range_fine", "s11_open", "s11_short", "trace_format", "per_div"):
            self.assertEqual(tj["LABELS"][key], qt_texts.LABELS[key], key)
        for key in ("format_vector", "format_s11_only", "ref_linear", "trace_unit_differs", "s11_needs_open"):
            self.assertIn(key, tj["REASON_TEXTS"])
        self.assertIn("ref_s11_name", tj["TEMPLATES"])
        # S11 のモードは使える（まだ使えないの理由を持たない）
        self.assertIsNone(dict((m[0], m[3]) for m in tj["MODES"])["s11"])


class StateViewTest(unittest.TestCase):
    def test_ui_has_the_same_views_as_qt(self):
        st = s11_state()
        ui = view.state_view(st)["ui"]
        self.assertEqual(ui["axis"], screen_s11.axis_view(st, "SWR"))
        self.assertTrue(ui["axis"]["linear"])
        self.assertAlmostEqual(ui["axis"]["top"], 6.0)
        self.assertEqual(ui["axis"]["div_label"], "SWR/div")
        self.assertEqual(ui["trace_format"]["1"]["value"], "swr")
        self.assertIsNone(ui["trace_drawn"]["1"])
        self.assertIn("SWR", ui["trace_drawn"]["2"])
        self.assertEqual(ui["s11"]["next"], "short")
        self.assertEqual(ui["ref_quick"]["label"], qt_texts.S11_QUICK_LABELS["short"])
        self.assertEqual(ui["ref_quick"]["take"], {"standard": "short", "kind": None, "show_zero": True})
        self.assertEqual(ui["unit"], "SWR")
        # 隠したトレースには描かない理由を出さない
        self.assertIsNone(view.state_view(s11_state(visible2=False))["ui"]["trace_drawn"]["2"])

    def test_marker_rows_carry_unit(self):
        snap = {"markers": [{"id": "M1", "kind": "M", "number": 1, "trace": 1, "freq_hz": 145 * MHZ, "level": 1.06,
                             "unit": "SWR", "status": "ok", "notes": [], "polarity": "dip", "bandwidth": [],
                             "threshold": None, "freq_offsets": [], "level_offsets": []}], "xy": {}}
        out = view.markers_view(snap, "SWR")
        self.assertEqual(out["rows"][0]["unit"], "SWR")
        self.assertEqual(out["rows"][0]["cells"][3], "1.06 SWR")

    def test_png_layout_linear_axis(self):
        st = s11_state()
        st["markers"] = {"markers": [], "xy": {}}
        lay = view.png_layout(st, {"width": 1600, "height": 1000, "ref_level": 3.0, "db_per_div": 0.2,
                                   "unit": "SWR"})
        self.assertEqual(lay["y_labels"][0], "3.0")
        self.assertEqual(lay["y_labels"][-1], "1.0")
        cond = next(items for color, items in lay["band"] if any("SWR" in i for i in items))
        self.assertIn("SWR 1〜3（0.2 SWR/div）", cond)
        # 縦軸と単位の違うトレース（2: dB）は凡例に入れない
        self.assertEqual([it["number"] for it in lay["legend"]], [1])


class MarkupTest(unittest.TestCase):
    def setUp(self):
        self.html = (UI_WEB / "index.html").read_text(encoding="utf-8")
        self.app = (UI_WEB / "app.js").read_text(encoding="utf-8")

    def test_trace_rows_have_format(self):
        sec = self.html[self.html.index('data-sec="traces"'):self.html.index('data-sec="files"')]
        self.assertIn('class="tfmt"', sec)
        self.assertIn("traceFormat(t).items", sec)
        self.assertIn(':disabled="!it.enabled"', sec)
        self.assertIn("traceDrawnReason(t)", sec)

    def test_source_tab_and_top_bar(self):
        src = self.html[self.html.index('data-tab="source"'):]
        for act in ("take_wide", "take_fine", "s11_open", "s11_short", "ref_use", "ref_unuse", "ref_rename", "ref_delete"):
            self.assertIn(f'data-act="{act}"', src, act)
        self.assertIn("takeS11('open')", src)
        self.assertIn("s11.state_text", src)
        self.assertIn('data-field="s11_range"', src)
        top = self.html[:self.html.index('<div class="cond"')]
        self.assertIn('@click="takeQuick"', top)
        self.assertIn("refQuick.label", top)
        self.assertIn("axis.div_label", top)
        self.assertIn('<script src="app_s11.js"></script>', self.html)
        self.assertIn("merge(base, window.GSAppS11 || {});", self.app)

    def test_graph_takes_linear_axis(self):
        g = (UI_WEB / "graph.js").read_text(encoding="utf-8")
        self.assertIn("setY(ref, div, unit, linear, decimals)", g)
        self.assertIn("this.amp.linear, this.amp.decimals", self.app)


def edge_ok() -> bool:
    try:
        from tests.test_web_layout import edge_available
    except Exception:  # noqa: BLE001
        return False
    return edge_available()


@unittest.skipUnless(edge_ok(), "Microsoft Edge が無い")
class S11BrowserTest(unittest.TestCase):
    """ヘッドレスの Edge で、S11 の基準の手順と表示の形式を通す。"""

    @classmethod
    def setUpClass(cls):
        from tests.test_web_layout import App, EdgePage

        cls.app = App()
        try:
            cls.page = EdgePage()
        except Exception:
            cls.app.close()
            raise

    @classmethod
    def tearDownClass(cls):
        cls.page.close()
        cls.app.close()

    def wait_tasks(self):
        end = time.monotonic() + 30
        while self.app.call("get_state")["tasks"]:
            self.assertLess(time.monotonic(), end)
            time.sleep(0.05)

    def test_open_short_and_swr(self):
        from tests.test_web_layout import open_app

        a, p = self.app, self.page
        a.call("set_sweep", start_hz=100 * MHZ, stop_hz=200 * MHZ)
        a.call("set_mode", mode="s11")
        open_app(p, a, 1280, 720)
        p.ev("gsApp.tab = 'source'; 1")
        p.settle()
        btn = "document.querySelector('[data-act=\"s11_%s\"]')"
        self.assertFalse(p.ev((btn % "open") + ".disabled"))
        self.assertTrue(p.ev((btn % "short") + ".disabled"))
        self.assertTrue(p.ev((btn % "open") + ".classList.contains('next')"))
        self.assertTrue(p.ev("document.querySelector('[data-act=\"take_wide\"]').disabled"))
        p.ev("gsApp.s11Range = 'fine'; " + (btn % "open") + ".click(); 1")
        p.wait("!!(gsApp.ui.s11 && gsApp.ui.s11.next === 'short')", "次はショート")
        p.settle()
        self.assertTrue(p.ev((btn % "short") + ".classList.contains('next')"))
        self.assertIn(qt_texts.LABELS["s11_range_fine"], p.ev("document.querySelector('[data-field=\"s11_state\"]').textContent"))
        self.assertEqual(p.ev("document.querySelector('.refquick button').textContent.trim()"),
                         qt_texts.S11_QUICK_LABELS["short"])
        p.ev((btn % "short") + ".click(); 1")
        p.wait("gsApp.st.display_quantity === 'ratio' && gsApp.refs.length === 1", "基準ができて一覧に出る", timeout=40)
        # 表示の形式を SWR に（トレースの行のドロップダウン）
        p.ev("gsApp.tab = 'trace'; 1")
        p.settle()
        opts = p.ev("[...document.querySelector('.tfmt').options].map((o) => [o.value, o.disabled])")
        self.assertEqual([o[0] for o in opts], ["db", "gamma", "swr", "phase", "group_delay", "smith", "polar"])
        self.assertEqual([o[1] for o in opts], [False, False, False, True, True, True, True])
        p.ev("(() => { const s = document.querySelector('.tfmt'); s.value = 'swr'; s.dispatchEvent(new Event('change')); return 1; })()")
        p.wait("gsApp.axisUnit === 'SWR'", "縦軸が SWR")
        p.settle()
        self.assertTrue(p.ev("document.querySelector('.field.ref input').disabled"))
        self.assertEqual(p.ev("gsApp.amp.ref"), 3.0)
        self.assertEqual(p.ev("gsApp.axis.div_label"), "SWR/div")
        # 縦軸と単位が違うトレース（2: dB）を表示すると、描かずにその行へ理由
        a.call("set_trace", number=2, visible=True, action="update")
        p.wait("!!document.querySelector('.tunit')", "描かない理由")
        self.assertIn("SWR", p.ev("document.querySelector('.tunit').textContent"))
        # PNG も SWR の縦軸で描ける（画面と同じ部品: png.js）
        size = p.ev("gsApp.pngBlob('').then((b) => b.size)")
        self.assertGreater(size, 1000)
        self.assertEqual(p.errors, [])
        a.call("set_trace", number=1, format="db")
        a.call("set_trace", number=2, visible=False, action="average", count=16)


if __name__ == "__main__":
    unittest.main()
