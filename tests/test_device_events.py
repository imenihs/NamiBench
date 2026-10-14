"""機器の切断の記録（設計B §7.7）の試験。シミュレータと libiio の偽物だけを使う（実機には繋がない）。

- 記録の形: 回す（1 MB・.1〜.4）・書けなくても測定は続く・例外から理由の欄を作る
- libiio の部品: バッファを作れなかった（NULL）ときに errno を残す。切れたとする判定は変えない
- 再起動の見分け: 書いた LO と同じ／電源投入時の値と同じ／どちらでもない（と、どちらとも言えない場合）
- 本体を通して: 接続・切れた・戻った・利用者の切断・待つのをやめた・外部 IQ 送信の始め・終わりの行と、その詳細の欄
"""
from __future__ import annotations

import contextlib
import ctypes
import io
import json
import tempfile
import time
import types
import unittest
from pathlib import Path

from namibench.api.local import _SimulatorBackend
from namibench.core.device_events import BACKUPS, MAX_BYTES, DeviceEventLog, describe_error, make_record
from namibench.core.devices.base import load_profile
from namibench.core.devices.plutosdr import events as pe
from namibench.core.devices.plutosdr.fake import FAKE_SERIAL, FAKE_URI
from namibench.core.devices.plutosdr.iio import IioDevice, IioError
from namibench.core.instrument import GIGAST_PROFILE_PATH, Instrument
from namibench.core.types import DeviceDisconnected
from tests.exttx_helpers import LO
from tests.test_instrument_ext_tx import ExtCase
from tests.test_plutosdr import PlutoCase

MHZ = 1_000_000


