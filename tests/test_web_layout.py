"""Web の画面の部品の大きさとそろい（設計J §4.11・docs/uxui_rule.md S-01〜S-07）。

Qt の tests/qt_layout_check.py と同じ決まりを、ヘッドレスの Edge で開いた Web の画面で数える。見た目の崩れを人の目で
1つずつ見つけない（2026-10-03 ユーザー指摘「高さがガタガタずれている」）。主画面の右の全部のタブ・設定の全部の
ページ・プリセットの窓を、決めた解像度（1280×720・1920×1080）で開き、出ている全部の欄・ドロップダウン・ボタンに
ついて次を数える（Qt の試験と同じく、機器の一覧・CSV に書き出す・保存・ブラウザからの接続のしかたの窓も数える。
マーカー表の上の山／谷の切り替えを谷にした主画面と、マーカー表の行の「操作」を開いた一覧も数える）:

- 高さが段の値（上段 40px・標準 26px・状態バーと上段の名前の行 20px）でない
- 文字の大きさが段の値でない（上段は欄・ボタン・連続/単発/停止、標準、小。値は sizes.py と同じ）
- 同じ場所（スクロールする枠・窓）で同じ行に並ぶ同じ段の部品の、上端か高さが 1px より違う（縦にずれている）
- ボタンの文字が切れている（文字の幅・高さがボタンの中に入らない）・ボタンが文字の幅より伸びている（S-03）
- ドロップダウンの幅が一番長い選択肢より狭い・欄の幅が中の文字（値・空の見本）より狭い
- 標準の段の入力欄の幅が、値の種類の幅（整数・値・名前・色）のどれでもない（S-02。打てるドロップダウンは ▼ まで入れて測る。
  読むだけの長い文字・表のセルを直す欄・トレースの回数（選択肢の幅）は除く）
- 行を重ねる並び（表と data-repeat の格子）で、同じ列の欄・ドロップダウン（格子ではボタンも）の幅か左端が 1px より違う
  （2026-10-03 ユーザー指摘「箱の長さがそろってなくてガタガタ」）。格子では部品の種類を問わず同じ列で比べる（トレースの
  表の2行目の選択は、1行目の動作・回数の真下で同じ幅）
- 上段の周波数の欄の幅が、上段の字で一番広い見本（sizes.TOP_FREQ_SAMPLES）の幅＋左右の余白と枠＋1px でない
  （伸ばさない: S-02）。上段の REF の欄（欄＋▼）の幅が、いまの単位の「−120.0 単位」＋左右の余白×2＋▼＋枠でない
  （Qt の DbChoiceField と同じ）。モードのボタン（data-same-width の組）が、5つとも一番長い名前の幅で同じでない
- 上段が1行に収まる幅（SA の 1600×900・1920×1080、TG の 1920×1080）で、RBW が Center と同じ行にない
- メニューバーの題・並び・項目が Qt と違う。広い画面の上段に Qt に無い部品（「≡」・保存・PNG）がある
- 1行の文字（名前・状態）とチェックボックス・ラジオが、同じ行の部品と縦の真ん中で 2px より違う
- 名前の付いたチェックボックス・ラジオの高さが標準の段の値でない

段は置き場所で決まる（S-01）。画面は置き場所に data-tier（L・S。無ければ標準）を付けて段を示す。

機器はシミュレータ（受信・聞くの欄は偽物の DLL の RTL-SDR）。Vue と uPlot は CDN から読むので、読めないとき
（ネットワークが無い）と Edge が無いときは飛ばす。画面を撮って目で見るとき（C-01）も同じ道具（EdgePage・App・open_app・
views）を使う。
"""
from __future__ import annotations

import base64
import json
import shutil
import subprocess
import tempfile
import time
import unittest
import urllib.request
from pathlib import Path

EDGE = Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")
SIZES = ((1280, 720), (1920, 1080))
# 主画面の右のタブ（app.js の tabList と同じ並び）
TABS = ("trace", "marker", "view", "source")

def layout_config() -> dict:
    """数える台本に渡す値（Qt の sizes.py と同じ値。sizes.py は Qt を読まずに読める）。"""
    from pygigast.ui_qt import sizes

    return {
        "H": {"L": sizes.H_L, "M": sizes.H_M, "S": sizes.H_S}, "rowH": sizes.ROW_H,
        "font": {"lValue": sizes.FONT_L_VALUE, "lButton": sizes.FONT_L_BUTTON, "run": sizes.FONT_RUN,
                 "m": sizes.FONT_M, "s": sizes.FONT_S},
        "freqSamples": list(sizes.TOP_FREQ_SAMPLES),
    }


