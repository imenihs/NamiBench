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
- 行のある表が横にはみ出す（横のスクロールが出る）・列が見える範囲の外で切れる・セルの文字が切れる

段は置き場所で決まる（S-01）。画面は置き場所に data-tier（L・S。無ければ標準）を付けて段を示す。

機器はシミュレータ（受信・聞くの欄は偽物の DLL の RTL-SDR）。Vue と uPlot は CDN から読むので、読めないとき
（ネットワークが無い）と Edge が無いときは飛ばす。画面を撮って目で見るとき（C-01）も同じ道具（EdgePage・App・open_app・
views）を使う。
"""
from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path

# ヘッドレスの Edge の道具は tests/edge_page.py。ここではそれに崩れを数える台本を足す（ほかの Web の試験もここから読む）
from tests import edge_page
from tests.edge_page import EDGE, edge_available  # noqa: F401
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
      // 格子に並ぶ箱（箱を持たない包み display: contents の中の部品は、そのまま格子に並ぶ: トレースの表の trow）
      let item = el;
      while (item.parentElement && item.parentElement !== grid && getComputedStyle(item.parentElement).display !== "contents")
        item = item.parentElement;
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
  // トレースの表: 2行目の選択（平均の種類・N 回の後・線）は1行目（動作・回数・クリア）の真下で同じ幅（線とクリアは
  // 列の幅: 一番長い線の名前とクリアの広い方。設計F §9）
  const acts = [...document.querySelectorAll(".tgrid .tact")].filter((e) => inScope(e) && shown(e));
  const avgs = [...document.querySelectorAll(".tgrid .tavg")].filter((e) => inScope(e) && shown(e));
  const cnts = [...document.querySelectorAll(".tgrid .tcnt")].filter((e) => inScope(e) && shown(e));
  const afts = [...document.querySelectorAll(".tgrid .taft")].filter((e) => inScope(e) && shown(e));
  const clrs = [...document.querySelectorAll(".tgrid .tclr")].filter((e) => inScope(e) && shown(e));
  const lines = [...document.querySelectorAll(".tgrid .tline")].filter((e) => inScope(e) && shown(e));
  if (acts.length !== avgs.length || cnts.length !== afts.length || acts.length !== cnts.length
      || clrs.length !== lines.length || clrs.length !== acts.length)
    out.push(`${where}: トレースの表の1行目と2行目の数が合わない`);
  for (let i = 0; i < Math.min(acts.length, avgs.length, cnts.length, afts.length, clrs.length, lines.length); i++) {
    for (const [a, b] of [[acts[i], avgs[i]], [cnts[i], afts[i]], [clrs[i], lines[i]]]) {
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
  // 行のある表: 横にはみ出さない（横のスクロールを出さない）・列が見える範囲の外で切れない・セルの文字が切れない
  // （マーカー表が 1280×720 で横にはみ出し、右端の列「操作」が見切れたのを数えていなかった: 2026-10-09）
  for (const t of document.querySelectorAll("table")) {
    if (!inScope(t) || !shown(t) || !t.querySelector("tbody tr")) continue;
    let box = null;  // 表を切り取る一番近い枠（スクロールする枠・はみ出しを隠す枠）
    for (let p = t.parentElement; p && !box; p = p.parentElement)
      if (/auto|scroll|hidden/.test(getComputedStyle(p).overflowX)) box = p;
    const label = `表（${t.className || "table"}）`;
    if (box && box.scrollWidth > box.clientWidth + 1)
      out.push(`${where}: ${label}が横にはみ出している（中身 ${box.scrollWidth}px・見える幅 ${box.clientWidth}px）`);
    const br = box ? box.getBoundingClientRect() : { left: 0 };
    const lo = box ? br.left + box.clientLeft : 0;
    const hi = box ? lo + box.clientWidth : document.documentElement.clientWidth;
    const heads = t.tHead ? [...t.tHead.rows[0].cells] : [...(t.querySelector("tbody tr") || { cells: [] }).cells];
    heads.forEach((c, i) => {
      const r = c.getBoundingClientRect();
      if (r.width > 0 && (r.left < lo - 1 || r.right > hi + 1))
        out.push(`${where}: ${label}の列「${textOf(c) || i}」が見える範囲（${lo.toFixed(0)}〜${hi.toFixed(0)}px）の外で切れている`);
    });
    for (const c of t.querySelectorAll("td, th")) {
      if (shown(c) && (c.scrollWidth > c.clientWidth + 1 || c.scrollHeight > c.clientHeight + 1))
        out.push(`${where}: ${label}のセル「${textOf(c).slice(0, 16)}」の文字が切れている（中 ${c.clientWidth}×${c.clientHeight}px・文字 ${c.scrollWidth}×${c.scrollHeight}px）`);
    }
  }
  return out;
};
true;
"""


