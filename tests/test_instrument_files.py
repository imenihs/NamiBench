"""計測器の本体: ノーマライズの基準・保存と開く・CSV・記録・救出・長い処理（設計A §4.3.1・設計E・設計H）。"""
import csv
import json
import unittest
from pathlib import Path

import numpy as np

from pygigast.core.instrument import CommandError
from pygigast.core.storage.h5file import FileHeader, H5Record
from pygigast.core.storage.recording import Recorder, RecordingReader
from pygigast.core.types import AdoptedSweep, FreqAxis, Mode, RawSweep
from tests.instrument_helpers import SERIAL, InstrumentCase


class ReferenceTest(InstrumentCase):
    def take_wide(self, name=None):
        r = self.call("reference_take", kind="wide", name=name)
        n = self.wait_task(r["task_id"])
        self.assertEqual(n["code"], "task_done", n)
        return n["detail"]["result"]["reference_id"]

    def test_wide_reference_normalizes_tg(self):
        self.connect(100_000_000, 200_000_000)
        self.call("set_mode", mode="tg")
        # 基準を選ぶ前: 電力のまま、理由は「基準を選んでいない」（設計E §6.4）
        st = self.single()
        self.assertEqual(st["normalization"]["reason"], "no_reference_selected")
        self.clear_events()
        ref_id = self.take_wide("ケーブルA")
        # 進み具合が出た
        self.assertTrue(any(k == "progress" and p.get("kind") == "task" for k, p in self.events))
        # 取った基準ですぐ測る（設計E §6.2）: 比（dB）で 0dB 付近
        t = self.wait_for(lambda k, p: k == "trace" and p["trace"] == 1 and p["quantity"] == "ratio")
        self.assertEqual(t["unit"], "dB")
        self.assertLess(abs(float(np.nanmedian(t["values"]))), 1.0)
        st = self.wait_for(lambda k, p: k == "state" and p["run"]["last_stop_reason"] == "single_done"
                           and p["normalization"].get("applied"))
        self.assertEqual(st["normalization"]["selected"]["id"], ref_id)
        self.assertEqual(st["normalization"]["selected"]["name"], "ケーブルA")
        self.assertEqual(st["normalization"]["accuracy"], "coarse")
        self.assertTrue((self.root / "data" / "references" / SERIAL / f"{ref_id}.h5").exists())
        # 状態の既存の掃引条件はいまの条件のまま（基準の掃引の条件に置き換わらない）
        self.assertEqual((st["sweep"]["start_hz"], st["sweep"]["stop_hz"]), (100_000_000, 200_000_000))

        # 一覧・名前の変更・選ばない・選ぶ・消す（設計E §6.1）
        refs = self.call("reference_list")["references"]
        self.assertEqual([(r["id"], r["selected"]) for r in refs], [(ref_id, True)])
        self.call("reference_rename", id=ref_id, name="ケーブルB")
        self.assertEqual(self.call("get_state")["normalization"]["selected"]["name"], "ケーブルB")
        self.call("reference_select", id=None)
        st = self.single()
        self.assertFalse(st["normalization"]["applied"])
        self.call("reference_select", id=ref_id)
        self.assertEqual(self.call("get_state")["normalization"]["selected"]["name"], "ケーブルB")
        with self.assertRaises(CommandError) as cm:
            self.call("reference_select", id="../x")
        self.assertEqual(cm.exception.code, "bad_reference_id")
        self.assertTrue(self.call("reference_delete", id=ref_id)["deleted"])
        self.assertIsNone(self.call("get_state")["normalization"]["selected"])

    def test_wide_reference_ignores_span_lock(self):
        # Span 固定中でも「広く取る」の範囲はアプリが決めた範囲のまま（固定の規則を通さない: C §5.12 の one_shot）
        from pygigast.core.normalize.reference import wide_range_hz
        self.connect(100_000_000, 200_000_000)
        self.call("set_mode", mode="tg")
        self.call("set_sweep", span_lock=True)
        self.take_wide()
        lo, hi, _step = wide_range_hz(self.inst._profile)
        seg = self.inst._ref.segments[0]
        self.assertEqual(seg.axis.start_hz, lo)
        self.assertGreaterEqual(seg.axis.last_hz, hi - seg.axis.step_hz)
        self.assertEqual(self.call("get_state")["sweep"]["span_lock_hz"], 100_000_000)

    def test_fine_reference_merges_into_selected(self):
        self.connect(100_000_000, 200_000_000)
        self.call("set_mode", mode="tg")
        ref_id = self.take_wide()
        self.wait_for(lambda k, p: k == "state" and p["run"]["last_stop_reason"] == "single_done")
        r = self.call("reference_take", kind="fine")
        self.assertEqual(self.wait_task(r["task_id"])["detail"]["result"]["reference_id"], ref_id)
        st = self.wait_for(lambda k, p: k == "state" and p["normalization"].get("accuracy") == "fine")
        self.assertTrue(st["normalization"]["applied"])

    def test_reference_restored_on_reconnect_after_restart(self):
        self.connect(100_000_000, 200_000_000)
        self.call("set_mode", mode="tg")
        ref_id = self.take_wide()
        self.inst.shutdown()
        self.inst = self.make_instrument()
        self.call("connect", backend="simulator", serial=SERIAL)
        st = self.wait_for(lambda k, p: k == "state" and (p["normalization"]["selected"] or {}).get("id") == ref_id)
        self.assertEqual(st["sweep"]["mode"], "tg")

    def test_cancel_reference_keeps_previous(self):
        self.connect(100_000_000, 200_000_000)
        self.call("set_mode", mode="tg")
        r = self.call("reference_take", kind="wide", averages=50)
        with self.assertRaises(CommandError) as cm:
            self.call("set_sweep", span_hz=10_000_000)
        self.assertEqual(cm.exception.code, "reference_in_progress")
        self.assertEqual(self.call("get_state")["tasks"][0]["kind"], "reference_take")
        self.call("cancel_task", task_id=r["task_id"])
        self.assertEqual(self.wait_task(r["task_id"])["code"], "task_cancelled")
        st = self.call("get_state")
        self.assertIsNone(st["normalization"]["selected"])
        self.assertEqual(st["run"]["state"], "stopped")
        self.assertEqual(st["tasks"], [])
        with self.assertRaises(CommandError) as cm:
            self.call("cancel_task", task_id=r["task_id"])
        self.assertEqual(cm.exception.code, "unknown_task")

    def test_reference_needs_tg_mode_and_s11_two_steps(self):
        self.connect()
        with self.assertRaises(CommandError) as cm:
            self.call("reference_take", kind="wide")
        self.assertEqual(cm.exception.code, "mode_not_normalizable")
        self.call("set_mode", mode="s11")
        with self.assertRaises(CommandError) as cm:
            self.call("reference_take", kind="fine", standard="short")
        self.assertEqual(cm.exception.code, "s11_open_missing")
        r = self.call("reference_take", kind="fine", standard="open")
        self.assertEqual(self.wait_task(r["task_id"])["detail"]["result"]["next"], "short")
        r = self.call("reference_take", kind="fine", standard="short")
        n = self.wait_task(r["task_id"])
        self.assertEqual(n["code"], "task_done", n)
        t = self.wait_for(lambda k, p: k == "trace" and p["quantity"] == "ratio")
        self.assertLess(abs(float(np.nanmedian(t["values"]))), 1.0)


