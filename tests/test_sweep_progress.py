"""掃引の進み具合（設計J §4.13）のコアの試験。

- 機器の口: 1画面の全部の要求（イメージ除去の回・帯ごとの区間・NF の HOT/COLD）を全体とし、届いた点の数で数える。
  最後の要求の点が全部届いたときにちょうど 100%。届かなくなったら数も止まる（時間で進まない）
- RTL-SDR は周波数を動かした段（位置）の数、シミュレータは GigaSt と同じ形
- 機器スレッドの間引き・制御スレッドの間引き（1秒に10回まで・最後の値を後で送る・前の世代は出さない）
- 本体: 見積もりが 1 秒を超える画面だけ出す・止まった機器では棒が止まる・止めたら隠す
"""
from __future__ import annotations

import math
import threading
import time
import unittest

import numpy as np

from namibench.core.devices.gigast import protocol
from namibench.core.devices.gigast.driver import GigaStDevice
from namibench.core.devices.gigast.protocol import Limits
from namibench.core.devices.gigast.transport import Transport
from namibench.core.devices.simulator.device import SimulatorDevice
from namibench.core.instrument import DeviceBackend, Instrument
from namibench.core.instrument_progress import MIN_INTERVAL_S, SHOW_ABOVE_S, ProgressReporter, SweepProgressMixin
from namibench.core.types import DeviceIdentity, Mode, SweepDiscarded
from tests.gigast_helpers import adopted
from tests.instrument_helpers import InstrumentCase
from tests.test_gigast_driver import TRAILER, FakeSerial, make_device, payload


class TrickleSerial(FakeSerial):
    """届いた分を一度に3バイトまでしか渡さない偽のポート（点が順に届く形: GigaSt は測った点から順に送り返す）。"""

    @property
    def in_waiting(self):
        return min(len(self.buf), 3)

    def read(self, size=1):
        return super().read(min(size, 3))


def record(dev, fake=None):
    """機器の口の進み具合を (済んだ点, 全体, そのときまでに送った要求の数) で集める。"""
    calls = []
    dev.progress_sink = lambda d, t: calls.append((d, t, len(fake.written) if fake is not None else 0))
    return calls


