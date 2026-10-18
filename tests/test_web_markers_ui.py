"""Web の画面: マーカー表の上のボタンの並び・山／谷（設計G §3.8）・行の「操作」（G §3.4）・ピークの一覧（設計P §9.7）。

Qt の画面（tests/test_ui_qt_polarity.py）と同じ並び・同じ語・同じ動きであることを、ヘッドレスの Edge で開いた
Web の画面で確かめる（道具は tests/test_web_layout.py の EdgePage・App・open_app）。

- ボタンの並び: 山・谷・ピーク・◀ ネクストピーク・ネクストピーク ▶・次に高い・マーカー追加、右端に全部消す・表をコピー
  （Qt と同じ。別ウィンドウにするボタンは題の行: Qt のドックの題と同じ所）。谷で名前が変わり、メニューの項目の名前も
  変わる。選択はブラウザに残る
- 次のピークはマーカーが無くても押せる（M を作ってから動かす: Qt と同じ）。全部消すは何本消えるかを聞く
- 行の「操作」: 見出し「操作」、項目はサーバーの marker_rows.row_ops（Qt と同じ）。デルタの基準が無ければ 2点を範囲に は
  使えない理由を出す
- ピークの一覧: SA は「ノイズフロアから」で、谷にすると探すが理由付きで使えない。TG（帯域除去フィルタを挟んだ比）は
  「最大から」、谷の一覧は深い順・差の列「最大からの差 (dB)」・見つけた数の行、「P に登録」は谷の P
"""
from __future__ import annotations

import json
import time
import unittest

from namibench.ui_qt import peak_view, texts
from tests.test_marker_dips import bef_in_tg
from tests.test_web_layout import App, EdgePage, edge_available, open_app

MHZ = 1_000_000
L = texts.LABELS


