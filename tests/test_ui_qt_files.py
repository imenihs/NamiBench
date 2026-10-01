"""ファイルの操作（保存・開く・ドラッグ＆ドロップ・書き出し・PNG・記録・起動時の救出・閉じるときの確認）を確かめる
（設計H §4・§4.1・§7.3・I §3.4・U-18）。"""
import csv
import unittest
from pathlib import Path

from PySide6.QtCore import QMimeData, QPoint, Qt, QUrl
from PySide6.QtGui import QDragEnterEvent, QDropEvent, QGuiApplication
from PySide6.QtWidgets import QApplication, QLabel, QPushButton

from pygigast.ui_qt import png_export, texts
from pygigast.ui_qt.files import ExportDialog
from pygigast.ui_qt.main_window import MainWindow
from pygigast.ui_qt.prefs import UiPrefs
from tests.test_ui_qt_support import UiBase, make_api


class SaveOpenTest(UiBase):
    def test_save_then_open_as_reference(self):
        self.connect_and_sweep()
        self.save_path = str(self.root / "a.h5")
        self.win.actions["save"].trigger()
        self.wait(lambda: "保存しました: a.h5" in self.win.st_note.text(), what="保存の知らせ")
        self.assertTrue(Path(self.save_path).exists())
        self.assertTrue(self.win.note_button.isVisible())      # そこから保存先を開ける（U-11）
        self.assertEqual(self.win.prefs.get("last_dir"), str(self.root))
        self.open_paths = [self.save_path]
        self.win.actions["open"].trigger()
        self.wait(lambda: self.win.traces.files, what="参照トレース")
        self.pump(0.2)
        tid = next(iter(self.win.traces.files))
        c = self.win.traces.files[tid]["curve"]
        self.assertEqual(c.opts["pen"].style(), Qt.PenStyle.DashLine)
        self.assertGreater(len(c.yData), 100)
        self.assertTrue(any(s.startswith("a.h5 #1") for _c, s, _p in self.win.traces.legend_entries()))
        self.assertIn("開きました: 1 ファイル", self.win.st_note.text())
        self.assertIn(self.state()["files"][0]["file_id"], self.win.trace_tab._file_rows)
        # 外す
        fid = self.state()["files"][0]["file_id"]
        self.win.call("close_file", file_id=fid)
        self.wait(lambda: not self.win.traces.files, what="外す")

    def test_file_with_other_unit_is_not_overlaid(self):
        """電力（dBm）で保存したトレースは、比（dB）の縦軸には重ねない。一覧では無効にして理由（E §6.4・U-08）。"""
        self.connect_and_sweep()
        p = self.root / "p.h5"
        self.cmd("save", path=str(p))
        self.wait(lambda: p.exists(), what="保存")
        self.win.files.open_files([str(p)])
        self.wait(lambda: self.win.traces.files and self.win.trace_tab._file_checks, what="開く")
        tid = next(iter(self.win.traces.files))
        self.assertTrue(self.win.traces.file_drawn(tid))
        self.cmd("set_amplitude", unit="dB")
        self.pump()
        self.assertFalse(self.win.traces.file_drawn(tid))
        self.assertEqual(len(self.win.traces.files[tid]["curve"].yData or []), 0)
        cb = self.win.trace_tab._file_checks[tid]
        self.assertFalse(cb.isEnabled())
        self.assertIn("単位が縦軸", cb.toolTip())

    def test_default_name_and_folder(self):
        seen = {}
        self.win.ask_save_path = lambda title, default, filt: seen.setdefault("d", default) and None
        self.win.prefs.set("last_dir", str(self.root))
        self.win.files.save()
        self.assertTrue(Path(seen["d"]).name.startswith("gigast_"))
        self.assertTrue(seen["d"].endswith(".h5"))
        self.assertEqual(Path(seen["d"]).parent, self.root)

    def test_drag_and_drop_opens(self):
        self.connect_and_sweep()
        p = self.root / "b.h5"
        self.cmd("save", path=str(p))
        self.wait(lambda: p.exists(), what="保存")
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(str(p)), QUrl.fromLocalFile(str(self.root / "x.txt"))])
        enter = QDragEnterEvent(QPoint(50, 50), Qt.DropAction.CopyAction, mime, Qt.MouseButton.LeftButton,
                                Qt.KeyboardModifier.NoModifier)
        QApplication.sendEvent(self.win, enter)
        self.assertTrue(enter.isAccepted())
        drop = QDropEvent(QPoint(50, 50), Qt.DropAction.CopyAction, mime, Qt.MouseButton.LeftButton,
                          Qt.KeyboardModifier.NoModifier)
        QApplication.sendEvent(self.win, drop)
        self.wait(lambda: len(self.state()["files"]) == 1, what="ドロップで開く")

    def test_open_failure_is_reported(self):
        bad = self.root / "bad.h5"
        bad.write_bytes(b"not hdf5")
        self.win.files.open_files([str(bad)])
        self.wait(lambda: "開けなかったもの" in self.win.st_note.text(), what="開けなかった知らせ")
        self.assertIn("bad.h5", self.win.st_note.text())