# 画面で数える台本（Qt の qt_layout_check.problems と同じ決まり）。root は数える範囲（CSS の選び方）、cfg は layout_config()
CHECK_JS = r"""
window.__gsLayout = function (rootSel, where, cfg) {
  const H = cfg.H;
  const ROW_H = cfg.rowH;
  const out = [];
  const roots = [...document.querySelectorAll(rootSel)];
  if (!roots.length) return [where + ": " + rootSel + " がありません"];
  // 主画面を数えるときは、上に重ねた窓（.modal-back）の中を数えない
  const inScope = (el) => roots.some((r) => r.contains(el)) && !(rootSel === ".app" && el.closest(".modal-back"));
  const shown = (el) => {
    if (!el.getClientRects().length) return false;
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0 && getComputedStyle(el).visibility !== "hidden";
  };
  const tier = (el) => { const t = el.closest("[data-tier]"); return t ? t.dataset.tier : "M"; };
  // 部品が載っている場所（一番近いスクロールする枠か窓）。行のそろいは同じ場所の中だけで比べる
  const area = (el) => {
    for (let p = el.parentElement; p; p = p.parentElement) {
      if (p.classList.contains("modal")) return p;
      const cs = getComputedStyle(p);
      if (/auto|scroll/.test(cs.overflowY) || /auto|scroll/.test(cs.overflowX)) return p;
    }
    return document.body;
  };
  const textOf = (el) => {
    if (el.tagName === "SELECT") return (el.selectedOptions[0] || {}).text || "";
    if (el.tagName === "INPUT" || el.tagName === "TEXTAREA") return el.value || el.placeholder || "";
    return (el.textContent || "").replace(/\s+/g, " ").trim();
  };
  const name = (el) => el.tagName.toLowerCase() + "「" + textOf(el).slice(0, 20) + "」";
  const isCtl = (el) => {
    if (el.matches("button, select, textarea")) return true;
    return el.matches("input") && !["checkbox", "radio", "range", "hidden", "file"].includes(el.type);
  };
  const ctx = document.createElement("canvas").getContext("2d");
  const textW = (el, t) => {
    const cs = getComputedStyle(el);
    ctx.font = `${cs.fontStyle} ${cs.fontWeight} ${cs.fontSize} ${cs.fontFamily}`;
    return ctx.measureText(t).width;
  };
  const ctls = [...document.querySelectorAll("button, select, textarea, input")].filter((el) => isCtl(el) && inScope(el) && shown(el));
  const recs = ctls.map((el) => ({ el, r: el.getBoundingClientRect(), t: tier(el), a: area(el) }));
  // 部品の自然な幅（同じ親の中に、幅を決めない写しを置いて測る。段や置き場所の決まりは同じに当たる）
  const natural = (el) => {
    const c = el.cloneNode(true);
    c.style.cssText = "position:absolute;visibility:hidden;width:auto;max-width:none;flex:none;left:0;top:0;";
    el.parentNode.insertBefore(c, el);
    const w = c.getBoundingClientRect().width;
    c.remove();
    return w;
  };
  const rootCs = getComputedStyle(document.documentElement);
  const KIND_W = ["--w-int", "--w-value", "--w-name", "--w-color"].map((v) => parseFloat(rootCs.getPropertyValue(v)));
  // 打てるドロップダウン（欄＋右の ▼）は ▼ まで入れた箱で測る
  const box = (el) => (el.nextElementSibling && el.nextElementSibling.classList.contains("field-choices") ? el.parentElement : el);
  // 段ごとの文字の大きさ（S-01・J §4.11）。上段のメニューの ≡ は文字ではなく印なので欄と同じ大きさ
  const F = cfg.font;
  const fontWant = (el, t) => {
    if (t === "S") return F.s;
    if (t === "M") return F.m;
    if (el.tagName !== "BUTTON" || el.classList.contains("menu-btn")) return F.lValue;
    return el.classList.contains("run-btn") ? F.run : F.lButton;
  };
  for (const { el, r, t } of recs) {
    const h = H[t];
    if (Math.abs(r.height - h) > 0.5) out.push(`${where}: ${name(el)} の高さ ${r.height}px（段の値 ${h}px）`);
    const fs = parseFloat(getComputedStyle(el).fontSize);
    if (fs !== fontWant(el, t)) out.push(`${where}: ${name(el)} の文字 ${fs}px（段の値 ${fontWant(el, t)}px）`);
    if (el.tagName === "BUTTON" && (el.scrollWidth > el.clientWidth + 1 || el.scrollHeight > el.clientHeight + 1))
      out.push(`${where}: ${name(el)} の文字が切れている（中 ${el.clientWidth}×${el.clientHeight}px・文字 ${el.scrollWidth}×${el.scrollHeight}px）`);
    // ボタンは文字の幅（短い語は最小幅）。横に伸ばさない（S-03）。同じ幅にそろえる組（data-same-width: モードの
    // ボタン）は、組の中で一番広い文字の幅にそろえる
    if (el.tagName === "BUTTON") {
      const group = el.closest("[data-same-width]");
      const want = group ? Math.max(...[...group.querySelectorAll("button")].map(natural)) : natural(el);
      if (r.width > want + 1) out.push(`${where}: ${name(el)} の幅 ${r.width}px が文字の幅 ${want}px より広い（伸びている）`);
      if (group) {
        const first = group.querySelector("button").getBoundingClientRect().width;
        if (Math.abs(r.width - first) > 0.5) out.push(`${where}: 同じ幅の組の ${name(el)} の幅 ${r.width}px が組の幅 ${first}px と違う`);
      }
    }
    // 標準の段の入力欄の幅は、値の種類の幅のどれか（S-02）
    if (el.tagName === "INPUT" && el.type !== "color" && t === "M" && !el.classList.contains("w-grow")
        && !el.classList.contains("cell-edit") && !el.closest(".count-field")) {
      const bw = box(el).getBoundingClientRect().width;
      if (!KIND_W.some((k) => Math.abs(bw - k) <= 0.5))
        out.push(`${where}: ${name(el)} の幅 ${bw}px が値の種類の幅 ${KIND_W.join("・")}px のどれでもない`);
    }
    if (el.tagName === "INPUT" && el.type !== "color") {
      const t = el.value || el.placeholder;
      if (t) {
        const cs = getComputedStyle(el);
        const room = el.clientWidth - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight);
        const w = textW(el, t);
        if (w > room + 1) out.push(`${where}: ${name(el)} の文字 ${w.toFixed(0)}px が欄の中 ${room}px に入らない`);
      }
    }
    // ドロップダウンは一番長い選択肢の幅（欄の右の一覧の矢印だけの部品は除く）
    if (el.tagName === "SELECT" && !el.classList.contains("field-choices")) {
      const need = natural(el);
      if (r.width + 0.5 < need) out.push(`${where}: ${name(el)} の幅 ${r.width}px が一番長い選択肢 ${need}px より狭い`);
    }
  }
  // 行を重ねる並び（表と data-repeat の格子）では、同じ列の欄・ドロップダウンは同じ幅・同じ左端。打てるドロップダウンは
  // ▼ まで入れた箱で比べる（▼ だけの部品は数えない）。格子ではボタンも比べる（表のセルは複数のボタンを並べるので除く）
  for (const g of document.querySelectorAll(".tgrid:not([data-repeat]), .fn-grid:not([data-repeat])"))
    if (inScope(g)) out.push(`${where}: 行を重ねる並び（${g.className}）に data-repeat が無い`);
  const gridCol = (grid, item) => {
    const cs = getComputedStyle(grid);
    const tracks = cs.gridTemplateColumns.split(" ").map(parseFloat);
    const gap = parseFloat(cs.columnGap) || 0;
    let x = grid.getBoundingClientRect().left + parseFloat(cs.borderLeftWidth) + parseFloat(cs.paddingLeft);
    const left = item.getBoundingClientRect().left;
    for (let i = 0; i < tracks.length; i++) {
      if (left >= x - 1 && left < x + tracks[i] + gap - 1) return i;
      x += tracks[i] + gap;
    }
    return -1;
  };
  const cellCol = (td) => {
    let c = 0;
    for (const x of td.parentElement.cells) { if (x === td) return c; c += x.colSpan; }
    return -1;
  };
  const cols = new Map();
  for (const { el } of recs) {
    if (!el.matches("select, input, button") || el.classList.contains("field-choices")) continue;
    const grid = el.closest("[data-repeat]");
    const td = el.closest("td, th");
    if (el.tagName === "BUTTON" && (td || !grid)) continue;
    let key = null;
    if (td && (!grid || grid.contains(td))) key = [td.closest("table"), cellCol(td), el.tagName];
    else if (grid && getComputedStyle(grid).display === "grid") {
      let item = el;
      while (item.parentElement && item.parentElement !== grid) item = item.parentElement;
      key = [grid, gridCol(grid, item), el.tagName];
    }
    if (!key) continue;
    const k = key[0];
    if (!cols.has(k)) cols.set(k, new Map());
    const m = cols.get(k);
    const ck = td ? key[1] + ":" + key[2] : String(key[1]);
    if (!m.has(ck)) m.set(ck, []);
    m.get(ck).push(el);
  }
  for (const m of cols.values()) {
    for (const list of m.values()) {
      const rs = list.map((el) => box(el).getBoundingClientRect());
      for (let i = 1; i < list.length; i++) {
        if (Math.abs(rs[i].width - rs[0].width) > 1 || Math.abs(rs[i].left - rs[0].left) > 1)
          out.push(`${where}: 同じ列の ${name(list[0])}（左端 ${rs[0].left}・幅 ${rs[0].width}）と ${name(list[i])}（左端 ${rs[i].left}・幅 ${rs[i].width}）がそろっていない`);
      }
    }
  }
  // 上段の周波数の欄は伸ばさない（S-02）: 上段の字で一番長い値＋左右の余白と枠
  const pad = (el) => { const cs = getComputedStyle(el); return parseFloat(cs.paddingLeft) + parseFloat(cs.paddingRight) + parseFloat(cs.borderLeftWidth) + parseFloat(cs.borderRightWidth); };
  for (const el of document.querySelectorAll("[data-tier=L] .field.big .field-input")) {
    if (!inScope(el) || !shown(el)) continue;
    const want = Math.ceil(Math.max(...cfg.freqSamples.map((t) => textW(el, t)))) + pad(el) + 1;
    const w = el.getBoundingClientRect().width;
    if (Math.abs(w - want) > 1) out.push(`${where}: 上段の周波数の欄 ${name(el)} の幅 ${w}px（一番広い見本が入る幅 ${want}px）`);
  }
  // 上段の REF の欄（欄＋▼）: いまの単位の一番長い値＋左右の余白×2（ボタンの余白）＋▼＋枠（Qt の DbChoiceField._fit_width）
  for (const el of document.querySelectorAll("[data-tier=L] .field.ref .field-input")) {
    if (!inScope(el) || !shown(el) || !window.gsApp) continue;
    const rootCs2 = getComputedStyle(document.documentElement);
    const padX = parseFloat(rootCs2.getPropertyValue("--btn-pad-x"));
    const want = textW(el, `${GSUnits.levelText(-120)} ${gsApp.axisUnit}`) + 2 * padX + 22 + 2;
    const w = box(el).getBoundingClientRect().width;
    if (Math.abs(w - want) > 1.5) out.push(`${where}: 上段の REF の欄の幅 ${w}px（いまの単位の一番長い値が入る幅 ${want.toFixed(1)}px）`);
  }
  // トレースの表: 2行目の選択（平均の種類・N 回の後）は1行目（動作・回数）の真下で同じ幅
  const acts = [...document.querySelectorAll(".tgrid .tact")].filter((e) => inScope(e) && shown(e));
  const avgs = [...document.querySelectorAll(".tgrid .tavg")].filter((e) => inScope(e) && shown(e));
  const cnts = [...document.querySelectorAll(".tgrid .tcnt")].filter((e) => inScope(e) && shown(e));
  const afts = [...document.querySelectorAll(".tgrid .taft")].filter((e) => inScope(e) && shown(e));
  if (acts.length !== avgs.length || cnts.length !== afts.length || acts.length !== cnts.length)
    out.push(`${where}: トレースの表の1行目と2行目の数が合わない`);
  for (let i = 0; i < Math.min(acts.length, avgs.length, cnts.length, afts.length); i++) {
    for (const [a, b] of [[acts[i], avgs[i]], [cnts[i], afts[i]]]) {
      const ra = a.getBoundingClientRect(), rb = b.getBoundingClientRect();
      if (Math.abs(ra.left - rb.left) > 1 || Math.abs(ra.width - rb.width) > 1)
        out.push(`${where}: トレース ${i + 1} の2行目 ${name(b)}（左端 ${rb.left}・幅 ${rb.width}）が1行目（左端 ${ra.left}・幅 ${ra.width}）の真下でない`);
    }
  }
  // 同じ行（縦に重なる）の同じ段の部品は、上端と高さがそろう
  for (let i = 0; i < recs.length; i++) {
    for (let j = i + 1; j < recs.length; j++) {
      const a = recs[i], b = recs[j];
      if (a.t !== b.t || a.a !== b.a) continue;
      const overlap = a.r.top < b.r.bottom && b.r.top < a.r.bottom;
      if (overlap && (Math.abs(a.r.top - b.r.top) > 1 || Math.abs(a.r.height - b.r.height) > 1))
        out.push(`${where}: ${name(a.el)}（上端 ${a.r.top}・高さ ${a.r.height}）と ${name(b.el)}（上端 ${b.r.top}・高さ ${b.r.height}）が同じ行でずれている`);
    }
  }
  // 1行の文字（名前・状態）とチェックボックス・ラジオは、同じ行の部品と縦の真ん中がそろう
  const singles = [];
  for (const el of document.querySelectorAll("input[type=checkbox], input[type=radio]")) {
    if (!inScope(el) || !shown(el)) continue;
    const lab = el.closest("label");
    singles.push({ el: lab || el, check: !!lab });
  }
  const SKIP = "button, select, option, textarea, script, style, svg, .uplot, .hover-tip";
  // 文字の部品は、文字を持つ箱（行の中の文字の切れ（display: inline）は、それを含む箱）。Qt の QLabel は1つの箱の中で
  // 折り返すので、折り返した文の切れの1つずつを1行の文字として数えない
  const textBox = (el) => {
    let b = el;
    while (b.parentElement && getComputedStyle(b).display === "inline" && !b.matches("label")) b = b.parentElement;
    return b;
  };
  const seenBox = new Set();
  for (const el of roots.flatMap((r) => [r, ...r.querySelectorAll("*")])) {
    if (!inScope(el) || el.closest(SKIP) || !shown(el)) continue;
    if (el.tagName === "LABEL" && el.querySelector("input[type=checkbox], input[type=radio]")) continue;
    const own = [...el.childNodes].some((n) => n.nodeType === 3 && n.textContent.trim());
    if (!own) continue;
    const b = textBox(el);
    if (seenBox.has(b) || (b.tagName === "LABEL" && b.querySelector("input[type=checkbox], input[type=radio]"))) continue;
    seenBox.add(b);
    singles.push({ el: b, check: false });
  }
  for (const s of singles) {
    const rs = s.el.getBoundingClientRect();
    if (s.check && Math.abs(rs.height - H.M) > 0.5)
      out.push(`${where}: チェック「${textOf(s.el).slice(0, 16)}」の高さ ${rs.height}px（段の値 ${H.M}px）`);
    if (rs.height > ROW_H) continue;
    const sa = area(s.el);
    for (const w of recs) {
      if (w.a !== sa || !(rs.top < w.r.bottom && w.r.top < rs.bottom)) continue;
      const d = (rs.top + rs.height / 2) - (w.r.top + w.r.height / 2);
      if (Math.abs(d) > 2) {
        out.push(`${where}: 「${textOf(s.el).slice(0, 16)}」の縦の真ん中が ${name(w.el)} と ${d.toFixed(1)}px ずれている`);
        break;
      }
    }
  }
  return out;
};
true;
"""