@unittest.skipUnless(edge_available(), "Microsoft Edge が無い")
class WebMarkerToolsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dut = {"on": False}
        cls._bef = bef_in_tg(lambda: cls.dut["on"])
        cls._bef.__enter__()
        cls.app = App(lan=False)
        try:
            cls.page = EdgePage()
        except Exception:
            cls.app.close()
            cls._bef.__exit__(None, None, None)
            raise

    @classmethod
    def tearDownClass(cls):
        cls.page.close()
        cls.app.close()
        cls._bef.__exit__(None, None, None)

    def setUp(self):
        # どの試験も同じ所から: SA・400〜600MHz を1回掃引し、マーカーは 500MHz の M1 だけ・山
        a = self.app
        self.dut["on"] = False
        a.call("set_mode", mode="sa")
        a.call("set_sweep", start_hz=400 * MHZ, stop_hz=600 * MHZ)
        a.call("marker_clear")
        self.sweep_once()
        a.call("marker_add", kind="M", trace=1, freq_hz=500 * MHZ)
        open_app(self.page, self.app, 1280, 720)
        self.page.ev("gsApp.setPolarity('peak'); 1")
        self.page.settle()

    def sweep_once(self):
        a = self.app
        n0 = a.call("get_state")["run"]["sweep_count"]
        a.call("run", mode="single")
        end = time.monotonic() + 30
        while True:
            run = a.call("get_state")["run"]
            if run["state"] == "stopped" and run["sweep_count"] > n0:
                return
            self.assertLess(time.monotonic(), end, "掃引が終わらない")
            time.sleep(0.05)

    def ev(self, js):
        return self.page.ev(js)

    def tools(self):
        return self.ev("[...document.querySelectorAll('.marker-tools button')].filter((e) => e.getClientRects().length)"
                       ".map((e) => e.textContent.trim())")

    def test_toolbar_same_as_qt_and_switch(self):
        S = texts.SEARCH_BUTTONS
        want = [texts.POLARITIES["peak"], texts.POLARITIES["dip"]] + [S["peak"][c] for c in peak_view.SEARCH_CODES] \
            + [L["clear_all"], L["copy_table"]]
        self.assertEqual(self.tools(), want)
        # 別ウィンドウにするボタンはボタンの並びではなく題の行（Qt のドックの題「マーカー表」と同じ所）
        head = self.ev("[...document.querySelectorAll('.marker-box .part-bar > *')].map((e) => e.textContent.trim())")
        self.assertEqual(head, [L["part_markers"], L["detach"]])
        self.assertTrue(self.ev("document.querySelector('.pol-btn[data-polarity=peak]').classList.contains('on')"))
        self.ev("document.querySelector('.pol-btn[data-polarity=dip]').click(); 1")
        self.page.settle()
        self.assertEqual(self.tools()[2:7], [S["dip"][c] for c in peak_view.SEARCH_CODES])
        self.assertEqual(self.tools()[2:7], ["ピーク", "◀ ネクストピーク", "ネクストピーク ▶", "ネクストピーク（レベル順・深い）", "マーカー追加"])
        self.assertEqual(self.ev("gsApp.menuItem('marker_peak').label"), texts.DIP_MENU_ITEMS["marker_peak"])
        self.assertEqual(self.ev("localStorage.getItem('pygigast.polarity')"), "dip")
        # メニューの「山／谷を切り替える」で戻る
        self.ev("gsApp.trigger('marker_polarity'); 1")
        self.page.settle()
        self.assertEqual(self.tools()[2], S["peak"]["marker_peak"])
        self.assertEqual(self.ev("gsApp.menuItem('marker_peak').label"), texts.MENU_ITEMS["marker_peak"])

    def test_next_peak_without_marker_and_clear_asks(self):
        # 50MHz〜2GHz: 一番高い 100MHz の信号の右にスプリアスと 1500MHz の信号がある
        self.app.call("set_sweep", start_hz=50 * MHZ, stop_hz=2000 * MHZ)
        self.sweep_once()
        self.app.call("marker_clear")
        self.page.wait("gsApp.markers.markers.length === 0", "マーカーが無い")
        self.ev("gsApp.selectedMarker = null; 1")
        # 次のピークはマーカーが無くても押せる（M を作ってから動かす: G §3.5。Qt と同じ）
        self.assertIsNone(self.ev("gsApp.itemReason('marker_peak_right')"))
        self.ev("document.querySelector('.marker-tools button[data-code=marker_peak_right]').click(); 1")
        self.page.wait("gsApp.markers.markers.length === 1", "次のピークで M1")
        # 全部消すは何本消えるかを聞く（Qt と同じ）
        self.ev("[...document.querySelectorAll('.marker-tools button')].find((e) => e.textContent.trim() === "
                f"{json.dumps(L['clear_all'])}).click(); 1")
        self.page.wait("!!gsApp.confirmBox", "全部消すの確かめ")
        self.assertIn("すべてのマーカー（1）", self.ev("gsApp.confirmBox.text"))
        self.ev("gsApp.answerConfirm(false); 1")
        self.page.settle()
        self.assertEqual(len(self.app.call("get_state")["markers"]["markers"]), 1)

    def test_row_ops_dropdown(self):
        last = self.ev("[...document.querySelectorAll('.marker-box .mtable thead th')].slice(-1)[0].textContent.trim()")
        self.assertEqual(last, L["row_ops"])
        # 行の「操作」のボタンの文字は「操作」（▾ は見た目の印で、文字には入れない）
        self.assertEqual(self.ev("document.querySelector('.marker-box .row-ops').textContent.trim()"), L["row_ops"])
        self.ev("[...document.querySelectorAll('.marker-box .mtable .row-ops')][0].click(); 1")
        self.page.wait("!!(gsApp.ctx && document.querySelector('.ctxmenu button'))", "行の「操作」")
        self.page.settle()
        # 一覧は画面の中に収まる（表は画面の下端の近くにあるので、下に入らなければボタンの上に開く）
        self.assertTrue(self.ev("(() => { const r = document.querySelector('.ctxmenu').getBoundingClientRect();"
                                " return r.top >= 0 && r.bottom <= innerHeight && r.right <= innerWidth; })()"))
        items = self.ev("[...document.querySelector('.ctxmenu').children].map((e) => e.tagName === 'HR' ? null"
                        " : [e.textContent.trim(), e.classList.contains('dis'), e.title])")
        self.assertEqual(items, [[L["row_center"], False, ""], [L["row_ref"], False, ""],
                                 [texts.MENU_ITEMS["marker_span"], True, texts.REASON_TEXTS["delta_ref"]], None,
                                 ["M1 を削除", False, ""]])
        # 使えない項目を押すと理由を状態バーに出す（閉じる）
        self.ev("document.querySelectorAll('.ctxmenu button')[2].click(); 1")
        self.page.settle()
        self.assertIn(texts.REASON_TEXTS["delta_ref"], self.ev("gsApp.status"))
        # Center に
        center = self.app.call("get_state")["markers"]["markers"][0]["freq_hz"]
        self.ev("[...document.querySelectorAll('.marker-box .mtable .row-ops')][0].click(); 1")
        self.page.wait("!!(gsApp.ctx && document.querySelector('.ctxmenu button'))", "行の「操作」")
        self.ev("document.querySelectorAll('.ctxmenu button')[0].click(); 1")
        end = time.monotonic() + 10
        while self.app.call("get_state")["sweep"]["center_hz"] != center:
            self.assertLess(time.monotonic(), end, "Center にならない")
            time.sleep(0.05)

    def test_marker_span_releases_lock_and_notes(self):
        """Span 固定 ON でメニューの「デルタの2点を Start・Stop に」: 固定を外して2点を Start・Stop にし、Span の欄に
        1回知らせる（設計C §5.13。Qt と同じ文: texts.adjustment_text）。"""
        a = self.app
        a.call("marker_add", kind="M", trace=1, freq_hz=550 * MHZ)
        a.call("marker_set", id="M2", ref="M1")
        a.call("set_sweep", span_lock=True)
        self.page.wait("gsApp.sw && gsApp.sw.span_lock_hz === 200000000", "Span 固定 ON")
        marks = {m["id"]: m["freq_hz"] for m in a.call("get_state")["markers"]["markers"]}
        self.ev("gsApp.selectedMarker = 'M2'; gsApp.trigger('marker_span'); 1")
        self.page.wait("gsApp.sw && gsApp.sw.span_lock_hz == null", "Span 固定が外れる")
        sw = a.call("get_state")["sweep"]
        self.assertEqual((sw["start_hz"], sw["stop_hz"]), tuple(sorted((marks["M1"], marks["M2"]))))
        self.page.wait("!!gsApp.notes.span", "Span の欄の知らせ")
        self.assertEqual(self.ev("gsApp.notes.span.text"), L["span_lock_released"])
        self.assertEqual(L["span_lock_released"], "Span 固定を解除しました")
        self.assertIsNone(self.ev("gsApp.notes.span.action"))

    def test_peak_list_sa_then_tg_dips(self):
        # SA の 50〜200MHz（100MHz の信号がノイズフロアより高い山）
        self.app.call("set_sweep", start_hz=50 * MHZ, stop_hz=200 * MHZ)
        self.sweep_once()
        self.page.wait("gsApp.st.sweep.start_hz === 50000000", "50〜200MHz")
        self.ev("gsApp.tab = 'marker'; 1")
        self.page.settle()
        label = "document.querySelector('[data-field=peak_margin]').textContent.trim()"
        self.assertEqual(self.ev(label), L["from_floor"])
        self.ev("gsApp.getPeakList(); 1")
        self.page.wait("!!(gsApp.peakList && document.querySelectorAll('.mtable.peaks tbody tr').length)", "一覧")
        self.assertEqual(self.ev("document.querySelectorAll('.mtable.peaks thead th')[2].textContent.trim()"),
                         L["col_height_db"])
        found = self.ev("gsApp.peakText().found")
        self.assertRegex(found, r"^検出数 \d+・ノイズフロア −\d+\.\d dBm$")
        # 谷: 電力では一覧の谷を探さない（理由付きで使えない）
        self.ev("gsApp.setPolarity('dip'); 1")
        self.page.settle()
        why = texts.REASON_TEXTS["dips_on_power"]
        for f in ("peak_find", "peak_top"):
            self.assertEqual(self.ev(f"[document.querySelector('[data-field={f}]').disabled,"
                                     f" document.querySelector('[data-field={f}]').title]"), [True, why])
        # TG: 直結で基準を取り、帯域除去フィルタを挟んだ比
        a = self.app
        a.call("set_sweep", start_hz=50 * MHZ, stop_hz=200 * MHZ)
        a.call("set_mode", mode="tg")
        self.dut["on"] = False
        a.call("reference_take", kind="wide", name="direct")
        end = time.monotonic() + 30
        while a.call("get_state")["tasks"] or not (a.call("get_state")["normalization"] or {}).get("selected"):
            self.assertLess(time.monotonic(), end, "基準を取り終えない")
            time.sleep(0.05)
        self.dut["on"] = True
        n0 = a.call("get_state")["run"]["sweep_count"]
        self.sweep_once()
        self.page.wait(f"gsApp.st.display_quantity === 'ratio' && gsApp.st.run.sweep_count > {n0}"
                       " && gsApp.st.run.state === 'stopped'", "比の掃引")
        self.page.settle()
        self.assertEqual(self.ev(label), L["from_max"])
        self.assertFalse(self.ev("document.querySelector('[data-field=peak_find]').disabled"))
        self.ev("gsApp.getPeakList(); 1")
        self.page.wait("!!(gsApp.peakList && gsApp.peakList.polarity === 'dip')", "谷の一覧")
        self.page.settle()
        rows = self.ev("[...document.querySelectorAll('.mtable.peaks tbody tr')].map((tr) => [...tr.cells].slice(0, 3)"
                       ".map((c) => c.textContent.trim()))")
        self.assertEqual(len(rows), 2, rows)
        self.assertAlmostEqual(float(rows[0][0]), 120.0, delta=1.0)
        self.assertAlmostEqual(float(rows[0][2]), -30.0, delta=1.5)
        self.assertLess(float(rows[0][2]), float(rows[1][2]))  # 深い順
        self.assertEqual(self.ev("document.querySelectorAll('.mtable.peaks thead th')[2].textContent.trim()"),
                         L["col_below_max_db"])
        self.assertTrue(self.ev("gsApp.peakText().found").startswith("2 個・最大 "))
        # 「P に登録」は谷の P
        self.ev("document.querySelector('.mtable.peaks tbody tr button').click(); 1")
        end = time.monotonic() + 10
        while True:
            ps = [m for m in a.call("get_state")["markers"]["markers"] if m["kind"] == "P"]
            if ps:
                break
            self.assertLess(time.monotonic(), end, "P に登録されない")
            time.sleep(0.05)
        self.assertEqual(ps[0]["polarity"], "dip")
        self.assertEqual(self.page.errors, [])


if __name__ == "__main__":
    unittest.main()
