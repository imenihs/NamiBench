"""Web の口の PlutoSDR（設計Q §7）: 状態の ui.pluto が Qt と同じ関数（screen_pluto.pluto_view）で作られ、命令が通ること。

libiio の偽物と本物の uvicorn で通す。受信の利得の欄は REF の欄と同じ db-field（打てるドロップダウン。一覧は自動と
10dB 刻み）で、Qt の DbChoiceField と同じ決まりで動くこと（Node と Edge で確かめる）。
"""
import json
import re
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from pygigast.api.local import LocalApi, _PlutoSdrBackend, _SimulatorBackend
from pygigast.core.devices.base import load_profile
from pygigast.core.devices.plutosdr import driver as pd
from pygigast.core.devices.plutosdr.fake import FAKE_URI, FakeIioLib
from pygigast.core.instrument import GIGAST_PROFILE_PATH, Instrument
from pygigast.core.units import minus_sign
from pygigast.ui_qt import screen_pluto, texts
from pygigast.ui_qt.fields import range_note
from tests import test_web_server as wst
from tests.test_web_server import UI_WEB, _Server

MHZ = 1_000_000


class WebPlutoTest(unittest.TestCase):
    post = wst.WebServerTest.post
    get = wst.WebServerTest.get
    ws = wst.WebServerTest.ws
    recv_until = staticmethod(wst.WebServerTest.recv_until)
    tearDown = wst.WebServerTest.tearDown

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        profile = load_profile(GIGAST_PROFILE_PATH)
        sim = _SimulatorBackend(profile)
        sim.speed = 0.05
        self.lib = FakeIioLib()
        pd._cache = pd._SerialCache()
        pl = _PlutoSdrBackend(lib_factory=lambda: self.lib)
        self.api = LocalApi(Instrument({"simulator": sim, "plutosdr": pl}, profile=profile,
                                       data_root=root / "data", cal_root=root / "cal"))
        self.srv = _Server(self.api)

    def state(self):
        return json.loads(self.get("/api/v1/state")[1])

    def test_ui_follows_device_and_commands(self):
        st = self.state()
        self.assertFalse(st["ui"]["pluto"]["shown"])
        self.assertEqual(st["ui"]["pluto"]["reason"], texts.PLUTO_LABELS["not_pluto"])
        self.assertEqual(self.post("connect", {"backend": "plutosdr", "port": FAKE_URI})[0], 200)
        st = self.state()
        ui = st["ui"]["pluto"]
        self.assertTrue(ui["shown"])
        self.assertEqual(ui, json.loads(json.dumps(screen_pluto.pluto_view(st))))
        self.assertEqual(ui["gain_choices"], [0.0, 10.0, 20.0, 30.0, 40.0, 50.0, 60.0, 70.0])
        self.assertEqual((ui["gain_lo"], ui["gain_hi"], ui["gain_db"]), (-1.0, 73.0, 30.0))
        self.assertEqual(ui["auto_text"], texts.PLUTO_LABELS["auto"])
        # 送信の減衰の一覧（0〜−80dB の 10dB 刻みと最小の −89.75dB: −90 は機器の範囲の外）と打てる範囲
        self.assertEqual(ui["tx_atten_choices"], [0.0, -10.0, -20.0, -30.0, -40.0, -50.0, -60.0, -70.0, -80.0, -89.75])
        self.assertEqual((ui["tx_atten_min"], ui["tx_atten_max"], ui["tx_atten_db"]), (-89.75, 0.0, -40.0))
        self.assertEqual(self.post("plutosdr_set", {"gain_db": 20.0})[0], 200)
        self.assertEqual(self.state()["plutosdr"]["gain_db"], 20.0)
        code, body = self.post("plutosdr_set", {"tx_atten_db": 3.0})
        self.assertNotEqual(code, 200)
        # DLL のフォルダはブラウザから変えない（設計A §4.3.2）
        code, body = self.post("plutosdr_set", {"dll_folder": "C:/x"})
        self.assertNotEqual(code, 200)
        # TG の「広く取る」は FFT の機器では理由付きで使えない
        self.post("set_mode", {"mode": "tg"})
        self.assertEqual(self.state()["ui"]["tg_wide_reason"], texts.ERRORS["reference_wide_fft"])
        self.assertEqual(self.state()["ui"]["ref_quick"]["take"]["kind"], "fine")

    def test_page_has_same_parts_as_qt(self):
        html = (UI_WEB / "index.html").read_text(encoding="utf-8")
        a = html.index('data-tab="trace"')
        b = html.index('data-tab="marker"')
        secs = re.findall(r'<section class="tsec" data-sec="(\w+)"', html[a:b])
        self.assertEqual(secs[-2:], ["rtlsdr", "plutosdr"])  # 機器に固有の欄は最後（Qt の add_device_section と同じ順）
        self.assertIn('data-part="pluto_tx"', html)
        # 受信の利得は REF の欄と同じ db-field（Qt の DbChoiceField と同じ部品）。1dB 刻みの全部を並べた一覧は無い
        self.assertRegex(html, r'<db-field data-field="pluto_gain"[^>]*:decimals="0"[^>]*:special="plutoView.auto_text"')
        self.assertNotIn('<select data-field="pluto_gain"', html)
        # 送信の減衰も同じ db-field。一覧と範囲は ui.pluto（Qt と同じ screen_pluto.pluto_view）から、小数 2 桁で出す
        self.assertRegex(html, r'<db-field data-field="tx_atten"[^>]*:choices="plutoView.tx_atten_choices"'
                               r'[^>]*:lo="plutoView.tx_atten_min"[^>]*:hi="plutoView.tx_atten_max"[^>]*:decimals="2"'
                               r'''[^>]*:field-note="notes.plutotx"[^>]*@clear-note="clearNote\('plutotx'\)"''')
        self.assertNotIn('<num-field data-field="tx_atten"', html)
        win = (UI_WEB / "app_windows.js").read_text(encoding="utf-8")
        self.assertIn("page === 'plutosdr'", win)