def edge_available() -> bool:
    return EDGE.exists()


class EdgePage:
    """ヘッドレスの Edge の1枚のページ（DevTools の口で動かす）。"""

    def __init__(self) -> None:
        from websockets.sync.client import connect

        self.profile = tempfile.mkdtemp(prefix="gs_edge_")
        self.proc = subprocess.Popen(
            [str(EDGE), "--headless=new", "--disable-gpu", "--remote-debugging-port=0", f"--user-data-dir={self.profile}",
             "--no-first-run", "--no-default-browser-check", "about:blank"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        port_file = Path(self.profile) / "DevToolsActivePort"
        end = time.monotonic() + 30
        page = None
        while page is None:
            if time.monotonic() > end:
                self.close()
                raise RuntimeError("Edge の DevTools の口が開かない")
            try:
                port = int(port_file.read_text().split()[0])
                tabs = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{port}/json", timeout=5).read())
                page = next(t for t in tabs if t["type"] == "page")
            except (OSError, ValueError, StopIteration, IndexError):
                time.sleep(0.1)
        self._ws_cm = connect(page["webSocketDebuggerUrl"], max_size=None, open_timeout=10)
        self.ws = self._ws_cm.__enter__()
        self._id = 0
        self.errors: list[str] = []
        self.send("Runtime.enable")
        self.send("Page.enable")
        self.send("Network.setCacheDisabled", cacheDisabled=True)

    def send(self, method: str, **params):
        self._id += 1
        mid = self._id
        self.ws.send(json.dumps({"id": mid, "method": method, "params": params}))
        while True:
            m = json.loads(self.ws.recv(timeout=60))
            if m.get("method") == "Runtime.exceptionThrown":
                self.errors.append(json.dumps(m["params"]["exceptionDetails"], ensure_ascii=False)[:500])
            if m.get("id") == mid:
                if "error" in m:
                    raise RuntimeError(f"{method}: {m['error']}")
                return m.get("result")

    def ev(self, js: str):
        r = self.send("Runtime.evaluate", expression=js, awaitPromise=True, returnByValue=True)
        if r.get("exceptionDetails"):
            raise RuntimeError(json.dumps(r["exceptionDetails"], ensure_ascii=False)[:800])
        return r.get("result", {}).get("value")

    def size(self, w: int, h: int) -> None:
        self.send("Emulation.setDeviceMetricsOverride", width=w, height=h, deviceScaleFactor=1, mobile=False)

    def goto(self, url: str) -> None:
        self.send("Page.navigate", url=url)

    def wait(self, js: str, what: str, timeout: float = 30) -> None:
        end = time.monotonic() + timeout
        while not self.ev(js):
            if time.monotonic() > end:
                raise AssertionError(f"{what} にならない")
            time.sleep(0.1)

    def settle(self) -> None:
        """Vue が描き直し、2回描画が回るまで待つ。"""
        self.ev("new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(() => setTimeout(r, 50))))")

    def shot(self, path: Path) -> None:
        s = self.send("Page.captureScreenshot", format="png")
        Path(path).write_bytes(base64.b64decode(s["data"]))

    def problems(self, root: str, where: str) -> list[str]:
        self.ev(CHECK_JS)
        return self.ev(f"window.__gsLayout({json.dumps(root)}, {json.dumps(where)}, "
                       f"{json.dumps(layout_config(), ensure_ascii=False)})")

    def close(self) -> None:
        # ブラウザを閉じてから（ページの接続を閉じさせる）、残っていれば止める
        try:
            self._id += 1
            self.ws.send(json.dumps({"id": self._id, "method": "Browser.close", "params": {}}))
            self.proc.wait(10)
        except Exception:  # noqa: BLE001 - 閉じるときの失敗は試験の結果に関係しない
            pass
        try:
            self._ws_cm.__exit__(None, None, None)
        except Exception:  # noqa: BLE001
            pass
        if self.proc.poll() is None:
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(self.proc.pid)], capture_output=True)
            try:
                self.proc.wait(10)
            except subprocess.TimeoutExpired:
                pass
        shutil.rmtree(self.profile, ignore_errors=True)


