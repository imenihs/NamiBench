// グラフ（設計J §3・§4.1。Qt の ui_qt/graph.py と同じ操作）。uPlot は目盛・格子・範囲の選択だけに使い、
// トレースは描画の段で自分で描く（参照トレースは周波数軸が違うので、uPlot の共通の横軸に載らないため）。
// - 横にドラッグ → その範囲を Start/Stop に
// - 周波数軸（グラフの中）でホイール → カーソルの位置を中心に拡大・縮小
// - レベル軸の上でホイール → REF を1目盛（Ctrl で dB/div を1段）、ドラッグ → REF を連続で
// - クリック → マーカーを置く、右クリック → 名前の付いた操作のメニュー
// 横軸の位置は機器の周波数のまま。周波数オフセット（設計O）は目盛の数字と軸の名前だけに足す
(function (root) {
  "use strict";

  const DIVISIONS = 10;
  // 塗りつぶしの濃さ（Qt の plot_layers._apply_fill のアルファ 70/255 と同じ）
  const FILL_ALPHA = 70 / 255;
  // マーカーの機能の線の濃さ（G §3.6: マーカーの色の不透明度 約 35%。Qt の plot_layers.FUNCTION_LINE_ALPHA と同じ）
  const FUNCTION_LINE_ALPHA = 89 / 255;
  const FUNCTION_LINE_DASH = [2, 3];

  function xUnit(span) {
    const a = Math.abs(span);
    return a >= 1e9 ? [1e9, "GHz"] : a >= 1e6 ? [1e6, "MHz"] : a >= 1e3 ? [1e3, "kHz"] : [1, "Hz"];
  }

  // 目盛の数字は REF の欄と同じ形（units.js の levelText）
  function fmtLevel(v) { return root.GSUnits.levelText(v); }

  class Graph {
    constructor(el, opts) {
      this.el = el;
      this.o = opts;
      this.colors = opts.colors;
      this.x0 = 0;
      this.x1 = 1;
      this.zeroSpan = false;
      this.xOffset = 0; // 周波数オフセット（Hz）と、その文字（+10 GHz）。目盛と軸の名前に使う
      this.xOffsetText = "";
      this.ref = 0;
      this.div = 10;
      this.unit = "dBm";
      this.traces = [];
      this.bands = [];
      this.markers = [];
      this.selectedMarker = null;
      this.functionRows = []; // マーカーの機能の線（マーカー表の機能の行の line: G §3.6）
      this.xy = null;
      this.zeroLine = false;
      this.mouse = null; // グラフの上のマウスの位置（M キー用）
      this._build(opts.width || el.clientWidth || 600, opts.height || el.clientHeight || 300);
      if (!opts.static) this._wire();
    }

    _build(w, h) {
      const c = this.colors;
      const self = this;
      const opts = {
        width: w,
        height: h,
        // 上に余白を取り、一番上の目盛（REF レベル）の数字を切らない（J §4.3）
        padding: [14, 18, 0, 0],
        legend: { show: false },
        pxAlign: 0,
        scales: {
          x: { time: false, auto: false, range: () => [self.x0, self.x1] },
          y: { auto: false, range: () => [self.ref - DIVISIONS * self.div, self.ref] },
        },
        axes: [
          {
            stroke: c.text,
            grid: { stroke: c.grid, width: 1 },
            ticks: { stroke: c.axis, width: 1 },
            label: "",
            labelSize: 18,
            font: "12px sans-serif",
            labelFont: "12px sans-serif",
            values: (u, splits) => {
              if (self.zeroSpan) return splits.map((v) => String(Math.round(v)));
              const [k] = xUnit(self.x1 - self.x0);
              return splits.map((v) => String(+((v + self.xOffset) / k).toPrecision(10)));
            },
          },
          {
            stroke: c.text,
            grid: { stroke: c.grid, width: 1 },
            ticks: { stroke: c.axis, width: 1 },
            label: "",
            labelSize: 18,
            size: 56,
            font: "12px sans-serif",
            labelFont: "12px sans-serif",
            // 目盛は REF から dB/div ごとに 10 区画（J §4.1）
            splits: () => {
              const out = [];
              for (let i = DIVISIONS; i >= 0; i--) out.push(self.ref - i * self.div);
              return out;
            },
            values: (u, splits) => splits.map(fmtLevel),
          },
        ],
        series: [{}, { stroke: "transparent", points: { show: false } }],
        cursor: {
          drag: { x: !this.o.static, y: false, setScale: false },
          points: { show: false },
          x: !this.o.static,
          y: false,
        },
        select: { show: !this.o.static },
        hooks: {
          draw: [(u) => self._draw(u)],
          setSelect: [(u) => self._onSelect(u)],
        },
      };
      this.u = new uPlot(opts, [[0, 1], [null, null]], this.el);
      this._applyLabels();
    }

    _applyLabels() {
      const [, unit] = xUnit(this.x1 - this.x0);
      // 軸の名前は texts の表（Qt と同じ語）
      const X = root.GSTexts;
      // 換算中は軸の名前で示す（設計O）。単位の付け方は換算しない軸の名前（axis_freq）と同じ形
      if (this.zeroSpan) this.u.axes[0].label = X.w("axis_zero_span");
      else if (this.xOffset) this.u.axes[0].label = X.fmtText(X.get("VIEW_TEXTS", "axis_freq_offset"), { off: this.xOffsetText }) + ` (${unit})`;
      else this.u.axes[0].label = X.w("axis_freq", { unit });
      this.u.axes[1].label = X.w("axis_level", { unit: this.unit });
    }

    setSize(w, h) {
      if (w > 0 && h > 0) this.u.setSize({ width: w, height: h });
    }

    setX(start, stop, zeroSpan) {
      this.x0 = start;
      this.x1 = stop > start ? stop : start + 1;
      this.zeroSpan = !!zeroSpan;
      this._applyLabels();
      this.u.setScale("x", { min: this.x0, max: this.x1 });
    }

    // 周波数オフセット（設計O）。off は Hz、text は軸の名前に入れる符号付きの文字
    setXOffset(off, text) {
      if (off === this.xOffset && text === this.xOffsetText) return;
      this.xOffset = off || 0;
      this.xOffsetText = text || "";
      this._applyLabels();
      this.u.redraw(false, true);
    }

    // 配色を入れ替える（J §4.4: 変えた色はその場で画面に出る）。uPlot の軸の色は関数で持たせ、描き直す
    setColors(c) {
      this.colors = c;
      for (const a of this.u.axes) {
        a.stroke = () => c.text;
        if (a.grid) a.grid.stroke = () => c.grid;
        if (a.ticks) a.ticks.stroke = () => c.axis;
      }
      this.u.redraw(false, true);
    }

    setY(ref, div, unit) {
      this.ref = ref;
      this.div = div;
      this.unit = unit || this.unit;
      this.zeroLine = this.unit === "dB";
      this._applyLabels();
      this.u.setScale("y", { min: ref - DIVISIONS * div, max: ref });
    }

    setTraces(list) { this.traces = list; this.u.redraw(false); }
    // 機器の範囲の外・意味の無い区間の帯（J §4.8）: [{start_hz, stop_hz, label, fill}]
    setBands(list) { this.bands = list || []; this.u.redraw(false); }
    setMarkers(rows, selected) { this.markers = rows || []; this.selectedMarker = selected; this.u.redraw(false); }
    setXY(xy) { this.xy = xy; this.u.redraw(false); }
    // マーカーの機能の線（G §3.6）: マーカー表の行のうち line を持つもの。出さないときは空を渡す
    setFunctionLines(rows) { this.functionRows = (rows || []).filter((r) => r.line); this.u.redraw(false); }

    // ---- 描く

    _draw(u) {
      const ctx = u.ctx;
      const b = u.bbox;
      ctx.save();
      ctx.beginPath();
      ctx.rect(b.left, b.top, b.width, b.height);
      ctx.clip();
      const dpr = b.width / Math.max(1, u.over ? u.over.clientWidth || b.width : b.width);
      if (!this.zeroSpan) this._drawBands(u, dpr || 1);
      if (this.zeroLine) this._hline(u, 0, this.colors.zero_db, [], 1.5);
      for (const t of this.traces) this._drawTrace(u, t);
      if (!this.zeroSpan) this._drawFunctionLines(u);
      this._drawCursors(u);
      this._drawMarkers(u, dpr || 1);
      ctx.restore();
    }

    // 帯は薄く塗ってトレースの下に敷く（トレースを隠さない）。説明は帯の左上に控えめな字で出し、横書きが
    // 入らない狭い帯は縦に、字も入らないほど狭い帯は出さない（Qt の mark_bands.py と同じ）
    _drawBands(u, dpr) {
      const ctx = u.ctx;
      const b = u.bbox;
      const s = Math.max(1, dpr);
      const pad = 4 * s;
      ctx.font = `${Math.round(11 * s)}px sans-serif`;
      ctx.textBaseline = "top";
      ctx.textAlign = "left";
      for (const m of this.bands) {
        const x0 = Math.max(b.left, u.valToPos(m.start_hz, "x", true));
        const x1 = Math.min(b.left + b.width, u.valToPos(m.stop_hz, "x", true));
        if (!(x1 > x0)) continue;
        ctx.globalAlpha = 0.14;
        ctx.fillStyle = m.fill;
        ctx.fillRect(x0, b.top, x1 - x0, b.height);
        ctx.globalAlpha = 0.35;
        ctx.strokeStyle = m.fill;
        ctx.lineWidth = 1;
        ctx.strokeRect(x0 + 0.5, b.top, Math.max(0, x1 - x0 - 1), b.height);
        ctx.globalAlpha = 1;
        const w = x1 - x0;
        if (w < 14 * s || !m.label) continue;
        ctx.fillStyle = m.fill;
        if (w >= ctx.measureText(m.label).width + 2 * pad) {
          ctx.fillText(m.label, x0 + pad, b.top + pad);
        } else {
          // 右へ90度回して、帯の左端に沿って上から下へ読む
          ctx.save();
          ctx.translate(x0 + pad, b.top + pad);
          ctx.rotate(Math.PI / 2);
          ctx.textBaseline = "bottom";
          ctx.fillText(m.label, 0, 0);
          ctx.restore();
        }
      }
    }

    _hline(u, level, color, dash, width) {
      const ctx = u.ctx;
      const b = u.bbox;
      const y = u.valToPos(level, "y", true);
      if (y < b.top - 1 || y > b.top + b.height + 1) return;
      ctx.strokeStyle = color;
      ctx.lineWidth = width || 1;
      ctx.setLineDash(dash || []);
      ctx.beginPath();
      ctx.moveTo(b.left, y);
      ctx.lineTo(b.left + b.width, y);
      ctx.stroke();
      ctx.setLineDash([]);
    }

    _vline(u, x, color, dash) {
      const ctx = u.ctx;
      const b = u.bbox;
      const px = u.valToPos(x, "x", true);
      if (px < b.left - 1 || px > b.left + b.width + 1) return;
      ctx.strokeStyle = color;
      ctx.lineWidth = 1;
      ctx.setLineDash(dash || []);
      ctx.beginPath();
      ctx.moveTo(px, b.top);
      ctx.lineTo(px, b.top + b.height);
      ctx.stroke();
      ctx.setLineDash([]);
    }

    // 画素より点が多いときは、画素の列ごとに最大値（最小保持のトレースは最小値）で間引く（設計F §5.5）。
    // 描き方が塗りつぶし（旧 Line/Dot・設計O）のときは、線の下からグラフの下端までをトレースの色で薄く塗る（Qt と同じ）
    _drawTrace(u, t) {
      const ctx = u.ctx;
      const b = u.bbox;
      const n = t.y.length;
      if (!n) return;
      const useMin = !!t.minHold;
      const segments = [[0, n, t.color]];
      if (t.head > 0 && t.dimColor) segments.splice(0, 1, [0, Math.min(n, t.head + 1), t.dimColor], [t.head, n, t.color]);
      for (const [i0, i1, color] of segments) {
        // 値の無い点で途切れる、つながった線の並び（[x0, y0, x1, y1, ...]）
        const runs = [];
        let run = null;
        let col = null;
        let agg = null;
        const flush = () => {
          if (col === null) return;
          if (agg === null) { run = null; return; }
          if (!run) { run = []; runs.push(run); }
          run.push(col, u.valToPos(agg, "y", true));
        };
        for (let i = i0; i < i1; i++) {
          const px = Math.round(u.valToPos(t.x(i), "x", true));
          const yv = t.y[i];
          const v = Number.isFinite(yv) ? yv : null;
          if (px !== col) {
            flush();
            col = px;
            agg = v;
            if (v === null) run = null;
          } else if (v !== null && agg !== null) {
            agg = useMin ? Math.min(agg, v) : Math.max(agg, v);
          } else if (v === null) {
            // 値の無い点は途切れにする（数値を作らない: 設計E §6.4）
            flush();
            agg = null;
            run = null;
          }
        }
        flush();
        if (t.fill) {
          const bottom = b.top + b.height;
          ctx.globalAlpha = FILL_ALPHA;
          ctx.fillStyle = color;
          for (const r of runs) {
            ctx.beginPath();
            ctx.moveTo(r[0], bottom);
            for (let k = 0; k < r.length; k += 2) ctx.lineTo(r[k], r[k + 1]);
            ctx.lineTo(r[r.length - 2], bottom);
            ctx.closePath();
            ctx.fill();
          }
          ctx.globalAlpha = 1;
        }
        ctx.strokeStyle = color;
        ctx.lineWidth = t.width || 1.25;
        ctx.setLineDash(t.dash || []);
        ctx.beginPath();
        for (const r of runs) {
          ctx.moveTo(r[0], r[1]);
          for (let k = 2; k < r.length; k += 2) ctx.lineTo(r[k], r[k + 1]);
        }
        ctx.stroke();
        ctx.setLineDash([]);
      }
    }

    // マーカーの機能の結果の線（G §3.6。Qt の plot_layers.MarkerLayer と同じ）: 横線 = レベル、縦線 = 交点の周波数
    // （交点が無い側は行に無い）。マーカーの色（選んでいるマーカーは強調の色）を薄くした点線。つまめない・読み値に出ない
    _drawFunctionLines(u) {
      const c = this.colors;
      const ctx = u.ctx;
      ctx.globalAlpha = FUNCTION_LINE_ALPHA;
      for (const r of this.functionRows) {
        const color = r.parent != null && r.parent === this.selectedMarker ? c.accent : c.marker;
        this._hline(u, r.line.level, color, FUNCTION_LINE_DASH, 1);
        for (const f of r.line.freqs_hz || []) this._vline(u, f, color, FUNCTION_LINE_DASH);
      }
      ctx.globalAlpha = 1;
    }

    _drawCursors(u) {
      const c = this.colors;
      for (const m of this.markers) {
        for (const r of m.freq_offsets || []) if (r.freq_hz != null) this._vline(u, r.freq_hz, c.marker, [4, 4]);
        for (const r of m.level_offsets || []) if (r.level != null) this._hline(u, r.level, c.marker, [4, 4]);
      }
      const xy = this.xy;
      if (!xy) return;
      for (const k of ["x1_hz", "x2_hz"]) if (xy[k] != null) this._vline(u, xy[k], c.accent, [2, 3]);
      for (const k of ["y1", "y2"]) if (xy[k] != null) this._hline(u, xy[k], c.accent, [2, 3]);
    }

    _drawMarkers(u, dpr) {
      const ctx = u.ctx;
      const c = this.colors;
      const s = Math.max(1, dpr);
      ctx.font = `${Math.round(12 * s)}px sans-serif`;
      ctx.textAlign = "center";
      ctx.textBaseline = "bottom";
      for (const m of this.markers) {
        if (m.freq_hz == null || m.level == null) continue;
        const px = u.valToPos(m.freq_hz, "x", true);
        const py = u.valToPos(m.level, "y", true);
        const sel = m.id === this.selectedMarker;
        ctx.fillStyle = sel ? c.accent : c.marker;
        ctx.beginPath();
        ctx.moveTo(px, py - 2 * s);
        ctx.lineTo(px - 5 * s, py - 11 * s);
        ctx.lineTo(px + 5 * s, py - 11 * s);
        ctx.closePath();
        ctx.fill();
        ctx.fillText(m.kind + m.number, px, py - 12 * s);
      }
    }

    // ---- 操作

    _onSelect(u) {
      const s = u.select;
      if (s.width > 4 && this.o.onRangeSelected && !this.zeroSpan) {
        const a = u.posToVal(s.left, "x");
        const b = u.posToVal(s.left + s.width, "x");
        this.o.onRangeSelected(a, b);
      }
      if (s.width > 0) u.setSelect({ left: 0, top: 0, width: 0, height: 0 }, false);
    }

    _wire() {
      const u = this.u;
      const over = u.over;
      const rootEl = u.root;
      rootEl.tabIndex = 0; // グラフにいるときだけ効くキー（J §4.6）のためにフォーカスを持てるようにする
      let down = null;
      over.addEventListener("mousedown", (e) => {
        if (e.button === 0) down = [e.clientX, e.clientY];
        rootEl.focus({ preventScroll: true });
      });
      over.addEventListener("mouseup", (e) => {
        if (e.button !== 0 || !down) return;
        const moved = Math.hypot(e.clientX - down[0], e.clientY - down[1]);
        down = null;
        if (moved < 4 && this.o.onClick) {
          const r = over.getBoundingClientRect();
          this.o.onClick(u.posToVal(e.clientX - r.left, "x"), u.posToVal(e.clientY - r.top, "y"));
        }
      });
      over.addEventListener("mousemove", (e) => {
        const r = over.getBoundingClientRect();
        // cx・cy は画面の位置（マウスの位置の読み値をカーソルの近くに出すため）。buttons は押しているボタン
        this.mouse = { x: u.posToVal(e.clientX - r.left, "x"), y: u.posToVal(e.clientY - r.top, "y"), cx: e.clientX, cy: e.clientY, buttons: e.buttons };
        if (this.o.onHover) this.o.onHover(this.mouse);
      });
      over.addEventListener("mouseleave", () => { this.mouse = null; if (this.o.onHover) this.o.onHover(null); });
      over.addEventListener("contextmenu", (e) => {
        e.preventDefault();
        const r = over.getBoundingClientRect();
        if (this.o.onContext) {
          this.o.onContext(u.posToVal(e.clientX - r.left, "x"), u.posToVal(e.clientY - r.top, "y"), e.clientX, e.clientY);
        }
      });
      // ホイール: グラフの中は周波数の拡大・縮小、左の軸の上は REF（Ctrl で dB/div）
      rootEl.addEventListener("wheel", (e) => {
        const r = over.getBoundingClientRect();
        const x = e.clientX - r.left;
        const y = e.clientY - r.top;
        if (y < 0 || y > r.height) return;
        const steps = e.deltaY < 0 ? 1 : -1; // 奥へ回すと正
        if (x < 0) {
          e.preventDefault();
          if (this.o.onLevelWheel) this.o.onLevelWheel(steps, e.ctrlKey);
        } else if (x <= r.width && !this.zeroSpan) {
          e.preventDefault();
          if (this.o.onFreqWheel) this.o.onFreqWheel(u.posToVal(x, "x"), steps);
        }
      }, { passive: false });
      // 左の軸のドラッグで REF を連続で動かす
      let drag = null;
      rootEl.addEventListener("mousedown", (e) => {
        const r = over.getBoundingClientRect();
        const x = e.clientX - r.left;
        const y = e.clientY - r.top;
        if (e.button === 0 && x < 0 && y >= 0 && y <= r.height) {
          drag = { y0: e.clientY, h: r.height };
          if (this.o.onLevelDrag) this.o.onLevelDrag(true, 0);
          e.preventDefault();
        }
      });
      window.addEventListener("mousemove", (e) => {
        if (drag && this.o.onLevelDrag) this.o.onLevelDrag(false, (e.clientY - drag.y0) / drag.h);
      });
      window.addEventListener("mouseup", () => { drag = null; });
      new ResizeObserver(() => this.setSize(this.el.clientWidth, this.el.clientHeight)).observe(this.el);
    }

    destroy() { this.u.destroy(); }
  }

  Graph.DIVISIONS = DIVISIONS;
  root.GSGraph = Graph;
})(window);
