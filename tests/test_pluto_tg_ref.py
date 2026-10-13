"""PlutoSDR の TG の基準の前の利得合わせと、利得・送信の減衰の固定（設計Q §7.9: Q §7.8 の①②を TG にも当てる）。
libiio の偽物（TX → 損失 → RX の直結）を使う。実機は使わない。

- 「基準を取る」は、まず送信しながら受信の利得を合わせ（3dB の余裕）、選んだ利得を欄（状態）に出して知らせてから基準を取る
  （自動なら手動へ移す）。合わせられない・いちばん小さい利得でも振り切れる・やめたら基準を取らず理由を出す
- 基準を取り始めてから、その基準を捨てるか TG を出るまで、受信の利得・「利得を合わせる」・送信の減衰を変えさせない
  （同じ値は断らない）。基準を選んだまま TG へ戻るとまた固定する。取るのが途中で終わったら外れる
- 固定の理由にするのは、いまのモードの基準だけ（S11 の基準は TG を固定しない・TG の基準は S11 を固定しない）
- GigaSt の TG は変わらない（合わせない・固定しない）
"""
from __future__ import annotations

import threading
import time
import unittest

from pygigast.core.devices.plutosdr.fake import FAKE_SERIAL, FakeIioLib, FakePluto
from pygigast.core.instrument_common import CommandError
from pygigast.core.types import DeviceError, SweepDiscarded
from tests import test_pluto_s11 as s11t

MHZ = 1_000_000
TG_LOCK = "plutosdr_tg_conditions_locked"
S11_LOCK = s11t.S11_LOCK


class Case(s11t.Case):
    """TG は TX → 損失 10dB → RX の直結（S11 の試験はブリッジ）。"""

    def setUp(self):
        super().setUp()
        self.pl = FakePluto(signals=[], loss_db=10.0)
        self.lib = FakeIioLib(self.pl)  # 繋ぐときに読む（_PlutoSdrBackend の lib_factory は self.lib を返す）

    def tg(self):
        self.s11(mode="tg")

    def take_tg(self, expect="task_done") -> dict:
        self.clear()
        self.call("reference_take", kind="fine")
        p = self.task_end()
        self.assertEqual(p["code"], expect, p)
        return p


