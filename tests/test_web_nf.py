"""Web の NF 測定（設計M。ui_web/app_nf.js）。Qt の tab_source の NF の欄・nf_dialog と同じ中身・同じ動き（J §5）。

- 状態の ui.nf に、Qt と同じ nf_view.nf_view の中身が付く。ENR の表は中身を命令で送る（パスを受け取らない: A §4.3.2）
- 語の表（NF_TEXTS・NF_WIRING）が届き、NF のモードは使える
- ブラウザで: NF に入るとつなぎ方の窓が1回だけ出る・NF の欄で ENR を打つ・表を読み込む・平均の種類を選べない理由・
  決めた解像度（1280×720・1920×1080）で崩れが 0 件（tests/test_web_layout と同じ検査）。Edge が無いとき・Vue を CDN から
  読めないときは飛ばす
"""
from __future__ import annotations

import json
import time
import unittest

from pygigast.ui_qt import nf_view
from pygigast.ui_qt import texts as qt_texts
from tests import test_web_server as tws
from tests.test_web_layout import SIZES, App, EdgePage, edge_available, open_app
from tests.test_web_settings import _Base

UI_WEB = tws.UI_WEB
MHZ = 1_000_000
N = qt_texts.NF_TEXTS


class NfServerTest(_Base):
    def test_state_carries_the_same_view_as_qt(self):
        self.connect_and_sweep(400 * MHZ, 600 * MHZ)
        self.assertEqual(self.post("set_mode", {"mode": "nf"})[0], 200)
        st, r = self.post("nf_load_enr_table", {"text": "Frequency (MHz),ENR\n400,15\n600,14\n", "file_name": "t.csv"})
        self.assertEqual(st, 200, r)
        s = self.state()
        ui = s["ui"]["nf"]
        self.assertEqual(ui, json.loads(json.dumps(nf_view.nf_view(s))))
        self.assertIn("t.csv", ui["table_text"])
        self.assertIsNone(s["ui"]["mode_reasons"]["nf"])
        st, r = self.post("nf_load_enr_table", {"text": "400,15\n400,14\n", "file_name": "b.csv"})
        self.assertEqual(r["error"]["code"], "enr_table_unreadable")
        self.assertIn("2 行目", r["error"]["text"])
        st, r = self.post("nf_set_enr", {"enr_db": 14.5})
        self.assertEqual(r["error"]["code"], "enr_table_in_use")

    def test_vertical_axis_is_shared(self):
        """縦軸は全部のモードで1つ（2026-10-09 ユーザー決定）: NF に入っても状態の REF・dB/div は変わらない。"""
        self.connect_and_sweep(400 * MHZ, 600 * MHZ)
        self.assertEqual(self.post("set_amplitude", {"ref_level": -30.0, "db_per_div": 10})[0], 200)
        self.assertEqual(self.post("set_mode", {"mode": "nf"})[0], 200)
        amp = self.state()["amplitude"]
        self.assertEqual((amp["ref_level"], amp["db_per_div"]), (-30.0, 10))

    def test_tables_and_files(self):
        t = json.loads(self.get("/api/v1/texts")[1])
        for name in ("NF_TEXTS", "NF_WIRING", "NF_ERRORS", "ENR_TABLE_REASONS", "NF_MARKER_NOTES"):
            self.assertIn(name, t)
        self.assertEqual(t["MARKER_NOTES"]["nf_small_difference"], qt_texts.MARKER_NOTES["nf_small_difference"])
        nf = next(m for m in t["MODES"] if m[0] == "nf")
        self.assertIsNone(nf[3])
        html = (UI_WEB / "index.html").read_text(encoding="utf-8")
        self.assertIn('<script src="app_nf.js"></script>', html)
        self.assertIn("<gs-nf>", html)
        self.assertIn("<gs-nf-guide", html)