class App:
    """シミュレータに繋いで1回掃引し、マーカー（子の行・XY カーソル付き）とプリセットを置いた Web の口。

    トレース 2 は平均にして、トレースの表の行ごとに選択肢が違う形（平均の種類・N 回の後）にする（同じ列の幅を確かめる）。
    LAN にも出して、接続のしかたの窓と設定の「ブラウザから使う」の注意の行も出す。
    rtl=True は偽物の DLL の RTL-SDR に繋ぐ（受信・聞くの欄が出る: Qt の LayoutRtlTest と同じ）。
    """

    def __init__(self, lan: bool = True, rtl: bool = False) -> None:
        from pygigast.api.local import LocalApi, _RtlSdrBackend, _SimulatorBackend
        from pygigast.api.web import server as web
        from pygigast.core.devices.base import load_profile
        from pygigast.core.instrument import GIGAST_PROFILE_PATH, Instrument

        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        profile = load_profile(GIGAST_PROFILE_PATH)
        sim = _SimulatorBackend(profile)
        sim.speed = 0.05
        backends = {"simulator": sim}
        if rtl:
            from tests.test_instrument_rtlsdr import FakeLib

            lib = FakeLib()
            lib.tones = [(84_700_000, 0.05)]
            backends["rtlsdr"] = _RtlSdrBackend(lib_factory=lambda: lib)
        self.api = LocalApi(Instrument(backends, profile=profile, data_root=root / "data", cal_root=root / "cal"))
        if rtl:
            self.call("connect", backend="rtlsdr", port="rtlsdr:0")
            self.call("set_sweep", start_hz=84_000_000, stop_hz=85_500_000)
        else:
            self.call("connect", backend="simulator", serial="SIM00001")
            self.call("set_sweep", start_hz=400_000_000, stop_hz=600_000_000)
        self.call("run", mode="single")
        end = time.monotonic() + 30
        while True:
            st = self.call("get_state")
            if st["run"]["state"] == "stopped" and st["run"]["sweep_count"] > 0:
                break
            if time.monotonic() > end:
                raise RuntimeError("掃引が終わらない")
            time.sleep(0.05)
        m = self.call("marker_add", kind="M", trace=1, freq_hz=84_700_000 if rtl else 500_000_000)
        self.call("marker_offset_add", id=m["id"], type="freq", sign="+", delta=1_000_000)
        self.call("marker_function", id=m["id"], bandwidth_db=[3.0], threshold={"level": -20.0, "side": "above"})
        self.call("set_xy", x1_hz=84_500_000 if rtl else 450_000_000, y1=-50.0)
        self.call("set_trace", number=2, action="average", count=16)
        self.call("preset_save", name="FM")
        self.srv = _Serve(web.create_app(self.api, lan=lan))

    def call(self, command: str, **kw):
        return self.api.call(command, **kw).result(30)

    def close(self) -> None:
        self.srv.stop()
        self.api.close()
        self.tmp.cleanup()