class TestTgFit(Case):
    def test_gain_is_fitted_before_the_reference(self):
        self.tg()
        seen = []
        orig = self.pl.samples

        def rec(n):
            seen.append(self.pl.rx_gain())
            return orig(n)

        self.pl.samples = rec
        p = self.take_tg()
        fitted = self.notices("tg_ref_gain_fitted")
        self.assertEqual(len(fitted), 1)
        d = fitted[0]["detail"]
        self.assertEqual(d["previous_gain_db"], 60.0)
        self.assertFalse(d["was_auto"])
        self.assertFalse(d["overload"])
        g = d["gain_db"]
        # 知らせは基準を取り終える前（合わせてから取る）。終わりの知らせにも選んだ利得が入る
        order = [q["code"] for q in self.notices()]
        self.assertLess(order.index("tg_ref_gain_fitted"), order.index("task_done"))
        self.assertEqual(p["detail"]["phase"], "measure")
        self.assertEqual(p["detail"]["result"]["gain_fitted"]["gain_db"], g)
        # いくつかの利得を試し、基準は選んだ利得で読む
        self.assertGreater(len(set(seen)), 2)
        self.assertEqual(seen[-1], g)
        # 選んだ利得は手動の値として設定に残り、欄（状態）に出る。振り切れる 60dB より下
        pl = self.pluto()
        self.assertEqual(pl["gain_db"], g)
        self.assertFalse(pl["agc"])
        self.assertLess(g, 60.0)
        st = self.call("get_state")
        self.assertEqual(st["normalization"]["selected"]["mode"], "tg")
        # 基準と同じ条件で測るので、すぐ測った比は 0dB 付近
        v = self.single()
        self.assertLess(abs(float(s11t.np.nanmedian(v))), 0.3)

    def test_auto_gain_moves_to_manual(self):
        self.tg()
        self.call("plutosdr_set", agc=True)
        self.take_tg()
        d = self.notices("tg_ref_gain_fitted")[0]["detail"]
        self.assertTrue(d["was_auto"])
        pl = self.pluto()
        self.assertFalse(pl["agc"])
        self.assertEqual(pl["gain_db"], d["gain_db"])

    def test_fit_failure_takes_no_reference(self):
        self.tg()

        def boom(*a, **k):
            raise DeviceError("broken")

        self.inst._device.inner.fit_gain = boom
        p = self.take_tg("task_failed")
        self.assertEqual(p["detail"]["error"]["code"], "tg_ref_fit_failed")
        st = self.call("get_state")
        self.assertIsNone(st["normalization"]["selected"])
        self.assertIsNone(st["plutosdr"]["gain_lock"])
        self.assertIsNone(st["plutosdr"]["tx_atten_lock"])
        self.assertEqual(st["plutosdr"]["gain_db"], 60.0)
        self.assertEqual(self.notices("tg_ref_gain_fitted"), [])

    def test_overload_at_the_lowest_gain_takes_no_reference(self):
        self.tg()
        self.pl.loss_db = -40.0  # いちばん小さい利得でも振り切れる強さ
        self.call("plutosdr_set", tx_atten_db=0.0)
        p = self.take_tg("task_failed")
        self.assertEqual(p["detail"]["error"]["code"], "tg_ref_overload")
        st = self.call("get_state")
        self.assertIsNone(st["normalization"]["selected"])
        self.assertIsNone(st["plutosdr"]["tx_atten_lock"])
        d = self.notices("tg_ref_gain_fitted")[0]["detail"]
        self.assertTrue(d["overload"])
        self.assertEqual(st["plutosdr"]["gain_db"], d["gain_db"])  # 選んだ（いちばん小さい）利得は知らせて残す

    def test_cancel_during_the_fit(self):
        self.tg()
        started = threading.Event()

        def slow(sweep, cancel=None, progress=None):
            started.set()
            while not cancel.is_set():
                time.sleep(0.01)
            raise SweepDiscarded("cancelled", {})

        self.inst._device.inner.fit_gain = slow
        self.clear()
        tid = self.call("reference_take", kind="fine")["task_id"]
        self.assertTrue(started.wait(10))
        st = self.call("get_state")
        task = next(t for t in st["tasks"] if t["task_id"] == tid)
        self.assertEqual(task["detail"]["phase"], "fit_gain")
        # 合わせている間（基準を取り始めた時点）から変えさせない
        self.assertEqual(st["plutosdr"]["gain_lock"], TG_LOCK)
        self.assertEqual(st["plutosdr"]["tx_atten_lock"], TG_LOCK)
        with self.assertRaises(CommandError) as cm:
            self.call("plutosdr_set", tx_atten_db=-20.0)
        self.assertEqual(cm.exception.code, TG_LOCK)
        self.call("cancel_task", task_id=tid)
        p = self.task_end()
        self.assertEqual(p["code"], "task_cancelled")
        st = self.call("get_state")
        self.assertIsNone(st["normalization"]["selected"])
        self.assertIsNone(st["plutosdr"]["tx_atten_lock"])
        self.assertEqual(st["plutosdr"]["gain_db"], 60.0)