@unittest.skipUnless(shutil.which("node"), "Node が無い")
class WebGainFieldRulesTest(unittest.TestCase):
    """fields.js の db-field の「自動」の行と整数の欄を、Vue を使わずに中の処理だけ動かして確かめる（Qt の DbChoiceField と
    同じ決まり: test_ui_qt_plutosdr）。"""

    SCRIPT = r"""
globalThis.window = globalThis;
globalThis.document = { activeElement: null };
const fs = require("fs");
require(process.argv[1]);
GSTexts.load(JSON.parse(fs.readFileSync(process.argv[2], "utf-8")));
globalThis.GSUnits = require(process.argv[4]);
require(process.argv[3]);
const F = GSFields;
function db(props) {
  const c = F.DbField;
  const self = { value: 30, unit: "dB", choices: [0, 10, 20, 30, 40, 50, 60, 70], signed: false, lo: -1, hi: 73, decimals: 0,
                 special: "AUTO", showSpecial: false, emitted: [], note: null, ...props };
  self.$emit = (n, v) => self.emitted.push([n, v === undefined ? null : v]);
  for (const [k, f] of Object.entries(c.methods)) self[k] = f.bind(self);
  for (const [k, f] of Object.entries(c.computed)) Object.defineProperty(self, k, { get: () => f.call(self) });
  return self;
}
const out = {};
let d = db({});
out.items = d.items.map((i) => i.text);
out.text = [d.text, db({ showSpecial: true }).text, db({ value: -0.2 }).text, db({ value: -3 }).text];
d.onCommit("42.4"); d.onCommit("80"); d.onCommit(" AUTO "); d.onCommit("AUTO dB");
out.typed = d.emitted; out.note = d.note && d.note.text;
d = db({ showSpecial: true, value: 71, hi: 71 }); d.onStep(0, 1); d.onStep(0, -1); out.stepped = d.emitted;
// 送信の減衰（小数 2 桁・範囲 −89.75〜0dB・一覧は 0〜−80 と −89.75。呼ぶ側の知らせ fieldNote）
const atten = (p) => db({ value: -30, choices: [0, -10, -20, -30, -40, -50, -60, -70, -80, -89.75], lo: -89.75, hi: 0,
                          decimals: 2, special: null, ...p });
d = atten({});
out.attenItems = d.items.map((i) => i.text);
out.attenText = [d.text, atten({ value: 0 }).text, atten({ value: -89.75 }).text];
d.onCommit("5"); d.onCommit("-90"); out.attenNote = d.note && d.note.text; d.onCommit("−12.3"); d.onCommit("-89.75 dB");
out.attenTyped = d.emitted;
d = atten({ value: -89.75 }); d.onStep(0, -1); d.onStep(0, 1); d.onStep(-2, 1); out.attenStepped = d.emitted;
d = atten({}); d.note = { text: "typed" }; d.onClearNote(); out.clearOwn = [d.note, d.emitted.length];
d.onClearNote(); out.clearOuter = d.emitted;
console.log(JSON.stringify(out));
"""

    def test_same_rules_as_qt(self):
        from pygigast.api.web import server as web
        from pygigast.ui_qt.fields import db_choice_text, db_value_text, range_note

        with tempfile.TemporaryDirectory() as d:
            tj = Path(d) / "texts.json"
            tj.write_text(json.dumps(web.texts_json(), ensure_ascii=False), encoding="utf-8")
            out = subprocess.run(["node", "-e", self.SCRIPT, str(UI_WEB / "texts.js"), str(tj), str(UI_WEB / "fields.js"),
                                  str(UI_WEB / "units.js")],
                                 capture_output=True, text=True, timeout=30, check=True, encoding="utf-8").stdout
        got = json.loads(out)
        self.assertEqual(got["items"], ["AUTO"] + [f"{g} dB" for g in range(0, 71, 10)])
        self.assertEqual(got["text"], ["30 dB", "AUTO", "0 dB", "−3 dB"])
        self.assertEqual([db_value_text(v, 0) for v in (30, -0.2, -3)], ["30", "0", "−3"])
        self.assertEqual(got["typed"], [["commit", 42], ["special", None]])  # 範囲の外と読めない文字は送らない
        self.assertEqual(got["note"], texts.LABELS["bad_value"])
        self.assertEqual(range_note(-1, 73, "dB"), range_note(-1.0, 73.0, "dB"))
        self.assertEqual(got["stepped"], [["commit", 71], ["commit", 70]])  # 端で止める
        # 送信の減衰: 一覧の行・欄の文字は Qt の DbChoiceField（db_choice_text・db_value_text）と同じ
        choices = screen_pluto.tx_atten_choices(-89.75, 0.0)
        self.assertEqual(got["attenItems"], [db_choice_text(v, "dB", False) for v in choices])
        self.assertEqual(got["attenItems"], ["0 dB"] + [f"−{a} dB" for a in range(10, 81, 10)] + ["−89.75 dB"])
        self.assertEqual(got["attenText"], [f"{db_value_text(v, 2)} dB" for v in (-30, 0, -89.75)])
        self.assertEqual(got["attenText"], ["−30.00 dB", "0.00 dB", "−89.75 dB"])
        self.assertEqual(got["attenNote"], range_note(-89.75, 0.0, "dB"))  # 範囲の外は送らずに範囲を知らせる
        self.assertEqual(got["attenTyped"], [["commit", -12.3], ["commit", -89.75]])  # 0.25dB への丸めはコア
        self.assertEqual(got["attenStepped"], [["commit", -89.75], ["commit", -88.75], ["commit", -89.74]])
        # 欄の知らせ: 打った値の知らせを先に消し、無ければ呼ぶ側の知らせ（notes.plutotx）を消させる
        self.assertEqual(got["clearOwn"], [None, 0])
        self.assertEqual(got["clearOuter"], [["clear-note", None]])


