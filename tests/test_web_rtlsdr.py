"""Web の口の RTL-SDR・聞く・rtl_tcp と、画面の文言をサーバーが Qt と同じ関数で足すこと（J §5）。

偽物の DLL の RTL-SDR と本物の uvicorn で通す。JavaScript の文言の決まり（文を持たない・型の差し込みが
Python と同じ）は Node で確かめる。
"""
import json
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from pygigast.api.local import LocalApi, _RtlSdrBackend, _SimulatorBackend
from pygigast.api.web import server as web
from pygigast.core import report_layout as rl
from pygigast.core.devices.base import load_profile
from pygigast.core.instrument import GIGAST_PROFILE_PATH, Instrument
from pygigast.ui_qt import texts
from tests.test_instrument_rtlsdr import FakeLib, free_port
from tests.test_web_server import UI_WEB, WebServerTest, _Server, decode_trace

MHZ = 1_000_000
UNCAL = "dB（未校正）"


class WebRtlTest(unittest.TestCase):
    # 道具は Web の口の試験と同じもの（試験そのものは引き継がない）
    post = WebServerTest.post
    get = WebServerTest.get
    ws = WebServerTest.ws
    recv_until = staticmethod(WebServerTest.recv_until)
    tearDown = WebServerTest.tearDown

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        profile = load_profile(GIGAST_PROFILE_PATH)
        sim = _SimulatorBackend(profile)
        sim.speed = 0.05
        self.lib = FakeLib()
        self.lib.tones = [(84_700_000, 0.05)]
        rtl = _RtlSdrBackend(lib_factory=lambda: self.lib)
        self.api = LocalApi(Instrument({"simulator": sim, "rtlsdr": rtl}, profile=profile,
                                       data_root=root / "data", cal_root=root / "cal"))
        self.srv = _Server(self.api)

    def connect_rtl(self, ws):
        self.assertEqual(self.post("connect", {"backend": "rtlsdr", "port": "rtlsdr:0"})[0], 200)
        self.post("set_sweep", {"start_hz": 84 * MHZ, "stop_hz": 85_500_000})
        self.post("run", {"mode": "single"})
        self.recv_until(ws, lambda k, p: k == "state" and p["run"]["last_stop_reason"] == "single_done")

    def test_state_ui_follows_device(self):
        _, body = self.get("/api/v1/state")
        ui = json.loads(body)["ui"]
        self.assertEqual([c["hz"] for c in ui["rbw"][0]["choices"]], [180000, 30000, 15000])
        self.assertTrue(ui["rtl"]["reason"])
        self.assertFalse(ui["rtl"]["shown"])  # 繋いでいない: 受信の欄を出さない（設計J §4.5）
        self.assertEqual(self.post("connect", {"backend": "simulator", "serial": "SIM00001"})[0], 200)
        self.assertFalse(json.loads(self.get("/api/v1/state")[1])["ui"]["rtl"]["shown"])  # GigaSt でも出さない
        self.assertEqual(ui["status"], texts.status_items(json.loads(body)))
        with self.ws() as ws:
            self.connect_rtl(ws)
        ui = json.loads(self.get("/api/v1/state")[1])["ui"]
        self.assertEqual([g["label"] for g in ui["rbw"]], ["Hann", "Flat top"])
        self.assertEqual(ui["mode_reasons"]["tg"], texts.MODE_UNSUPPORTED)
        self.assertEqual(ui["sweep_reasons"]["points"], texts.RTL_LABELS["points_fft"])
        self.assertIsNone(ui["rtl"]["reason"])
        self.assertTrue(ui["rtl"]["shown"])
        self.assertEqual(len(ui["rtl"]["gains"]), 30)
        self.assertIn(UNCAL, ui["rtl"]["unit_text"])
        self.assertTrue(any(i["key"] == "cal" and UNCAL in i["text"] for i in ui["status"]))

    def test_trace_tab_section_order(self):
        # 「トレース・掃引」のタブ: ①掃引 ②トレース ③参照トレース ④機器に固有の欄（RTL-SDR のときだけ: J §4.5）
        html = (UI_WEB / "index.html").read_text(encoding="utf-8")
        a = html.index('data-tab="trace"')
        b = html.index('data-tab="marker"')
        secs = re.findall(r'<section class="tsec" data-sec="(\w+)"([^>]*)>', html[a:b])
        self.assertEqual([s[0] for s in secs], ["sweep", "traces", "files", "rtlsdr"])
        self.assertIn('v-if="rtlView.shown"', secs[3][1])
        # 受信の欄の部品はその section の中だけにある
        self.assertNotIn("RTL_LABELS", html[a:html.index('data-sec="rtlsdr"')])

    def test_markers_rows_and_calibration_texts(self):
        with self.ws() as ws:
            self.connect_rtl(ws)
            st, r = self.post("marker_add", {"kind": "M", "trace": 1, "freq_hz": 84_700_000})
            self.assertEqual(st, 200)
            m = self.recv_until(ws, lambda k, p: k == "markers" and p.get("rows"))
        row = m["rows"][0]
        self.assertEqual(row["id"], "M1")
        self.assertIn(UNCAL, row["cells"][3])
        self.assertIn(texts.MARKER_NOTES["hann_scalloping"], row["cells"][7])
        st, body = self.post("rtlsdr_calibrate_ppm", {"marker_id": "M1", "true_freq_hz": 84_705_000})
        self.assertEqual(st, 200)
        self.assertEqual(body["ppm"], 59)
        self.assertIn("ppm を", body["texts"]["done"])
        st, body = self.post("rtlsdr_calibrate_level", {"marker_id": "M1", "true_dbm": -30})
        self.assertEqual(st, 200)
        self.assertIn("レベルの補正", body["texts"]["done"])
        # 失敗にも画面の文を足す
        st, body = self.post("rtlsdr_calibrate_ppm", {"marker_id": "M9", "true_freq_hz": 84_705_000})
        self.assertEqual(st, 400)
        self.assertEqual(body["error"]["text"], texts.ERRORS[body["error"]["code"]])

    def test_listen_audio_binary_and_status(self):
        with self.ws() as ws:
            self.connect_rtl(ws)
            self.post("marker_add", {"kind": "M", "trace": 1, "freq_hz": 84_700_000})
            st, body = self.post("listen_start", {"marker_id": "M1", "mode": "fm_broadcast"})
            self.assertEqual(st, 200)
            header, values = self.recv_until(ws, lambda k, p: k == "trace" and p[0]["kind"] == "audio")
            self.assertEqual(header["rate"], 48000)
            self.assertEqual(header["value_count"], len(values))
            self.assertGreater(len(values), 0)
            s = self.recv_until(ws, lambda k, p: k == "state" and p["listen"]["active"])
            self.assertTrue(any(i["key"] == "listen" and "FM放送" in i["text"] for i in s["ui"]["status"]))
            self.assertIn("M1", s["ui"]["listen"]["now_text"])
            self.post("listen_stop")
            n = self.recv_until(ws, lambda k, p: k == "notice" and p["code"] == "listen_stopped")
            self.assertEqual(n["view"]["text"], texts.notice_text(n))

    def test_rtltcp_settings_and_status(self):
        port = free_port()
        with self.ws() as ws:
            st, _ = self.post("rtltcp_set", {"enabled": True, "port": port})
            self.assertEqual(st, 200)
            s = self.recv_until(ws, lambda k, p: k == "state" and p["rtltcp"]["running"])
            self.assertTrue(any(i["key"] == "rtltcp" and f":{port}" in i["text"] for i in s["ui"]["status"]))
            self.assertIn(str(port), s["ui"]["rtltcp"]["text"])
            self.post("rtltcp_set", {"enabled": False})
            self.recv_until(ws, lambda k, p: k == "state" and not p["rtltcp"]["running"])

    def test_png_layout_same_rules_as_qt(self):
        with self.ws() as ws:
            self.connect_rtl(ws)
        self.post("marker_add", {"kind": "M", "trace": 1, "freq_hz": 84_700_000})
        st, lay = self.post("png_layout", {"width": 1280, "height": 720, "note": "memo", "ref_level": 0,
                                           "db_per_div": 10, "unit": UNCAL})
        self.assertEqual(st, 200)
        self.assertEqual(lay["font_px"], rl.font_px(1280, 720))
        self.assertEqual(lay["table"]["header"], list(rl.MARKER_COLUMNS))
        self.assertEqual(lay["table"]["rows"][0][0], "M1")
        self.assertIn("メモ: memo", [it for _c, items in lay["band"] for it in items])
        self.assertEqual(lay["meta_key"], rl.META_KEY)
        self.assertEqual(lay["legend"][0]["color"], "trace1")
        st, body = self.post("png_layout", {"width": 10, "height": 10})
        self.assertEqual(st, 400)


