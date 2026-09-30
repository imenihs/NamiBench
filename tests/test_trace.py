"""トレース（設計F §5.2〜§5.5）の試験。"""
from __future__ import annotations

import unittest

import numpy as np

from pygigast.core.trace import (
    DIFF_NO_OVERLAP,
    DIFF_QUANTITY_MISMATCH,
    NOTICE_AVERAGE_TYPE_RESET,
    NOTICE_LOG_AVERAGE_READS_LOW,
    NOTICE_RESET_BY_CONDITION,
    NOTICE_RESET_BY_FRAME_MISMATCH,
    AverageAfterCount,
    AverageType,
    ConditionChange,
    DisplayChange,
    HoldAfterCount,
    Trace,
    TraceAction,
    TraceSet,
    allowed_average_types,
    decimate,
    default_average_type,
    difference,
    to_db,
)
from pygigast.core.types import Frame, FreqAxis, Quantity, ValueKind

MHZ = 1_000_000
AXIS = FreqAxis(100 * MHZ, 1 * MHZ, 4)


def frame(values, quantity=Quantity.POWER, axis=AXIS, time=0.0, flags=None, profile=None):
    v = np.asarray(values)
    kind = ValueKind.COMPLEX if np.iscomplexobj(v) else ValueKind.REAL
    return Frame(
        axis=axis, values=v, quantity=quantity, value_kind=kind, time=time, duration_s=0.1,
        sweep_no=0, display_start_hz=axis.start_hz, display_stop_hz=axis.last_hz,
        flags=flags or {}, profile=profile or {},
    )


class TestActions(unittest.TestCase):
    """設計F §5.2 の表の各行。"""

    def test_update_replaces(self):
        t = Trace(1)
        t.apply(frame([1.0, 2, 3, 4]))
        u = t.apply(frame([4.0, 3, 2, 1], time=5.0))
        self.assertTrue(u.changed)
        np.testing.assert_array_equal(t.values, [4, 3, 2, 1])
        self.assertEqual(t.last_update_time, 5.0)

    def test_max_hold_real(self):
        t = Trace(1, TraceAction.MAX_HOLD)
        t.apply(frame([1.0, 5, 3, 4]))
        t.apply(frame([2.0, 1, 6, 4]))
        np.testing.assert_array_equal(t.values, [2, 5, 6, 4])

    def test_max_hold_complex_keeps_complex_of_larger_magnitude(self):
        t = Trace(1, TraceAction.MAX_HOLD)
        t.apply(frame(np.array([1 + 0j, 0 + 2j, 3, 0.1]), Quantity.RATIO))
        t.apply(frame(np.array([0 - 2j, 1 + 1j, -4, 0.1j]), Quantity.RATIO))
        np.testing.assert_array_equal(t.values, [0 - 2j, 0 + 2j, -4, 0.1])
        self.assertEqual(t.values.dtype, np.complex128)

    def test_min_hold(self):
        t = Trace(1, TraceAction.MIN_HOLD)
        t.apply(frame([1.0, 5, 3, 4]))
        t.apply(frame([2.0, 1, 6, 4]))
        np.testing.assert_array_equal(t.values, [1, 1, 3, 4])

    def test_min_hold_complex_by_magnitude(self):
        t = Trace(1, TraceAction.MIN_HOLD)
        t.apply(frame(np.array([2j, 1, 1, 1]), Quantity.RATIO))
        t.apply(frame(np.array([-1, 1j, 3, 1]), Quantity.RATIO))
        np.testing.assert_array_equal(t.values, [-1, 1, 1, 1])

    def test_average_power_default_cumulative(self):
        t = Trace(1, TraceAction.AVERAGE, count=4)
        t.apply(frame([1.0, 1, 1, 1]))
        u = t.apply(frame([3.0, 3, 3, 3]))
        np.testing.assert_allclose(t.values, [2, 2, 2, 2])
        self.assertEqual((u.count, u.count_target, u.count_reached), (2, 4, False))

    def test_fixed_does_not_update(self):
        t = Trace(1)
        t.apply(frame([1.0, 2, 3, 4]))
        t.configure(action=TraceAction.FIXED)
        np.testing.assert_array_equal(t.values, [1, 2, 3, 4])  # 固定に変えても値を残す
        u = t.apply(frame([9.0, 9, 9, 9]))
        self.assertFalse(u.changed)
        np.testing.assert_array_equal(t.values, [1, 2, 3, 4])

    def test_hidden_still_processes(self):
        t = Trace(2, TraceAction.MAX_HOLD, visible=False)
        t.apply(frame([1.0, 5, 3, 4]))
        t.apply(frame([2.0, 1, 6, 4]))
        np.testing.assert_array_equal(t.values, [2, 5, 6, 4])
        t.configure(visible=True)  # 表示の切り替えは何も捨てない
        np.testing.assert_array_equal(t.values, [2, 5, 6, 4])
        self.assertEqual(t.current_count, 2)

    def test_flags_and_profile_carried(self):
        t = Trace(1, TraceAction.MAX_HOLD)
        prof = {"model": "x"}
        t.apply(frame([1.0, 1, 1, 1], flags={"transient": np.array([True, False, False, False])}, profile=prof))
        t.apply(frame([1.0, 1, 1, 1], flags={"out_of_cal_range": np.array([False, False, False, True])}))
        np.testing.assert_array_equal(t.flags["transient"], [True, False, False, False])
        np.testing.assert_array_equal(t.flags["out_of_cal_range"], [False, False, False, True])
        prof["model"] = "changed"
        self.assertEqual(t.profile, {})  # 最後の掃引の写し（元の dict を触っても変わらない）

    def test_number_range(self):
        for n in (0, 5):
            with self.assertRaises(ValueError):
                Trace(n)
        Trace(1), Trace(4)

    def test_average_requires_count(self):
        with self.assertRaises(ValueError):
            Trace(1, TraceAction.AVERAGE)
        t = Trace(1)
        with self.assertRaises(ValueError):
            t.configure(action=TraceAction.AVERAGE)
        self.assertEqual(t.action, TraceAction.UPDATE)  # 失敗したら元のまま


