"""外部 IQ の送信（設計S §1）の機器の層: 押すスレッド・始める順・止める順・クレジット・途切れ・止める道（P1）。

libiio の偽物（devices/plutosdr/fake.py）を使う。実機は使わない。偽物の非循環の push は DAC の速さで押し返す。
"""
from __future__ import annotations

import threading
import time
import unittest

import numpy as np

from namibench.core.devices.plutosdr import driver as pd
from namibench.core.devices.plutosdr import ext_tx
from namibench.core.devices.plutosdr.fake import FAKE_URI, FakeIioLib, FakePluto
from namibench.core.devices.rtlsdr.driver import fft_at_rate, fft_params, make_plan
from namibench.core.sweep.model import SweepModel
from namibench.core.types import DeviceDisconnected, Mode, SweepRequest

MHZ = 1_000_000
LO = 1_575_420_000
TX_LO = "ad9361-phy/altvoltage1_out"
TX_ATTEN = "ad9361-phy/voltage0_out"
DDS = "cf-ad9361-dds-core-lpc"
BUF = "cf-ad9361-dds-core-lpc/buffer_out"
FS = 2_600_000
BLOCK = 16384


def small_profile(block=BLOCK, queue=4, prefill=2, starve=1.0, first=3.0) -> dict:
    p = pd.default_profile()
    p["ext_tx"].update({"block_samples": block, "queue_blocks": queue, "prefill_blocks": prefill,
                        "starve_s": starve, "first_data_s": first})
    return p


def tone_iq(k: int, n: int, f=250_000, fs=FS, amp=0.3) -> np.ndarray:
    t = np.arange(k, k + n)
    x = amp * np.exp(2j * np.pi * f * t / fs)
    out = np.empty(2 * n, dtype=np.int16)
    out[0::2] = np.round(x.real * 32767)
    out[1::2] = np.round(x.imag * 32767)
    return out


class _Feeder(threading.Thread):
    """クライアントのまね: 送ってよい数（credit）の中で、トーンを chunk ずつ送り続ける。pause で止める。"""

    def __init__(self, stream, chunk=4096) -> None:
        super().__init__(daemon=True)
        self.stream = stream
        self.chunk = chunk
        self.credit = stream.credit_initial
        self.cv = threading.Condition()
        self.k = 0
        self.paused = threading.Event()
        self.done = threading.Event()
        self.sent = []

    def on_msg(self, obj: dict) -> None:
        if obj.get("type") == "credit":
            with self.cv:
                self.credit += obj["add"]
                self.cv.notify_all()

    def run(self) -> None:
        while not self.done.is_set():
            if self.paused.is_set():
                time.sleep(0.005)
                continue
            with self.cv:
                if self.credit < self.chunk:
                    self.cv.wait(0.05)
                    continue
                self.credit -= self.chunk
            a = tone_iq(self.k, self.chunk)
            self.k += self.chunk
            if not self.stream.feed(a.tobytes()):
                return


class StreamCase(unittest.TestCase):
    def setUp(self):
        self.pl = FakePluto(signals=[], loss_db=40.0)
        self.lib = FakeIioLib(self.pl)
        self.dev = pd.PlutoSdrDevice(small_profile(), lib=self.lib)
        self.dev.open(FAKE_URI)
        self.events = []
        self.msgs = []
        self.ended = threading.Event()
        self.feeder = None

    def tearDown(self):
        if self.feeder is not None:
            self.feeder.done.set()
        s = self.dev.ext_stream
        if s is not None:
            s.request_stop("user")
            s.join(5)
        self.dev.close()

    def on_event(self, kind, d):
        self.events.append((kind, d))
        if kind == "end":
            self.ended.set()

    def send(self, obj):
        self.msgs.append(obj)
        if self.feeder is not None:
            self.feeder.on_msg(obj)

    def start(self, atten=-20.0, feed=True):
        self.assertEqual(self.dev.ext_tx_prepare(FS), FS)
        stream = self.dev.ext_tx_start(LO, atten, self.send, self.on_event)
        if feed:
            self.feeder = _Feeder(stream)
            self.feeder.start()
        return stream

    def wait_on(self, stream, timeout=5.0):
        end = time.monotonic() + timeout
        while stream.state != ext_tx.STATE_ON:
            self.assertLess(time.monotonic(), end, "送信が始まらない")
            time.sleep(0.01)

    def end_detail(self) -> dict:
        self.assertTrue(self.ended.wait(6), "押すスレッドが終わらない")
        return [d for k, d in self.events if k == "end"][0]

    def assert_tx_off(self):
        self.assertEqual(self.pl.tx_atten, -89.75)
        self.assertFalse(self.pl.tx_lo_powered)
        self.assertIsNone(self.pl.tx_buffer)
        self.assertIsNone(self.dev.ext_stream)
        self.assertIsNone(self.dev.tx_on)


