"""rtl_tcp の出口（設計Q §4.3・§6.7）の試験。この PC の中（127.0.0.1）の空いたポートだけを使う。"""
from __future__ import annotations

import socket
import struct
import threading
import time
import unittest

import numpy as np

from namibench.core.rtltcp import (
    BIND_LOCAL,
    TUNER_R820T,
    EnvelopeToIQ,
    RtlTcpServer,
    dbm_to_amplitude,
    decode_command,
    encode_header,
    iq_to_u8,
)


def _wait(cond, timeout=3.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.01)
    return cond()


def _recv_exact(s: socket.socket, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        d = s.recv(n - len(buf))
        if not d:
            break
        buf += d
    return buf


class FormatTests(unittest.TestCase):
    def test_header(self):
        h = encode_header(TUNER_R820T, 29)
        self.assertEqual(h, b"RTL0" + bytes([0, 0, 0, 5, 0, 0, 0, 29]))

    def test_commands(self):
        self.assertEqual(decode_command(bytes([0x01]) + struct.pack(">I", 100_000_000)), ("set_freq", 100_000_000))
        self.assertEqual(decode_command(bytes([0x02]) + struct.pack(">I", 2_048_000)), ("set_sample_rate", 2_048_000))
        self.assertEqual(decode_command(bytes([0x04]) + struct.pack(">I", 297)), ("set_gain", 297))
        # 周波数のずれは符号付き
        self.assertEqual(decode_command(bytes([0x05]) + struct.pack(">i", -57)), ("set_freq_correction", -57))
        # IF の利得は (段, 符号付きの利得)
        self.assertEqual(
            decode_command(bytes([0x06]) + struct.pack(">I", (3 << 16) | (0xFFFF & -30))), ("set_if_gain", (3, -30))
        )
        self.assertEqual(decode_command(bytes([0x0E, 0, 0, 0, 1])), ("set_bias_tee", 1))
        self.assertEqual(decode_command(bytes([0x7F, 0, 0, 0, 1]))[0], "unknown_0x7f")
        names = [decode_command(bytes([c, 0, 0, 0, 0]))[0] for c in range(1, 0x0F)]
        self.assertEqual(len(set(names)), 14)
        self.assertFalse(any(n.startswith("unknown") for n in names))

    def test_iq_to_u8(self):
        b = iq_to_u8(np.array([0, 1 - 1j, 2 + 0.5j], dtype=np.complex64))
        # I・Q の順。0 → 128、+1 → 255、−1 → 0、振り切れの外は切る
        self.assertEqual(list(b), [128, 128, 255, 0, 255, 191])


class LoopbackTests(unittest.TestCase):
    def setUp(self):
        self.commands: list = []
        self.counts: list = []
        self.server = RtlTcpServer(
            on_command=lambda n, v: self.commands.append((n, v)),
            on_clients_changed=self.counts.append,
            tuner_type=TUNER_R820T,
            gain_count=29,
        )
        self.port = self.server.start(0, BIND_LOCAL)

    def tearDown(self):
        self.server.stop()

    def _connect(self) -> socket.socket:
        s = socket.create_connection(("127.0.0.1", self.port), timeout=3)
        self.addCleanup(s.close)
        return s

    def test_binds_local_only(self):
        self.assertTrue(self.server.running)
        self.assertEqual(self.server._listen.getsockname()[0], "127.0.0.1")

    def test_header_iq_and_commands(self):
        s = self._connect()
        self.assertEqual(_recv_exact(s, 12), encode_header(TUNER_R820T, 29))
        self.assertTrue(_wait(lambda: self.server.client_count == 1))
        iq = np.array([0.5 + 0.25j, -0.5 - 1j], dtype=np.complex64)
        self.server.push_iq(iq, 2_048_000)
        self.assertEqual(_recv_exact(s, 4), iq_to_u8(iq))
        self.assertEqual(self.server.sample_rate, 2_048_000)
        # 命令は 5 バイトずつ。分けて届いても・まとめて届いても読む
        c1 = bytes([0x01]) + struct.pack(">I", 433_920_000)
        c2 = bytes([0x02]) + struct.pack(">I", 1_024_000)
        c3 = bytes([0x04]) + struct.pack(">I", 100)
        s.sendall(c1[:2])
        time.sleep(0.05)
        s.sendall(c1[2:] + c2 + c3)
        self.assertTrue(_wait(lambda: len(self.commands) == 3))
        self.assertEqual(
            self.commands, [("set_freq", 433_920_000), ("set_sample_rate", 1_024_000), ("set_gain", 100)]
        )

    def test_client_count_and_disconnect(self):
        a = self._connect()
        b = self._connect()
        _recv_exact(a, 12)
        _recv_exact(b, 12)
        self.assertTrue(_wait(lambda: self.server.client_count == 2))
        a.close()
        self.assertTrue(_wait(lambda: self.server.client_count == 1))
        self.server.stop()
        self.assertEqual(self.server.client_count, 0)
        self.assertFalse(self.server.running)
        self.assertEqual(self.counts[-1], 0)

    def test_callback_error_keeps_connection(self):
        def bad(n, v):
            raise RuntimeError("x")

        self.server.on_command = bad
        s = self._connect()
        _recv_exact(s, 12)
        with self.assertLogs("namibench.core.rtltcp.server", level="ERROR"):
            s.sendall(bytes([0x01, 0, 0, 0, 1]))
            time.sleep(0.1)
        self.assertEqual(self.server.client_count, 1)

    def test_slow_client_does_not_block(self):
        """読まない相手がいても push_iq はすぐ戻り、古い塊から捨てて数える。"""
        self.server.max_queue_bytes = 64 * 1024
        s = self._connect()
        s.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)
        self.assertTrue(_wait(lambda: self.server.client_count == 1))
        block = np.zeros(32 * 1024, dtype=np.complex64)  # 64KiB
        t0 = time.perf_counter()
        for _ in range(200):  # 12.8MB（OS の送りのバッファより十分大きい）
            self.server.push_iq(block, 2_048_000)
        self.assertLess(time.perf_counter() - t0, 2.0)
        self.assertTrue(_wait(lambda: self.server.dropped_chunks > 0))
        # 先頭の 12 バイトは捨てずに最初に届く
        self.assertEqual(_recv_exact(s, 12), encode_header(TUNER_R820T, 29))

    def test_port_in_use_raises(self):
        other = RtlTcpServer()
        with self.assertRaises(OSError):
            other.start(self.port, BIND_LOCAL)
        self.assertFalse(other.running)

    def test_bad_bind(self):
        with self.assertRaises(ValueError):
            RtlTcpServer().start(0, "everywhere")