class _Serve:
    """空いているポートで Web の口を立てる（tests/test_web_server.py の _Server と同じ。LAN の設定も渡せる）。"""

    def __init__(self, asgi) -> None:
        import threading

        import uvicorn

        cfg = uvicorn.Config(asgi, host="127.0.0.1", port=0, log_level="warning", lifespan="on")
        self.server = uvicorn.Server(cfg)
        self.thread = threading.Thread(target=self.server.run, daemon=True)
        self.thread.start()
        end = time.monotonic() + 10
        while not self.server.started:
            if time.monotonic() > end:
                raise RuntimeError("サーバーが立たない")
            time.sleep(0.02)
        self.port = self.server.servers[0].sockets[0].getsockname()[1]
        self.base = f"http://127.0.0.1:{self.port}"

    def stop(self) -> None:
        self.server.should_exit = True
        self.thread.join(10)


def open_app(page: EdgePage, app: App, w: int, h: int) -> None:
    """画面を開き、表とトレースとマーカーが届いて、マーカーを選んだところまで待つ。"""
    page.size(w, h)
    page.goto(app.srv.base + "/")
    page.wait("document.readyState === 'complete'", "読み込み")
    if not page.ev("typeof Vue !== 'undefined' && typeof uPlot !== 'undefined'"):
        raise unittest.SkipTest("Vue・uPlot を CDN から読めない（ネットワークが無い）")
    page.wait("!!(window.gsApp && gsApp.textsReady && gsApp.st && gsApp.$traces && gsApp.$traces.size"
              " && gsApp.markers.markers.length)", "状態・トレース・マーカーが届いた画面")
    # 列の幅と上段の周波数の欄の幅を測り終えるまで（app.js の fitColumns）
    page.wait("getComputedStyle(document.documentElement).getPropertyValue('--top-freq-w') !== ''", "欄の幅を測る")
    page.ev("gsApp.selectedMarker = 'M1'; 1")
    page.settle()


