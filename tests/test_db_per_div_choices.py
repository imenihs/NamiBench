"""dB/div の選択肢（設計J §4.1。20・40 dB/div を足した: 2026-10-11 ユーザー指摘「10dB/div では足りない」）。

- コアが受け付ける並び・Qt と Web の欄の並び（screen_s11.axis_view が配る）が同じ1つの表から来ること
- 20・40 dB/div でも REF の吸い付き・REF の自動・PNG の縦軸の数字・元に戻すが成り立つこと
- |Γ|・SWR の縦軸の1目盛の並びは変えないこと
"""
import re
import time
import unittest
from pathlib import Path

from namibench.core.display_marks import auto_ref_level
from namibench.core.instrument import CommandError
from namibench.core.instrument_display import DB_PER_DIV_CHOICES
from namibench.core.report_layout import y_labels
from namibench.core.trace import formats
from namibench.core.units import drag_ref_level
from namibench.ui_qt import screen_s11
from tests.instrument_helpers import InstrumentCase

UI_WEB = Path(__file__).resolve().parent.parent / "namibench" / "ui_web"


class ChoicesTest(unittest.TestCase):
    def test_one_list(self):
        self.assertEqual(DB_PER_DIV_CHOICES, (1, 2, 5, 10, 20, 40))
        ax = screen_s11.axis_view({"amplitude": {}}, "dBm")
        self.assertEqual([c["value"] for c in ax["div_choices"]], list(DB_PER_DIV_CHOICES))
        self.assertEqual([c["text"] for c in ax["div_choices"]][-2:], ["20 dB", "40 dB"])
        self.assertIn("40 dB", screen_s11.div_width_samples())
        # |Γ|・SWR の1目盛の並びは別（設計R §3）
        for unit, spec in formats.LINEAR_AXES.items():
            ax = screen_s11.axis_view({"amplitude": {}}, unit)
            self.assertEqual([c["value"] for c in ax["div_choices"]], [float(v) for v in spec["choices"]], unit)

    def test_web_has_no_own_list(self):
        app = (UI_WEB / "app.js").read_text(encoding="utf-8")
        self.assertNotIn("DB_PER_DIV_CHOICES", app)  # Ctrl+ホイールもサーバーの ui.axis.div_choices を送る
        s11 = (UI_WEB / "app_s11.js").read_text(encoding="utf-8")
        m = re.search(r"div_choices: \[([\d, ]+)\]\.map", s11)  # 状態が届く前の既定
        self.assertEqual(tuple(int(x) for x in m.group(1).split(",")), DB_PER_DIV_CHOICES)

    def test_rules_with_wide_divisions(self):
        # REF の吸い付き: dB/div の倍数の ±0.15 目盛（40dB/div なら ±6dB）
        self.assertEqual(drag_ref_level(0.0, -0.1, 40), -40.0)
        self.assertEqual(drag_ref_level(-3.0, -0.1, 40), -40.0)     # −43 は −40 から 3dB → 吸い付く
        self.assertEqual(drag_ref_level(-10.0, -0.1, 40), -50.0)    # −50 は −40 から 10dB → そのまま
        self.assertEqual(drag_ref_level(0.0, -0.05, 20), -10.0)     # 半目盛は吸い付かない
        # REF の自動: 最大値が上から1目盛下（0.1dB 単位で切り上げ）
        self.assertEqual(auto_ref_level([-37.25], 40), 2.8)
        self.assertEqual(auto_ref_level([-37.25], 20), -17.2)
        # PNG・報告の縦軸の数字（REF から 10 目盛）
        self.assertEqual(y_labels(0.0, 40.0, "dBm")[-1], "−400.0")
        self.assertEqual(y_labels(10.0, 20.0, "dBm")[:3], ["10.0", "−10.0", "−30.0"])


class AmplitudeCommandTest(InstrumentCase):
    def test_set_preset_and_undo(self):
        self.connect()
        for v in (20, 40):
            self.call("set_amplitude", db_per_div=v)
            self.assertEqual(self.call("get_state")["amplitude"]["db_per_div"], v)
        with self.assertRaises(CommandError):
            self.call("set_amplitude", db_per_div=30)
        self.assertEqual(self.call("get_state")["amplitude"]["db_per_div"], 40)
        # プリセットは 40dB/div を持って戻し、呼び出しは Ctrl+Z で戻せる
        pid = self.call("preset_save", name="wide")["id"]
        self.call("set_amplitude", db_per_div=10)
        self.call("preset_load", id=pid)
        self.assertEqual(self.call("get_state")["amplitude"]["db_per_div"], 40)
        self.call("undo")
        self.assertEqual(self.call("get_state")["amplitude"]["db_per_div"], 10)


def _edge_ok() -> bool:
    try:
        from tests.test_web_layout import edge_available
    except Exception:  # noqa: BLE001
        return False
    return edge_available()


@unittest.skipUnless(_edge_ok(), "Microsoft Edge が無い")
class WebDivBrowserTest(unittest.TestCase):
    """ヘッドレスの Edge で、上段の dB/div の一覧と Ctrl+ホイールが 20・40 に届くこと（Qt の test_ui_qt_graph と同じ）。"""

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

    def div(self):
        return self.app.call("get_state")["amplitude"]["db_per_div"]

    def wait_div(self, want):
        end = time.monotonic() + 15
        while self.div() != want:
            self.assertLess(time.monotonic(), end, f"{want} dB/div")
            time.sleep(0.05)

    def test_list_and_ctrl_wheel(self):
        from tests.test_web_layout import open_app

        a, p = self.app, self.page
        a.call("set_amplitude", ref_level=0.0, db_per_div=10)
        open_app(p, a, 1280, 720)
        sel = "document.querySelector('[data-field=db_per_div]')"
        self.assertEqual(p.ev(f"[...{sel}.options].map((o) => o.textContent.trim())"),
                         ["1 dB", "2 dB", "5 dB", "10 dB", "20 dB", "40 dB"])
        for want in (20, 40, 40):
            p.ev("gsApp.onLevelWheel(1, true); 1")
            self.wait_div(want)
            p.wait(f"gsApp.amp.div === {want}", "画面の dB/div")
        self.assertEqual(p.ev("gsApp.amp.ref"), 0)  # REF は動かない（J §4.1）
        self.assertEqual(p.ev(f"{sel}.value"), "40")
        p.ev("gsApp.onLevelWheel(-1, true); 1")
        self.wait_div(20)
        self.assertEqual(p.errors, [])


if __name__ == "__main__":
    unittest.main()
