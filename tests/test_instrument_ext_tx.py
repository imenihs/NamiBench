"""外部 IQ 送信の口（設計S §1）の本体と口の試験。libiio の偽物の PlutoSDR と、127.0.0.1 の口に繋ぐ試験のクライアント。

- 口: 印と版・2つ目は busy・受け付けを切ると開けない
- start を断る（PlutoSDR 無し・送信できない機器・SG/TG/S11・校正中・範囲の外の速さ・読み戻しが違う・範囲の外の LO）。
  断った後も接続は残り、速さは 4.096MS/s のまま
- 流れ（例のクライアント tools/ext_iq_client.py をそのまま使う）: 押した標本が送った標本とビット単位で同じ・consumed が合う
- 止める道（相手が落ちた・stop・［止める］・受け付けを切る・push の失敗・機器が抜けた・切断・アプリの終了）: どれも送信が
  止まり、速さが 4.096MS/s に戻り、SG/TG/S11 が使える。つなぎ直しても送信は出ない
- SA の追従・P2（抜けても元の速さのモデル）・P3（rtl_tcp の出口）・減衰（別の値・すぐ入る）
"""
from __future__ import annotations

import socket
import threading
import time
import unittest

import numpy as np

from pygigast.core.devices.plutosdr.fake import FAKE_URI
from pygigast.core.exttx.protocol import HEADER, encode_hello
from pygigast.core.instrument_common import CommandError
from pygigast.ui_qt import screen_model, texts
from tests.exttx_helpers import FS, LO, MHZ, RawClient, free_port, load_example_client, tone_iq
from tests.test_plutosdr import PlutoCase, fake_lib

BASE = 4_096_000


class ExtCase(PlutoCase):
    connect = True

    def setUp(self):
        super().setUp()
        self.lib = fake_lib(signals=[])
        self.lib.pluto.loss_db = 40.0
        self.inst._backends["plutosdr"]._lib_factory = lambda: self.lib
        self.port = free_port()
        self.call("ext_tx_set", enabled=True, port=self.port)
        if self.connect:
            self.call("connect", backend="plutosdr", port=FAKE_URI)
            self.call("set_sweep", start_hz=1574 * MHZ, stop_hz=1577 * MHZ, rbw_hz=1500)
            self.call("run", mode="continuous")
        self.clients: list[RawClient] = []

    def tearDown(self):
        for c in self.clients:
            c.close()
        super().tearDown()

    @property
    def pl(self):
        return self.lib.pluto

    def client(self, **kw) -> RawClient:
        c = RawClient(self.port, **kw)
        self.clients.append(c)
        return c

    def state(self) -> dict:
        return self.call("get_state")

    def until(self, pred, timeout=10.0, what="状態"):
        end = time.monotonic() + timeout
        while True:
            st = self.state()
            if pred(st):
                return st
            self.assertLess(time.monotonic(), end, f"{what} にならない")
            time.sleep(0.05)

    def streaming(self) -> RawClient:
        c = self.client()
        c.wait_type("welcome")
        self.assertEqual(c.start()["type"], "ready")
        c.feed()
        c.wait_type("on")
        self.until(lambda st: (st["ext_tx"]["session"] or {}).get("state") == "on", what="出力中")
        return c

    def assert_stopped_everywhere(self):
        st = self.until(lambda st: st["ext_tx"]["session"] is None and st["sweep"]["sample_rate_hz"] == BASE,
                        what="外部の送信が終わって 4.096MS/s")
        end = time.monotonic() + 5
        while self.pl.tx_lo_powered or self.pl.tx_buffer is not None or self.pl.fs != BASE:
            self.assertLess(time.monotonic(), end, "機器の送信が止まらない")
            time.sleep(0.05)
        self.assertEqual(self.pl.tx_atten, -89.75)
        self.assertEqual(self.pl.fs, BASE)
        reasons = screen_model.mode_reasons(st)
        self.assertIsNone(reasons["tg"])
        self.assertIsNone(reasons["sg"])
        self.assertIsNone(reasons["s11"])
        return st


