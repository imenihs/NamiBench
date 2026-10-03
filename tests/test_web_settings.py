"""Web の画面の設定・プリセット・表示の設定・キーの割り当て・別ウィンドウ（設計O・J §4.4・§4.6・§4.3.2）。

- 表（/api/v1/texts）に設定の画面の語・キーの初期値と割り当てられないキーが届く（Qt と同じ表: J §5）
- 周波数オフセットで、マーカー表・マウスの位置の読み値・調整の知らせの周波数が換算後になる（Web の口が足す文）
- プリセットの一覧に行の文（Qt の窓と同じ screen_model.preset_rows）が付く
- 画面のファイルに設定の画面・プリセット・確かめる窓・ピークの一覧の別ウィンドウ・描き方の入口がある
- キーの割り当ての決まり（app_keys.js）が Qt の keymap.py と同じ（Node があるときだけ）

本物の uvicorn とシミュレータの機器で通す（tests/test_web_server.py と同じやり方）。
"""
import json
import re
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from pygigast.api.local import LocalApi, _SimulatorBackend
from pygigast.api.web import server as web
from pygigast.core.devices.base import load_profile
from pygigast.core.instrument import GIGAST_PROFILE_PATH, Instrument
from pygigast.core.units import format_freq
from pygigast.ui_qt import texts as qt_texts
from tests import test_web_server as tws

UI_WEB = tws.UI_WEB

OFF = 10_000_000_000  # 周波数オフセット 10GHz（変換器の観測の例）


class _Base(unittest.TestCase):
    post = tws.WebServerTest.post
    get = tws.WebServerTest.get
    tearDown = tws.WebServerTest.tearDown

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        profile = load_profile(GIGAST_PROFILE_PATH)
        sim = _SimulatorBackend(profile)
        sim.speed = 0.05
        self.api = LocalApi(Instrument({"simulator": sim}, profile=profile,
                                       data_root=root / "data", cal_root=root / "cal"))
        self.srv = tws._Server(self.api)

    def state(self):
        return json.loads(self.get("/api/v1/state")[1])

    def connect_and_sweep(self, start_hz, stop_hz):
        self.assertEqual(self.post("connect", {"backend": "simulator", "serial": "SIM00001"})[0], 200)
        self.assertEqual(self.post("set_sweep", {"start_hz": start_hz, "stop_hz": stop_hz})[0], 200)
        n0 = self.state()["run"]["sweep_count"]
        self.assertEqual(self.post("run", {"mode": "single"})[0], 200)
        end = time.monotonic() + 20
        while True:
            st = self.state()
            if st["run"]["state"] == "stopped" and st["run"]["sweep_count"] > n0:
                return st
            self.assertLess(time.monotonic(), end, "掃引が終わらない")
            time.sleep(0.05)


class TextsTest(_Base):
    def test_settings_tables_are_served(self):
        t = json.loads(self.get("/api/v1/texts")[1])
        for name in ("SETTINGS_PAGES", "SETTINGS_TEXTS", "PALETTE_NAMES", "PALETTE_BASES", "VIEW_TEXTS", "PRESET_TEXTS",
                     "LAYOUT_TEXTS", "WEB_KEY_DEFAULTS", "GRAPH_ONLY_KEYS", "SHIFT_STEP_KEYS", "KEY_RESERVED_REASONS",
                     "WEB_RESERVED_KEYS", "PNG_PALETTES", "WEB_SETTINGS_TEXTS"):
            self.assertIn(name, t, name)
        self.assertEqual(t["SETTINGS_PAGES"], qt_texts.SETTINGS_PAGES)
        self.assertEqual(t["LABELS"]["part_peaks"], qt_texts.LAYOUT_TEXTS["part_peaks"])
        for code in ("presets", "preset_save", "layout_reset"):
            self.assertIn(code, t["MENU_ITEMS"])
        # 配色の編集・キーの割り当ては作ったので「まだ使えません」にしない
        self.assertNotIn("view_colors", t["MENU_NOT_YET"])
        self.assertNotIn("settings_keys", t["MENU_NOT_YET"])
        # Web の初期値は、ブラウザが先に取るキーを使わない（J §4.6）。理由のコードは全部文がある
        for code, key in t["WEB_KEY_DEFAULTS"].items():
            self.assertNotIn(key, t["WEB_RESERVED_KEYS"], code)
        for key, why in t["WEB_RESERVED_KEYS"].items():
            self.assertIn(why, t["KEY_RESERVED_REASONS"], key)
        # 画面から消した「まだ使えません」の語は表からも消した
        self.assertNotIn("unit_not_yet", t["LABELS"])
        self.assertNotIn("amplitude_not_yet", t["LABELS"])