class TestAverageTypes(unittest.TestCase):
    """設計F §5.2.1 の平均の種類。"""

    def test_defaults(self):
        self.assertEqual(default_average_type(Quantity.POWER, ValueKind.REAL), AverageType.POWER)
        self.assertEqual(default_average_type(Quantity.RATIO, ValueKind.REAL), AverageType.VOLTAGE)
        self.assertEqual(default_average_type(Quantity.RATIO, ValueKind.COMPLEX), AverageType.VECTOR)

    def test_allowed(self):
        self.assertEqual(allowed_average_types(ValueKind.COMPLEX), (AverageType.VECTOR,))
        self.assertNotIn(AverageType.VECTOR, allowed_average_types(ValueKind.REAL))

    def _avg(self, kind, values_list, quantity=Quantity.POWER, n=8):
        t = Trace(1, TraceAction.AVERAGE, count=n, average_type=kind)
        for v in values_list:
            t.apply(frame(v, quantity))
        return t

    def test_power_average_w(self):
        t = self._avg(None, [[1.0] * 4, [4.0] * 4])
        np.testing.assert_allclose(t.values, 2.5)
        self.assertEqual(t.effective_average_type(), AverageType.POWER)

    def test_voltage_average_power(self):
        t = self._avg(AverageType.VOLTAGE, [[1.0] * 4, [4.0] * 4])
        np.testing.assert_allclose(t.values, 2.25)  # ((1+2)/2)^2

    def test_log_average_power(self):
        t = self._avg(AverageType.LOG, [[1.0] * 4, [100.0] * 4])
        np.testing.assert_allclose(t.values, 10.0)  # 0dB と 20dB の平均 10dB

    def test_ratio_real_default_voltage(self):
        t = self._avg(None, [[1.0] * 4, [3.0] * 4], Quantity.RATIO)
        np.testing.assert_allclose(t.values, 2.0)
        self.assertEqual(t.effective_average_type(), AverageType.VOLTAGE)

    def test_ratio_real_power_average(self):
        t = self._avg(AverageType.POWER, [[1.0] * 4, np.sqrt([7.0] * 4)], Quantity.RATIO)
        np.testing.assert_allclose(t.values, 2.0)  # sqrt((1+7)/2)

    def test_ratio_real_log_average(self):
        t = self._avg(AverageType.LOG, [[1.0] * 4, [100.0] * 4], Quantity.RATIO)
        np.testing.assert_allclose(t.values, 10.0)  # 0dB と 40dB の平均 20dB（振幅 10）

    def test_vector_average_cancels_noise(self):
        t = self._avg(None, [np.array([1 + 1j] * 4), np.array([1 - 1j] * 4)], Quantity.RATIO)
        np.testing.assert_allclose(t.values, 1 + 0j)
        self.assertEqual(t.effective_average_type(), AverageType.VECTOR)

    def test_log_selection_gives_notice(self):
        t = Trace(1, TraceAction.AVERAGE, count=4)
        self.assertEqual(t.configure(average_type=AverageType.LOG), (NOTICE_LOG_AVERAGE_READS_LOW,))
        self.assertEqual(t.configure(count=8), ())  # 対数平均のまま他を変えても繰り返さない
        self.assertEqual(t.configure(average_type=AverageType.POWER), ())

    def test_vector_not_selectable_on_real(self):
        t = Trace(1, TraceAction.AVERAGE, count=4)
        t.apply(frame([1.0] * 4))
        with self.assertRaises(ValueError):
            t.configure(average_type=AverageType.VECTOR)
        self.assertIsNone(t.average_type)

    def test_type_incompatible_with_frame_falls_back(self):
        t = Trace(1, TraceAction.AVERAGE, count=4, average_type=AverageType.LOG)
        u = t.apply(frame(np.array([1j] * 4), Quantity.RATIO))
        self.assertIn(NOTICE_AVERAGE_TYPE_RESET, u.notices)
        self.assertEqual(t.effective_average_type(), AverageType.VECTOR)

    def test_log_average_with_zero_stays_finite(self):
        t = Trace(1, TraceAction.AVERAGE, count=2, average_type=AverageType.LOG)
        for v in ([0.0, 1, 1, 1], [1.0, 1, 1, 1], [1.0, 1, 1, 1]):
            t.apply(frame(v))
        self.assertFalse(np.any(np.isnan(t.values)))


