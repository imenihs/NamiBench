"""Qt の画面: 機器の範囲の外・意味の無い区間の帯と、REF の自動（設計J §4.8・§4.1）。シミュレータの本体で通す。"""
import unittest

from namibench.core import display_marks as dm
from namibench.ui_qt import png_data, texts
from tests.test_ui_qt_support import UiBase

MHZ = 1_000_000


class MarksTest(UiBase):
    size = (1280, 720)

    def single(self, mode, start=-100 * MHZ, stop=100 * MHZ):
        self.cmd("connect", backend="simulator", serial="SIM00001")
        self.cmd("set_mode", mode=mode)
        self.cmd("set_sweep", start_hz=start, stop_hz=stop)
        n0 = self.state()["run"]["sweep_count"]
        self.cmd("run", mode="single")
        self.wait(lambda: self.state()["run"]["state"] == "stopped" and self.state()["run"]["sweep_count"] > n0,
                  what="単発の掃引")
        self.wait(lambda: (self.win.state.get("run") or {}).get("sweep_count", 0) > n0
                  and self.win.traces.live_payload(1) is not None, what="画面に届く")
        self.pump(0.3)

    def band_texts(self):
        return [(lb.textItem.toPlainText(), lb.isVisible()) for lb in self.win.mark_bands.labels()]

    def test_sa_bands_and_auto_ignores_lo_leak(self):
        self.single("sa")
        self.assertEqual(self.band_texts(), [(texts.DISPLAY_MARKS["negative_mirror"], True),
                                             (texts.DISPLAY_MARKS["lo_leak"], True)])
        regions = [r for r, _lb, _m in self.win.mark_bands.items]
        self.assertEqual(regions[1].getRegion(), (-5 * MHZ, 5 * MHZ))
        self.assertIn(texts.DISPLAY_MARKS["lo_leak"], regions[1].toolTip())
        # 局発の漏れの帯（±5MHz）は狭いので説明は縦に出る
        self.assertEqual(self.win.mark_bands.items[1][1].angle, -90)
        self.assertEqual(self.win.mark_bands.items[0][1].angle, 0)
        self.assertTrue(self.win.btn_ref_auto.isEnabled())
        self.win.btn_ref_auto.click()
        peak = self.win.state["ref_auto"]["peaks"]["1"]["max"]
        self.assertAlmostEqual(self.win._ref_dbm, dm.auto_ref_level([peak], 10), places=6)
        # 0Hz の漏れ（シミュレータは +1.9dBm）なら REF は約 +12dBm になる。漏れを使っていない
        self.assertLess(self.win._ref_dbm, -10.0)
        # PNG の帯の注意にも出る（画面に出ている帯と同じ文）
        warns = "\n".join(png_data.build(self.win)["warnings"])
        self.assertIn(texts.DISPLAY_MARKS["lo_leak"], warns)
        self.assertIn(texts.DISPLAY_MARKS["negative_mirror"], warns)

    def test_tg_band_and_no_band_inside_range(self):
        self.single("tg")
        self.assertEqual(self.band_texts(), [(texts.DISPLAY_MARKS["outside_tg_range"], True)])
        self.assertEqual(self.win.mark_bands.items[0][1].angle, 0)  # 半分を覆う帯は横書き
        self.single("tg", 10 * MHZ, 200 * MHZ)
        self.assertEqual(self.band_texts(), [])

    def test_auto_disabled_with_reason_when_nothing_usable(self):
        # 表示が局発の漏れの帯の中だけ: 使える点が無い
        self.single("sa", -2 * MHZ, 2 * MHZ)
        self.assertFalse(self.win.btn_ref_auto.isEnabled())
        self.assertIn(texts.REASON_TEXTS["no_auto_points"], self.win.btn_ref_auto.toolTip())


if __name__ == "__main__":
    unittest.main()
