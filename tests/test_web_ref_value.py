"""Web の基準の値（設計E §6.7: 表示 = 基準との比 + 基準の値）。Qt と同じ部品・語・動き（J §5）。

- 語と欄の決まりの表が Web へ届く（Qt と同じ表）
- 状態に付けて送る画面の中身（欄の使えない理由・上段と信号源のタブの基準の文）と、一覧の行の文（命令の戻り値に付ける）が
  Qt と同じ関数
- 知らせ（直した）と失敗（範囲の外）の文が Qt と同じ
- 画面のファイルが同じ部品を持ち、取るときに欄の値を送る
- ヘッドレスの Edge で、欄の値で取る → 一覧で直す → S11 で使えない を通す（Edge と CDN が無いときは飛ばす）
"""
from __future__ import annotations

import time
import unittest
from pathlib import Path

from namibench.api.web import server as web
from namibench.api.web import view
from namibench.ui_qt import screen_model, screen_s11
from namibench.ui_qt import texts as qt_texts

UI_WEB = Path(__file__).resolve().parent.parent / "namibench" / "ui_web"
MHZ = 1_000_000


def tg_state(value_db=40.0, mode="tg") -> dict:
    return {
        "sweep": {"mode": mode}, "connection": {"state": "connected", "identity": {"serial": "SIM00001"}},
        "amplitude": {"ref_level": 50.0, "db_per_div": 10, "unit": "dB"}, "display_quantity": "ratio", "tasks": [],
        "normalization": {"used": True, "applied": True, "accuracy": "coarse",
                          "selected": {"name": "ATT 40", "taken_at": 1.0e9, "value_db": value_db}},
        "traces": [],
    }


class TextsReachTheWebTest(unittest.TestCase):
    def test_tables(self):
        tj = web.texts_json()
        self.assertEqual(tj["REF_VALUE_SPEC"], qt_texts.REF_VALUE_SPEC)
        self.assertEqual((tj["REF_VALUE_SPEC"]["lo"], tj["REF_VALUE_SPEC"]["hi"]), (-100.0, 100.0))
        for key in ("ref_value", "ref_value_tip", "ref_value_dots", "ref_value_title", "ref_value_new"):
            self.assertEqual(tj["LABELS"][key], qt_texts.LABELS[key], key)
        self.assertEqual(tj["REASON_TEXTS"]["ref_value_s11"], qt_texts.REASON_TEXTS["ref_value_s11"])
        for key in ("norm_ref_value", "ref_selected_value", "ref_quick_name_value", "ref_quick_now_value",
                    "ref_value_changed"):
            self.assertIn(key, tj["TEMPLATES"])
        self.assertIn("reference_value_s11", tj["ERRORS"])