class TestCounts(unittest.TestCase):
    """設計F §5.2.1 の回数と N に達した後。"""

    def test_exponential_after_n(self):
        t = Trace(1, TraceAction.AVERAGE, count=2)
        t.apply(frame([0.0] * 4))
        t.apply(frame([2.0] * 4))  # 累積平均 1
        u = t.apply(frame([5.0] * 4))  # 1 + (5-1)/2 = 3
        np.testing.assert_allclose(t.values, 3.0)
        self.assertEqual((u.count, u.count_reached, u.changed), (2, True, True))

    def test_restart_every_n(self):
        t = Trace(1, TraceAction.AVERAGE, count=2, average_after=AverageAfterCount.RESTART)
        t.apply(frame([0.0] * 4))
        np.testing.assert_allclose(t.values, 0.0)  # 1回目の N 回は途中の平均を出す
        t.apply(frame([2.0] * 4))
        np.testing.assert_allclose(t.values, 1.0)
        u = t.apply(frame([10.0] * 4))  # 新しい平均を作り始める。出す値は前の平均のまま
        self.assertFalse(u.changed)
        self.assertEqual(u.count, 1)
        np.testing.assert_allclose(t.values, 1.0)
        u = t.apply(frame([20.0] * 4))
        self.assertTrue(u.changed)
        np.testing.assert_allclose(t.values, 15.0)

    def test_restart_n1(self):
        t = Trace(1, TraceAction.AVERAGE, count=1, average_after=AverageAfterCount.RESTART)
        t.apply(frame([1.0] * 4))
        u = t.apply(frame([3.0] * 4))
        self.assertTrue(u.changed)
        np.testing.assert_allclose(t.values, 3.0)

    def test_hold_count_continue(self):
        t = Trace(1, TraceAction.MAX_HOLD, count=2)
        for v in (1.0, 2.0, 3.0):
            u = t.apply(frame([v] * 4))
        np.testing.assert_allclose(t.values, 3.0)
        self.assertEqual((u.count, u.count_target, u.count_reached), (2, 2, True))

    def test_hold_count_stop(self):
        t = Trace(1, TraceAction.MAX_HOLD, count=2, hold_after=HoldAfterCount.STOP)
        for v in (1.0, 2.0):
            t.apply(frame([v] * 4))
        u = t.apply(frame([3.0] * 4))
        self.assertFalse(u.changed)
        np.testing.assert_allclose(t.values, 2.0)

    def test_hold_without_count_counts_on(self):
        t = Trace(1, TraceAction.MIN_HOLD)
        for v in (3.0, 2.0, 1.0):
            u = t.apply(frame([v] * 4))
        self.assertEqual((u.count, u.count_target, u.count_reached), (3, None, False))

    def test_count_boundaries(self):
        # N−1 / N / N+1
        for applied, reached in ((3, False), (4, True), (5, True)):
            t = Trace(1, TraceAction.AVERAGE, count=4)
            for _ in range(applied):
                u = t.apply(frame([1.0] * 4))
            self.assertEqual((u.count, u.count_reached), (min(applied, 4), reached))