@unittest.skipUnless(shutil.which("node"), "Node が無い")
class TextsJsTest(unittest.TestCase):
    """texts.js の差し込みが texts.tpl と同じになること。"""

    def test_fmt_same_as_python(self):
        kw = {"value": "1.2 MHz", "reason": "上限", "n": 3, "port": 1234, "clients": 0, "freq": "84.7 MHz",
              "mode": "FM放送", "s": "1.5", "text": "x", "cause": "c", "center": "84.4 MHz"}
        data = json.loads(web.dumps(web.texts_json()))
        script = (
            "const X = require(process.argv[1]); X.load(JSON.parse(require('fs').readFileSync(0, 'utf8')));"
            "const kw = JSON.parse(process.argv[2]); const out = {};"
            "for (const k of Object.keys(X.dict('TEMPLATES'))) out[k] = X.fmt(k, kw);"
            "console.log(JSON.stringify(out));"
        )
        out = subprocess.run(["node", "-e", script, str(UI_WEB / "texts.js"), json.dumps(kw)], input=json.dumps(data),
                             capture_output=True, text=True, encoding="utf-8", timeout=30, check=True).stdout
        got = json.loads(out)
        self.assertEqual(got, {k: texts.tpl(k, **kw) for k in texts.TEMPLATES})