class DeviceCountsTest(unittest.TestCase):
    def check_monotonic(self, calls, total):
        self.assertEqual(calls[0][:2], (0, total))
        self.assertEqual(calls[-1][:2], (total, total))
        self.assertTrue(all(t == total for _d, t, _w in calls))
        dones = [d for d, _t, _w in calls]
        self.assertEqual(dones, sorted(dones))

    def test_accurate_six_passes_reach_100_only_at_the_end(self):
        # 正確で帯の境目（1100MHz）をまたぐ: 帯の中 4回＋帯の外 2回（設計F §7.2）。前置き 2 点を含めて全体を数える
        a = adopted(start_hz=1_099_960_000, step_hz=20_000, points=5, preroll=2, image_reject="accurate")
        seg0 = [7, 7, 10, 11]
        seg1 = [7, 7, 20, 21, 22]
        fake = TrickleSerial([payload(seg0) + TRAILER] * 4 + [payload(seg1) + TRAILER] * 2)
        dev = make_device(fake)
        calls = record(dev, fake)
        dev.measure(a)
        total = 4 * 4 + 2 * 5
        self.check_monotonic(calls, total)
        # 100% は6回目の要求の点が全部届いてから（それより前の要求では 100% にならない）
        first_full = next(w for d, _t, w in calls if d == total)
        self.assertEqual(first_full, 6)
        # 要求ごとの終わりで、それまでの全部の要求の点の数（1点 2 バイト。半端な1バイトは数えない）
        for k, end in enumerate((4, 8, 12, 16, 21), start=1):
            self.assertIn(end, [d for d, _t, w in calls if w == k])
            self.assertTrue(all(d <= end for d, _t, w in calls if w == k))

    def test_fast_and_off(self):
        for choice, passes in (("fast", 2), ("off", 1)):
            a = adopted(points=4, preroll=1, image_reject=choice)
            fake = TrickleSerial([payload([9, 1, 2, 3, 4]) + TRAILER] * passes)
            dev = make_device(fake)
            calls = record(dev, fake)
            dev.measure(a)
            self.check_monotonic(calls, passes * 5)

    def test_nf_hot_and_cold(self):
        a = adopted(points=3, preroll=0, mode=Mode.NF)
        fake = TrickleSerial([payload([5, 6, 7]) + TRAILER, payload([1, 2, 3]) + TRAILER])
        dev = make_device(fake)
        calls = record(dev, fake)
        dev.measure(a)
        self.check_monotonic(calls, 6)
        self.assertTrue(all(d <= 3 for d, _t, w in calls if w == 1))

    def test_stops_when_bytes_stop(self):
        # 3点だけ届いて黙った（機器が止まった）: 数は 3 で止まり、時間では進まない。上限時間で画面ごと捨てる
        a = adopted(points=8, preroll=0)
        fake = FakeSerial([("partial", payload([1, 2, 3]))])
        dev = make_device(fake)
        calls = []
        dev.progress_sink = lambda d, t: calls.append((time.monotonic(), d, t))
        t0 = time.monotonic()
        with self.assertRaises(SweepDiscarded):
            dev.measure(a)
        t_end = time.monotonic()
        self.assertEqual([(d, t) for _w, d, t in calls], [(0, 8), (3, 8)])
        # 最後の知らせは黙る前（届いたとき）。上限時間（0.15s）を待つ間は1つも来ない
        self.assertLess(calls[-1][0] - t0, 0.1)
        self.assertGreater(t_end - calls[-1][0], 0.1)

    def test_no_sink_is_fine(self):
        a = adopted(points=4, preroll=0)
        raw = make_device(FakeSerial([payload([1, 2, 3, 4]) + TRAILER])).measure(a)
        self.assertEqual(len(raw.raw), 4)

    def test_simulator_counts_the_same_way(self):
        # シミュレータも実機と同じ送受信のコードで、届いた点の数を知らせる
        a = adopted(start_hz=1_099_000_000, step_hz=20_000, points=100, preroll=5, image_reject="accurate")
        dev = SimulatorDevice(speed=0.05, seed=1)
        calls = []
        dev.progress_sink = lambda d, t: calls.append((d, t, 0))
        dev.measure(a)
        total = sum(s.passes * (s.points + s.preroll_points) for s in a.segments)
        self.assertEqual(total, 4 * 55 + 2 * 55)
        self.check_monotonic(calls, total)
        self.assertGreater(len(calls), 10)  # 点が順に届く（一度にまとめて来ない）

    def test_rtlsdr_counts_positions(self):
        # RTL-SDR は周波数を動かした段（位置）の数。本体が使う包み（RtlSdrInstrumentDevice）を通しても同じ
        from namibench.core.instrument_rtlsdr import open_rtlsdr_device
        from tests.test_rtlsdr_driver import MHZ, Base

        b = Base()
        b.setUp()
        dev = open_rtlsdr_device(b.lib, port="rtlsdr:0", profile=b.profile)
        s = b.sweep(76 * MHZ, 95 * MHZ, 1500)
        calls = []
        dev.progress_sink = lambda d, t: calls.append((d, t, 0))
        r = dev.measure(s)
        n = r.extra["positions"]
        self.assertEqual(n, 25)
        self.assertEqual([d for d, _t, _w in calls], list(range(n + 1)))
        self.check_monotonic(calls, n)
        dev.close()


class FakeClock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


