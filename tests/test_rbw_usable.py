"""RBW の欄は実際に使う RBW を出し、いまの条件で使えない RBW は理由付きで選べなくする（設計C §5.14）。"""
from __future__ import annotations

import unittest

from namibench.core.devices.base import load_profile
from namibench.core.devices.gigast.driver import timing_fn
from namibench.core.devices.rtlsdr import driver as rtl
from namibench.core.instrument import GIGAST_PROFILE_PATH
from namibench.core.sweep.model import SweepModel
from namibench.core.types import Mode, SweepRequest
from namibench.ui_qt import screen_model
from tests.test_ui_qt_support import UiBase

MHZ = 1_000_000


class GigaStModelTest(unittest.TestCase):
    def setUp(self):
        p = load_profile(GIGAST_PROFILE_PATH)
        self.m = SweepModel(p, timing_fn(p))
        self.cur = self.m.default()

    def at(self, center, span, current=None, **kw):
        return self.m.apply(SweepRequest(start_hz=int(center - span // 2), stop_hz=int(center + span // 2),
                                         edited=("start_hz", "stop_hz"), **kw), current or self.cur)

    def test_sa_narrow_all_usable(self):
        a = self.at(500 * MHZ, 10 * MHZ, rbw_preference_hz=30_000)
        self.assertEqual((a.rbw_hz, a.rbw_limit), (30_000, None))
        self.assertEqual(set(a.rbw_usable_hz), {180_000, 30_000, 15_000})

    def test_tg_only_180k(self):
        a = self.at(500 * MHZ, 10 * MHZ, rbw_preference_hz=30_000, mode=Mode.TG)
        self.assertEqual((a.rbw_hz, a.rbw_preference_hz, a.rbw_usable_hz, a.rbw_limit),
                         (180_000, 30_000, (180_000,), "mode_fixed"))

    def test_span_boundary(self):
        # Span 50MHz ちょうどまでは選べ、超えると 180kHz だけ（境界 N-1 / N / N+1）
        for span, limit in ((50 * MHZ - 200, None), (50 * MHZ, None), (50 * MHZ + 200, "span_wide")):
            a = self.at(500 * MHZ, span, rbw_preference_hz=30_000)
            self.assertEqual(a.rbw_limit, limit, span)

    def test_restored_notice_when_leaving_fixed(self):
        sa = self.at(500 * MHZ, 10 * MHZ, rbw_preference_hz=30_000)
        tg = self.m.apply(SweepRequest(mode=Mode.TG), sa)
        self.assertEqual(tg.rbw_hz, 180_000)
        back = self.m.apply(SweepRequest(mode=Mode.SA), tg)
        self.assertEqual(back.rbw_hz, 30_000)
        self.assertIn(("rbw_hz", "rbw_restored"), {(x.field, x.reason) for x in back.adjustments})
        # 起動の初期値では「戻しました」を出さない
        self.assertNotIn("rbw_restored", {x.reason for x in self.cur.adjustments})


class FftModelTest(unittest.TestCase):
    def test_points_max_disables_narrow_rbw(self):
        p = rtl.default_profile()
        m = SweepModel(p, rtl.timing_fn(p))
        wide = m.apply(SweepRequest(start_hz=24 * MHZ, stop_hz=1766 * MHZ, edited=("start_hz", "stop_hz")), m.default())
        self.assertEqual(wide.rbw_limit, "points_max")
        self.assertIn(wide.rbw_hz, wide.rbw_usable_hz)
        self.assertLess(len(wide.rbw_usable_hz), len(m.default().rbw_usable_hz) or 99)
        narrow = m.apply(SweepRequest(start_hz=88 * MHZ, stop_hz=90 * MHZ, edited=("start_hz", "stop_hz")), m.default())
        self.assertIsNone(narrow.rbw_limit)


class ScreenModelTest(unittest.TestCase):
    def test_reason_on_unusable_choices_only(self):
        st = {"connection": {"profile": load_profile(GIGAST_PROFILE_PATH)},
              "sweep": {"rbw_hz": 180_000, "rbw_preference_hz": 30_000, "rbw_usable_hz": [180_000], "rbw_limit": "mode_fixed"}}
        groups = screen_model.rbw_groups(st)
        ch = {c["hz"]: c["reason"] for g in groups for c in g["choices"]}
        if not ch:
            self.skipTest("この状態の形では機器のプロファイルが引けない")
        self.assertIsNone(ch[180_000])
        self.assertIn("TG", ch[30_000])
        self.assertIn("180kHz", ch[30_000].replace(" ", ""))


class QtRbwFieldTest(UiBase):
    def test_tg_shows_180k_and_disables_others(self):
        self.connect_and_sweep(start=495 * MHZ, stop=505 * MHZ)
        self.cmd("set_sweep", rbw_hz=30_000)
        self.pump(0.3)
        combo = self.win.rbw_combo
        self.assertEqual(combo.currentData(), 30_000)
        self.cmd("set_mode", mode="tg")
        self.pump(0.5)
        self.assertEqual(combo.currentData(), 180_000)  # 欄は実際に使う RBW
        i = combo.findData(30_000)
        item = combo.model().item(i)
        self.assertFalse(item.isEnabled())
        self.assertIn("TG", item.toolTip())
        self.assertIn("TG", combo.toolTip())
        # 上段の下に「180kHz で測っています」を出し続けない
        from namibench.ui_qt import texts
        self.assertFalse(any("で測っています" in line for line in texts.condition_lines(self.state())))
        self.cmd("set_mode", mode="sa")
        self.pump(0.5)
        self.assertEqual(combo.currentData(), 30_000)  # 条件から出たら好みに戻る
        self.assertTrue(combo.model().item(combo.findData(30_000)).isEnabled())


if __name__ == "__main__":
    unittest.main()
