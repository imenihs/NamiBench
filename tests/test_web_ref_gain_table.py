"""Web: 基準と PlutoSDR の受信の利得の補正の表が違うときの理由（設計Q §7.6）。Qt の test_ui_qt_ref_gain_table と同じ。

- 語の表（NORM_REASONS）が Web へ届く（Qt と同じ文）
- 状態に付けて送る画面の中身（状態バーの基準の項目・信号源のタブの基準の文・上段の基準の文のツールチップ）と、電力に
  戻ったときの知らせの文に、RBW・イメージ除去の違いと同じ場所で理由が出る
- ヘッドレスの Edge で、状態バーと信号源のタブの基準の文に出る（Edge が無いときは飛ばす）
"""
from __future__ import annotations

import json
import time
import unittest

from namibench.api.web import server as web
from namibench.api.web import view
from namibench.ui_qt import texts as qt_texts
from namibench.core.devices.plutosdr.fake import FAKE_URI
from tests.test_ref_gain_table import A, table
from tests.test_web_layout import App, EdgePage, edge_available, open_app
from tests import test_web_pluto_gain as twp

REASON = qt_texts.NORM_REASONS["gain_correction_mismatch"]


def take_and_change_table(call, inst) -> None:
    """TG で表なしの基準を取り、1回当ててから表を入れて、もう1回掃引する。"""
    def sweep():
        n0 = call("get_state")["run"]["sweep_count"]
        call("run", mode="single")
        end = time.monotonic() + 30
        while True:
            st = call("get_state")
            if st["run"]["state"] == "stopped" and st["run"]["sweep_count"] > n0:
                return st
            assert time.monotonic() < end, "掃引が終わらない"
            time.sleep(0.05)

    call("set_mode", mode="tg")
    call("plutosdr_set", tx_atten_db=-10.0)
    call("reference_take", kind="fine")
    end = time.monotonic() + 30
    while True:
        st = call("get_state")
        if not st["tasks"] and (st["normalization"] or {}).get("selected"):
            break
        assert time.monotonic() < end, "基準が取れない"
        time.sleep(0.05)
    assert sweep()["normalization"]["applied"]
    inst._pluto_gain_cal = table(A)
    st = sweep()
    assert st["normalization"]["reason"] == "gain_correction_mismatch", st["normalization"]


class TextsTest(unittest.TestCase):
    def test_reason_reaches_the_web(self):
        tj = web.texts_json()
        self.assertEqual(tj["NORM_REASONS"]["gain_correction_mismatch"], REASON)

    def test_power_notice_same_as_qt(self):
        n = {"code": "power_display", "detail": {"reason": "gain_correction_mismatch", "mode": "tg", "unit": "dBm"}}
        v = view.notice_view(n, {})["view"]
        self.assertEqual(v, qt_texts.notice_view(n, {}))
        self.assertIn(REASON, v["text"])
        self.assertIn(REASON, v["modal"]["text"])


class StateTest(unittest.TestCase):
    setUp = twp.PlutoGainServerTest.setUp
    tearDown = twp.PlutoGainServerTest.tearDown
    post = twp.PlutoGainServerTest.post
    get = twp.PlutoGainServerTest.get
    state = twp.PlutoGainServerTest.state

    def test_reason_in_the_same_places(self):
        self.assertEqual(self.post("connect", {"backend": "plutosdr", "port": FAKE_URI})[0], 200)
        self.post("set_sweep", {"center_hz": 100_000_000, "span_hz": 1_000_000})

        def call(name, **kw):
            if name == "get_state":
                return self.state()
            st, r = self.post(name, kw)
            self.assertEqual(st, 200, r)
            return r

        take_and_change_table(call, self.api._inst)
        ui = self.state()["ui"]
        norm = [i for i in ui["status"] if i["key"] == "norm"][0]
        self.assertIn(REASON, norm["text"])
        self.assertEqual(norm["cls"], "warn")
        self.assertIn(REASON, ui["ref_state"])
        self.assertIn(REASON, ui["ref_quick"]["tip"])
        self.assertIn(REASON, ui["normalization"])


@unittest.skipUnless(edge_available(), "Microsoft Edge が無い")
class BrowserTest(unittest.TestCase):
    def test_reason_on_screen(self):
        app = App(lan=False, pluto=True)
        try:
            take_and_change_table(lambda name, **kw: app.call(name, **kw), app.api._inst)
            page = EdgePage()
            try:
                open_app(page, app, 1280, 720)
                page.ev("gsApp.tab = 'source'; 1")
                page.settle()
                page.wait(f"[...document.querySelectorAll('.statusbar span')].some((s) => s.textContent.includes("
                          f"{json.dumps(REASON)}))", "状態バーの基準の項目")
                self.assertIn(REASON, page.ev("document.querySelector('.ref-state').textContent"))
                self.assertEqual(page.errors, [])
            finally:
                page.close()
        finally:
            app.close()


if __name__ == "__main__":
    unittest.main()
