"""画像出力（設計I）: 画面を撮らずに描けること、tEXt の JSON、上書きしない名前を確かめる。

見本の画像を ref/capture/<日付>/png_samples/ に書く（git の管理外）。見た目は書いた画像を開いて確かめる（C-01）。
"""
import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# offscreen は Windows では文字の書体を持たないので、見本の画像の文字が豆腐になる。OS の書体のフォルダを渡す
if os.name == "nt" and "QT_QPA_FONTDIR" not in os.environ:
    os.environ["QT_QPA_FONTDIR"] = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")

import numpy as np  # noqa: E402
from PySide6.QtGui import QImage  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from pygigast.ui_qt import png_export as pe  # noqa: E402

app = QApplication.instance() or QApplication([])

SAMPLE_DIR = Path(__file__).resolve().parent.parent / "ref" / "capture" / "2026-10-01" / "png_samples"


def sample_data(unit="dBm", many_markers=False):
    """スペアナの1画面ぶん: トレース3本（1本は印の付いた点を含む）・マーカー・子の行・注意。"""
    start, stop, n = 1_450_000_000, 1_550_000_000, 1001
    step = (stop - start) // (n - 1)
    f = start + step * np.arange(n)
    rng = np.random.default_rng(1)
    floor = -85 + rng.normal(0, 1.5, n)
    peak = -12 - 20 * np.log10(1 + ((f - 1_500_000_000) / 2e6) ** 2) * 2
    t1 = np.maximum(floor, peak)
    t2 = t1 + 3 + rng.normal(0, 0.5, n)
    t3 = t1 - 6
    flags = np.zeros(n, dtype=bool)
    flags[:40] = True
    if unit == "dB":
        t1, t2, t3 = t1 + 12, t2 + 12, t3 + 12
    markers = [
        {"name": "P1", "kind": "marker", "trace": 1, "freq_hz": 1_500_000_000, "value": float(t1[500]),
         "ref": None, "delta_freq_hz": None, "delta_value": None},
        {"name": "P1 −3dB", "kind": "level_cursor", "trace": 1, "freq_hz": None, "value": float(t1[500]) - 3,
         "ref": "P1", "delta_freq_hz": None, "delta_value": -3.0, "child": True},
        {"name": "M1", "kind": "marker", "trace": 2, "freq_hz": 1_520_000_000, "value": float(t2[700]),
         "ref": "P1", "delta_freq_hz": 20_000_000, "delta_value": float(t2[700] - t1[500])},
        {"name": "M1 −10MHz", "kind": "freq_cursor", "trace": 2, "freq_hz": 1_510_000_000, "value": float(t2[600]),
         "ref": "M1", "delta_freq_hz": -10_000_000, "delta_value": float(t2[600] - t2[700]), "child": True},
        {"name": "M2", "kind": "marker", "trace": 1, "freq_hz": 1_600_000_000, "value": None,
         "ref": None, "note": "範囲外"},
    ]
    if many_markers:
        for i in range(3, 20):
            fi = 1_455_000_000 + i * 4_000_000
            k = int((fi - start) // step)
            markers.append({"name": f"M{i}", "kind": "marker", "trace": 1, "freq_hz": fi,
                            "value": float(t1[k]), "ref": None})
    return {
        "measured_at": "2026-10-01T09:15:30+09:00",
        "device": {"model": "GigaSt v5", "serial": "TEST0001"},
        "app_version": "0.1.0",
        "mode": "スペアナ",
        "conditions": {"start_hz": start, "stop_hz": stop, "rbw_hz": 180_000, "points": n, "step_hz": step,
                       "image_cancel": True, "ref_level": 0.0 if unit == "dBm" else 10.0, "db_per_div": 10.0,
                       "unit": unit},
        "calibration": {"source": "旧校正ファイル gigast.txt", "at": "2026-09-30 14:00 +09:00"},
        "normalization": {"name": "直結 1.4–1.6GHz", "at": "2026-10-01 09:00 +09:00", "applied": True,
                          "before_power_cycle": True},
        "traces": [
            {"number": 1, "start_hz": start, "step_hz": step, "values_db": t1, "color": "trace1",
             "action": "Clear/Write", "average": None, "count": None, "flags": flags},
            {"number": 2, "start_hz": start, "step_hz": step, "values_db": t2.tolist(), "color": "trace2",
             "action": "Max Hold", "average": None, "count": 25},
            {"number": 3, "start_hz": start, "step_hz": step, "values_db": t3, "color": "trace3",
             "action": "平均", "average": "電力平均", "count": 12, "count_target": 30},
            {"number": 4, "start_hz": start, "step_hz": step, "values_db": t3, "color": "trace4",
             "action": "Clear/Write", "visible": False},
        ],
        "markers": markers,
        "warnings": ["RBW 15kHz の1回の掃引（約2dB 高く出うる）", "ノーマライズの基準が電源の入れ直し前"],
    }


class RenderTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        SAMPLE_DIR.mkdir(parents=True, exist_ok=True)

    def check(self, png, size):
        img = QImage.fromData(png, "PNG")
        self.assertFalse(img.isNull())
        self.assertEqual((img.width(), img.height()), size)

    def test_both_palettes_and_sizes(self):
        for palette in ("light", "dark"):
            for size in ((1600, 1000), (1280, 720)):
                with self.subTest(palette=palette, size=size):
                    png = pe.render_png(sample_data(), size=size, palette=palette,
                                        note="LNA 入力に 30dB ATT。アンテナ端で測定")
                    self.check(png, size)
                    (SAMPLE_DIR / f"sa_{palette}_{size[0]}x{size[1]}.png").write_bytes(png)

    def test_ratio_trace_and_many_markers(self):
        for size in ((1600, 1000), (1280, 720)):
            png = pe.render_png(sample_data(unit="dB", many_markers=True), size=size)
            self.check(png, size)
            (SAMPLE_DIR / f"tg_many_{size[0]}x{size[1]}.png").write_bytes(png)

    def test_table_overflow_is_counted(self):
        # 表に収まらない行は黙って切らず「ほか N 行」と書く。tEXt には全行が残る
        data = sample_data()
        data["markers"] = [{"name": f"M{i}", "trace": 1, "freq_hz": 1_460_000_000 + i * 1_000_000,
                            "value": -80.0} for i in range(60)]
        png = pe.render_png(data, size=(1280, 720))
        self.check(png, (1280, 720))
        self.assertEqual(len(pe.read_png_metadata(png)["markers"]), 60)
        (SAMPLE_DIR / "overflow_1280x720.png").write_bytes(png)

    def test_user_palette_fills_missing(self):
        png = pe.render_png(sample_data(), size=(1280, 720), palette={"graph_bg": "#fffff0"})
        self.check(png, (1280, 720))

    def test_minimal_data(self):
        # 条件だけ（トレース・マーカー・補正なし）でも描ける。空の段は出さない
        png = pe.render_png({"conditions": {"start_hz": 0, "stop_hz": 12_000_000_000}})
        self.check(png, (1600, 1000))

    def test_text_chunk_round_trip(self):
        data = sample_data()
        png = pe.render_png(data, note="メモ")
        kinds = [k for k, _ in pe.iter_png_chunks(png)]  # CRC も確かめる
        self.assertEqual(kinds[0], b"IHDR")
        self.assertEqual(kinds[-1], b"IEND")
        self.assertIn(b"tEXt", kinds)
        meta = pe.read_png_metadata(png)
        self.assertEqual(meta["format"], 1)
        self.assertEqual(meta["measured_at"], "2026-10-01 09:15:30 +09:00")
        self.assertEqual(meta["conditions"], data["conditions"])
        self.assertEqual(meta["device"]["serial"], "TEST0001")
        self.assertEqual(meta["note"], "メモ")
        self.assertEqual(meta["markers"][2]["name"], "M1")
        self.assertEqual([t["number"] for t in meta["traces"]], [1, 2, 3])  # 見えているものだけ
        # Qt 自身が読んでも壊れていない
        self.assertFalse(QImage.fromData(png, "PNG").isNull())

    def test_band_rows(self):
        rows = pe.band_rows(sample_data(), note="")
        flat = [it for _, items in rows for it in items]
        self.assertIn("2026-10-01 09:15:30 +09:00", flat)
        self.assertIn("GigaSt v5 S/N TEST0001", flat)
        self.assertIn("RBW 180 kHz", flat)
        self.assertIn("Span 100 MHz", flat)
        self.assertIn("REF 0 dBm", flat)
        self.assertFalse(any(s.startswith("メモ") for s in flat))  # 空のメモは出さない
        rows2 = pe.band_rows({"conditions": {}}, note="")
        flat2 = [it for _, items in rows2 for it in items]
        self.assertIn("校正データ: なし", flat2)

    def test_measured_at_naive_gets_local_offset(self):
        s = pe.format_measured_at(datetime(2026, 10, 1, 9, 15, 30))
        self.assertRegex(s, r"^2026-10-01 09:15:30 [+-]\d\d:\d\d$")


class SaveTest(unittest.TestCase):
    def test_never_overwrite(self):
        with tempfile.TemporaryDirectory() as d:
            when = datetime(2026, 10, 1, 9, 15, 30)
            a = pe.save_png(d, b"A", when=when)
            b = pe.save_png(d, b"B", when=when)
            c = pe.save_png(d, b"C", when=when)
            self.assertEqual(a.name, "gigast_20261001_091530.png")
            self.assertEqual(b.name, "gigast_20261001_091530_1.png")
            self.assertEqual(c.name, "gigast_20261001_091530_2.png")
            self.assertEqual((a.read_bytes(), b.read_bytes(), c.read_bytes()), (b"A", b"B", b"C"))
            # ファイル名を指定したときも同じ規則
            e = pe.save_png(Path(d) / "gigast_20261001_091530.png", b"E")
            self.assertEqual(e.name, "gigast_20261001_091530_3.png")

    def test_clipboard(self):
        png = pe.render_png(sample_data(), size=(640, 400))
        pe.copy_png_to_clipboard(png)
        mime = app.clipboard().mimeData()
        self.assertTrue(mime.hasImage())


if __name__ == "__main__":
    unittest.main()