class TestTgLocks(Case):
    def assert_locked(self, code=TG_LOCK):
        pl = self.pluto()
        self.assertEqual(pl["gain_lock"], code)
        self.assertEqual(pl["tx_atten_lock"], code)
        for kw in ({"gain_db": 40.0}, {"agc": True}, {"tx_atten_db": -20.0}):
            with self.assertRaises(CommandError) as cm:
                self.call("plutosdr_set", **kw)
            self.assertEqual(cm.exception.code, code, kw)
        with self.assertRaises(CommandError) as cm:
            self.call("plutosdr_fit_gain")
        self.assertEqual(cm.exception.code, code)
        self.call("plutosdr_set", tx_atten_db=-10.0)  # 同じ値は断らない（変えていない）

    def assert_free(self):
        pl = self.pluto()
        self.assertIsNone(pl["gain_lock"])
        self.assertIsNone(pl["tx_atten_lock"])
        self.call("plutosdr_set", tx_atten_db=-11.0)
        self.call("plutosdr_set", tx_atten_db=-10.0)

    def test_until_the_reference_is_discarded(self):
        self.tg()
        self.assert_free()
        self.take_tg()
        self.assert_locked()
        g = self.pluto()["gain_db"]
        # 基準を使ったまま取り直しても、利得は合わせ直さない（決まっている利得のまま）
        self.take_tg()
        self.assertEqual(self.notices("tg_ref_gain_fitted"), [])
        self.assertEqual(self.pluto()["gain_db"], g)
        self.assert_locked()
        self.call("reference_select", id=None)
        self.assert_free()

    def test_leaving_tg_frees_and_coming_back_locks_again(self):
        self.tg()
        self.take_tg()
        self.assert_locked()
        self.call("set_mode", mode="sa")
        self.assert_free()
        self.call("set_mode", mode="tg")
        self.assert_locked()  # 選んでいる TG の基準がまた当たる

    def test_power_cycle_while_taking_frees(self):
        self.tg()
        started = threading.Event()

        def slow(sweep, cancel=None, progress=None):
            started.set()
            while not cancel.is_set():
                time.sleep(0.01)
            raise SweepDiscarded("cancelled", {})

        self.inst._device.inner.fit_gain = slow
        self.clear()
        self.call("reference_take", kind="fine")
        self.assertTrue(started.wait(10))
        self.assertEqual(self.pluto()["tx_atten_lock"], TG_LOCK)
        self.call("disconnect")
        self.assertEqual(self.task_end()["code"], "task_failed")
        self.call("connect", backend="plutosdr", serial=FAKE_SERIAL)
        self.assertIsNone(self.call("get_state")["normalization"]["selected"])
        self.assert_free()

    def test_power_cycle_keeps_the_selected_reference_locked(self):
        """電源を入れ直しても選んでいる TG の基準は当たり続ける（「電源の入れ直し前」の印付き）ので、固定も続く。"""
        self.tg()
        self.take_tg()
        self.call("disconnect")
        self.call("connect", backend="plutosdr", serial=FAKE_SERIAL)
        self.assertEqual(self.call("get_state")["normalization"]["selected"]["mode"], "tg")
        self.assert_locked()
        self.call("reference_select", id=None)
        self.assert_free()

    def test_s11_reference_does_not_lock_tg(self):
        self.tg()
        self.call("set_mode", mode="s11")
        self.call("reference_take", standard="open", kind="fine")
        self.assertEqual(self.task_end()["code"], "task_done")
        self.clear()
        self.call("reference_take", standard="short")
        self.assertEqual(self.task_end()["code"], "task_done")
        self.assertEqual(self.call("get_state")["normalization"]["selected"]["mode"], "s11")
        self.assert_locked(S11_LOCK)
        self.call("set_mode", mode="tg")
        self.assert_free()
        # TG の基準は無いので、取る前に利得を合わせる
        self.take_tg()
        self.assertEqual(len(self.notices("tg_ref_gain_fitted")), 1)
        self.assert_locked()

    def test_tg_reference_does_not_lock_s11(self):
        self.tg()
        self.take_tg()
        self.assert_locked()
        self.call("set_mode", mode="s11")
        self.assert_free()

    def test_gigast_tg_has_no_lock_and_no_fit(self):
        self.call("connect", backend="simulator", serial="SIM00001")
        self.call("set_mode", mode="tg")
        self.clear()
        self.call("reference_take")
        p = self.task_end()
        self.assertEqual(p["code"], "task_done")
        self.assertNotIn("gain_fitted", p["detail"]["result"])
        self.assertEqual(self.notices("tg_ref_gain_fitted"), [])
        pl = self.call("get_state")["plutosdr"]
        self.assertIsNone(pl["gain_lock"])
        self.assertIsNone(pl["tx_atten_lock"])


if __name__ == "__main__":
    unittest.main()


class TestHoldWhileSaving(Case):
    def test_hold_continues_until_the_reference_is_saved_and_selected(self):
        """基準を取り終えてから保存が済んで選ぶまでの間も、利得と送信の減衰を変えさせない（設計Q §7.9）。"""
        from pygigast.core import instrument_files as inf

        gate = threading.Event()
        entered = threading.Event()
        real = inf.save_reference

        def slow_save(*a, **kw):
            entered.set()
            gate.wait(10)
            return real(*a, **kw)

        inf.save_reference = slow_save
        try:
            self.tg()
            self.clear()
            self.call("reference_take", kind="fine")
            self.assertTrue(entered.wait(30), "保存に入らない")
            for kw in ({"tx_atten_db": -20.0}, {"gain_db": 10.0}):
                with self.assertRaises(CommandError) as cm:
                    self.call("plutosdr_set", **kw)
                self.assertEqual(cm.exception.code, TG_LOCK, kw)
        finally:
            gate.set()
            inf.save_reference = real
        self.assertEqual(self.task_end()["code"], "task_done")
        with self.assertRaises(CommandError):
            self.call("plutosdr_set", tx_atten_db=-20.0)  # 選んだ後も固定