class EdgePage(edge_page.EdgePage):
    """ヘッドレスの Edge の1枚のページ（tests/edge_page.py）に、崩れを数える台本（CHECK_JS）を足したもの。"""

    def problems(self, root: str, where: str) -> list[str]:
        self.ev(CHECK_JS)
        return self.ev(f"window.__gsLayout({json.dumps(root)}, {json.dumps(where)}, "
                       f"{json.dumps(layout_config(), ensure_ascii=False)})")


class App:
    """シミュレータに繋いで1回掃引し、マーカー（子の行・XY カーソル付き）とプリセットを置いた Web の口。

    トレース 2 は平均にして、トレースの表の行ごとに選択肢が違う形（平均の種類・N 回の後）にする（同じ列の幅を確かめる）。
    LAN にも出して、接続のしかたの窓と設定の「ブラウザから使う」の注意の行も出す。
    rtl=True は偽物の DLL の RTL-SDR に繋ぐ（受信・聞くの欄が出る: Qt の LayoutRtlTest と同じ）。
    """

    def __init__(self, lan: bool = True, rtl: bool = False, zero_span: bool = False, pluto: bool = False) -> None:
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
        if pluto:
            # PlutoSDR（libiio の偽物）: 受信の欄（トレース・掃引のタブ）と送信の欄（信号源のタブ）が出る
            from pygigast.api.local import _PlutoSdrBackend
            from pygigast.core.devices.plutosdr.fake import FakeIioLib

            plib = FakeIioLib()
            backends["plutosdr"] = _PlutoSdrBackend(lib_factory=lambda: plib)
        self.api = LocalApi(Instrument(backends, profile=profile, data_root=root / "data", cal_root=root / "cal"))
        if rtl:
            self.call("connect", backend="rtlsdr", port="rtlsdr:0")
            self.call("set_sweep", start_hz=84_000_000, stop_hz=85_500_000)
        elif pluto:
            self.call("connect", backend="plutosdr", port="ip:192.168.2.1")
            self.call("set_sweep", start_hz=84_000_000, stop_hz=85_500_000)
            # 設定の利得がいまの範囲で使えない（使う値の行が出る）形にする: 2.4GHz 帯は 71dB まで
            self.call("plutosdr_set", gain_db=73.0)
            self.call("set_sweep", start_hz=2_400_000_000, stop_hz=2_402_000_000)
            self.call("set_mode", mode="sg", sg_hz=2_401_000_000)
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
        mk = 84_700_000 if rtl else (2_401_000_000 if pluto else 500_000_000)
        m = self.call("marker_add", kind="M", trace=1, freq_hz=mk)
        self.call("marker_offset_add", id=m["id"], type="freq", sign="+", delta=1_000_000)
        self.call("marker_function", id=m["id"], bandwidth_db=[3.0], threshold={"level": -20.0, "side": "above"})
        self.call("set_xy", x1_hz=84_500_000 if rtl else (2_400_500_000 if pluto else 450_000_000), y1=-50.0)
        self.call("set_trace", number=2, action="average", count=16)
        self.call("preset_save", name="FM")
        # 保存した配色（J §4.4.1）: いまの配色と、読みにくい組のある取り込んだ配色（一覧の行の知らせも数える）
        pal = self.call("palette_save", name="夜の観測")
        d = json.loads(self.call("palette_export", id=pal["id"])["text"])
        d["content"]["colors"]["text_dim"] = d["content"]["colors"]["panel_bg"]
        d["name"] = "読みにくい配色"
        self.call("palette_import", text=json.dumps(d))
        if zero_span:
            # ゼロスパン（設計N §1）: 流す表示に時間のマーカー（ピーク）と X1・X2 を置く
            self.call("set_view", zero_span_s=10)
            self.call("set_sweep", center_hz=100_000_000, span_hz=0)
            n0 = self.call("get_state")["run"]["sweep_count"]
            self.call("run", mode="single")
            end = time.monotonic() + 30
            while self.call("get_state")["run"]["sweep_count"] <= n0 or self.call("get_state")["run"]["state"] != "stopped":
                if time.monotonic() > end:
                    raise RuntimeError("掃引が終わらない")
                time.sleep(0.05)
            self.call("marker_add", kind="M", trace=1)
            self.call("set_xy", x1_hz=-8_000_000, x2_hz=-1_000_000)
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