class TestPort(ExtCase):
    connect = False

    def test_bad_magic_closes(self):
        c = self.client(hello=b"POST / H")
        self.assertTrue(c.wait_closed(3))
        self.assertEqual(c.msgs, [])

    def test_major_mismatch(self):
        c = self.client(hello=encode_hello(2, 0))
        m = c.wait_type("error")
        self.assertEqual((m["code"], m["supported"]), ("unsupported_protocol", [1, 0]))
        self.assertTrue(c.wait_closed(3))

    def test_welcome_and_second_is_busy(self):
        c = self.client()
        w = c.wait_type("welcome")
        self.assertEqual((w["protocol"], w["iq_format"], w["full_scale"]), ([1, 0], "ci16le", 32767))
        self.assertEqual(w["sample_rate_range_hz"], [2_083_334, 4_096_000])
        self.assertEqual((w["block_samples"], w["queue_samples"]), (65536, 65536 * 8))
        c2 = self.client()
        m = c2.wait_type("error")
        self.assertEqual(m["code"], "busy")
        self.assertTrue(c2.wait_closed(3))
        self.assertFalse(c.closed)  # いまの相手は切らない

    def test_disabled_does_not_listen(self):
        self.call("ext_tx_set", enabled=False)
        with self.assertRaises(OSError):
            socket.create_connection(("127.0.0.1", self.port), timeout=1).close()
        self.assertFalse(self.state()["ext_tx"]["listening"])

    def test_listens_only_on_localhost(self):
        srv = self.inst._ext_server
        self.assertEqual(srv._listen.getsockname()[0], "127.0.0.1")

    def test_not_connected_then_cannot_transmit(self):
        c = self.client()
        c.wait_type("welcome")
        self.assertEqual(c.start()["code"], "not_connected")
        self.call("connect", backend="simulator", serial="SIM00001")
        self.assertEqual(c.start()["code"], "device_cannot_transmit")
        self.assertFalse(c.closed)


class TestRefuse(ExtCase):
    def refused(self, code, **start):
        for old in self.clients:
            old.close()
        self.until(lambda st: st["ext_tx"]["client"] is None, what="前の相手が閉じた")
        c = self.client()
        c.wait_type("welcome")
        m = c.start(**start)
        self.assertEqual(m.get("code"), code, m)
        self.assertFalse(c.closed)  # 断っても接続は残す
        st = self.state()
        self.assertEqual(st["sweep"]["sample_rate_hz"], BASE)
        self.assertEqual(self.pl.fs, BASE)
        self.assertIsNone(st["ext_tx"]["session"])
        return c, m

    def test_mode_conflict(self):
        for mode in ("sg", "tg", "s11"):
            self.call("set_mode", mode=mode, **({"sg_hz": 1576 * MHZ} if mode == "sg" else {}))
            c, m = self.refused("mode_conflict")
            self.assertEqual(m["mode"], mode)

    def test_calibrating(self):
        done = threading.Event()

        from pygigast.core.instrument_pluto_cal import _PlutoCalJob

        def busy():
            self.inst._cal_job = object.__new__(_PlutoCalJob)  # 利得の補正を測っている形（中身は使わない）
            done.set()

        self.inst._q.put(("call", busy))
        done.wait(5)
        try:
            self.refused("calibrating")
        finally:
            ok = threading.Event()
            self.inst._q.put(("call", lambda: (setattr(self.inst, "_cal_job", None), ok.set())))
            ok.wait(5)

    def test_rate_and_lo_out_of_range(self):
        _, m = self.refused("sample_rate_unsupported", rate=5_000_000)
        self.assertEqual((m["min"], m["max"]), (2_083_334, 4_096_000))
        self.refused("sample_rate_unsupported", rate=2_083_334)  # SA の RBW が1つも使えない速さ
        _, m = self.refused("lo_out_of_range", lo=10_000_000)
        self.assertEqual(m["min"], 46_875_002)  # 申告の下限 46875001Hz は機器が断る: 書いて通った値（実機 2026-10-13）

    def test_readback_differs_then_retry(self):
        self.pl.rate_readback[FS] = FS + 1
        c, m = self.refused("sample_rate_unsupported")
        self.assertEqual(m["readback"], FS + 1)
        self.pl.rate_readback.clear()
        self.assertEqual(c.start()["type"], "ready")  # 直して start し直せる

    def test_already_started(self):
        c = self.streaming()
        c.send_json({"type": "start", "sample_rate_hz": FS, "tx_lo_hz": LO})
        self.assertEqual(c.wait(lambda m: m.get("code") == "already_started")["type"], "error")


