"""校正作業の命令（設計K §4.1〜§4.5。core/instrument_cal.py）をシミュレータで通す。

シミュレータには受信の周波数特性と RBW ごとの利得の差を持たせ（SynthConfig.freq_gain_db・rbw_gain_db）、外部の SG は
入力の信号（signals）で置く。確かめること:
- 周波数特性: 1点を測ると（Span 1MHz・RBW 180kHz・30回のパワー平均・イメージ除去なし）補正値が入り、次の点へ進む。
  信号が無ければ入れずに理由を残す。いまの補正と 6dB を超えて違えば確かめ待ち。飛ばす・やめる
- 測る間もメイン画面の条件は変わらず、終わったら測る前の動作に戻る。測る間は条件と動作を変えられない
- 保存: 新しいファイルを足し（古いものは消さない）、これから使う校正にする。換算に効く
- RBW（全自動。SG で 500MHz を出す）・NF の増幅度・旧ファイルの取り込み（測った値との違いの印）
- 保存していない作業がある間は種類を切り替えない（捨てると決めたら切り替える）・切れたら測るのをやめる
"""
from __future__ import annotations

import time

from pygigast.core.level import cal_work as cw
from pygigast.core.instrument_common import CommandError
from tests.instrument_helpers import SERIAL, InstrumentCase
from tests.test_level import build_legacy_bytes

GAIN = ((0, 0.0), (12_000_000_000, -20.0))  # 受信の利得: 0Hz で 0dB、12GHz で −20dB の直線
B3_DB = -1.0  # シミュレータの b3=1 のレベルの差（イメージ除去なしは b3=1）


def gain_at(f: int) -> float:
    return -20.0 * f / 12_000_000_000


class CalCase(InstrumentCase):
    def setUp(self):
        super().setUp()
        self.connect(400_000_000, 600_000_000)
        self.dev = self.inst._device
        self.dev.config.freq_gain_db = GAIN

    def work(self) -> dict:
        return self.call("get_state")["calibration_work"]

    def measure(self, **kw) -> dict:
        """測って終わりを待ち、終わりの知らせ（task_done など）を返す。"""
        self.clear_events()
        r = self.call("cal_measure", **kw)
        return self.wait_task(r["task_id"])

    def sg(self, f: int, dbm: float = -10.0) -> None:
        self.dev.config.signals = [(f, dbm)]