class ReporterTest(unittest.TestCase):
    def test_short_sweeps_keep_nothing(self):
        posts = []
        r = ProgressReporter(posts.append)
        a = adopted(points=500)
        self.assertLessEqual(a.estimated_sweep_s, SHOW_ABOVE_S)
        r.begin(1, a)
        for d in range(0, 501, 10):
            r(d, 500)
        self.assertEqual((posts, r.latest, r.active), ([], None, False))

    def test_long_sweep_keeps_latest_and_wakes_once(self):
        posts = []
        r = ProgressReporter(posts.append)
        a = adopted(points=12000, step_hz=1_000_000, start_hz=0)
        self.assertGreater(a.estimated_sweep_s, SHOW_ABOVE_S)
        r.begin(7, a)
        for d in range(0, 1001):
            r(d, 1000)
        self.assertEqual(posts, [("sweep_progress",)])  # 始まりに1回起こすだけ（量ごとには送らない）
        self.assertEqual(r.latest, (7, 1000, 1000))
        r(2000, 1000)
        self.assertEqual(r.latest, (7, 1000, 1000))  # 全体を超えない
        r.end()
        self.assertEqual((r.latest, r.active), (None, False))


class _Worker:
    def __init__(self):
        self.progress = ProgressReporter(lambda item: None)


class _Ctl(SweepProgressMixin):
    """制御スレッドの側だけを持つ偽の本体（機器スレッドの量は _worker.progress に置く）。"""

    def __init__(self, clock):
        self._gen = 1
        self.sent = []
        self._worker = _Worker()
        self._init_sweep_progress(clock)

    def _emit(self, kind, payload):
        self.sent.append((self._sp_clock(), kind, payload))