class WebWordingTest(unittest.TestCase):
    """Web の画面は文を持たない（文言の正本は texts の表だけ: J §5）。"""

    JP = re.compile(r"[぀-ヺー-ヿ一-鿿]")  # 「・」は区切りの記号なので除く

    def test_no_japanese_text_outside_comments(self):
        for f in sorted(UI_WEB.glob("*.js")):
            src = f.read_text(encoding="utf-8")
            src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
            src = re.sub(r"(^|\s)//.*$", "", src, flags=re.M)
            self.assertIsNone(self.JP.search(src), f"{f.name}: {self.JP.search(src) and src[self.JP.search(src).start() - 40:self.JP.search(src).start() + 20]}")
        html = (UI_WEB / "index.html").read_text(encoding="utf-8")
        html = re.sub(r"<!--.*?-->", "", html, flags=re.S)
        html = re.sub(r"<noscript>.*?</noscript>", "", html, flags=re.S)  # JavaScript が無いときの1文だけは表から引けない
        m = self.JP.search(html)
        self.assertIsNone(m, m and html[m.start() - 60:m.start() + 20])

    def test_labels_used_by_web_exist(self):
        src = "".join(f.read_text(encoding="utf-8") for f in UI_WEB.glob("*.js")) + \
            (UI_WEB / "index.html").read_text(encoding="utf-8")
        for key in set(re.findall(r"\bW\(\s*['\"]([a-z0-9_\-]+)['\"]", src)):
            if key.endswith("_"):
                continue  # 後ろを足して引く名前（"part_" + 部分の名前・"tab_" + タブ）
            self.assertIn(key, texts.LABELS, key)
        for key in set(re.findall(r"\bX\.fmt\(\s*['\"]([a-z0-9_]+)['\"]", src)) | \
                set(re.findall(r"\bF\(\s*['\"]([a-z0-9_]+)['\"]", src)):
            self.assertIn(key, texts.TEMPLATES, key)


if __name__ == "__main__":
    unittest.main()