class OffsetTest(_Base):
    def test_markers_and_mouse_reading_are_shifted(self):
        self.connect_and_sweep(50_000_000, 150_000_000)
        st, r = self.post("marker_add", {"kind": "M", "trace": 1, "freq_hz": 100_000_000})
        self.assertEqual(st, 200, r)
        st0 = self.state()
        row0 = next(x for x in st0["markers"]["rows"] if x["id"] == r["id"])
        f_dev = next(m for m in st0["markers"]["markers"] if m["id"] == r["id"])["freq_hz"]
        self.assertAlmostEqual(float(row0["cells"][2]), f_dev / 1e6, places=3)
        self.assertEqual(self.post("set_view", {"freq_offset_hz": OFF})[0], 200)
        st1 = self.state()
        # 状態の周波数は機器の周波数のまま、表の文字は換算後（設計O）
        self.assertEqual(next(m for m in st1["markers"]["markers"] if m["id"] == r["id"])["freq_hz"], f_dev)
        row1 = next(x for x in st1["markers"]["rows"] if x["id"] == r["id"])
        self.assertAlmostEqual(float(row1["cells"][2]), (f_dev + OFF) / 1e6, places=3)
        self.assertEqual(st1["ui"]["freq_offset_text"], qt_texts.offset_text(OFF))
        self.assertTrue(any("+10 GHz" in line for line in st1["ui"]["cond"]))
        self.assertIn("offset", [i["key"] for i in st1["ui"]["status"]])
        # マウスの位置の読み値（Qt のツールチップと同じ文）も換算後の周波数
        level = next(m for m in st1["markers"]["markers"] if m["id"] == r["id"])["level"]
        st, mr = self.post("mouse_reading", {"x": f_dev, "y": level})
        self.assertEqual(st, 200, mr)
        self.assertEqual(mr["freq_hz"], f_dev)
        self.assertEqual(mr["lines"][0], format_freq(f_dev + OFF))
        # 0 で使わない（換算の文が消える）
        self.assertEqual(self.post("set_view", {"freq_offset_hz": 0})[0], 200)
        self.assertEqual(self.state()["ui"]["freq_offset_text"], "")

    def test_adjustment_notice_uses_shown_frequency(self):
        self.connect_and_sweep(50_000_000, 150_000_000)
        self.assertEqual(self.post("set_view", {"freq_offset_hz": 1_000_000_000})[0], 200)
        # 下限（−100MHz）より下の Start は下限に合わせられる。知らせの周波数は欄と同じ換算後（900MHz）
        st, r = self.post("set_sweep", {"start_hz": -500_000_000})
        self.assertEqual(st, 200, r)
        adj = [a for a in r["texts"]["adjustments"] if a["field"] == "start_hz"]
        self.assertTrue(adj, r)
        self.assertIn(format_freq(-100_000_000 + 1_000_000_000), adj[0]["text"])

    def test_unit_and_draw_style(self):
        self.connect_and_sweep(50_000_000, 150_000_000)
        st, r = self.post("set_view", {"level_unit": "dBuV", "ext_db": 10, "draw_style": "fill"})
        self.assertEqual(st, 200, r)
        s = self.state()
        self.assertEqual(s["view"]["level_unit_label"], "dBμV")
        self.assertEqual(s["view"]["draw_style"], "fill")
        self.assertIn("ext", [i["key"] for i in s["ui"]["status"]])
        # null は初期値に戻す（「単位と外部補正を初期値に戻す」）
        self.assertEqual(self.post("set_view", {"level_unit": None, "ext_db": None})[0], 200)
        s = self.state()
        self.assertEqual((s["view"]["level_unit"], s["view"]["ext_db"]), ("dBm", 0.0))


