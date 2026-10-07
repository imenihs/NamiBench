// ウォーターフォールと密度表示（設計P §2・§3・§4・§5・§8・§9.2・§9.3。Qt の ui_qt/waterfall_view.py・density_view.py と
// 同じ描き方・同じ操作）。目盛・格子は uPlot、行と升目の絵は canvas に描く（P §8: Web はウォーターフォールと密度は canvas）。
// - 1掃引＝1行。新しい行を上に足して下へ流す。最新に付いていく間は1行 ROW_PX 画素、時間の範囲を選んだ・再生中は範囲を縦いっぱいに
// - 横軸（周波数）はスペクトルと同じ範囲。各行はその行の周波数の位置に描き、範囲の外は空白。条件の変わった境目に線と条件
// - 色はサーバーが決めた色の範囲とカラーマップ（Qt と同じ 256 段の表: /api/v1/info の colormaps）
// - 行の上のマウス → 読み値（時刻・周波数・レベル）、クリック → その時刻の掃引を出す（過去のトレース／再生のカーソル）
// - グラフの中の横のドラッグ・ホイール → スペクトルと同じ周波数の操作。左の時刻の軸の上のドラッグ → 時間の範囲、ホイール → 前後へ
// 横軸の位置は機器の周波数のまま。周波数オフセット（設計O）は目盛の数字と軸の名前だけに足す（graph.js と同じ）
(function (root) {
  "use strict";

  const ROW_PX = 2;
  const TICK_STEPS_S = [1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600, 7200, 10800, 21600, 43200, 86400];
  const TICK_MIN_PX = 36;
  const DIVISIONS = 10;
  // 縦軸の幅（スペクトルの graph.js と同じ。横軸の画素をそろえる: P §2 の連動）
  const Y_SIZE = 56;
  const Y_LABEL = 18;

  function xUnit(span) {
    const a = Math.abs(span);
    return a >= 1e9 ? [1e9, "GHz"] : a >= 1e6 ? [1e6, "MHz"] : a >= 1e3 ? [1e3, "kHz"] : [1, "Hz"];
  }

  // 行の値を横軸 [x0, x1] の width 列に移す（列に点が多いときは最大値: P §8。少ないときは近い点）。範囲の外は NaN（Qt と同じ）
  function resample(row, x0, x1, width) {
    const out = new Float32Array(width).fill(NaN);
    const vals = row.values;
    const n = vals.length;
    if (!n || width <= 0 || x1 <= x0) return out;
    const start = row.start_hz;
    const step = row.step_hz || 1;
    const scale = width / (x1 - x0);
    for (let j = 0; j < n; j++) {
      const v = vals[j];
      if (!Number.isFinite(v)) continue;
      const c = Math.floor((start + step * j - x0) * scale);
      if (c < 0 || c >= width) continue;
      const cur = out[c];
      if (!(cur >= v)) out[c] = v;
    }
    if (step * scale > 1) {
      for (let c = 0; c < width; c++) {
        if (!Number.isNaN(out[c])) continue;
        const fx = x0 + (c + 0.5) / scale;
        const j = Math.round((fx - start) / step);
        if (j >= 0 && j < n) out[c] = vals[j];
      }
    }
    return out;
  }

  function tickStep(secPerPx) {
    for (const s of TICK_STEPS_S) if (s / Math.max(1e-9, secPerPx) >= TICK_MIN_PX) return s;
    return TICK_STEPS_S[TICK_STEPS_S.length - 1];
  }

  function hhmmss(t) {
    const d = new Date(t * 1000);
    const p = (n) => String(n).padStart(2, "0");
    return `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
  }

  // 共通の土台: uPlot で目盛・格子・操作を持つ。描く中身は draw(u) で
  class Base {
    constructor(el, opts, yAxis) {
      this.el = el;
      this.o = opts;
      this.colors = opts.colors;
      this.x0 = 0;
      this.x1 = 1;
      this.zeroSpan = false;
      this.xOffset = 0;
      this.xOffsetText = "";
      this.lut = null;
      this.yAxis = yAxis;
      // 周波数軸を出すか。スペクトルのすぐ下に付いている間は出さない（同じ軸を2回出さない: P §2 の連動。setXAxisShown）
      this.showX = opts.showX !== false;
      this._drag = null;
      this._build();
      this._wireWindow();
    }

    // 周波数軸を出す・出さない（uPlot は軸の有無を作ってから変えられないので作り直す）
    setXAxisShown(show) {
      if (!!show === this.showX) return;
      this.showX = !!show;
      this.u.destroy();
      this._build();
      this.rebuild();
    }

    _build() {
      const self = this;
      const el = this.el;
      const c = this.colors;
      const yAxis = this.yAxis;
      const uopts = {
        width: el.clientWidth || 600,
        height: el.clientHeight || 200,
        // 周波数軸を出さない間は下に余白（縦軸の一番下の目盛の数字を切らない。Qt と同じ）
        padding: [6, 18, this.showX ? 0 : 8, 0],
        legend: { show: false },
        pxAlign: 0,
        scales: {
          x: { time: false, auto: false, range: () => [self.x0, self.x1] },
          y: { auto: false, range: () => self.yRange() },
        },
        axes: [
          {
            show: this.showX,
            stroke: c.text, grid: { stroke: c.grid, width: 1 }, ticks: { stroke: c.axis, width: 1 }, label: "",
            labelSize: Y_LABEL, font: "12px sans-serif", labelFont: "12px sans-serif",
            values: (u, splits) => {
              if (self.zeroSpan) return splits.map((v) => String(Math.round(v)));
              const [k] = xUnit(self.x1 - self.x0);
              return splits.map((v) => String(+((v + self.xOffset) / k).toPrecision(10)));
            },
          },
          // 時刻の目盛「22:37:06」が縦軸の幅（スペクトルと同じ）に入るよう、目盛の線と隙間を短くする（ウォーターフォールだけ）
          { stroke: c.text, grid: { stroke: c.grid, width: 1, show: yAxis.grid },
            ticks: { stroke: c.axis, width: 1, size: yAxis.grid ? 10 : 3 }, gap: yAxis.grid ? 5 : 2,
            label: "", labelSize: Y_LABEL, size: Y_SIZE, font: "12px sans-serif", labelFont: "12px sans-serif",
            splits: () => self.ySplits(), values: (u, splits) => self.yValues(splits) },
        ],
        series: [{}, { stroke: "transparent", points: { show: false } }],
        cursor: { drag: { x: true, y: false, setScale: false }, points: { show: false }, x: true, y: false },
        select: { show: true },
        hooks: { draw: [(u) => self._draw(u)], setSelect: [(u) => self._onSelect(u)] },
      };
      this.u = new uPlot(uopts, [[0, 1], [null, null]], el);
      this._colorsKey = JSON.stringify(c);
      this._labels();
      this._wire();
    }

    _labels() {
      const X = root.GSTexts;
      const [, unit] = xUnit(this.x1 - this.x0);
      if (this.zeroSpan) this.u.axes[0].label = X.w("axis_zero_span");
      else if (this.xOffset) this.u.axes[0].label = X.fmtText(X.get("VIEW_TEXTS", "axis_freq_offset"), { off: this.xOffsetText }) + ` (${unit})`;
      else this.u.axes[0].label = X.w("axis_freq", { unit });
      this.u.axes[1].label = this.yLabel();
    }

    setColors(c) {
      // 同じ配色なら何もしない（状態が届くたびに描き直さない）
      const key = JSON.stringify(c);
      if (key === this._colorsKey) return;
      this._colorsKey = key;
      this.colors = c;
      for (const a of this.u.axes) {
        a.stroke = () => c.text;
        if (a.grid) a.grid.stroke = () => c.grid;
        if (a.ticks) a.ticks.stroke = () => c.axis;
      }
      this.u.redraw(false, true);
    }

    setLut(arr) { this.lut = arr; this.repaint(); }

    setX(x0, x1, zeroSpan, off, offText) {
      const nx1 = x1 > x0 ? x1 : x0 + 1;
      const changed = x0 !== this.x0 || nx1 !== this.x1 || !!zeroSpan !== this.zeroSpan;
      if (!changed && (zeroSpan ? 0 : (off || 0)) === this.xOffset && (offText || "") === this.xOffsetText) return;
      this.x0 = x0;
      this.x1 = nx1;
      this.zeroSpan = !!zeroSpan;
      this.xOffset = zeroSpan ? 0 : (off || 0);
      this.xOffsetText = offText || "";
      this._labels();
      this.u.setScale("x", { min: this.x0, max: this.x1 });
      if (changed) this.rebuild();
    }

    setSize(w, h) { if (w > 0 && h > 0) this.u.setSize({ width: w, height: h }); }

    plotPx() {
      const o = this.u.over;
      return { w: Math.max(16, o.clientWidth || 16), h: Math.max(1, o.clientHeight || 1) };
    }

    _onSelect(u) {
      const s = u.select;
      if (s.width > 4 && this.o.onRangeSelected && !this.zeroSpan) {
        this.o.onRangeSelected(u.posToVal(s.left, "x"), u.posToVal(s.left + s.width, "x"));
      }
      if (s.width > 0) u.setSelect({ left: 0, top: 0, width: 0, height: 0 }, false);
    }

    _wire() {
      const u = this.u;
      const over = u.over;
      const rootEl = u.root;
      let down = null;
      over.addEventListener("mousedown", (e) => { if (e.button === 0) down = [e.clientX, e.clientY]; });
      over.addEventListener("mouseup", (e) => {
        if (e.button !== 0 || !down) return;
        const moved = Math.hypot(e.clientX - down[0], e.clientY - down[1]);
        down = null;
        if (moved < 4) {
          const r = over.getBoundingClientRect();
          this.click(e.clientX - r.left, e.clientY - r.top);
        }
      });
      over.addEventListener("mousemove", (e) => {
        const r = over.getBoundingClientRect();
        this.hover(e.clientX - r.left, e.clientY - r.top, e);
      });
      over.addEventListener("mouseleave", () => this.hover(null, null, null));
      rootEl.addEventListener("wheel", (e) => {
        const r = over.getBoundingClientRect();
        const x = e.clientX - r.left;
        const y = e.clientY - r.top;
        if (y < 0 || y > r.height) return;
        const steps = e.deltaY < 0 ? 1 : -1;
        if (x < 0) { e.preventDefault(); this.leftWheel(steps, e.ctrlKey); }
        else if (x <= r.width && !this.zeroSpan) { e.preventDefault(); if (this.o.onFreqWheel) this.o.onFreqWheel(u.posToVal(x, "x"), steps); }
      }, { passive: false });
      rootEl.addEventListener("mousedown", (e) => {
        const r = over.getBoundingClientRect();
        const x = e.clientX - r.left;
        const y = e.clientY - r.top;
        if (e.button === 0 && x < 0 && y >= 0 && y <= r.height) {
          this._drag = { y0: y, h: r.height, top: r.top };
          this.leftDrag("start", y, this._drag);
          e.preventDefault();
        }
      });
    }

    _wireWindow() {
      window.addEventListener("mousemove", (e) => { const d = this._drag; if (d) this.leftDrag("move", e.clientY - d.top, d); });
      window.addEventListener("mouseup", (e) => { const d = this._drag; if (d) { this._drag = null; this.leftDrag("end", e.clientY - d.top, d); } });
      new ResizeObserver(() => { this.setSize(this.el.clientWidth, this.el.clientHeight); this.resized(); }).observe(this.el);
    }

    click() {}
    hover() {}
    leftWheel() {}
    leftDrag() {}
    resized() {}
    rebuild() {}
    // 絵（行・升目の色）が変わった: 次に描くときに塗り直す。目盛だけの描き直しでは塗り直さない（重い）
    repaint() {
      this.dirty = true;
      // 縦軸の目盛（ウォーターフォールの時刻）は行で決まるので、目盛も計算し直す
      root.GSOnFrame(this, "_frame", () => this.u.redraw(false, true));
    }
    destroy() { this.u.destroy(); }
  }

  class Waterfall extends Base {
    constructor(el, opts) {
      super(el, opts, { grid: false });
      this.rows = []; // 新しい順（0 が一番上）
      this.shown = [];
      this.follow = true;
      this.lo = -110;
      this.hi = -20;
      this.grid = null; // Float32Array（行 × 列）
      this.gridW = 0;
      this.gridN = 0;
      this.cursorT = null;
      this.band = null; // 時刻の軸で選んでいる範囲（行の番号）
      this.off = document.createElement("canvas");
    }

    yLabel() { return root.GSTexts.w("axis_time"); }
    yRange() { return [0, Math.max(1, this.gridN)]; }
    rowPx() { return this.follow ? ROW_PX : this.plotPx().h / Math.max(1, this.shown.length); }
    visibleRows() { return Math.max(1, Math.floor(this.plotPx().h / ROW_PX)); }
    pixelRows() { return Math.max(1, Math.floor(this.plotPx().h)); }
    // 行 i（0 が一番上）の縦の値（uPlot の y は下から上へ増える）
    yOf(i) { return this.gridN - i; }
    rowAt(py) {
      const n = this.gridN;
      const h = this.plotPx().h;
      const i = Math.floor((py / h) * n);
      return i >= 0 && i < this.shown.length ? i : -1;
    }

    ySplits() {
      const rows = this.shown;
      if (!rows.length) return [];
      if (rows.length < 2) return [this.yOf(0)];
      const span = Math.abs(rows[0].t - rows[rows.length - 1].t);
      const step = tickStep(span / Math.max(1, rows.length * this.rowPx()));
      const out = [];
      let prev = null;
      rows.forEach((r, i) => {
        const k = Math.floor(r.t / step);
        if (prev !== null && k !== prev) out.push(this.yOf(i));
        prev = k;
      });
      // 区切りが1つも無い（短い時間の行だけ）ときは一番上の行の時刻を出す（Qt と同じ）
      return out.length ? out : [this.yOf(0)];
    }

    yValues(splits) {
      return splits.map((v) => {
        const i = Math.round(this.gridN - v);
        return i >= 0 && i < this.shown.length ? hhmmss(this.shown[i].t) : "";
      });
    }

    setRange(lo, hi) { if (lo !== this.lo || hi !== this.hi) { this.lo = lo; this.hi = hi; this.repaint(); } }
    setCursor(t) { if (t !== this.cursorT) { this.cursorT = t; this.repaint(); } }

    setRows(rowsOldestFirst, follow) {
      this.follow = !!follow;
      this.rows = rowsOldestFirst.slice().reverse();
      this.rebuild();
    }

    append(rowsOldestFirst) {
      for (const r of rowsOldestFirst) this.rows.unshift(r);
      const keep = this.visibleRows() * 2;
      if (this.rows.length > keep) this.rows.length = keep;
      if (!this.follow) return;
      const n = this.visibleRows();
      const w = this.plotPx().w;
      if (!this.grid || this.gridW !== w || this.gridN !== n || rowsOldestFirst.length > 4) { this.rebuild(); return; }
      const k = rowsOldestFirst.length;
      this.grid.copyWithin(k * w, 0, (n - k) * w);
      rowsOldestFirst.slice().reverse().forEach((r, i) => this.grid.set(resample(r, this.x0, this.x1, w), i * w));
      this.shown = this.rows.slice(0, n);
      this.repaint();
    }

    rebuild() {
      const w = this.plotPx().w;
      let n;
      if (this.follow) { n = this.visibleRows(); this.shown = this.rows.slice(0, n); }
      else { n = Math.max(1, this.rows.length); this.shown = this.rows; }
      const g = new Float32Array(n * w).fill(NaN);
      this.shown.forEach((r, i) => { if (i < n) g.set(resample(r, this.x0, this.x1, w), i * w); });
      this.grid = g;
      this.gridW = w;
      this.gridN = n;
      this.u.setScale("y", { min: 0, max: n });
      this.repaint();
    }

    resized() {
      if (this.plotPx().w !== this.gridW || (this.follow && this.visibleRows() !== this.gridN)) this.rebuild();
      if (this.o.onResized) this.o.onResized();
    }

    _draw(u) {
      const ctx = u.ctx;
      const b = u.bbox;
      if (!this.grid || !this.lut) return;
      const w = this.gridW;
      const n = this.gridN;
      const off = this.off;
      if (this.dirty || off.width !== w || off.height !== n) {
        this.dirty = false;
        off.width = w;
        off.height = n;
        const octx = off.getContext("2d");
        const img = octx.createImageData(w, n);
        const d = img.data;
        const lut = this.lut;
        const lo = this.lo;
        const span = Math.max(1e-6, this.hi - this.lo);
        const g = this.grid;
        for (let k = 0; k < g.length; k++) {
          const v = g[k];
          if (!Number.isFinite(v)) continue;
          let idx = Math.round(((v - lo) / span) * 255);
          idx = idx < 0 ? 0 : idx > 255 ? 255 : idx;
          const p = k * 4;
          d[p] = lut[idx * 3];
          d[p + 1] = lut[idx * 3 + 1];
          d[p + 2] = lut[idx * 3 + 2];
          d[p + 3] = 255;
        }
        octx.putImageData(img, 0, 0);
      }
      ctx.save();
      ctx.beginPath();
      ctx.rect(b.left, b.top, b.width, b.height);
      ctx.clip();
      ctx.imageSmoothingEnabled = false;
      ctx.drawImage(off, 0, 0, w, n, b.left, b.top, b.width, b.height);
      const dpr = b.height / Math.max(1, this.plotPx().h);
      const rowH = b.height / Math.max(1, n);
      // 条件の変わった境目（P §3）: 行の間に点線と、新しい側の条件
      const c = this.colors;
      ctx.font = `${Math.round(11 * dpr)}px sans-serif`;
      ctx.textBaseline = "bottom";
      let count = 0;
      for (let i = 0; i < this.shown.length - 1 && count < 8; i++) {
        const a = this.shown[i].cond;
        const z = this.shown[i + 1].cond;
        if (!a || JSON.stringify(a) === JSON.stringify(z)) continue;
        const y = b.top + (i + 1) * rowH;
        ctx.strokeStyle = c.text_dim;
        ctx.setLineDash([4 * dpr, 3 * dpr]);
        ctx.beginPath();
        ctx.moveTo(b.left, y);
        ctx.lineTo(b.left + b.width, y);
        ctx.stroke();
        ctx.setLineDash([]);
        const text = this.o.boundary ? this.o.boundary(a) : "";
        if (text) {
          const tw = ctx.measureText(text).width;
          ctx.fillStyle = c.graph_bg;
          ctx.fillRect(b.left, y - 14 * dpr, tw + 6 * dpr, 14 * dpr);
          ctx.fillStyle = c.text;
          ctx.fillText(text, b.left + 3 * dpr, y);
        }
        count++;
      }
      // 再生のカーソル・過去のトレースの行
      if (this.cursorT != null) {
        const i = this.shown.findIndex((r) => r.t <= this.cursorT + 1e-6);
        if (i >= 0) {
          const y = b.top + (i + 0.5) * rowH;
          ctx.strokeStyle = c.accent;
          ctx.lineWidth = 1.5 * dpr;
          ctx.beginPath();
          ctx.moveTo(b.left, y);
          ctx.lineTo(b.left + b.width, y);
          ctx.stroke();
          ctx.lineWidth = 1;
        }
      }
      if (this.band) {
        const [i0, i1] = this.band;
        ctx.fillStyle = "rgba(79,163,255,0.24)";
        ctx.fillRect(b.left, b.top + Math.min(i0, i1) * rowH, b.width, (Math.abs(i1 - i0) + 1) * rowH);
      }
      ctx.restore();
    }

    rowValue(r, x) {
      const step = r.step_hz || 1;
      const j = Math.round((x - r.start_hz) / step);
      if (j < 0 || j >= r.values.length) return { level: null, j: null };
      const v = r.values[j];
      return { level: Number.isFinite(v) ? v : null, j };
    }

    click(px, py) {
      const i = this.rowAt(py);
      if (i >= 0 && this.o.onPick) this.o.onPick(this.shown[i].t, this.shown[i].pos || null);
    }

    hover(px, py, e) {
      if (!this.o.onHover) return;
      if (px === null || (e && e.buttons)) { this.o.onHover(null); return; }
      const i = this.rowAt(py);
      if (i < 0) { this.o.onHover(null); return; }
      const r = this.shown[i];
      const x = this.u.posToVal(px, "x");
      const { level, j } = this.rowValue(r, x);
      this.o.onHover({ row: r, j, level, x, cx: e.clientX, cy: e.clientY });
    }

    leftWheel(steps) { if (this.o.onTimeScroll) this.o.onTimeScroll(steps); }

    leftDrag(phase, py, drag) {
      const a = this.rowAt(Math.max(0, Math.min(drag.y0, drag.h - 1)));
      const z = this.rowAt(Math.max(0, Math.min(py, drag.h - 1)));
      if (phase === "end") {
        this.band = null;
        this.repaint();
        if (Math.abs(py - drag.y0) >= 6 && a >= 0 && z >= 0 && this.o.onTimeRange) {
          const newest = this.shown[Math.min(a, z)];
          const oldest = this.shown[Math.max(a, z)];
          this.o.onTimeRange(oldest.t, newest.t_end != null ? newest.t_end : newest.t);
        }
        return;
      }
      if (a >= 0 && z >= 0) { this.band = [a, z]; this.repaint(); }
    }
  }

  class Density extends Base {
    constructor(el, opts) {
      super(el, opts, { grid: true });
      this.ref = 0;
      this.div = 10;
      this.unit = "dBm";
      this.d = null;
      this.off = document.createElement("canvas");
    }

    yLabel() { return root.GSTexts.w("axis_level", { unit: this.unit }); }
    yRange() { return [this.ref - DIVISIONS * this.div, this.ref]; }
    ySplits() { const out = []; for (let i = DIVISIONS; i >= 0; i--) out.push(this.ref - i * this.div); return out; }
    // 目盛の数字はグラフと同じ桁（|Γ|・SWR は1目盛を書き分けられる桁: 設計R §3。graph.js の fmtTick と同じ）
    yValues(splits) {
      const d = this.decimals || 1;
      return splits.map((v) => {
        if (d <= 1) return root.GSUnits.levelText(v);
        const s = Number(v).toFixed(d);
        return root.GSUnits.minusSign(Number(s) === 0 ? Number(0).toFixed(d) : s);
      });
    }

    // ref は一番上（|Γ|・SWR は下端 + 10 目盛）。グラフの縦軸と同じ範囲（設計P §4）
    setY(ref, div, unit, decimals) {
      if (ref === this.ref && div === this.div && (unit || this.unit) === this.unit
          && (decimals || 1) === (this.decimals || 1)) return;
      this.ref = ref;
      this.div = div;
      this.unit = unit || this.unit;
      this.decimals = decimals || 1;
      this._labels();
      this.u.setScale("y", { min: ref - DIVISIONS * div, max: ref });
    }

    // 描かない理由（行にするトレースの単位が縦軸と違う: Qt の DensityView.set_reason と同じ）。理由がある間は絵を出さない
    setReason(why) {
      const r = why || null;
      if (r === (this.reason || null)) return;
      this.reason = r;
      this.u.redraw(false);
    }

    // 届いた密度を描く。画面に出ていない間は塗らずに持つだけ（出したときに塗る）
    setDensity(d) {
      this.d = d;
      this.dirty = true;
      if (this.el.offsetParent !== null) root.GSOnFrame(this, "_frame", () => this.u.redraw(false));
    }

    _draw(u) {
      const d = this.d;
      if (this.reason || !d || !d.values || !d.bins || !d.cols || !this.lut) return;
      const off = this.off;
      if (this.dirty) {
        this.dirty = false;
        off.width = d.cols;
        off.height = d.bins;
        const octx = off.getContext("2d");
        const img = octx.createImageData(d.cols, d.bins);
        const px = img.data;
        const lut = this.lut;
        const v = d.values;
        for (let k = 0; k < v.length; k++) {
          const a = v[k];
          if (!a) continue;
          const idx = Math.min(255, Math.max(0, Math.floor(((a - 1) * 255) / 254)));
          const p = k * 4;
          px[p] = lut[idx * 3];
          px[p + 1] = lut[idx * 3 + 1];
          px[p + 2] = lut[idx * 3 + 2];
          px[p + 3] = 255;
        }
        octx.putImageData(img, 0, 0);
      }
      let x0 = d.start_hz;
      let x1 = d.stop_hz;
      if (d.zero_span) { x0 = 0; x1 = Math.max(1, d.cols - 1); }
      if (!(x1 > x0)) x1 = x0 + 1;
      const left = u.valToPos(x0, "x", true);
      const right = u.valToPos(x1, "x", true);
      const top = u.valToPos(d.level_hi, "y", true);
      const bottom = u.valToPos(d.level_lo, "y", true);
      const ctx = u.ctx;
      const b = u.bbox;
      ctx.save();
      ctx.beginPath();
      ctx.rect(b.left, b.top, b.width, b.height);
      ctx.clip();
      ctx.imageSmoothingEnabled = false;
      ctx.drawImage(off, 0, 0, d.cols, d.bins, left, top, right - left, bottom - top);
      ctx.restore();
    }

    leftWheel(steps, ctrl) { if (this.o.onLevelWheel) this.o.onLevelWheel(steps, ctrl); }
    leftDrag(phase, py, drag) {
      if (!this.o.onLevelDrag) return;
      if (phase === "start") this.o.onLevelDrag(true, 0);
      else this.o.onLevelDrag(false, (py - drag.y0) / drag.h);
    }
  }

  root.GSWaterfall = Waterfall;
  root.GSDensity = Density;
  root.GSWaterfallUtil = { resample, tickStep, ROW_PX };
})(window);