class FreqResponseTest(CalCase):
    def test_measure_point_and_save(self):
        self.call("set_mode", mode="sa")
        self.call("run", mode="continuous")
        before = self.call("get_state")["sweep"]
        r = self.call("cal_open", kind="freq")
        self.assertEqual(r["calibration_work"]["kind"], "freq")
        self.assertEqual(r["calibration_work"]["serial"], SERIAL)
        self.assertIsNone(r["calibration_work"]["base"])  # まだ校正データが無い
        self.call("cal_set", current=5)
        self.sg(500_000_000)
        self.clear_events()
        t = self.call("cal_measure")
        # 測る間はメイン画面の条件と動作を変えられない（K §4.1）
        st = self.call("get_state")
        self.assertEqual(st["run"]["state"], "calibration")
        self.assertEqual(st["sweep"], before)
        with self.assertRaises(CommandError) as cm:
            self.call("set_sweep", start_hz=100_000_000)
        self.assertEqual(cm.exception.code, "calibration_in_progress")
        with self.assertRaises(CommandError):
            self.call("cal_measure")
        done = self.wait_task(t["task_id"])
        self.assertEqual(done["code"], "task_done")
        self.assertEqual(done["detail"]["task"], "cal_point")
        w = self.work()
        p = w["points"][5]
        self.assertEqual(p["status"], "measured")
        expect = -10.0 - (-10.0 + B3_DB + gain_at(500_000_000))
        self.assertAlmostEqual(p["after_db"], expect, delta=0.05)
        self.assertEqual(w["current"], 6)
        self.assertEqual(w["unsaved"], 1)
        # 終わったら測る前の動作（連続）に戻り、条件は変わっていない
        st = self.call("get_state")
        self.assertEqual(st["run"]["state"], "continuous")
        self.assertEqual(st["sweep"], before)
        # 保存: 新しいファイルを足し、これから使う校正にする
        r = self.call("cal_save")
        self.assertTrue(r["file"].endswith(".json"))
        self.assertEqual(r["calibration_work"]["unsaved"], 0)
        self.assertEqual(r["calibration_work"]["saved"]["file"], r["file"])
        self.assertEqual(r["calibration_work"]["base"]["file"], r["file"])
        st = self.call("get_state")
        self.assertEqual(st["calibration"]["source"]["kind"], cw.SOURCE_KIND)
        self.assertAlmostEqual(self.inst._cal.level_offset_db, expect, delta=0.05)
        store = self.inst._cal_store
        self.assertEqual(store.current_file(SERIAL).name, r["file"])
        self.assertEqual(len(store.history(SERIAL)), 1)

    def test_saved_calibration_corrects_the_reading(self):
        self.call("cal_open", kind="freq")
        self.call("cal_set", current=15, sg_level_dbm=-20.0, cable_loss_db=1.5)
        self.sg(1_500_000_000, -21.5)  # SG −20dBm・ケーブル 1.5dB → SP 入力に −21.5dBm
        self.assertEqual(self.measure()["code"], "task_done")
        self.call("cal_save")
        self.call("cal_close")
        self.call("set_sweep", start_hz=1_499_000_000, stop_hz=1_501_000_000, rbw_hz=180_000, image_reject="off")
        self.call("set_mode", mode="sa")
        self.single()
        tr = self.call("get_trace", id=1)
        peak = max(v for v in tr["values"] if v is not None)
        # 換算後は SP 入力のレベル（SG − ケーブルの損失）
        self.assertAlmostEqual(peak, -21.5, delta=0.3)

    def test_no_signal_keeps_previous_value(self):
        self.call("cal_open", kind="freq")
        self.call("cal_set", current=20)
        self.dev.config.signals = []
        done = self.measure()
        self.assertEqual(done["code"], "task_failed")
        self.assertEqual(done["detail"]["error"]["code"], "cal_no_signal")
        w = self.work()
        self.assertEqual(w["points"][20]["status"], "none")
        self.assertEqual(w["current"], 20)
        self.assertEqual(w["message"]["code"], "cal_no_signal")
        self.assertEqual(w["unsaved"], 0)
        with self.assertRaises(CommandError) as cm:
            self.call("cal_save")
        self.assertEqual(cm.exception.code, "calibration_nothing_to_save")
        self.assertEqual(self.call("get_state")["run"]["state"], "stopped")

    def test_big_difference_needs_confirmation(self):
        p = self.root / "gigast.txt"
        p.write_bytes(build_legacy_bytes())
        self.call("import_legacy_calibration", path=str(p), serial=SERIAL)
        w = self.call("cal_open", kind="freq")["calibration_work"]
        self.assertEqual(w["base"]["source"]["kind"], "legacy_file")
        base_total = w["points"][15]["before_db"]
        self.call("cal_set", current=15)
        # 補正値が前より 10dB 大きくなる入力: 補正 = −10 − 読み = 前 + 10 → 読み = −20 − 前
        self.sg(1_500_000_000, -20.0 - base_total - B3_DB - gain_at(1_500_000_000))
        done = self.measure()
        self.assertTrue(done["detail"]["result"]["pending"])
        w = self.work()
        self.assertIsNotNone(w["pending"])
        self.assertEqual(w["points"][15]["status"], "none")
        for name, kw in (("cal_measure", {}), ("cal_skip", {}), ("cal_save", {})):
            with self.assertRaises(CommandError) as cm:
                self.call(name, **kw)
            self.assertIn(cm.exception.code, ("calibration_confirm_pending",), name)
        self.call("cal_confirm", accept=True)
        w = self.work()
        self.assertIsNone(w["pending"])
        self.assertEqual(w["points"][15]["status"], "measured")
        with self.assertRaises(CommandError) as cm:
            self.call("cal_confirm", accept=True)
        self.assertEqual(cm.exception.code, "calibration_no_pending")

    def test_skip_and_select(self):
        self.call("cal_open", kind="freq")
        self.call("cal_set", current=120)
        w = self.call("cal_skip")["calibration_work"]
        self.assertEqual(w["points"][120]["status"], "skipped")
        self.assertEqual(w["current"], 0)
        with self.assertRaises(CommandError):
            self.call("cal_set", current=121)
        with self.assertRaises(CommandError):
            self.call("cal_set", sg_level_dbm=50.0)
        with self.assertRaises(CommandError):
            self.call("cal_set", cable_loss_db=-1.0)

    def test_cancel(self):
        self.call("run", mode="continuous")
        self.call("cal_open", kind="freq")
        self.sg(10_000_000)
        self.clear_events()
        t = self.call("cal_measure")
        self.call("cancel_task", task_id=t["task_id"])
        done = self.wait_task(t["task_id"])
        self.assertEqual(done["code"], "task_cancelled")
        w = self.work()
        self.assertEqual(w["points"][0]["status"], "none")
        self.assertEqual(w["message"], {"code": "cancelled", "task": "cal_point", "detail": {}})
        self.assertIsNone(w["task_id"])
        self.assertEqual(self.call("get_state")["run"]["state"], "continuous")

    def test_disconnect_stops_measuring(self):
        self.call("cal_open", kind="freq")
        self.sg(10_000_000)
        self.clear_events()
        t = self.call("cal_measure")
        self.call("disconnect")
        done = self.wait_task(t["task_id"])
        self.assertEqual(done["code"], "task_failed")
        w = self.work()
        self.assertEqual(w["device_reason"], "not_connected")
        self.assertEqual(w["message"]["code"], "disconnected")
        with self.assertRaises(CommandError) as cm:
            self.call("cal_measure")
        self.assertEqual(cm.exception.code, "not_connected")

    def test_other_device_is_refused(self):
        self.call("cal_open", kind="freq")
        self.inst._cal_session.serial = "OTHER0001"  # 測った値は別の機器のもの（画面は理由を出す）
        self.assertEqual(self.work()["device_reason"], "calibration_device_changed")
        with self.assertRaises(CommandError) as cm:
            self.call("cal_measure")
        self.assertEqual(cm.exception.code, "calibration_device_changed")