class PresetPaletteTest(_Base):
    def test_preset_list_has_rows(self):
        self.connect_and_sweep(400_000_000, 500_000_000)
        st, r = self.post("preset_save", {"name": "FM"})
        self.assertEqual(st, 200, r)
        st, r = self.post("preset_list", {})
        self.assertEqual(st, 200, r)
        self.assertEqual([x["name"] for x in r["rows"]], ["FM"])
        self.assertIn("400 MHz", r["rows"][0]["summary"])
        self.assertEqual(r["rows"][0]["id"], r["presets"][0]["id"])

    def test_palette_view_counts_changed(self):
        st, r = self.post("palette_set", {"colors": {"trace2": "#ffd24a"}})
        self.assertEqual(st, 200, r)
        pv = self.state()["ui"]["palette"]
        self.assertEqual(pv["changed_n"], 1)
        row = next(x for x in pv["rows"] if x["name"] == "trace2")
        self.assertTrue(row["changed"])
        # トレース 1 と 2 が同じ色。知らせは変えた色（トレース 2）の行に出す（U-25）
        self.assertTrue(row["warn"])
        self.assertFalse(next(x for x in pv["rows"] if x["name"] == "trace1")["warn"])
        st, r = self.post("palette_set", {"colors": {"trace2": "red"}})
        self.assertEqual((st, r["error"]["code"]), (400, "bad_color"))
        self.assertEqual(self.post("palette_reset", {"base": "light"})[1]["lost"], 1)
        self.assertEqual(self.state()["palette"]["base"], "light")


    def test_saved_palettes_rows_and_import_text(self):
        """保存した配色（J §4.4.1）: 一覧に行の文（Qt と同じ screen_model.palette_rows）、取り込みに知らせの文（読みにくい
        組はその場で）。書き出し・取り込みは中身の文字でやりとりし、パスを受け取らない（A §4.3.2）。"""
        st, a = self.post("palette_save", {"name": "夜の観測"})
        self.assertEqual(st, 200, a)
        self.assertEqual(self.state()["ui"]["palette"]["origin_id"], a["id"])
        self.post("palette_set", {"colors": {"trace2": "#ffd24a"}})  # トレース 1 と同じ色（見分けにくい）
        self.assertEqual(self.state()["ui"]["palette"]["unsaved_n"], 1)
        st, out = self.post("palette_export", {"id": a["id"]})
        self.assertEqual((st, out["file_name"]), (200, "夜の観測.json"))
        d = json.loads(out["text"])
        d["content"]["colors"]["trace2"] = "#ffd24a"
        st, r = self.post("palette_import", {"text": json.dumps(d), "file_name": "a.json"})
        self.assertEqual(st, 200, r)
        self.assertEqual(r["name"], "夜の観測 (2)")
        self.assertTrue(r["texts"]["done"].startswith("配色「夜の観測 (2)」を取り込みました（⚠ 読みにくい組 1"))
        st, r = self.post("palette_list", {})
        self.assertEqual([x["name"] for x in r["rows"]], ["夜の観測", "夜の観測 (2)"])
        self.assertEqual([bool(x["warn"]) for x in r["rows"]], [False, True])
        self.assertIn("トレース 1", r["rows"][1]["warn_tip"])
        self.assertEqual([x["origin"] for x in r["rows"]], [True, False])
        st, r = self.post("palette_import", {"text": "{"})
        self.assertEqual((st, r["error"]["code"]), (400, "palette_file_unreadable"))
        self.assertEqual(r["error"]["text"], qt_texts.ERRORS["palette_file_unreadable"])
        # パスは受け取らない（Web の口の決まり）
        st, r = self.post("palette_import", {"text": out["text"], "path": "C:/x.json"})
        self.assertEqual((st, r["error"]["code"]), (400, "path_not_allowed"))


