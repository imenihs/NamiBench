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
                       "image_reject": "fast", "ref_level": 0.0 if unit == "dBm" else 10.0, "db_per_div": 10.0,
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
        self.assertIn("REF 0.0 dBm", flat)
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


class FunctionLinesPngTest(unittest.TestCase):
    """マーカーの機能の線（G §3.6）は、画面で出しているときだけ PNG にも描く。"""

    def _png(self, line):
        from PySide6.QtGui import QImage
        data = sample_data()
        data["markers"] = list(data["markers"]) + [
            {"name": "P1 3dB 帯域幅", "kind": "function", "trace": 1, "freq_hz": None, "value": None,
             "ref": None, "delta_freq_hz": None, "delta_value": None, "child": True, "note": "", "line": line}]
        img = QImage()
        img.loadFromData(pe.render_png(data, size=(1280, 720)))
        return img

    def test_lines_drawn_only_when_given(self):
        line = {"level": -40.0, "freqs_hz": [1_480_000_000, 1_530_000_000]}
        with_line, without = self._png(line), self._png(None)
        diff = sum(1 for x in range(0, 1280, 2) for y in range(0, 720, 2)
                   if with_line.pixel(x, y) != without.pixel(x, y))
        self.assertGreater(diff, 200)  # 横線1本と縦線2本ぶん

    def test_png_rows_drop_lines_when_off(self):
        from pygigast.ui_qt import marker_rows
        rows = [{"name": "M1 3dB 帯域幅", "kind": "function", "line": {"level": -3.0, "freqs_hz": [1]}}]
        self.assertIsNotNone(marker_rows.png_rows(rows)[0]["line"])
        self.assertIsNone(marker_rows.png_rows(rows, lines=False)[0]["line"])
        self.assertFalse(marker_rows.function_lines_on({"view": {"function_lines": False}}))
        self.assertTrue(marker_rows.function_lines_on({}))