def views(page: EdgePage, tabs_only: bool = False):
    """数える画面を順に開き、(どこ, 数える範囲) を返す。tabs_only は主画面の右のタブだけ。"""
    for tab in TABS:
        page.ev(f"gsApp.closeSettings(); gsApp.closePresets(); gsApp.tab = '{tab}'; 1")
        page.settle()
        yield f"主画面 {tab}", ".app"
    if tabs_only:
        return
    # 探すもの「谷」（G §3.8）: マーカー表の上のボタンの名前が変わる（場所は同じ）。マーカーのタブのピークの一覧も
    page.ev("gsApp.setPolarity('dip'); gsApp.tab = 'marker'; 1")
    page.settle()
    yield "主画面 marker（谷）", ".app"
    page.ev("gsApp.setPolarity('peak'); 1")
    # マーカー表の行の「操作」（Qt の行のドロップダウンと同じ項目。開いた一覧）
    page.ev("(() => { const b = document.querySelector('.mtable .row-ops'); b.click(); return 1; })()")
    page.wait("!!(gsApp.ctx && document.querySelector('.ctxmenu button'))", "行の「操作」")
    page.settle()
    yield "マーカー表の行の操作", ".ctxmenu"
    page.ev("gsApp.ctx = null; 1")
    for pg in page.ev("Object.keys(GSTexts.dict('SETTINGS_PAGES'))"):
        page.ev(f"gsApp.openSettings('{pg}'); 1")
        page.settle()
        yield f"設定 {pg}", ".modal.setwin"
    page.ev("gsApp.closeSettings(); gsApp.openPresets(); 1")
    page.wait("!!(gsApp.presetsWin && gsApp.presetsWin.ready)", "プリセットの一覧")
    page.settle()
    yield "プリセット", ".modal.presetwin"
    page.ev("gsApp.closePresets(); gsApp.openMenu(0, false); 1")
    page.wait("!!document.querySelector('.menubar .menu .menu-item')", "メニューバーのファイル")
    page.settle()
    yield "メニューバー（ファイル）", ".menubar"
    page.ev("gsApp.menuOpen = false; gsApp.openDevices(); 1")
    page.wait("!!(gsApp.devWin && (gsApp.devWin.rows.length || gsApp.devWin.empty))", "機器の一覧")
    page.settle()
    yield "機器の一覧", ".modal.devwin"
    page.ev("gsApp.closeDevices(); gsApp.exportDialog(); 1")
    page.settle()
    yield "CSV に書き出す", ".modal.input-win"
    page.ev("gsApp.modal = null; gsApp.saveDialog(); 1")
    page.settle()
    yield "保存", ".modal.input-win"
    page.ev("gsApp.modal = null; gsApp.showConnect(); 1")
    page.wait("!!gsApp.connectInfo", "ブラウザからの接続のしかた")
    page.settle()
    yield "ブラウザからの接続のしかた", ".modal.connect"
    page.ev("gsApp.closeConnect(); 1")


