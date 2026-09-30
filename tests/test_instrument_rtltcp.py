"""計測器の本体の rtl_tcp の出口（設計Q §4.3・§6.7）をシミュレータで確かめる。"""
import socket
import struct
import time
import unittest

from pygigast.core.rtltcp import encode_header
from tests.instrument_helpers import InstrumentCase


def free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def recv_exact(sock: socket.socket, n: int, timeout: float = 10.0) -> bytes:
    sock.settimeout(timeout)
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("closed")
        buf += chunk
    return buf


class RtlTcpInstrumentTest(InstrumentCase):
    def state(self):
        return self.call("get_state")["rtltcp"]

    def start(self, port=None):
        port = port or free_port()
        res = self.call("rtltcp_set", enabled=True, port=port, bind="local")
        return port, res["rtltcp"]

    def test_default_off(self):
        st = self.state()
        self.assertEqual(st["enabled"], False)
        self.assertEqual(st["running"], False)
        self.assertEqual(st["port"], 1234)
        self.assertEqual(st["bind"], "local")
        self.assertEqual(st["clients"], 0)
        self.assertIsNone(st["error"])

    def test_start_stop_by_command_and_persist(self):
        self.clear_events()
        port, st = self.start()
        self.assertTrue(st["running"])
        self.assertEqual(st["port"], port)
        self.wait_for(lambda k, p: k == "notice" and p["code"] == "rtltcp_started"
                      and p["detail"]["port"] == port)
        st = self.call("rtltcp_set", enabled=False)["rtltcp"]
        self.assertFalse(st["running"])
        self.assertFalse(st["enabled"])
        self.wait_for(lambda k, p: k == "notice" and p["code"] == "rtltcp_stopped")
        # 止めたらつながらない
        with self.assertRaises(OSError):
            socket.create_connection(("127.0.0.1", port), timeout=1).close()
        # 設定は settings.json に残る
        self.assertEqual(self.inst._settings.get("rtltcp"), {"enabled": False, "port": port, "bind": "local"})

    def test_bad_arguments(self):
        for kw, code in (({"port": 0}, "bad_value"), ({"port": "1234"}, "not_integer"),
                         ({"bind": "any"}, "bad_value"), ({"enabled": 1}, "bad_value")):
            with self.assertRaises(Exception) as cm:
                self.call("rtltcp_set", **kw)
            self.assertEqual(cm.exception.code, code, kw)

    def test_starts_at_app_start_when_enabled(self):
        port, _ = self.start()
        self.inst.shutdown()
        # 終了で止まっている
        with self.assertRaises(OSError):
            socket.create_connection(("127.0.0.1", port), timeout=1).close()
        self.inst = self.make_instrument()
        end = time.monotonic() + 10
        while not self.state()["running"]:
            self.assertLess(time.monotonic(), end)
            time.sleep(0.05)
        self.assertEqual(self.state()["port"], port)

    def test_port_in_use(self):
        blocker = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            blocker.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        blocker.bind(("127.0.0.1", 0))
        blocker.listen(1)
        port = blocker.getsockname()[1]
        try:
            self.clear_events()
            st = self.call("rtltcp_set", enabled=True, port=port)["rtltcp"]
            self.assertFalse(st["running"])
            self.assertTrue(st["enabled"])
            self.assertEqual(st["error"]["code"], "port_in_use")
            self.assertEqual(st["error"]["port"], port)
            self.wait_for(lambda k, p: k == "notice" and p["code"] == "rtltcp_port_in_use")
        finally:
            blocker.close()
        # ポートが空けば、もう一度の rtltcp_set で始まり、error は消える
        st = self.call("rtltcp_set", enabled=True)["rtltcp"]
        self.assertTrue(st["running"])
        self.assertIsNone(st["error"])

    def test_client_receives_header_and_envelope(self):
        port, _ = self.start()
        # 100MHz（シミュレータの −30dBm の信号）のゼロスパン。REF を −30dBm にして振り切れ近くにする
        self.call("set_sweep", center_hz=100_000_000, span_hz=0)
        self.call("set_amplitude", ref_level=-30.0, unit="dBm")
        self.call("connect", backend="simulator", serial="SIM00001")
        c = socket.create_connection(("127.0.0.1", port), timeout=5)
        try:
            self.assertEqual(recv_exact(c, 12), encode_header(0, 0))
            end = time.monotonic() + 10
            while self.state()["clients"] != 1:
                self.assertLess(time.monotonic(), end)
                time.sleep(0.02)
            c.sendall(struct.pack(">BI", 0x02, 48_000))  # 出力の速さを下げて量を減らす
            self.call("run", mode="continuous")
            # 振幅の載った標本（I が 0 の位置 128 より大きい）が届くまで読む。Q はいつも 0（128）
            got_signal = False
            end = time.monotonic() + 20
            while not got_signal and time.monotonic() < end:
                data = recv_exact(c, 4096)
                i, q = data[0::2], data[1::2]
                self.assertTrue(all(x == 128 for x in q))
                got_signal = any(x > 200 for x in i)
            self.assertTrue(got_signal)
        finally:
            c.close()
            self.call("stop")

    def test_set_freq_from_client(self):
        port, _ = self.start()
        self.call("set_sweep", start_hz=50_000_000, stop_hz=200_000_000)
        c = socket.create_connection(("127.0.0.1", port), timeout=5)
        try:
            recv_exact(c, 12)
            self.clear_events()
            c.sendall(struct.pack(">BI", 0x01, 433_920_000))
            p = self.wait_for(lambda k, p: k == "notice" and p["code"] == "rtltcp_freq_from_client")
            self.assertTrue(p["detail"]["applied"])
            sw = self.call("get_state")["sweep"]
            self.assertEqual(sw["center_hz"], p["detail"]["center_hz"])
            self.assertLessEqual(abs(sw["center_hz"] - 433_920_000), 100)  # 機器の単位 100Hz への丸め
            self.assertEqual(sw["span_hz"], 0)
            # 同じ周波数の繰り返しは知らせない。利得の命令は無視（条件を変えない）
            self.clear_events()
            c.sendall(struct.pack(">BI", 0x01, 433_920_000) + struct.pack(">BI", 0x04, 300))
            time.sleep(0.5)
            self.call("get_state")  # 待ち行列の前の仕事が済んだことを確かめる
            with self.cv:
                codes = [p["code"] for k, p in self.events if k == "notice"]
            self.assertNotIn("rtltcp_freq_from_client", codes)
            self.assertEqual(self.call("get_state")["sweep"], sw)
        finally:
            c.close()


if __name__ == "__main__":
    unittest.main()
