"""GigaSt の送受信と measure（設計B §3・§7.2・§7.3）。偽のシリアルで確かめる。"""
import threading
import time
import unittest
from collections import deque

import numpy as np
import serial

from pygigast.core.devices.gigast import protocol
from pygigast.core.devices.gigast.driver import GigaStDevice, Reconnector
from pygigast.core.devices.gigast.protocol import Limits
from pygigast.core.devices.gigast.transport import PortInfo, Transport
from pygigast.core.types import DeviceDisconnected, DeviceIdentity, InvalidSweep, Mode, SweepDiscarded

from tests.gigast_helpers import adopted

TRAILER = bytes.fromhex("48431ca5")
SHORT = Limits(first_byte_s=0.15, silence_s=0.15, total_s=0.6, estimated_s=0.05)


def payload(values) -> bytes:
    return np.asarray(values, dtype="<u2").tobytes()


class FakeSerial:
    """要求ごとに決めた応答を返す偽のポート。

    script の1要素が1回の要求への応答: bytes（全部すぐ届く）・("partial", bytes)（それだけ届いて黙る）・
    例外のインスタンス（write で投げる）・("read_error", 例外)（read で投げる）。
    """

    def __init__(self, script=(), stale: bytes = b""):
        self.script = deque(script)
        self.buf = bytearray(stale)
        self.log = []  # ("reset"|"write"|"read", ...)
        self.written = []
        self.timeout = 0.02
        self.closed = False
        self._read_error = None

    @property
    def in_waiting(self):
        return len(self.buf)

    def reset_input_buffer(self):
        self.log.append("reset")
        self.buf.clear()

    def write(self, data):
        self.log.append("write")
        self.written.append(bytes(data))
        item = self.script.popleft()
        if isinstance(item, Exception):
            raise item
        if isinstance(item, tuple) and item[0] == "read_error":
            self._read_error = item[1]
        elif isinstance(item, tuple) and item[0] == "partial":
            self.buf += item[1]
        else:
            self.buf += item
        return len(data)

    def read(self, size=1):
        if self._read_error is not None:
            e, self._read_error = self._read_error, None
            raise e
        if not self.buf:
            time.sleep(self.timeout)
            return b""
        out = bytes(self.buf[:size])
        del self.buf[:size]
        return out

    def close(self):
        self.closed = True


class ShortLimitDevice(GigaStDevice):
    """上限時間を短くした GigaStDevice（試験を速くするため）。"""

    def _limits(self, ds):
        return SHORT


def make_device(fake: FakeSerial) -> GigaStDevice:
    dev = ShortLimitDevice()
    dev._transport = Transport(fake)
    dev._identity = DeviceIdentity("gigast-v5", "TESTSERIAL", "TESTPORT")
    return dev


def response_for(a, values=None) -> bytes:
    n = a.axis.points + a.preroll_points
    if values is None:
        values = np.arange(n) % 4000
    return payload(values) + TRAILER


class TestTransport(unittest.TestCase):
    def test_exact_bytes_and_flush_before_send(self):
        fake = FakeSerial([payload(range(500)) + TRAILER], stale=b"\x00")  # 前の余計な1バイト（T6）
        t = Transport(fake)
        ex = t.exchange(b"\x55" + bytes(31), 1004, SHORT)
        self.assertEqual(len(ex.response), 1004)
        self.assertEqual(ex.response[:2], b"\x00\x00")  # 余計な 00 が先頭に混ざっていない
        self.assertEqual(ex.response[2:4], b"\x01\x00")
        self.assertEqual(fake.log[:2], ["reset", "write"])

    def test_trailer_value_is_not_used(self):
        fake = FakeSerial([payload(range(500)) + b"\xff\xff\xff\xff"])
        ex = Transport(fake).exchange(b"\x55" + bytes(31), 1004, SHORT)
        self.assertEqual(len(ex.response), 1004)

    def test_truncated_is_discarded_after_silence(self):
        # 途中が欠けて末尾だけ届いた（§6.5）: バイト数が足りないので無音の上限で捨てる
        fake = FakeSerial([("partial", payload(range(150)) + TRAILER)])
        with self.assertRaises(SweepDiscarded) as cm:
            Transport(fake).exchange(b"\x55" + bytes(31), 1004, SHORT)
        self.assertEqual(cm.exception.reason, "silence")
        self.assertEqual(cm.exception.detail["received"], 304)
        self.assertEqual(fake.log[-1], "reset")  # 打ち切ったら受信バッファを捨てる

    def test_no_response_first_byte_limit(self):
        fake = FakeSerial([("partial", b"")])
        with self.assertRaises(SweepDiscarded) as cm:
            Transport(fake).exchange(b"\x55" + bytes(31), 1004, SHORT)
        self.assertEqual(cm.exception.reason, "first_byte")

    def test_total_limit(self):
        class Trickle(FakeSerial):
            def read(self, size=1):
                time.sleep(0.02)
                return b"\x01"  # 途切れないが遅い

        fake = Trickle([b""])
        lim = Limits(first_byte_s=1, silence_s=1, total_s=0.2, estimated_s=0.1)
        with self.assertRaises(SweepDiscarded) as cm:
            Transport(fake).exchange(b"\x55" + bytes(31), 1004, lim)
        self.assertEqual(cm.exception.reason, "total")

    def test_serial_exception_is_disconnect(self):
        fake = FakeSerial([("read_error", serial.SerialException("ClearCommError failed"))])
        with self.assertRaises(DeviceDisconnected):
            Transport(fake).exchange(b"\x55" + bytes(31), 1004, SHORT)
        self.assertTrue(fake.closed)
        fake2 = FakeSerial([OSError("gone")])
        with self.assertRaises(DeviceDisconnected):
            Transport(fake2).exchange(b"\x55" + bytes(31), 1004, SHORT)