class KindTest(CalCase):
    def test_switch_needs_discard_when_unsaved(self):
        self.call("cal_open", kind="freq")
        self.sg(10_000_000)
        self.measure()
        with self.assertRaises(CommandError) as cm:
            self.call("cal_set_kind", kind="rbw")
        self.assertEqual(cm.exception.code, "calibration_unsaved")
        self.assertEqual(cm.exception.detail["count"], 1)
        # 開き直しても作業は消えない（保存していない作業を黙って捨てない）
        self.assertEqual(self.call("cal_open", kind="rbw")["calibration_work"]["kind"], "freq")
        w = self.call("cal_set_kind", kind="rbw", discard=True)["calibration_work"]
        self.assertEqual(w["kind"], "rbw")
        self.assertEqual(w["unsaved"], 0)
        self.call("cal_close")
        self.assertIsNone(self.call("get_state")["calibration_work"])
        with self.assertRaises(CommandError) as cm:
            self.call("cal_measure")
        self.assertEqual(cm.exception.code, "calibration_not_open")


class RbwTest(CalCase):
    def test_full_auto(self):
        self.dev.config.rbw_gain_db = {30_000: -0.5, 15_000: -1.6}
        self.call("cal_open", kind="rbw")
        self.call("cal_set", rbw_extra=True)
        self.clear_events()
        t = self.call("cal_measure")
        # SG で 500MHz を出している（状態バーの「SG 出力中」: 設計L）
        st = self.wait_for(lambda k, p: k == "state" and p["run"]["state"] == "calibration")
        self.assertEqual(st["source"], {"output": "sg", "freq_hz": 500_000_000})
        # SG の欄の状態も機器へ渡している信号源で決める（メインのモードが SA のままでも「SG 出力中 500MHz」: 設計J §4.12）。
        # Qt と Web は同じ文（Web は状態に付けた ui.sg_state）
        from pygigast.api.web import view as web_view
        from pygigast.ui_qt import texts as qt_texts

        self.assertNotEqual(st["sweep"]["mode"], "sg")
        want = qt_texts.tpl("source_sg", freq=qt_texts.format_freq(500_000_000))
        self.assertEqual(qt_texts.sg_state_text(st), want)
        self.assertEqual(web_view.state_view(st)["ui"]["sg_state"], want)
        done = self.wait_task(t["task_id"])
        self.assertEqual(done["code"], "task_done")
        w = self.work()
        self.assertEqual([f["freq_hz"] for f in w["rbw"]], [500_000_000, 100_000_000, 1_500_000_000, 3_000_000_000])
        items = {i["item"]: i["after"] for i in w["items"]}
        self.assertAlmostEqual(items["rbw:30000"], 0.5, delta=0.1)
        self.assertAlmostEqual(items["rbw:15000"], 1.6, delta=0.1)
        self.call("cal_save")
        self.assertAlmostEqual(self.inst._cal.rbw_offset_db[15_000], 1.6, delta=0.1)
        self.assertEqual(self.inst._cal.rbw_offset_db[180_000], 0.0)

    def test_no_direct_connection_stops_at_first_step(self):
        self.dev.config.sg_dbm = -120.0  # TG 出力と SP 入力がつながっていない
        self.call("cal_open", kind="rbw")
        done = self.measure()
        self.assertEqual(done["code"], "task_failed")
        self.assertEqual(done["detail"]["error"]["code"], "cal_no_signal_direct")
        self.assertEqual(done["detail"]["error"]["detail"]["rbw_hz"], 180_000)  # 最初の段で止まる
        self.assertIsNone(self.work()["rbw"])