class StaticFilesTest(_Base):
    def test_new_ui_hooks(self):
        html = self.get("/")[1].decode("utf-8")
        for f in ("app_keys.js", "app_settings.js", "app_windows.js", "app_markers.js"):
            self.assertIn(f'src="{f}"', html)
            self.assertEqual(self.get("/" + f)[0], 200, f)
        # 窓（設定・プリセット・確かめる）・ピークの一覧の別ウィンドウ・描き方・振幅の詳細・周波数オフセット。
        # ピークの一覧の周波数（周波数オフセット込み）はサーバーが Qt と同じ peak_view.result_view で作る
        for hook in ("<gs-settings", "<gs-presets", "<gs-confirm", "detach('peaks')", "part === 'peaks'",
                     "draw_style", "commitOffset", "commitExt", "level_unit", "resetPalette('light')",
                     "openSettings('display')", "peakText().rows"):
            self.assertIn(hook, html, hook)
        # 古い「まだ使えません」の欄と、入力の窓に入れていた設定は消した
        self.assertNotIn("unit_not_yet", html)
        self.assertNotIn("modal.settings", html)
        windows = (UI_WEB / "app_windows.js").read_text(encoding="utf-8")
        for page in qt_texts.SETTINGS_PAGES:
            self.assertIn(f"page === '{page}'", windows, page)
        # キーは表（WEB_KEY_DEFAULTS）と利用者の割り当てから作る。メニューの処理にキーを直に書かない
        menu = (UI_WEB / "app_menu.js").read_text(encoding="utf-8")
        self.assertIsNone(re.search(r"\"Ctrl\+[A-Z]\"", menu))
        self.assertIn("keySeqs", menu)
        for code in ("presets", "preset_save", "layout_reset", "view_colors", "settings_keys"):
            self.assertRegex(menu, rf"\b{code}: \(\) =>", code)
        graph = (UI_WEB / "graph.js").read_text(encoding="utf-8")
        for hook in ("setColors(", "setXOffset(", "t.fill"):
            self.assertIn(hook, graph, hook)


@unittest.skipUnless(shutil.which("node"), "Node が無い")
class KeyRulesTest(unittest.TestCase):
    """app_keys.js の決まり（ぶつかる・割り当てられない・Shift 付き・付け替え）が Qt の keymap.py と同じ。"""

    SCRIPT = r"""
globalThis.window = globalThis; const store = {};
globalThis.localStorage = { getItem: (k) => store[k] || null, setItem: (k, v) => { store[k] = v; } };
const fs = require("fs");
require(process.argv[1]);
GSTexts.load(JSON.parse(fs.readFileSync(process.argv[2], "utf-8")));
require(process.argv[3]);
const K = GSKeys;
const km = K.effective({});
const ev = (o) => ({ key: "", code: "", ctrlKey: false, altKey: false, shiftKey: false, metaKey: false, ...o });
console.log(JSON.stringify({
  commands: K.commands(),
  km,
  shift: K.sequences("marker_left", "ArrowLeft"),
  conflictP: K.conflict(km, "marker_add", "P"),
  conflictShift: K.conflict(km, "marker_add", "Shift+ArrowLeft"),
  reserved: K.reservedReason("Ctrl+W"),
  free: K.reservedReason("G"),
  take: K.assign({}, "marker_add", "P", "marker_peak"),
  back: K.assign({ run_cont: "G" }, "run_cont", "R"),
  changed: K.changedCount({ run_cont: "G", stop: "X" }),
  keyOf: [K.keyOf(ev({ key: "w", ctrlKey: true, shiftKey: true })), K.keyOf(ev({ key: "P", shiftKey: true })),
          K.keyOf(ev({ key: "ArrowLeft", shiftKey: true })), K.keyOf(ev({ key: "!", code: "Digit1", altKey: true, shiftKey: true }))],
  name: [K.commandName("mode_sa"), K.commandName("run_cont")],
  label: [K.keyLabel("Shift+ArrowLeft"), K.keyLabel("")],
}));
"""

    def test_same_rules_as_qt(self):
        from pygigast.ui_qt import keymap

        with tempfile.TemporaryDirectory() as d:
            tj = Path(d) / "texts.json"
            tj.write_text(json.dumps(web.texts_json(), ensure_ascii=False), encoding="utf-8")
            out = subprocess.run(["node", "-e", self.SCRIPT, str(UI_WEB / "texts.js"), str(tj), str(UI_WEB / "app_keys.js")],
                                 capture_output=True, text=True, timeout=30, check=True, encoding="utf-8").stdout
        got = json.loads(out)
        T = qt_texts
        # 命令の並びは Qt と同じ（Web に無い「終了」だけ除く）
        self.assertEqual(got["commands"], [c for c in keymap.commands() if c != "quit"])
        self.assertEqual(got["km"], {c: T.WEB_KEY_DEFAULTS.get(c, "") for c in got["commands"]})
        self.assertEqual(got["shift"], ["ArrowLeft", "Shift+ArrowLeft"])
        self.assertEqual(got["conflictP"], "marker_peak")
        self.assertEqual(got["conflictShift"], "marker_left")
        self.assertEqual(got["reserved"], T.KEY_RESERVED_REASONS["browser_close"])
        self.assertIsNone(got["free"])
        self.assertEqual(got["take"], keymap.assign({}, "marker_add", "P", T.WEB_KEY_DEFAULTS, take_from="marker_peak"))
        self.assertEqual(got["back"], {})
        self.assertEqual(got["changed"], 1)
        # Ctrl・Alt と一緒の Shift はキーの名前に入る（Ctrl+Shift+W は割り当てられないキーとして見つかる）
        self.assertEqual(got["keyOf"], ["Ctrl+Shift+W", "P", "Shift+ArrowLeft", "Alt+Shift+1"])
        self.assertEqual(got["name"], [keymap.command_name("mode_sa"), T.MENU_ITEMS["run_cont"]])
        self.assertEqual(got["label"], ["Shift+←", T.SETTINGS_TEXTS["keys_none"]])