class StateViewTest(unittest.TestCase):
    def test_texts_with_value(self):
        st = tg_state(40.0)
        ui = view.state_view(st)["ui"]
        self.assertEqual(ui["ref_value"], screen_s11.ref_value_view(st))
        self.assertIsNone(ui["ref_value"]["reason"])
        self.assertEqual(ui["ref_quick"]["text"], "基準: ATT 40（" + qt_texts.hhmm(1.0e9) + "・+40.0 dB）")
        self.assertTrue(ui["ref_state"].startswith("基準: ATT 40（+40.0 dB）・"), ui["ref_state"])
        self.assertIn("基準: ATT 40（+40.0 dB）", ui["normalization"])
        # 0 dB は出さない（U-15）。負の数は「−」
        self.assertNotIn("dB）", view.state_view(tg_state(0.0))["ui"]["ref_quick"]["text"])
        self.assertIn("（−12.5 dB）", view.state_view(tg_state(-12.5))["ui"]["ref_state"])
        # 使わないモードでは「選んでいる基準: 名前（日時・値）」
        st = tg_state(40.0, mode="sa")
        st["normalization"] = {"used": False, "selected": st["normalization"]["selected"]}
        self.assertTrue(screen_s11.ref_state_text(st).endswith("・+40.0 dB）"))
        # S11 では欄を理由付きで使えなくする
        self.assertEqual(view.state_view(tg_state(mode="s11"))["ui"]["ref_value"]["reason"],
                         qt_texts.REASON_TEXTS["ref_value_s11"])

    def test_reference_list_rows(self):
        refs = [{"id": "a", "name": "ATT", "mode": "tg", "taken_at": 1.0e9, "selected": True, "value_db": -40.0},
                {"id": "b", "name": "S11 1", "mode": "s11", "taken_at": None, "selected": False, "value_db": 0.0}]
        out = view.result_view("reference_list", {}, {"references": refs})
        rows = out["references"]
        self.assertEqual(rows[0]["text"], screen_s11.ref_row(refs[0])["text"])
        self.assertTrue(rows[0]["text"].startswith("● ATT  TG  "))
        self.assertTrue(rows[0]["text"].endswith("  −40.0 dB"))
        self.assertIsNone(rows[0]["value_reason"])
        self.assertEqual(rows[1]["text"], "   S11 1  S11  ")
        self.assertEqual(rows[1]["value_reason"], qt_texts.REASON_TEXTS["ref_value_s11"])

    def test_notice_and_error_texts(self):
        n = view.notice_view({"code": "reference_value_changed",
                              "detail": {"name": "ATT", "value_db": -40.0, "previous": 0.0}}, tg_state())
        self.assertEqual(n["view"]["text"], "基準「ATT」の値を −40.0 dB に変更しました")
        n = view.notice_view({"code": "reference_value_changed", "detail": {"name": "ATT", "value_db": 0.0}}, None)
        self.assertEqual(n["view"]["text"], "基準「ATT」の値を 0.0 dB に変更しました")
        e = view.error_view({"error": {"code": "reference_value_range", "detail": {"lo": -100.0, "hi": 100.0}}})
        self.assertEqual(e["error"]["text"], "−100〜+100 dB の値を入力してください")
        e = view.error_view({"error": {"code": "reference_value_s11", "detail": {}}})
        self.assertEqual(e["error"]["text"], qt_texts.ERRORS["reference_value_s11"])

    def test_png_band_carries_the_value(self):
        """PNG の帯（Qt と Web が同じ report_layout で作る）のノーマライズの行に基準の値。0 dB では出さない。"""
        def band_items(value):
            st = tg_state(value)
            st["markers"] = {"markers": [], "xy": {}}
            lay = view.png_layout(st, {"width": 1600, "height": 1000, "ref_level": 50.0, "db_per_div": 10, "unit": "dB"})
            return [i for _color, items in lay["band"] for i in items]

        row = next(i for i in band_items(-40.0) if i.startswith("ノーマライズ: ATT 40"))
        self.assertIn(" 基準の値 −40.0 dB", row)
        self.assertFalse(any("基準の値" in i for i in band_items(0.0)))

    def test_quick_take_is_the_same_for_qt_and_web(self):
        # 上段で送る中身はサーバーの ref_quick_view（TG は基準の値を画面が足す・S11 には足さない）
        self.assertEqual(screen_model.ref_quick_view(tg_state())["take"], {"standard": None, "kind": "wide",
                                                                           "show_zero": True})


class MarkupTest(unittest.TestCase):
    def setUp(self):
        self.html = (UI_WEB / "index.html").read_text(encoding="utf-8")
        self.js = (UI_WEB / "app_s11.js").read_text(encoding="utf-8")

    def test_source_tab(self):
        src = self.html[self.html.index('data-tab="source"'):]
        self.assertIn('data-field="ref_value"', src)
        self.assertIn('data-act="ref_value"', src)
        self.assertIn("refValueSpec.choices", src)
        self.assertIn("refValueView.reason", src)
        self.assertIn("{{ r.text }}", src)  # 行の文はサーバー（Qt と同じ screen_s11.ref_row）
        # 名前…・基準の値…・消す… の順（Qt と同じ）
        self.assertLess(src.index('data-act="ref_rename"'), src.index('data-act="ref_value"'))
        self.assertLess(src.index('data-act="ref_value"'), src.index('data-act="ref_delete"'))

    def test_takes_send_the_value(self):
        take_ref = self.js[self.js.index("async takeRef(kind)"):self.js.index("async takeS11(")]
        self.assertIn("value_db: Number(this.refValue) || 0", take_ref)
        quick = self.js[self.js.index("takeQuick()"):self.js.index("async refAction(")]
        self.assertIn("value_db: Number(this.refValue) || 0", quick)
        self.assertIn('"reference_set_value"', self.js)
        self.assertIn('p.code === "reference_value_changed"', self.js)


