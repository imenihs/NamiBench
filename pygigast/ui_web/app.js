// Web のメイン画面（設計J §4.3 の案B。Qt の ui_qt/main_window.py と同じ構成・同じ語・同じ操作: J §5）。
// コアへは Web の口（api.js）だけを使う。状態の正本はコアの1つで、変更は全部の画面に state で届く（A §4.4）。
// 文言はここに書かない: 表は /api/v1/texts、状態から作る文はサーバーが Qt と同じ関数で作って送る（view.py）。
// 受信（RTL-SDR）・聞く・rtl_tcp の設定は app_rtl.js、メニューとキーは app_menu.js、マーカー表の上のボタン・行の「操作」・
// ピークの一覧は app_markers.js に分けて、ここで合わせる。
(function () {
  "use strict";

  const { createApp, markRaw } = Vue;
  const U = GSUnits;
  const X = GSTexts;
  const W = (k, kw) => X.w(k, kw);
  // 回数を持つ動作と、平均を選んだときの既定の回数（設計F §2。Qt の tab_traces と同じ）
  const COUNTED = ["average", "max_hold", "min_hold"];
  const DEFAULT_AVERAGE_COUNT = 16;
  // イメージ除去の正確・スピードを選んだときの注意を出しておく長さ（Qt の tab_traces.IC_NOTE_MS と同じ）
  const IC_NOTE_MS = 10000;
  const call = GSApi.call;

  const DB_PER_DIV_CHOICES = [1, 2, 5, 10];
  // 周波数軸の上のホイール1段で Span を何倍にするか（奥へ回すと拡大 = Span を狭く。Qt と同じ）
  const ZOOM_STEP = 0.8;
  // マウスの位置の読み値を取り直す最短の間隔（ミリ秒）。1秒に30回まで（Qt の mouse_reading.py と同じ）
  const HOVER_MS = 34;
  // 参照トレース（ファイル）の色。J §4.4 に無いので、トレースの色を順に使い点線で描く（設計に無い判断）
  const FILE_COLORS = ["trace2", "trace3", "trace4", "trace1"];
  const PARTS = ["graph", "waterfall", "density", "markers", "tab-trace", "tab-marker", "tab-view", "tab-source", "peaks"];
  // 上段の周波数の欄の幅を決める値の見本（Qt の sizes.TOP_FREQ_SAMPLES と同じ。100Hz 単位の最長の形と、RTL-SDR の
  // 1Hz 単位の形）。一番広い見本で測る
  const TOP_FREQ_SAMPLES = ["-12100.0000 MHz", "0000.000000 MHz"];

  function nowName() {
    const d = new Date();
    const p = (n) => String(n).padStart(2, "0");
    return `gigast_${d.getFullYear()}${p(d.getMonth() + 1)}${p(d.getDate())}_${p(d.getHours())}${p(d.getMinutes())}${p(d.getSeconds())}`;
  }
  function fmtLevel(v, unit) { return v == null ? "" : `${v.toFixed(2)} ${unit || ""}`.trim(); }
  // 控えめな色で描く点（Qt の plot_layers.marked_points と同じ: 前置き不足・校正の範囲外・DIM_POINT_FLAGS の印）。
  // DIM_POINT_FLAGS は Qt の plot_layers.DIM_POINT_FLAGS と同じ（NF の HOT と COLD の差が小さい点: 設計M §1）。
  // 点ごとの印は [始まり, 終わり) の組の並びで届く（api/web/server.py の encode_trace）
  const DIM_POINT_FLAGS = ["transient", "nf_small_difference"];
  const dimCache = new WeakMap();
  function dimMarks(t) {
    if (dimCache.has(t)) return dimCache.get(t);
    const fl = t.flags || {};
    const n = (t.values || []).length;
    const m = new Uint8Array(n);
    let any = false;
    const put = (s, e) => {
      for (let i = Math.max(0, s); i < Math.min(n, e); i++) { m[i] = 1; any = true; }
    };
    put(0, fl.transient_head_points || 0);
    const pts = fl.points || {};
    for (const ranges of [fl.out_of_cal_range, ...DIM_POINT_FLAGS.map((k) => pts[k])]) {
      if (Array.isArray(ranges)) for (const r of ranges) if (Array.isArray(r)) put(r[0], r[1]);
    }
    const out = any ? m : null;
    dimCache.set(t, out);
    return out;
  }

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
        menuOpen: false,  // false・メニューバーの開いている題の番号・true（「≡」で全部）
        ctx: null,
        modal: null,
        tab: "trace",
        selectedMarker: null,
        selectedRow: null,  // マーカー表で選んだ行（子の行を含む。Delete はこの行だけを消す: G §3.7）
        editCell: null,  // ダブルクリックで直しているセル {id, col, text}
        traceNotes: {},
        icNote: false,  // イメージ除去の正確・スピードを選んだときだけ一度出す注意（ずっとは出さない。ツールチップにもある）
        fileHidden: {},
        progress: {},
        // 上段の掃引の進み具合（J §4.13。サーバーの screen_model.sweep_progress_view: Qt と同じ）
        sweepProgress: { visible: false, percent: 0, text: "" },
        waiting: "",
        detached: {},
        off: { type: "freq", sign: "±", delta: "" },
        // マーカーの機能を切っている間の値（Qt の tab_markers と同じ初期値。入れている間はマーカーの値を出す: fnView）
        fn: { bw1: 3, bw2: 60, thr: -20, side: "below" },
        fnSent: null,  // 送って戻りを待っている機能（fnView・fnSend）
        // dB の打てるドロップダウンの一覧と打てる範囲（G §3.6。fields.js に Qt の fields.py と同じ値で置く）
        dbLists: { bw: GSFields.BANDWIDTH_CHOICES, floor: GSFields.FLOOR_CHOICES, ref: GSFields.REF_CHOICES },
        dbRanges: GSFields.DB_RANGES,
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
      // RBW の欄の注意の色とツールチップ（信用できない条件の文・選べない RBW の理由・窓の説明。サーバーの
      // screen_model.rbw_field_view: Qt と同じ）
      rbwField() { return this.ui.rbw_field || { warn: false, tip: "" }; },
      sweepReasons() { return this.ui.sweep_reasons || {}; },
      // イメージ除去の選択（サーバーの screen_model.image_reject_view: 選択肢の文字・いまの値・使えない理由）
      rejectView() {
        const s = this.sw || {};
        return this.ui.image_reject || { items: [], value: s.image_reject, reason: this.sweepReasons.image_reject || null };
      },
      // 1目盛の選択肢（dB/div か、|Γ|・SWR の /div: 設計R §3。サーバーの screen_s11.axis_view）
      divChoices() { return this.axis.div_choices; },
      sw() { return (this.st && this.st.sweep) || null; },
      run() { return (this.st && this.st.run) || {}; },
      conn() { return (this.st && this.st.connection) || {}; },
      connected() { return this.conn.state === "connected"; },
      running() { return ["continuous", "single"].includes(this.run.state); },
      amp() {
        const a = (this.st && this.st.amplitude) || {};
        const l = this.ampLocal || {};
        const pick = (k, d) => (l[k] !== undefined ? l[k] : a[k] != null ? a[k] : d);
        // |Γ|・SWR の縦軸は下端が決まっていて、一番上 = 下端 + 10 目盛（設計R §3。app_s11.js）
        if (this.axis.linear) return this.linearAmp(a, pick);
        return { ref: pick("ref_level", 0), div: pick("db_per_div", 10), unit: a.unit, linear: false, decimals: 1 };
      },
      // 縦軸の単位 = 表示している測定中のトレースの最初の単位（Qt と同じ screen_s11.axis_unit。サーバーが ui.axis に入れる）
      axisUnit() { return this.axis.unit || this.amp.unit || this.ui.unit || "dBm"; },
      // Center・Start・Stop は周波数オフセットを足して出す（設計O。Span は幅、SG は機器が出す周波数なので足さない）。
      // 再生中は再生の表示の範囲（設計P §5: app_display.js の fv）
      freqText() {
        const s = this.sw;
        const f = this.fv;
        if (!s || !f) return { center: "", span: "", start: "", stop: "", sg: "" };
        const o = this.freqOff;
        return {
          center: U.freqFieldText(f.center_hz + o), span: U.freqFieldText(f.span_hz),
          start: U.freqFieldText(f.start_hz + o), stop: U.freqFieldText(f.stop_hz + o),
          sg: s.sg_hz != null ? U.freqFieldText(s.sg_hz) : "",
        };
      },
      // 負号は見間違えない字形「−」で出す（J §4.1）。打つときは「-」でも「−」でもよい
      refText() { return `${U.levelText(this.amp.ref)} ${this.axisUnit}`; },
      // 上段の REF の欄の文字の幅: いまの単位で一番長い値「−120.0 単位」（Qt の DbChoiceField._fit_width と同じ）。
      // 欄の幅は style.css がこれに左右の余白と ▼ を足す
      refTextW() {
        if (!this.textsReady) return 0;
        return Math.ceil(GSFields.textWidth(`${U.levelText(-120)} ${this.axisUnit}`, GSFields.rootPx("--font-l-value")));
      },
      // REF レベルの一覧（J §4.1: +20〜−120 を 10dB 刻み。Qt の fields.REF_CHOICES と同じ）
      refChoices() {
        const out = [];
        for (let v = 20; v >= -120; v -= 10) out.push({ value: v, text: U.minusSign(v > 0 ? `+${v}` : `${v}`) + " " + this.axisUnit });
        return out;
      },
      // 上段の「基準を取る」（J §4.2。出すか・理由・文はサーバーの screen_model.ref_quick_view）
      refQuick() { return this.ui.ref_quick || { visible: false }; },
      spanLockText() {
        const s = this.sw;
        if (!s || s.span_lock_hz == null) return W("span_lock");
        return s.span_lock_hz !== s.span_hz ? W("span_locked_at", { span: U.formatFreq(s.span_lock_hz) }) : W("span_locked");
      },
      condLines() { return this.ui.cond || []; },
      freqOpsReason() {
        const s = this.fv;
        if (!s) return X.get("REASON_TEXTS", "no_sweep");
        if (s.step_hz === 0) return X.get("REASON_TEXTS", "zero_span_axis");
        return null;
      },
      // マーカーを置けない理由（ゼロスパンでも時間の位置に置ける: 設計N §1）
      markerReason() { return this.sw ? null : X.get("REASON_TEXTS", "no_sweep"); },
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
        return ((this.st && this.st.traces) || []).map((t) => ({ ...t, color: this.palette["trace" + t.number] }));
      },
      // 画面の配色はコアの配色（ユーザー設定の1つ。Qt と共有: J §4.4）。状態が届くまではダークの初期値
      palette() { return (this.st && this.st.palette && this.st.palette.colors) || (this.colors && this.colors.dark) || {}; },
      markerRows() { return this.markers.rows || []; },
      selectedRowId() {
        const rows = this.markerRows;
        return this.selectedRow && rows.some((r) => r.id === this.selectedRow) ? this.selectedRow : this.selectedMarker;
      },
      markerColumns() { return this.markers.columns || X.list("MARKER_TABLE_COLUMNS"); },
      selMarker() { return (this.markers.markers || []).find((m) => m.id === this.selectedMarker) || null; },
      files() { return (this.st && this.st.files) || []; },
      clipboardReason() { return window.isSecureContext && navigator.clipboard && window.ClipboardItem ? null : W("no_clipboard"); },
      partTitle() { return this.part ? W("part_" + this.part) : ""; },
      layoutStyle() { return { "--split": this.splitPct + "%", "--side": this.sidePx + "px", "--lower": this.lowerPct + "%" }; },
      tabList() { return ["trace", "marker", "view", "source"].map((t) => [t, W("tab_" + t)]); },
    },
    created() {
      this.$traces = new Map(); // トレースの値（大きいので Vue の反応の外に置く）
      try {
        const s = JSON.parse(localStorage.getItem("pygigast.layout") || "{}");
        if (s.splitPct) this.splitPct = s.splitPct;
        if (s.sidePx) this.sidePx = s.sidePx;
        if (s.lowerPct) this.lowerPct = s.lowerPct;
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
      this.watchPolarityStore();
      this.watchThSpec();
      this.textsReady = true;
      this.$nextTick(() => this.makeGraph());
      this.fitColumns();
      this.stream = new GSApi.EventStream({
        onOpen: () => { this.wsOpen = true; },
        onClose: () => { this.wsOpen = false; },
        onEvent: (k, p) => this.onEvent(k, p),
      });
      this.stream.open();
      window.addEventListener("keydown", (e) => this.onKey(e));
      // 外を押したら右クリックのメニューとメニューバー・「≡」のメニューを閉じる（Qt と同じ）
      window.addEventListener("click", () => { this.ctx = null; this.menuOpen = false; });
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
        // Span 固定を外した知らせは Span の欄に出す（固定の切り替えは Span の欄にある: 設計C §5.13。Qt と同じ）
        const fields = { start_hz: "start", stop_hz: "stop", center_hz: "center", span_hz: "span", span_lock: "span", rbw_hz: "rbw", sg_hz: "sg", points: "points" };
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
        if (this.refBlocked()) return;
        let v;
        try { v = U.parseDb(String(text).replace(this.axisUnit, "")); } catch (e) { this.note("ref", W("bad_value")); return; }
        this.setAmp({ ref_level: Math.round(v * 100) / 100 });
      },
      stepRef(exp, dir) { if (this.refBlocked()) return; this.setAmp({ ref_level: Math.round((this.amp.ref + dir * 10 ** exp) * 100) / 100 }); },
      setDiv(v) { this.setAmp({ [this.axis.div_key || "db_per_div"]: Number(v) }); },
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
        if (this.axis.ref_reason) return { level: null, reason: this.axis.ref_reason };
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
      // 基準の範囲外の帯（設計E §6.4: 値なしの区間を注意の色の帯で示す。Qt の plot_layers.set_bands と同じ区間・色。
      // 説明の文字は持たない: 範囲は状態バーの「基準の範囲外」）
      normBands() { return (this.ui.norm_bands || []).map(([lo, hi]) => ({ start_hz: lo, stop_hz: hi, label: "", fill: this.palette.warn })); },
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
          // px_hz: 1画素の周波数の幅（内部の漏れの印をマウスで拾う幅: 設計L §1.4。Qt と同じ）
          const r = await call("mouse_reading", { x: m.x, y: m.y, px_hz: this.$graph ? this.$graph.pxHz() : 0 });
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
          // 状態は画面の描き直し1回に1つだけ映す（掃引の速い機器で状態が描くより速く届いても、描画を止めない: 設計P §8）
          if (this.st && p.version < this.st.version) return;
          if (this.$pendingState && p.version < this.$pendingState.version) return;
          this.$pendingState = p;
          this.$pendingMarkersSeq = this.$markersSeq || 0;
          GSOnFrame(this, "$stateFrame", () => {
            const s = this.$pendingState;
            this.$pendingState = null;
            if (s) this.applyStateEvent(s, (this.$markersSeq || 0) === this.$pendingMarkersSeq);
          });
        } else if (kind === "markers") {
          this.$markersSeq = (this.$markersSeq || 0) + 1;
          this.markers = p;
          if (this.selectedMarker && !(p.markers || []).some((m) => m.id === this.selectedMarker)) this.selectedMarker = null;
          this.drawMarkers();
        } else {
          this.onOtherEvent(kind, p);
        }
      },
      // 状態を映す。markers が後から届いていれば、状態の中の古いマーカー表で上書きしない
      applyStateEvent(p, takeMarkers) {
        if (this.st && p.version < this.st.version) return;
        this.st = p;
        if (p.markers && takeMarkers) this.markers = p.markers;
        if (this.ampLocal) {
          const a = p.amplitude || {};
          if (Object.keys(this.ampLocal).every((k) => a[k] === this.ampLocal[k]) && !this.ampBusy) this.ampLocal = null;
        }
        const alive = new Set((p.tasks || []).map((t) => t.task_id));
        for (const id of Object.keys(this.progress)) if (!alive.has(id)) delete this.progress[id];
        if (!this.running && this.run.state !== "stopping") this.waiting = "";
        this.onStateRtl(p);
        this.onStateDevices(p);
        this.onStateCal(p);
        this.onStateS11(p);
        this.onStateFiles(p);
        this.applyLook();
        this.watchPaletteOrigin();
        this.applyX();
        this.applyY();
        this.applyDisplay();  // ウォーターフォール・密度・再生（設計P: app_display.js）
        this.redraw();
      },
      onOtherEvent(kind, p) {
        if (kind === "trace") {
          this.$traces.set(String(p.trace), markRaw(p));
          this.traceVersion++;
          this.applyY(); // 値の単位（dBm ⇔ dB）は届いたトレースで変わる。縦軸の名前を合わせる
          this.redraw();
        } else if (kind === "audio") {
          this.onAudio(p);
        } else if (kind === "waterfall") {
          this.onWaterfall(p);
        } else if (kind === "density") {
          this.onDensity(p);
        } else if (kind === "devices") {
          this.onDevices(p);
        } else if (kind === "notice") {
          if (this.onNoticeNf && this.onNoticeNf(p)) return;  // NF に初めて入った: つなぎ方の窓（app_nf.js）
          this.onNoticeFiles(p);
          this.onNoticeS11(p);
          const v = p.view || { text: p.code, traces: {} };
          for (const [n, text] of Object.entries(v.traces || {})) this.traceNote(Number(n), text);
          this.onViewNotice(v); // 単位が変わった（欄の下）・比の表示に変わった（窓）: J §4.10。app_settings.js
          this.note(null, v.text);
        } else if (kind === "sweep_progress") {
          this.sweepProgress = p.ui;
        } else if (kind === "progress") {
          if (p.kind === "task") this.progress = { ...this.progress, [p.task_id]: p };
          else if (p.kind === "waiting_sweep_end") {
            this.waiting = p.text;
            this.note(null, this.waiting);
          }
        }
      },
      // イメージ除去を選んだ（設計F §7.4。Qt の tab_traces と同じ）: 同じ値なら何もしない。正確・スピードは注意を
      // IC_NOTE_MS だけ出し、切るは注意を消す
      setImageReject(code) {
        if (code === this.rejectView.value) return;
        this.sweep({ image_reject: code });
        clearTimeout(this._icNoteTimer);
        this.icNote = code === "accurate" || code === "fast";
        if (this.icNote) this._icNoteTimer = setTimeout(() => { this.icNote = false; }, IC_NOTE_MS);
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
          onBookmark: (hz) => this.centerOn(hz),
          onContext: (x, y, cx, cy) => this.onGraphContext(x, y, cx, cy),
          onHover: (m) => this.onGraphHover(m),
        }));
        this.applyX();
        this.applyY();
        this.redraw();
        this.$nextTick(() => this.makeLower());
      },
      applyX() {
        const g = this.$graph;
        const s = this.fv;  // 再生中は再生の表示の範囲（設計P §5）
        if (!g || !s) return;
        // ゼロスパンは時間（推定）の軸（範囲・名前はサーバーの ui.x_axis: Qt と同じ screen_model.x_axis_view）
        const xa = this.ui.x_axis;
        if (xa && xa.kind === "time") g.setX(xa.x0, xa.x1, xa);
        else g.setX(s.start_hz, s.stop_hz, null);
        g.setXOffset(s.step_hz === 0 ? 0 : this.freqOff, this.ui.freq_offset_text || "");
      },
      applyY() {
        if (this.$graph) this.$graph.setY(this.amp.ref, this.amp.div, this.axisUnit, this.amp.linear, this.amp.decimals);
        // 密度の縦はグラフの縦軸と同じ範囲（|Γ|・SWR は下端 + 10 目盛: 設計R §3）
        if (this.$dens) this.$dens.setY(this.amp.ref, this.amp.div, this.axisUnit, this.amp.decimals);
      },
      // 縦軸と単位が同じか（1つの目盛りに違う単位を混ぜない: E §6.4・設計R §3。Qt の TraceLayer と同じ）
      onAxis(t) { return !t || !t.unit || t.unit === this.axisUnit; },
      traceShown(n) {
        const t = ((this.st && this.st.traces) || []).find((x) => x.number === n);
        return !!(t && t.visible && t.has_values);
      },
      shownFiles(colors) {
        // 重ねている参照トレース（見えているもの）と色。凡例の文字は Qt と同じ（サーバーの ui.file_legend）。PNG の凡例にも
        // 同じものを渡す。単位が縦軸と違うものは重ねない（色の順は変えない: Qt の plot_layers.file_drawn と同じ）
        const out = [];
        const names = this.ui.file_legend || {};
        let i = 0;
        for (const f of this.files) {
          for (const tr of f.traces) {
            const color = FILE_COLORS[i++ % FILE_COLORS.length];
            // 縦軸と単位の違う参照トレースは重ねない（チェックは理由付きで無効: fileUnitReason。Qt の file_unit_ok と同じ）
            if (!this.fileHidden[tr.id] && !this.fileUnitReason(tr.id)) out.push({ id: tr.id, colorKey: color, color: colors[color], label: names[tr.id] || X.fmt("file_trace_label", { name: f.name, number: tr.number }) });
          }
        }
        return out;
      },
      // 1つの目盛りに比（dB）と電力（dBm など）を混ぜない（E §6.4）。単位が縦軸と違う参照トレースは重ねず、チェックを
      // 無効にして理由を出す（U-08。Qt の tab_traces.update_file_checks と同じ語）。値がまだ届いていないものは null
      fileUnitReason(id) {
        void this.traceVersion;
        const t = this.$traces && this.$traces.get(id);
        if (!t || !t.unit || t.unit === this.axisUnit) return null;
        return W("file_unit_differs", { axis: this.axisUnit, unit: t.unit });
      },
      // 線の名前（設計F §9: 状態の traces[].line・参照トレースは reference）から描き方（core/report_layout.LINE_STYLES）
      lineStyle(code) {
        const styles = this.info.line_styles || {};
        return styles[code] || styles.solid || { width: 1, dash: null, z: 1 };
      },
      traceLine(n) {
        const t = ((this.st && this.st.traces) || []).find((x) => x.number === n);
        return (t && t.line) || "solid";
      },
      // 描く順に並べる（下から: 参照トレース → 細線 → 実線・破線・点線 → 太線。同じ段は番号順: 設計F §9。Qt の重なりと同じ）
      graphTraces(colors) {
        const out = [];
        for (const f of this.shownFiles(colors)) {
          const t = this.$traces.get(f.id);
          if (t) out.push(this.graphTrace(t, f.color, colors, true, this.lineStyle("reference")));
        }
        const live = [];
        for (const n of [1, 2, 3, 4]) {
          const t = this.$traces.get(String(n));
          if (!t || !this.traceShown(n) || !this.onAxis(t)) continue;
          const style = this.lineStyle(this.traceLine(n));
          live.push([style.z, n, this.graphTrace(t, colors["trace" + n], colors, false, style)]);
        }
        live.sort((a, b) => a[0] - b[0] || a[1] - b[1]);
        return out.concat(live.map((x) => x[2]));
      },
      graphTrace(t, color, colors, isFile, style) {
        return {
          x: GSZeroSpan.traceX(t.axis), y: t.values, color, minHold: t.action === "min_hold",
          head: isFile ? 0 : (t.flags && t.flags.transient_head_points) || 0, fill: !isFile && this.drawFill,
          mark: isFile ? null : dimMarks(t),
          dimColor: colors.text_dim, style,
          // ステップ掃引を全部重ねた段ごとの線（設計L §1.3。{x0, dx, rows}。無ければ null）
          steps: !isFile && t.steps && t.steps.rows ? t.steps : null,
        };
      },
      // グラフの中の凡例（Qt と同じ中身と並び: 表示中のトレースを番号順、その後に参照トレース）
      legendItems() {
        const legend = this.ui.legend || {};
        const out = [];
        for (const n of [1, 2, 3, 4]) {
          if (this.$traces.get(String(n)) && this.traceShown(n) && this.onAxis(this.$traces.get(String(n)))) out.push({ text: legend[String(n)] || "", color: this.palette["trace" + n], style: this.lineStyle(this.traceLine(n)) });
        }
        for (const f of this.shownFiles(this.palette)) {
          if (this.$traces.get(f.id)) out.push({ text: f.label, color: f.color, style: this.lineStyle("reference") });
        }
        return out;
      },
      redraw() {
        if (this.rafPending) return;
        this.rafPending = true;
        requestAnimationFrame(() => {
          this.rafPending = false;
          if (!this.$graph) return;
          this.$graph.setBands([...this.normBands(), ...this.marks()]);
          this.$graph.setLeaks(this.leakMarks());  // 内部の漏れの印（設計L §1.4。app_step.js）
          // バンドプランの帯とブックマークの印（設計P §6。中身はサーバーの display_view.bandplan_view）
          this.$graph.setBandplan(this.bpView.bands, this.bpView.bookmarks);
          this.$graph.setTraces(this.graphTraces(this.palette));
          this.$graph.setLegend(this.legendItems());
          this.drawMarkers();
        });
      },
      drawMarkers() {
        if (!this.$graph) return;
        this.$graph.setMarkers(this.markers.markers || [], this.selectedMarker);
        this.$graph.setXY(this.markers.xy);
        this.$graph.setCursorRows(this.markerRows);
        // マーカーの機能の線（G §3.6）。出すかはコアの表示の設定 view.function_lines（Qt と共有。無ければ出す）
        this.$graph.setFunctionLines(this.viewOpts.function_lines === false ? [] : this.markerRows);
      },
      onRangeSelected(a, b) {
        // ドラッグは Start と Stop を同時に触ったものとして扱う（C §5.1）。Span 固定の規則はコアが当てる（C §5.12）
        if (this.freqOpsReason) return;
        this.sweep({ start_hz: Math.round(Math.min(a, b)), stop_hz: Math.round(Math.max(a, b)), edited: ["start_hz", "stop_hz"] }, "start");
      },
      onFreqWheel(x, steps) {
        const s = this.sw;
        const f = this.fv;
        if (this.freqOpsReason || !s || !f) return;
        // Span 固定中は幅を変えない。命令を送らず、Span の欄で変えることをその場で知らせる（C §5.12。Qt と同じ）。
        // 再生中は表示の範囲だけを変えるので固定によらない（設計P §5）
        if (s.span_lock_hz != null && !f.playback) { this.note("span", W("span_lock_wheel")); return; }
        // カーソルの位置の周波数を画面上の同じ位置に保って拡大・縮小する（Qt と同じ）
        const k = ZOOM_STEP ** steps;
        const ns = x - (x - f.start_hz) * k;
        const ne = x + (f.stop_hz - x) * k;
        const span = Math.max(Math.round(ne - ns), 1);
        const center = Math.round(ns) + Math.floor(span / 2);
        this.sweep({ center_hz: center, span_hz: span, edited: ["center_hz", "span_hz"] }, "span");
      },
      onLevelWheel(steps, ctrl) {
        if (this.axis.linear) { this.stepLinearDiv(steps); return; }  // |Γ|・SWR はいつも1目盛を変える（Qt と同じ）
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
        if (this.axis.linear) return;  // |Γ|・SWR の縦軸は下端が決まっていて動かさない（設計R §3）
        // つかんだ目盛がマウスに付いてくる: 下へ引くと、より上の値が見える（REF が上がる）
        if (start) { this.dragRef0 = this.amp.ref; return; }
        this.setAmp({ ref_level: GSUnits.dragRefLevel(this.dragRef0, frac, this.amp.div, GSGraph.DIVISIONS) });
      },
      nearestTrace(x, y) {
        // クリック位置に縦方向で最も近い、見えているトレース（G §3.2）
        let best = null;
        for (const n of [1, 2, 3, 4]) {
          const t = this.$traces.get(String(n));
          if (!t || !this.traceShown(n) || !this.onAxis(t)) continue;
          const i = GSZeroSpan.indexAt(t.axis, x);
          if (i === null || i < 0 || i >= t.values.length || !Number.isFinite(t.values[i])) continue;
          const d = Math.abs(t.values[i] - y);
          if (best === null || d < best[1]) best = [n, d];
        }
        return best ? best[0] : null;
      },
      async markerAdd(x, y) {
        if (this.markerReason) return;
        const trace = this.nearestTrace(x, y);
        if (trace === null) { this.note(null, X.get("ERRORS", "no_trace_data")); return; }
        // 向きは置いたときの山／谷の選択（G §3.8: N dB 帯域幅・追従の向き。Qt と同じ）
        const r = await this.cmd("marker_add", { kind: "M", trace, freq_hz: Math.round(x), polarity: this.polarity });
        if (r && r.id) this.selectedMarker = r.id;
      },
      onGraphClick(x, y) { this.markerAdd(x, y); },
      onGraphContext(x, y, cx, cy) {
        const why = this.freqOpsReason;
        const mwhy = this.markerReason;
        const hz = Math.round(x);
        const level = Math.round(y * 10) / 10;
        // 位置の文字: 換算後の周波数（設計O）、ゼロスパンは時間（設計N §1）
        const at = this.sw ? X.fmt("paren", { text: this.xText(x) }) : "";
        const items = [
          { label: W("ctx_center") + (why ? "" : at), why, run: () => this.sweep({ center_hz: hz }, "center") },
          { label: W("ctx_marker") + at, why: mwhy, run: () => this.markerAdd(x, y) },
          { label: X.get("DISPLAY_TEXTS", "ctx_bookmark"), why, run: () => this.addBookmarkAt(hz) },
          { label: W("ctx_ref", { level: level.toFixed(1), unit: this.axisUnit }), why: this.axis.ref_reason, run: () => this.setAmp({ ref_level: level }) },
          { label: W("ctx_x1"), why: mwhy, run: () => this.cmd("set_xy", { x1_hz: hz }) },
          { label: W("ctx_x2"), why: mwhy, run: () => this.cmd("set_xy", { x2_hz: hz }) },
        ];
        const sel = this.selectedMarker;
        if (sel) {
          // 選んでいるマーカーへの操作（Qt の右クリックと同じ: G §3.4・設計Q §6.8）。ゼロスパンの位置は Center にできない
          items.push(
            { label: W("ctx_m_center", { m: sel }), why: this.zsView.freq_only, run: () => this.cmd("marker_to", { id: sel, target: "center" }) },
            { label: W("ctx_m_ref", { m: sel }), run: () => this.cmd("marker_to", { id: sel, target: "ref" }) },
            { label: W("ctx_m_listen", { m: sel, start: X.get("LISTEN_LABELS", "start") }), why: this.listenStartReason, run: () => this.listenStart() },
          );
        }
        this.showCtx({ x: cx, y: cy, above: cy, items });
      },
      // 右クリックのメニュー・行の「操作」の一覧を開く。画面の下に入らなければ上へ（above の位置から上に）、右に入らなければ
      // 左へずらして、画面の中に収める（Qt のメニューと同じ: 項目が画面の外に切れない）
      showCtx(ctx) {
        const seq = (this.ctxSeq = (this.ctxSeq || 0) + 1);
        setTimeout(() => {
          this.ctx = { ...ctx, seq };
          this.$nextTick(() => {
            const c = this.ctx;
            const el = document.querySelector(".ctxmenu");
            if (!el || !c || c.seq !== seq) return;  // 間に別の一覧を開いた・閉じた
            const r = el.getBoundingClientRect();
            if (r.bottom > window.innerHeight) c.y = Math.max(0, c.above - r.height);
            if (r.right > window.innerWidth) c.x = Math.max(0, window.innerWidth - r.width);
          });
        }, 0);
      },

      // ------------------------------------------------------------ マーカー表の行（G §3.7: 決まりはサーバーの marker_rows。
      // 探すボタン・行の「操作」・ピークの一覧は app_markers.js）
      rowClick(r) {
        this.selectedRow = r.id;
        if (!r.child && r.kind === "marker") this.selectedMarker = r.id;
        else if (r.parent) this.selectedMarker = r.parent;  // 子の行を選んだら親のマーカーを選ぶ（←→ は親を動かす）
      },
      removeSelectedRow() {
        // 子の行（機能・オフセットのカーソル）や XY の行を選んでいたら、その行だけを消す。マーカーの行なら false
        const r = this.markerRows.find((x) => x.id === this.selectedRow);
        if (!r || !r.remove || (!r.child && r.kind === "marker")) return false;
        this.cmd(r.remove.cmd, r.remove.args);
        this.selectedRow = null;
        return true;
      },
      startEdit(r, i) {
        if (!r.edits || !r.edits[String(i)] || this.readOnly) return;
        this.editCell = { id: r.id, col: i, text: String(r.cells[i] || "").trim() };
        this.$nextTick(() => {
          const el = document.querySelector("input.cell-edit");
          if (el) { el.focus(); el.select(); }
        });
      },
      finishEdit(text) {
        const ec = this.editCell;
        this.editCell = null;
        if (!ec || text == null || !String(text).trim()) return;
        const r = this.markerRows.find((x) => x.id === ec.id);
        const spec = r && r.edits && r.edits[String(ec.col)];
        if (!spec) return;
        let v;
        try {
          v = spec.parse === "freq" ? U.parseFreqHz(text) : spec.parse === "time" ? U.parseTimeUs(text) : U.parseDb(text);
        } catch (e) { this.note(null, W("bad_value")); return; }
        // marker_rows.edit_command と同じ計算（表の周波数は周波数オフセット込みなので差し引く。時間はそのまま: 設計N §1）
        if (spec.parse === "freq") v = Math.round(spec.base + v - (spec.minus_offset ? (this.freqOff || 0) : 0));
        else if (spec.parse === "time") v = Math.round(spec.base + v);
        else v = spec.base + v;
        this.cmd(spec.cmd, { ...spec.args, [spec.arg]: v });
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
        const type = this.offType();  // ゼロスパンでは周波数のオフセットは選べない（設計N §1。Qt と同じくレベルにする）
        try { delta = type === "freq" ? U.parseFreqHz(this.off.delta) : U.parseDb(this.off.delta); } catch (e) { this.note("offset", W("bad_value")); return; }
        const r = await this.cmd("marker_offset_add", { id: m.id, type, sign: this.off.sign, delta }, { field: "offset" });
        if (r) this.off.delta = "";
      },
      // マーカーの機能（G §3.5・§3.6。Qt の tab_markers と同じ: チェックで入れる・切る、dB の欄で N・しきい値を変える）。
      // 入れている機能は選んだマーカーの値を出し、切っている間は欄に残した値（fn）を出す
      // 送って戻りを待つ間は、送った値（fnSent）を出す。続けて回したホイールが、前の値の戻りから数え直さないように
      // （REF レベルの ampLocal と同じ考え。最後の命令の戻りから 1 秒でコアの値に戻す）
      fnView() {
        const m = this.selMarker;
        const bws = (m && m.bandwidth) || [];
        const th = (m && m.threshold) || null;
        const v = {
          on1: bws.length > 0, n1: bws.length > 0 ? bws[0].n_db : this.fn.bw1,
          on2: bws.length > 1, n2: bws.length > 1 ? bws[1].n_db : this.fn.bw2,
          thOn: !!th, thr: th ? th.level : this.fn.thr, side: th ? th.side : this.fn.side,
        };
        const s = this.fnSent;
        if (m && s && s.id === m.id) {
          if (s.bws) [[v.on1, v.n1], [v.on2, v.n2]] = s.bws;
          if (s.th) [v.thOn, v.thr, v.side] = [s.th.on, s.th.level, s.th.side];
        }
        return v;
      },
      async fnSend(m, part, args) {
        const s = this.fnSent && this.fnSent.id === m.id ? this.fnSent : { id: m.id };
        this.fnSent = { ...s, ...part };
        clearTimeout(this.fnTimer);
        this.fnBusy = (this.fnBusy || 0) + 1;
        await this.cmd("marker_function", { id: m.id, ...args }, { field: "function" });
        this.fnBusy -= 1;
        if (!this.fnBusy) this.fnTimer = setTimeout(() => { this.fnSent = null; }, 1000);
      },
      // i: 0 = N dB 帯域幅、1 = 2つ目の N。on・n は変えるものだけ（null は今のまま）
      setBandwidth(i, on, n) {
        const v = this.fnView();
        const rows = [[v.on1, v.n1], [v.on2, v.n2]];
        if (on !== null) rows[i][0] = on;
        if (n !== null) rows[i][1] = n;
        this.fn[i ? "bw2" : "bw1"] = rows[i][1];  // 切っても欄の値は残す（Qt と同じ）
        const m = this.selMarker;
        if (!m) return;
        this.fnSend(m, { bws: rows }, { bandwidth_db: rows.filter((r) => r[0]).map((r) => r[1]) });
      },
      // patch: { on, level, side } のうち変えるもの
      setThreshold(patch) {
        const v = this.fnView();
        const s = { on: v.thOn, level: v.thr, side: v.side, ...patch };
        this.fn.thr = s.level;
        this.fn.side = s.side;
        const m = this.selMarker;
        if (!m) return;
        this.fnSend(m, { th: s }, { threshold: s.on ? { level: s.level, side: s.side } : null });
      },
      commitXY(key, text) {
        if (String(text).trim() === "") { this.cmd("set_xy", { [key]: null }); return; }
        try {
          // X はゼロスパンでは時間（ms で打つ: 設計N §1）
          const xv = () => (this.zsView.time ? U.parseTimeUs(text) : this.deviceHz(U.parseFreqHz(text)));
          const v = key.endsWith("_hz") ? xv() : U.parseDb(text);
          this.cmd("set_xy", { [key]: v }, { field: "xy" });
        } catch (e) { this.note("xy", W("bad_value")); }
      },
      xyText(key) {
        const v = (this.markers.xy || {})[key];
        if (v == null) return "";
        if (key.endsWith("_hz") && this.zsView.time) return U.formatTimeUs(v);
        return key.endsWith("_hz") ? U.freqFieldText(this.shownHz(v)) : `${v.toFixed(2)}`;
      },
      // ------------------------------------------------------------ トレース
      setTrace(n, key, value) {
        const args = { number: n, [key]: value };
        this.cmd("set_trace", args, { field: "trace" + n });
      },
      setTraceAction(t, action) {
        const args = { number: t.number, action };
        // 平均は回数を持つ（設計F §2）。回数が無ければ既定の回数で始める（Qt の tab_traces と同じ）
        if (action === "average" && !t.count) args.count = DEFAULT_AVERAGE_COUNT;
        this.cmd("set_trace", args, { field: "trace" + t.number });
      },
      // 行の2段目の文（Qt と同じ: screen_model.trace_status が作り、ui.trace_status で届く）。1行に収めて切り、
      // 全部はツールチップ（掃引ごとに長さが変わっても、列の幅も行の高さも動かさない: 設計J §4.11）
      traceStatus(t) {
        return ((this.ui && this.ui.trace_status) || {})[String(t.number)] || { text: "", tip: "" };
      },
      traceTip(key, reason) { return W(key) + (reason ? " — " + reason : ""); },
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
      // 重ねた直後は、保存したときに表示していたトレースだけを出す（隠していたものはチェックで出せる。決まりはサーバーの
      // screen_model.file_trace_shown_at_open: Qt の plot_layers と同じ）。前から重ねているものは利用者の切り替えを保ち、
      // 外した参照トレースの覚えは捨てる（同じ id で開き直したら、また保存したときの表示から）
      onStateFiles(p) {
        const shownAtOpen = (p.ui && p.ui.file_shown) || {};
        const ids = new Set();
        let hidden = null;
        for (const f of p.files || []) {
          for (const tr of f.traces || []) {
            ids.add(tr.id);
            if (tr.id in this.fileHidden) continue;
            hidden = hidden || { ...this.fileHidden };
            hidden[tr.id] = shownAtOpen[tr.id] === false;
          }
        }
        for (const id of Object.keys(this.fileHidden)) {
          if (ids.has(id)) continue;
          hidden = hidden || { ...this.fileHidden };
          delete hidden[id];
        }
        if (hidden) this.fileHidden = hidden;
      },

      // 基準（信号源のタブ: 設計E §6）・S11 のオープン/ショート・表示の形式は app_s11.js

      // ------------------------------------------------------------ 列の幅（J §4.11・S-02）
      // トレースの表の列 A（動作・平均の種類）と列 B（回数・N 回の後）は、その列に出うる一番長い中身の幅に1つにそろえる
      // （1行目と2行目の両方の選択肢。行や動作で選択肢が変わっても幅を変えない。2026-10-03 ユーザー指摘: 箱の長さが
      // そろわず、設定を変えるときマウスを大きく動かす）。上段の周波数の欄の幅もここで測る（伸ばさない: S-02）。
      // 測るのは字が入ってから（system の字なので待つのは一度だけ）
      async fitColumns() {
        try { if (document.fonts) await document.fonts.ready; } catch (e) { /* 字を待てなくても測る */ }
        const F = GSFields;
        const types = Object.values(X.dict("AVERAGE_TYPES"));
        // 3行目の表示の形式（設計R §3）も列 A（S11 の「リターンロス（dB）」を含む。Qt の format_width_samples と同じ）
        const fmts = [...Object.values(X.dict("TRACE_FORMATS")), ...Object.values(X.dict("S11_FORMAT_NAMES"))];
        const colA = Math.max(F.selectWidth(Object.values(X.dict("TRACE_ACTIONS"))),
          F.selectWidth([W("default"), ...types.map((t) => W("default_with", { type: t })), ...types]), F.selectWidth(fmts));
        // 回数の欄は打てる欄＋▼: 一番長い回数の文字＋欄の左右の余白（6px×2）と枠（1px×2）＋▼（22px、欄に 1px 重ねる）
        const counts = [F.countText(null), ...F.COUNT_CHOICES.map((n) => F.countText(n)), F.countText(F.COUNT_MAX)];
        const countW = Math.max(...counts.map((t) => F.textWidth(t, F.rootPx("--font-m")))) + 2 * 6 + 2 + 21;
        const colB = Math.max(F.selectWidth([...Object.values(X.dict("AVERAGE_AFTER")), ...Object.values(X.dict("HOLD_AFTER"))]), countW);
        // 上段の周波数の欄: 上段の字で一番広い見本＋欄の左右の余白（6px×2）と枠（1px×2）（Qt の FreqField と同じ）。
        // 字の幅の端数で文字がはみ出さない（scrollWidth が clientWidth を超えない）よう 1px 足す
        const lv = F.rootPx("--font-l-value");
        const freqW = Math.ceil(Math.max(...TOP_FREQ_SAMPLES.map((t) => F.textWidth(t, lv)))) + 2 * 6 + 2 + 1;
        // モードのボタン: 5つとも一番長い名前＋左右の余白と枠（同じ幅にそろえる。最小幅は使わない）
        const lb = F.rootPx("--font-l-button");
        const modeW = Math.ceil(Math.max(...X.list("MODES").map((m) => F.textWidth(m[1], lb)))) + 2 * F.rootPx("--btn-pad-x") + 2;
        // イメージ除去の欄: 出しうる一番長い選択肢（秒は 999.9 で測る。Qt の screen_model.image_reject_width_samples と同じ）
        const reject = Object.values(X.dict("IMAGE_REJECT_CHOICES")).map((name) => W("image_reject_item", { name, s: "999.9" }));
        // 線の列（クリアの真下: 設計F §9）: 一番長い線の名前と、同じ列のクリアのボタン（文字＋左右の余白と枠・最小幅）の広い方
        // （Qt の tab_traces の線の列と同じ）
        const clearW = Math.max(F.rootPx("--btn-min-w"),
          Math.ceil(F.textWidth(W("clear"), F.rootPx("--font-m"))) + 2 * F.rootPx("--btn-pad-x") + 2);
        const line = Math.max(F.selectWidth(Object.values(X.dict("TRACE_LINES"))), clearW);
        F.fitColumns({ a: colA, b: colB, reject, line });
        document.documentElement.style.setProperty("--top-freq-w", freqW + "px");
        document.documentElement.style.setProperty("--mode-w", modeW + "px");
      },

      // ------------------------------------------------------------ 入力の窓（ファイルの窓は app_files.js）
      // fields: [{ key, label, value, width（入力欄の幅のクラス。省くと名前の幅）, placeholder, multiline }]
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
      // 配色と大きさは設定の画面の「保存」で選んだもの（ブラウザごと。既定はユーザー指定の配色・1600×1000: I §3.1・§3.6）
      pngBlob(note) {
        const colors = this.pngColors();
        return GSPng.render({
          st: this.st, amp: { ...this.amp, unit: this.axisUnit }, colors,
          // 描く順と線（設計F §9）は画面と同じ。線の幅は PNG の基準の幅（png.js が lineBase で渡す）
          traces: (c) => this.graphTraces(c), markers: this.markers,
          files: this.shownFiles(colors).map((f) => ({ label: f.label, color: f.colorKey })), note,
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
        if (changed) {
          this.detached = d;
          this.$nextTick(() => { if (this.$graph) this.$graph.setSize(this.$refs.graph.clientWidth, this.$refs.graph.clientHeight); this.resizeLower(); });
        }
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
        // 'g' はスペクトルとウォーターフォール（密度表示）の境目（設計P §2。比はグラフの部分の高さに対する下の割合）
        const area = which === "g" && this.$refs.garea ? this.$refs.garea.getBoundingClientRect() : null;
        const move = (ev) => {
          if (which === "v") this.splitPct = Math.min(90, Math.max(30, ((ev.clientY - box.top) / box.height) * 100));
          else if (which === "g" && area) this.lowerPct = Math.min(80, Math.max(20, ((area.bottom - ev.clientY) / area.height) * 100));
          else this.sidePx = Math.min(700, Math.max(240, box.right - ev.clientX));
        };
        const up = () => {
          window.removeEventListener("mousemove", move);
          window.removeEventListener("mouseup", up);
          try { localStorage.setItem("pygigast.layout", JSON.stringify({ splitPct: this.splitPct, sidePx: this.sidePx, lowerPct: this.lowerPct })); } catch (err) { /* 残せなくても動く */ }
          this.$nextTick(() => this.resizeLower());
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
  merge(base, GSAppMarkers);
  merge(base, GSAppFiles);
  merge(base, GSAppRtl);
  merge(base, GSAppDevices);
  merge(base, window.GSAppCal || {});  // 校正の画面（app_cal.js）
  merge(base, window.GSAppZeroSpan || {});  // ゼロスパンの時間軸（app_zerospan.js: 設計N §1）
  merge(base, window.GSAppS11 || {});  // 信号源のタブの基準・S11・表示の形式（app_s11.js）
  merge(base, window.GSAppStep || {});  // SG のステップ掃引・内部の漏れの印（app_step.js）
  merge(base, window.GSAppNf || {});  // NF 測定の欄とつなぎ方の窓（app_nf.js）
  merge(base, GSAppDisplay);  // ウォーターフォール・密度・再生・バンドプラン（app_display.js: 設計P）
  // キーの割り当て（app_keys.js）と設定の画面・プリセット・表示の設定（app_settings.js）
  merge(base, window.GSAppKeys || {});
  merge(base, window.GSAppSettings || {});

  const app = createApp(base);
  app.component("num-field", GSFields.NumField);
  app.component("count-field", GSFields.CountField);
  app.component("int-field", GSFields.IntField);
  app.component("db-field", GSFields.DbField);
  // 状態を映す入力は、触っている間は書き直さない（fields.js の Steady）
  app.directive("steady", GSFields.Steady);
  // 設定の画面・プリセットの窓・確かめる窓（app_windows.js）
  for (const [name, c] of Object.entries(window.GSWindows || {})) app.component(name, c);
  window.addEventListener("DOMContentLoaded", () => { window.gsApp = app.mount("#app"); });
})();