class TraceLinePngTest(unittest.TestCase):
    """トレースの線（設計F §9）: PNG も画面と同じ線で描き、太い線を上に重ね、凡例の見本もその線。"""

    W, H = 1600, 1000

    def render(self, traces):
        """平らなトレース（線の名前, レベル）を並べた PNG（ダーク）。"""
        start, stop, n = 100_000_000, 200_000_000, 201
        step = (stop - start) // (n - 1)
        data = {"conditions": {"start_hz": start, "stop_hz": stop, "ref_level": 0.0, "db_per_div": 10.0,
                               "unit": "dBm"},
                "traces": [{"number": i + 1, "start_hz": start, "step_hz": step, "values_db": np.full(n, lv),
                            "color": f"trace{i + 1}", "action": "更新", "count": None, "line": ln}
                           for i, (ln, lv) in enumerate(traces)],
                "markers": [], "warnings": []}
        img = QImage.fromData(pe.render_png(data, size=(self.W, self.H), palette="dark"), "PNG")
        self.assertFalse(img.isNull())
        return img

    @staticmethod
    def near(img, x, y, hexcolor, tol=60):
        from PySide6.QtGui import QColor

        c, w = img.pixelColor(x, y), QColor(hexcolor)
        return abs(c.red() - w.red()) + abs(c.green() - w.green()) + abs(c.blue() - w.blue()) <= tol

    def column(self, img, x, hexcolor, tol=60, y0=60, y1=800):
        return [y for y in range(y0, y1) if self.near(img, x, y, hexcolor, tol)]

    def test_widths_follow_the_line(self):
        img = self.render([("thin", -30.0), ("thick", -60.0), ("solid", -45.0)])
        x = self.W // 2
        thick = self.column(img, x, pe.DARK_DEFAULT["trace2"])
        solid = self.column(img, x, pe.DARK_DEFAULT["trace3"])
        self.assertTrue(thick and solid, (thick, solid))
        self.assertGreaterEqual(len(thick), 2 * len(solid) - 1, (thick, solid))
        # 細線は 1 画素より細く、ぼかして描く（色が薄い: はっきりした色の点は無い）
        self.assertTrue(self.column(img, x, pe.DARK_DEFAULT["trace1"], tol=400))
        self.assertEqual(self.column(img, x, pe.DARK_DEFAULT["trace1"], tol=60), [])

    def test_thick_is_drawn_on_top(self):
        # 同じ値の 1（太線）と 2（実線）: 番号順なら 2 が上だが、太い線を上に描く（設計F §9）
        x = self.W // 2
        img = self.render([("thick", -50.0), ("solid", -50.0)])
        self.assertTrue(self.column(img, x, pe.DARK_DEFAULT["trace1"]))
        self.assertEqual(self.column(img, x, pe.DARK_DEFAULT["trace2"], tol=40), [])
        # 同じ段なら番号の大きいほうが上（今までと同じ）
        img = self.render([("solid", -50.0), ("solid", -50.0)])
        self.assertTrue(self.column(img, x, pe.DARK_DEFAULT["trace2"]))
        self.assertEqual(self.column(img, x, pe.DARK_DEFAULT["trace1"], tol=40), [])

    def test_dash_and_dot_have_gaps(self):
        img = self.render([("dash", -30.0), ("dot", -60.0), ("solid", -45.0)])
        for n, line in ((1, "dash"), (2, "dot"), (3, "solid")):
            col = pe.DARK_DEFAULT[f"trace{n}"]
            ys = [y for x in range(790, 830) for y in self.column(img, x, col, tol=200)]
            self.assertTrue(ys, line)
            y = sorted(ys)[len(ys) // 2]
            hit = [any(self.near(img, x, yy, col, tol=200) for yy in (y - 1, y, y + 1)) for x in range(300, 1400)]
            covered = sum(hit) / len(hit)
            if line == "solid":
                self.assertGreater(covered, 0.97, line)
            else:
                self.assertLess(covered, 0.85, line)

    def test_legend_sample_is_the_line(self):
        from pygigast.core import report_layout as rl

        data = {"conditions": {"start_hz": 1, "stop_hz": 2}, "traces": [
            {"number": 1, "action": "更新", "line": "thin"}, {"number": 2, "action": "平均", "line": "thick"},
            {"number": 3, "action": "更新", "line": "dash"},
            {"number": "", "action": "", "label": "a.h5 #1", "line": rl.REFERENCE_LINE}]}
        lay = rl.layout(data, (1600, 1000))
        base = lay["line_base_px"]
        self.assertEqual(base, rl.png_line_base(lay["font_px"]))
        got = {it["line"]: it["stroke"] for it in lay["legend"]}
        for code in ("thin", "thick", "dash", rl.REFERENCE_LINE):
            tr = rl.stroke(code, base)
            # 見本はトレースの線の倍の太さ、切れ目の長さはトレースの線と同じ
            self.assertAlmostEqual(got[code]["width"], 2 * tr["width"])
            self.assertEqual(got[code]["dash"], tr["dash"])
        self.assertIsNone(got["thin"]["dash"])
        self.assertAlmostEqual(got["thick"]["width"], 4 * got["thin"]["width"])
        # 破線は見本の長さに切れ目が入る（見本の線は全角1字 × legend_sample × 0.8。全角1字は文字の大きさとみなす）
        sample = lay["font_px"] * lay["legend_sample"] * 0.8
        self.assertGreater(sample, got["dash"]["dash"][0] + got["dash"]["dash"][1])
        self.assertEqual(lay["line_styles"], rl.LINE_STYLES)


class TraceLineRuleTest(unittest.TestCase):
    """線の描き方は1か所（core/report_layout）: 名前は core の TraceLine と画面の語の表と同じ。重なりの順。"""

    def test_names_and_order(self):
        from pygigast.core import report_layout as rl
        from pygigast.core.trace import TraceLine
        from pygigast.ui_qt import texts

        codes = [x.value for x in TraceLine]
        self.assertEqual(list(rl.TRACE_LINE_STYLES), codes)
        self.assertEqual(list(texts.TRACE_LINES), codes)
        self.assertEqual(list(texts.TRACE_LINES.values()), ["細線", "実線", "太線", "破線", "点線"])
        self.assertEqual(rl.TRACE_LINE_DEFAULT, TraceLine.SOLID.value)
        order = sorted([("thick", 1), ("solid", 4), ("thin", 3), ("dash", 2), ("dot", 1), (rl.REFERENCE_LINE, "")],
                       key=lambda t: rl.draw_order_key(*t))
        self.assertEqual(order, [(rl.REFERENCE_LINE, ""), ("thin", 3), ("dot", 1), ("dash", 2), ("solid", 4),
                                 ("thick", 1)])
        self.assertEqual(rl.stroke("solid", 1.25), {"width": 1.25, "dash": None})
        self.assertEqual(rl.stroke("dash", 1.0)["dash"], [8.0, 4.0])
        self.assertEqual(rl.stroke("nonsense", 1.0), rl.stroke("solid", 1.0))


class AxesRuleTest(unittest.TestCase):
    """PNG の目盛と単位の置き場所は1か所（core/report_layout.graph_axes）。Qt の png_export はその値で描く（Web の png.js
    も同じ値で描く: tests/test_web_png.py）。目盛は縦も横も 10 区画の線の上、縦軸の単位は縦軸の数字の上（凡例の段の左端）、
    横軸の単位は数字の下の見出し。"""

    def test_layout_axes(self):
        from pygigast.core import report_layout as rl

        lay = rl.layout(sample_data(), (1600, 1000))
        ax = lay["axes"]
        self.assertEqual(ax["unit_left"], lay["margin_px"])
        self.assertEqual(ax["legend_left"], ax["plot_left"])
        self.assertLess(ax["y_label_right"], ax["plot_left"])
        # 縦軸の数字と単位が枠の左に入る（見積もりは広め: 一番広い書体の Meiryo UI の幅でも入る）
        widest = max(len(s) for s in lay["y_labels"] + [lay["unit"]])
        self.assertGreaterEqual(ax["y_label_right"] - ax["unit_left"], widest * lay["font_px"] * 0.62 - 1)
        self.assertEqual(ax["x_every"], 1)
        self.assertEqual(len(lay["x_labels"]), rl.DIVISIONS + 1)
        # 狭い画像では間引く（重ならない最小の間隔）
        small = rl.layout(sample_data(), (320, 240))["axes"]
        self.assertIn(small["x_every"], (2, 5, 10))
        # 同じ大きさでも、1Hz 単位の長い数字は間引く（短い数字は間引かない）
        self.assertEqual(rl.layout(sample_data(), (640, 400))["axes"]["x_every"], 1)
        d = sample_data()
        d["conditions"].update(start_hz=1_420_405_751, stop_hz=1_420_405_761)
        long_lay = rl.layout(d, (640, 400))
        self.assertGreater(long_lay["axes"]["x_every"], 1, long_lay["x_labels"])

    def test_zero_span_axis_is_time(self):
        from pygigast.core import report_layout as rl

        d = sample_data()
        d["conditions"].update(step_hz=0, stop_hz=d["conditions"]["start_hz"], points=501,
                               x_axis={"kind": "time", "x0": -10_000_000, "x1": 0, "factor": 1_000_000, "unit": "s",
                                       "length_s": 10.0, "point_time_s": 0.00014})
        lay = rl.layout(d, (1600, 1000))
        self.assertEqual(lay["x_labels"][0], "-10")
        self.assertEqual(lay["x_labels"][-1], "0")
        self.assertEqual(lay["x_title"], rl.X_AXIS_TITLE_TIME.replace("{unit}", "s"))
        png = pe.render_png(d, size=(800, 500))
        self.assertFalse(QImage.fromData(png, "PNG").isNull())

    def test_qt_draws_from_layout(self):
        from unittest import mock

        from PySide6.QtGui import QPainter

        from pygigast.core import report_layout as rl

        for size in ((1600, 1000), (640, 400), (320, 240)):
            with self.subTest(size=size):
                rec = []
                orig = QPainter.drawText

                def spy(painter, *a):
                    if len(a) == 2 and hasattr(a[0], "x"):
                        rec.append((a[0].x(), a[0].y(), a[1]))
                    return orig(painter, *a)

                with mock.patch.object(QPainter, "drawText", spy):
                    pe.render_png(sample_data(), size=size)
                lay = rl.layout(sample_data(), size)
                ax = lay["axes"]
                # 横軸の数字: x_every 目盛ごと・同じ段（グラフの下の1段目）。見出しはその1行下。マーカー表の見出しにも
                # 同じ「周波数 (MHz)」があるので、1行上に横軸の最初の数字がある方を軸の見出しとする
                gap = ax["x_title_top"] - ax["x_label_top"]
                title = [r for r in rec if r[2] == lay["x_title"]
                         and any(abs(q[1] - (r[1] - gap)) < 0.01 and q[2] == lay["x_labels"][0] for q in rec)]
                self.assertEqual(len(title), 1)
                row_y = title[0][1] - gap
                drawn = [r[2] for r in rec if abs(r[1] - row_y) < 0.01]
                self.assertEqual(drawn, lay["x_labels"][::ax["x_every"]])
                # 縦軸の単位は凡例の段の左端
                unit = [r for r in rec if r[2] == lay["unit"]]
                self.assertEqual([r[0] for r in unit], [ax["unit_left"]])
                self.assertLess(unit[0][1], ax["plot_top"])