class EnvelopeTests(unittest.TestCase):
    def test_amplitude(self):
        a = dbm_to_amplitude(np.array([0.0, -20.0, 10.0, np.nan]), ref_dbm=0.0)
        np.testing.assert_allclose(a, [1.0, 0.1, 1.0, 0.0])

    def test_resample_and_q_zero(self):
        e = EnvelopeToIQ(out_rate=100_000, ref_dbm=-20.0)
        dbm = np.full(1000, -40.0)  # 振幅 0.1
        iq = e.push(dbm, t_start=10.0, point_rate=10_000.0)  # 0.1 秒分
        self.assertAlmostEqual(iq.size, 10_000, delta=10)
        self.assertEqual(iq.dtype, np.complex64)
        np.testing.assert_allclose(iq.real, 0.1, rtol=1e-5)
        self.assertTrue(np.all(iq.imag == 0))

    def test_continuous_blocks_have_no_gap(self):
        e = EnvelopeToIQ(out_rate=48_000, ref_dbm=0.0)
        rate = 10_000.0
        n = 500
        out = []
        for i in range(4):
            out.append(e.push(np.full(n, -6.0), t_start=i * n / rate, point_rate=rate))
        iq = np.concatenate(out)
        self.assertAlmostEqual(iq.size, 4 * n / rate * 48_000, delta=10)
        self.assertTrue(np.all(iq.real > 0.4))
        self.assertEqual(e.filled_samples, 0)

    def test_gap_filled_with_zero(self):
        e = EnvelopeToIQ(out_rate=48_000, ref_dbm=0.0)
        rate = 10_000.0
        a = e.push(np.full(1000, 0.0), t_start=0.0, point_rate=rate)  # 0〜0.1 秒
        b = e.push(np.full(1000, 0.0), t_start=0.15, point_rate=rate)  # 0.05 秒の途切れ
        self.assertTrue(np.all(a.real == 1.0))
        zeros = np.count_nonzero(b.real == 0.0)
        self.assertAlmostEqual(zeros, 0.05 * 48_000, delta=10)
        self.assertEqual(e.filled_samples, zeros)
        self.assertAlmostEqual(a.size + b.size, (0.15 + 0.0999) * 48_000, delta=10)

    def test_fill_until_and_late_block(self):
        e = EnvelopeToIQ(out_rate=1_000, ref_dbm=0.0)
        e.push(np.zeros(10), t_start=0.0, point_rate=1_000.0)
        z = e.fill_until(0.5)
        self.assertAlmostEqual(z.size, 490, delta=2)
        self.assertTrue(np.all(z == 0))
        # 出し終えた時刻より前の塊は捨てる
        self.assertEqual(e.push(np.zeros(10), t_start=0.2, point_rate=1_000.0).size, 0)

    def test_into_server_bytes(self):
        """GigaSt の振幅の IQ は、8bit で I が 128〜255、Q が 128 になる。"""
        e = EnvelopeToIQ(out_rate=2_048_000, ref_dbm=0.0)
        iq = e.push(np.linspace(-60, 0, 100), 0.0, 10_000.0)
        b = np.frombuffer(iq_to_u8(iq), dtype=np.uint8)
        self.assertTrue(np.all(b[1::2] == 128))
        self.assertGreaterEqual(b[0::2].min(), 128)
        self.assertEqual(b[0::2].max(), 255)


if __name__ == "__main__":
    unittest.main()