class TestMeasure(unittest.TestCase):
    def test_single_b3_1_and_preroll_cut(self):
        a = adopted(points=500, preroll=215, transient_head_points=3)
        vals = np.arange(715) + 100
        fake = FakeSerial([response_for(a, vals)])
        raw = make_device(fake).measure(a)
        self.assertEqual(len(fake.written), 1)
        self.assertEqual(fake.written[0][14], 0x08)  # OFF でも b3=1
        self.assertEqual(raw.raw.dtype, np.uint16)
        self.assertEqual(len(raw.raw), 500)
        np.testing.assert_array_equal(raw.raw, vals[215:])
        self.assertEqual(raw.encoding, "gigast_ad_u16")
        self.assertEqual(raw.transient_head_points, 3)
        self.assertIsNone(raw.raw_b3_0)
        self.assertIs(raw.adopted, a)

    def test_image_cancel_4_passes(self):
        # b3=0 → 1 → 0 → 1 の4回（設計F §6 の 1）。同じ b3 の2回が同じなら平均はその値、そのあと両側の min
        a = adopted(points=4, preroll=1, image_cancel=True)
        r0 = [9, 100, 500, 50, 300]
        r1 = [9, 200, 400, 60, 100]
        fake = FakeSerial([payload(r0) + TRAILER, payload(r1) + TRAILER] * 2)
        raw = make_device(fake).measure(a)
        self.assertEqual([w[14] for w in fake.written], [0x00, 0x08, 0x00, 0x08])
        self.assertEqual(raw.raw_passes.shape, (4, 4))
        np.testing.assert_array_equal(raw.raw_passes[0], r0[1:])
        np.testing.assert_array_equal(raw.raw_passes[1], r1[1:])
        np.testing.assert_array_equal(raw.raw_passes[2], r0[1:])
        np.testing.assert_array_equal(raw.raw_passes[3], r1[1:])
        np.testing.assert_array_equal(raw.raw, [100, 400, 50, 100])
        self.assertEqual(raw.raw.dtype, np.uint16)
        self.assertIsNone(raw.raw_b3_0)
        self.assertEqual(raw.extra["passes"], 4)
        self.assertEqual(raw.extra["pass_b3"], [0, 1, 0, 1])

    def test_image_cancel_burst_on_floor_removed(self):
        # 180kHz の床は AD 149。b3=0 の1回目に雑音の上の山（+10dB）が乗っても、2回目の低い方を採る
        a = adopted(points=3, preroll=0, image_cancel=True)
        burst = 120 + 248
        p0a, p1a, p0b, p1b = [burst, 120, 2000], [130, 125, 1980], [120, 120, 2000], [128, 126, 1980]
        fake = FakeSerial([payload(x) + TRAILER for x in (p0a, p1a, p0b, p1b)])
        raw = make_device(fake).measure(a)
        self.assertLessEqual(int(raw.raw[0]), 130)
        self.assertEqual(int(raw.raw[2]), 1980)

    def test_image_cancel_pair_discarded_if_second_fails(self):
        a = adopted(points=4, preroll=0, image_cancel=True)
        fake = FakeSerial([payload([1, 2, 3, 4]) + TRAILER, ("partial", payload([1, 2]))])
        with self.assertRaises(SweepDiscarded) as cm:
            make_device(fake).measure(a)
        self.assertEqual(cm.exception.reason, "silence")
        self.assertTrue(cm.exception.detail["pair_discarded"])
        self.assertEqual(cm.exception.detail["pass"], "b3_1a")
        self.assertEqual(len(fake.written), 2)  # 残りの2回は送らない

    def test_image_cancel_4set_discarded_if_last_fails(self):
        # 4回のどれかが捨てられたら、その組（4回）ごと捨てる（設計F §6 の 5）
        a = adopted(points=4, preroll=0, image_cancel=True)
        ok = payload([1, 2, 3, 4]) + TRAILER
        fake = FakeSerial([ok, ok, ok, ("partial", payload([1, 2]))])
        with self.assertRaises(SweepDiscarded) as cm:
            make_device(fake).measure(a)
        self.assertEqual(cm.exception.detail["pass"], "b3_1b")
        self.assertEqual(cm.exception.detail["passes"], 4)
        self.assertEqual(len(fake.written), 4)

    def test_image_cancel_pair_discarded_if_first_fails(self):
        a = adopted(points=4, preroll=0, image_cancel=True)
        fake = FakeSerial([("partial", payload([1, 2])), payload([1, 2, 3, 4]) + TRAILER])
        with self.assertRaises(SweepDiscarded):
            make_device(fake).measure(a)
        self.assertEqual(len(fake.written), 1)  # 組の片方が捨てられたら残りを送らない

    def test_nf_hot_cold(self):
        a = adopted(points=3, preroll=0, mode=Mode.NF)
        fake = FakeSerial([payload([5, 6, 7]) + TRAILER, payload([1, 2, 3]) + TRAILER])
        raw = make_device(fake).measure(a)
        self.assertEqual([w[14] for w in fake.written], [0x0C, 0x08])
        np.testing.assert_array_equal(raw.raw_hot, [5, 6, 7])
        np.testing.assert_array_equal(raw.raw_cold, [1, 2, 3])
        np.testing.assert_array_equal(raw.raw, raw.raw_hot)

    def test_cancel_before_start_sends_nothing(self):
        fake = FakeSerial([])
        ev = threading.Event()
        ev.set()
        with self.assertRaises(SweepDiscarded) as cm:
            make_device(fake).measure(adopted(), ev)
        self.assertEqual(cm.exception.reason, "cancelled")
        self.assertEqual(fake.written, [])

    def test_cancel_during_sweep_waits_for_end_then_discards(self):
        a = adopted(points=4, preroll=0, image_cancel=True)
        ev = threading.Event()

        class CancelOnWrite(FakeSerial):
            def write(self, data):
                ev.set()  # 1つ目の掃引の途中で取り消された
                return super().write(data)

        fake = CancelOnWrite([payload([1, 2, 3, 4]) + TRAILER, payload([1, 2, 3, 4]) + TRAILER])
        with self.assertRaises(SweepDiscarded) as cm:
            make_device(fake).measure(a, ev)
        self.assertEqual(cm.exception.reason, "cancelled")
        self.assertEqual(len(fake.written), 1)  # 次の要求は送らない
        self.assertEqual(fake.buf, bytearray())  # いまの掃引は最後まで受けた

    def test_invalid_sends_nothing(self):
        fake = FakeSerial([])
        with self.assertRaises(InvalidSweep):
            make_device(fake).measure(adopted(start_hz=-100_000_100))
        self.assertEqual(fake.written, [])

    def test_disconnect_then_not_open(self):
        fake = FakeSerial([("read_error", serial.SerialException("unplugged"))])
        dev = make_device(fake)
        with self.assertRaises(DeviceDisconnected):
            dev.measure(adopted())
        self.assertFalse(dev.is_open)
        with self.assertRaises(DeviceDisconnected):
            dev.measure(adopted())

    def test_real_limits_used_by_default(self):
        dev = GigaStDevice()
        ds = protocol.device_sweep(adopted(step_hz=1_000_000, points=12000, start_hz=0), dev.profile(), 1)
        lim = dev._limits(ds)
        self.assertAlmostEqual(lim.total_s, protocol.limits(dev.profile(), 1_000_000, 180_000, 12000).total_s)


class TestReconnector(unittest.TestCase):
    def test_waits_until_same_serial_returns(self):
        answers = deque([None, None, PortInfo("P2", "ABC")])
        r = Reconnector("ABC", finder=lambda s: answers.popleft() if answers else None, interval_s=0.01)
        self.assertEqual(r.wait(timeout_s=1).port, "P2")

    def test_stop_and_timeout(self):
        r = Reconnector("ABC", finder=lambda s: None, interval_s=0.01)
        self.assertIsNone(r.wait(timeout_s=0.05))
        ev = threading.Event()
        ev.set()
        self.assertIsNone(r.wait(ev))


if __name__ == "__main__":
    unittest.main()