class ControlThrottleTest(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.c = _Ctl(self.clock)
        self.rep = self.c._worker.progress
        self.rep.begin(1, adopted(points=12000, step_hz=1_000_000, start_hz=0))

    def payloads(self):
        return [p for _t, _k, p in self.c.sent]

    def put(self, done, total=1000, gen=1):
        self.rep.latest = (gen, done, total)
        self.c._sweep_progress_poll()

    def test_at_most_ten_per_second_and_latest_value_later(self):
        # 機器スレッドが 0.7ms ごとに量を置き、制御スレッドが毎回読む（最も多く読む場合）
        t0 = self.clock.t
        for d in range(0, 1001):
            self.put(d)
            self.clock.t += 0.0007
        times = [t for t, _k, _p in self.c.sent]
        self.assertTrue(all(b - a >= MIN_INTERVAL_S - 1e-9 for a, b in zip(times, times[1:])))
        # 0.1 秒おき（始まりを含む）。0.7 秒の間に 8 回まで
        self.assertLessEqual(len(times), math.floor((self.clock.t - t0) / MIN_INTERVAL_S) + 1)
        # 間に来た値は捨てず、最後の値（1000）を間隔が明けてから送る
        self.assertNotEqual(self.payloads()[-1]["done"], 1000)
        wait = self.c._sweep_progress_wait()
        self.assertLess(wait, MIN_INTERVAL_S)
        self.clock.t += wait
        self.c._sweep_progress_poll()
        self.assertEqual(self.payloads()[-1], {"show": True, "done": 1000, "total": 1000})
        # 出す画面を測っている間は、量が来なくても 0.1 秒ごとに読みに行く（止まった機器の最後の量を逃さない）
        self.assertEqual(self.c._sweep_progress_wait(), MIN_INTERVAL_S)
        self.rep.end()
        self.assertIsNone(self.c._sweep_progress_wait())

    def test_stalled_value_is_sent_without_new_values(self):
        self.put(0)
        self.clock.t += 0.01
        self.rep.latest = (1, 400, 1000)  # 届いて、そのあと機器が黙った（量はもう来ない）
        self.c._sweep_progress_poll()  # 間隔の内: 後で送る
        self.assertEqual(len(self.c.sent), 1)
        self.clock.t += self.c._sweep_progress_wait()
        self.c._sweep_progress_poll()
        self.assertEqual(self.payloads()[-1], {"show": True, "done": 400, "total": 1000})
        for _ in range(20):  # 黙っている間は何も送らない（時間では進まない）
            self.clock.t += MIN_INTERVAL_S
            self.c._sweep_progress_poll()
        self.assertEqual(len(self.c.sent), 2)

    def test_older_generation_is_not_shown(self):
        self.put(10, 100, gen=0)
        self.assertEqual(self.c.sent, [])

    def test_screen_done_is_100_percent(self):
        self.put(930)
        self.clock.t += 1
        self.c._sweep_progress_screen_done(1)
        self.assertEqual(self.payloads()[-1], {"show": True, "done": 1000, "total": 1000})
        self.clock.t += 1
        self.c._sweep_progress_screen_done(0)  # 前の世代の結果では何もしない
        self.assertEqual(len(self.c.sent), 2)

    def sends(self):
        """(送った時刻, 済んだ量か "hide") の並び。"""
        return [(t, p["done"] if p["show"] else "hide") for t, _k, p in self.c.sent]

    def check_100_kept(self, after):
        """画面の更新の 100% はすぐ送り、次の値（after）はその 0.1 秒後より前に送らない。"""
        s = self.sends()
        i = [d for _t, d in s].index(1000)
        self.assertEqual([d for _t, d in s[i:]], [1000, after])
        self.assertGreaterEqual(s[i + 1][0] - s[i][0], MIN_INTERVAL_S - 1e-9)

    def test_screen_done_soon_after_a_send_is_100_and_next_sweep_waits(self):
        # 連続: 直前の知らせから 0.1 秒以内に画面が更新され、すぐ次の掃引の 0% が来る（2026-10-07 反証で見つかった形:
        # 100% が後回しになり、0% に置き換わって 100% が出なかった）
        self.put(930)
        self.clock.t += 0.05
        self.c._sweep_progress_screen_done(1)
        self.assertEqual(self.payloads()[-1]["done"], 1000)  # 間隔の内でもすぐ送る
        for _ in range(9):
            self.clock.t += 0.01
            self.put(0 if self.clock.t < 100.1 else 5)
        self.clock.t += self.c._sweep_progress_wait()
        self.c._sweep_progress_poll()
        self.check_100_kept(5)

    def test_screen_done_then_hide_waits(self):
        # 単発の終わり・機器が仕事を持たない・ステップ掃引の段の変わり目: 100% の直後に隠す
        self.put(930)
        self.clock.t += 0.05
        self.c._sweep_progress_screen_done(1)
        self.clock.t += 0.001
        self.c._sweep_progress_hide()
        self.clock.t += 0.05
        self.c._sweep_progress_poll()
        self.assertEqual(self.payloads()[-1]["done"], 1000)
        self.clock.t += self.c._sweep_progress_wait()
        self.c._sweep_progress_poll()
        self.check_100_kept("hide")

    def test_next_sweep_read_before_the_screen_result_waits_for_it(self):
        # 機器スレッドが次の掃引を始めて 0 を置いたのを、画面の更新（掃引の結果）を受ける前に読んだ: 0 は出さず、
        # 結果を受けてから 100% → 0
        self.put(930)
        self.clock.t += 0.2
        self.put(0)
        self.assertEqual(self.payloads()[-1]["done"], 930)
        self.c._sweep_progress_screen_done(1)
        self.clock.t += self.c._sweep_progress_wait()
        self.c._sweep_progress_poll()
        self.assertEqual([d for _t, d in self.sends()], [930, 1000, 0])

    def test_discarded_screen_starts_again_from_zero(self):
        # 上限時間で画面ごと捨てた: 100% にせず、次の掃引の 0 から出す
        self.put(400)
        self.clock.t += 0.2
        self.c._sweep_progress_screen_dropped()
        self.put(0)
        self.assertEqual([d for _t, d in self.sends()], [400, 0])

    def test_hide_replaces_pending_and_is_sent_once(self):
        self.put(10, 100)
        self.clock.t += 0.01
        self.put(20, 100)  # 間隔の内: 後で送る
        self.c._sweep_progress_hide()  # 隠すが後から来た: 20% は送らない
        self.clock.t += MIN_INTERVAL_S
        self.c._sweep_progress_poll()
        self.c._sweep_progress_hide()
        self.clock.t += 1
        self.c._sweep_progress_hide()
        self.c._sweep_progress_poll()
        self.assertEqual(self.payloads(), [{"show": True, "done": 10, "total": 100}, {"show": False}])

    def test_same_value_not_sent_twice(self):
        self.put(10, 100)
        self.clock.t += 1
        self.put(10, 100)
        self.c._sweep_progress_poll()
        self.assertEqual(len(self.c.sent), 1)


class SweepProgressEventsTest(InstrumentCase):
    """本体: シミュレータ（見積もりの 1/20 の時間）で、イベント sweep_progress を確かめる。"""

    def progress_events(self):
        with self.cv:
            return list(self.sp)

    def make_instrument(self):
        inst = super().make_instrument()
        self.sp = []  # (知らせ, 届いた時刻)

        def on_progress(kind, payload):
            if kind == "sweep_progress":
                with self.cv:
                    self.sp.append((payload, time.monotonic()))

        inst.subscribe(on_progress)
        return inst

    def test_long_screen_shown_then_hidden(self):
        # 50MHz〜2GHz・正確: 帯の中 4回＋帯の外 2回（見積もり 5.7 秒。実際は 1/20）
        self.connect(50_000_000, 2_000_000_000)
        self.call("set_sweep", image_reject="accurate")
        st = self.call("get_state")
        segs = self.inst._adopted.segments
        self.assertGreater(st["sweep"]["estimated_sweep_s"], SHOW_ABOVE_S)
        total = sum(s.passes * (s.points + s.preroll_points) for s in segs)
        self.assertEqual(sorted(s.passes for s in segs), [2, 4])
        self.single()
        self.wait_for(lambda k, p: k == "sweep_progress" and not p["show"])
        ev = self.progress_events()
        shown = [p for p, _t in ev if p["show"]]
        self.assertTrue(shown)
        self.assertTrue(all(p["total"] == total for p in shown))
        dones = [p["done"] for p in shown]
        self.assertEqual(dones, sorted(dones))
        self.assertLessEqual(max(dones), total)
        # 0.1 秒おき。画面の更新の 100% だけは間隔によらずすぐ送る（その次は 0.1 秒待つ）
        self.assertEqual(dones[-1], total)
        gaps = [(tb - ta, b) for (_a, ta), (b, tb) in zip(ev, ev[1:])]
        self.assertTrue(all(g >= MIN_INTERVAL_S - 0.005 for g, b in gaps if not (b["show"] and b["done"] == b["total"])),
                        gaps)
        self.assertFalse(ev[-1][0]["show"])
        self.assertNotIn("sweep_progress", st)  # 状態（PNG の元）には入れない

    def check_every_screen_ends_at_100(self, ev, screens, stopped_mid_screen=False):
        """画面ごとに 100% を出し、次の掃引の値・隠すはその 0.1 秒後より前に来ない（設計J §4.13「画面更新までを 100%」）。

        stopped_mid_screen: 最後の隠すは止める操作（途中の画面を捨てた）なので、その前は 100% でなくてよい。
        """
        seq = [p["done"] if p["show"] else "hide" for p, _t in ev]
        full = [p for p, _t in ev if p["show"] and p["done"] == p["total"]]
        self.assertEqual(len(full), screens, seq)
        for i, ((a, ta), (b, tb)) in enumerate(zip(ev, ev[1:])):
            if not a["show"]:
                continue
            last_hide = i + 1 == len(ev) - 1 and not b["show"]
            if (b["show"] and b["done"] < a["done"]) or (not b["show"] and not (last_hide and stopped_mid_screen)):
                self.assertEqual(a["done"], a["total"], seq)  # 画面の区切り（次の掃引が 0 から・隠す）の前は 100%
            if a["done"] == a["total"]:
                self.assertGreaterEqual(tb - ta, MIN_INTERVAL_S - 0.005, seq)

    def long_screens(self):
        # 50MHz〜2GHz・正確（見積もり 5.7 秒）。シミュレータは見積もりの 1/10 の時間（1画面 約 0.57 秒）
        self.inst._backends["simulator"].speed = 0.1
        self.connect(50_000_000, 2_000_000_000)
        self.call("set_sweep", image_reject="accurate")
        self.assertGreater(self.call("get_state")["sweep"]["estimated_sweep_s"], SHOW_ABOVE_S)

    def test_single_every_screen_reaches_100(self):
        self.long_screens()
        with self.cv:
            self.sp.clear()
        self.clear_events()
        self.call("run", mode="single", count=3)
        self.wait_for(lambda k, p: k == "state" and p["run"]["state"] == "stopped"
                      and p["run"]["last_stop_reason"] == "single_done")
        self.wait_for(lambda k, p: k == "sweep_progress" and not p["show"])
        ev = self.progress_events()
        self.assertFalse(ev[-1][0]["show"])
        self.check_every_screen_ends_at_100(ev, 3)

    def test_continuous_every_screen_reaches_100(self):
        self.long_screens()
        n0 = self.call("get_state")["run"]["sweep_count"]
        with self.cv:
            self.sp.clear()
        self.call("run", mode="continuous")
        end = time.monotonic() + 20
        while self.call("get_state")["run"]["sweep_count"] < n0 + 4:
            self.assertLess(time.monotonic(), end)
            time.sleep(0.02)
        self.call("stop")
        self.wait_for(lambda k, p: k == "state" and p["run"]["state"] == "stopped")
        time.sleep(0.3)
        screens = self.call("get_state")["run"]["sweep_count"] - n0
        ev = self.progress_events()
        self.assertFalse(ev[-1][0]["show"])
        self.check_every_screen_ends_at_100(ev, screens, stopped_mid_screen=True)

    def test_short_screen_not_shown(self):
        self.connect(100_000_000, 200_000_000)
        self.assertLessEqual(self.call("get_state")["sweep"]["estimated_sweep_s"], SHOW_ABOVE_S)
        self.single()
        time.sleep(0.3)
        self.assertEqual([p for p, _t in self.progress_events() if p["show"]], [])

    def test_step_sweep_counts_each_device_sweep(self):
        # SG のステップ掃引（設計L §1.3）: 段ごとの掃引も1回ずつ画面を更新するので、1回の掃引を 100% とする。機器へ
        # 渡す条件はイメージ除去なし（1回）なので、利用者の選択（正確: 6回）の点の数で数えない。段の進み具合は別の
        # 長い処理の知らせ（task "step_sweep"）が出す
        self.inst._backends["simulator"].speed = 0.2
        self.connect(50_000_000, 2_000_000_000)
        self.call("set_sweep", image_reject="accurate")
        self.call("set_mode", mode="sg", sg_hz=500_000_000)
        accurate_total = sum(s.passes * (s.points + s.preroll_points) for s in self.inst._adopted.segments)
        sent = []
        orig = self.inst._step_push_job

        def spy():
            job = self.inst._step_job
            if job is not None and job.adopted is not None:
                sent.append(job.adopted)
            return orig()

        self.inst._step_push_job = spy
        self.call("set_step_sweep", start_hz=400_000_000, stop_hz=450_000_000, step_hz=50_000_000, per_step=1,
                  combine="max", trace=2)
        self.clear_events()
        with self.cv:
            self.sp.clear()
        r = self.call("step_sweep_start")
        self.wait_task(r["task_id"])
        self.wait_for(lambda k, p: k == "sweep_progress" and not p["show"])
        self.assertTrue(sent)
        totals = {sum(s.passes * (s.points + s.preroll_points) for s in a.segments) for a in sent}
        self.assertEqual({a.image_reject_used for a in sent}, {"off"})
        self.assertGreater(min(a.estimated_sweep_s for a in sent), SHOW_ABOVE_S)
        shown = [p for p, _t in self.progress_events() if p["show"]]
        self.assertTrue(shown)
        self.assertEqual({p["total"] for p in shown}, totals)
        self.assertNotIn(accurate_total, totals)
        self.assertTrue(all(p["done"] <= p["total"] for p in shown))
        # 2つの段の掃引が、どちらも 100% で終わる（段の変わり目・終わりの隠すに置き換わらない）
        self.check_every_screen_ends_at_100(self.progress_events(), 2)


class StallPort:
    """要求ごとに応答の 40% だけを届けて黙る偽のポート（掃引の途中で止まった機器）。"""

    def __init__(self):
        self.buf = bytearray()
        self.timeout = 0.02
        self.lock = threading.Lock()

    @property
    def in_waiting(self):
        with self.lock:
            return len(self.buf)

    def reset_input_buffer(self):
        with self.lock:
            self.buf.clear()

    def write(self, data):
        ds = protocol.unpack(bytes(data))
        n = (ds.points * 2) * 2 // 5
        with self.lock:
            self.buf += np.full(n // 2, 300, dtype="<u2").tobytes()
        return len(data)

    def read(self, size=1):
        with self.lock:
            if self.buf:
                out = bytes(self.buf[:size])
                del self.buf[:size]
                return out
        time.sleep(self.timeout)
        return b""

    def close(self):
        pass


class StallDevice(GigaStDevice):
    def _limits(self, ds):
        # 黙ってから 1.5 秒で上限（試験を長くしない。それまでの間に棒が進まないことを見る）
        return Limits(first_byte_s=1.5, silence_s=1.5, total_s=5.0, estimated_s=1.0)


class StallBackend(DeviceBackend):
    def discover(self):
        return [{"port": "STALL", "serial": "STALL1", "description": "stall"}]

    def probe(self, port):
        return True

    def open(self, serial, port):
        dev = StallDevice()
        dev._transport = Transport(StallPort())
        dev._identity = DeviceIdentity("gigast-v5", "STALL1", "STALL")
        return dev


class StalledDeviceTest(InstrumentCase):
    def make_instrument(self):
        inst = Instrument({"stall": StallBackend()}, profile=self.profile,
                          data_root=self.root / "data", cal_root=self.root / "cal")
        self.events = []
        self.cv = threading.Condition()

        def on_event(kind, payload):
            with self.cv:
                self.events.append((kind, payload))
                self.cv.notify_all()

        inst.subscribe(on_event)
        return inst

    def test_bar_stops_when_device_stops(self):
        self.call("set_sweep", start_hz=50_000_000, stop_hz=2_000_000_000, image_reject="off")
        self.call("connect", backend="stall", serial="STALL1")
        self.assertGreater(self.call("get_state")["sweep"]["estimated_sweep_s"], SHOW_ABOVE_S)
        self.call("run", mode="continuous")
        first = self.wait_for(lambda k, p: k == "sweep_progress" and p["show"] and p["done"] > 0)
        time.sleep(0.8)  # 上限時間（1.5 秒）より前
        with self.cv:
            later = [p for k, p in self.events if k == "sweep_progress"]
        # 40% で止まったまま（時間では進まない）。隠しもしない（止まったことが見える）
        self.assertTrue(later[-1]["show"])
        self.assertEqual({p["done"] for p in later if p["show"] and p["done"] > 0}, {first["done"]})
        # 1回の要求（イメージ除去なし）の 40% のバイト
        self.assertEqual(first["done"], (first["total"] * 2 * 2 // 5) // 2)
        self.call("stop")


if __name__ == "__main__":
    unittest.main()