@unittest.skipUnless(shutil.which("node"), "Node が無い")
class LevelTextTest(unittest.TestCase):
    """縦軸の目盛と REF の欄の数字の形（units.js の levelText）が Python の units.level_text と同じ。"""

    VALUES = [116.99, 106.99, 96.99000000000001, 0.0, -0.01, -0.04, -37.0, -120.0, 3.05, -106.98999999999999]

    def test_same_as_python(self):
        from pygigast.core.units import level_text

        script = "const U = require(process.argv[1]); console.log(JSON.stringify(JSON.parse(process.argv[2]).map(U.levelText)));"
        out = subprocess.run(["node", "-e", script, str(UI_WEB / "units.js"), json.dumps(self.VALUES)],
                             capture_output=True, text=True, timeout=30, check=True, encoding="utf-8").stdout
        got = json.loads(out)
        self.assertEqual(got, [level_text(v) for v in self.VALUES])
        self.assertEqual(got[:4], ["117.0", "107.0", "97.0", "0.0"])
        self.assertEqual(got[4], "0.0")  # −0.0 と書かない
        self.assertEqual(got[6], "−37.0")
        # 縦軸の目盛と REF の欄がこの形を使う
        self.assertIn("GSUnits.levelText(v)", (UI_WEB / "graph.js").read_text(encoding="utf-8"))
        self.assertIn("U.levelText(this.amp.ref)", (UI_WEB / "app.js").read_text(encoding="utf-8"))


