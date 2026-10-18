"""画面の語の決まり（設計J §4.9）と、語を表だけに置くこと（J §5）の試験。

- 画面の文言の表（ui_qt/texts*.py。Web も同じ表を使う）に「使わない語」が入っていない
- PNG の帯と表の語（core/report_layout.py）・`serve` などの起動の説明（__main__.py）にも入っていない
- Qt の画面のコード（ui_qt/*.py。texts*.py を除く）に日本語の文を直に書いていない（表へ移す）
- Web の画面（ui_web/）は文を持たない（コメントを除いて日本語の文字が無い）
- 「聞く」の欄は IQ を持つ機器（RTL-SDR）に繋いでいるときだけ出す（J §4.5）
"""
from __future__ import annotations

import ast
import re
import unittest
from pathlib import Path

from namibench.ui_qt import screen_model, texts

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "namibench"

# 使わない語（J §4.9 の表の左の列。開発者の俗称・内部の名前・設計の番号・プログラムの言葉）。
# 大文字と小文字を区別しない（"FFT" も "fft" も機種名で言い換える: J §4.9 の表）
BANNED = (
    "口", "鍵", "トークン", "設計", "§", "PATH", "前置き", "過渡", "正本", "sweep_kind", "fft", "AD 値", "次の回",
    "LAN に開", "LAN に公開", "Q が 0", "IQ を持つ", "既定（PATH", "生の値", "イベント", "スレッド", "キュー",
    "命令", "引数", "Web の口",
    # 語を略さない（docs/uxui_rule.md U-26。2026-10-03 ユーザー指摘「アプリ設定は省略を使わない UX/UI ルール違反」）
    "アプリ", "スマホ",
)

# 使わない語の文字を含むが、計測器・無線の語として使ってよいもの（J §4.9 の「使ってよい」）。
# 語 → 理由。ここに足すときは理由を書く
ALLOWED_WORDS: dict[str, str] = {
    "アプリケーション": "略さない形（「アプリ」を使わない: U-26）",
}

# 画面の語ではない表（コード・並び・色の種類）。値は画面に出ない
STRUCTURAL = {"MENU_LAYOUT", "MENU_NOT_YET", "MENU_MNEMONICS", "DEVICE_STATE_CLS", "RULE_REASONS", "FREQ_FIELDS",
              "MARKER_NOTES_SILENT", "POWER_HINT_REASONS", "DETACH_PARTS"}

# 日本語の文字（ひらがな・カタカナ・漢字）。「・」「ー」と全角のかっこ・句読点は文の部品の区切りとして許す
JP_LETTER = re.compile(r"[ぁ-ゖァ-ヺ一-鿿]")

# Qt の画面のコードに残してよい日本語（表へ移せないもの）: ファイル → {文字: 理由}
QT_LITERAL_ALLOW = {
    "png_export.py": {
        "あ": "全角1字の幅を測るための見本の字（画面に出さない）",
        "PNG ではありません": "作った PNG の形を確かめる自己検査の例外（利用者の操作では起きない）",
        "CRC が一致しません: ": "同上（自己検査の例外）",
        "IEND がありません": "同上（自己検査の例外）",
        "画像が小さすぎます: ": "大きさは選択肢から選ぶので利用者の操作では起きない（core/report_layout.check_size と同じ検査）",
        "PNG に書き込めませんでした": "Qt の画像の保存の失敗（利用者の操作では起きない自己検査）",
        "PNG を読み込めません": "同上（自己検査の例外）",
    },
}

# Web の画面に残してよい日本語: ファイル → {行の中の文字: 理由}
WEB_ALLOW = {
    "index.html": {
        "この画面は JavaScript を使います。": "JavaScript が動かないときの表示（表を受け取る前なので表から引けない）",
    },
}


def _walk(obj, path: str):
    if isinstance(obj, str):
        yield path, obj
    elif isinstance(obj, dict):
        for k, v in obj.items():
            yield from _walk(v, f"{path}.{k}")
    elif isinstance(obj, (list, tuple)):
        for i, v in enumerate(obj):
            yield from _walk(v, f"{path}[{i}]")