class TestFlow(ExtCase):
    def test_example_client_bit_exact(self):
        cli = load_example_client()
        res = cli.run(fs=FS, lo_hz=LO, tone_hz=250_000, seconds=2.0, port=self.port, log=lambda *a: None)
        self.assertEqual(res, {"type": "stopped", "reason": "client"})
        self.assert_stopped_everywhere()
        got = self.pl.pushed_samples()
        n = got.size // 2
        self.assertGreater(n, FS)
        make = cli.tone(FS, 250_000)
        want = np.empty(2 * n, dtype=np.int16)
        x = make(0, n, 32767)
        want[0::2] = np.round(x.real * 32767)
        want[1::2] = np.round(x.imag * 32767)
        np.testing.assert_array_equal(got, want)

    def test_restart_right_after_stop(self):
        # 止めた直後（元の速さへ戻している途中）につなぎ直して start しても、戻すのと入れるのが入れ違わない
        c = self.streaming()
        c.send_json({"type": "stop"})
        c.wait_type("stopped")
        c.close()
        c2 = self.client()
        c2.wait_type("welcome")
        self.assertEqual(c2.start()["type"], "ready")
        c2.feed()
        c2.wait_type("on")
        time.sleep(0.3)
        self.assertEqual(self.pl.fs, FS)
        self.assertEqual(self.state()["sweep"]["sample_rate_hz"], FS)
        c2.send_json({"type": "stop"})
        self.assert_stopped_everywhere()

    def test_state_while_on(self):
        c = self.streaming()
        st = self.state()
        s = st["ext_tx"]["session"]
        self.assertEqual((s["sample_rate_hz"], s["tx_lo_hz"], s["client"]), (FS, LO, "test"))
        self.assertEqual(st["plutosdr"]["tx_on"], "ext")
        self.assertAlmostEqual(s["power_dbfs"], 20 * np.log10(0.3), delta=0.1)
        item = texts.ext_status_item(st)
        from pygigast.core.units import format_freq

        self.assertEqual(item["text"], "外部 IQ 出力中 " + format_freq(LO))
        self.assertTrue(item["stop"])
        reasons = screen_model.mode_reasons(st)
        for m in ("sg", "tg", "s11"):
            self.assertEqual(reasons[m], "外部のアプリケーションが送信しています")
        self.assertIsNone(reasons["sa"])
        consumed = [m for m in c.msgs if m["type"] == "credit"]
        self.assertTrue(consumed)
        self.assertTrue(all(m["consumed"] % 65536 == 0 for m in consumed))

    def test_mode_refused_and_cal_refused(self):
        self.streaming()
        with self.assertRaises(CommandError) as cm:
            self.call("set_mode", mode="tg")
        self.assertEqual(cm.exception.code, "ext_tx_active")
        self.assertEqual(self.state()["sweep"]["mode"], "sa")
        self.call("cal_open", kind="pluto_gain")
        with self.assertRaises(CommandError) as cm:
            self.call("cal_measure")
        self.assertEqual(cm.exception.code, "ext_tx_active")
        self.call("cal_close")

    def test_sa_follows_rate(self):
        self.call("set_sweep", rbw_hz=48000)
        self.assertEqual(self.state()["sweep"]["rbw_hz"], 48000)
        with self.cv:
            self.events.clear()
        c = self.streaming()
        st = self.state()
        sw = st["sweep"]
        self.assertEqual(sw["sample_rate_hz"], FS)
        self.assertEqual(set(sw["rbw_rate_unusable_hz"]), {24000, 48000, 60324, 120648})
        self.assertEqual((sw["rbw_hz"], sw["rbw_preference_hz"]), (12000, 48000))
        groups = screen_model.rbw_groups(st)
        hann = {ch["hz"]: ch["reason"] for g in groups if g["window"] == "hann" for ch in g["choices"]}
        self.assertEqual(hann[48000], "外部 IQ の標本化の速さ（2.6MS/s）では使えません")
        self.assertIsNone(hann[1500])
        notices = [p for k, p in self.events if k == "notice"]
        adj = [n for n in notices if n["code"] == "ext_tx_rate_adjusted"]
        self.assertTrue(adj and adj[0]["detail"]["adjustments"][0]["reason"] == "rbw_sample_rate")
        # 1500Hz の FFT の長さは 2600（点の間隔と点数は変わらない）
        self.call("set_sweep", rbw_hz=1500)
        self.wait_for(lambda k, p: k == "state" and p["run"]["sweep_count"] > 0)
        raw = self.inst._last_raw
        end = time.monotonic() + 10
        while raw is None or raw.extra.get("sample_rate_hz") != FS or raw.adopted.rbw_hz != 1500:
            self.assertLess(time.monotonic(), end)
            time.sleep(0.05)
            raw = self.inst._last_raw
        self.assertEqual(raw.extra["fft_n"], 2600)
        self.assertEqual(raw.extra["tx"], "ext")
        self.call("set_sweep", rbw_hz=48000)  # 好みは使えない RBW のまま残る（終わったら戻る）
        self.assertEqual(self.state()["sweep"]["rbw_hz"], 12000)
        c.send_json({"type": "stop"})
        st = self.assert_stopped_everywhere()
        self.assertEqual(st["sweep"]["rbw_hz"], 48000)

    def test_trace_reset_on_rate_change(self):
        self.call("set_trace", number=2, action="average", count=16)
        self.wait_for(lambda k, p: k == "state" and p["run"]["sweep_count"] > 2)
        with self.cv:
            self.events.clear()
        self.streaming()
        notices = [p for k, p in self.events if k == "notice" and p["code"] == "trace_reset"]
        self.assertTrue(any(n["detail"]["cause"] == "sample_rate" for n in notices))

    def test_atten_is_separate_and_applies_now(self):
        self.call("plutosdr_set", tx_atten_db=-10.0)
        st = self.state()
        self.assertEqual(st["ext_tx"]["atten_db"], -89.75)  # 初期値は最小の出力（O1）
        self.streaming()
        self.assertEqual(self.pl.tx_atten, -89.75)
        self.call("ext_tx_atten", atten_db=-30.0)
        end = time.monotonic() + 1.0
        while self.pl.tx_atten != -30.0:
            self.assertLess(time.monotonic(), end, "減衰がすぐ入らない")
            time.sleep(0.01)
        self.assertEqual(self.state()["plutosdr"]["tx_atten_db"], -10.0)  # SG・TG の値は変わらない
        self.assertEqual(self.inst._settings.get("ext_tx")["atten_db"], -30.0)
        with self.assertRaises(CommandError):
            self.call("ext_tx_atten", atten_db=5.0)


