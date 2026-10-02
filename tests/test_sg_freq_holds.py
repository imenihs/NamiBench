"""SG の周波数を変えたときの蓄積（設計F §8）の試験。

- トレース: 条件の変更 SG_FREQ と、最大・最小保持を残す決まり（HoldKeep）
- 本体: 手で SG の周波数を変えたら平均は捨てる。最大・最小保持は選択 sg_clears_holds に従う（初期値: 捨てる）。
  「N 回で止める」保持は選択によらず捨てる。SG のステップ掃引の口（step_sweep）は保持を全部残す。
  SG を出さないモードでは SG の周波数で何も捨てない。選択は前回終了時の状態に入る
- 捨てたことの知らせは理由（SG の周波数）を言う
"""
import threading
import unittest

import numpy as np

from pygigast.core.trace import (
    NOTICE_RESET_BY_CONDITION,
    ConditionChange,
    HoldAfterCount,
    HoldKeep,
    Trace,
    TraceAction,
    TraceSet,
)
from pygigast.core.types import Frame, FreqAxis, Quantity, SweepRequest, ValueKind
from pygigast.ui_qt import texts
from tests.instrument_helpers import InstrumentCase

MHZ = 1_000_000
AXIS = FreqAxis(100 * MHZ, 1 * MHZ, 4)


def frame(values):
    v = np.asarray(values, dtype=float)
    return Frame(axis=AXIS, values=v, quantity=Quantity.POWER, value_kind=ValueKind.REAL, time=0.0, duration_s=0.1,
                 sweep_no=0, display_start_hz=AXIS.start_hz, display_stop_hz=AXIS.last_hz, flags={}, profile={})


class HoldKeepTest(unittest.TestCase):
    """トレースの決まり: 平均はどれでも捨てる。保持は HoldKeep で残す（N 回で止める保持は UNLIMITED では捨てる）。"""

    def traces(self):
        s = TraceSet()
        s[1].configure(action="average", count=4)
        s[2].configure(action="max_hold")
        s[3].configure(action="min_hold", count=8)  # 回数あり・保持し続ける
        s[4].configure(action="max_hold", count=2, hold_after="stop")  # N 回で止める
        s.apply(frame([1.0, 2, 3, 4]))
        return s

    def test_discard_is_default(self):
        s = self.traces()
        self.assertEqual(s.on_condition_change(ConditionChange.SG_FREQ),
                         {n: NOTICE_RESET_BY_CONDITION for n in (1, 2, 3, 4)})

    def test_unlimited_keeps_holds_that_do_not_stop(self):
        s = self.traces()
        self.assertEqual(s.on_condition_change(ConditionChange.SG_FREQ, HoldKeep.UNLIMITED),
                         {1: NOTICE_RESET_BY_CONDITION, 4: NOTICE_RESET_BY_CONDITION})
        self.assertEqual((s[2].current_count, s[3].current_count), (1, 1))
        np.testing.assert_array_equal(s[2].values, [1, 2, 3, 4])
        self.assertIsNone(s[4].values)

    def test_all_keeps_every_hold(self):
        s = self.traces()
        self.assertEqual(s.on_condition_change(ConditionChange.SG_FREQ, HoldKeep.ALL), {1: NOTICE_RESET_BY_CONDITION})
        self.assertEqual([s[n].current_count for n in (2, 3, 4)], [1, 1, 1])

    def test_holds_keep_accumulating_after_kept(self):
        # 残した最大保持は、新しい周波数の掃引を前の分に重ねる（F §8: 消さない選択）
        t = Trace(1, TraceAction.MAX_HOLD)
        t.apply(frame([5.0, 1, 1, 1]))
        self.assertIsNone(t.on_condition_change("sg_freq", "unlimited"))
        t.apply(frame([1.0, 1, 7, 1]))
        np.testing.assert_array_equal(t.values, [5, 1, 7, 1])
        self.assertEqual(t.hold_after, HoldAfterCount.CONTINUE)