def table_strings():
    """画面の文言の表の全部の文字（Web へ配る /api/v1/texts と同じ、大文字の名前の表）。"""
    for name in dir(texts):
        if not name.isupper() or name in STRUCTURAL:
            continue
        value = getattr(texts, name)
        if name == "MODES":
            # (コード, ボタンの文字, 名前, 使えない理由) のうち画面に出る3つ
            for i, (_code, label, mname, why) in enumerate(value):
                yield from _walk((label, mname, why or ""), f"MODES[{i}]")
            continue
        yield from _walk(value, name)


def _literals(path: Path):
    """Python のファイルの文字列の定数（docstring を除く）を (行, 文字) で返す。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docs = set()
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and body \
                and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
            docs.add(id(body[0].value))
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docs:
            yield node.lineno, node.value


def banned_in(text: str) -> list[str]:
    t = text
    for word in ALLOWED_WORDS:
        t = t.replace(word, "")
    low = t.lower()
    return [b for b in BANNED if b.lower() in low]


class BannedWordsTest(unittest.TestCase):
    def test_tables_have_no_banned_words(self):
        hits = [(p, s, banned_in(s)) for p, s in table_strings() if banned_in(s)]
        self.assertEqual(hits, [])

    def test_tables_are_not_empty(self):
        # 表を読めていること（読めずに空なら上の試験は何も守らない）
        paths = [p for p, _s in table_strings()]
        self.assertGreater(len(paths), 900)
        for name in ("LABELS", "TEMPLATES", "ERRORS", "WEB_LABELS", "SETTINGS_LABELS", "NOT_YET", "REASON_TEXTS"):
            self.assertTrue(any(p.startswith(name + ".") for p in paths), name)

    def test_detects_a_banned_word(self):
        # 検査そのものが働くこと（わざと入れて見つかるか）
        self.assertEqual(banned_in("Web の口を開く"), ["口", "Web の口"])
        self.assertEqual(banned_in("この機器（FFT で測る）"), ["fft"])
        self.assertEqual(banned_in("次の回で実装します（設計J §4.6）"), ["設計", "§", "次の回"])
        self.assertEqual(banned_in("掃引の始めの不安定な点"), [])

    def test_png_and_console_texts_have_no_banned_words(self):
        for rel in ("core/report_layout.py", "__main__.py"):
            hits = [(ln, s) for ln, s in _literals(PKG / rel) if JP_LETTER.search(s) and banned_in(s)]
            self.assertEqual(hits, [], rel)

    def test_image_reject_word(self):
        # 画面の語は「イメージキャンセル」を「イメージ除去」にそろえる（設計F §7.4: メニュー・知らせ・ツールチップ・PNG）
        old = "イメージキャンセル"
        self.assertEqual([p for p, s in table_strings() if old in s], [])
        self.assertEqual([(ln, s) for ln, s in _literals(PKG / "core/report_layout.py") if old in s], [])
        self.assertTrue([p for p, s in table_strings() if "イメージ除去" in s])

    def test_noise_floor_word(self):
        # 「床」は一般の語ではないので「ノイズフロア」にそろえる（設計P §9.7・J §4.9）。ピークの一覧の欄の名前は
        # 基準で変わる（電力はノイズフロアから、基準との比は最大から）
        self.assertEqual([(p, s) for p, s in table_strings() if "床" in s], [])
        self.assertEqual((texts.LABELS["from_floor"], texts.LABELS["from_max"]), ("ノイズフロアから", "最大から"))
        self.assertIn("ノイズフロア", texts.LABELS["peaks_found_floor"])
        self.assertIn("ノイズフロア", texts.LABELS["floor_tip"])


class HardCodedTextTest(unittest.TestCase):
    def test_qt_code_has_no_japanese_literals(self):
        """Qt の画面の語は表（texts*.py）から取る（J §5: Web と同じ表。J §4.9: 試験で語を確かめられる）。"""
        found = []
        for path in sorted((PKG / "ui_qt").glob("*.py")):
            if path.name.startswith("texts"):
                continue
            allow = QT_LITERAL_ALLOW.get(path.name, {})
            for ln, s in _literals(path):
                if JP_LETTER.search(s) and s not in allow:
                    found.append(f"{path.name}:{ln}: {s!r}")
        self.assertEqual(found, [])

    def test_qt_allow_list_is_used(self):
        # 許した文字が実際にまだあること（消えたら許可も消す）
        for name, allow in QT_LITERAL_ALLOW.items():
            have = {s for _ln, s in _literals(PKG / "ui_qt" / name)}
            self.assertEqual(sorted(set(allow) - have), [], name)

    def test_web_files_have_no_sentences(self):
        """Web の画面は文を持たない（表と、サーバーが足した文を映すだけ）。コメントは除く。"""
        found = []
        for path in sorted((PKG / "ui_web").iterdir()):
            if path.suffix not in (".js", ".html", ".css"):
                continue
            t = path.read_text(encoding="utf-8")
            if path.suffix == ".html":
                t = re.sub(r"<!--.*?-->", lambda m: "\n" * m.group(0).count("\n"), t, flags=re.S)
            t = re.sub(r"/\*.*?\*/", lambda m: "\n" * m.group(0).count("\n"), t, flags=re.S)
            t = re.sub(r"(?m)(^|[^:\\])//.*$", r"\1", t)
            allow = WEB_ALLOW.get(path.name, {})
            for i, line in enumerate(t.split("\n"), 1):
                rest = line
                for a in allow:
                    rest = rest.replace(a, "")
                if JP_LETTER.search(rest):
                    found.append(f"{path.name}:{i}: {line.strip()[:80]}")
        self.assertEqual(found, [])


class PortNameTest(unittest.TestCase):
    """ポートは COM 番号を持つ GigaSt だけ出す。RTL-SDR（rtlsdr:0）・シミュレータのポートは内部の名前（J §4.9）。"""

    @staticmethod
    def conn_text(backend, port):
        st = {"connection": {"state": "connected", "backend": backend, "name": "X 0001",
                             "identity": {"port": port, "serial": "S"}}, "run": {"state": "stopped"}}
        return next(i["text"] for i in texts.status_items(st) if i["key"] == "conn")

    def test_status_bar(self):
        self.assertIn("COM7", self.conn_text("gigast", "COM7"))
        self.assertNotIn("rtlsdr:0", self.conn_text("rtlsdr", "rtlsdr:0"))
        self.assertNotIn("simulator", self.conn_text("simulator", "simulator"))
        self.assertIn("X 0001", self.conn_text("rtlsdr", "rtlsdr:0"))

    def test_profile_notice_uses_model_name(self):
        # 機種のプロファイルの名前（コード）は出さず、機種名で言う
        t = texts.notice_text({"code": "device_profile_changed", "detail": {"model": "rtlsdr-r820t"}})
        self.assertIn("RTL-SDR (R820T)", t)
        self.assertNotIn("rtlsdr-r820t", t)

    def test_device_details(self):
        self.assertIn("COM7", texts.device_details({"kind": "gigast", "port": "COM7", "serial": "A"}))
        self.assertNotIn("rtlsdr:0", texts.device_details({"kind": "rtlsdr", "port": "rtlsdr:0", "serial": "1"}))


class ListenShownTest(unittest.TestCase):
    """「聞く」の欄は IQ を持つ機器に繋いでいるときだけ出す（J §4.5。Qt と Web は同じ listen_view の shown）。"""

    def test_shown_only_for_connected_iq_device(self):
        none = {}
        gigast = {"connection": {"state": "connected", "sweep_kind": "stepped"}, "rtlsdr": {"connected": False}}
        rtl = {"connection": {"state": "connected", "sweep_kind": "fft"}, "rtlsdr": {"connected": True}}
        self.assertFalse(screen_model.listen_view(none)["shown"])
        self.assertFalse(screen_model.listen_view(gigast)["shown"])
        self.assertTrue(screen_model.listen_view(rtl)["shown"])
        # 出さないときも理由は持つ（メニューの「選んでいるマーカーの位置を聞く」は消さずに無効: U-08）
        self.assertEqual(screen_model.listen_view(gigast)["reason"], texts.LISTEN_LABELS["not_iq"])

    def test_web_section_follows_shown(self):
        html = (PKG / "ui_web" / "index.html").read_text(encoding="utf-8")
        start = html.index('<template v-if="listenView.shown">')
        end = html.index("</template>", start)
        self.assertIn("T('LISTEN_LABELS', 'group')", html[start:end])
        # 聞く間の動き（チューナーの中心をずらす・掃引が止まる）は画面に書かない（取扱説明書: J §4.12・U-26）
        self.assertNotIn("listen_note", html[start:end])


if __name__ == "__main__":
    unittest.main()