def read_lines(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            pass  # 書いている途中の行
    return out


class LogWait:
    """記録の行を待つ（本体の制御スレッドが書く）。"""

    def log_path(self) -> Path:
        return self.inst._evt_log.path

    def lines(self) -> list[dict]:
        return read_lines(self.log_path())

    def wait_line(self, event: str, after: int = 0, timeout: float = 20.0, pred=lambda r: True) -> dict:
        end = time.monotonic() + timeout
        while True:
            for r in self.lines()[after:]:
                if r["event"] == event and pred(r):
                    return r
            if time.monotonic() > end:
                self.fail(f"記録に {event} の行が来ない: {[r['event'] for r in self.lines()]}")
            time.sleep(0.05)

    def index_of(self, rec: dict) -> int:
        return self.lines().index(rec) + 1


# ---------------------------------------------------------------------- 記録の形

class TestLogFile(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "logs" / "device_events.jsonl"

    def tearDown(self):
        self.tmp.cleanup()

    def test_rotates_before_exceeding_1mb(self):
        log = DeviceEventLog(self.path)
        rec = make_record("lost", "device_disconnected", "simulator", "m", "s", {"pad": "x" * 1000})
        one = len((json.dumps(rec, ensure_ascii=False) + "\n").encode("utf-8"))
        n = MAX_BYTES // one  # これだけ書いても 1 MB を超えない
        for _ in range(n):
            self.assertTrue(log.write(rec))
        self.assertFalse(self.path.with_name(self.path.name + ".1").exists())
        self.assertLessEqual(self.path.stat().st_size, MAX_BYTES)
        log.write(rec)  # 次の1行で 1 MB を超える: 先に回す
        first = self.path.with_name(self.path.name + ".1")
        self.assertTrue(first.exists())
        self.assertLessEqual(first.stat().st_size, MAX_BYTES)
        self.assertEqual(len(read_lines(self.path)), 1)

    def test_keeps_at_most_five_files(self):
        log = DeviceEventLog(self.path)
        rec = make_record("lost", None, None, None, None, {"pad": "y" * 5000})
        for _ in range(7 * (MAX_BYTES // 5000)):
            log.write(rec)
        names = sorted(p.name for p in self.path.parent.iterdir())
        self.assertEqual(names, ["device_events.jsonl"] + [f"device_events.jsonl.{i}" for i in range(1, BACKUPS + 1)])
        for p in self.path.parent.iterdir():
            self.assertLessEqual(p.stat().st_size, MAX_BYTES)

    def test_write_failure_is_counted_not_raised(self):
        self.path.parent.parent.mkdir(parents=True, exist_ok=True)
        self.path.parent.write_text("logs のフォルダの代わりにファイル")  # 書けない置き場所
        log = DeviceEventLog(self.path)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertFalse(log.write(make_record("connected", "user", None, None, None)))
            self.assertFalse(log.write(make_record("connected", "user", None, None, None)))
        self.assertEqual(log.failures, 2)
        self.assertEqual(len(err.getvalue().splitlines()), 1)  # 標準エラーに出すのは最初の1回だけ

    def test_record_has_common_fields_and_tz_time(self):
        r = make_record("connected", "user", "plutosdr", "plutosdr-ad9364", "S1", {"a": 1})
        self.assertEqual(set(r), {"time", "event", "reason", "kind", "model", "serial", "detail"})
        self.assertRegex(r["time"], r"[+-]\d\d:\d\d$")  # タイムゾーン付き

    def test_describe_error_from_iio_chain(self):
        try:
            try:
                raise IioError("buffer_refill", -110, "timed out", strerror="Connection timed out")
            except IioError as e:
                raise DeviceDisconnected(str(e)) from e
        except DeviceDisconnected as e:
            d = describe_error(e)
        self.assertEqual((d["exception"], d["iio_function"], d["errno"], d["strerror"]),
                         ("DeviceDisconnected", "iio_buffer_refill", 110, "Connection timed out"))
        self.assertEqual(d["cause_chain"], ["IioError"])

    def test_describe_error_from_oserror(self):
        try:
            try:
                raise OSError(5, "Input/output error")
            except OSError as e:
                raise DeviceDisconnected("読めない") from e
        except DeviceDisconnected as e:
            d = describe_error(e)
        self.assertEqual((d["errno"], d["strerror"]), (5, "Input/output error"))
        self.assertNotIn("iio_function", d)


# ---------------------------------------------------------------------- libiio の部品

class TestCreateBufferErrno(unittest.TestCase):
    def _device(self, errno_value: int) -> IioDevice:
        def create_buffer(dev, samples, cyclic):
            ctypes.set_errno(errno_value)  # libiio は NULL を返す前に errno を立てる
            return None

        lib = types.SimpleNamespace(strerror=lambda code: f"msg {code}")
        ctx = types.SimpleNamespace(_f={"device_create_buffer": create_buffer}, _lib=lib)
        return IioDevice(ctx, object(), "cf-ad9361-lpc")

    def test_null_buffer_records_errno(self):
        with self.assertRaises(IioError) as cm:
            self._device(16).create_buffer(1024)
        e = cm.exception
        self.assertEqual((e.func, e.errno, e.strerror), ("device_create_buffer", 16, "msg 16"))
        self.assertEqual(e.code, 0)  # 判定に使う code は変えない

    def test_null_buffer_with_einval_is_still_not_a_value_error(self):
        from namibench.core.devices.plutosdr.driver import PlutoSdrDevice

        with self.assertRaises(IioError) as cm:
            self._device(22).create_buffer(1024)
        self.assertEqual(cm.exception.errno, 22)
        self.assertFalse(PlutoSdrDevice._is_value_error(cm.exception))  # 切れたとみなすまま

    def test_null_buffer_without_errno(self):
        with self.assertRaises(IioError) as cm:
            self._device(0).create_buffer(1024)
        self.assertIsNone(cm.exception.errno)


# ---------------------------------------------------------------------- 再起動の見分け

class TestRebootJudgement(unittest.TestCase):
    def test_three_ways(self):
        app = [46_875_002]
        self.assertIs(pe.judge_reboot(46_875_002, app), False)  # アプリが書いた値のまま
        self.assertIs(pe.judge_reboot(46_875_003, app), False)  # 読み戻しの数 Hz のずれ
        self.assertIs(pe.judge_reboot(2_450_000_000, app), True)  # 電源を入れた直後の値
        self.assertIs(pe.judge_reboot(2_449_999_998, app), True)
        self.assertEqual(pe.judge_reboot(1_234_567_890, app), "unknown")

    def test_unknown_cases(self):
        self.assertEqual(pe.judge_reboot(2_450_000_000, []), "unknown")  # アプリが一度も書いていない
        self.assertEqual(pe.judge_reboot(None, [46_875_002]), "unknown")  # 読めなかった
        self.assertEqual(pe.judge_reboot(2_450_000_000, [2_450_000_000]), "unknown")  # どちらとも言える

    def test_power_on_default(self):
        self.assertEqual(pe.POWER_ON_TX_LO_HZ, 2_450_000_000)

    def test_watch_remembers_written_and_unsure(self):
        class Ch:
            fail = False

            def set_attr(self, name, value):
                if self.fail:
                    raise IioError("channel_attr_write", -5, "unplugged")

            def attr(self, name):
                return "123 Hz"

        ch = Ch()
        w = pe.LoWatch(ch)
        w.set_attr("powerdown", 1)
        self.assertEqual(w.written(), [])
        w.set_attr("frequency", 100)
        self.assertEqual(w.written(), [100])
        ch.fail = True
        with self.assertRaises(IioError):
            w.set_attr("frequency", 200)
        self.assertEqual(w.written(), [100, 200])  # 届いたか分からない値も比べる
        self.assertEqual(pe.read_tx_lo(w), 123)


class TestDropCause(unittest.TestCase):
    def test_cause_kept_when_dropped_outside_measure(self):
        # 掃引の外（on_idle で送信を止める）で切れた: 次の measure は「開いていない」としか言えないので、捨てたときの
        # libiio の失敗を口が覚え、切れた行に残す
        from namibench.core.devices.plutosdr import driver as pd
        from namibench.core.devices.plutosdr.fake import FakeIioLib, FakePluto
        from namibench.core.sweep.model import SweepModel
        from namibench.core.types import Mode, SweepRequest

        lib = FakeIioLib(FakePluto(signals=[]))
        dev = pd.PlutoSdrDevice(lib=lib)
        dev.open(FAKE_URI)
        model = SweepModel(dev.profile(), pd.timing_fn(dev.profile()))
        sg = model.apply(SweepRequest(mode=Mode.SG, sg_hz=100 * MHZ), model.default())
        dev.measure(sg)  # SG を出す（on_idle が止めに行く送信）
        self.assertIsNotNone(dev.last_rx_at)
        lib.pluto.unplugged = True
        dev.on_idle()
        with self.assertRaises(DeviceDisconnected):
            dev.measure(sg)
        d = pe.lost_detail(dev)
        self.assertEqual((d["drop_iio_function"], d["drop_errno"]), ("iio_channel_attr_write", 5))
        self.assertIsNotNone(d["drop_at"])


# ---------------------------------------------------------------------- 本体を通して（シミュレータ: GigaSt と同じ道）

class TestSimulatorEvents(LogWait, unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        profile = load_profile(GIGAST_PROFILE_PATH)
        sim = _SimulatorBackend(profile)
        sim.speed = 0.05
        self.devices = []
        orig = sim.open

        def tracking_open(serial, port):
            d = orig(serial, port)
            self.devices.append(d)
            return d

        sim.open = tracking_open
        self.inst = Instrument({"simulator": sim}, profile=profile, data_root=self.root / "data",
                               cal_root=self.root / "cal")

    def tearDown(self):
        self.inst.shutdown()
        self.tmp.cleanup()

    def call(self, name, **kw):
        return self.inst.submit(name, **kw).result(10)

    def test_place(self):
        self.assertEqual(self.log_path(), self.root / "data" / "logs" / "device_events.jsonl")

    def test_connect_lost_recovered_disconnect(self):
        self.call("connect", backend="simulator", serial="SIM00001")
        c = self.wait_line("connected")
        self.assertEqual((c["reason"], c["kind"], c["serial"]), ("user", "simulator", "SIM00001"))
        self.call("set_sweep", start_hz=100 * MHZ, stop_hz=200 * MHZ)
        self.call("run", mode="continuous")
        end = time.monotonic() + 20
        while self.inst._worker is None or self.inst._worker.last_ok_at is None:
            self.assertLess(time.monotonic(), end, "掃引が返らない")
            time.sleep(0.05)
        self.devices[0].unplug()
        lost = self.wait_line("lost")
        d = lost["detail"]
        self.assertEqual(lost["reason"], "device_disconnected")
        self.assertEqual(d["exception"], "DeviceDisconnected")
        self.assertEqual(d["activity"], ["sweep"])
        self.assertEqual((d["mode"], d["run"]), ("sa", "continuous"))
        self.assertIsNotNone(d["last_sweep_at"])
        self.assertGreaterEqual(d["connected_for_s"], 0.0)
        rec = self.wait_line("recovered", self.index_of(lost))
        self.assertEqual(rec["reason"], "same_serial_returned")
        self.assertGreaterEqual(rec["detail"]["outage_s"], 0.0)
        self.assertNotIn("rebooted", rec["detail"])  # 再起動の見分けは PlutoSDR だけ
        self.call("disconnect")
        dis = self.wait_line("disconnected", self.index_of(rec))
        self.assertEqual((dis["reason"], dis["serial"]), ("user", "SIM00001"))
        self.assertEqual(dis["detail"]["activity"], ["sweep"])

    def test_measurement_continues_when_log_unwritable(self):
        self.inst.shutdown()
        data = self.root / "data2"
        data.mkdir()
        (data / "logs").write_text("フォルダの代わりにファイル")
        profile = load_profile(GIGAST_PROFILE_PATH)
        sim = _SimulatorBackend(profile)
        sim.speed = 0.05
        self.inst = Instrument({"simulator": sim}, profile=profile, data_root=data, cal_root=self.root / "cal2")
        with contextlib.redirect_stderr(io.StringIO()):
            self.call("connect", backend="simulator", serial="SIM00001")
        self.call("set_sweep", start_hz=100 * MHZ, stop_hz=200 * MHZ)
        self.call("run", mode="single")
        end = time.monotonic() + 20
        while self.call("get_state")["run"]["last_stop_reason"] != "single_done":
            self.assertLess(time.monotonic(), end, "単発が終わらない")
            time.sleep(0.05)
        self.assertGreaterEqual(self.inst._evt_log.failures, 1)


# ---------------------------------------------------------------------- 本体を通して（PlutoSDR・libiio の偽物）

class TestPlutoEvents(LogWait, PlutoCase):
    def setUp(self):
        super().setUp()
        self.call("connect", backend="plutosdr", serial=FAKE_SERIAL)
        self.call("run", mode="continuous")
        self.wait_line("connected")
        end = time.monotonic() + 20
        while self.inst._device.inner.last_rx_at is None:
            self.assertLess(time.monotonic(), end, "受信が成り立たない")
            time.sleep(0.05)

    @property
    def pl(self):
        return self.lib.pluto

    def set_tx_lo(self, hz: int) -> None:
        self.pl._set_raw("ad9361-phy", "altvoltage1", True, "frequency", str(hz))

    def unplug_and_back(self, tx_lo_hz=None) -> tuple[dict, dict]:
        self.pl.unplugged = True
        lost = self.wait_line("lost")
        if tx_lo_hz is not None:
            self.set_tx_lo(tx_lo_hz)
        self.pl.unplugged = False
        rec = self.wait_line("recovered", self.index_of(lost))
        return lost, rec

    def test_connected_line_has_tx_lo_at_open(self):
        c = self.lines()[0]
        self.assertEqual((c["event"], c["kind"], c["serial"]), ("connected", "plutosdr", FAKE_SERIAL))
        self.assertEqual(c["detail"]["tx_lo_read_hz"], 2_450_000_000)  # 偽物の電源投入時の値を静める前に読んだ
        self.assertEqual(c["detail"]["fw_version"], "v0.39-fake")

    def test_lost_details(self):
        lost, _ = self.unplug_and_back()
        d = lost["detail"]
        self.assertTrue(d["iio_function"].startswith("iio_"))
        self.assertEqual(d["errno"], 5)  # 偽物の「抜けた」は EIO
        self.assertIsNotNone(d["last_rx_refill_at"])
        self.assertIsNone(d["last_tx_push_at"])  # SA は送信しない
        self.assertEqual(d["activity"], ["sweep"])
        self.assertTrue(d["tx_lo_written_hz"])
        self.assertIsNotNone(d["connected_for_s"])

    def test_not_rebooted(self):
        lost, rec = self.unplug_and_back()
        d = rec["detail"]
        self.assertIs(d["rebooted"], False)
        self.assertIn(d["tx_lo_read_hz"], lost["detail"]["tx_lo_written_hz"])
        self.assertGreaterEqual(d["outage_s"], 0.0)

    def test_rebooted(self):
        _, rec = self.unplug_and_back(2_450_000_000)
        self.assertIs(rec["detail"]["rebooted"], True)
        self.assertEqual(rec["detail"]["tx_lo_power_on_hz"], 2_450_000_000)

    def test_reboot_unknown(self):
        _, rec = self.unplug_and_back(1_234_567_890)
        self.assertEqual(rec["detail"]["rebooted"], "unknown")

    def test_create_buffer_errno_recorded_and_still_lost(self):
        self.pl.buffer_errno = 22  # EINVAL でも、バッファを作れないのは切れた扱いのまま（判定は変えない）
        lost = self.wait_line("lost")
        self.pl.buffer_errno = None
        d = lost["detail"]
        self.assertEqual((d["iio_function"], d["errno"]), ("iio_device_create_buffer", 22))
        self.wait_line("recovered", self.index_of(lost))

    def test_gave_up_waiting(self):
        self.pl.unplugged = True
        lost = self.wait_line("lost")
        self.call("connect", backend="simulator", serial="SIM00001")
        g = self.wait_line("gave_up_waiting", self.index_of(lost))
        self.assertEqual((g["reason"], g["kind"], g["serial"]), ("user_picked_device", "plutosdr", FAKE_SERIAL))
        self.assertGreaterEqual(g["detail"]["waited_s"], 0.0)
        c = self.wait_line("connected", self.index_of(g))
        self.assertEqual(c["kind"], "simulator")


class TestExtIqEvents(LogWait, ExtCase):
    def test_start_and_stop_lines(self):
        c = self.streaming()
        s = self.wait_line("ext_iq_started")
        self.assertEqual(s["reason"], "client_start")
        self.assertEqual((s["detail"]["tx_lo_hz"], s["detail"]["client"]), (LO, "test"))
        c.send_json({"type": "stop"})
        e = self.wait_line("ext_iq_stopped", self.index_of(s))
        self.assertEqual(e["reason"], "client")
        self.assertGreater(e["detail"]["consumed"], 0)

    def test_lost_while_ext_iq(self):
        self.streaming()
        end = time.monotonic() + 10
        while self.inst._device.inner.last_tx_at is None:
            self.assertLess(time.monotonic(), end, "送信が押されない")
            time.sleep(0.05)
        # 受信の読みだけを先に失敗させる（押すスレッドが先に失敗すると、外部 IQ の終わりの行が切れた行より先に来る）
        self.pl.fail_reads = True
        lost = self.wait_line("lost")
        self.assertIn("ext_iq", lost["detail"]["activity"])
        self.assertIsNotNone(lost["detail"]["last_tx_push_at"])
        e = self.wait_line("ext_iq_stopped", self.index_of(lost))
        self.assertEqual(e["reason"], "device_lost")
        self.pl.fail_reads = False
        rec = self.wait_line("recovered", self.index_of(lost))
        self.assertIs(rec["detail"]["rebooted"], False)  # 外部 IQ の LO を書いたまま戻った


if __name__ == "__main__":
    unittest.main()