class InstrumentSgFreqTest(InstrumentCase):
    SG1 = 500 * MHZ
    SG2 = 600 * MHZ

    def setUp(self):
        super().setUp()
        self.connect(400 * MHZ, 700 * MHZ)
        self.call("set_mode", mode="sg", sg_hz=self.SG1)
        self.call("set_trace", number=1, action="average", count=3)
        self.call("set_trace", number=2, action="max_hold")
        self.call("set_trace", number=3, action="min_hold")
        self.call("set_trace", number=4, action="max_hold", count=2, hold_after="stop")
        self.single()  # 回数の最大（3回）

    def counts(self):
        return {t["number"]: (t["current_count"], t["has_values"]) for t in self.call("get_state")["traces"]}

    def reset_notice(self):
        return self.wait_for(lambda k, p: k == "notice" and p["code"] == "trace_reset")["detail"]

    def on_control(self, fn):
        """制御スレッドで fn を呼ぶ（本体の状態は制御スレッドだけが触る: 設計A §4.2）。"""
        done = threading.Event()
        self.inst._q.put(("call", lambda: (fn(), done.set())))
        self.assertTrue(done.wait(10))

    def test_manual_change_clears_all_by_default(self):
        self.assertIs(self.call("get_state")["trace_options"]["sg_clears_holds"], True)
        self.assertEqual(self.counts()[2], (3, True))
        self.clear_events()
        self.call("set_mode", mode="sg", sg_hz=self.SG2)
        d = self.reset_notice()
        self.assertEqual((d["traces"], d["cause"]), ([1, 2, 3, 4], "sg_freq"))
        c = self.counts()
        self.assertEqual([c[n] for n in (1, 2, 3, 4)], [(0, False)] * 4)

    def test_manual_change_keeps_holds_when_option_off(self):
        r = self.call("set_trace_options", sg_clears_holds=False)
        self.assertEqual(r, {"trace_options": {"sg_clears_holds": False}})
        self.clear_events()
        self.call("set_mode", mode="sg", sg_hz=self.SG2)
        d = self.reset_notice()
        # 平均と「N 回で止める」保持だけ捨てる
        self.assertEqual(d["traces"], [1, 4])
        c = self.counts()
        self.assertEqual((c[1], c[4]), ((0, False), (0, False)))
        self.assertEqual((c[2], c[3]), ((3, True), (3, True)))

    def test_sg_field_in_other_mode_discards_nothing(self):
        # SG を出さないモード（SA）の SG の周波数は測る値を変えない
        self.call("set_mode", mode="sa")
        self.single()
        before = self.counts()
        self.clear_events()
        self.call("set_mode", mode="sa", sg_hz=self.SG2)
        self.assertEqual(self.counts(), before)
        self.assertFalse([p for k, p in self.events if k == "notice" and p["code"] == "trace_reset"])

    def test_step_sweep_path_keeps_holds(self):
        # ステップ掃引（設計L。まだ無い）が周波数を動かす口: 選択（初期値: 消す）によらず保持を残し、平均は捨てる
        self.clear_events()

        def step():
            new = self.inst._model.apply(SweepRequest(sg_hz=self.SG2), self.inst._adopted)
            self.inst._set_adopted(new, step_sweep=True)

        self.on_control(step)
        d = self.reset_notice()
        self.assertEqual((d["traces"], d["cause"]), ([1], "sg_freq"))
        c = self.counts()
        self.assertEqual([c[n] for n in (2, 3, 4)], [(3, True), (3, True), (2, True)])
        self.assertEqual(self.call("get_state")["sweep"]["sg_hz"], self.SG2)

    def test_option_is_saved_and_checked(self):
        with self.assertRaises(Exception) as cm:
            self.call("set_trace_options", sg_clears_holds="no")
        self.assertEqual(cm.exception.code, "bad_value")
        self.call("set_trace_options", sg_clears_holds=False)
        self.inst.shutdown()
        self.inst = self.make_instrument()
        self.assertIs(self.call("get_state")["trace_options"]["sg_clears_holds"], False)

    def test_reset_notice_says_why(self):
        n = {"code": "trace_reset", "detail": {"traces": [1, 2], "cause": "sg_freq"}}
        v = texts.notice_view(n)
        self.assertIn(texts.RESET_CAUSES["sg_freq"], v["text"])
        self.assertEqual(v["traces"]["1"], texts.trace_reset_text({"cause": "sg_freq"}))


if __name__ == "__main__":
    unittest.main()