@unittest.skipUnless(edge_available(), "Microsoft Edge が無い")
class WebLayoutTest(unittest.TestCase):
    """主画面の全部のタブ・設定の全部のページ・プリセットを、決めた解像度で開いて数える。"""

    @classmethod
    def setUpClass(cls):
        cls.app = App()
        try:
            cls.page = EdgePage()
        except Exception:
            cls.app.close()
            raise

    @classmethod
    def tearDownClass(cls):
        cls.page.close()
        cls.app.close()

    def check_size(self, w: int, h: int) -> None:
        open_app(self.page, self.app, w, h)
        found: list[str] = []
        seen: list[str] = []
        for where, root in views(self.page):
            seen.append(where)
            found += self.page.problems(root, f"{w}×{h} {where}")
        # 数えた画面が揃っていること（開けずに数えなければ何も守らない）
        self.assertEqual(len(seen), len(TABS) + 2 + 8 + 6, seen)
        self.assertEqual(self.page.errors, [])
        self.assertEqual(sorted(set(found)), [])

    def test_1280x720(self):
        self.check_size(*SIZES[0])

    def test_1920x1080(self):
        self.check_size(*SIZES[1])

    def test_top_freq_samples_fit(self):
        """見本の値を上段の周波数の欄に入れても、文字が欄からはみ出さない（scrollWidth <= clientWidth）。"""
        open_app(self.page, self.app, *SIZES[0])
        js = """((samples) => {
          const out = [];
          for (const el of document.querySelectorAll('[data-tier=L] .field.big .field-input')) {
            const keep = el.value;
            for (const t of samples) {
              el.value = t;
              if (el.scrollWidth > el.clientWidth) out.push(`${t}: ${el.scrollWidth} > ${el.clientWidth}`);
            }
            el.value = keep;
          }
          return [document.querySelectorAll('[data-tier=L] .field.big .field-input').length, out];
        })(%s)""" % json.dumps(layout_config()["freqSamples"], ensure_ascii=False)
        n, over = self.page.ev(js)
        self.assertEqual(n, 4)
        self.assertEqual(over, [])

    def test_top_bar_one_row(self):
        """上段が1行に収まる幅では1行（RBW が Center と同じ行）: SA の 1600×900 と 1920×1080、TG（直結を 0dB にすると
        いまの基準の文を出す）の 1920×1080。どれも数えた問題は 0。上段は Qt と同じ部品だけ（ユーザーの決定 2026-10-03:
        「Qt と違う UI を採用している時点でおかしい」）。"""
        same_row = ("(() => { const c = document.querySelector('[data-tier=L] .field.big .field-input').getBoundingClientRect();"
                    " const r = document.querySelector('[data-tier=L] .field-select').getBoundingClientRect();"
                    " return [c.top, r.top]; })()")
        found: list[str] = []
        try:
            for mode, (w, h) in (("sa", (1600, 900)), ("tg", (1920, 1080)), ("sa", (1920, 1080))):
                self.app.call("set_mode", mode=mode)
                open_app(self.page, self.app, w, h)
                if mode == "tg":
                    self.page.wait("!!(gsApp.refQuick.visible && document.querySelector('.refquick-state'))", "直結を 0dB にする")
                c_top, r_top = self.page.ev(same_row)
                self.assertAlmostEqual(c_top, r_top, delta=0.5, msg=f"{mode} {w}×{h}: 上段が2行になった")
                self.page.ev("gsApp.tab = 'trace'; 1")
                self.page.settle()
                found += self.page.problems(".app", f"{w}×{h} {mode} 上段")
        finally:
            self.app.call("set_mode", mode="sa")
        self.assertEqual(sorted(set(found)), [])

    def test_menu_bar_and_top_bar_same_as_qt(self):
        """メニューバーの題・並び・アクセスキー・項目は Qt と同じ表から同じ形（ui_qt/menu_table.table と同じ作り方）。
        広い画面の上段には Qt に無い部品（「≡」・保存・PNG）を置かない（狭い画面だけ「≡」）。"""
        from pygigast.ui_qt import texts as T

        open_app(self.page, self.app, *SIZES[0])
        titles = self.page.ev("[...document.querySelectorAll('.menubar .menubar-title')].filter((e) => e.getClientRects().length)"
                              ".map((e) => e.textContent.trim())")
        self.assertEqual(titles, [f"{T.MENU_TITLES[sec]}({T.MENU_MNEMONICS[sec]})" for sec, _codes in T.MENU_LAYOUT])
        web = self.page.ev("gsApp.menu.map((s) => s.items.filter((i) => i).map((i) => i.label))")
        # Qt の menu_table.table と同じ項目。Web が違えてよいのは次の2つだけ（理由つき）:
        # - 終了: ブラウザの窓を閉じる（app_menu.js の WEB_SKIP）
        # - 表示の「…を別ウィンドウで開く」: Qt はドックを掴んで外す。Web は部分ごとの項目（J §4.3.2）
        detach = self.page.ev("gsApp.menu.find((s) => s.code === 'view').items.filter((i) => i && i.code.startsWith('detach_'))"
                              ".map((i) => i.label)")
        self.assertTrue(detach)
        for (sec, codes), got in zip(T.MENU_LAYOUT, web):
            if sec == "mode":
                want = [f"{label}（{name}）" for _c, label, name, _why in T.MODES]
            else:
                want = [T.MENU_ITEMS[c] for c in codes if c is not None and c != "quit"]
            if sec == "view":
                want += detach
            self.assertEqual(got, want, sec)
        self.assertEqual(len(web), len(T.MENU_LAYOUT))
        # 広い画面の上段: 「≡」は出さず、保存・PNG のボタンは置かない（ファイルのメニューとキーで使う: Qt と同じ）
        top = self.page.ev("[...document.querySelectorAll('.topbar button')].filter((e) => e.getClientRects().length)"
                           ".map((e) => e.textContent.trim())")
        for word in ("≡", "PNG", T.LABELS["save"]):
            self.assertNotIn(word, top)
        # 狭い画面（スマートフォン）ではメニューバーの代わりに「≡」
        self.page.size(400, 860)
        self.page.settle()
        self.assertTrue(self.page.ev("!!document.querySelector('.menu-btn').getClientRects().length"))
        self.assertFalse(self.page.ev("!!document.querySelector('.menubar').getClientRects().length"))

    def test_menu_keys(self):
        """Alt+アクセスキーで題を開き、←→ で隣の題、Esc で閉じる（Qt のメニューバーと同じ。ブラウザが先に取るキーは除く）。"""
        open_app(self.page, self.app, *SIZES[0])
        key = ("((k, code, alt) => { document.body.dispatchEvent(new KeyboardEvent('keydown', {key: k, code, altKey: alt,"
               " bubbles: true, cancelable: true})); return gsApp.menuOpen; })")
        self.assertEqual(self.page.ev(f"{key}('f', 'KeyF', true)"), 0)
        self.assertEqual(self.page.ev(f"{key}('ArrowRight', 'ArrowRight', false)"), 1)
        self.assertEqual(self.page.ev(f"{key}('ArrowLeft', 'ArrowLeft', false)"), 0)
        self.assertEqual(self.page.ev(f"{key}('h', 'KeyH', true)"), len(self.page.ev("gsApp.menu")) - 1)
        self.assertIs(self.page.ev(f"{key}('Escape', 'Escape', false)"), False)

    def test_checker_finds_a_broken_control(self):
        """検査そのものが働くこと（わざと崩して見つかるか: S-01〜S-03・C-01）。前提は本番と同じ画面と同じ台本。"""
        open_app(self.page, self.app, *SIZES[0])
        self.page.ev("gsApp.tab = 'trace'; 1")
        self.page.settle()
        self.assertEqual(self.page.problems(".app", "崩す前"), [])
        # 高さと縦の位置（クリア）・同じ列の幅（2行目の動作）・値の種類の幅（周波数オフセット）・伸びたボタン（2行目のクリア）・
        # 文字の大きさ（3行目のクリア）
        js = ("(() => { document.querySelectorAll('.tact')[1].style.width = '200px';"
              " const c = document.querySelector('.tclr'); c.style.height = '30px'; c.style.marginTop = '5px';"
              " document.querySelector('[data-field=freq_offset]').style.width = '150px';"
              " const c2 = document.querySelectorAll('.tclr')[1]; c2.style.width = '120px';"
              " document.querySelectorAll('.tclr')[2].style.fontSize = '12px'; return 1; })()")
        self.page.ev(js)
        found = self.page.problems(".app", "壊した画面")
        self.page.ev("(() => { for (const e of document.querySelectorAll('.tact, .tclr, [data-field=freq_offset]'))"
                     " e.removeAttribute('style'); return 1; })()")
        self.assertTrue(any("の高さ 30px" in p for p in found), found)
        self.assertTrue(any("同じ行でずれている" in p for p in found), found)
        self.assertTrue(any("同じ列の" in p and "そろっていない" in p for p in found), found)
        self.assertTrue(any("値の種類の幅" in p for p in found), found)
        self.assertTrue(any("伸びている" in p for p in found), found)
        self.assertTrue(any("の文字 12px（段の値 13px）" in p for p in found), found)
        self.assertEqual(self.page.problems(".app", "戻した後"), [])