class NfTest(CalCase):
    def test_amp_from_two_states(self):
        self.call("cal_open", kind="nf")
        self.call("cal_set", nf_freq_hz=1_000_000_000)
        self.dev.config.nf_hot_db = 4.0
        self.assertEqual(self.measure(state="with_att")["code"], "task_done")
        self.dev.config.nf_hot_db = 10.5
        self.assertEqual(self.measure(state="without_att")["code"], "task_done")
        nf = self.work()["nf"]
        self.assertAlmostEqual(nf["diff_db"], 6.5, delta=0.1)
        self.assertAlmostEqual(nf["amp_raw"], cw.nf_amp_raw(6.5), delta=1.5)
        self.call("cal_save")
        self.assertAlmostEqual(self.inst._cal.nf.amp_raw, nf["amp_raw"], delta=0.01)
        with self.assertRaises(CommandError):
            self.call("cal_measure")  # どちらの状態かを言わない

    def test_no_noise_difference_and_swapped(self):
        self.call("cal_open", kind="nf")
        self.dev.config.nf_hot_db = 0.0
        done = self.measure(state="with_att")
        self.assertEqual(done["detail"]["error"]["code"], "cal_nf_no_difference")
        self.dev.config.nf_hot_db = 10.0
        self.measure(state="with_att")
        self.dev.config.nf_hot_db = 4.0
        self.measure(state="without_att")
        w = self.work()
        self.assertEqual(w["message"]["code"], "cal_nf_att_no_difference")
        self.assertFalse(w["savable"])


class LegacyTest(CalCase):
    def test_load_compare_and_save(self):
        # 先に校正作業で RBW を測っておく（旧ファイルの値と比べる材料）
        self.dev.config.rbw_gain_db = {30_000: -0.4}
        self.call("cal_open", kind="rbw")
        self.measure()
        self.call("cal_save")
        self.call("cal_set_kind", kind="legacy")
        p = self.root / "gigast.txt"
        p.write_bytes(build_legacy_bytes())
        w = self.call("cal_legacy_load", path=str(p))["calibration_work"]
        self.assertEqual(w["legacy"]["file_name"], "gigast.txt")
        items = {i["item"]: i for i in w["items"]}
        self.assertTrue(items["rbw:30000"]["warn"])  # 旧ファイル −2dB・測った約 +0.4dB
        self.assertAlmostEqual(items["rbw:30000"]["measured"], 0.4, delta=0.1)
        self.assertFalse(items["level"]["warn"])
        # 保存するまで校正データは変わらない
        self.assertEqual(self.call("get_state")["calibration"]["source"]["kind"], cw.SOURCE_KIND)
        r = self.call("cal_save")
        st = self.call("get_state")
        self.assertEqual(st["calibration"]["source"]["kind"], "legacy_file")
        self.assertEqual(len(self.inst._cal_store.history(SERIAL)), 2)
        self.assertEqual(self.inst._cal.work["previous"]["source"], cw.SOURCE_KIND)
        self.assertTrue(r["file"])

    def test_unreadable_file(self):
        self.call("cal_open", kind="legacy")
        p = self.root / "bad.txt"
        p.write_bytes(b"x\r\n")
        with self.assertRaises(CommandError) as cm:
            self.call("cal_legacy_load", path=str(p))
        self.assertEqual(cm.exception.code, "legacy_unreadable")
        with self.assertRaises(CommandError) as cm:
            self.call("cal_legacy_load", path=str(self.root / "none.txt"))
        self.assertEqual(cm.exception.code, "file_unreadable")


class NotConnectedTest(InstrumentCase):
    def test_open_without_device(self):
        w = self.call("cal_open", kind="freq")["calibration_work"]
        self.assertIsNone(w["serial"])
        self.assertEqual(w["device_reason"], "not_connected")
        with self.assertRaises(CommandError) as cm:
            self.call("cal_measure")
        self.assertEqual(cm.exception.code, "not_connected")
        # つなぐと、作業が空なのでその機器を当てる先にする
        self.connect()
        end = time.monotonic() + 10
        while self.call("get_state")["calibration_work"]["serial"] != SERIAL:
            self.assertLess(time.monotonic(), end)
            time.sleep(0.05)
        self.assertIsNone(self.call("get_state")["calibration_work"]["device_reason"])