class SaveOpenExportTest(InstrumentCase):
    def test_save_open_round_trip_and_csv(self):
        self.connect()
        self.call("set_trace", number=2, action="max_hold", visible=True)
        self.call("marker_add", kind="M", trace=1, freq_hz=100_000_000)
        self.single()
        path = self.root / "a.h5"
        r = self.call("save", path=str(path), note="メモ")
        n = self.wait_task(r["task_id"])
        self.assertEqual(n["code"], "task_done", n)
        with H5Record(path) as rec:
            self.assertEqual(rec.header.note, "メモ")
            # 表示していたトレース（1 と 2）だけ。3・4 は非表示で値はあっても入れない（設計H §4・§7.1）
            self.assertEqual([t.number for t in rec.traces()], [1, 2])
            self.assertEqual(rec.markers()[0]["target_hz"], 100_000_000)
            self.assertEqual(rec.segments[0].n_sweeps, 1)

        self.clear_events()
        r = self.call("open", paths=[str(path), str(self.root / "none.h5")])
        n = self.wait_task(r["task_id"])
        self.assertEqual(n["code"], "task_done", n)
        res = n["detail"]["result"]
        self.assertEqual(len(res["files"]), 1)
        self.assertEqual(len(res["errors"]), 1)
        fid = res["files"][0]["file_id"]
        ids = [t["id"] for t in res["files"][0]["traces"]]
        self.assertIn(f"{fid}.1", ids)
        t = self.wait_for(lambda k, p: k == "trace" and p["trace"] == f"{fid}.1")
        self.assertEqual((t["source"], t["unit"]), ("file", "dBm"))
        self.assertEqual(t["origin"]["file"], "a.h5")
        live = self.call("get_trace", id=1)
        n_disp = len(t["values"])
        np.testing.assert_allclose(t["values"], live["values"][:n_disp], atol=1e-3)
        self.assertEqual(self.call("get_trace", id=f"{fid}.1")["trace"], f"{fid}.1")
        self.assertEqual(self.call("get_state")["files"][0]["file_id"], fid)

        # CSV: 生きたトレースと参照トレースを並べる（同じ軸）
        out = self.root / "a.csv"
        r = self.call("export_csv", path=str(out), traces=[1, f"{fid}.1"], start_hz=80_000_000, stop_hz=120_000_000)
        n = self.wait_task(r["task_id"])
        self.assertEqual(n["code"], "task_done", n)
        with open(out, newline="", encoding="ascii") as f:
            first = f.readline()
            self.assertTrue(first.startswith("# meta: "))
            meta = json.loads(first[len("# meta: "):])
            self.assertEqual(meta["device"]["serial"], SERIAL)
            rows = list(csv.reader(f))
        self.assertEqual(rows[0], ["frequency_hz", "trace1_dBm", "trace1_dBm"])
        freqs = [int(r[0]) for r in rows[1:]]
        self.assertTrue(80_000_000 <= freqs[0] and freqs[-1] <= 120_000_000)
        self.assertEqual(n["detail"]["result"]["rows"], len(rows) - 1)

        self.call("close_file", file_id=fid)
        self.assertEqual(self.call("get_state")["files"], [])
        with self.assertRaises(CommandError):
            self.call("close_file", file_id=fid)

    def test_save_keeps_only_visible_traces_and_raw(self):
        # 非表示にしたトレースは値があっても保存しない。生の値は表示に関係なく残す（設計H §4・§7.1）
        self.connect()
        self.call("set_trace", number=2, action="max_hold", visible=True)
        self.single()
        self.call("set_trace", number=1, visible=False)
        st = self.call("get_state")
        self.assertTrue(st["traces"][0]["has_values"])
        path = self.root / "v.h5"
        n = self.wait_task(self.call("save", path=str(path))["task_id"])
        self.assertEqual(n["code"], "task_done", n)
        with H5Record(path) as rec:
            self.assertEqual([t.number for t in rec.traces()], [2])
            self.assertEqual(rec.segments[0].n_sweeps, 1)
        # 全部非表示でも、生の値があれば保存できる（トレースは空）
        self.call("set_trace", number=2, visible=False)
        path = self.root / "raw_only.h5"
        n = self.wait_task(self.call("save", path=str(path))["task_id"])
        self.assertEqual(n["code"], "task_done", n)
        with H5Record(path) as rec:
            self.assertEqual(rec.traces(), [])
            self.assertEqual(rec.segments[0].n_sweeps, 1)

    def test_save_errors(self):
        with self.assertRaises(CommandError) as cm:
            self.call("save", path=str(self.root / "x.h5"))
        self.assertEqual(cm.exception.code, "no_data")
        with self.assertRaises(CommandError) as cm:
            self.call("save")
        self.assertEqual(cm.exception.code, "path_required")
        self.connect()
        self.single()
        r = self.call("save", path=str(self.root / "no_such_dir" / "x.h5"))
        self.assertEqual(self.wait_task(r["task_id"])["code"], "task_failed")