class TopFreqSampleTest(unittest.TestCase):
    def test_web_uses_the_same_samples_as_qt(self):
        """上段の周波数の欄の幅を決める見本は Qt と Web で同じ（同じ並び・同じ文字。同じ幅になる: U-09）。"""
        from pygigast.api.web import server as web

        js = (Path(web.UI_WEB_DIR) / "app.js").read_text(encoding="utf-8")
        samples = json.dumps(layout_config()["freqSamples"], ensure_ascii=False)
        self.assertIn(f"const TOP_FREQ_SAMPLES = {samples};", js)


@unittest.skipUnless(edge_available(), "Microsoft Edge が無い")
class WebLayoutRtlTest(unittest.TestCase):
    """RTL-SDR に繋いだときだけ出る欄（受信・聞く）を、決めた解像度で開いて数える（Qt の LayoutRtlTest と同じ）。"""

    @classmethod
    def setUpClass(cls):
        cls.app = App(lan=False, rtl=True)
        try:
            cls.page = EdgePage()
        except Exception:
            cls.app.close()
            raise

    @classmethod
    def tearDownClass(cls):
        cls.page.close()
        cls.app.close()

    def test_receiver_and_listen(self):
        found: list[str] = []
        for w, h in SIZES:
            open_app(self.page, self.app, w, h)
            # 受信の欄（トレース・掃引のタブ）と聞くの欄（マーカーのタブ）が出ていること（出ずに数えなければ何も守らない）
            self.assertTrue(self.page.ev("!!document.querySelector('[data-sec=rtlsdr]')"))
            self.assertTrue(self.page.ev("gsApp.listenView.shown"))
            for where, root in views(self.page, tabs_only=True):
                found += self.page.problems(root, f"{w}×{h} RTL-SDR {where}")
        self.assertEqual(self.page.errors, [])
        self.assertEqual(sorted(set(found)), [])


if __name__ == "__main__":
    unittest.main()
