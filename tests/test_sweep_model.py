"""掃引条件モデル（設計C §5）の試験。境界は設計C §5.9 の N−1 / N / N+1。"""
from __future__ import annotations

import json
import math
import random
import unittest
from pathlib import Path

from pygigast.core.sweep.model import POINTS_AUTO, SweepModel
from pygigast.core.types import AdoptedSweep, Mode, SweepRequest

PROFILE_PATH = Path(__file__).resolve().parents[1] / "pygigast" / "core" / "devices" / "gigast" / "profile.json"

MHZ = 1_000_000
KHZ = 1_000
MIN_HZ = -100 * MHZ
MAX_HZ = 12100 * MHZ
# プロトコルの Step の桁（×100Hz）
STEP_PROTOCOL_MAX = 65535 * 100


def design_timing(step_hz: int, rbw_hz: int, mode=None) -> tuple[float, float]:
    """設計B §7.2 の式。Sample = round(Step_100Hz / (5 × RBW_kHz))（設計C §5.5）。"""
    sample = round((step_hz // 100) / (5 * (rbw_hz // 1000)))
    return 0.022 + 0.019 * sample, 0.00014 + 0.00005 * sample


def load_profile() -> dict:
    with open(PROFILE_PATH, encoding="utf-8") as f:
        return json.load(f)


def adj_map(sweep: AdoptedSweep) -> dict:
    return {a.field: a for a in sweep.adjustments}


class Base(unittest.TestCase):
    def setUp(self) -> None:
        self.profile = load_profile()
        self.m = SweepModel(self.profile, design_timing)
        self.cur = self.m.default()

    def req(self, current=None, **kw) -> AdoptedSweep:
        return self.m.apply(SweepRequest(**kw), current or self.cur)

    def at(self, start: int, stop: int, **kw) -> AdoptedSweep:
        """Start/Stop を同時に触った条件（ドラッグ）を作る。"""
        return self.req(start_hz=start, stop_hz=stop, edited=("start_hz", "stop_hz"), **kw)

    def assertDeviceOk(self, s: AdoptedSweep) -> None:
        """機器へ送る条件が制約内に収まる（設計C §5.9 の「送る32バイトが制約内」）。"""
        ax = s.axis
        dev_start = ax.start_hz - s.preroll_points * ax.step_hz
        self.assertGreaterEqual(dev_start, MIN_HZ)
        self.assertLessEqual(ax.last_hz, MAX_HZ)
        self.assertLessEqual(ax.points + s.preroll_points, 12500)
        self.assertGreaterEqual(ax.points, 1)
        self.assertLessEqual(ax.step_hz, STEP_PROTOCOL_MAX)
        self.assertLessEqual(ax.step_hz, 1 * MHZ)
        self.assertEqual(ax.start_hz % 100, 0)
        self.assertEqual(ax.step_hz % 100, 0)
        self.assertEqual(ax.start_hz, s.start_hz)
        self.assertTrue(MIN_HZ <= s.start_hz <= s.stop_hz <= MAX_HZ)
        if ax.step_hz == 0:
            self.assertEqual(s.start_hz, s.stop_hz)
        else:
            self.assertLess(s.start_hz, s.stop_hz)
            # 表示する範囲は機器の点で覆われる（最後の点を削ったときだけ手前に隙間）
            if s.tail_gap:
                self.assertLess(ax.last_hz, s.stop_hz)
                self.assertGreater(ax.last_hz + ax.step_hz, MAX_HZ)
            else:
                self.assertGreaterEqual(ax.last_hz, s.stop_hz)
                self.assertLess(ax.last_hz - ax.step_hz, s.stop_hz)
        for v in (s.start_hz, s.stop_hz, ax.start_hz, ax.step_hz, ax.points):
            self.assertIs(type(v), int)
        # イメージ除去の区間（設計F §7.2）: 点を順に重ならずに覆い、各区間の要求も制約内
        for segs in (s.segments, s.accurate_segments):
            self.assertEqual(sum(g.points for g in segs), ax.points)
            pos = 0
            for g in segs:
                self.assertEqual(g.point0, pos)
                pos += g.points
                self.assertGreaterEqual(ax.start_hz + (g.point0 - g.preroll_points) * ax.step_hz, MIN_HZ)
                self.assertLessEqual(g.points + g.preroll_points, 12500)
        self.assertEqual(s.preroll_points, s.segments[0].preroll_points)


class TestDefault(Base):
    def test_default(self):
        d = self.cur
        self.assertEqual((d.start_hz, d.stop_hz), (MIN_HZ, MAX_HZ))
        self.assertEqual(d.mode, Mode.SA)
        self.assertEqual(d.rbw_hz, 180 * KHZ)
        self.assertIsNone(d.points_setting)
        self.assertEqual(d.adjustments, ())
        self.assertDeviceOk(d)


class TestEditedField(Base):
    """設計C §5.1 の表: 触った欄を守り、相方を動かす。"""

    def setUp(self):
        super().setUp()
        self.cur = self.at(1000 * MHZ, 1100 * MHZ)

    def test_center_keeps_span(self):
        s = self.req(center_hz=2000 * MHZ, edited=("center_hz",))
        self.assertEqual((s.center_hz, s.span_hz), (2000 * MHZ, 100 * MHZ))
        self.assertEqual((s.start_hz, s.stop_hz), (1950 * MHZ, 2050 * MHZ))
        self.assertEqual(s.adjustments, ())

    def test_span_keeps_center(self):
        s = self.req(span_hz=10 * MHZ, edited=("span_hz",))
        self.assertEqual((s.center_hz, s.span_hz), (1050 * MHZ, 10 * MHZ))
        self.assertEqual(s.adjustments, ())

    def test_start_keeps_stop(self):
        s = self.req(start_hz=900 * MHZ, edited=("start_hz",))
        self.assertEqual((s.start_hz, s.stop_hz), (900 * MHZ, 1100 * MHZ))

    def test_stop_keeps_start(self):
        s = self.req(stop_hz=1500 * MHZ, edited=("stop_hz",))
        self.assertEqual((s.start_hz, s.stop_hz), (1000 * MHZ, 1500 * MHZ))

    def test_drag_sets_both(self):
        s = self.at(300 * MHZ, 400 * MHZ)
        self.assertEqual((s.start_hz, s.stop_hz), (300 * MHZ, 400 * MHZ))

    def test_edited_inferred_from_values(self):
        s = self.m.apply(SweepRequest(center_hz=2000 * MHZ), self.cur)
        self.assertEqual(s.span_hz, 100 * MHZ)

    def test_mixed_groups_rejected(self):
        with self.assertRaises(ValueError):
            self.req(start_hz=1, center_hz=2, edited=("start_hz", "center_hz"))

    def test_float_rejected(self):
        with self.assertRaises(TypeError):
            self.req(start_hz=1.0e9, edited=("start_hz",))


class TestClamp(Base):
    """設計C §5.2 範囲に収める。"""

    def test_center_over_upper_keeps_center(self):
        # 既定は Center を守って Span を縮める（設計C §5.11 の 1）
        cur = self.at(1000 * MHZ, 1100 * MHZ)
        s = self.req(cur, center_hz=12080 * MHZ, edited=("center_hz",))
        self.assertEqual((s.start_hz, s.stop_hz), (12060 * MHZ, 12100 * MHZ))
        a = adj_map(s)["span_hz"]
        self.assertEqual((a.requested, a.adopted, a.reason), (100 * MHZ, 40 * MHZ, "keep_center"))
        # 反対の結果（Span を守って Center を動かす）を1つ添える
        self.assertEqual((s.alternative.rule, s.alternative.start_hz, s.alternative.stop_hz),
                         ("keep_span", 12000 * MHZ, 12100 * MHZ))
        self.assertDeviceOk(s)

    def test_center_below_lower_keeps_min_span(self):
        # Center 自体が範囲の外: 端に合わせ、縮めると最小 Span を割るので最小 Span を守って内側へ
        cur = self.at(1000 * MHZ, 1100 * MHZ)
        s = self.req(cur, center_hz=-130 * MHZ, edited=("center_hz",))
        self.assertEqual((s.start_hz, s.stop_hz), (MIN_HZ, MIN_HZ + 49_900))
        self.assertDeviceOk(s)

    def test_full_range_center_input_takes_effect(self):
        # 範囲いっぱいで Center に 1200MHz → Center 1200MHz・Span 2600MHz（設計C §5.11 の例）
        s = self.req(self.m.default(), center_hz=1200 * MHZ, edited=("center_hz",))
        self.assertEqual((s.start_hz, s.stop_hz), (-100 * MHZ, 2500 * MHZ))
        self.assertEqual(s.center_hz, 1200 * MHZ)
        # 反対の規則（Span を守る）の結果は範囲いっぱいのまま。いまの結果と違うのでボタンに出す
        self.assertEqual((s.alternative.rule, s.alternative.start_hz, s.alternative.stop_hz),
                         ("keep_span", MIN_HZ, MAX_HZ))

    def test_span_1000_center_11900(self):
        cur = self.req(self.m.default(), center_hz=6000 * MHZ, span_hz=1000 * MHZ,
                       edited=("center_hz", "span_hz"))
        s = self.req(cur, center_hz=11900 * MHZ, edited=("center_hz",))
        self.assertEqual((s.center_hz, s.span_hz), (11900 * MHZ, 400 * MHZ))
        self.assertEqual((s.alternative.start_hz, s.alternative.stop_hz), (11100 * MHZ, 12100 * MHZ))

    def test_span_lock_keeps_span_and_lock_value(self):
        cur = self.req(self.m.default(), center_hz=6000 * MHZ, span_hz=1000 * MHZ,
                       edited=("center_hz", "span_hz"))
        cur = self.req(cur, span_lock=True)
        self.assertEqual(cur.span_lock_hz, 1000 * MHZ)
        s = self.req(cur, center_hz=11900 * MHZ, edited=("center_hz",))
        self.assertEqual((s.start_hz, s.stop_hz), (11100 * MHZ, 12100 * MHZ))
        self.assertEqual(adj_map(s)["center_hz"].reason, "keep_span")
        self.assertEqual((s.alternative.rule, s.alternative.start_hz, s.alternative.stop_hz),
                         ("keep_center", 11700 * MHZ, 12100 * MHZ))
        # その1回だけの命令（反対の結果のボタン）は固定 Span を変えない
        b = self.req(s, start_hz=s.alternative.start_hz, stop_hz=s.alternative.stop_hz,
                     edited=("start_hz", "stop_hz"), one_shot=True)
        self.assertEqual((b.span_hz, b.span_lock_hz), (400 * MHZ, 1000 * MHZ))
        # 次に Center を触ると、固定した 1000MHz が守られる
        n = self.req(b, center_hz=6000 * MHZ, edited=("center_hz",))
        self.assertEqual(n.span_hz, 1000 * MHZ)
        # 利用者が Span を直接変えると固定 Span も変わる
        d = self.req(n, span_hz=200 * MHZ, edited=("span_hz",))
        self.assertEqual(d.span_lock_hz, 200 * MHZ)
        # OFF で固定 Span は無くなる
        off = self.req(d, span_lock=False)
        self.assertIsNone(off.span_lock_hz)

    def test_span_lock_full_range_center(self):
        # 固定中で Span が範囲いっぱい: Center は動けない。反対の結果は Center を守って縮めたもの
        cur = self.req(self.m.default(), span_lock=True)
        s = self.req(cur, center_hz=1200 * MHZ, edited=("center_hz",))
        self.assertEqual((s.start_hz, s.stop_hz), (MIN_HZ, MAX_HZ))
        self.assertEqual((s.alternative.start_hz, s.alternative.stop_hz), (-100 * MHZ, 2500 * MHZ))

    def test_center_boundaries_both_rules(self):
        # 設計C §5.11 の 6: 下限 + 最小 Span/2 の前後・上限 − 最小 Span/2 の前後・端ちょうど・その外
        half = 49_900 // 2
        for lock in (False, True):
            base = self.req(self.m.default(), center_hz=6000 * MHZ, span_hz=100 * MHZ,
                            edited=("center_hz", "span_hz"))
            base = self.req(base, span_lock=lock)
            for c in (MIN_HZ + half - 100, MIN_HZ + half, MIN_HZ + half + 100, MIN_HZ, MIN_HZ - 100,
                      MAX_HZ - half - 100, MAX_HZ - half, MAX_HZ - half + 100, MAX_HZ, MAX_HZ + 100):
                s = self.req(base, center_hz=c, edited=("center_hz",))
                self.assertGreaterEqual(s.start_hz, MIN_HZ, (lock, c))
                self.assertLessEqual(s.stop_hz, MAX_HZ, (lock, c))
                self.assertGreaterEqual(s.span_hz, 49_900, (lock, c))
                if lock:
                    self.assertEqual(s.span_hz, 100 * MHZ, (lock, c))
                self.assertDeviceOk(s)

    def test_center_and_span_wider_than_range(self):
        s = self.req(center_hz=6000 * MHZ, span_hz=20000 * MHZ, edited=("center_hz", "span_hz"))
        self.assertEqual((s.start_hz, s.stop_hz), (MIN_HZ, MAX_HZ))
        self.assertEqual(adj_map(s)["span_hz"].reason, "upper_limit")
        self.assertDeviceOk(s)

    def test_span_over_upper_keeps_span(self):
        # Span を打って範囲を超えた: 打った Span を守り、Center を内側へ動かす（設計C §5.12。旧 §5.2 の
        # 「Center を守って Span を縮める」を置き換えた）
        cur = self.at(11900 * MHZ, 12000 * MHZ)  # Center 11950MHz
        s = self.req(cur, span_hz=1000 * MHZ, edited=("span_hz",))
        self.assertEqual((s.start_hz, s.stop_hz), (11100 * MHZ, 12100 * MHZ))
        a = adj_map(s)["center_hz"]
        self.assertEqual((a.requested, a.adopted, a.reason), (11950 * MHZ, 11600 * MHZ, "upper_limit"))
        self.assertNotIn("span_hz", adj_map(s))
        self.assertIsNone(s.alternative)

    def test_span_center_at_edge_keeps_span(self):
        # Center が上限ぎりぎり: 打った Span を守って Center を内側へ（設計C §5.12）
        cur = self.at(MAX_HZ - 49_900, MAX_HZ)  # 最小 Span、Center = 上限 − 24950Hz
        s = self.req(cur, span_hz=10 * MHZ, edited=("span_hz",))
        self.assertEqual((s.start_hz, s.stop_hz), (MAX_HZ - 10 * MHZ, MAX_HZ))
        self.assertEqual(adj_map(s)["center_hz"].reason, "upper_limit")
        self.assertNotIn("span_hz", adj_map(s))
        self.assertDeviceOk(s)

    def test_span_center_at_edge_moves_center(self):
        # 同時に点数を増やして最小 Span が上がると、縮めても収まらない: Span を守って Center を動かす
        cur = self.at(MAX_HZ - 49_900, MAX_HZ)
        s = self.req(cur, span_hz=10 * MHZ, edited=("span_hz",), points=12000)
        self.assertEqual((s.start_hz, s.stop_hz), (MAX_HZ - 10 * MHZ, MAX_HZ))
        self.assertEqual(adj_map(s)["center_hz"].reason, "upper_limit")
        self.assertNotIn("span_hz", adj_map(s))
        self.assertDeviceOk(s)

    def test_start_over_stop_keeps_typed(self):
        cur = self.at(1000 * MHZ, 1100 * MHZ)
        s = self.req(cur, start_hz=2000 * MHZ, edited=("start_hz",))
        self.assertEqual((s.start_hz, s.stop_hz), (2000 * MHZ, 2000 * MHZ + 49_900))
        a = adj_map(s)["stop_hz"]
        self.assertEqual((a.requested, a.adopted, a.reason), (1100 * MHZ, 2000 * MHZ + 49_900, "min_span"))

    def test_stop_below_start_keeps_typed(self):
        cur = self.at(1000 * MHZ, 1100 * MHZ)
        s = self.req(cur, stop_hz=500 * MHZ, edited=("stop_hz",))
        self.assertEqual((s.start_hz, s.stop_hz), (500 * MHZ - 49_900, 500 * MHZ))
        self.assertEqual(adj_map(s)["start_hz"].reason, "min_span")

    def test_start_equal_stop_is_not_zero_span(self):
        cur = self.at(1000 * MHZ, 1100 * MHZ)
        s = self.req(cur, start_hz=1100 * MHZ, edited=("start_hz",))
        self.assertEqual(s.span_hz, 49_900)

    def test_start_at_upper_moves_start(self):
        cur = self.at(1000 * MHZ, 1100 * MHZ)
        s = self.req(cur, start_hz=13000 * MHZ, edited=("start_hz",))
        self.assertEqual((s.start_hz, s.stop_hz), (MAX_HZ - 49_900, MAX_HZ))
        self.assertEqual(adj_map(s)["start_hz"].reason, "upper_limit")
        self.assertDeviceOk(s)

    def test_stop_at_lower_moves_stop(self):
        cur = self.at(1000 * MHZ, 1100 * MHZ)
        s = self.req(cur, stop_hz=-200 * MHZ, edited=("stop_hz",))
        self.assertEqual((s.start_hz, s.stop_hz), (MIN_HZ, MIN_HZ + 49_900))
        self.assertEqual(adj_map(s)["stop_hz"].reason, "lower_limit")
        self.assertDeviceOk(s)


class TestSpanLockTable(Base):
    """設計C §5.12 の表: 変えた欄ごとに、Span 固定 OFF / ON で動くもの。"""

    L = 100 * MHZ  # 固定 Span

    def base(self, lock: bool, center=6000 * MHZ, span=None) -> AdoptedSweep:
        cur = self.req(self.m.default(), center_hz=center, span_hz=span or self.L, edited=("center_hz", "span_hz"))
        return self.req(cur, span_lock=lock)

    # ---- Center
    def test_center(self):
        for lock in (False, True):
            s = self.req(self.base(lock), center_hz=7000 * MHZ, edited=("center_hz",))
            self.assertEqual((s.start_hz, s.stop_hz), (6950 * MHZ, 7050 * MHZ), lock)
        # 範囲の外に出る: OFF は Center を守って Span を縮める、ON は固定 Span を守って Center を内側へ
        off = self.req(self.base(False), center_hz=12080 * MHZ, edited=("center_hz",))
        self.assertEqual((off.start_hz, off.stop_hz), (12060 * MHZ, 12100 * MHZ))
        on = self.req(self.base(True), center_hz=12080 * MHZ, edited=("center_hz",))
        self.assertEqual((on.start_hz, on.stop_hz), (12000 * MHZ, 12100 * MHZ))
        self.assertEqual(on.span_lock_hz, self.L)

    # ---- Span
    def test_span(self):
        for lock in (False, True):
            s = self.req(self.base(lock), span_hz=200 * MHZ, edited=("span_hz",))
            self.assertEqual((s.start_hz, s.stop_hz), (5900 * MHZ, 6100 * MHZ), lock)
            if lock:
                self.assertEqual(s.span_lock_hz, 200 * MHZ)  # 固定 Span は採用した Span に置き換わる
            else:
                self.assertIsNone(s.span_lock_hz)

    def test_span_over_range_keeps_typed_span(self):
        for lock in (False, True):
            s = self.req(self.base(lock, center=12000 * MHZ), span_hz=1000 * MHZ, edited=("span_hz",))
            self.assertEqual((s.start_hz, s.stop_hz), (11100 * MHZ, 12100 * MHZ), lock)
            self.assertEqual(adj_map(s)["center_hz"].reason, "upper_limit")
            self.assertNotIn("span_hz", adj_map(s))
            if lock:
                self.assertEqual(s.span_lock_hz, 1000 * MHZ)
            s = self.req(self.base(lock, center=0), span_hz=1000 * MHZ, edited=("span_hz",))
            self.assertEqual((s.start_hz, s.stop_hz), (MIN_HZ, 900 * MHZ), lock)
            self.assertEqual(adj_map(s)["center_hz"].reason, "lower_limit")

    def test_span_wider_than_range(self):
        for lock in (False, True):
            s = self.req(self.base(lock), span_hz=20000 * MHZ, edited=("span_hz",))
            self.assertEqual((s.start_hz, s.stop_hz), (MIN_HZ, MAX_HZ), lock)
            self.assertEqual(adj_map(s)["span_hz"].reason, "upper_limit")
            if lock:
                self.assertEqual(s.span_lock_hz, MAX_HZ - MIN_HZ)

    def test_span_boundaries(self):
        # Center 11000MHz で Stop が 上限 −100Hz / ちょうど / +100Hz になる Span
        exact = 2 * (MAX_HZ - 11000 * MHZ)
        for lock in (False, True):
            for d, moved in ((-200, False), (0, False), (200, True)):
                s = self.req(self.base(lock, center=11000 * MHZ), span_hz=exact + d, edited=("span_hz",))
                self.assertEqual(s.span_hz, exact + d, (lock, d))
                self.assertEqual(s.stop_hz, MAX_HZ if d >= 0 else MAX_HZ - 100, (lock, d))
                self.assertEqual("center_hz" in adj_map(s), moved, (lock, d))
                self.assertDeviceOk(s)

    # ---- Start
    def test_start(self):
        off = self.req(self.base(False), start_hz=5000 * MHZ, edited=("start_hz",))
        self.assertEqual((off.start_hz, off.stop_hz), (5000 * MHZ, 6050 * MHZ))
        on = self.req(self.base(True), start_hz=5000 * MHZ, edited=("start_hz",))
        self.assertEqual((on.start_hz, on.stop_hz), (5000 * MHZ, 5100 * MHZ))
        self.assertEqual(on.span_lock_hz, self.L)
        a = adj_map(on)["stop_hz"]
        self.assertEqual((a.requested, a.adopted, a.reason), (6050 * MHZ, 5100 * MHZ, "span_lock"))
        self.assertNotIn("start_hz", adj_map(on))
        self.assertIsNone(on.alternative)

    def test_start_over_upper_with_lock(self):
        on = self.req(self.base(True), start_hz=12050 * MHZ, edited=("start_hz",))
        self.assertEqual((on.start_hz, on.stop_hz), (12000 * MHZ, 12100 * MHZ))
        a = adj_map(on)["start_hz"]
        self.assertEqual((a.requested, a.adopted, a.reason), (12050 * MHZ, 12000 * MHZ, "keep_span"))
        self.assertEqual(adj_map(on)["stop_hz"].reason, "span_lock")
        # 反対の結果: 打った Start を守り、Stop を上限にして Span を縮める
        alt = on.alternative
        self.assertEqual((alt.rule, alt.start_hz, alt.stop_hz), ("keep_typed", 12050 * MHZ, MAX_HZ))
        # ボタン（その1回だけの命令）: 送った Start・Stop をそのまま使い、固定 Span は変えない
        b = self.req(on, start_hz=alt.start_hz, stop_hz=alt.stop_hz, edited=("start_hz", "stop_hz"), one_shot=True)
        self.assertEqual((b.start_hz, b.stop_hz, b.span_lock_hz), (12050 * MHZ, MAX_HZ, self.L))
        self.assertEqual(b.adjustments, ())

    def test_start_beyond_upper_with_lock(self):
        on = self.req(self.base(True), start_hz=13000 * MHZ, edited=("start_hz",))
        self.assertEqual((on.start_hz, on.stop_hz), (12000 * MHZ, MAX_HZ))
        self.assertEqual(adj_map(on)["start_hz"].reason, "keep_span")
        self.assertEqual((on.alternative.start_hz, on.alternative.stop_hz), (MAX_HZ - 49_900, MAX_HZ))
        self.assertDeviceOk(on)

    # ---- Stop
    def test_stop(self):
        off = self.req(self.base(False), stop_hz=7000 * MHZ, edited=("stop_hz",))
        self.assertEqual((off.start_hz, off.stop_hz), (5950 * MHZ, 7000 * MHZ))
        on = self.req(self.base(True), stop_hz=7000 * MHZ, edited=("stop_hz",))
        self.assertEqual((on.start_hz, on.stop_hz), (6900 * MHZ, 7000 * MHZ))
        a = adj_map(on)["start_hz"]
        self.assertEqual((a.requested, a.adopted, a.reason), (5950 * MHZ, 6900 * MHZ, "span_lock"))
        self.assertIsNone(on.alternative)
        self.assertEqual(on.span_lock_hz, self.L)

    def test_stop_below_lower_with_lock(self):
        on = self.req(self.base(True), stop_hz=-50 * MHZ, edited=("stop_hz",))
        self.assertEqual((on.start_hz, on.stop_hz), (MIN_HZ, 0))
        a = adj_map(on)["stop_hz"]
        self.assertEqual((a.requested, a.adopted, a.reason), (-50 * MHZ, 0, "keep_span"))
        alt = on.alternative
        self.assertEqual((alt.rule, alt.start_hz, alt.stop_hz), ("keep_typed", MIN_HZ, -50 * MHZ))
        b = self.req(on, start_hz=alt.start_hz, stop_hz=alt.stop_hz, edited=("start_hz", "stop_hz"), one_shot=True)
        self.assertEqual((b.start_hz, b.stop_hz, b.span_lock_hz), (MIN_HZ, -50 * MHZ, self.L))

    def test_user_reported_case(self):
        # 2026-10-01 ユーザー指摘: 固定 ON・Span 44.571MHz で Start に 470MHz → Stop 514.571MHz、Span は変わらない
        cur = self.base(True, center=1000 * MHZ, span=44_571_000)
        self.assertEqual(cur.span_lock_hz, 44_571_000)
        s = self.req(cur, start_hz=470 * MHZ, edited=("start_hz",))
        self.assertEqual((s.start_hz, s.stop_hz), (470 * MHZ, 514_571_000))
        self.assertEqual((s.span_hz, s.span_lock_hz), (44_571_000, 44_571_000))

    # ---- ドラッグ
    def test_drag(self):
        off = self.at(1000 * MHZ, 1500 * MHZ, current=self.base(False))
        self.assertEqual((off.start_hz, off.stop_hz), (1000 * MHZ, 1500 * MHZ))
        on = self.at(1000 * MHZ, 1500 * MHZ, current=self.base(True))
        # 選んだ範囲の真ん中（1250MHz）を Center に、幅は固定 Span のまま
        self.assertEqual((on.start_hz, on.stop_hz), (1200 * MHZ, 1300 * MHZ))
        self.assertEqual(on.span_lock_hz, self.L)
        self.assertEqual(adj_map(on)["start_hz"].reason, "span_lock")
        self.assertEqual(adj_map(on)["stop_hz"].reason, "span_lock")
        self.assertIsNone(on.alternative)

    def test_drag_at_edge_with_lock(self):
        cur = self.base(True, span=1000 * MHZ)
        on = self.at(12000 * MHZ, 12100 * MHZ, current=cur)
        self.assertEqual((on.start_hz, on.stop_hz), (11100 * MHZ, 12100 * MHZ))
        self.assertEqual(adj_map(on)["center_hz"].reason, "keep_span")
        # 反対の結果は Center（選んだ範囲の真ん中 12050MHz）を守って Span を縮めたもの（§5.11 と同じ）
        alt = on.alternative
        self.assertEqual((alt.rule, alt.start_hz, alt.stop_hz), ("keep_center", 12000 * MHZ, 12100 * MHZ))

    def test_one_shot_bypasses_lock(self):
        on = self.at(200 * MHZ, 250 * MHZ, current=self.base(True), one_shot=True)
        self.assertEqual((on.start_hz, on.stop_hz, on.span_lock_hz), (200 * MHZ, 250 * MHZ, self.L))
        # 範囲だけは守る
        on = self.at(12050 * MHZ, 12200 * MHZ, current=self.base(True), one_shot=True)
        self.assertEqual((on.start_hz, on.stop_hz, on.span_lock_hz), (12050 * MHZ, MAX_HZ, self.L))
        self.assertEqual(adj_map(on)["stop_hz"].reason, "upper_limit")

    # ---- 境界（N−1 / N / N+1）
    def test_start_boundaries_with_lock(self):
        n = MAX_HZ - self.L
        for start, moved in ((n - 100, False), (n, False), (n + 100, True)):
            s = self.req(self.base(True), start_hz=start, edited=("start_hz",))
            self.assertEqual(s.span_hz, self.L, start)
            self.assertEqual(s.start_hz, n if moved else start)
            self.assertEqual(s.alternative is not None, moved, start)
            self.assertEqual("start_hz" in adj_map(s), moved, start)
            self.assertDeviceOk(s)

    def test_stop_boundaries_with_lock(self):
        n = MIN_HZ + self.L
        for stop, moved in ((n - 100, True), (n, False), (n + 100, False)):
            s = self.req(self.base(True), stop_hz=stop, edited=("stop_hz",))
            self.assertEqual(s.span_hz, self.L, stop)
            self.assertEqual(s.stop_hz, n if moved else stop)
            self.assertEqual(s.alternative is not None, moved, stop)
            self.assertDeviceOk(s)

    def test_full_range_lock(self):
        # 固定 Span が範囲いっぱい: Start・Stop・ドラッグのどれでも範囲いっぱいのまま。反対の結果を添える
        cur = self.req(self.m.default(), span_lock=True)
        self.assertEqual(cur.span_lock_hz, MAX_HZ - MIN_HZ)
        s = self.req(cur, start_hz=1000 * MHZ, edited=("start_hz",))
        self.assertEqual((s.start_hz, s.stop_hz), (MIN_HZ, MAX_HZ))
        self.assertEqual((s.alternative.start_hz, s.alternative.stop_hz), (1000 * MHZ, MAX_HZ))
        s = self.req(cur, stop_hz=1000 * MHZ, edited=("stop_hz",))
        self.assertEqual((s.start_hz, s.stop_hz), (MIN_HZ, MAX_HZ))
        self.assertEqual((s.alternative.start_hz, s.alternative.stop_hz), (MIN_HZ, 1000 * MHZ))
        d = self.at(1000 * MHZ, 2000 * MHZ, current=cur)
        self.assertEqual((d.start_hz, d.stop_hz), (MIN_HZ, MAX_HZ))
        self.assertEqual((d.alternative.start_hz, d.alternative.stop_hz), (MIN_HZ, 3100 * MHZ))


class TestFreqUnit(Base):
    def test_round_to_100hz(self):
        cur = self.at(1000 * MHZ, 1100 * MHZ)
        s = self.req(cur, start_hz=1000 * MHZ + 50, edited=("start_hz",))
        self.assertEqual(s.start_hz, 1000 * MHZ + 100)
        a = adj_map(s)["start_hz"]
        self.assertEqual((a.requested, a.adopted, a.reason), (1000 * MHZ + 50, 1000 * MHZ + 100, "freq_unit"))
        s = self.req(cur, start_hz=1000 * MHZ + 49, edited=("start_hz",))
        self.assertEqual(s.start_hz, 1000 * MHZ)

    def test_round_and_clamp_is_one_adjustment(self):
        s = self.req(start_hz=MIN_HZ - 130, edited=("start_hz",))
        adjs = [a for a in s.adjustments if a.field == "start_hz"]
        self.assertEqual(len(adjs), 1)
        self.assertEqual((adjs[0].requested, adjs[0].adopted, adjs[0].reason), (MIN_HZ - 130, MIN_HZ, "lower_limit"))

    def test_odd_span_center_half_unit(self):
        # Span が単位の奇数倍では Center が単位の半分ずれる。知らせる
        s = self.req(center_hz=1000 * MHZ, span_hz=100_100, edited=("center_hz", "span_hz"))
        self.assertEqual(s.span_hz, 100_100)
        self.assertEqual(abs(s.center_hz - 1000 * MHZ), 50)
        self.assertEqual(adj_map(s)["center_hz"].reason, "freq_unit")


class TestBoundaries(Base):
    """設計C §5.9 の境界値。"""

    def test_lower_limit(self):
        for start, adopted, reason in ((MIN_HZ - 100, MIN_HZ, "lower_limit"), (MIN_HZ, MIN_HZ, None), (MIN_HZ + 100, MIN_HZ + 100, None)):
            s = self.at(start, 0)
            self.assertEqual(s.start_hz, adopted)
            self.assertEqual(adj_map(s).get("start_hz") and adj_map(s)["start_hz"].reason, reason)
            self.assertDeviceOk(s)

    def test_upper_limit(self):
        for stop, adopted, reason in ((MAX_HZ - 100, MAX_HZ - 100, None), (MAX_HZ, MAX_HZ, None), (MAX_HZ + 100, MAX_HZ, "upper_limit")):
            s = self.at(12000 * MHZ, stop)
            self.assertEqual(s.stop_hz, adopted)
            self.assertEqual(adj_map(s).get("stop_hz") and adj_map(s)["stop_hz"].reason, reason)
            self.assertDeviceOk(s)

    def test_step_switch(self):
        start = 1000 * MHZ
        cases = (
            (500 * MHZ - 100, 999_900, 502),  # 499,999,900/500 を切り捨て。割り切れず最後の点が Stop の先
            (500 * MHZ, 1 * MHZ, 501),
            (501 * MHZ, 1 * MHZ, 502),
        )
        for span, step, points in cases:
            s = self.at(start, start + span)
            self.assertEqual((s.axis.step_hz, s.axis.points), (step, points), span)
            self.assertDeviceOk(s)

    def test_rbw_condition(self):
        cur = self.at(1000 * MHZ, 1010 * MHZ, rbw_preference_hz=30 * KHZ)
        for span, rbw in ((50 * MHZ - 100, 30 * KHZ), (50 * MHZ, 30 * KHZ), (50 * MHZ + 100, 180 * KHZ)):
            s = self.req(cur, start_hz=1000 * MHZ, stop_hz=1000 * MHZ + span, edited=("start_hz", "stop_hz"))
            self.assertEqual(s.rbw_hz, rbw, span)
            self.assertEqual(s.rbw_preference_hz, 30 * KHZ)
            if rbw == 180 * KHZ:
                self.assertEqual(adj_map(s)["rbw_hz"].reason, "rbw_fixed")

    def test_min_span(self):
        for n in (500, 101):
            cur = self.req(points=n)
            limit = 100 * (n - 1)
            center = 1000 * MHZ
            for span, adopted in ((limit - 100, limit), (limit, limit), (limit + 100, limit + 100)):
                s = self.req(cur, center_hz=center, span_hz=span, edited=("center_hz", "span_hz"))
                self.assertEqual(s.span_hz, adopted, (n, span))
                if span < limit:
                    self.assertEqual(adj_map(s)["span_hz"].reason, "min_span")
                else:
                    self.assertNotIn("span_hz", adj_map(s))
                self.assertLessEqual(s.axis.points, n)
                self.assertDeviceOk(s)
            s = self.req(cur, center_hz=center, span_hz=limit, edited=("center_hz", "span_hz"))
            self.assertEqual((s.axis.step_hz, s.axis.points), (100, n))
            z = self.req(cur, center_hz=center, span_hz=0, edited=("center_hz", "span_hz"))
            self.assertTrue(z.zero_span)
            self.assertEqual((z.start_hz, z.stop_hz, z.axis.points), (center, center, n))
            self.assertDeviceOk(z)

    def test_min_span_auto(self):
        # 自動は既定の点数（500）で数える
        s = self.req(center_hz=1000 * MHZ, span_hz=49_800, edited=("center_hz", "span_hz"))
        self.assertEqual(s.span_hz, 49_900)
        self.assertEqual((s.axis.step_hz, s.axis.points), (100, 500))

    def test_zero_span_default(self):
        s = self.req(center_hz=1000 * MHZ, span_hz=0, edited=("center_hz", "span_hz"))
        self.assertEqual((s.axis.step_hz, s.axis.points), (0, 500))
        self.assertGreater(s.preroll_points, 0)
        self.assertDeviceOk(s)

    def test_stop_fraction(self):
        # 割り切れる
        s = self.at(1000 * MHZ, 1100 * MHZ)
        self.assertEqual((s.axis.step_hz, s.axis.points, s.axis.last_hz), (200 * KHZ, 501, 1100 * MHZ))
        self.assertFalse(s.tail_gap)
        # 割り切れない: 最後の点が Stop の先（Step 未満）
        s = self.at(1000 * MHZ, 1000 * MHZ + 500 * MHZ - 100)
        self.assertGreater(s.axis.last_hz, s.stop_hz)
        self.assertFalse(s.tail_gap)
        self.assertDeviceOk(s)
        # 超えた点が上限の外: 1点減らして印
        s = self.at(MAX_HZ - (500 * MHZ - 100), MAX_HZ)
        self.assertTrue(s.tail_gap)
        self.assertEqual(s.axis.points, 501)
        self.assertLess(s.axis.last_hz, s.stop_hz)
        self.assertDeviceOk(s)

    def test_points_setting(self):
        # Span が 11999 で割り切れる範囲にして、格子の都合による点数の変化を除く
        self.cur = self.at(1000 * MHZ, 1000 * MHZ + 11999 * 10 * KHZ)
        s = self.req(points=12000)
        self.assertEqual((s.points_setting, s.axis.points), (12000, 12000))
        self.assertNotIn("points", adj_map(s))
        s = self.req(points=12001)
        self.assertEqual(s.points_setting, 12000)
        a = adj_map(s)["points"]
        self.assertEqual((a.requested, a.adopted, a.reason), (12001, 12000, "points_max"))
        s = self.req(points=1)
        self.assertEqual(s.points_setting, 2)
        self.assertEqual(adj_map(s)["points"].requested, 1)
        z = self.req(center_hz=1000 * MHZ, span_hz=0, edited=("center_hz", "span_hz"), points=1)
        a = adj_map(z)["points"]
        self.assertEqual((a.requested, a.adopted, a.reason, z.axis.points), (1, 2, "points_min", 2))

    def test_preroll_device_points(self):
        # 前置きを足して 12500 / 12501（1点の時間を変えた見積もりで前置きの点数を作る）
        span = 100 * (12000 - 1)
        for want, preroll, head in ((500, 500, 0), (501, 500, 1)):
            per_point = 0.030 / (want - 0.5)
            m = SweepModel(self.profile, lambda step, rbw, mode, p=per_point: (0.02, p))
            cur = m.apply(SweepRequest(points=12000), m.default())
            s = m.apply(SweepRequest(center_hz=5000 * MHZ, span_hz=span, edited=("center_hz", "span_hz")), cur)
            self.assertEqual(s.axis.points, 12000)
            self.assertEqual((s.preroll_points, s.transient_head_points), (preroll, head), want)
            self.assertEqual(s.axis.points + s.preroll_points, 12500)
            self.assertDeviceOk(s)

    def test_preroll_lower_limit(self):
        step = 100 * KHZ  # Span 50MHz の自動 Step。Sample = round(1000/900) = 1
        n_pre = math.ceil(0.030 / design_timing(step, 180 * KHZ)[1])
        base = MIN_HZ + n_pre * step
        for start, preroll, head in ((base - step, n_pre - 1, 1), (base, n_pre, 0), (base + step, n_pre, 0)):
            s = self.at(start, start + 50 * MHZ)
            self.assertEqual(s.axis.step_hz, step)
            self.assertEqual((s.preroll_points, s.transient_head_points), (preroll, head))
            self.assertDeviceOk(s)
        # 1Step 未満の前後（100Hz）でも下限を割らない
        s = self.at(base - 100, base - 100 + 50 * MHZ)
        self.assertEqual((s.preroll_points, s.transient_head_points), (n_pre - 1, 1))
        self.assertDeviceOk(s)

    def test_sg_range(self):
        cases = (
            (1 * MHZ - 1 * KHZ, 1 * MHZ, "sg_range"),
            (1 * MHZ, 1 * MHZ, None),
            (4000 * MHZ, 4000 * MHZ, None),
            (4000 * MHZ + 1 * KHZ, 4000 * MHZ, "sg_range"),
            (1 * MHZ + 400, 1 * MHZ, "freq_unit"),
        )
        for req, adopted, reason in cases:
            s = self.req(mode=Mode.SG, sg_hz=req)
            self.assertEqual(s.sg_hz, adopted)
            a = adj_map(s).get("sg_hz")
            self.assertEqual(a and a.reason, reason)
            if a:
                self.assertEqual(a.requested, req)


class TestPointsAndStep(Base):
    def test_grid_reduces_points(self):
        # 点数 = ceil(Span/Step)+1 が選んだ点数を超えない最小の Step（100Hz 単位）を取る。減った分は知らせる
        cur = self.at(1000 * MHZ, 1100 * MHZ, points=12000)
        self.assertEqual((cur.axis.step_hz, cur.axis.points), (8400, 11906))
        self.assertEqual(adj_map(cur)["points"].reason, "freq_unit")
        self.assertDeviceOk(cur)

    def test_step_max_forces_more_points(self):
        cur = self.req(points=500)
        s = self.at_cur(cur, 1000 * MHZ, 2000 * MHZ)
        self.assertEqual((s.axis.step_hz, s.axis.points), (1 * MHZ, 1001))
        self.assertEqual(adj_map(s)["points"].reason, "step_max")
        self.assertEqual(s.points_setting, 500)

    def test_selected_points_fine(self):
        cur = self.req(points=10001)
        s = self.at_cur(cur, 1000 * MHZ, 2000 * MHZ)
        self.assertEqual((s.axis.step_hz, s.axis.points), (100 * KHZ, 10001))

    def test_points_setting_kept_and_auto(self):
        cur = self.req(points=1001)
        s = self.at_cur(cur, 1000 * MHZ, 1100 * MHZ)
        self.assertEqual(s.points_setting, 1001)
        s = self.m.apply(SweepRequest(points=POINTS_AUTO), s)
        self.assertIsNone(s.points_setting)
        self.assertEqual(s.axis.points, 501)

    def test_points_raise_min_span(self):
        cur = self.at(1000 * MHZ, 1000 * MHZ + 49_900)
        s = self.req(cur, points=12000)
        self.assertEqual(s.span_hz, 100 * 11999)
        self.assertEqual(adj_map(s)["span_hz"].reason, "min_span")
        self.assertDeviceOk(s)

    def at_cur(self, cur, start, stop):
        return self.req(cur, start_hz=start, stop_hz=stop, edited=("start_hz", "stop_hz"))


class TestRbwAndMode(Base):
    def test_fixed_in_tg_and_restored(self):
        cur = self.at(1000 * MHZ, 1010 * MHZ, rbw_preference_hz=30 * KHZ)
        self.assertEqual(cur.rbw_hz, 30 * KHZ)
        for mode in (Mode.TG, Mode.S11, Mode.NF):
            s = self.req(cur, mode=mode)
            self.assertEqual((s.rbw_hz, s.rbw_preference_hz), (180 * KHZ, 30 * KHZ))
            self.assertEqual(adj_map(s)["rbw_hz"].reason, "rbw_fixed")
            back = self.req(s, mode=Mode.SA)
            self.assertEqual(back.rbw_hz, 30 * KHZ)

    def test_rbw_fixed_noticed_once(self):
        cur = self.at(1000 * MHZ, 1010 * MHZ, rbw_preference_hz=30 * KHZ)
        s = self.req(cur, mode=Mode.TG)
        s2 = self.req(s, center_hz=2000 * MHZ, edited=("center_hz",))
        self.assertNotIn("rbw_hz", adj_map(s2))

    def test_invalid_rbw(self):
        with self.assertRaises(ValueError):
            self.req(rbw_preference_hz=100 * KHZ)

    def test_warnings(self):
        s = self.at(1000 * MHZ, 1000 * MHZ + 200 * KHZ)
        self.assertIn("rbw180k_narrow_span", s.warnings)
        s = self.at(1000 * MHZ, 1000 * MHZ + 300 * KHZ)
        self.assertNotIn("rbw180k_narrow_span", s.warnings)
        s = self.at(1000 * MHZ, 1000 * MHZ + 500 * KHZ, rbw_preference_hz=30 * KHZ)  # Step 1kHz
        self.assertIn("rbw30k_fine_step", s.warnings)
        s = self.at(1000 * MHZ, 1001 * MHZ, rbw_preference_hz=30 * KHZ)  # Step 2kHz
        self.assertNotIn("rbw30k_fine_step", s.warnings)
        s = self.at(1000 * MHZ, 1010 * MHZ, rbw_preference_hz=15 * KHZ)
        self.assertIn("rbw15k_unstable", s.warnings)
        z = self.req(center_hz=1000 * MHZ, span_hz=0, edited=("center_hz", "span_hz"))
        self.assertNotIn("rbw180k_narrow_span", z.warnings)

    def test_sg_required_in_sg_mode(self):
        s = self.req(mode=Mode.SG)
        self.assertEqual(s.sg_hz, 1 * MHZ)


class TestEstimate(Base):
    def test_estimate_and_doubling(self):
        # GigaSt の初期値はスピード（設計F §7.3）なので、1回分の見積もりはなしにして比べる。範囲は帯の中
        s = self.req(self.at(500 * MHZ, 600 * MHZ), image_reject="off")
        first, pp = design_timing(s.axis.step_hz, s.rbw_hz)
        one = first + (s.axis.points + s.preroll_points) * pp
        self.assertAlmostEqual(s.estimated_sweep_s, one)
        self.assertEqual(s.preroll_points, math.ceil(0.030 / pp))
        # スピードは2回、正確は帯の中なので4回で1画面（設計F §7.1）
        self.assertAlmostEqual(self.req(s, image_reject="fast").estimated_sweep_s, 2 * one)
        self.assertAlmostEqual(self.req(s, image_reject="accurate").estimated_sweep_s, 4 * one)
        self.assertAlmostEqual(self.req(s, mode=Mode.NF).estimated_sweep_s, 2 * one)
        # NF は HOT/COLD の2回だけ（イメージ除去の選択によらない: 設計F §7.1）
        nf_acc = self.req(s, mode=Mode.NF, image_reject="accurate")
        self.assertAlmostEqual(nf_acc.estimated_sweep_s, 2 * one)

    def test_image_reject_default_fast_for_gigast(self):
        # イメージ除去の初期値はスピード（設計F §7.3。2026-10-02 ユーザー決定。プロファイル image_reject.default）
        self.assertEqual(self.m.default().image_reject, "fast")


class TestInvariant(Base):
    def test_random_requests_stay_in_constraints(self):
        rnd = random.Random(20261001)
        cur = self.cur
        fields = ("start_hz", "stop_hz", "center_hz", "span_hz")
        for _ in range(3000):
            kind = rnd.choice(("start", "stop", "center", "span", "cs", "drag", "points", "rbw", "mode", "lock",
                               "oneshot"))
            f = lambda: rnd.randint(-2000 * MHZ, 14000 * MHZ)
            if kind == "start":
                r = SweepRequest(start_hz=f(), edited=("start_hz",))
            elif kind == "stop":
                r = SweepRequest(stop_hz=f(), edited=("stop_hz",))
            elif kind == "center":
                r = SweepRequest(center_hz=f(), edited=("center_hz",))
            elif kind == "span":
                r = SweepRequest(span_hz=rnd.choice((0, rnd.randint(-1000, 200_000), f())), edited=("span_hz",))
            elif kind == "cs":
                r = SweepRequest(center_hz=f(), span_hz=rnd.randint(0, 3000 * MHZ), edited=("center_hz", "span_hz"))
            elif kind == "drag":
                r = SweepRequest(start_hz=f(), stop_hz=f(), edited=("start_hz", "stop_hz"))
            elif kind == "lock":
                r = SweepRequest(span_lock=rnd.choice((True, False)))
            elif kind == "oneshot":
                r = SweepRequest(start_hz=f(), stop_hz=f(), edited=("start_hz", "stop_hz"), one_shot=True)
            elif kind == "points":
                r = SweepRequest(points=rnd.choice((POINTS_AUTO, 1, 2, 3, 101, 500, 12000, 12001, rnd.randint(2, 13000))))
            elif kind == "rbw":
                r = SweepRequest(rbw_preference_hz=rnd.choice((180 * KHZ, 30 * KHZ, 15 * KHZ)))
            else:
                r = SweepRequest(mode=rnd.choice(list(Mode)), image_reject=rnd.choice(("accurate", "fast", "off")))
            prev_lock = cur.span_lock_hz
            cur = self.m.apply(r, cur)
            self.assertDeviceOk(cur)
            if prev_lock is not None and kind in ("start", "stop", "drag"):
                # 固定中に Start・Stop・ドラッグで変えても Span は固定 Span のまま（設計C §5.12）
                n = 500 if cur.points_setting is None else cur.points_setting
                expect = prev_lock if prev_lock == 0 or prev_lock >= 100 * (n - 1) else 100 * (n - 1)
                self.assertEqual(cur.span_hz, expect, kind)
                self.assertEqual(cur.span_lock_hz, prev_lock)
            if prev_lock is not None and kind == "oneshot":
                self.assertEqual(cur.span_lock_hz, prev_lock)
            if cur.span_hz:
                n = 500 if cur.points_setting is None else cur.points_setting
                self.assertGreaterEqual(cur.span_hz, 100 * (n - 1))
            if cur.points_setting is not None and cur.axis.step_hz < 1 * MHZ and cur.span_hz:
                self.assertLessEqual(cur.axis.points, cur.points_setting)
            for a in cur.adjustments:
                self.assertIn(a.field, fields + ("points", "rbw_hz", "sg_hz"))


if __name__ == "__main__":
    unittest.main()