class TestRate(StreamCase):
    def test_rate_readback_and_profile(self):
        self.assertEqual(self.dev.ext_tx_prepare(FS), FS)
        self.assertEqual(self.pl.fs, FS)
        fft = self.dev.profile()["fft"]
        self.assertEqual((fft["sample_rate_hz"], fft["grid_rate_hz"], fft["rf_bandwidth_hz"]), (FS, 4_096_000, FS))
        self.assertNotIn("passband", fft)  # 別の速さでは通過帯域の割り戻しを当てない
        self.dev.ext_tx_restore()
        self.assertEqual(self.pl.fs, 4_096_000)
        self.assertEqual(self.dev.profile()["fft"]["sample_rate_hz"], 4_096_000)
        self.assertIn("passband", self.dev.profile()["fft"])
        self.assertEqual(self.pl.get("ad9361-phy", "voltage0", True, "rf_bandwidth"), "18000000")  # 接続したときの値へ

    def test_readback_mismatch_restores(self):
        self.pl.rate_readback[FS] = FS - 1
        self.assertEqual(self.dev.ext_tx_prepare(FS), FS - 1)
        self.assertEqual(self.pl.fs, 4_096_000)
        self.assertEqual(self.dev.profile()["fft"]["sample_rate_hz"], 4_096_000)

    def test_restore_profile_even_if_device_gone(self):
        # P2: 書き込みが失敗しても機器の口のプロファイルは元へ戻す
        self.dev.ext_tx_prepare(FS)
        self.pl.unplugged = True
        self.dev._drop()  # 抜けた（コンテキストはもう使えない）
        self.dev.ext_tx_restore()
        self.assertEqual(self.dev.profile()["fft"]["sample_rate_hz"], 4_096_000)

    def test_fft_keeps_grid(self):
        prof = pd.default_profile()
        prof["fft"] = fft_at_rate(prof["fft"], FS)
        p = fft_params(prof, 1500)
        self.assertEqual((p.n, p.grid, p.fs), (2600, 1000, FS))
        for rbw in (24000, 48000, 60324, 120648):
            with self.assertRaises(Exception):
                fft_params(prof, rbw)
        # 4.096MS/s と RTL-SDR は今と同じ長さ
        base = pd.default_profile()
        self.assertEqual(fft_params(base, 1500).n, 4096)