def _edge_ok() -> bool:
    try:
        from tests.test_web_layout import edge_available
    except Exception:  # noqa: BLE001
        return False
    return edge_available()


@unittest.skipUnless(_edge_ok(), "Microsoft Edge が無い")
class WebGainFieldBrowserTest(unittest.TestCase):
    """ヘッドレスの Edge で、受信の利得の欄に打つ・一覧から選ぶ・自動を選ぶ（Qt の test_gain_auto_and_used_value と同じ流れ）。"""

    @classmethod
    def setUpClass(cls):
        from tests.test_web_layout import App, EdgePage

        cls.app = App(lan=False, pluto=True)
        try:
            cls.page = EdgePage()
        except Exception:
            cls.app.close()
            raise

    @classmethod
    def tearDownClass(cls):
        cls.page.close()
        cls.app.close()

    def gain(self):
        return self.app.call("get_state")["plutosdr"]

    def wait_state(self, cond, what):
        end = time.monotonic() + 15
        while not cond(self.gain()):
            self.assertLess(time.monotonic(), end, what)
            time.sleep(0.05)

    def test_type_choose_and_auto(self):
        from tests.test_web_layout import open_app

        a, p = self.app, self.page
        open_app(p, a, 1280, 720)
        p.ev("gsApp.tab = 'trace'; 1")
        p.settle()
        sel = "document.querySelector('[data-field=pluto_gain]')"
        # App は 2.4GHz 帯で 73dB を設定している: 欄は使う値 71dB、下に使う値の行
        self.assertEqual(p.ev(f"{sel}.querySelector('input').value"), "71 dB")
        opts = p.ev(f"[...{sel}.querySelectorAll('option')].map((o) => o.textContent.trim()).filter((t) => t)")
        self.assertEqual(opts, [texts.PLUTO_LABELS["auto"]] + [f"{g} dB" for g in range(0, 71, 10)])
        typed = ("(() => {{ const i = {sel}.querySelector('input'); i.focus(); i.value = '{v}'; "
                 "i.dispatchEvent(new Event('input')); i.dispatchEvent(new KeyboardEvent('keydown', {{key: 'Enter'}})); "
                 "return 1; }})()")
        p.ev(typed.format(sel=sel, v="55"))
        self.wait_state(lambda g: g["gain_db"] == 55.0 and not g["agc"], "打った利得")
        p.wait(f"{sel}.querySelector('input').value === '55 dB'", "欄が映す")
        pick = ("(() => {{ const s = {sel}.querySelector('select'); s.value = '{v}'; "
                "s.dispatchEvent(new Event('change')); return 1; }})()")
        p.ev(pick.format(sel=sel, v="20 dB"))
        self.wait_state(lambda g: g["gain_db"] == 20.0, "一覧の利得")
        p.ev(pick.format(sel=sel, v=texts.PLUTO_LABELS["auto"]))
        self.wait_state(lambda g: g["agc"], "自動")
        p.wait(f"{sel}.querySelector('input').value === {json.dumps(texts.PLUTO_LABELS['auto'])}", "欄が自動")
        self.assertEqual(p.errors, [])