class TestTraceSet(unittest.TestCase):
    def test_defaults(self):
        s = TraceSet()
        self.assertEqual([t.action for t in s], [TraceAction.UPDATE] * 4)
        self.assertEqual([t.visible for t in s], [True, False, False, False])
        self.assertIs(s[3], s.traces[2])

    def test_single_target(self):
        s = TraceSet()
        self.assertEqual(s.single_target(), 1)
        s[2].configure(action=TraceAction.AVERAGE, count=16)
        s[3].configure(action=TraceAction.MAX_HOLD, count=20, visible=False)
        s[4].configure(action=TraceAction.MIN_HOLD)  # 回数なしは数えない
        self.assertEqual(s.single_target(), 20)
        s[3].configure(action=TraceAction.UPDATE)  # 更新では回数は意味を持たない
        self.assertEqual(s.single_target(), 16)

    def test_reset_for_single(self):
        s = TraceSet()
        s[2].configure(action=TraceAction.AVERAGE, count=4)
        s[4].configure(action=TraceAction.MAX_HOLD)
        s.apply(frame([1.0] * 4))
        s.apply(frame([2.0] * 4))
        s.reset_for_single()
        self.assertEqual(s[2].current_count, 0)
        self.assertIsNone(s[2].values)
        self.assertEqual(s[4].current_count, 2)  # 回数を持たないトレースはそのまま
        self.assertEqual(s[2].action, TraceAction.AVERAGE)


class TestDiscard(unittest.TestCase):
    """設計F §5.3。"""

    def _accumulated(self, action=TraceAction.MAX_HOLD, **kw):
        t = Trace(1, action, **kw)
        t.apply(frame([1.0] * 4))
        t.apply(frame([2.0] * 4))
        return t

    def test_condition_changes_discard_and_keep_action(self):
        for kind in ConditionChange:
            for action, kw in ((TraceAction.MAX_HOLD, {}), (TraceAction.MIN_HOLD, {}),
                               (TraceAction.AVERAGE, {"count": 4})):
                t = self._accumulated(action, **kw)
                self.assertEqual(t.on_condition_change(kind), NOTICE_RESET_BY_CONDITION)
                self.assertEqual(t.action, action)
                self.assertEqual(t.current_count, 0)
                self.assertIsNone(t.values)
                # 知らせは1回だけ（捨てるものが無ければ知らせない）
                self.assertIsNone(t.on_condition_change(kind))

    def test_condition_change_by_string(self):
        t = self._accumulated()
        self.assertEqual(t.on_condition_change("rbw"), NOTICE_RESET_BY_CONDITION)

    def test_display_changes_keep(self):
        for kind in DisplayChange:
            t = self._accumulated()
            self.assertIsNone(t.on_condition_change(kind))
            self.assertIsNone(t.on_condition_change(kind.value))
            self.assertEqual(t.current_count, 2)
            np.testing.assert_allclose(t.values, 2.0)

    def test_unknown_kind_raises(self):
        with self.assertRaises(ValueError):
            Trace(1).on_condition_change("zzz")

    def test_fixed_never_discarded_and_reports_axis(self):
        s = TraceSet()
        s[1].apply(frame([1.0] * 4))
        s[1].configure(action=TraceAction.FIXED)
        self.assertEqual(s.on_condition_change(ConditionChange.AXIS), {})
        np.testing.assert_allclose(s[1].values, 1.0)
        new_axis = FreqAxis(200 * MHZ, 1 * MHZ, 4)
        self.assertEqual(s.fixed_axis_differs(new_axis), [1])
        self.assertEqual(s.fixed_axis_differs(AXIS), [])

    def test_traceset_notices(self):
        s = TraceSet()
        s[2].configure(action=TraceAction.MAX_HOLD)
        s.apply(frame([1.0] * 4))
        self.assertEqual(s.on_condition_change("axis"), {2: NOTICE_RESET_BY_CONDITION})

    def test_frame_axis_mismatch_resets(self):
        t = self._accumulated()
        u = t.apply(frame([0.5] * 3, axis=FreqAxis(0, 1 * MHZ, 3)))
        self.assertIn(NOTICE_RESET_BY_FRAME_MISMATCH, u.notices)
        self.assertEqual(u.count, 1)
        np.testing.assert_allclose(t.values, 0.5)


