// Web のメイン画面（設計J §4.3 の案B。Qt の ui_qt/main_window.py と同じ構成・同じ語・同じ操作: J §5）。
// コアへは Web の口（api.js）だけを使う。状態の正本はコアの1つで、変更は全部の画面に state で届く（A §4.4）。
(function () {
  "use strict";

  const { createApp, markRaw } = Vue;
  const U = GSUnits;
  const X = GSTexts;
  const call = GSApi.call;

  const DB_PER_DIV_CHOICES = [1, 2, 5, 10];
  const RBW_CHOICES = [180000, 30000, 15000];
  // 周波数軸の上のホイール1段で Span を何倍にするか（奥へ回すと拡大 = Span を狭く。Qt と同じ）
  const ZOOM_STEP = 0.8;

  // モード（コード・ボタンの文字・名前・使えない理由）。並びがキーの 1〜5 の順（J §4.6）。Qt と同じ
  const MODES = [
    ["sa", "SA", "スペクトラムアナライザ", null],
    ["tg", "TG", "伝送特性（TG 出力を被測定物へ）", null],
    ["s11", "S11", "反射", "反射（方向性結合器）は次の回で実装します"],
    ["sg", "SG", "信号源", null],
    ["nf", "NF", "NF 測定", "NF 測定は次の回で実装します"],
  ];

  // 配色の初期値（J §4.4 の表。名前は Qt の png_export.DARK_DEFAULT / LIGHT_DEFAULT と同じ）。画面はダーク（Qt と同じ）
  const DARK = {
    graph_bg: "#15181d", panel_bg: "#1e232b", text: "#e6e9ee", text_dim: "#aab3c0", accent: "#4fa3ff",
    warn: "#ffcc66", grid: "#2a313c", axis: "#3a4452", trace1: "#ffd24a", trace2: "#4fd1c5",
    trace3: "#ff7aa8", trace4: "#9fe870", zero_db: "#7fb3ff", running: "#1f6f3a", marker: "#e6e9ee",
  };
  const LIGHT = {
    graph_bg: "#ffffff", panel_bg: "#f4f6f9", text: "#1b1f24", text_dim: "#4a5562", accent: "#1f5fae",
    warn: "#6f4600", grid: "#d5dbe3", axis: "#5a6573", trace1: "#d18a00", trace2: "#00897b",
    trace3: "#c2185b", trace4: "#2e7d32", zero_db: "#1f5fae", running: "#2e7d32", marker: "#1b1f24",
  };
  // 参照トレース（ファイル）の色。J §4.4 に無いので、トレースの色を順に使い点線で描く（設計に無い判断）
  const FILE_COLORS = ["trace2", "trace3", "trace4", "trace1"];

  const NOT_YET_CAL = "校正の画面は次の回で実装します";
  const NOT_YET_VIEW = "表示の切り替えは次の回で実装します";
  const NOT_YET_SETTINGS = "設定の画面は次の回で実装します";
  const NOT_YET_DIAG = "自己診断の結果を見る画面は次の回で実装します";
  const NO_CLIPBOARD = "この接続（http で別の PC から開いている）では、ブラウザが画像のコピーを許しません。PNG を保存してください";

  // キー操作の Web の初期値（J §4.6。ブラウザが先に取るキーは避けた: F5 の再読み込み・Ctrl+数字のタブ切り替え・
  // Ctrl+Shift+C の開発者ツール）。グラフにいるときだけのキーは graph に印を付ける
  const KEYS = {
    open: ["Ctrl+O"], save: ["Ctrl+S"], export: ["Ctrl+E"], png_save: ["Ctrl+P"], png_copy: ["Ctrl+Alt+C"],
    undo: ["Ctrl+Z"], redo: ["Ctrl+Y"],
    run_cont: ["R"], run_single: ["S"], stop: ["X"], pause: ["Pause"],
    mode_sa: ["Alt+1"], mode_tg: ["Alt+2"], mode_s11: ["Alt+3"], mode_sg: ["Alt+4"], mode_nf: ["Alt+5"],
    marker_peak: ["P"], marker_peak_left: ["["], marker_peak_right: ["]"],
    marker_left: ["ArrowLeft", "Shift+ArrowLeft"], marker_right: ["ArrowRight", "Shift+ArrowRight"], marker_here: ["M"],
  };
  const GRAPH_ONLY = new Set(["marker_peak", "marker_peak_left", "marker_peak_right", "marker_left", "marker_right", "marker_here"]);
  const KEY_NAMES = { ArrowLeft: "←", ArrowRight: "→" };

  const PART_NAMES = {
    graph: "グラフ", markers: "マーカー表", "tab-trace": "トレース・掃引", "tab-marker": "マーカー",
    "tab-view": "表示", "tab-source": "信号源",
  };

  function keyOf(e) {
    let k = e.key.length === 1 ? e.key.toUpperCase() : e.key;
    if (e.code && /^Digit\d$/.test(e.code)) k = e.code.slice(5); // Alt+数字で文字が変わる配列でも数字で見る
    const mods = [];
    if (e.ctrlKey || e.metaKey) mods.push("Ctrl");
    if (e.altKey) mods.push("Alt");
    if (e.shiftKey && e.key.length !== 1) mods.push("Shift");
    return [...mods, k].join("+");
  }
  function keyLabel(k) { return k.split("+").map((p) => KEY_NAMES[p] || p).join("+"); }

  function nowName() {
    const d = new Date();
    const p = (n) => String(n).padStart(2, "0");
    return `gigast_${d.getFullYear()}${p(d.getMonth() + 1)}${p(d.getDate())}_${p(d.getHours())}${p(d.getMinutes())}${p(d.getSeconds())}`;
  }
  function joinPath(dir, name) {
    if (!dir) return name;
    const sep = dir.includes("\\") ? "\\" : "/";
    return dir.replace(/[\\/]+$/, "") + sep + name;
  }
  function fmtLevel(v, unit) { return v == null ? "" : `${v.toFixed(2)} ${unit || ""}`.trim(); }
  function fmtBytes(n) {
    if (!n) return "0 B";
    const u = ["B", "KB", "MB", "GB"];
    let i = 0;
    while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
    return `${n.toFixed(i ? 1 : 0)} ${u[i]}`;
  }
  function fmtClock(s) {
    s = Math.floor(s || 0);
    const p = (n) => String(n).padStart(2, "0");
    return `${Math.floor(s / 3600)}:${p(Math.floor(s / 60) % 60)}:${p(s % 60)}`;
  }

  const app = createApp({
    data() {
      const part = new URLSearchParams(location.search).get("part");
      return {
        part: PART_NAMES[part] ? part : null,
        st: null,
        markers: { markers: [], xy: {} },
        info: {},
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
        ampLocal: null,
        splitPct: 70,
        sidePx: 340,
        traceVersion: 0,
      };
    },
    computed: {
      modes() { return MODES; },
      rbwChoices() { return RBW_CHOICES; },
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
        return this.amp.unit || "dBm";
      },
      freqText() {
        const s = this.sw;
        if (!s) return { center: "", span: "", start: "", stop: "", sg: "" };
        return {
          center: U.freqFieldText(s.center_hz), span: U.freqFieldText(s.span_hz),
          start: U.freqFieldText(s.start_hz), stop: U.freqFieldText(s.stop_hz),
          sg: s.sg_hz != null ? U.freqFieldText(s.sg_hz) : "",
        };
      },
      refText() { return `${this.amp.ref.toFixed(1)} ${this.axisUnit}`; },
      spanLockText() {
        const s = this.sw;
        if (!s || s.span_lock_hz == null) return "Span 固定";
        return s.span_lock_hz !== s.span_hz ? `固定 ${U.formatFreq(s.span_lock_hz)}` : "固定";
      },
      condLines() {
        const s = this.sw;
        if (!s || !this.textsReady) return [];
        const out = [];
        if (s.rbw_hz !== s.rbw_preference_hz) {
          out.push(`RBW は ${U.formatFreq(s.rbw_hz)} で測っています（TG・NF・S11、または Span 50MHz 超では 180kHz 固定。条件から出ると ${U.formatFreq(s.rbw_preference_hz)} に戻ります）`);
        }
        for (const w of s.warnings || []) out.push(X.get("WARNINGS", w));
        if (s.tail_gap) out.push("Stop の手前 1 Step 未満はデータがありません（機器の上限）");
        return out;
      },
      freqOpsReason() {
        const s = this.sw;
        if (!s) return "掃引条件がまだありません";
        if (s.step_hz === 0) return "ゼロスパンの横軸は周波数ではありません";
        return null;
      },
      statusItems() {
        if (!this.st || !this.textsReady) return [];
        const c = this.conn;
        const ident = c.identity || {};
        const who = c.identity ? ` ${ident.serial || ""} ${ident.port || ""}` : "";
        const r = this.run;
        let rs = X.get("RUN_STATES", r.state);
        if (r.state === "single" || (r.state === "stopped" && r.last_stop_reason === "single_done")) {
          rs += ` ${r.single_done}/${r.single_target} 回`;
        }
        const cal = this.st.calibration;
        const items = [
          { key: "conn", text: `● ${X.get("CONN_STATES", c.state)}${who}`, cls: c.state === "connected" ? "ok" : "warn" },
          { key: "run", text: `掃引: ${rs}` },
          { key: "time", text: r.last_sweep_s ? `掃引時間 ${r.last_sweep_s.toFixed(2)} 秒` : "掃引時間 —" },
          { key: "discard", text: `捨てた掃引 ${r.discard_count} 回` },
          { key: "cal", text: "補正: " + (cal ? `校正データ ${cal.created_at}` : "校正データなし（補正なしの値）") },
        ];
        const nt = X.normalizationText(this.st.normalization);
        if (nt) items.push({ key: "norm", text: nt, cls: this.st.normalization.applied ? "" : "warn" });
        const rec = this.st.recording || {};
        if (rec.active || rec.stopped) {
          items.push({
            key: "rec", cls: rec.active ? "rec" : "",
            text: `${rec.active ? "● 記録中" : "記録を止めました（まとめ待ち）"} ${fmtClock(rec.elapsed_s)}・${rec.sweeps} 掃引・${fmtBytes(rec.file_bytes)}`,
          });
        }
        if (!this.wsOpen) items.push({ key: "ws", text: "サーバーと切れています（繋ぎ直しています）", cls: "warn" });
        return items;
      },
      tasks() {
        return ((this.st && this.st.tasks) || []).map((t) => ({ ...t, ...(this.progress[t.task_id] || {}), task: t.kind }));
      },
      traceRows() {
        return ((this.st && this.st.traces) || []).map((t) => ({ ...t, color: DARK["trace" + t.number] }));
      },
      markerRowsView() { return this.buildMarkerRows(); },
      selMarker() { return (this.markers.markers || []).find((m) => m.id === this.selectedMarker) || null; },
      files() { return (this.st && this.st.files) || []; },
      clipboardReason() { return window.isSecureContext && navigator.clipboard && window.ClipboardItem ? null : NO_CLIPBOARD; },
      menu() { return this.buildMenu(); },
      keyRows() {
        const rows = [];
        for (const sec of this.menu) for (const it of sec.items) if (it && it.keys && it.keys.length) rows.push([it.keys.map(keyLabel).join("・"), it.label]);
        rows.push(["↑ / ↓・ホイール（数値の欄）", "カーソルのある桁を増減"], ["Enter / Tab / 欄を離れる", "確定"], ["Esc（数値の欄）", "打つ前の値に戻す"]);
        return rows;
      },
      partTitle() { return this.part ? PART_NAMES[this.part] : ""; },
      layoutStyle() { return { "--split": this.splitPct + "%", "--side": this.sidePx + "px" }; },
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
      if (this.part) document.title = `pyGigaSt — ${this.partTitle}`;
      try {
        X.load(await GSApi.getJson("/api/v1/texts"));
        this.info = await GSApi.getJson("/api/v1/info");
      } catch (e) { this.note(null, "サーバーから文言を読めませんでした"); }
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
        try {
          const r = await call(name, args);
          if (r && r.adjustments) this.showAdjustments(r.adjustments, r.alternative);
          if (opts.ok) opts.ok(r);
          return r;
        } catch (e) {
          if ((opts.quiet || []).includes(e.code)) return null;
          this.note(opts.field || null, X.errorText(e));
          return null;
        }
      },
      sweep(args, field) { return this.cmd("set_sweep", args, { field }); },
      note(field, text, action) {
        if (field) this.notes = { ...this.notes, [field]: { text, action } };
        else this.status = `${new Date().toLocaleTimeString()} ${text}`;
      },
      clearNote(field) { const n = { ...this.notes }; delete n[field]; this.notes = n; },
      showAdjustments(adjs, alt) {
        const fields = { start_hz: "start", stop_hz: "stop", center_hz: "center", span_hz: "span", rbw_hz: "rbw", sg_hz: "sg", points: "points" };
        for (const a of adjs) {
          const f = fields[a.field];
          const text = X.adjustmentText(a);
          let action = null;
          if (alt && (X.dict("RULE_REASONS") || []).includes(a.reason) && f) {
            // 規則で動いた欄の知らせに、反対の規則の結果を「その1回だけ」のボタンで添える（C §5.11 の 3）
            action = { label: X.alternativeLabel(alt), run: () => this.applyAlternative(alt, f) };
          }
          if (f) this.note(f, text, action);
          else this.note(null, `${X.get("FIELD_NAMES", a.field)}: ${text}`);
        }
      },
      async applyAlternative(alt, field) {
        // その1回だけの命令（固定 Span は変わらない: C §5.11 の 3）
        const r = await this.cmd("set_sweep", { start_hz: alt.start_hz, stop_hz: alt.stop_hz, edited: ["start_hz", "stop_hz"], one_shot: true }, { field });
        if (r && r.adopted) this.note(field, X.alternativeDoneText(r.adopted.center_hz, r.adopted.span_hz));
      },

      // ---- 周波数の欄
      commitFreq(field, key, text) {
        let hz;
        try { hz = U.parseFreqHz(text); } catch (e) { this.note(field, "読めない値です（例: 1.2G・300k・1500）"); return; }
        if (key === "sg_hz") { this.cmd("set_mode", { mode: this.sw ? this.sw.mode : "sg", sg_hz: hz }, { field }); return; }
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
      setRbw(hz) { this.sweep({ rbw_hz: Number(hz) }, "rbw"); },

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
        try { v = U.parseDb(String(text).replace(this.axisUnit, "")); } catch (e) { this.note("ref", "読めない値です"); return; }
        this.setAmp({ ref_level: Math.round(v * 100) / 100 });
      },
      stepRef(exp, dir) { this.setAmp({ ref_level: Math.round((this.amp.ref + dir * 10 ** exp) * 100) / 100 }); },
      setDiv(v) { this.setAmp({ db_per_div: Number(v) }); },
      refAutoReason() {
        void this.traceVersion;
        return this.traceMax() === null ? "波形がまだありません（掃引すると使えます）" : null;
      },
      refAuto() {
        const peak = this.traceMax();
        if (peak === null) { this.note("ref", "合わせる波形がまだありません"); return; }
        // いまの波形の最大値が上から1目盛下に来るよう REF を合わせる。表示の桁（0.1dB）へ切り上げる
        this.setAmp({ ref_level: Math.ceil((peak + this.amp.div) * 10 - 1e-9) / 10 });
      },
      traceMax() {
        let best = null;
        const stop = this.sw ? this.sw.stop_hz : null;
        for (const n of [1, 2, 3, 4]) {
          const t = this.$traces.get(String(n));
          if (!t || !this.traceShown(n)) continue;
          const ax = t.axis;
          for (let i = 0; i < t.values.length; i++) {
            // Stop の先に出た最後の点（C §5.3）は表示の外なので数えない
            if (ax.step_hz > 0 && stop != null && ax.start_hz + ax.step_hz * i > stop) break;
            const v = t.values[i];
            if (Number.isFinite(v) && (best === null || v > best)) best = v;
          }
        }
        return best;
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
          this.redraw();
        } else if (kind === "notice") {
          const text = X.noticeText(p);
          const d = p.detail || {};
          if (p.code === "trace_reset") for (const n of d.traces || []) this.traceNote(n, X.traceResetText(d));
          else if (d.trace && (p.code === "trace" || X.has("TRACE_NOTICES", p.code))) this.traceNote(d.trace, text);
          this.note(null, text);
        } else if (kind === "progress") {
          if (p.kind === "task") this.progress = { ...this.progress, [p.task_id]: p };
          else if (p.kind === "waiting_sweep_end") {
            this.waiting = `いまの掃引の終わりを待っています（残り約 ${p.remaining_s.toFixed(1)} 秒）`;
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
        if (!el || this.$graph) return;
        this.$graph = markRaw(new GSGraph(el, {
          colors: DARK,
          onRangeSelected: (a, b) => this.onRangeSelected(a, b),
          onFreqWheel: (x, steps) => this.onFreqWheel(x, steps),
          onLevelWheel: (steps, ctrl) => this.onLevelWheel(steps, ctrl),
          onLevelDrag: (start, frac) => this.onLevelDrag(start, frac),
          onClick: (x, y) => this.onGraphClick(x, y),
          onContext: (x, y, cx, cy) => this.onGraphContext(x, y, cx, cy),
          onHover: (m) => { this.hover = m; },
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
      },
      applyY() { if (this.$graph) this.$graph.setY(this.amp.ref, this.amp.div, this.axisUnit); },
      traceShown(n) {
        const t = ((this.st && this.st.traces) || []).find((x) => x.number === n);
        return !!(t && t.visible && t.has_values);
      },
      graphTraces(colors) {
        const out = [];
        for (const n of [1, 2, 3, 4]) {
          const t = this.$traces.get(String(n));
          if (!t || !this.traceShown(n)) continue;
          out.push(this.graphTrace(t, colors["trace" + n], colors, false));
        }
        let i = 0;
        for (const f of this.files) {
          for (const tr of f.traces) {
            const t = this.$traces.get(tr.id);
            const color = colors[FILE_COLORS[i++ % FILE_COLORS.length]];
            if (t && !this.fileHidden[tr.id]) out.push(this.graphTrace(t, color, colors, true));
          }
        }
        return out;
      },
      graphTrace(t, color, colors, isFile) {
        const ax = t.axis;
        const x = ax.freqs_hz ? (i) => ax.freqs_hz[i] : (i) => ax.start_hz + ax.step_hz * i;
        const zero = this.sw && this.sw.step_hz === 0 && !isFile;
        return {
          x: zero ? (i) => i : x, y: t.values, color, minHold: t.action === "min_hold",
          head: isFile ? 0 : (t.flags && t.flags.transient_head_points) || 0,
          dimColor: colors.text_dim, dash: isFile ? [6, 4] : null,
        };
      },
      redraw() {
        if (this.rafPending) return;
        this.rafPending = true;
        requestAnimationFrame(() => {
          this.rafPending = false;
          if (!this.$graph) return;
          this.$graph.setTraces(this.graphTraces(DARK));
          this.drawMarkers();
        });
      },
      drawMarkers() {
        if (!this.$graph) return;
        this.$graph.setMarkers(this.markers.markers || [], this.selectedMarker);
        this.$graph.setXY(this.markers.xy);
      },
      onRangeSelected(a, b) {
        // ドラッグは Start と Stop を同時に触ったものとして扱う（C §5.1）
        if (this.freqOpsReason) return;
        this.sweep({ start_hz: Math.round(Math.min(a, b)), stop_hz: Math.round(Math.max(a, b)), edited: ["start_hz", "stop_hz"] }, "start");
      },
      onFreqWheel(x, steps) {
        const s = this.sw;
        if (this.freqOpsReason || !s) return;
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
        const items = [
          { label: "ここを Center に" + (why ? "" : `（${U.formatFreq(hz)}）`), why, run: () => this.sweep({ center_hz: hz }, "center") },
          { label: "ここにマーカー", why, run: () => this.markerAdd(x, y) },
          { label: `REF をここに（${level.toFixed(1)} ${this.axisUnit}）`, run: () => this.setAmp({ ref_level: level }) },
          { label: "ここを X1 に", why, run: () => this.cmd("set_xy", { x1_hz: hz }) },
          { label: "ここを X2 に", why, run: () => this.cmd("set_xy", { x2_hz: hz }) },
        ];
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
      buildMarkerRows() {
        const rows = [];
        const ms = this.markers.markers || [];
        const status = (s) => (s && s.status && s.status !== "ok" ? X.get("MARKER_STATUS", s.status) : "");
        const notes = (list) => (list || []).filter((c) => !(X.dict("MARKER_NOTES_SILENT") || []).includes(c)).map((c) => X.get("MARKER_NOTES", c)).join("・");
        const mhz = (hz, sign) => (hz == null ? "" : (sign && hz > 0 ? "+" : "") + (hz / 1e6).toFixed(6).replace(/0{1,3}$/, ""));
        for (const m of ms) {
          const d = m.delta || {};
          const refRow = ms.find((x) => x.id === m.ref);
          rows.push({
            id: m.id, parent: null, name: m.kind + m.number, trace: m.trace, freq: mhz(m.freq_hz),
            value: m.level == null ? status(m) : fmtLevel(m.level, m.unit),
            ref: m.ref, refName: refRow ? refRow.kind + refRow.number : "",
            dfreq: m.ref ? (d.freq_hz != null ? mhz(d.freq_hz, true) : status(d)) : "",
            dval: m.ref && d.level_db != null ? (d.level_db >= 0 ? "+" : "") + d.level_db.toFixed(2) : "",
            note: [notes(m.notes), m.peak_track ? "ピーク追従" : ""].filter((x) => x).join("・"),
            m,
          });
          for (const r of m.freq_offsets || []) {
            rows.push({
              id: r.id, parent: m.id, name: `${m.kind}${m.number} ${r.offset_hz >= 0 ? "+" : "−"}${U.formatFreq(Math.abs(r.offset_hz))}`,
              trace: m.trace, freq: mhz(r.freq_hz), value: r.level == null ? status(r) : fmtLevel(r.level, r.unit),
              refName: "", dfreq: "", dval: r.delta_level_db != null ? (r.delta_level_db >= 0 ? "+" : "") + r.delta_level_db.toFixed(2) : "",
              note: notes(r.notes),
            });
          }
          for (const r of m.level_offsets || []) {
            rows.push({
              id: r.id, parent: m.id, name: `${m.kind}${m.number} ${r.offset_db >= 0 ? "+" : "−"}${Math.abs(r.offset_db)} dB`,
              trace: m.trace, freq: "", value: r.level == null ? status(r) : fmtLevel(r.level, r.unit), refName: "", dfreq: "", dval: "", note: "",
            });
          }
          for (const b of m.bandwidth || []) {
            rows.push({
              id: `${m.id}.bw${b.n_db}`, parent: m.id, name: `${m.kind}${m.number} ${b.n_db} dB 帯域幅`, trace: m.trace,
              freq: b.width_hz != null ? mhz(b.width_hz) : "", value: b.status === "ok" ? `中心 ${mhz(b.center_hz)} MHz・Q ${b.q != null ? b.q.toFixed(1) : "—"}` : X.get("BAND_STATUS", b.status),
              refName: "", dfreq: b.left_hz != null && b.right_hz != null ? `${mhz(b.left_hz)}〜${mhz(b.right_hz)}` : "", dval: "", note: "",
            });
          }
          if (m.shape_factor != null) rows.push({ id: `${m.id}.sf`, parent: m.id, name: `${m.kind}${m.number} シェープファクタ`, trace: m.trace, freq: "", value: m.shape_factor.toFixed(2), refName: "", dfreq: "", dval: "", note: "" });
          if (m.threshold) {
            const t = m.threshold;
            rows.push({
              id: `${m.id}.thr`, parent: m.id, name: `${m.kind}${m.number} しきい値 ${t.level} ${X.get("THRESHOLD_SIDES", t.side)}`, trace: m.trace,
              freq: t.width_hz != null ? mhz(t.width_hz) : "", value: t.status === "ok" ? "" : X.get("BAND_STATUS", t.status),
              refName: "", dfreq: t.left_hz != null && t.right_hz != null ? `${mhz(t.left_hz)}〜${mhz(t.right_hz)}` : "", dval: "", note: "",
            });
          }
        }
        return rows;
      },
      copyMarkerTable() {
        // タブ区切りで表計算へ貼れる形（G §3.3）。子の行と機能の結果も含める（G §3.5）
        const head = ["番号", "トレース", "周波数 (MHz)", "値", "基準", "Δ周波数 (MHz)", "Δ値 (dB)", "注意"];
        const lines = [head.join("\t")];
        for (const r of this.markerRowsView) lines.push([r.name, r.trace, r.freq, r.value, r.refName, r.dfreq, r.dval, r.note].join("\t"));
        this.copyText(lines.join("\n"), "マーカー表をコピーしました");
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
        this.note(null, ok ? done : "コピーできませんでした");
      },
      async addOffset() {
        const m = this.selMarker;
        if (!m) return;
        let delta;
        try { delta = this.off.type === "freq" ? U.parseFreqHz(this.off.delta) : U.parseDb(this.off.delta); } catch (e) { this.note("offset", "読めない値です"); return; }
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
        try { level = U.parseDb(this.fn.thr); } catch (e) { this.note("function", "読めない値です"); return; }
        this.cmd("marker_function", { id: m.id, threshold: { level, side: this.fn.side } }, { field: "function" });
      },
      commitXY(key, text) {
        if (String(text).trim() === "") { this.cmd("set_xy", { [key]: null }); return; }
        try {
          const v = key.endsWith("_hz") ? U.parseFreqHz(text) : U.parseDb(text);
          this.cmd("set_xy", { [key]: v }, { field: "xy" });
        } catch (e) { this.note("xy", "読めない値です"); }
      },
      xyText(key) {
        const v = (this.markers.xy || {})[key];
        if (v == null) return "";
        return key.endsWith("_hz") ? U.freqFieldText(v) : `${v.toFixed(2)}`;
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
      async refAction(name, args) { await this.cmd(name, args, { field: "refs" }); this.loadRefs(); },
      renameRef(r) {
        this.openModal("基準の名前を変える", [{ key: "name", label: "名前", value: r.name }], (v) => this.refAction("reference_rename", { id: r.id, name: v.name }));
      },

      // ------------------------------------------------------------ ファイル（パスはサーバーの PC の上）
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
      saveDialog() {
        this.openModal("保存", [
          { key: "path", label: "保存先（サーバーの PC の上のパス）", value: joinPath(this.info.default_dir, nowName() + ".h5") },
          { key: "note", label: "メモ", value: "" },
        ], (v) => this.cmd("save", { path: v.path, note: v.note }));
      },
      openDialog() {
        this.openModal("開く（参照トレースとして重ねる）", [
          { key: "paths", label: "ファイル（サーバーの PC の上のパス。1行に1つ）", value: "", multiline: true },
        ], (v) => this.cmd("open", { paths: v.paths.split(/\r?\n/).map((s) => s.trim()).filter((s) => s) }));
      },
      exportDialog() {
        const s = this.sw || {};
        this.openModal("CSV に書き出す", [
          { key: "path", label: "書き出し先（サーバーの PC の上のパス）", value: joinPath(this.info.default_dir, nowName() + ".csv") },
          { key: "start", label: "Start（空なら全部）", value: s.start_hz != null ? U.freqFieldText(s.start_hz) : "" },
          { key: "stop", label: "Stop（空なら全部）", value: s.stop_hz != null ? U.freqFieldText(s.stop_hz) : "" },
        ], (v) => {
          const args = { path: v.path };
          try {
            if (v.start.trim()) args.start_hz = U.parseFreqHz(v.start);
            if (v.stop.trim()) args.stop_hz = U.parseFreqHz(v.stop);
          } catch (e) { this.note(null, "読めない値です（例: 1.2G・300k・1500）"); return; }
          this.cmd("export_csv", args);
        }, "見えているトレースを書き出します");
      },
      legacyDialog() {
        const serial = (this.conn.identity || {}).serial;
        if (!serial) { this.note(null, "校正を当てる機器を決めるため、先に機器に接続してください。"); return; }
        this.openModal("旧校正ファイル（gigast.txt / ba205.txt）", [
          { key: "path", label: "ファイル（サーバーの PC の上のパス）", value: "" },
        ], (v) => this.cmd("import_legacy_calibration", { path: v.path, serial }, { ok: () => this.note(null, "旧校正ファイルを取り込みました") }),
        `このファイルを、接続中の機器（${serial}）の校正として取り込みます`);
      },
      recordFinishDialog() {
        this.openModal("記録をまとめる", [
          { key: "path", label: "保存先（サーバーの PC の上のパス）", value: joinPath(this.info.default_dir, nowName() + ".h5") },
        ], (v) => this.cmd("record_finish", { path: v.path }));
      },
      async connectDialog() {
        const r = await this.cmd("list_devices", { backend: "gigast" });
        if (!r) return;
        const devs = r.devices || [];
        this.modal = {
          title: "機器に接続", devices: devs, pick: devs.length ? 0 : -1, fields: [],
          note: devs.length
            ? "USB ID が FTDI 0403:6001 のポートを並べています。この部品は GigaSt 以外の機器にも使われるので、初めての機器は確認の掃引を1回送り、GigaSt の応答が返るかを確かめてから繋ぎます。"
            : "GigaSt が見つかりません。USB ケーブルを確かめてください。",
          ok: (v, m) => {
            const d = devs[m.pick];
            if (!d) return;
            this.note(null, "接続しています…");
            this.cmd("connect", { backend: "gigast", serial: d.serial, port: d.port });
          },
        };
      },

      // ------------------------------------------------------------ PNG（設計I。Web は canvas で描く）
      pngBlob(note) {
        return GSPng.render({
          st: this.st, markers: this.markers, amp: { ...this.amp, unit: this.axisUnit },
          traces: (colors) => this.graphTraces(colors), traceStates: (this.st && this.st.traces) || [],
          appVersion: this.info.version, colors: LIGHT, rows: this.markerRowsView, note,
        });
      },
      // 画像を作る前にメモ（1行）を打てる（I §3.2 の 5）。配色はライトの初期値（I §3.1 の既定）
      pngSave() {
        this.openModal("PNG を保存", [{ key: "note", label: "メモ（1行。空なら出さない）", value: "" }], async (v) => {
          const blob = await this.pngBlob(v.note);
          const a = document.createElement("a");
          a.href = URL.createObjectURL(blob);
          a.download = nowName() + ".png";
          a.click();
          setTimeout(() => URL.revokeObjectURL(a.href), 5000);
          this.note(null, `保存しました: ${a.download}（ブラウザの保存先）`);
        });
      },
      pngCopy() {
        if (this.clipboardReason) { this.note(null, this.clipboardReason); return; }
        this.openModal("PNG をクリップボードへ", [{ key: "note", label: "メモ（1行。空なら出さない）", value: "" }], (v) => {
          // 画像を作る間にクリック直後の許可が切れないよう、作りかけの Promise のまま渡す
          navigator.clipboard.write([new ClipboardItem({ "image/png": this.pngBlob(v.note) })]).then(
            () => this.note(null, "PNG をクリップボードへ置きました"),
            (e) => this.note(null, `コピーできませんでした（${e}）`));
        });
      },

      // ------------------------------------------------------------ メニュー（J §4.3.1。Qt のメニューバーと同じ並び）
      buildMenu() {
        const I = (code, label, run, why = null) => ({ code, label, run, why, keys: KEYS[code] || [], graphOnly: GRAPH_ONLY.has(code) });
        const noMarker = this.selectedMarker ? null : "マーカーを選んでください（表の行か、グラフのクリック）";
        const rec = (this.st && this.st.recording) || {};
        return [
          { title: "ファイル", items: [
            I("open", "開く…", () => this.openDialog()),
            I("save", "保存…", () => this.saveDialog()),
            I("export", "書き出し…", () => this.exportDialog()),
            null,
            I("png_save", "PNG を保存…", () => this.pngSave(), this.st ? null : "状態がまだありません"),
            I("png_copy", "PNG をクリップボードへ", () => this.pngCopy(), this.clipboardReason),
            null,
            I("record_start", "記録を始める", () => this.cmd("record_start"), rec.active || rec.stopped ? X.get("ERRORS", "already_recording") : null),
            I("record_stop", "記録を止める", () => this.cmd("record_stop"), rec.active ? null : X.get("ERRORS", "not_recording")),
            I("record_finish", "記録をまとめる…", () => this.recordFinishDialog(), rec.active || rec.stopped ? null : X.get("ERRORS", "not_recording")),
          ] },
          { title: "編集", items: [
            I("undo", "条件の変更を戻す", () => this.cmd("undo", {}, { quiet: ["nothing_to_undo"] }), this.st && this.st.undo && !this.st.undo.can_undo ? "戻す変更がありません" : null),
            I("redo", "条件の変更をやり直す", () => this.cmd("redo", {}, { quiet: ["nothing_to_redo"] }), this.st && this.st.undo && !this.st.undo.can_redo ? "やり直す変更がありません" : null),
          ] },
          { title: "機器", items: [
            I("connect", "接続…", () => this.connectDialog()),
            I("connect_sim", "シミュレータに接続", () => this.cmd("connect", { backend: "simulator", serial: "SIM00001" })),
            I("disconnect", "切断", () => this.cmd("disconnect")),
            null,
            I("self_test", "自己診断の結果", null, NOT_YET_DIAG),
          ] },
          { title: "掃引", items: [
            I("run_cont", "連続", () => this.runCont(), this.connected ? null : X.get("ERRORS", "not_connected")),
            I("run_single", "単発", () => this.runSingle(), this.connected ? null : X.get("ERRORS", "not_connected")),
            I("stop", "停止", () => this.stop()),
            I("pause", "連続 ⇔ 停止", () => this.toggleRun()),
          ] },
          { title: "モード", items: MODES.map(([c, l, n, why]) => I("mode_" + c, `${l}（${n}）`, () => this.setMode(c), why)) },
          { title: "振幅", items: [I("ref_auto", "REF レベルを波形に合わせる（自動）", () => this.refAuto(), this.refAutoReason())] },
          { title: "マーカー", items: [
            I("marker_peak", "選んでいるマーカーをピークへ", () => this.markerPeak("highest")),
            I("marker_peak_left", "次のピーク（左）", () => this.markerPeak("left"), noMarker),
            I("marker_peak_right", "次のピーク（右）", () => this.markerPeak("right"), noMarker),
            I("marker_left", "マーカーを左へ1点（Shift で10点）", () => this.markerStep(-1), noMarker),
            I("marker_right", "マーカーを右へ1点（Shift で10点）", () => this.markerStep(1), noMarker),
            I("marker_here", "マウスの位置に M を置く", () => this.markerHere()),
          ] },
          { title: "校正", items: [
            I("cal_freq", "周波数特性…", null, NOT_YET_CAL),
            I("cal_rbw", "RBW…", null, NOT_YET_CAL),
            I("cal_nf", "NF の増幅度…", null, NOT_YET_CAL),
            null,
            I("cal_legacy", "旧校正ファイルを読み込む…", () => this.legacyDialog()),
          ] },
          { title: "表示", items: [
            I("view_colors", "配色…", null, NOT_YET_VIEW),
            I("view_waterfall", "ウォーターフォール", null, NOT_YET_VIEW),
            I("view_density", "密度表示", null, NOT_YET_VIEW),
            null,
            ...Object.keys(PART_NAMES).map((p) => I("detach_" + p, `${PART_NAMES[p]}を別ウィンドウで開く`, () => this.detach(p), this.part ? "別ウィンドウの中からは開けません" : null)),
          ] },
          { title: "設定", items: [
            I("settings", "アプリ設定…", null, NOT_YET_SETTINGS),
            I("settings_keys", "キー操作…", null, NOT_YET_SETTINGS),
          ] },
          { title: "ヘルプ", items: [
            I("help_keys", "キー操作の一覧", () => { this.modal = { title: "キー操作の一覧", keys: true, fields: [] }; }),
            I("help_about", "pyGigaSt について", () => { this.modal = { title: "pyGigaSt について", note: `pyGigaSt ${this.info.version || ""} — GigaSt v5 を PC から操作・表示するアプリ`, fields: [] }; }),
          ] },
        ];
      },
      itemTitle(it) {
        let t = it.label;
        if (it.keys.length) t += `（${it.keys.map(keyLabel).join("・")}）`;
        if (it.why) t += ` — ${it.why}`;
        return t;
      },
      menuItem(code) {
        for (const s of this.menu) for (const it of s.items) if (it && it.code === code) return it;
        return null;
      },
      trigger(code) {
        const it = this.menuItem(code);
        this.menuOpen = false;
        if (!it) return;
        if (it.why || !it.run) { if (it.why) this.note(null, it.why); return; }
        it.run();
      },
      keyHint(code) { const k = KEYS[code]; return k ? `（${k.map(keyLabel).join("・")}）` : ""; },
      onKey(e) {
        const k = keyOf(e);
        const tag = (e.target && e.target.tagName) || "";
        const inField = ["INPUT", "SELECT", "TEXTAREA"].includes(tag);
        const inGraph = this.$graph && this.$graph.u.root.contains(e.target);
        if (k === "Escape") { this.menuOpen = false; this.ctx = null; if (this.modal && !inField) this.modal = null; }
        if (this.modal) return;
        for (const [code, keys] of Object.entries(KEYS)) {
          if (!keys.includes(k)) continue;
          // 入力の欄にいる間は、Ctrl・Alt の付かないキーと Ctrl+Z/Y は欄に渡す（J §4.6）
          const plain = !e.ctrlKey && !e.altKey && !e.metaKey;
          if (inField && (plain || code === "undo" || code === "redo")) return;
          if (GRAPH_ONLY.has(code) && !inGraph) return;
          e.preventDefault();
          if (code === "marker_left" || code === "marker_right") { this.markerStep((code === "marker_left" ? -1 : 1) * (e.shiftKey ? 10 : 1)); return; }
          this.trigger(code);
          return;
        }
      },

      // ------------------------------------------------------------ 別ウィンドウ（J §4.3.2）
      detach(p) {
        const w = window.open(`/?part=${p}`, `pygigast_${p}`, "width=960,height=640");
        if (!w) { this.note(null, "ブラウザが新しいウィンドウを止めました（ポップアップの許可が要ります）"); return; }
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
      showTab(t) { return this.part ? this.part === "tab-" + t : this.tab === t && !this.detached["tab-" + t]; },

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
      keysText(it) { return it.keys.map(keyLabel).join("・"); },
      offsetDefs(m) {
        // 表の子の行（± は2行）から、消すときの番号（0 始まり）ごとの1項目にまとめる
        const out = [];
        const seen = new Set();
        for (const r of m.freq_offsets || []) {
          const i = parseInt(r.id.split(".f")[1], 10) - 1;
          if (seen.has("f" + i)) continue;
          seen.add("f" + i);
          out.push({ key: "f" + i, type: "freq", index: i, label: `周波数 ${r.sign === "-" ? "−" : r.sign}${U.formatFreq(Math.abs(r.offset_hz))}` });
        }
        for (const r of m.level_offsets || []) {
          const i = parseInt(r.id.split(".l")[1], 10) - 1;
          if (seen.has("l" + i)) continue;
          seen.add("l" + i);
          out.push({ key: "l" + i, type: "level", index: i, label: `レベル ${r.sign === "-" ? "−" : r.sign}${Math.abs(r.offset_db)} dB` });
        }
        return out;
      },
      fmtFreq(hz) { return hz == null ? "" : U.formatFreq(hz); },
      fmtLevel,
      T(dict, code) { return X.get(dict, code); },
      Td(dict) { return X.dict(dict); },
      taskLabel(t) { return X.taskLabel(t); },
      legend(t) { return X.legendText(t); },
    },
  });

  app.component("num-field", GSFields.NumField);
  window.addEventListener("DOMContentLoaded", () => { window.gsApp = app.mount("#app"); });
})();
