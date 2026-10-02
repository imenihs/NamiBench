// PNG の画像出力（設計I §3。Web はブラウザの canvas で、画面の外に描き直す）。
// 配置（文字の大きさ・行の高さ・余白・帯の段と項目・マーカー表・凡例・tEXt の中身）はサーバーが
// core/report_layout.py で作って返す（POST /api/v1/png_layout。Qt の png_export も同じ関数で描く: I §3.1）。
// ここで行うのは、文字の幅を測って帯を折り返すことと、線や文字を描くことだけ（書体の幅は描く側でしか測れない）。
(function (root) {
  "use strict";

  // 全角1字の幅を測る字（Qt の png_export と同じく「あ」）
  const WIDE = String.fromCharCode(0x3042);

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

  // 帯の段ごとに、項目の切れ目で折り返した行の並び（Qt の png_export._flow_band と同じ規則）
  function flowBand(g, band, width, sep) {
    const lines = [];
    const split = (text) => {
      const out = [];
      let cur = "";
      for (const ch of text) {
        if (cur && g.measureText(cur + ch).width > width) { out.push(cur); cur = ""; }
        cur += ch;
      }
      if (cur) out.push(cur);
      return out;
    };
    for (const [color, items] of band) {
      let cur = [];
      let curW = 0;
      for (const it of items) {
        for (const piece of split(it)) {
          const iw = g.measureText(piece).width;
          if (cur.length && curW + sep + iw > width) { lines.push([color, cur]); cur = []; curW = 0; }
          curW = cur.length ? curW + sep + iw : iw;
          cur.push(piece);
        }
      }
      if (cur.length) lines.push([color, cur]);
    }
    return lines;
  }

  async function render(ctx, size = [1600, 1000]) {
    const [W, H] = size;
    const c = ctx.colors;
    const lay = await GSApi.call("png_layout", {
      width: W, height: H, note: ctx.note || "", ref_level: ctx.amp.ref, db_per_div: ctx.amp.div, unit: ctx.amp.unit,
      files: ctx.files || [],
    });
    const px = lay.font_px;
    const lh = lay.line_px;
    const margin = lay.margin_px;
    const gap = lay.gap_px;
    const font = `${Math.round(px)}px ${lay.fonts.map((f) => `"${f}"`).join(",")},sans-serif`;

    const canvas = document.createElement("canvas");
    canvas.width = W;
    canvas.height = H;
    const g2 = canvas.getContext("2d");
    g2.fillStyle = c.graph_bg;
    g2.fillRect(0, 0, W, H);
    g2.font = font;
    g2.textBaseline = "middle";
    const innerW = W - 2 * margin;
    const sep = g2.measureText(WIDE).width * lay.item_gap;

    // 区画の配分（Qt と同じ: 帯は下端、表はその上、残りがグラフ）
    const band = flowBand(g2, lay.band, innerW, sep);
    const bandH = band.length * lh + (band.length ? 2 * gap : 0);
    const t = lay.table;
    const tableH = t.rows.length ? (t.rows_per_col + 1) * lh : 0;
    const bandTop = H - bandH;
    const tableTop = bandTop - gap - tableH;
    const graphBottom = t.rows.length ? tableTop - gap : bandTop - gap;

    // 下端の帯（I §3.2）
    g2.fillStyle = c.panel_bg;
    g2.fillRect(0, bandTop, W, bandH);
    g2.strokeStyle = c.axis;
    g2.beginPath(); g2.moveTo(0, bandTop + 0.5); g2.lineTo(W, bandTop + 0.5); g2.stroke();
    let y = bandTop + gap;
    for (const [color, items] of band) {
      g2.fillStyle = c[color] || c.text;
      let x = margin;
      for (const it of items) {
        g2.fillText(it, x, y + lh / 2);
        x += g2.measureText(it).width + sep;
      }
      y += lh;
    }

    // マーカー表（グラフのすぐ下。多いときは2列、収まらない分は「ほか N 行」はサーバーが入れる: I §3.5）
    if (t.rows.length) {
      const pad = g2.measureText(WIDE).width;
      const colGap = pad * 2;
      const blockW = (innerW - colGap * (t.cols - 1)) / t.cols;
      const all = [t.header, ...t.rows];
      let widths = t.header.map((_, i) => Math.max(...all.map((r) => g2.measureText(String(r[i] || "")).width)));
      const need = widths.reduce((a, b) => a + b, 0) + pad * (widths.length - 1);
      let tfont = font;
      if (need > blockW) {
        // 幅が足りないときは表の文字だけ縮める（切らない）
        tfont = `${Math.max(8, Math.floor(px * blockW / need))}px ${lay.fonts.map((f) => `"${f}"`).join(",")},sans-serif`;
        g2.font = tfont;
        widths = t.header.map((_, i) => Math.max(...all.map((r) => g2.measureText(String(r[i] || "")).width)));
      }
      const pad2 = g2.measureText(WIDE).width;
      for (let b = 0; b < t.cols; b++) {
        const chunk = t.rows.slice(b * t.rows_per_col, (b + 1) * t.rows_per_col);
        if (!chunk.length) continue;
        const x0 = margin + b * (blockW + colGap);
        let yy = tableTop;
        [t.header, ...chunk].forEach((row, ri) => {
          let x = x0;
          row.forEach((cell, i) => {
            const s = String(cell || "");
            const tw = g2.measureText(s).width;
            g2.fillStyle = ri === 0 ? c.text_dim : (i === row.length - 1 && s ? c.warn : c.text);
            g2.fillText(s, t.right[i] ? x + widths[i] - tw : x, yy + lh / 2);
            x += widths[i] + pad2;
          });
          if (ri === 0) {
            g2.strokeStyle = c.grid;
            g2.beginPath(); g2.moveTo(x0, yy + lh - 0.5); g2.lineTo(x - pad2, yy + lh - 0.5); g2.stroke();
          }
          yy += lh;
        });
      }
      g2.font = font;
    }

    // 凡例（グラフの上の1段: U-14）
    let lx = margin;
    const sw = g2.measureText(WIDE).width * lay.legend_sample;
    for (const it of lay.legend) {
      const col = String(it.color || "").startsWith("#") ? it.color : (c[it.color] || c.trace1);
      const tw = g2.measureText(it.text).width;
      if (lx + sw + tw > W - margin) break;
      // 線の見本はそのトレースの線（設計F §9。太さと切れ目はサーバーの report_layout.legend_stroke: Qt と同じ）
      g2.strokeStyle = col;
      g2.lineWidth = it.stroke.width;
      g2.setLineDash(it.stroke.dash || []);
      g2.beginPath(); g2.moveTo(lx, margin + lh / 2); g2.lineTo(lx + sw * 0.8, margin + lh / 2); g2.stroke();
      g2.setLineDash([]);
      g2.lineWidth = 1;
      g2.fillStyle = c.text;
      g2.fillText(it.text, lx + sw, margin + lh / 2);
      lx += sw + tw + g2.measureText(WIDE).width * 2;
    }

    // グラフ: 画面と同じ描画の部品（GSGraph）を、画面の外の要素に決めた大きさで描く
    const gTop = margin + lh;
    const graphW = innerW;
    const graphH = Math.max(lh * 3, graphBottom - gTop);
    const host = document.createElement("div");
    host.style.cssText = `position:fixed;left:-20000px;top:0;width:${graphW}px;height:${graphH}px;background:${c.graph_bg}`;
    document.body.appendChild(host);
    try {
      // トレースの線の基準の幅は Qt の PNG と同じ（report_layout.png_line_base: 文字の大きさから）
      const g = new GSGraph(host, { colors: c, static: true, width: graphW, height: graphH, lineBase: lay.line_base_px });
      const s = (ctx.st || {}).sweep || {};
      if (s.step_hz === 0) g.setX(0, Math.max(1, s.points - 1), true); else g.setX(s.start_hz, s.stop_hz, false);
      // 周波数オフセット（設計O）: 目盛の数字と軸の名前は画面と同じく換算後
      if (s.step_hz !== 0 && ctx.offset && ctx.offset.hz) g.setXOffset(ctx.offset.hz, ctx.offset.text);
      g.setY(ctx.amp.ref, ctx.amp.div, ctx.amp.unit);
      g.setTraces(ctx.traces(c));
      g.setMarkers((ctx.markers || {}).markers || [], null);
      // マーカーの機能の線（G §3.6）: 画面で出しているときだけ PNG にも描く
      if ((((ctx.st || {}).view) || {}).function_lines !== false) g.setFunctionLines((ctx.markers || {}).rows || []);
      g.setXY((ctx.markers || {}).xy);
      g.u.redraw(false, false);
      // uPlot は描き直しをまとめて microtask で行うので、描き終わるのを待ってから写す
      // （requestAnimationFrame は裏のタブで止まるので使わない）
      await new Promise((r) => setTimeout(r, 0));
      g2.drawImage(g.u.ctx.canvas, margin, gTop, graphW, graphH);
      g.destroy();
    } finally {
      host.remove();
    }

    const blob = await new Promise((res) => canvas.toBlob(res, "image/png"));
    const png = new Uint8Array(await blob.arrayBuffer());
    return new Blob([addTextChunk(png, lay.meta_key || "pygigast", asciiJson(lay.meta))], { type: "image/png" });
  }

  root.GSPng = { render, addTextChunk, crc32, flowBand };
})(window);
