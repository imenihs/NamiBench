// Web のメイン画面（設計J §4.3 の案B。Qt の ui_qt/main_window.py と同じ構成・同じ語・同じ操作: J §5）。
// コアへは Web の口（api.js）だけを使う。状態の正本はコアの1つで、変更は全部の画面に state で届く（A §4.4）。
// 文言はここに書かない: 表は /api/v1/texts、状態から作る文はサーバーが Qt と同じ関数で作って送る（view.py）。
// 受信（RTL-SDR）・聞く・rtl_tcp の設定は app_rtl.js、メニューとキーは app_menu.js に分けて、ここで合わせる。
(function () {
  "use strict";

  const { createApp, markRaw } = Vue;
  const U = GSUnits;
  const X = GSTexts;
  const W = (k, kw) => X.w(k, kw);
  const call = GSApi.call;

  const DB_PER_DIV_CHOICES = [1, 2, 5, 10];
  // 周波数軸の上のホイール1段で Span を何倍にするか（奥へ回すと拡大 = Span を狭く。Qt と同じ）
  const ZOOM_STEP = 0.8;
  // マウスの位置の読み値を取り直す最短の間隔（ミリ秒）。1秒に30回まで（Qt の mouse_reading.py と同じ）
  const HOVER_MS = 34;
  // 参照トレース（ファイル）の色。J §4.4 に無いので、トレースの色を順に使い点線で描く（設計に無い判断）
  const FILE_COLORS = ["trace2", "trace3", "trace4", "trace1"];
  const PARTS = ["graph", "markers", "tab-trace", "tab-marker", "tab-view", "tab-source", "peaks"];

  function nowName() {
    const d = new Date();
    const p = (n) => String(n).padStart(2, "0");
    return `gigast_${d.getFullYear()}${p(d.getMonth() + 1)}${p(d.getDate())}_${p(d.getHours())}${p(d.getMinutes())}${p(d.getSeconds())}`;
  }
  function fmtLevel(v, unit) { return v == null ? "" : `${v.toFixed(2)} ${unit || ""}`.trim(); }

  const base = {
    data() {
      const part = new URLSearchParams(location.search).get("part");
      return {
        part: PARTS.includes(part) ? part : null,
        st: null,
        markers: { markers: [], xy: {}, rows: [] },
        info: {},
        colors: null,
        wsOpen: false,
        textsReady: false,
        notes: {},
        status: "",
        menuOpen: false,
        ctx: null,
        modal: null,
        tab: "trace",
        selectedMarker: null,
        traceNotes: {},
        fileHidden: {},
        progress: {},
        waiting: "",
        detached: {},
        peakList: null,
        peakTrace: 1,
        peakMargin: "10",
        topN: "3",
        off: { type: "freq", sign: "±", delta: "" },
        fn: { bw1: "3", bw2: "", thr: "", side: "below" },
        refs: [],
        refName: "",
        hover: null,
        hoverLines: null,
        ampLocal: null,
        splitPct: 70,
        sidePx: 340,
        traceVersion: 0,
      };
    },
    computed: {
      ui() { return (this.st && this.st.ui) || {}; },
      modes() { return X.list("MODES"); },
      modeReason() { return (code) => (this.ui.mode_reasons || {})[code] || null; },
      rbwGroups() { return this.ui.rbw || []; },
      rbwNote() {
        const s = this.sw;
        if (!s) return "";
        const g = this.rbwGroups.find((x) => x.choices.some((c) => c.hz === s.rbw_preference_hz));
        return g ? g.note : "";
      },
      sweepReasons() { return this.ui.sweep_reasons || {}; },
      divChoices() { return DB_PER_DIV_CHOICES; },
      sw() { return (this.st && this.st.sweep) || null; },
      run() { return (this.st && this.st.run) || {}; },
      conn() { return (this.st && this.st.connection) || {}; },
      connected() { return this.conn.state === "connected"; },
      running() { return ["continuous", "single"].includes(this.run.state); },
      amp() {
        const a = (this.st && this.st.amplitude) || {};
        const l = this.ampLocal || {};
        const pick = (k, d) => (l[k] !== undefined ? l[k] : a[k] != null ? a[k] : d);
        return { ref: pick("ref_level", 0), div: pick("db_per_div", 10), unit: a.unit };
      },
      axisUnit() {
        void this.traceVersion;
        for (const n of [1, 2, 3, 4]) {
          const t = this.$traces && this.$traces.get(String(n));
          if (t && this.traceShown(n)) return t.unit;
        }
        return this.amp.unit || this.ui.unit || "dBm";
      },
      // Center・Start・Stop は周波数オフセットを足して出す（設計O。Span は幅、SG は機器が出す周波数なので足さない）
      freqText() {
        const s = this.sw;
        if (!s) return { center: "", span: "", start: "", stop: "", sg: "" };
        const o = this.freqOff;
        return {
          center: U.freqFieldText(s.center_hz + o), span: U.freqFieldText(s.span_hz),
          start: U.freqFieldText(s.start_hz + o), stop: U.freqFieldText(s.stop_hz + o),
          sg: s.sg_hz != null ? U.freqFieldText(s.sg_hz) : "",
        };
      },
      // 負号は見間違えない字形「−」で出す（J §4.1）。打つときは「-」でも「−」でもよい
      refText() { return `${U.levelText(this.amp.ref)} ${this.axisUnit}`; },
      // REF レベルの一覧（J §4.1: +20〜−120 を 10dB 刻み。Qt の fields.REF_CHOICES と同じ）
      refChoices() {
        const out = [];
        for (let v = 20; v >= -120; v -= 10) out.push({ value: v, text: U.minusSign(v > 0 ? `+${v}` : `${v}`) + " " + this.axisUnit });
        return out;
      },
      // 上段の「直結を 0dB にする」（J §4.2。出すか・理由・文はサーバーの screen_model.ref_quick_view）
      refQuick() { return this.ui.ref_quick || { visible: false }; },
      spanLockText() {
        const s = this.sw;
        if (!s || s.span_lock_hz == null) return W("span_lock");
        return s.span_lock_hz !== s.span_hz ? W("span_locked_at", { span: U.formatFreq(s.span_lock_hz) }) : W("span_locked");
      },
      condLines() { return this.ui.cond || []; },
      freqOpsReason() {
        const s = this.sw;
        if (!s) return X.get("REASON_TEXTS", "no_sweep");
        if (s.step_hz === 0) return X.get("REASON_TEXTS", "zero_span_axis");
        return null;
      },
      statusItems() {
        const items = this.ui.status ? [...this.ui.status] : [];
        if (!this.wsOpen && this.textsReady) items.push({ key: "ws", text: X.fmt("status_ws_down"), cls: "warn" });
        if (this.readOnly) items.unshift({ key: "ro", text: this.readOnlyReason, cls: "warn" });
        return items;
      },
      tasks() {
        const labels = this.ui.tasks || {};
        return ((this.st && this.st.tasks) || []).map((t) => {
          const p = this.progress[t.task_id] || {};
          return { ...t, ...p, label: p.text || labels[t.task_id] || "" };
        });
      },
      traceRows() {
        const legend = this.ui.legend || {};
        return ((this.st && this.st.traces) || []).map((t) => ({ ...t, color: this.palette["trace" + t.number], legend: legend[String(t.number)] || "" }));
      },
      // 画面の配色はコアの配色（ユーザー設定の1つ。Qt と共有: J §4.4）。状態が届くまではダークの初期値
      palette() { return (this.st && this.st.palette && this.st.palette.colors) || (this.colors && this.colors.dark) || {}; },
      markerRows() { return this.markers.rows || []; },
      markerColumns() { return this.markers.columns || X.list("MARKER_TABLE_COLUMNS"); },
      selMarker() { return (this.markers.markers || []).find((m) => m.id === this.selectedMarker) || null; },
      files() { return (this.st && this.st.files) || []; },
      clipboardReason() { return window.isSecureContext && navigator.clipboard && window.ClipboardItem ? null : W("no_clipboard"); },
      partTitle() { return this.part ? W("part_" + this.part) : ""; },
      layoutStyle() { return { "--split": this.splitPct + "%", "--side": this.sidePx + "px" }; },
      tabList() { return ["trace", "marker", "view", "source"].map((t) => [t, W("tab_" + t)]); },
    },
    created() {
      this.$traces = new Map(); // トレースの値（大きいので Vue の反応の外に置く）
      try {
        const s = JSON.parse(localStorage.getItem("pygigast.layout") || "{}");
        if (s.splitPct) this.splitPct = s.splitPct;
        if (s.sidePx) this.sidePx = s.sidePx;
      } catch (e) { /* 読めなければ既定の並び */ }
    },
    async mounted() {
      try {
        await this.authFromHash();
        X.load(await GSApi.getJson("/api/v1/texts"));
        this.info = await GSApi.getJson("/api/v1/info");
        this.colors = this.info.palettes;
      } catch (e) { this.note(null, String(e)); }
      if (this.part) document.title = `pyGigaSt — ${this.partTitle}`;
      this.watchKeyStore();
      this.textsReady = true;
      this.$nextTick(() => this.makeGraph());
      this.stream = new GSApi.EventStream({
        onOpen: () => { this.wsOpen = true; },
        onClose: () => { this.wsOpen = false; },
        onEvent: (k, p) => this.onEvent(k, p),
      });
      this.stream.open();
      window.addEventListener("keydown", (e) => this.onKey(e));
      window.addEventListener("click", () => { this.ctx = null; });
      setInterval(() => this.pollDetached(), 1000);
    },
    methods: {
      // ------------------------------------------------------------ 命令
      async cmd(name, args = {}, opts = {}) {
        if (this.readOnlyBlocks(name)) return null;
        try {
          const r = await call(name, args);
          if (r && r.texts && r.texts.adjustments) this.showAdjustments(r.texts, r.alternative);
          if (opts.ok) opts.ok(r);
          return r;
        } catch (e) {
          if ((opts.quiet || []).includes(e.code)) return null;
          this.note(opts.field || null, e.text || X.get("ERRORS", e.code, e.code || String(e)));
          return null;
        }
      },
      sweep(args, field) { return this.cmd("set_sweep", args, { field }); },
      note(field, text, action) {
        if (field) this.notes = { ...this.notes, [field]: { text, action } };
        else this.status = `${new Date().toLocaleTimeString()} ${text}`;
      },
      clearNote(field) { const n = { ...this.notes }; delete n[field]; this.notes = n; },
      showAdjustments(t, alt) {
        const fields = { start_hz: "start", stop_hz: "stop", center_hz: "center", span_hz: "span", rbw_hz: "rbw", sg_hz: "sg", points: "points" };
        for (const a of t.adjustments) {
          const f = fields[a.field];
          let action = null;
          if (alt && a.rule && f) {
            // 規則で動いた欄の知らせに、反対の規則の結果を「その1回だけ」のボタンで添える（C §5.11 の 3）
            action = { label: t.alternative_label, run: () => this.applyAlternative(alt, f) };
          }
          if (f) this.note(f, a.text, action);
          else this.note(null, `${a.field_name}: ${a.text}`);
        }
      },
      async applyAlternative(alt, field) {
        // その1回だけの命令（固定 Span は変わらない: C §5.11 の 3）
        const r = await this.cmd("set_sweep", { start_hz: alt.start_hz, stop_hz: alt.stop_hz, edited: ["start_hz", "stop_hz"], one_shot: true }, { field });
        if (r && r.texts && r.texts.alternative_done) this.note(field, r.texts.alternative_done);
      },

      // ---- 周波数の欄
      commitFreq(field, key, text) {
        let hz;
        try { hz = U.parseFreqHz(text); } catch (e) { this.note(field, W("bad_freq")); return; }
        if (key === "sg_hz") { this.cmd("set_mode", { mode: this.sw ? this.sw.mode : "sg", sg_hz: hz }, { field }); return; }
        // 欄は換算後の周波数で打つので、機器の周波数へ戻して送る（Span は幅なので戻さない）
        if (key !== "span_hz") hz = this.deviceHz(hz);
        this.sweep({ [key]: hz }, field);
      },
      stepFreq(field, key, exp, dir) {
        if (!this.sw) return;
        // 欄の数字は MHz なので、10^exp MHz = 10^(exp+6) Hz
        const step = exp + 6 >= 0 ? 10 ** (exp + 6) : 1;
        const cur = key === "sg_hz" ? (this.sw.sg_hz || 0) : this.sw[key];
        const hz = cur + dir * step;
        if (key === "sg_hz") this.cmd("set_mode", { mode: this.sw.mode, sg_hz: hz }, { field });
        else this.sweep({ [key]: hz }, field);
      },
      toggleSpanLock() { this.sweep({ span_lock: this.sw.span_lock_hz == null }, "span"); },
      setRbw(hz) { if (hz !== "") this.sweep({ rbw_hz: Number(hz) }, "rbw"); },

      // ---- 縦軸（J §4.1。値はコアの振幅に持ち、全部の画面で同じにする）
      setAmp(args) {
        this.ampLocal = { ...(this.ampLocal || {}), ...args };
        this.applyY();
        this.ampPending = { ...(this.ampPending || {}), ...args };
        if (!this.ampBusy) this.flushAmp();
      },
      async flushAmp() {
        this.ampBusy = true;
        while (this.ampPending) {
          const a = this.ampPending;
          this.ampPending = null;
          await this.cmd("set_amplitude", a, { field: "ref" });
        }
        this.ampBusy = false;
        clearTimeout(this.ampTimer);
        this.ampTimer = setTimeout(() => { this.ampLocal = null; this.applyY(); }, 1000);
      },
      commitRef(text) {
        let v;
        try { v = U.parseDb(String(text).replace(this.axisUnit, "")); } catch (e) { this.note("ref", W("bad_value")); return; }
        this.setAmp({ ref_level: Math.round(v * 100) / 100 });
      },
      stepRef(exp, dir) { this.setAmp({ ref_level: Math.round((this.amp.ref + dir * 10 ** exp) * 100) / 100 }); },
      setDiv(v) { this.setAmp({ db_per_div: Number(v) }); },
      refAutoReason() { return this.refAutoView().reason; },
      refAuto() {
        const v = this.refAutoView();
        if (v.level === null) { this.note("ref", v.reason); return; }
        this.setAmp({ ref_level: v.level });
      },
      // REF の自動（J §4.1・§4.8。Qt の screen_model.ref_auto_view と同じ規則）。使わない点（局発の漏れ・前置き不足・
      // 値の無い点・TG の範囲の外・表示範囲の外）を除いたトレースごとの最大値はコアが出す（state.ref_auto）。
      // ここは表示中で縦軸と単位が同じトレースの最大を取り、最大値が上から1目盛下に来る REF を 0.1dB へ切り上げる
      refAutoView() {
        void this.traceVersion;
        const shown = [];
        for (const n of [1, 2, 3, 4]) if (this.$traces.get(String(n)) && this.traceShown(n)) shown.push(String(n));
        for (const f of this.shownFiles(this.palette)) if (this.$traces.get(f.id)) shown.push(f.id);
        if (!shown.length) return { level: null, reason: X.get("REASON_TEXTS", "no_wave") };
        const peaks = (this.st && this.st.ref_auto && this.st.ref_auto.peaks) || {};
        let best = null;
        for (const id of shown) {
          const p = peaks[id];
          if (p && p.unit === this.axisUnit && p.max !== null && p.max !== undefined && (best === null || p.max > best)) best = p.max;
        }
        if (best === null) return { level: null, reason: X.get("REASON_TEXTS", "no_auto_points") };
        return { level: Math.ceil((best + this.amp.div) * 10 - 1e-9) / 10, reason: null };
      },
      // 機器の範囲の外・意味の無い区間の帯（J §4.8）。区間・説明・色はサーバーが付けた ui.marks
      marks() { return (this.ui.marks || []).map((m) => ({ ...m, fill: this.palette[m.color] || this.palette.warn })); },
      // マウスの位置の読み値（設計G §2: マウスの縦の位置ではなく波形の値）。中身（周波数・最も近いトレースの点の値・
      // 印の付いた点の注意）はサーバーが Qt のツールチップと同じ関数（screen_model.mouse_reading）で作る。
      // 取りに行くのは1つずつ、HOVER_MS より詰めない。動かしている間に来た位置は最後の1つだけ取る
      onGraphHover(m) {
        this.hover = m;
        const q = this.$hoverQ || (this.$hoverQ = { busy: false, want: null, timer: null });
        // ボタンを押している間（範囲のドラッグ）は出さない（Qt と同じ）
        if (!m || m.buttons) { q.want = null; this.hoverLines = null; return; }
        q.want = m;
        if (!q.busy && !q.timer) this.hoverFetch();
      },
      async hoverFetch() {
        const q = this.$hoverQ;
        const m = q.want;
        q.want = null;
        if (!m || !this.hover) return;
        q.busy = true;
        const t0 = performance.now();
        try {
          const r = await call("mouse_reading", { x: m.x, y: m.y });
          if (this.hover && !this.hover.buttons) this.hoverLines = r.lines || null;
        } catch (e) {
          this.hoverLines = null; // 読めないときは出さない（グラフの操作は妨げない）
        }
        q.busy = false;
        q.timer = setTimeout(() => {
          q.timer = null;
          if (q.want) this.hoverFetch();
        }, Math.max(0, HOVER_MS - (performance.now() - t0)));
      },
      // 読み値の箱の位置。カーソルから右下へ離し、画面の右端・下端では左・上へ回す（カーソルの下に出さない）
      hoverStyle() {
        const h = this.hover;
        if (!h) return {};
        const left = h.cx + 16 + 260 > window.innerWidth ? { right: (window.innerWidth - h.cx + 12) + "px" } : { left: (h.cx + 16) + "px" };
        const top = h.cy + 16 + 90 > window.innerHeight ? { bottom: (window.innerHeight - h.cy + 12) + "px" } : { top: (h.cy + 16) + "px" };
        return { ...left, ...top };
      },

      // ---- 掃引・モード
      runCont() { this.cmd("run", { mode: "continuous" }); },
      runSingle() { this.cmd("run", { mode: "single" }); },
      stop() { this.cmd("stop"); },
      toggleRun() {
        if (this.running) this.stop();
        else if (this.run.state === "stopped") this.runCont();
      },
      setMode(code) { this.cmd("set_mode", { mode: code }); },

      // ------------------------------------------------------------ イベント
      onEvent(kind, p) {
        if (kind === "state") {
          if (this.st && p.version < this.st.version) return;
          this.st = p;
          if (p.markers) this.markers = p.markers;
          if (this.ampLocal) {
            const a = p.amplitude || {};
            if (Object.keys(this.ampLocal).every((k) => a[k] === this.ampLocal[k]) && !this.ampBusy) this.ampLocal = null;
          }
          const alive = new Set((p.tasks || []).map((t) => t.task_id));
          for (const id of Object.keys(this.progress)) if (!alive.has(id)) delete this.progress[id];
          if (!this.running && this.run.state !== "stopping") this.waiting = "";
          this.onStateRtl(p);
          this.onStateDevices(p);
          this.applyLook();
          this.applyX();
          this.applyY();
          this.redraw();
        } else if (kind === "markers") {
          this.markers = p;
          if (this.selectedMarker && !(p.markers || []).some((m) => m.id === this.selectedMarker)) this.selectedMarker = null;
          this.drawMarkers();
        } else if (kind === "trace") {
          this.$traces.set(String(p.trace), markRaw(p));
          this.traceVersion++;
          this.applyY(); // 値の単位（dBm ⇔ dB）は届いたトレースで変わる。縦軸の名前を合わせる
          this.redraw();
        } else if (kind === "audio") {
          this.onAudio(p);
        } else if (kind === "devices") {
          this.onDevices(p);
        } else if (kind === "notice") {
          this.onNoticeFiles(p);
          const v = p.view || { text: p.code, traces: {} };
          for (const [n, text] of Object.entries(v.traces || {})) this.traceNote(Number(n), text);
          this.note(null, v.text);
        } else if (kind === "progress") {
          if (p.kind === "task") this.progress = { ...this.progress, [p.task_id]: p };
          else if (p.kind === "waiting_sweep_end") {
            this.waiting = p.text;
            this.note(null, this.waiting);
          }
        }
      },
      traceNote(n, text) {
        this.traceNotes = { ...this.traceNotes, [n]: text };
        setTimeout(() => { if (this.traceNotes[n] === text) { const t = { ...this.traceNotes }; delete t[n]; this.traceNotes = t; } }, 8000);
      },

      // ------------------------------------------------------------ グラフ
      makeGraph() {
        const el = this.$refs.graph;
        if (!el || this.$graph || !this.colors) return;
        this.$graph = markRaw(new GSGraph(el, {
          colors: this.palette,
          onRangeSelected: (a, b) => this.onRangeSelected(a, b),
          onFreqWheel: (x, steps) => this.onFreqWheel(x, steps),
          onLevelWheel: (steps, ctrl) => this.onLevelWheel(steps, ctrl),
          onLevelDrag: (start, frac) => this.onLevelDrag(start, frac),
          onClick: (x, y) => this.onGraphClick(x, y),
          onContext: (x, y, cx, cy) => this.onGraphContext(x, y, cx, cy),
          onHover: (m) => this.onGraphHover(m),
        }));
        this.applyX();
        this.applyY();
        this.redraw();
      },
      applyX() {
        const g = this.$graph;
        const s = this.sw;
        if (!g || !s) return;
        if (s.step_hz === 0) g.setX(0, Math.max(1, s.points - 1), true);
        else g.setX(s.start_hz, s.stop_hz, false);
        g.setXOffset(s.step_hz === 0 ? 0 : this.freqOff, this.ui.freq_offset_text || "");
      },
      applyY() { if (this.$graph) this.$graph.setY(this.amp.ref, this.amp.div, this.axisUnit); },
      traceShown(n) {
        const t = ((this.st && this.st.traces) || []).find((x) => x.number === n);
        return !!(t && t.visible && t.has_values);
      },
      shownFiles(colors) {
        // 重ねている参照トレース（見えているもの）と色。PNG の凡例にも同じものを渡す
        const out = [];
        let i = 0;
        for (const f of this.files) {
          for (const tr of f.traces) {
            const color = FILE_COLORS[i++ % FILE_COLORS.length];
            if (!this.fileHidden[tr.id]) out.push({ id: tr.id, colorKey: color, color: colors[color], label: X.fmt("file_trace_label", { name: f.name, number: tr.number }) });
          }
        }
        return out;
      },
      graphTraces(colors) {
        const out = [];
        for (const n of [1, 2, 3, 4]) {
          const t = this.$traces.get(String(n));
          if (!t || !this.traceShown(n)) continue;
          out.push(this.graphTrace(t, colors["trace" + n], colors, false));
        }
        for (const f of this.shownFiles(colors)) {
          const t = this.$traces.get(f.id);
          if (t) out.push(this.graphTrace(t, f.color, colors, true));
        }
        return out;
      },
      graphTrace(t, color, colors, isFile) {
        const ax = t.axis;
        const x = ax.freqs_hz ? (i) => ax.freqs_hz[i] : (i) => ax.start_hz + ax.step_hz * i;
        const zero = this.sw && this.sw.step_hz === 0 && !isFile;
        return {
          x: zero ? (i) => i : x, y: t.values, color, minHold: t.action === "min_hold",
          head: isFile ? 0 : (t.flags && t.flags.transient_head_points) || 0, fill: !isFile && this.drawFill,
          dimColor: colors.text_dim, dash: isFile ? [6, 4] : null,
        };
      },
      redraw() {
        if (this.rafPending) return;
        this.rafPending = true;
        requestAnimationFrame(() => {
          this.rafPending = false;
          if (!this.$graph) return;
          this.$graph.setBands(this.marks());
          this.$graph.setTraces(this.graphTraces(this.palette));
          this.drawMarkers();
        });
      },
      drawMarkers() {
        if (!this.$graph) return;
        this.$graph.setMarkers(this.markers.markers || [], this.selectedMarker);
        this.$graph.setXY(this.markers.xy);
      },
      onRangeSelected(a, b) {
        // ドラッグは Start と Stop を同時に触ったものとして扱う（C §5.1）。Span 固定の規則はコアが当てる（C §5.12）
        if (this.freqOpsReason) return;
        this.sweep({ start_hz: Math.round(Math.min(a, b)), stop_hz: Math.round(Math.max(a, b)), edited: ["start_hz", "stop_hz"] }, "start");
      },
      onFreqWheel(x, steps) {
        const s = this.sw;
        if (this.freqOpsReason || !s) return;
        // Span 固定中は幅を変えない。命令を送らず、Span の欄で変えることをその場で知らせる（C §5.12。Qt と同じ）
        if (s.span_lock_hz != null) { this.note("span", W("span_lock_wheel")); return; }
        // カーソルの位置の周波数を画面上の同じ位置に保って拡大・縮小する（Qt と同じ）
        const k = ZOOM_STEP ** steps;
        const ns = x - (x - s.start_hz) * k;
        const ne = x + (s.stop_hz - x) * k;
        const span = Math.max(Math.round(ne - ns), 1);
        const center = Math.round(ns) + Math.floor(span / 2);
        this.sweep({ center_hz: center, span_hz: span, edited: ["center_hz", "span_hz"] }, "span");
      },
      onLevelWheel(steps, ctrl) {
        if (ctrl) {
          let i = DB_PER_DIV_CHOICES.indexOf(this.amp.div);
          if (i < 0) i = 3;
          i = Math.min(Math.max(i + (steps > 0 ? 1 : -1), 0), DB_PER_DIV_CHOICES.length - 1);
          this.setDiv(DB_PER_DIV_CHOICES[i]);
        } else {
          this.setAmp({ ref_level: Math.round((this.amp.ref + steps * this.amp.div) * 100) / 100 });
        }
      },
      onLevelDrag(start, frac) {
        // つかんだ目盛がマウスに付いてくる: 下へ引くと、より上の値が見える（REF が上がる）
        if (start) { this.dragRef0 = this.amp.ref; return; }
        this.setAmp({ ref_level: Math.round((this.dragRef0 + frac * GSGraph.DIVISIONS * this.amp.div) * 10) / 10 });
      },
      nearestTrace(x, y) {
        // クリック位置に縦方向で最も近い、見えているトレース（G §3.2）
        let best = null;
        for (const n of [1, 2, 3, 4]) {
          const t = this.$traces.get(String(n));
          if (!t || !this.traceShown(n) || !t.axis.step_hz) continue;
          const i = Math.round((x - t.axis.start_hz) / t.axis.step_hz);
          if (i < 0 || i >= t.values.length || !Number.isFinite(t.values[i])) continue;
          const d = Math.abs(t.values[i] - y);
          if (best === null || d < best[1]) best = [n, d];
        }
        return best ? best[0] : null;
      },
      async markerAdd(x, y) {
        if (this.freqOpsReason) return;
        const trace = this.nearestTrace(x, y);
        if (trace === null) { this.note(null, X.get("ERRORS", "no_trace_data")); return; }
        const r = await this.cmd("marker_add", { kind: "M", trace, freq_hz: Math.round(x) });
        if (r && r.id) this.selectedMarker = r.id;
      },
      onGraphClick(x, y) { this.markerAdd(x, y); },
      onGraphContext(x, y, cx, cy) {
        const why = this.freqOpsReason;
        const hz = Math.round(x);
        const level = Math.round(y * 10) / 10;
        const at = why ? "" : X.fmt("paren", { text: U.formatFreq(this.shownHz(hz)) });
        const items = [
          { label: W("ctx_center") + at, why, run: () => this.sweep({ center_hz: hz }, "center") },
          { label: W("ctx_marker") + at, why, run: () => this.markerAdd(x, y) },
          { label: W("ctx_ref", { level: level.toFixed(1), unit: this.axisUnit }), run: () => this.setAmp({ ref_level: level }) },
          { label: W("ctx_x1"), why, run: () => this.cmd("set_xy", { x1_hz: hz }) },
          { label: W("ctx_x2"), why, run: () => this.cmd("set_xy", { x2_hz: hz }) },
        ];
        const sel = this.selectedMarker;
        if (sel) {
          // 選んでいるマーカーへの操作（Qt の右クリックと同じ: G §3.4・設計Q §6.8）
          items.push(
            { label: W("ctx_m_center", { m: sel }), run: () => this.cmd("marker_to", { id: sel, target: "center" }) },
            { label: W("ctx_m_ref", { m: sel }), run: () => this.cmd("marker_to", { id: sel, target: "ref" }) },
            { label: W("ctx_m_listen", { m: sel, start: X.get("LISTEN_LABELS", "start") }), why: this.listenStartReason, run: () => this.listenStart() },
          );
        }
        setTimeout(() => { this.ctx = { x: cx, y: cy, items }; }, 0);
      },

      // ------------------------------------------------------------ マーカー
      async markerPeak(direction) {
        const args = { direction, trace: 1 };
        if (this.selectedMarker) args.id = this.selectedMarker;
        const r = await this.cmd("marker_peak", args);
        if (r && r.id) this.selectedMarker = r.id;
      },
      markerStep(n) { if (this.selectedMarker) this.cmd("marker_set", { id: this.selectedMarker, step: n }); },
      markerHere() {
        const m = this.$graph && this.$graph.mouse;
        if (m) this.markerAdd(m.x, m.y);
      },
      copyMarkerTable() {
        // タブ区切りで表計算へ貼れる形（G §3.3）。子の行と機能の結果も含める（G §3.5）。Qt と同じ行・同じ文字
        const lines = [this.markerColumns.join("\t")];
        for (const r of this.markerRows) lines.push(r.cells.join("\t"));
        this.copyText(lines.join("\n"), X.fmt("marker_copied", { n: this.markerRows.length }));
      },
      copyText(text, done) {
        const ok = () => this.note(null, done);
        if (navigator.clipboard && window.isSecureContext) { navigator.clipboard.writeText(text).then(ok, () => this.copyFallback(text, done)); return; }
        this.copyFallback(text, done);
      },
      copyFallback(text, done) {
        const ta = document.createElement("textarea");
        ta.value = text;
        document.body.appendChild(ta);
        ta.select();
        const ok = document.execCommand("copy");
        ta.remove();
        this.note(null, ok ? done : W("copy_failed"));
      },
      async addOffset() {
        const m = this.selMarker;
        if (!m) return;
        let delta;
        try { delta = this.off.type === "freq" ? U.parseFreqHz(this.off.delta) : U.parseDb(this.off.delta); } catch (e) { this.note("offset", W("bad_value")); return; }
        const r = await this.cmd("marker_offset_add", { id: m.id, type: this.off.type, sign: this.off.sign, delta }, { field: "offset" });
        if (r) this.off.delta = "";
      },
      setFunction() {
        const m = this.selMarker;
        if (!m) return;
        const bws = [this.fn.bw1, this.fn.bw2].filter((v) => String(v).trim() !== "").map(Number);
        if (bws.some((v) => !Number.isFinite(v))) { this.note("function", X.get("ERRORS", "bad_marker_function")); return; }
        this.cmd("marker_function", { id: m.id, bandwidth_db: bws }, { field: "function" });
      },
      setThreshold(on) {
        const m = this.selMarker;
        if (!m) return;
        if (!on) { this.cmd("marker_function", { id: m.id, threshold: null }, { field: "function" }); return; }
        let level;
        try { level = U.parseDb(this.fn.thr); } catch (e) { this.note("function", W("bad_value")); return; }
        this.cmd("marker_function", { id: m.id, threshold: { level, side: this.fn.side } }, { field: "function" });
      },
      commitXY(key, text) {
        if (String(text).trim() === "") { this.cmd("set_xy", { [key]: null }); return; }
        try {
          const v = key.endsWith("_hz") ? this.deviceHz(U.parseFreqHz(text)) : U.parseDb(text);
          this.cmd("set_xy", { [key]: v }, { field: "xy" });
        } catch (e) { this.note("xy", W("bad_value")); }
      },
      xyText(key) {
        const v = (this.markers.xy || {})[key];
        if (v == null) return "";
        return key.endsWith("_hz") ? U.freqFieldText(this.shownHz(v)) : `${v.toFixed(2)}`;
      },
      async getPeakList() {
        const r = await this.cmd("peak_list", { trace: Number(this.peakTrace), floor_margin_db: Number(this.peakMargin) || 10 }, { field: "peaks" });
        if (r) this.peakList = r;
      },
      async topPeaks() {
        const n = parseInt(this.topN, 10);
        if (!(n > 0)) { this.note("peaks", X.get("ERRORS", "not_integer")); return; }
        await this.cmd("marker_top_peaks", { trace: Number(this.peakTrace), n }, { field: "peaks" });
      },

      // ------------------------------------------------------------ トレース
      setTrace(n, key, value) {
        const args = { number: n, [key]: value };
        this.cmd("set_trace", args, { field: "trace" + n });
      },
      setTraceCount(n, text) {
        const t = String(text).trim();
        if (t === "") { this.setTrace(n, "count", null); return; }
        const v = Number(t);
        if (!Number.isInteger(v)) { this.note("trace" + n, X.get("ERRORS", "not_integer")); return; }
        this.setTrace(n, "count", v);
      },
      setPointsAuto(auto) {
        if (auto) this.sweep({ points_auto: true }, "points");
        else this.sweep({ points: this.sw.points }, "points");
      },
      setPoints(text) {
        const v = Number(text);
        if (!Number.isInteger(v)) { this.note("points", X.get("ERRORS", "not_integer")); return; }
        this.sweep({ points: v }, "points");
      },
      toggleFileTrace(id) { this.fileHidden = { ...this.fileHidden, [id]: !this.fileHidden[id] }; this.redraw(); },

      // ------------------------------------------------------------ 基準（信号源のタブ: 設計E §6）
      async loadRefs() {
        const r = await this.cmd("reference_list", {}, { field: "refs", quiet: ["device_not_selected"] });
        this.refs = (r && r.references) || [];
      },
      async takeRef(kind) {
        await this.cmd("reference_take", { kind, name: this.refName || null }, { field: "refs" });
      },
      takeDirect() {
        // 広く取り、すぐ1回測って 0dB を見せる（縦軸の外ならコアが REF を動かして知らせる: show_zero）
        const d = new Date();
        const p = (n) => String(n).padStart(2, "0");
        this.cmd("reference_take", { kind: "wide", name: X.fmt("ref_direct_name", { time: `${p(d.getHours())}:${p(d.getMinutes())}` }), show_zero: true });
      },
      async refAction(name, args) { await this.cmd(name, args, { field: "refs" }); this.loadRefs(); },
      renameRef(r) {
        this.openModal(W("rename_ref_title"), [{ key: "name", label: W("name"), value: r.name }], (v) => this.refAction("reference_rename", { id: r.id, name: v.name }));
      },

      // ------------------------------------------------------------ 入力の窓（ファイルの窓は app_files.js）
      openModal(title, fields, ok, note) {
        this.modal = { title, note, fields: fields.map((f) => ({ ...f })), ok };
      },
      modalOk() {
        const m = this.modal;
        const v = {};
        for (const f of m.fields) v[f.key] = f.type === "check" ? !!f.value : f.value;
        this.modal = null;
        m.ok(v, m);
      },
      // ------------------------------------------------------------ PNG（設計I。配置はサーバーの report_layout、描くのは canvas）
      // 配色と大きさは設定の画面の「保存」で選んだもの（ブラウザごと。既定はライトの初期値・1600×1000: I §3.1）
      pngBlob(note) {
        const colors = this.pngColors();
        return GSPng.render({
          st: this.st, amp: { ...this.amp, unit: this.axisUnit }, colors,
          traces: (c) => this.graphTraces(c), markers: this.markers,
          files: this.shownFiles(colors).map((f) => ({ label: f.label, color: f.colorKey })), note,
          offset: { hz: this.freqOff, text: this.ui.freq_offset_text || "" },
        }, this.pngSizeWH());
      },
      // 画像を作る前にメモ（1行）を打てる（I §3.2 の 5）
      pngSave() {
        this.openModal(W("png_save_title"), [{ key: "note", label: W("png_memo"), value: "" }], async (v) => {
          try {
            const blob = await this.pngBlob(v.note);
            const a = document.createElement("a");
            a.href = URL.createObjectURL(blob);
            a.download = nowName() + ".png";
            a.click();
            setTimeout(() => URL.revokeObjectURL(a.href), 5000);
            this.note(null, W("png_saved", { name: a.download }));
          } catch (e) { this.note(null, e.text || String(e)); }
        });
      },
      pngCopy() {
        if (this.clipboardReason) { this.note(null, this.clipboardReason); return; }
        this.openModal(W("png_copy_title"), [{ key: "note", label: W("png_memo"), value: "" }], (v) => {
          // 画像を作る間にクリック直後の許可が切れないよう、作りかけの Promise のまま渡す
          navigator.clipboard.write([new ClipboardItem({ "image/png": this.pngBlob(v.note) })]).then(
            () => this.note(null, W("png_copied")),
            (e) => this.note(null, W("copy_failed_why", { why: String(e) })));
        });
      },

      // ------------------------------------------------------------ 別ウィンドウ（J §4.3.2）
      detach(p) {
        const w = window.open(`/?part=${p}`, `pygigast_${p}`, "width=960,height=640");
        if (!w) { this.note(null, W("popup_blocked")); return; }
        this.detachedWins = this.detachedWins || {};
        this.detachedWins[p] = w;
        this.detached = { ...this.detached, [p]: true };
      },
      reattach(p) {
        const w = (this.detachedWins || {})[p];
        if (w && !w.closed) w.close();
        this.pollDetached(true);
      },
      pollDetached() {
        // 別ウィンドウが閉じられたら元の位置に戻す（なくならない）
        const wins = this.detachedWins || {};
        let changed = false;
        const d = { ...this.detached };
        for (const p of Object.keys(d)) {
          if (!wins[p] || wins[p].closed) { delete d[p]; delete wins[p]; changed = true; }
        }
        if (changed) { this.detached = d; this.$nextTick(() => this.$graph && this.$graph.setSize(this.$refs.graph.clientWidth, this.$refs.graph.clientHeight)); }
      },
      show(p) { return this.part ? this.part === p : !this.detached[p]; },
      // ピークの一覧だけの別ウィンドウは、マーカーのタブの中のピークの一覧の欄だけを出す（index.html）
      showTab(t) {
        if (this.part) return this.part === "tab-" + t || (t === "marker" && this.part === "peaks");
        return this.tab === t && !this.detached["tab-" + t];
      },

      // ------------------------------------------------------------ 分割（J §4.3.2。比は次に開いたときに戻す）
      startSplit(e, which) {
        e.preventDefault();
        const box = this.$refs.main.getBoundingClientRect();
        const move = (ev) => {
          if (which === "v") this.splitPct = Math.min(90, Math.max(30, ((ev.clientY - box.top) / box.height) * 100));
          else this.sidePx = Math.min(700, Math.max(240, box.right - ev.clientX));
        };
        const up = () => {
          window.removeEventListener("mousemove", move);
          window.removeEventListener("mouseup", up);
          try { localStorage.setItem("pygigast.layout", JSON.stringify({ splitPct: this.splitPct, sidePx: this.sidePx })); } catch (err) { /* 残せなくても動く */ }
        };
        window.addEventListener("mousemove", move);
        window.addEventListener("mouseup", up);
      },

      // テンプレートから使う
      offsetDefs(m) {
        // 表の子の行（± は2行）から、消すときの番号（0 始まり）ごとの1項目にまとめる
        const out = [];
        const seen = new Set();
        for (const r of m.freq_offsets || []) {
          const i = parseInt(r.id.split(".f")[1], 10) - 1;
          if (seen.has("f" + i)) continue;
          seen.add("f" + i);
          out.push({ key: "f" + i, type: "freq", index: i, label: W("offset_freq_item", { sign: r.sign === "-" ? "−" : r.sign, value: U.formatFreq(Math.abs(r.offset_hz)) }) });
        }
        for (const r of m.level_offsets || []) {
          const i = parseInt(r.id.split(".l")[1], 10) - 1;
          if (seen.has("l" + i)) continue;
          seen.add("l" + i);
          out.push({ key: "l" + i, type: "level", index: i, label: W("offset_level_item", { sign: r.sign === "-" ? "−" : r.sign, value: Math.abs(r.offset_db) }) });
        }
        return out;
      },
      fmtFreq(hz) { return hz == null ? "" : U.formatFreq(hz); },
      fmtLevel,
      T(dict, code) { return X.get(dict, code); },
      Td(dict) { return X.dict(dict); },
      W(key, kw) { return X.w(key, kw); },
      F(key, kw) { return X.fmt(key, kw); },
    },
  };

  // 受信・聞く・rtl_tcp（app_rtl.js）とメニュー・キー（app_menu.js）を合わせる
  function merge(target, part) {
    const d0 = target.data;
    const d1 = part.data;
    target.data = function () { return { ...d0.call(this), ...(d1 ? d1.call(this) : {}) }; };
    Object.assign(target.computed, part.computed || {});
    Object.assign(target.methods, part.methods || {});
  }
  merge(base, GSAppMenu);
  merge(base, GSAppFiles);
  merge(base, GSAppRtl);
  merge(base, GSAppDevices);
  // キーの割り当て（app_keys.js）と設定の画面・プリセット・表示の設定（app_settings.js）
  merge(base, window.GSAppKeys || {});
  merge(base, window.GSAppSettings || {});

  const app = createApp(base);
  app.component("num-field", GSFields.NumField);
  // 状態を映す入力は、触っている間は書き直さない（fields.js の Steady）
  app.directive("steady", GSFields.Steady);
  // 設定の画面・プリセットの窓・確かめる窓（app_windows.js）
  for (const [name, c] of Object.entries(window.GSWindows || {})) app.component(name, c);
  window.addEventListener("DOMContentLoaded", () => { window.gsApp = app.mount("#app"); });
})();