class ExportTest(UiBase):
    def test_export_current_range(self):
        self.connect_and_sweep(start=100_000_000, stop=1_000_000_000)
        dlg = ExportDialog(self.win)
        dlg.r_custom.setChecked(True)
        dlg.f_start.set_hz(200_000_000)
        dlg.f_stop.set_hz(300_000_000)
        traces, lo, hi = dlg.choice()
        self.assertEqual((traces, lo, hi), ([1], 200_000_000, 300_000_000))
        self.save_path = str(self.root / "e.csv")
        dlg.accepted.connect(lambda: self.win.files._export_chosen(dlg))
        dlg.accept()
        self.wait(lambda: "書き出しました: e.csv" in self.win.st_note.text(), what="書き出しの知らせ")
        with open(self.save_path, encoding="utf-8") as f:
            rows = list(csv.reader(line for line in f if not line.startswith("#")))
        freqs = [int(r[0]) for r in rows[1:]]
        self.assertEqual((min(freqs), max(freqs)), (200_000_000, 300_000_000))
        self.assertIn("101 行", self.win.st_note.text())

    def test_export_dialog_view_range(self):
        self.connect_and_sweep(start=100_000_000, stop=1_000_000_000)
        dlg = ExportDialog(self.win)
        self.assertTrue(dlg.r_view.isChecked())
        self.assertEqual(dlg.choice(), ([1], 100_000_000, 1_000_000_000))
        dlg.r_all.setChecked(True)
        self.assertEqual(dlg.choice(), ([1], None, None))


class PngTest(UiBase):
    def test_png_save_and_copy(self):
        self.connect_and_sweep()
        self.cmd("marker_add", kind="M", trace=1, freq_hz=1_500_000_000)
        self.pump()
        self.win.display_tab.png_note.setText("フィルタ A")
        self.win.actions["png_save"].trigger()
        files = list((self.root / "png").glob("gigast_*.png"))
        self.assertEqual(len(files), 1)
        self.assertIn(f"保存しました: {files[0].name}", self.win.st_note.text())
        self.assertTrue(self.win.note_button.isVisible())
        meta = png_export.read_png_metadata(files[0].read_bytes())
        self.assertEqual(meta["markers"][0]["name"], "M1")
        self.assertEqual(meta["conditions"]["start_hz"], 50_000_000)
        self.assertEqual(meta["device"]["serial"], "SIM00001")
        # 同じ秒にもう一度でも上書きしない（I §3.4）
        self.win.actions["png_save"].trigger()
        self.assertEqual(len(list((self.root / "png").glob("gigast_*.png"))), 2)
        self.win.actions["png_copy"].trigger()
        self.assertFalse(QGuiApplication.clipboard().image().isNull())
        self.assertIn(texts.LABELS["png_copied"], self.win.st_note.text())

    def test_palette_choice_default_light(self):
        self.assertEqual(self.win.display_tab.png_palette_choice(), "light")
        self.assertEqual(self.win.display_tab.png_size_choice(), (1600, 1000))


class RecordingTest(UiBase):
    def test_record_start_finish_and_status(self):
        self.connect_and_sweep(single=False)
        a = self.win.actions
        self.assertFalse(a["record_stop"].isEnabled())
        a["record_start"].trigger()
        self.wait(lambda: self.win.st_rec.isVisible() and "記録中" in self.win.st_rec.text(), what="記録中")
        self.assertFalse(a["record_start"].isEnabled())
        self.wait(lambda: self.state()["recording"].get("sweeps", 0) >= 2, what="2回記録")
        self.save_path = str(self.root / "rec.h5")
        a["record_finish"].trigger()
        self.wait(lambda: "記録をまとめました: rec.h5" in self.win.st_note.text(), timeout=15, what="まとめた")
        self.assertTrue(Path(self.save_path).exists())
        self.wait(lambda: not self.win.st_rec.isVisible(), what="記録の表示が消える")

    def test_close_during_recording_asks(self):
        self.cmd("record_start")
        self.pump()
        self.confirm_answer = False
        self.win.close()
        self.pump()
        self.assertTrue(self.win.isVisible())
        self.assertIn("作業ファイルは残り", self.confirm_asked[-1])
        self.confirm_answer = True


class UnfinishedTest(UiBase):
    def test_startup_offers_unfinished_and_discard(self):
        # 記録を止めてまとめないまま終わる → 次の起動で聞く（H §4）
        self.connect_and_sweep(single=False)
        self.cmd("record_start")
        self.wait(lambda: self.state()["recording"].get("sweeps", 0) >= 1, what="1回記録")
        self.cmd("record_stop")
        self.cmd("stop")
        self.pump(0.3)
        self.win.close()
        self.api.close()
        self.api = make_api(self.root)
        self.win = MainWindow(self.api, prefs=UiPrefs(), check_unfinished=True)
        self.win.confirm = lambda title, text: (self.confirm_asked.append(text), self.confirm_answer)[1]
        self.win.show()
        self.wait(lambda: self.win.files.unfinished_dialog is not None, what="救出の問い")
        dlg = self.win.files.unfinished_dialog
        self.assertEqual(len(dlg.row_widgets), 1)
        self.assertIn("回の掃引", dlg.row_widgets[0].findChildren(QLabel)[0].text())
        buttons = {b.text(): b for b in dlg.row_widgets[0].findChildren(QPushButton)}
        buttons["捨てる…"].click()
        self.assertIn("失われ", self.confirm_asked[-1])
        self.wait(lambda: self.api.call("list_unfinished").result(5)["recordings"] == [], what="捨てた")


if __name__ == "__main__":
    unittest.main()