class TestStream(StreamCase):
    def test_safe_start_order_and_prefill(self):
        n0 = len(self.pl.log)
        self.assertEqual(self.dev.ext_tx_prepare(FS), FS)
        stream = self.dev.ext_tx_start(LO, -20.0, self.send, self.on_event)
        # 始める分（2 塊）たまるまで押さない
        stream.feed(tone_iq(0, BLOCK).tobytes())
        time.sleep(0.3)
        self.assertEqual(self.pl.pushed, [])
        self.assertFalse(self.pl.tx_lo_powered)
        stream.feed(tone_iq(BLOCK, BLOCK).tobytes())
        self.wait_on(stream)
        log = [w for w in self.pl.log[n0:] if w[0] in (TX_LO, TX_ATTEN, BUF) or w[0].startswith(DDS)]
        seq = []
        for dev, name, value in log:
            if (dev, name) == (TX_ATTEN, "hardwaregain"):
                seq.append(("atten", float(value)))
            elif (dev, name) == (TX_LO, "powerdown"):
                seq.append(("lo_power", int(value)))
            elif (dev, name) == (TX_LO, "frequency"):
                seq.append(("lo", int(value)))
            elif dev.startswith(DDS) and name == "scale":
                seq.append(("dds0",))
            elif dev == BUF and name == "push":
                seq.append(("push",))
        seq = [x for i, x in enumerate(seq) if i == 0 or x != seq[i - 1]]
        first_push = seq.index(("push",))
        self.assertEqual(seq[:first_push + 1], [("atten", -89.75), ("lo_power", 1), ("dds0",), ("lo", LO), ("push",)])
        after = [x for x in seq[first_push + 1:] if x != ("push",)]
        self.assertEqual(after[:2], [("lo_power", 0), ("atten", -20.0)])
        self.assertEqual(self.dev.tx_on, "ext")
        self.assertEqual([m["type"] for m in self.msgs if m["type"] == "on"], ["on"])

    def test_bit_exact_flow_and_credit(self):
        stream = self.start()
        self.wait_on(stream)
        time.sleep(1.0)
        self.feeder.done.set()
        stream.request_stop("client")
        d = self.end_detail()
        real = d["consumed"] - d["underrun"]
        got = self.pl.pushed_samples()
        self.assertGreater(real, FS // 2)
        self.assertEqual(d["underrun"], 0)
        np.testing.assert_array_equal(got[: 2 * real], tone_iq(0, real))
        credits = [m for m in self.msgs if m["type"] == "credit"]
        self.assertEqual(credits[-1]["consumed"], d["consumed"])
        self.assertEqual(sum(m["add"] for m in credits), real)
        self.assertEqual(d["reason"], "client")
        self.assert_tx_off()

    def test_dac_pace(self):
        stream = self.start()
        self.wait_on(stream)
        t0, c0 = time.monotonic(), stream.consumed
        time.sleep(1.0)
        rate = (stream.consumed - c0) / (time.monotonic() - t0)
        self.assertLess(abs(rate / FS - 1.0), 0.1)  # 押す速さは DAC の速さ（偽物）に縛られる

    def test_underrun_fills_zero_and_starve_stops(self):
        stream = self.start()
        self.wait_on(stream)
        time.sleep(0.2)
        self.feeder.paused.set()
        time.sleep(0.5)  # 待ち行列（4 塊 ≒ 25ms）と機器の中の分より長く止める → 0 で埋める。1 秒より短いので続ける
        self.feeder.paused.clear()
        time.sleep(0.3)
        self.assertGreater(stream.underrun, 0)
        self.assertIsNotNone(stream.last_underrun_at)
        self.assertEqual(stream.state, ext_tx.STATE_ON)
        self.assertFalse(self.ended.is_set())
        self.feeder.paused.set()
        d = self.end_detail()
        self.assertEqual(d["reason"], "starved")
        self.assert_tx_off()

    def test_first_data_wait(self):
        self.dev.ext_tx_prepare(FS)
        self.dev.ext_tx_start(LO, -20.0, self.send, self.on_event)
        t0 = time.monotonic()
        d = self.end_detail()
        self.assertEqual(d["reason"], "starved")
        self.assertGreater(time.monotonic() - t0, 2.5)  # 最初の標本は first_data_s（試験では 3 秒）まで待つ
        self.assert_tx_off()

    def test_credit_exceeded(self):
        stream = self.start(feed=False)
        ok = stream.feed(np.zeros(2 * (stream.credit_initial + 2), dtype=np.int16).tobytes())
        self.assertFalse(ok)
        d = self.end_detail()
        self.assertEqual((d["reason"], d["detail"]), ("protocol_error", "credit_exceeded"))
        self.assertEqual(self.pl.pushed, [])
        self.assert_tx_off()

    def test_atten_changes_without_waiting_for_the_sweep(self):
        stream = self.start()
        self.wait_on(stream)
        with self.dev._lock:  # 掃引の錠を握ったまま（掃引の途中）
            n0 = len(self.pl.log)
            stream.set_atten(-5.0)
            end = time.monotonic() + 1.0
            while not [w for w in self.pl.log[n0:] if w[:3] == (TX_ATTEN, "hardwaregain", "-5.00")]:
                self.assertLess(time.monotonic(), end, "掃引の途中に減衰が入らない")
                time.sleep(0.005)
        self.assertEqual(self.pl.tx_atten, -5.0)

    def test_push_failure_stops(self):
        stream = self.start()
        self.wait_on(stream)
        self.pl.push_fail = True
        d = self.end_detail()
        self.assertEqual(d["reason"], "device_lost")
        self.assert_tx_off()

    def test_sg_tg_refused_while_ext(self):
        stream = self.start()
        self.wait_on(stream)
        prof = self.dev.profile()
        model = SweepModel(prof, pd.timing_fn(prof))
        tg = model.apply(SweepRequest(mode=Mode.TG, start_hz=1574 * MHZ, stop_hz=1577 * MHZ,
                                      edited=("start_hz", "stop_hz")), model.default())
        from namibench.core.types import InvalidSweep

        with self.assertRaises(InvalidSweep):
            self.dev.measure(tg)
        self.assertEqual(stream.state, ext_tx.STATE_ON)


class TestSaTogether(StreamCase):
    def sa(self):
        prof = self.dev.profile()
        model = SweepModel(prof, pd.timing_fn(prof))
        return model.apply(SweepRequest(start_hz=1574 * MHZ, stop_hz=1577 * MHZ, edited=("start_hz", "stop_hz"),
                                        rbw_preference_hz=1500), model.default())

    def test_sa_does_not_touch_ext_tx(self):
        stream = self.start()
        self.wait_on(stream)
        time.sleep(0.1)
        a = self.sa()
        n0 = len(self.pl.log)
        for _ in range(3):
            r = self.dev.measure(a)
        self.dev.on_idle()  # 掃引を止めても送信は続く
        writes = [w for w in self.pl.log[n0:] if w[0] in (TX_ATTEN, TX_LO) or w[0].startswith(DDS) and w[1] != "push"]
        self.assertEqual(writes, [])
        self.assertEqual(stream.state, ext_tx.STATE_ON)
        self.assertEqual((r.extra["tx"], r.extra["tx_lo_hz"], r.extra["tx_atten_db"]), ("ext", LO, -20.0))
        self.assertEqual(r.extra["fft_n"], 2600)
        self.assertFalse(r.extra["passband_corrected"])
        # トレースに LO + 250kHz のトーン
        f = a.axis.freqs()
        db = 10 * np.log10(np.asarray(r.raw))
        peak = f[int(np.nanargmax(db))]
        self.assertLess(abs(peak - (LO + 250_000)), 3000)


class TestStopPaths(StreamCase):
    """P1: 押すスレッドが生きている間はコンテキストを閉じない・錠を持つ道で待たない・止める順を必ず通る。"""

    def test_sa_read_fails_first(self):
        stream = self.start()
        self.wait_on(stream)
        ctx = self.dev._ctx
        self.pl.fail_reads = True
        a = TestSaTogether.sa(self)
        with self.assertRaises(DeviceDisconnected):
            self.dev.measure(a)
        d = self.end_detail()
        self.assertEqual(d["reason"], "device_lost")
        stream.join(2)
        self.assert_tx_off()  # 書き込みは通る（読みだけ失敗）ので止める順が効く
        self.assertTrue(ctx.closed)  # 押すスレッドが閉じた

    def test_disconnect_while_pushing(self):
        stream = self.start()
        self.wait_on(stream)
        ctx = self.dev._ctx
        self.pl.push_hang_s = 0.8
        self.pl.push_fail = True  # 押す途中に切れる（時間切れの分だけ止めてから失敗）
        time.sleep(0.1)
        t0 = time.monotonic()
        closer = threading.Thread(target=lambda: self.dev.close(ext_wait_s=3.0))
        closer.start()
        time.sleep(0.3)
        self.assertFalse(ctx.closed)  # 押すスレッドが押している間は閉じない
        closer.join(5)
        self.assertTrue(stream.join(3))
        self.assertTrue(ctx.closed)
        self.assertLess(time.monotonic() - t0, 4.0)
        d = self.end_detail()
        self.assertEqual(d["reason"], "device_lost")

    def test_on_idle_failure_does_not_wait(self):
        stream = self.start()
        self.wait_on(stream)
        ctx = self.dev._ctx
        self.pl.push_hang_s = 1.0
        self.pl.push_fail = True
        time.sleep(0.05)
        self.pl.unplugged = True
        with self.dev._lock:  # 掃引の途中に変えた利得（次に on_idle が書く。抜けているので失敗する道）
            self.dev.set_gain(10.0)
        cv = threading.Condition()
        with cv:  # 機器スレッドが条件の錠を持ったまま呼ぶ形
            t0 = time.monotonic()
            self.dev.on_idle()
            self.assertLess(time.monotonic() - t0, 0.2)
        self.assertIsNone(self.dev._ctx)
        self.assertFalse(ctx.closed)  # 押すスレッドが終わるまで閉じない
        self.assertTrue(stream.join(4))
        self.assertTrue(ctx.closed)

    def test_emergency_tx_off_with_stuck_push(self):
        stream = self.start()
        self.wait_on(stream)
        ctx = self.dev._ctx
        self.pl.push_hang_s = 2.0
        self.pl.push_fail = True
        time.sleep(0.1)
        n0 = len(self.pl.log)
        t0 = time.monotonic()
        self.dev.emergency_tx_off(wait_s=0.3)
        self.assertLess(time.monotonic() - t0, 1.5)
        log = self.pl.log[n0:]
        self.assertIn((TX_ATTEN, "hardwaregain", "-89.75"), log)
        self.assertIn((TX_LO, "powerdown", "1"), log)
        self.assertFalse(ctx.closed)
        self.assertTrue(stream.join(4))

    def test_close_waits_and_stops(self):
        stream = self.start()
        self.wait_on(stream)
        ctx = self.dev._ctx
        self.dev.close()
        self.assertFalse(stream.alive)
        self.assertTrue(ctx.closed)
        self.assertEqual(self.pl.tx_atten, -89.75)
        self.assertFalse(self.pl.tx_lo_powered)


class TestModelAtRate(unittest.TestCase):
    def test_rbw_usable_at_rate(self):
        prof = pd.default_profile()
        prof["fft"] = fft_at_rate(prof["fft"], FS)
        model = SweepModel(prof, pd.timing_fn(prof))
        a = model.apply(SweepRequest(start_hz=1574 * MHZ, stop_hz=1577 * MHZ, edited=("start_hz", "stop_hz"),
                                     rbw_preference_hz=48000), model.default())
        self.assertEqual(set(a.rbw_rate_unusable_hz), {24000, 48000, 60324, 120648})
        self.assertEqual(a.rbw_hz, 12000)
        self.assertEqual(a.rbw_preference_hz, 48000)
        self.assertIn("rbw_sample_rate", [x.reason for x in a.adjustments])
        self.assertNotIn(48000, a.rbw_usable_hz)
        base = pd.default_profile()
        mb = SweepModel(base, pd.timing_fn(base))
        b = mb.apply(SweepRequest(rbw_preference_hz=1500, start_hz=1574 * MHZ, stop_hz=1577 * MHZ,
                                  edited=("start_hz", "stop_hz")), mb.default())
        c = model.apply(SweepRequest(rbw_preference_hz=1500, start_hz=1574 * MHZ, stop_hz=1577 * MHZ,
                                     edited=("start_hz", "stop_hz")), model.default())
        self.assertEqual((b.axis.step_hz, b.axis.points), (c.axis.step_hz, c.axis.points))  # 点の間隔と点数は同じ
        p, pl = make_plan(prof, c.axis, c.rbw_hz)
        self.assertEqual(p.n, 2600)

    def test_wide_span_is_narrowed(self):
        prof = pd.default_profile()
        prof["fft"] = fft_at_rate(prof["fft"], FS)
        model = SweepModel(prof, pd.timing_fn(prof))
        a = model.apply(SweepRequest(start_hz=100 * MHZ, stop_hz=6000 * MHZ, edited=("start_hz", "stop_hz")),
                        model.default())
        self.assertLessEqual(a.axis.points, 200000)
        self.assertIn("span_sample_rate", [x.reason for x in a.adjustments])
        # 求めた範囲を残す（設計C §5.1）。範囲を触らない変更では引き継ぎ、範囲を触ったら置き換わる
        self.assertEqual(a.range_request, (100 * MHZ, 6000 * MHZ))
        b = model.apply(SweepRequest(rbw_preference_hz=3000), a)
        self.assertEqual(b.range_request, (100 * MHZ, 6000 * MHZ))
        c = model.apply(SweepRequest(start_hz=1574 * MHZ, stop_hz=1577 * MHZ, edited=("start_hz", "stop_hz")), b)
        self.assertIsNone(c.range_request)
        base = pd.default_profile()
        d = SweepModel(base, pd.timing_fn(base)).apply(
            SweepRequest(start_hz=100 * MHZ, stop_hz=6000 * MHZ, edited=("start_hz", "stop_hz")), model.default())
        self.assertIsNone(d.range_request)  # 元の速さでは狭めない
        # Span 固定を ON にしたら、いま見えている（狭めた）範囲を固定したものとして求めを捨てる（設計C §5.12）
        e = model.apply(SweepRequest(span_lock=True), a)
        self.assertIsNone(e.range_request)
        self.assertEqual(e.span_lock_hz, e.stop_hz - e.start_hz)

    def test_no_usable_rbw_rate_rejected(self):
        prof = pd.default_profile()
        prof["fft"] = fft_at_rate(prof["fft"], 2_083_334)
        with self.assertRaises(ValueError):
            SweepModel(prof, pd.timing_fn(prof))


if __name__ == "__main__":
    unittest.main()