@unittest.skipUnless(_edge_ok(), "Microsoft Edge が無い")
class WebTxAttenBrowserTest(unittest.TestCase):
    """ヘッドレスの Edge で、送信の減衰の欄の一覧から選ぶ・範囲の外を打つ（Qt の test_tx_atten_choices_and_range と同じ流れ）。
    SG・TG では plutosdr_set、外部 IQ の送信の間は ext_tx_atten に届く。"""

    SEL = "document.querySelector('[data-field=tx_atten]')"

    @classmethod
    def setUpClass(cls):
        from tests.test_web_layout import App, EdgePage

        cls.app = App(lan=False, pluto=True)
        try:
            cls.page = EdgePage()
        except Exception:
            cls.app.close()
            raise

    @classmethod
    def tearDownClass(cls):
        cls.page.close()
        cls.app.close()

    def wait_state(self, cond, what):
        end = time.monotonic() + 15
        while not cond(self.app.call("get_state")):
            self.assertLess(time.monotonic(), end, what)
            time.sleep(0.05)

    def open_source(self):
        from tests.test_web_layout import open_app

        open_app(self.page, self.app, 1280, 720)
        self.page.ev("gsApp.tab = 'source'; 1")
        self.page.settle()

    def pick(self, text):
        self.page.ev(f"(() => {{ const s = {self.SEL}.querySelector('select'); s.value = {json.dumps(text)}; "
                     "s.dispatchEvent(new Event('change')); return 1; })()")

    def type_in(self, text):
        self.page.ev(f"(() => {{ const i = {self.SEL}.querySelector('input'); i.focus(); i.value = {json.dumps(text)}; "
                     "i.dispatchEvent(new Event('input')); i.dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter'})); "
                     "return 1; })()")

    def wait_shown(self, text, what):
        self.page.wait(f"{self.SEL}.querySelector('input').value === {json.dumps(text)}", what)

    def test_choose_in_sg_tg_and_out_of_range(self):
        a, p = self.app, self.page
        try:
            self.open_source()
            opts = p.ev(f"[...{self.SEL}.querySelectorAll('option')].map((o) => o.textContent.trim()).filter((t) => t)")
            self.assertEqual(opts, ["0 dB"] + [f"−{v} dB" for v in range(10, 81, 10)] + ["−89.75 dB"])
            self.wait_shown("−40.00 dB", "初めの値")
            # SG・TG: 一覧から選ぶと plutosdr_set の tx_atten_db
            for mode, extra, text, want in (("sg", {"sg_hz": 2_401_000_000}, "−30 dB", -30.0),
                                            ("tg", {}, "−89.75 dB", -89.75)):
                a.call("set_mode", mode=mode, **extra)
                p.wait(f"gsApp.st.sweep.mode === '{mode}'", mode)
                self.pick(text)
                self.wait_state(lambda s: s["plutosdr"]["tx_atten_db"] == want, f"{mode} の送信の減衰")
                self.wait_shown(minus_sign(f"{want:.2f} dB"), "欄が映す（小数 2 桁）")
            # 範囲の外を打つと、欄の下に範囲を知らせて元に戻す（送らない）
            self.type_in("5")
            p.wait(f"!!{self.SEL}.querySelector('.note-pop')", "範囲の知らせ")
            self.assertEqual(p.ev(f"{self.SEL}.querySelector('.note-pop span').textContent"),
                             range_note(-89.75, 0.0, "dB"))
            self.wait_shown("−89.75 dB", "元に戻す")
            self.assertEqual(a.call("get_state")["plutosdr"]["tx_atten_db"], -89.75)
        finally:
            a.call("set_mode", mode="sg", sg_hz=2_401_000_000)
            a.call("plutosdr_set", tx_atten_db=-40.0)
        self.assertEqual(p.errors, [])

    def test_command_note_under_the_field(self):
        # 命令の知らせ（notes.plutotx: 機器が断った理由など）も、いままでと同じく欄の下に出る
        p = self.page
        self.open_source()
        p.ev("gsApp.note('plutotx', 'refused'); 1")
        p.wait(f"!!{self.SEL}.querySelector('.note-pop') && "
               f"{self.SEL}.querySelector('.note-pop span').textContent === 'refused'", "呼ぶ側の知らせ")
        self.assertEqual(p.errors, [])

    def test_choose_while_ext_iq_streaming(self):
        from tests.exttx_helpers import RawClient, free_port

        a, p = self.app, self.page
        a.call("set_mode", mode="sa")
        a.call("set_sweep", start_hz=1_574_000_000, stop_hz=1_577_000_000, rbw_hz=48000)
        a.call("run", mode="continuous")
        port = free_port()
        a.call("ext_tx_set", enabled=True, port=port)
        c = RawClient(port)
        try:
            c.wait_type("welcome")
            self.assertEqual(c.start(name="GPS emulator")["type"], "ready")
            c.feed()
            c.wait_type("on")
            self.open_source()
            p.wait("gsApp.plutoView.tx_atten_ext", "外部 IQ の送信の間")
            before = a.call("get_state")["plutosdr"]["tx_atten_db"]
            # 外部 IQ の送信の間は一覧から選ぶと ext_tx_atten（SG・TG の値は変えない）
            self.pick("−20 dB")
            self.wait_state(lambda s: s["ext_tx"]["atten_db"] == -20.0, "外部の送信の減衰")
            self.assertEqual(a.call("get_state")["plutosdr"]["tx_atten_db"], before)
            self.wait_shown("−20.00 dB", "欄が映す")
        finally:
            c.close()
            a.call("ext_tx_set", enabled=False)
            a.call("run", mode="single")
            a.call("set_sweep", start_hz=2_400_000_000, stop_hz=2_402_000_000, rbw_hz=1500)
            a.call("set_mode", mode="sg", sg_hz=2_401_000_000)
        self.assertEqual(p.errors, [])


if __name__ == "__main__":
    unittest.main()