class RecordingTest(InstrumentCase):
    def test_record_survives_stop_then_finish(self):
        self.connect()
        r = self.call("record_start")
        work = Path(r["path"])
        self.assertTrue(self.call("get_state")["recording"]["active"])
        self.call("run", mode="continuous")
        self.wait_for(lambda k, p: k == "state" and p["recording"].get("sweeps", 0) >= 3)
        self.call("record_stop")
        self.call("stop")
        st = self.wait_for(lambda k, p: k == "state" and p["recording"].get("stopped")
                           and p["recording"]["pending"] == 0 and p["run"]["state"] == "stopped")
        committed = st["recording"]["sweeps"]
        # 止めた後も作業ファイルが残り、掃引が読める（まとめる前に落ちても直前の掃引まで残る: 設計H §2）
        self.assertTrue(work.exists())
        with RecordingReader(work) as rd:
            self.assertEqual(rd.n_sweeps, committed)
        h5 = self.root / "rec.h5"
        r = self.call("record_finish", path=str(h5))
        n = self.wait_task(r["task_id"])
        self.assertEqual(n["code"], "task_done", n)
        with H5Record(h5) as rec:
            self.assertEqual(sum(s.n_sweeps for s in rec.segments), committed)
        self.assertFalse(work.exists())
        st = self.call("get_state")
        self.assertFalse(st["recording"]["active"])
        self.assertEqual(st["recording"]["last"]["path"], str(h5))
        with self.assertRaises(CommandError) as cm:
            self.call("record_stop")
        self.assertEqual(cm.exception.code, "not_recording")

    def _make_unfinished(self):
        """落ちた記録の作業ファイルを記録の場所に作る。"""
        d = self.root / "data" / "recordings"
        a = self.call("get_state")["sweep"]
        axis = FreqAxis(a["start_hz"], a["step_hz"], a["points"])
        adopted = AdoptedSweep(a["start_hz"], a["stop_hz"], axis, a["rbw_hz"], a["rbw_preference_hz"], Mode.SA,
                               None, False, 0, 0, 0.1)
        path = d / "active_20260101_000000.sqlite"
        rec = Recorder(path, FileHeader("gigast-v5", SERIAL))
        rec.wait_opened(10)
        for i in range(3):
            rec.append(RawSweep(adopted, np.full(axis.points, 100 + i, dtype=np.uint16), "gigast_ad_u16",
                                1000.0 + i, 0.1))
        rec.stop().result(10)
        return path

    def test_unfinished_list_finish_discard(self):
        path = self._make_unfinished()
        rows = self.call("list_unfinished")["recordings"]
        self.assertEqual([(r["path"], r["n_sweeps"]) for r in rows], [(str(path), 3)])
        h5 = self.root / "rescued.h5"
        r = self.call("finish_unfinished", path=str(path), h5_path=str(h5))
        self.assertEqual(self.wait_task(r["task_id"])["code"], "task_done")
        self.assertTrue(h5.exists())
        self.assertEqual(self.call("list_unfinished")["recordings"], [])
        path = self._make_unfinished()
        self.call("discard_unfinished", path=str(path))
        self.assertFalse(path.exists())
        with self.assertRaises(CommandError) as cm:
            self.call("discard_unfinished", path=str(self.root / "a.sqlite"))
        self.assertEqual(cm.exception.code, "not_a_work_file")


if __name__ == "__main__":
    unittest.main()