def display_views(page: EdgePage, app: "App"):
    """設計P の部分（ウォーターフォール・密度表示・再生の帯・表示のタブのブックマークの一覧）を開いて (どこ, 範囲) を返す。"""
    page.ev("gsApp.closeSettings(); gsApp.closePresets(); gsApp.tab = 'view'; 1")
    app.call("bookmark_add", name="ビーコン", hz=500_000_000)
    app.call("bookmark_add", name="FM", hz=450_000_000)
    app.call("record_start")
    app.call("set_view", layout="waterfall")
    for _ in range(2):
        n0 = app.call("get_state")["run"]["sweep_count"]
        app.call("run", mode="single")
        end = time.monotonic() + 30
        while app.call("get_state")["run"]["sweep_count"] <= n0 or app.call("get_state")["run"]["state"] != "stopped":
            if time.monotonic() > end:
                raise RuntimeError("掃引が終わらない")
            time.sleep(0.05)
    page.wait("gsApp.layoutCode === 'waterfall' && !!gsApp.$wf && gsApp.$wf.shown.length >= 2", "ウォーターフォールの行")
    page.settle()
    yield "主画面 ＋ウォーターフォール", ".app"
    app.call("set_view", layout="density")
    page.wait("gsApp.layoutCode === 'density' && !!(gsApp.$dens && gsApp.$dens.d)", "密度の絵")
    page.settle()
    yield "主画面 ＋密度", ".app"
    app.call("record_stop")
    h5 = Path(app.tmp.name) / "layout_rec.h5"
    app.call("record_finish", path=str(h5))
    end = time.monotonic() + 30
    while not h5.exists():
        if time.monotonic() > end:
            raise RuntimeError("記録をまとめられない")
        time.sleep(0.05)
    app.call("open", paths=[str(h5)])
    page.wait("gsApp.pbView.visible && !!document.querySelector('.pbbar')", "再生の帯")
    page.settle()
    yield "主画面 再生", ".app"
    app.call("playback_close")
    app.call("set_view", layout="spectrum")
    page.wait("!gsApp.pbView.visible && gsApp.layoutCode === 'spectrum'", "ライブに戻る")
    page.settle()


def cal_views(page: EdgePage, app: "App"):
    """校正の画面（設計K）の手順を開く: 準備・測る（周波数特性・RBW・NF の増幅度）・確かめる（周波数特性・旧校正ファイル）。
    測った点と結果がある形で数える（Qt の LayoutTest.cal_pages と同じ手順）。"""
    from tests.test_level import build_legacy_bytes

    dev = app.api._inst._device

    def wait_idle():
        end = time.monotonic() + 30
        while app.call("get_state")["tasks"]:
            if time.monotonic() > end:
                raise AssertionError("校正の測定が終わらない")
            time.sleep(0.05)

    page.ev("gsApp.openCal('freq'); 1")
    page.wait("!!document.querySelector('.calwin')", "校正の画面")
    page.settle()
    yield "校正 準備", ".modal.calwin"
    dev.config.signals = [(10_000_000, -10.0)]
    app.call("cal_measure")
    wait_idle()
    page.ev("gsApp.calWin.page = 1; 1")
    page.settle()
    yield "校正 測る（周波数特性）", ".modal.calwin"
    page.ev("gsApp.calWin.page = 2; 1")
    page.settle()
    yield "校正 確かめる（周波数特性）", ".modal.calwin"
    app.call("cal_set_kind", kind="rbw", discard=True)
    app.call("cal_measure")
    wait_idle()
    page.ev("gsApp.calWin.page = 1; 1")
    page.settle()
    yield "校正 測る（RBW）", ".modal.calwin"
    app.call("cal_set_kind", kind="nf", discard=True)
    page.ev("gsApp.calWin.page = 1; 1")
    page.settle()
    yield "校正 測る（NF の増幅度）", ".modal.calwin"
    app.call("cal_set_kind", kind="legacy", discard=True)
    p = Path(app.tmp.name) / "gigast.txt"
    p.write_bytes(build_legacy_bytes())
    app.call("cal_legacy_load", path=str(p))
    page.ev("gsApp.calWin.page = 2; 1")
    page.settle()
    yield "校正 確かめる（旧校正ファイル）", ".modal.calwin"
    app.call("cal_close")
    dev.config.signals = []
    page.wait("!gsApp.calWin", "校正の画面を閉じる")


# 校正の画面で数える手順の数（cal_views）
CAL_VIEWS = 6


