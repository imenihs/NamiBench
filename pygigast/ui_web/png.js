// PNG の画像出力（設計I §3。Web はブラウザの canvas で、画面の外に描き直す）。
// 配置（帯の段・文字の大きさの比・マーカー表の上限）は Qt の ui_qt/png_export.py の LAYOUT と同じ値。
// I §3.1 は「配置はコアに1つ」だが、いまは Qt 側にあるので同じ値をここに写している（コアへ移したら読み替える）。
(function (root) {
  "use strict";

  const LAYOUT = {
    base_px_at_1600x1000: 16.0,
    min_px: 11.0,
    margin: 1.0,
    gap: 0.6,
    item_gap: 1.6,
    table_max_ratio: 0.30,
    table_two_columns_from: 7,
    band_rows: ["when", "conditions", "correction", "warnings", "note"],
  };
  const META_KEY = "pygigast";
  const META_FORMAT = 1;
  const MODE_LABELS = { sa: "SA", tg: "TG", s11: "S11", sg: "SG", nf: "NF" };
  const FONT = '"Yu Gothic UI","Meiryo UI","Meiryo","Noto Sans CJK JP","Noto Sans JP","Hiragino Sans",sans-serif';

  function measuredAt(d) {
    const p = (n) => String(n).padStart(2, "0");
    const off = -d.getTimezoneOffset();
    const sign = off >= 0 ? "+" : "-";
    const a = Math.abs(off);
    return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())} ${sign}${p(Math.floor(a / 60))}:${p(a % 60)}`;
  }

  // 帯の段ごとの項目（png_export.band_rows と同じ段・同じ語）
  function bandRows(data) {
    const F = GSUnits.formatFreq;
    const rows = {};
    const dev = data.device || {};
    const devText = [dev.model, dev.serial ? `S/N ${dev.serial}` : null].filter((x) => x).join(" ");
    rows.when = ["text", [data.measured_at, ...(devText ? [devText] : []), `pyGigaSt ${data.app_version || ""}`.trim()]];
    const c = data.conditions || {};
    const cond = [];
    if (data.mode) cond.push(data.mode);
    if (c.start_hz != null && c.stop_hz != null) {
      cond.push(`Start ${F(c.start_hz)}`, `Stop ${F(c.stop_hz)}`, `Center ${F(c.start_hz + Math.floor((c.stop_hz - c.start_hz) / 2))}`, `Span ${F(c.stop_hz - c.start_hz)}`);
    }
    if (c.rbw_hz != null) cond.push(`RBW ${F(c.rbw_hz)}`);
    if (c.points != null) cond.push(`${c.points} 点` + (c.step_hz != null ? `（Step ${F(c.step_hz)}）` : ""));
    if (c.image_cancel != null) cond.push("イメージキャンセル " + (c.image_cancel ? "ON" : "OFF"));
    if (c.ref_level != null) cond.push(`REF ${c.ref_level} ${c.unit || "dBm"}`);
    if (c.db_per_div != null) cond.push(`${c.db_per_div} dB/div`);
    rows.conditions = ["text", cond];
    const corr = [];
    const cal = data.calibration;
    corr.push(cal ? `校正データ: ${cal.source || ""}` + (cal.at ? `（${cal.at}）` : "") : "校正データ: なし");
    const nz = data.normalization;
    if (nz) {
      let s = `ノーマライズ: ${nz.name || ""}` + (nz.at ? `（${nz.at}）` : "");
      if (nz.before_power_cycle) s += " 電源の入れ直し前の基準";
      if (nz.applied === false) s += " 当てていない" + (nz.reason ? `: ${nz.reason}` : "");
      corr.push(s);
    }
    rows.correction = ["text", corr];
    rows.warnings = ["warn", (data.warnings || []).filter((w) => w).map((w) => `注意: ${w}`)];
    const memo = (data.note || "").trim();
    rows.note = ["text", memo ? [`メモ: ${memo}`] : []];
    // 空の段は出さない（U-15）
    return LAYOUT.band_rows.map((k) => rows[k]).filter((r) => r[1].length);
  }

  // 画面の状態から、帯とメタデータの中身を作る
  function collect(ctx) {
    const st = ctx.st || {};
    const s = st.sweep || {};
    const conn = st.connection || {};
    const ident = conn.identity || {};
    const cal = st.calibration;
    const n = st.normalization || {};
    const data = {
      measured_at: measuredAt(new Date()),
      app_version: ctx.appVersion,
      device: { model: ident.model || conn.profile_model, serial: ident.serial },
      mode: MODE_LABELS[s.mode] || s.mode,
      conditions: {
        start_hz: s.start_hz, stop_hz: s.stop_hz, rbw_hz: s.rbw_hz, points: s.points, step_hz: s.step_hz,
        image_cancel: s.image_cancel, ref_level: ctx.amp.ref, db_per_div: ctx.amp.div, unit: ctx.amp.unit,
      },
      calibration: cal ? { source: (cal.source && (cal.source.file_name || cal.source.kind)) || "", at: cal.created_at } : null,
      normalization: n.used && n.selected ? {
        name: n.selected.name, at: n.selected.taken_at, before_power_cycle: !!n.power_cycled, applied: !!n.applied,
        reason: n.applied ? null : GSTexts.get("NORM_REASONS", n.reason || "", n.reason),
      } : null,
      warnings: (s.warnings || []).map((w) => GSTexts.get("WARNINGS", w)),
      markers: (ctx.rows || []).map((r) => ({ name: r.name, trace: r.trace, freq_mhz: r.freq, value: r.value, ref: r.refName, delta_freq_mhz: r.dfreq, delta_value: r.dval, note: r.note, child: !!r.parent })),
      traces: (ctx.traceStates || []).filter((t) => t.visible && t.has_values).map((t) => ({ number: t.number, legend: GSTexts.legendText({ ...t, count: t.current_count }) })),
      note: ctx.note || "",
    };
    return data;
  }

  // ---- PNG の塊（tEXt は Latin-1 なので、JSON は ASCII にしてから入れる）
  const CRC_TABLE = (() => {
    const t = new Uint32Array(256);
    for (let n = 0; n < 256; n++) {
      let c = n;
      for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
      t[n] = c >>> 0;
    }
    return t;
  })();
  function crc32(bytes) {
    let c = 0xffffffff;
    for (let i = 0; i < bytes.length; i++) c = CRC_TABLE[(c ^ bytes[i]) & 0xff] ^ (c >>> 8);
    return (c ^ 0xffffffff) >>> 0;
  }
  function asciiJson(v) {
    return JSON.stringify(v).replace(/[\u0080-￿]/g, (ch) => "\\u" + ch.charCodeAt(0).toString(16).padStart(4, "0"));
  }
  function addTextChunk(png, key, text) {
    const body = new TextEncoder().encode(key + "\0" + text);
    const kind = new TextEncoder().encode("tEXt");
    const chunk = new Uint8Array(12 + body.length);
    const dv = new DataView(chunk.buffer);
    dv.setUint32(0, body.length);
    chunk.set(kind, 4);
    chunk.set(body, 8);
    const crcIn = new Uint8Array(4 + body.length);
    crcIn.set(kind, 0);
    crcIn.set(body, 4);
    dv.setUint32(8 + body.length, crc32(crcIn));
    // IEND（最後の 12 バイト）の前に入れる
    const iend = png.length - 12;
    const out = new Uint8Array(png.length + chunk.length);
    out.set(png.subarray(0, iend), 0);
    out.set(chunk, iend);
    out.set(png.subarray(iend), iend + chunk.length);
    return out;
  }

  async function render(ctx, size = [1600, 1000]) {
    const [W, H] = size;
    const c = ctx.colors;
    const data = collect(ctx);
    const fontPx = Math.max(LAYOUT.min_px, LAYOUT.base_px_at_1600x1000 * Math.min(W / 1600, H / 1000));
    const lineH = fontPx * 1.45;
    const margin = fontPx * LAYOUT.margin;
    const gap = fontPx * LAYOUT.gap;
    const band = bandRows(data);
    const tableRows = data.markers;
    const maxTableH = H * LAYOUT.table_max_ratio;
    const twoCols = tableRows.length > LAYOUT.table_two_columns_from;
    const perCol = twoCols ? Math.ceil(tableRows.length / 2) : tableRows.length;
    let shownPerCol = Math.max(0, Math.min(perCol, Math.floor(maxTableH / lineH) - 1));
    const tableH = tableRows.length ? (shownPerCol + 1) * lineH + (shownPerCol < perCol ? lineH : 0) : 0;
    const bandH = band.length * lineH;
    const graphH = Math.max(120, H - 2 * margin - bandH - tableH - 2 * gap);
    const graphW = W - 2 * margin;

    const canvas = document.createElement("canvas");
    canvas.width = W;
    canvas.height = H;
    const g2 = canvas.getContext("2d");
    g2.fillStyle = c.graph_bg;
    g2.fillRect(0, 0, W, H);

    // グラフ: 画面と同じ描画の部品（GSGraph）を、画面の外の要素に決めた大きさで描く
    const host = document.createElement("div");
    host.style.cssText = `position:fixed;left:-20000px;top:0;width:${graphW}px;height:${graphH}px;background:${c.graph_bg}`;
    document.body.appendChild(host);
    try {
      const g = new GSGraph(host, { colors: c, static: true, width: graphW, height: graphH });
      const s = (ctx.st || {}).sweep || {};
      if (s.step_hz === 0) g.setX(0, Math.max(1, s.points - 1), true); else g.setX(s.start_hz, s.stop_hz, false);
      g.setY(ctx.amp.ref, ctx.amp.div, ctx.amp.unit);
      g.setTraces(ctx.traces(c));
      g.setMarkers((ctx.markers || {}).markers || [], null);
      g.setXY((ctx.markers || {}).xy);
      g.u.redraw(false, false);
      // uPlot は描き直しをまとめて microtask で行うので、描き終わるのを待ってから写す
      // （requestAnimationFrame は裏のタブで止まるので使わない）
      await new Promise((r) => setTimeout(r, 0));
      g2.drawImage(g.u.ctx.canvas, margin, margin, graphW, graphH);
      g.destroy();
    } finally {
      host.remove();
    }

    g2.textBaseline = "top";
    g2.font = `${fontPx}px ${FONT}`;
    // 凡例（トレースの番号・動作・回数: U-14）。グラフの右上に重ねる
    let ly = margin + fontPx * 0.8;
    g2.textAlign = "right";
    for (const t of data.traces) {
      g2.fillStyle = c["trace" + t.number] || c.text;
      g2.fillText(t.legend, margin + graphW - fontPx * 1.5, ly);
      ly += lineH;
    }
    g2.textAlign = "left";

    // マーカー表（グラフのすぐ下。多いときは2列、収まらない分は「ほか N 行」: I §3.5）
    let y = margin + graphH + gap;
    if (tableRows.length) {
      const colW = twoCols ? (W - 2 * margin) / 2 : W - 2 * margin;
      const cols = ["番号", "トレース", "周波数 (MHz)", "値", "基準", "Δ周波数 (MHz)", "Δ値 (dB)", ""];
      const widths = [0.13, 0.08, 0.17, 0.17, 0.08, 0.17, 0.1, 0.1];
      for (let k = 0; k < (twoCols ? 2 : 1); k++) {
        const x0 = margin + k * colW;
        const part = tableRows.slice(k * perCol, (k + 1) * perCol);
        let x = x0;
        g2.fillStyle = c.text_dim;
        cols.forEach((h, i) => { g2.fillText(h, x, y); x += widths[i] * colW; });
        part.slice(0, shownPerCol).forEach((r, j) => {
          const vals = [(r.child ? "  " : "") + r.name, r.trace, r.freq_mhz, r.value, r.ref, r.delta_freq_mhz, r.delta_value, r.note];
          let xx = x0;
          g2.fillStyle = r.child ? c.text_dim : c.marker;
          vals.forEach((v, i) => { g2.fillText(String(v == null ? "" : v), xx, y + (j + 1) * lineH, widths[i] * colW - 4); xx += widths[i] * colW; });
        });
        if (part.length > shownPerCol) {
          g2.fillStyle = c.text_dim;
          g2.fillText(`ほか ${part.length - shownPerCol} 行`, x0, y + (shownPerCol + 1) * lineH);
        }
      }
      y += tableH + gap;
    }

    // 下端の帯（I §3.2）
    y = H - margin - bandH;
    for (const [color, items] of band) {
      g2.fillStyle = color === "warn" ? c.warn : c.text;
      let x = margin;
      for (const it of items) {
        g2.fillText(it, x, y, W - margin - x);
        x += g2.measureText(it).width + fontPx * LAYOUT.item_gap;
      }
      y += lineH;
    }

    const blob = await new Promise((res) => canvas.toBlob(res, "image/png"));
    const png = new Uint8Array(await blob.arrayBuffer());
    const meta = { format: META_FORMAT, ...data };
    delete meta.note;
    if (data.note) meta.note = data.note;
    return new Blob([addTextChunk(png, META_KEY, asciiJson(meta))], { type: "image/png" });
  }

  root.GSPng = { render, bandRows, addTextChunk, crc32 };
})(window);