def edge_ok() -> bool:
    try:
        from tests.test_web_layout import edge_available
    except Exception:  # noqa: BLE001
        return False
    return edge_available()


@unittest.skipUnless(edge_ok(), "Microsoft Edge が無い")
class RefValueBrowserTest(unittest.TestCase):
    """ヘッドレスの Edge で、欄の値で取る → 一覧で直す → S11 では使えない を通す（崩れの数は test_web_layout）。"""

    @classmethod
    def setUpClass(cls):
        from tests.test_web_layout import App, EdgePage

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

    def wait_tasks(self):
        end = time.monotonic() + 30
        while self.app.call("get_state")["tasks"]:
            self.assertLess(time.monotonic(), end)
            time.sleep(0.05)

    def test_take_edit_and_s11(self):
        from tests.test_web_layout import SIZES, open_app

        a, p = self.app, self.page
        a.call("set_sweep", start_hz=100 * MHZ, stop_hz=200 * MHZ)
        a.call("set_mode", mode="tg")
        try:
            open_app(p, a, *SIZES[0])
            p.ev("gsApp.tab = 'source'; 1")
            p.settle()
            inp = "document.querySelector('[data-field=\"ref_value\"] input')"
            self.assertEqual(p.ev(inp + ".value"), "0.0 dB")
            self.assertFalse(p.ev(inp + ".disabled"))
            # 欄に打って確定 → 上段の「基準を取る」がその値で取る
            p.ev(f"(() => {{ const e = {inp}; e.focus(); e.value = '-40'; e.dispatchEvent(new Event('input'));"
                 " e.dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter'})); return 1; })()")
            p.wait("gsApp.refValue === -40", "欄の値")
            p.ev("document.querySelector('.refquick button').click(); 1")
            p.wait("gsApp.refs.length === 1 && gsApp.st.normalization.selected"
                   " && gsApp.st.normalization.selected.value_db === -40", "−40 dB の基準", timeout=40)
            self.wait_tasks()
            p.ev("gsApp.answerConfirm(true); 1")
            p.wait("(document.querySelector('.refquick-state') || {}).textContent.includes('−40.0 dB')", "上段の文")
            self.assertTrue(p.ev("document.querySelector('.ref-list td').textContent.trim().endsWith('−40.0 dB')"))
            btn = "document.querySelector('[data-act=\"ref_value\"]')"
            self.assertTrue(p.ev(btn + ".disabled"))
            # 一覧で選んで「基準の値…」→ 窓で直す（測り直さない）
            p.ev("document.querySelector('.ref-list tr').click(); 1")
            p.settle()
            self.assertFalse(p.ev(btn + ".disabled"))
            p.ev(btn + ".click(); 1")
            p.wait("!!document.querySelector('.modal.input-win input')", "基準の値の窓")
            self.assertEqual(p.ev("document.querySelector('.modal.input-win input').value"), "−40.0")
            p.ev("(() => { const e = document.querySelector('.modal.input-win input'); e.value = '+3';"
                 " e.dispatchEvent(new Event('input')); return 1; })()")
            p.ev("document.querySelector('.modal.input-win button').click(); 1")
            p.wait("gsApp.st.normalization.selected.value_db === 3", "直した値")
            p.wait("document.querySelector('.ref-list td').textContent.trim().endsWith('+3.0 dB')", "一覧の行")
            # S11 では欄を使えなくする（全反射 = 0dB）
            a.call("set_mode", mode="s11")
            p.wait(inp + ".disabled", "S11 では使えない")
            self.assertIn(qt_texts.REASON_TEXTS["ref_value_s11"],
                          p.ev("document.querySelector('[data-field=\"ref_value\"]').title"))
            self.assertEqual(p.errors, [])
        finally:
            a.call("set_mode", mode="sa")


if __name__ == "__main__":
    unittest.main()