@unittest.skipUnless(shutil.which("node"), "Node が無い")
class PngPaletteTest(unittest.TestCase):
    """Web の PNG の配色（app_settings.js の pngColors）: ユーザー指定（既定）・ライト・ダークが Qt と同じ（I §3.6）。

    Web の PNG はブラウザの canvas が pngColors() の色だけで描く（png.js は ctx.colors 以外の色を持たない）。
    """

    SCRIPT = r"""
globalThis.window = globalThis; const store = {};
globalThis.localStorage = { getItem: (k) => (k in store ? store[k] : null), setItem: (k, v) => { store[k] = String(v); },
                            removeItem: (k) => { delete store[k]; } };
const fs = require("fs");
require(process.argv[1]);
GSTexts.load(JSON.parse(fs.readFileSync(process.argv[2], "utf-8")));
require(process.argv[3]);
const A = GSAppSettings;
const P = JSON.parse(process.argv[4]);
function vm(palette) {
  const o = { ...A.data.call({}), colors: { light: P.light, dark: P.dark }, palette: P.user };
  if (palette !== undefined) o.pngPrefs = { ...o.pngPrefs, palette };
  Object.defineProperty(o, "pngPalette", { get() { return A.computed.pngPalette.call(o); } });
  for (const [k, f] of Object.entries(A.methods)) o[k] = f;
  return o;
}
const out = { colors: {} };
for (const c of [undefined, "screen", "light", "dark", "sepia"]) {
  const o = vm(c);
  out.colors[String(c)] = { code: o.pngPalette, colors: o.pngColors() };
}
// 覚えるのは選んだ項目だけ（大きさを変えても配色を覚えない = 配色は初期値のユーザー指定に従う）
const o = vm();
o.setPng("size", "1920x1080");
out.afterSize = JSON.parse(store["pygigast.png"]);
out.freshDefault = vm().pngPalette;
o.setPng("palette", "dark");
out.afterDark = JSON.parse(store["pygigast.png"]);
out.freshDark = vm().pngPalette;
o.resetPng("palette");
out.afterReset = JSON.parse(store["pygigast.png"]);
console.log(JSON.stringify(out));
"""

    def test_three_choices_like_qt(self):
        from pygigast.core import report_layout as rl

        user = {**rl.DARK_DEFAULT, "graph_bg": "#203040", "panel_bg": "#302010", "trace1": "#ff00ff"}
        with tempfile.TemporaryDirectory() as d:
            tj = Path(d) / "texts.json"
            tj.write_text(json.dumps(web.texts_json(), ensure_ascii=False), encoding="utf-8")
            arg = json.dumps({"user": user, "light": rl.LIGHT_DEFAULT, "dark": rl.DARK_DEFAULT})
            out = subprocess.run(["node", "-e", self.SCRIPT, str(UI_WEB / "texts.js"), str(tj),
                                  str(UI_WEB / "app_settings.js"), arg],
                                 capture_output=True, text=True, timeout=30, check=True, encoding="utf-8").stdout
        got = json.loads(out)
        expect = {"undefined": ("screen", user), "screen": ("screen", user), "light": ("light", rl.LIGHT_DEFAULT),
                  "dark": ("dark", rl.DARK_DEFAULT), "sepia": ("screen", user)}
        for k, (code, colors) in expect.items():
            self.assertEqual(got["colors"][k]["code"], code, k)
            self.assertEqual(got["colors"][k]["colors"], colors, k)
            # Qt と同じ規則（core/report_layout.png_palette）
            self.assertEqual(colors, rl.png_palette(None if k == "undefined" else k, user), k)
        self.assertEqual(rl.PNG_PALETTE_DEFAULT, "screen")
        self.assertEqual(got["afterSize"], {"size": "1920x1080"})
        self.assertEqual(got["freshDefault"], "screen")
        self.assertEqual(got["afterDark"], {"size": "1920x1080", "palette": "dark"})
        self.assertEqual(got["freshDark"], "dark")
        self.assertEqual(got["afterReset"]["palette"], "screen")

    def test_choices_table_and_wiring(self):
        from pygigast.core import report_layout as rl

        # 選ぶ欄の並びと語は表から（Qt と Web が同じ表: J §5）
        table = web.texts_json()["PNG_PALETTES"]
        self.assertEqual(list(table), list(rl.PNG_PALETTE_CHOICES))
        self.assertEqual(list(table.values()), ["ユーザー指定", "ライト", "ダーク"])
        # 選択肢の意味はツールチップ（名前に説明を付けない: U-24）
        self.assertEqual(list(web.texts_json()["PNG_PALETTE_TIPS"]), list(rl.PNG_PALETTE_CHOICES))
        # PNG を描くときに pngColors() の色を使い、設定の画面の欄は検めた選び方（pngPalette）を映す
        app = (UI_WEB / "app.js").read_text(encoding="utf-8")
        self.assertRegex(app, r"pngBlob\(note\) \{\s*const colors = this\.pngColors\(\);")
        windows = (UI_WEB / "app_windows.js").read_text(encoding="utf-8")
        self.assertIn(":value=\"a.pngPalette\"", windows)
        self.assertIn(":title=\"a.T('PNG_PALETTE_TIPS', c)\"", windows)
        self.assertNotIn("png_palette_note", windows)
        # png.js は渡された配色（ctx.colors）だけで描く: 色の値を直に書いていない
        png = (UI_WEB / "png.js").read_text(encoding="utf-8")
        self.assertIsNone(re.search(r"#[0-9a-fA-F]{3,6}\b", png))


if __name__ == "__main__":
    unittest.main()