class TestDb(unittest.TestCase):
    def test_to_db(self):
        np.testing.assert_allclose(to_db(np.array([1e-3, 1.0]), Quantity.POWER), [0.0, 30.0])
        np.testing.assert_allclose(to_db(np.array([0.1, 1j]), Quantity.RATIO), [-20.0, 0.0])
        self.assertEqual(to_db(np.array([0.0]), Quantity.POWER)[0], -np.inf)


class TestDifference(unittest.TestCase):
    """設計F §5.4。"""

    def test_power_difference_with_db_interpolation_over_overlap(self):
        fa = np.array([100, 110, 120, 130], dtype=np.int64) * MHZ
        fb = np.array([105, 125, 145], dtype=np.int64) * MHZ
        a = np.full(4, 1e-3)  # 0dBm
        b = np.array([1e-4, 1e-2, 1e-3])  # -10, +10, 0 dBm
        d = difference(fa, a, Quantity.POWER, fb, b, Quantity.POWER)
        self.assertIsNone(d.error)
        np.testing.assert_array_equal(d.freqs_hz, np.array([110, 120, 130]) * MHZ)
        # B の dB: 110→-5, 120→+5, 130→+7.5
        np.testing.assert_allclose(d.values_db, [5.0, -5.0, -7.5])
        self.assertEqual(d.freqs_hz.dtype, np.int64)

    def test_quantity_mismatch_refused(self):
        f = np.array([1, 2], dtype=np.int64)
        d = difference(f, np.ones(2), Quantity.POWER, f, np.ones(2), Quantity.RATIO)
        self.assertEqual(d.error, DIFF_QUANTITY_MISMATCH)

    def test_no_overlap(self):
        d = difference(np.array([1, 2]), np.ones(2), Quantity.RATIO,
                       np.array([3, 4]), np.ones(2), Quantity.RATIO)
        self.assertEqual(d.error, DIFF_NO_OVERLAP)

    def test_complex_vs_real_is_real(self):
        f = np.array([1, 2], dtype=np.int64)
        d = difference(f, np.array([10j, -10]), Quantity.RATIO, f, np.array([1.0, 1.0]), Quantity.RATIO)
        self.assertEqual(d.value_kind, ValueKind.REAL)
        self.assertFalse(np.iscomplexobj(d.values_db))
        np.testing.assert_allclose(d.values_db, [20.0, 20.0])

    def test_float_freqs_rejected(self):
        with self.assertRaises(TypeError):
            difference(np.array([1.0]), np.ones(1), Quantity.RATIO, np.array([1]), np.ones(1), Quantity.RATIO)


class TestDecimate(unittest.TestCase):
    """設計F §5.5。"""

    def test_max_per_pixel(self):
        f = np.arange(8, dtype=np.int64) * 10
        v = np.array([1, 5, 2, 3, 9, 0, 4, 4], dtype=float)
        r = decimate(f, v, 4, 0, 80)
        np.testing.assert_array_equal(r.pixel, [0, 1, 2, 3])
        np.testing.assert_array_equal(r.values, [5, 3, 9, 4])
        np.testing.assert_array_equal(r.freqs_hz, [10, 30, 40, 60])

    def test_min_hold_uses_min(self):
        f = np.arange(8, dtype=np.int64) * 10
        v = np.array([1, 5, 2, 3, 9, 0, 4, 4], dtype=float)
        r = decimate(f, v, 4, 0, 80, use_min=True)
        np.testing.assert_array_equal(r.values, [1, 2, 0, 4])

    def test_fewer_points_than_pixels_kept(self):
        f = np.array([0, 50, 100], dtype=np.int64)
        r = decimate(f, np.array([1.0, 2, 3]), 10, 0, 100)
        np.testing.assert_array_equal(r.values, [1, 2, 3])
        np.testing.assert_array_equal(r.pixel, [0, 5, 9])

    def test_outside_display_range_dropped(self):
        f = np.array([0, 10, 20, 30], dtype=np.int64)
        r = decimate(f, np.array([9.0, 1, 2, 9]), 1, 10, 20)
        np.testing.assert_array_equal(r.values, [2])

    def test_complex_by_magnitude_and_nan(self):
        f = np.arange(4, dtype=np.int64)
        v = np.array([np.nan, 1j, 3, -4j])
        r = decimate(f, v, 2, 0, 4)
        np.testing.assert_array_equal(r.values, [1j, -4j])

    def test_zero_span_uses_index(self):
        f = np.full(6, 100, dtype=np.int64)
        r = decimate(f, np.array([1.0, 2, 3, 4, 5, 6]), 3, 100, 100)
        np.testing.assert_array_equal(r.values, [2, 4, 6])


if __name__ == "__main__":
    unittest.main()