class TestStopPaths(ExtCase):
    def test_client_stop(self):
        c = self.streaming()
        c.send_json({"type": "stop"})
        self.assertEqual(c.wait_type("stopped")["reason"], "client")
        self.assertTrue(c.wait_closed(3))
        self.assert_stopped_everywhere()
        self.call("set_mode", mode="tg")  # SG・TG・S11 が使える

    def test_client_crash(self):
        c = self.streaming()
        c.crash()
        self.assert_stopped_everywhere()

    def test_user_stop(self):
        c = self.streaming()
        self.call("ext_tx_stop")
        self.assertEqual(c.wait_type("stopped")["reason"], "user")
        self.assert_stopped_everywhere()

    def test_disable(self):
        c = self.streaming()
        self.call("ext_tx_set", enabled=False)
        self.assertEqual(c.wait_type("stopped")["reason"], "disabled")
        self.assert_stopped_everywhere()

    def test_starved(self):
        c = self.streaming()
        c.paused.set()
        self.assertEqual(c.wait_type("stopped", 6)["reason"], "starved")
        self.assert_stopped_everywhere()

    def test_credit_exceeded(self):
        c = self.client()
        c.wait_type("welcome")
        ready = c.start()
        data = tone_iq(0, ready["credit"] + 16).tobytes()
        try:
            # 1 枠 1MiB（上限）に分け、全部を1回で送る（枠の間で止まると、試験の負荷しだいで先に「途切れ」になる）
            step = 1 << 20
            c.send_raw(b"".join(HEADER.pack(len(data[i: i + step]), 2) + data[i: i + step]
                                for i in range(0, len(data), step)))
        except OSError:
            pass  # 超えた時点でアプリが閉じる
        m = c.wait_type("stopped")
        self.assertEqual((m["reason"], m.get("detail")), ("protocol_error", "credit_exceeded"))
        self.assert_stopped_everywhere()

    def test_push_failure(self):
        c = self.streaming()
        self.pl.push_fail = True
        self.assertEqual(c.wait_type("stopped")["reason"], "device_lost")
        st = self.until(lambda st: st["ext_tx"]["session"] is None)
        self.assertEqual(st["sweep"]["sample_rate_hz"], BASE)
        self.assertEqual(self.pl.tx_atten, -89.75)

    def test_unplug_then_reconnect_does_not_transmit(self):
        c = self.streaming()
        self.pl.unplugged = True
        self.assertEqual(c.wait_type("stopped")["reason"], "device_lost")
        st = self.until(lambda st: st["connection"]["state"] == "reconnecting" and st["ext_tx"]["session"] is None)
        # P2: 抜けていても本体のプロファイルとモデルは 4.096MS/s の元へ戻す
        self.assertEqual(st["sweep"]["sample_rate_hz"], BASE)
        self.assertEqual(st["sweep"]["rbw_rate_unusable_hz"], [])
        self.pl.unplugged = False
        self.pl.push_fail = False
        st = self.until(lambda st: st["connection"]["state"] == "connected", 15, "つなぎ直し")
        time.sleep(0.5)
        self.assertEqual(self.pl.fs, BASE)  # 次の接続の open が元の速さを書く
        self.assertFalse(self.pl.tx_lo_powered)
        self.assertIsNone(self.state()["ext_tx"]["session"])

    def test_sa_read_fails_while_push_hangs(self):
        # P1・P2: 受信の読みが先に失敗し、押すスレッドは時間切れを待っている間。本体は待たずに閉じ、モデルを元へ戻す
        c = self.streaming()
        self.pl.push_hang_s = 2.5
        self.pl.push_fail = True
        self.pl.fail_reads = True
        t0 = time.monotonic()
        st = self.until(lambda st: st["connection"]["state"] == "reconnecting" and st["ext_tx"]["session"] is None,
                        what="切れて外部の送信が終わる")
        self.assertLess(time.monotonic() - t0, 2.0)  # 押すスレッドの時間切れを待たない
        self.assertEqual(st["sweep"]["sample_rate_hz"], BASE)
        self.assertEqual(st["sweep"]["rbw_rate_unusable_hz"], [])
        self.assertEqual(c.wait_type("stopped", 5)["reason"], "device_lost")
        end = time.monotonic() + 6
        while self.pl.tx_lo_powered or self.pl.tx_buffer is not None:
            self.assertLess(time.monotonic(), end, "押すスレッドが止める順を通らない")
            time.sleep(0.05)
        self.assertEqual(self.pl.tx_atten, -89.75)
        self.pl.fail_reads = False
        self.pl.push_fail = False

    def test_disconnect_command(self):
        c = self.streaming()
        self.call("disconnect")
        self.assertEqual(c.wait_type("stopped")["reason"], "user")
        end = time.monotonic() + 5
        while self.pl.tx_lo_powered or self.pl.tx_buffer is not None:
            self.assertLess(time.monotonic(), end)
            time.sleep(0.05)
        self.assertEqual(self.pl.tx_atten, -89.75)
        st = self.state()
        self.assertEqual(st["sweep"]["sample_rate_hz"], BASE)

    def test_app_exit(self):
        c = self.streaming()
        self.inst.shutdown()
        self.assertEqual(c.wait_type("stopped", 5)["reason"], "app_exit")
        self.assertFalse(self.pl.tx_lo_powered)
        self.assertEqual(self.pl.tx_atten, -89.75)
        self.assertIsNone(self.pl.tx_buffer)

    def test_stop_during_preparing(self):
        # 速さを入れる前に相手が落ちる（掃引の錠を握って準備を待たせる）
        dev = self.inst._device.inner
        with dev._lock:
            c = self.client()
            c.wait_type("welcome")
            c.send_json({"type": "start", "sample_rate_hz": FS, "tx_lo_hz": LO})
            self.until(lambda st: (st["ext_tx"]["session"] or {}).get("state") == "preparing")
            c.crash()
            self.until(lambda st: st["ext_tx"]["session"] is None)
        self.assert_stopped_everywhere()