def views(page: EdgePage, tabs_only: bool = False, app: "App" = None):
    """数える画面を順に開き、(どこ, 数える範囲) を返す。tabs_only は主画面の右のタブだけ。app を渡すと設計P の部分と校正の画面も。"""
    for tab in TABS:
        page.ev(f"gsApp.closeSettings(); gsApp.closePresets(); gsApp.tab = '{tab}'; 1")
        page.settle()
        yield f"主画面 {tab}", ".app"
    if tabs_only:
        return
    if app is not None:
        yield from display_views(page, app)
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
        if pg == "display":
            # 保存した配色の一覧（J §4.4.1）は開いてから読む。行（読みにくい組の知らせの行を含む）が出るまで待つ
            page.wait("gsApp.palStore.ready && gsApp.palStore.rows.length >= 2", "保存した配色の一覧")
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
    if app is not None:
        yield from cal_views(page, app)


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
        for where, root in views(self.page, app=self.app):
            seen.append(where)
            found += self.page.problems(root, f"{w}×{h} {where}")
        # 数えた画面が揃っていること（開けずに数えなければ何も守らない）。設計P の3つ（ウォーターフォール・密度・再生）を含む
        self.assertEqual(len(seen), len(TABS) + 3 + 2 + 9 + 6 + CAL_VIEWS, seen)  # 設定は 9 ページ（PlutoSDR を足した）
        self.assertEqual(self.page.errors, [])
        self.assertEqual(sorted(set(found)), [])

    def test_1280x720(self):
        self.check_size(*SIZES[0])

    def test_1920x1080(self):
        self.check_size(*SIZES[1])

    def test_rbw_warning_keeps_layout(self):
        """RBW の欄の注意（U-25: 欄を注意の色にして文はツールチップ）が出ても出なくても、主画面の全部のタブの崩れは
        0 件で、欄の大きさと場所・グラフの位置は変わらない（Qt の LayoutTest.test_rbw_warning_keeps_layout と同じ）。"""
        box = ("(() => { const a = document.querySelector('[data-tier=L] .field-select').getBoundingClientRect();"
               " const g = document.querySelector('.graph').getBoundingClientRect();"
               " return [a.left, a.top, a.width, a.height, g.top]; })()")
        self.app.call("set_sweep", start_hz=495_000_000, stop_hz=505_000_000, rbw_hz=180_000)
        self.sweep_once()
        try:
            for w, h in SIZES:
                open_app(self.page, self.app, w, h)
                found: list[str] = []
                boxes = []
                for rbw, warn in ((180_000, False), (15_000, True)):
                    self.app.call("set_sweep", rbw_hz=rbw)
                    self.page.wait(f"gsApp.rbwField.warn === {'true' if warn else 'false'}", f"RBW {rbw} の注意")
                    for where, root in views(self.page, tabs_only=True):
                        found += self.page.problems(root, f"{w}×{h} RBW {rbw} {where}")
                    self.page.ev("gsApp.tab = 'trace'; 1")
                    self.page.settle()
                    boxes.append(self.page.ev(box))
                self.assertEqual(sorted(set(found)), [])
                self.assertEqual(boxes[0], boxes[1], f"{w}×{h}")
                self.assertEqual(self.page.errors, [])
        finally:
            self.app.call("set_sweep", start_hz=400_000_000, stop_hz=600_000_000, rbw_hz=180_000)
            self.sweep_once()

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
        """上段が1行に収まる幅では1行（RBW が Center と同じ行）: SA の 1600×900 と 1920×1080、TG（基準を取ると
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
                    self.page.wait("!!(gsApp.refQuick.visible && document.querySelector('.refquick-state'))", "基準を取る")
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
        # Qt の menu_table.table と同じ項目。Web が違えてよいのは終了だけ（ブラウザの窓を閉じる: app_menu.js の WEB_SKIP）。
        # 表示の「…を別ウィンドウで開く」は Qt のメニュー「表示」の後ろに足す項目と同じ並び・語・コード（texts の DETACH_PARTS。
        # Qt の側は test_ui_qt_settings の DockLayoutTest が数える）
        detach = self.page.ev("gsApp.menu.find((s) => s.code === 'view').items.filter((i) => i && i.code.startsWith('detach_'))"
                              ".map((i) => [i.code, i.label])")
        self.assertEqual(detach, [[f"detach_{k}", T.w("detach_item", part=T.LABELS["part_" + k])] for k in T.DETACH_PARTS])
        detach = [label for _code, label in detach]
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

    def sweep_once(self) -> None:
        n0 = self.app.call("get_state")["run"]["sweep_count"]
        self.app.call("run", mode="single")
        end = time.monotonic() + 30
        while True:
            st = self.app.call("get_state")
            if st["run"]["state"] == "stopped" and st["run"]["sweep_count"] > n0:
                return
            self.assertLess(time.monotonic(), end, "掃引が終わらない")
            time.sleep(0.05)

    def test_graph_legend_box_and_lines(self):
        """凡例はグラフの中の右上の箱（Qt と同じ中身と並び）。線の見本はそのトレースの線（設計F §9）。
        トレースの表の線のドロップダウンで変えるとコアの線が変わる。"""
        try:
            self.app.call("set_trace", number=2, visible=True, line="thick")
            self.sweep_once()
            open_app(self.page, self.app, *SIZES[0])
            self.page.wait("gsApp.traceShown(2) && !!gsApp.$traces.get('2')", "トレース2")
            self.page.settle()
            self.page.ev("new Promise((r) => requestAnimationFrame(() => setTimeout(r, 50)))")
            box = self.page.ev("""(() => {
              const g = document.querySelector('.graph .uplot .u-over .glegend');
              if (!g || g.hidden) return null;
              const o = g.parentElement.getBoundingClientRect(), b = g.getBoundingClientRect();
              return {right: o.right - b.right, top: b.top - o.top,
                      rows: [...g.querySelectorAll('.glegend-row')].map((r) => [r.textContent,
                        r.querySelector('line').getAttribute('stroke-width'), r.querySelector('line').getAttribute('stroke-dasharray')]),
                      want: [gsApp.ui.legend['1'], gsApp.ui.legend['2']], strip: !!document.querySelector('.part-bar .legend')};
            })()""")
            self.assertIsNotNone(box)
            self.assertAlmostEqual(box["right"], 8, delta=1)   # Qt の凡例と同じ位置（右上から 8px・上から 6px）
            self.assertAlmostEqual(box["top"], 6, delta=1)
            self.assertEqual([r[0] for r in box["rows"]], box["want"])
            self.assertEqual([(r[1], r[2]) for r in box["rows"]], [("1.25", None), ("2.5", None)])
            self.assertFalse(box["strip"])
            # 線のドロップダウン（2段目のクリアの真下）で変える → コアの線 → 凡例の見本も破線
            self.page.ev("(() => { const s = document.querySelectorAll('.tline')[0]; s.value = 'dash';"
                         " s.dispatchEvent(new Event('change')); return 1; })()")
            end = time.monotonic() + 10
            while self.app.call("get_state")["traces"][0]["line"] != "dash":
                self.assertLess(time.monotonic(), end, "線がコアに届かない")
                time.sleep(0.05)
            self.page.wait("(document.querySelector('.glegend-row line') || {getAttribute() { return null; }})"
                           ".getAttribute('stroke-dasharray') === '10 5'", "凡例の見本が破線")
            # 太い線を上に描く順（参照トレース → 細線 → 実線・破線・点線 → 太線、同じ段は番号順）
            c1, c2 = self.page.ev("[gsApp.palette.trace1, gsApp.palette.trace2]")
            self.assertEqual(self.page.ev("gsApp.graphTraces(gsApp.palette).map((t) => t.color)"), [c1, c2])
            self.app.call("set_trace", number=1, line="thick")
            self.app.call("set_trace", number=2, line="solid")
            self.page.wait("gsApp.traceLine(1) === 'thick' && gsApp.traceLine(2) === 'solid'", "線が画面に届く")
            self.assertEqual(self.page.ev("gsApp.graphTraces(gsApp.palette).map((t) => t.color)"), [c2, c1])
            self.assertEqual(self.page.problems(".app", "線を変えた画面"), [])
        finally:
            self.app.call("set_trace", number=1, line="solid")
            self.app.call("set_trace", number=2, visible=False, line="solid")

    def test_open_button_in_reference_traces(self):
        """参照トレースのまとまりの［開く…］は、メニュー「ファイル → 開く…」と同じ処理（ファイルを選ぶ）を呼ぶ（Qt と同じ）。"""
        open_app(self.page, self.app, *SIZES[0])
        self.page.ev("gsApp.tab = 'trace'; window.__opened = 0; gsApp.openDialog = () => { window.__opened += 1; }; 1")
        self.page.settle()
        text = self.page.ev("(() => { const b = document.querySelector('[data-sec=files] [data-act=open]');"
                            " b.click(); return b.textContent.trim(); })()")
        from pygigast.ui_qt import texts as T

        self.assertEqual(text, T.MENU_ITEMS["open"])
        self.assertEqual(self.page.ev("window.__opened"), 1)

    def test_phone_width_stacks_without_side_scroll(self):
        """スマートフォンの幅（400px）: グラフが上・設定が下に縦に並び、右のパネルがページを横にスクロールさせない（J §5）。
        トレースの表は1本ずつ積み、2段目の選択は1段目の真下のまま。広い画面の並びは変えない（他の試験）。"""
        open_app(self.page, self.app, *SIZES[0])
        try:
            self.page.size(400, 860)
            self.page.settle()
            for tab in TABS:
                self.page.ev(f"gsApp.tab = '{tab}'; 1")
                self.page.settle()
                w, cw = self.page.ev("[document.documentElement.scrollWidth, document.documentElement.clientWidth]")
                self.assertLessEqual(w, cw, tab)
            self.page.ev("gsApp.tab = 'trace'; 1")
            self.page.settle()
            r = self.page.ev("""(() => {
              const g = document.querySelector('.graph-box').getBoundingClientRect();
              const s = document.querySelector('.side').getBoundingClientRect();
              const pairs = [['.tact', '.tavg'], ['.tcnt', '.taft'], ['.tclr', '.tline']].map(([a, b]) =>
                [...document.querySelectorAll(a)].map((e, i) => {
                  const ra = e.getBoundingClientRect(), rb = document.querySelectorAll(b)[i].getBoundingClientRect();
                  return Math.abs(ra.left - rb.left) <= 1 && Math.abs(ra.width - rb.width) <= 1 && rb.top > ra.top;
                }));
              return {stacked: s.top >= g.bottom, pairs, right: s.right, vw: document.documentElement.clientWidth};
            })()""")
            self.assertTrue(r["stacked"], r)
            self.assertLessEqual(r["right"], r["vw"])
            self.assertTrue(all(all(p) for p in r["pairs"]), r["pairs"])
        finally:
            self.page.size(*SIZES[0])
            self.page.settle()

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

    def test_checker_finds_a_broken_table(self):
        """表の検査そのものが働くこと: 本物のマーカー表をわざと横にはみ出させ、セルの文字を切ると、はみ出し・右端の
        「操作」の見切れ・切れた文字が見つかる（Qt の LayoutTest.test_checker_finds_broken_table と同じ）。"""
        open_app(self.page, self.app, *SIZES[0])
        self.assertEqual(self.page.problems(".app", "崩す前"), [])
        self.page.ev("(() => { document.querySelector('.marker-box .mtable').style.minWidth = '3000px';"
                     " const c = document.querySelector('.marker-box .mtable tbody td:nth-child(4)');"
                     " c.style.display = 'block'; c.style.width = '8px'; c.style.overflow = 'hidden'; return 1; })()")
        found = self.page.problems(".app", "壊した表")
        self.page.ev("(() => { document.querySelector('.marker-box .mtable').removeAttribute('style');"
                     " document.querySelector('.marker-box .mtable tbody td:nth-child(4)').removeAttribute('style');"
                     " return 1; })()")
        self.assertTrue(any("が横にはみ出している" in p for p in found), found)
        self.assertTrue(any("列「操作」" in p and "切れている" in p for p in found), found)
        self.assertTrue(any("dBm」の文字が切れている" in p for p in found), found)
        self.page.settle()
        self.assertEqual(self.page.problems(".app", "戻した後"), [])

    def test_marker_table_long_note_in_every_layout(self):
        """どの表示の組み合わせ（スペクトルのみ・＋ウォーターフォール・＋密度: 設計P §2）でも、行のあるマーカー表が横に
        はみ出さず、右端の「操作」まで見える。機能の結果が長い文（交点が見つかった N dB 帯域幅）でも、注意の列で折り返す
        （Qt の LayoutTest.test_marker_table_in_every_layout と同じ場面）。"""
        dev = self.app.api._inst._device
        dev.config.signals = [(500_000_000, -30.0)]
        found: list[str] = []
        try:
            # 信号の山が何点かに載る幅（M1 は 500 MHz）。帯域の端を補間で求められる
            self.app.call("set_sweep", start_hz=480_000_000, stop_hz=520_000_000)
            self.sweep_once()
            for w, h in SIZES:
                open_app(self.page, self.app, w, h)
                # 交点が両側で見つかった（線の縦線が2本ある）N dB 帯域幅の行: 注意の列に下端〜上端・中心・幅・Q の長い文
                self.page.wait("gsApp.markerRows.some((r) => r.kind === 'function' && r.line"
                               " && r.line.freqs_hz.length === 2)", "N dB 帯域幅の結果")
                for layout in ("spectrum", "waterfall", "density"):
                    self.app.call("set_view", layout=layout)
                    self.sweep_once()
                    self.page.wait(f"gsApp.layoutCode === '{layout}'", f"表示の組み合わせ {layout}")
                    for tab in ("trace", "view"):
                        self.page.ev(f"gsApp.tab = '{tab}'; 1")
                        self.page.settle()
                        found += self.page.problems(".app", f"{w}×{h} {layout} {tab}")
                # 折り返した行がある（1280×720 では注意の列が狭い）ことも確かめる: 注意のセルが1行より高い
                if w == SIZES[0][0]:
                    tall = self.page.ev("[...document.querySelectorAll('.marker-box td.note-cell')]"
                                        ".some((c) => c.getBoundingClientRect().height > "
                                        "parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--row-h')) + 1)")
                    self.assertTrue(tall)
        finally:
            dev.config.signals = []
            self.app.call("set_view", layout="spectrum")
            self.app.call("set_sweep", start_hz=400_000_000, stop_hz=600_000_000)
            self.sweep_once()
        self.assertEqual(self.page.errors, [])
        self.assertEqual(sorted(set(found)), [])


class WebLayoutZeroSpanTest(unittest.TestCase):
    """ゼロスパン（設計N §1）の主画面の右のタブ: 時間の欄（X1・X2）・区間の行・表示する長さの欄も同じ決まりで数える。"""

    @classmethod
    def setUpClass(cls):
        cls.app = App(lan=False, zero_span=True)
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
        for where, root in views(self.page, tabs_only=True):
            found += self.page.problems(root, f"{w}×{h} ゼロスパン {where}")
        self.assertEqual(self.page.ev("gsApp.ui.x_axis.kind"), "time")
        self.assertEqual(self.page.errors, [])
        self.assertEqual(sorted(set(found)), [])

    def test_1280x720(self):
        self.check_size(*SIZES[0])

    def test_1920x1080(self):
        self.check_size(*SIZES[1])


@unittest.skipUnless(edge_available(), "Microsoft Edge が無い")
class WebLayoutS11Test(unittest.TestCase):
    """S11（設計E §6.5・設計R §3）の画面を、決めた解像度で開いて数える（Qt の LayoutTest.test_s11_screens と同じ場面）:
    オープンを測った後の信号源のタブと上段、基準を取って SWR にしたトレースの表（表示の形式・描かない理由）・
    マーカーのタブ（SWR のしきい値）・|Γ|・SWR の縦軸の上段。"""

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

    def wait_tasks(self) -> None:
        end = time.monotonic() + 30
        while self.app.call("get_state")["tasks"]:
            self.assertLess(time.monotonic(), end, "基準を取り終えない")
            time.sleep(0.05)

    def check_size(self, w: int, h: int) -> None:
        a, p = self.app, self.page
        a.call("set_view", s11_format="db")
        a.call("set_trace", number=2, visible=False, action="update")
        a.call("set_sweep", start_hz=100_000_000, stop_hz=200_000_000)
        a.call("set_mode", mode="s11")
        a.call("reference_take", kind="fine", standard="open")
        self.wait_tasks()
        open_app(p, a, w, h)
        found: list[str] = []
        p.ev("gsApp.tab = 'source'; 1")
        p.wait("!!document.querySelector('[data-field=\"s11_state\"]')", "オープンを測った")
        p.settle()
        found += p.problems(".app", f"{w}×{h} S11 オープンの後 source")
        # 基準を取る前の電力（dBm）でトレース2を固定しておく（縦軸と単位が違うので描かない固定のトレース）
        a.call("set_trace", number=2, visible=True, action="update")
        a.call("run", mode="single")
        p.wait("gsApp.st.traces[1].has_values && gsApp.st.run.state === 'stopped'", "トレース2")
        a.call("set_trace", number=2, action="fixed")
        a.call("reference_take", standard="short")
        self.wait_tasks()
        a.call("run", mode="single")
        a.call("set_view", s11_format="swr")
        mid = a.call("get_state")["markers"]["markers"][0]["id"]
        a.call("marker_peak", id=mid, polarity="dip")
        a.call("marker_function", id=mid, threshold={"level": 2.0, "side": "below"}, bandwidth_db=[])
        p.wait("gsApp.axisUnit === 'SWR' && document.querySelectorAll('.tvis input')[1].disabled", "縦軸が SWR")
        p.ev("gsApp.modal = null; gsApp.confirmBox = null; 1")
        for tab in ("trace", "marker", "view", "source"):
            p.ev(f"gsApp.tab = '{tab}'; gsApp.selectedMarker = '{mid}'; 1")
            p.settle()
            found += p.problems(".app", f"{w}×{h} S11（SWR） {tab}")
        self.assertEqual(p.errors, [])
        self.assertEqual(sorted(set(found)), [])

    def test_1280x720(self):
        self.check_size(*SIZES[0])

    def test_1920x1080(self):
        self.check_size(*SIZES[1])


@unittest.skipUnless(edge_available(), "Microsoft Edge が無い")
class WebLayoutRefValueTest(unittest.TestCase):
    """基準の値（設計E §6.7）の画面を、決めた解像度で開いて数える（Qt の LayoutTest.test_reference_value_screens と同じ
    場面）: 値を付けた基準を一覧で選んだ信号源のタブ（基準の値の欄・行の値・「基準の値…」）と上段、「基準の値…」の窓、
    S11 で欄を使えなくした信号源のタブ。"""

    @classmethod
    def setUpClass(cls):
        cls.app = App(lan=False)
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
        a, p = self.app, self.page
        a.call("set_sweep", start_hz=100_000_000, stop_hz=200_000_000)
        a.call("set_mode", mode="tg")
        if not a.call("reference_list")["references"]:
            a.call("reference_take", kind="wide", name="ATT", value_db=-40.0, show_zero=True)
            end = time.monotonic() + 30
            while a.call("get_state")["tasks"]:
                self.assertLess(time.monotonic(), end, "基準を取り終えない")
                time.sleep(0.05)
        open_app(p, a, w, h)
        found: list[str] = []
        p.ev("gsApp.modal = null; gsApp.confirmBox = null; gsApp.tab = 'source'; 1")
        p.wait("gsApp.refs.length === 1", "基準の一覧")
        p.ev("gsApp.refSel = gsApp.refs[0].id; 1")
        btn = "document.querySelector('[data-act=\"ref_value\"]')"
        p.ev(btn + ".scrollIntoView({block: 'center'}); 1")
        p.settle()
        self.assertFalse(p.ev(btn + ".disabled"))
        found += p.problems(".app", f"{w}×{h} 基準の値 source")
        p.ev(btn + ".click(); 1")
        p.wait("!!document.querySelector('.modal.input-win input')", "基準の値の窓")
        p.settle()
        found += p.problems(".app", f"{w}×{h} 基準の値の窓")
        p.ev("gsApp.modal = null; 1")
        a.call("set_mode", mode="s11")
        try:
            p.wait("document.querySelector('[data-field=\"ref_value\"] input').disabled", "S11 では使えない")
            p.settle()
            found += p.problems(".app", f"{w}×{h} 基準の値 S11 source")
        finally:
            a.call("set_mode", mode="sa")
        self.assertEqual(p.errors, [])
        self.assertEqual(sorted(set(found)), [])

    def test_1280x720(self):
        self.check_size(*SIZES[0])

    def test_1920x1080(self):
        self.check_size(*SIZES[1])


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


@unittest.skipUnless(edge_available(), "Microsoft Edge が無い")
class WebLayoutPlutoTest(unittest.TestCase):
    """PlutoSDR に繋いだときだけ出る欄（受信・送信）を、決めた解像度で開いて数える（Qt の LayoutPlutoTest と同じ）。"""

    @classmethod
    def setUpClass(cls):
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

    def test_receiver_and_transmitter(self):
        found: list[str] = []
        for w, h in SIZES:
            open_app(self.page, self.app, w, h)
            self.assertTrue(self.page.ev("!!document.querySelector('[data-sec=plutosdr]')"))
            self.assertTrue(self.page.ev("!!document.querySelector('[data-part=pluto_tx]')"))
            self.assertTrue(self.page.ev("!!gsApp.plutoView.gain_used_text"))
            for where, root in views(self.page, tabs_only=True):
                found += self.page.problems(root, f"{w}×{h} PlutoSDR {where}")
        self.assertEqual(self.page.errors, [])
        self.assertEqual(sorted(set(found)), [])

    def test_gain_calibration_pages(self):
        """校正の画面の PlutoSDR の利得の補正（設計Q §7.6）: 準備・測った後の測る・確かめると、受信の欄の「利得の補正なし」
        の印（Qt の LayoutPlutoGainTest と同じ手順）。"""
        app, page = self.app, self.page
        app.api._inst._pluto_gain_grid = [100_000_000, 2_400_000_000, 5_000_000_000]
        found: list[str] = []
        try:
            for w, h in SIZES:
                open_app(page, app, w, h)
                page.ev("gsApp.tab = 'trace'; 1")
                page.settle()
                self.assertTrue(page.ev("!!document.querySelector('[data-mark=pluto_gain_cal]')"))
                found += page.problems(".app", f"{w}×{h} 主画面 PlutoSDR 利得の補正なし")
                page.ev("gsApp.openCal('pluto_gain'); 1")
                page.wait("!!document.querySelector('.calwin')", "校正の画面")
                page.ev("gsApp.calWin.page = 0; 1")
                page.settle()
                found += page.problems(".modal.calwin", f"{w}×{h} PlutoSDR の利得の補正 準備")
                if not app.call("get_state")["calibration_work"]["unsaved"]:
                    app.call("cal_measure")
                    end = time.monotonic() + 90
                    while app.call("get_state")["tasks"]:
                        self.assertLess(time.monotonic(), end, "測り終わらない")
                        time.sleep(0.1)
                page.ev("gsApp.calWin.page = 1; 1")
                page.wait("gsApp.calView && gsApp.calView.unsaved > 0 && !gsApp.calView.measuring", "測った")
                page.settle()
                found += page.problems(".modal.calwin", f"{w}×{h} PlutoSDR の利得の補正 測る")
                page.ev("gsApp.calWin.page = 2; 1")
                page.settle()
                found += page.problems(".modal.calwin", f"{w}×{h} PlutoSDR の利得の補正 確かめる")
                page.ev("gsApp.calWin.page = 0; 1")
        finally:
            app.call("cal_close")
            page.wait("!gsApp.calWin", "校正の画面を閉じる")
        self.assertEqual(page.errors, [])
        self.assertEqual(sorted(set(found)), [])


if __name__ == "__main__":
    unittest.main()