@unittest.skipUnless(edge_available(), "Microsoft Edge が無い")
class NfBrowserTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
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

    def wait_sweep(self):
        app = self.app
        n0 = app.call("get_state")["run"]["sweep_count"]
        app.call("run", mode="single")
        end = time.monotonic() + 30
        while True:
            st = app.call("get_state")
            if st["run"]["state"] == "stopped" and st["run"]["sweep_count"] > n0:
                return st
            self.assertLess(time.monotonic(), end, "掃引が終わらない")
            time.sleep(0.05)

    def test_nf_mode_guide_fields_and_layout(self):
        page, app = self.page, self.app
        app.call("set_amplitude", ref_level=-30.0, db_per_div=10)
        open_app(page, app, *SIZES[0])
        # NF に入ると（どの画面から入っても）つなぎ方の窓が1回だけ出る
        page.ev("gsApp.setMode('nf'); 1")
        page.wait("!!document.querySelector('.modal.nfguide')", "つなぎ方の窓")
        # 縦軸は変えない（全部のモードで1つ: Qt と同じ）
        self.assertEqual(page.ev("[gsApp.amp.ref, gsApp.amp.div]"), [-30, 10])
        page.settle()
        self.assertEqual(page.ev("document.querySelector('.modal.nfguide h3').textContent"), N["guide_title"])
        boxes = page.ev("[...document.querySelectorAll('.modal.nfguide .cal-box')].map(b => b.textContent.trim())")
        self.assertEqual(boxes, list(qt_texts.NF_WIRING))
        found = page.problems(".modal.nfguide", f"{SIZES[0]} つなぎ方")
        page.ev("document.querySelector('.modal.nfguide .modal-btns button').click(); 1")
        page.wait("!gsApp.nfGuide", "閉じる")
        page.ev("gsApp.setMode('sa'); 1")
        page.wait("gsApp.sw.mode === 'sa'", "SA")
        page.ev("gsApp.setMode('nf'); 1")
        page.wait("gsApp.sw.mode === 'nf'", "NF")
        page.settle()
        time.sleep(0.3)
        self.assertFalse(page.ev("gsApp.nfGuide"))
        # NF の欄: ENR が無い知らせ → ENR を打つ
        page.ev("gsApp.tab = 'source'; 1")
        page.settle()
        self.assertEqual(page.ev("document.querySelector('[data-nf=enr-warn]').textContent.trim()"), N["enr_needed"])
        page.ev("(() => { const i = document.querySelector('[data-field=enr] input'); i.focus(); i.value = '14.25';"
                " i.dispatchEvent(new Event('input')); i.dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter'}));"
                " return 1; })()")
        end = time.monotonic() + 10
        while (app.call("get_state")["nf"].get("enr") or {}).get("db") != 14.25:
            self.assertLess(time.monotonic(), end, "ENR が入らない")
            time.sleep(0.05)
        page.wait("!document.querySelector('[data-nf=enr-warn]')", "ENR の知らせが消える")
        self.assertEqual(page.ev("document.querySelector('[data-field=enr] input').value"), "14.25 dB")
        # 表（中身を送る）と、いまの範囲の一部が表の外の知らせ
        app.call("nf_load_enr_table", text="Frequency (MHz),ENR\n400,15\n500,14\n", file_name="346B_ENR.csv")
        page.wait("!!document.querySelector('[data-nf=table-warn]')", "表の範囲の知らせ")
        self.assertIn("346B_ENR.csv", page.ev("document.querySelector('[data-nf=table]').textContent"))
        self.assertTrue(page.ev("document.querySelector('[data-field=enr] input').disabled"))
        # 平均の種類は選べない（理由はツールチップ）
        app.call("set_trace", number=1, action="average", count=4)
        self.wait_sweep()
        page.ev("gsApp.tab = 'trace'; 1")
        page.settle()
        self.assertTrue(page.ev("document.querySelector('select.tavg').disabled"))
        self.assertIn(N["average_type_nf"], page.ev("document.querySelector('select.tavg').title"))
        # 単位の欄は NF の dB
        self.assertEqual(page.ev("gsApp.unitView.current"), "nf")
        # HOT と COLD の差が小さい点は控えめな色で重ねる（Qt の plot_layers.marked_points と同じ点）
        app.api._inst._device.config.nf_hot_db = 0.7
        app.call("clear_trace", number=1)  # 単発は1組ずつ足す（設計F §5.2.1）: 前の組の HOT・COLD を混ぜない
        st = self.wait_sweep()
        page.wait(f"gsApp.st && gsApp.st.run.sweep_count >= {st['run']['sweep_count']}", "掃引の状態")
        page.settle()
        got = page.ev("""(() => { const t = gsApp.$traces.get('1');
          const g = gsApp.graphTraces(gsApp.palette).find((x) => x.y === t.values);
          const r = ((t.flags || {}).points || {}).nf_small_difference || [];
          return {mark: g && g.mark ? Array.from(g.mark).reduce((a, v) => a + v, 0) : 0,
                  small: r.reduce((a, x) => a + (x[1] - x[0]), 0)}; })()""")
        self.assertGreater(got["small"], 0)
        self.assertGreaterEqual(got["mark"], got["small"])
        app.api._inst._device.config.nf_hot_db = 3.0
        # 決めた解像度で崩れを数える（信号源のタブ・つなぎ方の窓。読めない理由の行も出して）
        for w, h in SIZES:
            open_app(page, app, w, h)
            page.ev("gsApp.tab = 'source'; gsApp.nfFlashNote(GSTexts.get('ERRORS', 'enr_table_unreadable')); 1")
            page.settle()
            found += page.problems(".app", f"{w}×{h} 信号源 NF")
            page.ev("gsApp.openNfGuide(); 1")
            page.settle()
            found += page.problems(".modal.nfguide", f"{w}×{h} つなぎ方")
            page.ev("gsApp.closeNfGuide(); gsApp.nfFlashNote(''); 1")
        self.assertEqual(sorted(set(found)), [])
        self.assertEqual(page.errors, [])
        app.call("set_mode", mode="sa")
        app.call("nf_clear_enr_table")


if __name__ == "__main__":
    unittest.main()