class TestRtlTcp(ExtCase):
    def test_decimator_paused_and_rebuilt(self):
        from pygigast.core.rtltcp import RtlTcpServer  # noqa: F401

        p = free_port()
        self.call("rtltcp_set", enabled=True, port=p)
        s = socket.create_connection(("127.0.0.1", p), timeout=5)
        try:
            self.until(lambda st: st["rtltcp"]["clients"] == 1)
            self.assertFalse(self.inst._pluto_rtl_paused)
            with self.cv:
                self.events.clear()
            c = self.streaming()
            self.assertTrue(self.inst._pluto_rtl_paused)  # 2.6MS/s を 2.048MS/s に整数で落とせない
            self.wait_for(lambda k, p: k == "notice" and p["code"] == "rtltcp_sample_rate_unsupported"
                          and p["detail"].get("paused"))
            c.send_json({"type": "stop"})
            self.assert_stopped_everywhere()
            self.assertFalse(self.inst._pluto_rtl_paused)
            self.assertEqual(self.inst._pluto_decim.fs_in, BASE)
        finally:
            s.close()



class TestRequestedValuesSurvive(ExtCase):
    """残しておいた条件を戻す道（元に戻す・やり直す）も掃引条件モデルを通り、いまの速さで使える条件になる。速さのために
    変えた範囲・RBW は、求めた値を残して送信が終わったら戻る（設計C §5.1・設計S §1 §3.2）。"""

    def sweeps_at(self, fs: int, rbw: int) -> None:
        """いまの条件（速さ fs・RBW rbw）で掃引が続いている（invalid_sweep で止まっていない）。"""
        end = time.monotonic() + 10
        while True:
            raw = self.inst._last_raw
            if raw is not None and raw.extra.get("sample_rate_hz") == fs and raw.adopted.rbw_hz == rbw:
                break
            self.assertLess(time.monotonic(), end, f"{fs}Hz・RBW {rbw}Hz の掃引が来ない")
            time.sleep(0.05)
        st = self.state()
        self.assertEqual(st["run"]["state"], "continuous")
        self.assertFalse([p for k, p in self.events if k == "notice" and p["code"] == "invalid_sweep"])

    def test_undo_during_stream_uses_rate(self):
        self.call("set_sweep", rbw_hz=24000)
        self.call("set_sweep", rbw_hz=1500)
        self.streaming()
        with self.cv:
            self.events.clear()
        r = self.call("undo")
        sw = self.state()["sweep"]
        self.assertEqual(sw["sample_rate_hz"], FS)
        self.assertEqual(sw["rbw_preference_hz"], 24000)  # 求めた値は残る
        self.assertEqual(sw["rbw_hz"], 12000)  # 2.6MS/s で使える近い RBW（同じ窓の下の段）
        self.assertIn(sw["rbw_hz"], sw["rbw_usable_hz"])
        self.assertNotIn(sw["rbw_hz"], sw["rbw_rate_unusable_hz"])
        self.assertIn(("rbw_hz", "rbw_sample_rate"), {(a["field"], a["reason"]) for a in r["adjustments"]})
        self.sweeps_at(FS, 12000)
        # やり直す: 1500Hz に戻る（使える値は知らせない）
        r = self.call("redo")
        self.assertEqual(r["adjustments"], [])
        self.assertEqual(self.state()["sweep"]["rbw_hz"], 1500)
        self.call("undo")
        self.call("ext_tx_stop")
        st = self.assert_stopped_everywhere()
        self.assertEqual(st["sweep"]["rbw_hz"], 24000)  # 送信が終わったら求めた RBW に戻る

    def test_undo_after_stream_has_no_stale_rate(self):
        c = self.streaming()
        self.call("set_sweep", rbw_hz=3000)  # 2.6MS/s の条件を積む
        c.send_json({"type": "stop"})
        self.assert_stopped_everywhere()
        with self.cv:
            self.events.clear()
        r = self.call("undo")
        sw = self.state()["sweep"]
        self.assertEqual(sw["sample_rate_hz"], BASE)
        self.assertEqual(sw["rbw_rate_unusable_hz"], [])
        self.assertEqual((sw["rbw_hz"], sw["rbw_preference_hz"]), (1500, 1500))
        self.assertEqual(r["adjustments"], [])
        self.sweeps_at(BASE, 1500)
        self.call("redo")
        self.assertEqual(self.state()["sweep"]["sample_rate_hz"], BASE)

    def test_narrowed_span_comes_back(self):
        self.call("set_sweep", start_hz=100 * MHZ, stop_hz=5900 * MHZ)
        with self.cv:
            self.events.clear()
        c = self.streaming()
        sw = self.state()["sweep"]
        self.assertLess(sw["span_hz"], 5800 * MHZ)
        self.assertEqual(sw["center_hz"], 3000 * MHZ)  # Center を守って狭める
        notices = [p for k, p in self.events if k == "notice" and p["code"] == "ext_tx_rate_adjusted"]
        self.assertIn("span_sample_rate", {a["reason"] for n in notices for a in n["detail"]["adjustments"]})
        self.call("set_sweep", rbw_hz=3000)  # 範囲を触らない変更では、求めた範囲は残る
        with self.cv:
            self.events.clear()
        c.send_json({"type": "stop"})
        st = self.assert_stopped_everywhere()
        self.assertEqual((st["sweep"]["start_hz"], st["sweep"]["stop_hz"]), (100 * MHZ, 5900 * MHZ))
        notices = [p for k, p in self.events if k == "notice" and p["code"] == "ext_tx_rate_adjusted"]
        self.assertIn(("start_hz", "span_sample_rate_restored"),
                      {(a["field"], a["reason"]) for n in notices for a in n["detail"]["adjustments"]})

    def test_span_lock_during_stream_keeps_the_locked_span(self):
        self.call("set_sweep", start_hz=100 * MHZ, stop_hz=5900 * MHZ)
        c = self.streaming()
        narrow = self.state()["sweep"]
        self.call("set_sweep", span_lock=True)
        c.send_json({"type": "stop"})
        st = self.assert_stopped_everywhere()
        self.assertEqual((st["sweep"]["start_hz"], st["sweep"]["stop_hz"]), (narrow["start_hz"], narrow["stop_hz"]))
        self.assertEqual(st["sweep"]["span_lock_hz"], narrow["stop_hz"] - narrow["start_hz"])

    def test_preset_saved_during_stream_keeps_the_requested_range(self):
        self.call("set_sweep", start_hz=100 * MHZ, stop_hz=5900 * MHZ)
        self.streaming()
        content = self.inst._preset_content()
        self.assertEqual((content["sweep"]["start_hz"], content["sweep"]["stop_hz"]), (100 * MHZ, 5900 * MHZ))

    def test_undo_to_wide_span_during_stream(self):
        self.call("set_sweep", start_hz=100 * MHZ, stop_hz=5900 * MHZ)
        self.call("set_sweep", start_hz=1574 * MHZ, stop_hz=1577 * MHZ)
        c = self.streaming()
        r = self.call("undo")
        sw = self.state()["sweep"]
        self.assertLess(sw["span_hz"], 5800 * MHZ)  # いまの速さで測れる幅
        self.assertIn(("start_hz", "span_sample_rate"), {(a["field"], a["reason"]) for a in r["adjustments"]})
        c.send_json({"type": "stop"})
        st = self.assert_stopped_everywhere()
        self.assertEqual((st["sweep"]["start_hz"], st["sweep"]["stop_hz"]), (100 * MHZ, 5900 * MHZ))
        self.call("undo")  # その前の条件へ
        sw = self.state()["sweep"]
        self.assertEqual((sw["start_hz"], sw["stop_hz"], sw["sample_rate_hz"]), (1574 * MHZ, 1577 * MHZ, BASE))


if __name__ == "__main__":
    unittest.main()


class TestSettingsSurviveRestart(unittest.TestCase):
    def test_ext_tx_settings_are_read_back_after_restart(self):
        """受け付け・ポート・外部の送信の減衰は、設定を読み直しても残る（設計S §1。起動のたびに入れ直さない）。"""
        import tempfile
        from pathlib import Path

        from pygigast.core.settings import Settings

        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "settings.json"
            s = Settings(path)
            s.set("ext_tx", {"enabled": True, "port": 1240, "atten_db": -20.0})
            s.save()
            self.assertEqual(Settings(path).get("ext_tx"), {"enabled": True, "port": 1240, "atten_db": -20.0})
