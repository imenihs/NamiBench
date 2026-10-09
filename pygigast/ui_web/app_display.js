// 表示（設計P）: ウォーターフォール・密度表示・記録の再生・バンドプランとブックマーク。Qt の ui_qt/display_control.py・
// display_groups.py・playback_bar.py と同じ部品・同じ語・同じ動き（J §5）。画面の中身（欄の選択肢・帯の文・使えない理由）は
// サーバーが Qt と同じ display_view で作って ui.display で送る。描くのは waterfall.js。app.js が読み込んで合わせる。
(function (root) {
  "use strict";

  const X = root.GSTexts;
  const U = root.GSUnits;
  const D = (k, kw) => X.fmtText(X.get("DISPLAY_TEXTS", k, k), kw);
  // 時刻の軸の上のホイール1段でずらす量（いま出している時間の長さに対する割合。Qt と同じ）
  const SCROLL_FRACTION = 0.25;
  // 再生のつまみを動かしている間にカーソルを送る間隔（ミリ秒。Qt の playback_bar.SEEK_MS と同じ）
  const SEEK_MS = 120;
  const LOWER = ["waterfall", "density"];

  root.GSAppDisplay = {
    data() {
      return {
        wfFollow: true, wfWindow: null, wfEpoch: null, wfSeq: 0, wfUnit: "dBm", wfInfo: {},
        pbActive: false, lowerPct: 40, bmName: "", bmFreq: "", wfShownLayout: "spectrum", wfBusy: false, wfAgain: false,
      };
    },
    computed: {
      dv() { return this.ui.display || {}; },
      layoutCode() { return (this.st && this.st.view && this.st.view.layout) || "spectrum"; },
      // いま出す組み合わせ（ゼロスパンの間はスペクトルのみ。設定の layout は変えない: display_view.shown_layout）
      shownLayout() { return this.dv.shown_layout || this.layoutCode; },
      layoutReasons() { return this.dtab.layout_reasons || {}; },
      // 横軸・上段の欄の範囲（ライブは掃引の範囲・再生は再生の表示の範囲: display_view.freq_view。P §2・§5）
      fv() {
        const f = this.dv.freq_view;
        if (f && f.start_hz != null) return f;
        const s = this.sw;
        return s ? { start_hz: s.start_hz, stop_hz: s.stop_hz, center_hz: s.center_hz, span_hz: s.span_hz, step_hz: s.step_hz, points: s.points, playback: false } : null;
      },
      pbView() { return this.dv.playback || { visible: false }; },
      dtab() {
        return this.dv.tab || { layout: "spectrum", trace: 1, traces: [], auto: true, lo: -110, hi: -20, range_reason: null, unit: "dBm",
          colormap: "viridis", colormaps: [], rows: 2000, rows_choices: [], half: 20, half_choices: [], bandplan_shown: true, bookmarks_shown: true };
      },
      bpView() { return this.dv.bandplan || { bands: [], bookmarks: [], list: [], count: "", file: "" }; },
      bpCount() { return [this.bpView.count, this.bpView.file].filter((t) => t).join("・"); },
      // グラフの下に出す部分（表示の組み合わせ: P §2）
      lowerKey() { return LOWER.includes(this.shownLayout) ? this.shownLayout : null; },
      lowerShown() { return !!this.lowerKey && this.show(this.lowerKey); },
      followLabel() { return this.pbActive ? D("whole") : D("latest"); },
      followReason() {
        if (this.pbActive) return this.wfWindow ? null : X.get("REASON_TEXTS", "wf_whole");
        return this.wfFollow ? X.get("REASON_TEXTS", "wf_following") : null;
      },
      densitySeconds() { return this.dv.density_seconds || ""; },
      // 密度を描かない理由（行にするトレースの単位が縦軸と違う: display_view.density_reason）。題の行の秒の場所に出す
      densityReason() { return this.dv.density_reason || null; },
      runReason() { return this.ui.run_reason || null; },
    },
    methods: {
      D,
      lowerVisible(key) {
        if (this.part) return this.part === key;
        return this.shownLayout === key && !this.detached[key];
      },
      // ---------------------------------------------------------- 部品を作る（グラフを作った後に app.js が呼ぶ）
      makeLower() {
        const luts = (this.info && this.info.colormaps) || {};
        const common = {
          colors: this.palette,
          onRangeSelected: (a, b) => this.onRangeSelected(a, b),
          onFreqWheel: (x, steps) => this.onFreqWheel(x, steps),
          showX: !!this.part || !!this.detached.graph,
        };
        if (this.$refs.wf && !this.$wf) {
          this.$wf = Vue.markRaw(new GSWaterfall(this.$refs.wf, {
            ...common,
            boundary: (cond) => this.boundaryText(cond),
            onPick: (t, pos) => this.cmd("waterfall_pick", { t, pos }),
            onTimeRange: (lo, hi) => this.wfTimeRange(lo, hi),
            onTimeScroll: (steps) => this.wfScroll(steps),
            onHover: (h) => this.wfHover(h),
            onResized: () => this.wfResized(),
          }));
        }
        if (this.$refs.dens && !this.$dens) {
          this.$dens = Vue.markRaw(new GSDensity(this.$refs.dens, {
            ...common,
            onLevelWheel: (steps, ctrl) => this.onLevelWheel(steps, ctrl),
            onLevelDrag: (start, frac) => this.onLevelDrag(start, frac),
          }));
        }
        const cm = (this.st && this.st.palette && this.st.palette.colormap) || "viridis";
        if (this.$wf) this.$wf.setLut(luts[cm] || null);
        if (this.$dens) this.$dens.setLut(luts[cm] || null);
        this.applyDisplay();
      },
      // ---------------------------------------------------------- 状態を映す
      applyDisplay() {
        const st = this.st;
        if (!st) return;
        const f = this.fv;
        const luts = (this.info && this.info.colormaps) || {};
        const cm = (st.palette && st.palette.colormap) || "viridis";
        for (const g of [this.$wf, this.$dens]) {
          if (!g) continue;
          g.setColors(this.palette);
          if (g.lutName !== cm) { g.lutName = cm; g.setLut(luts[cm] || null); }
          // ゼロスパン（横軸が時間）の間は下の部分を出さないので、周波数の範囲のときだけ合わせる（Qt と同じ）
          if (f && f.step_hz !== 0) g.setX(f.start_hz, f.stop_hz, false, this.freqOff, this.ui.freq_offset_text || "");
        }
        if (this.$dens) {
          this.$dens.setY(this.amp.ref, this.amp.div, this.axisUnit, this.amp.decimals);
          this.$dens.setReason(this.densityReason);
        }
        this.applyLowerAxes();
        const wf = st.waterfall || {};
        if (this.$wf && wf.range && wf.range.lo != null) this.$wf.setRange(wf.range.lo, wf.range.hi);
        const pb = st.playback || {};
        const active = !!pb.active;
        const layoutChanged = this.shownLayout !== this.wfShownLayout;
        this.wfShownLayout = this.shownLayout;
        if (active !== this.pbActive) {
          this.pbActive = active;
          this.wfFollow = !active;
          this.wfWindow = null;
          this.fetchRows();
        } else if (wf.epoch !== this.wfEpoch || (layoutChanged && this.shownLayout === "waterfall")) {
          this.fetchRows();
        }
        if (this.$wf) this.$wf.setCursor(active ? pb.t : this.pastTime());
        if (layoutChanged) this.$nextTick(() => this.resizeLower());
      },
      pastTime() {
        for (const f of this.files) if (f.kind === "past") return f.t;
        return null;
      },
      resizeLower() {
        this.applyLowerAxes();
        for (const [g, el] of [[this.$wf, this.$refs.wf], [this.$dens, this.$refs.dens]]) if (g && el) g.setSize(el.clientWidth, el.clientHeight);
        if (this.$wf) this.$wf.rebuild();
      },
      // ウォーターフォール・密度表示の周波数軸は、スペクトルのすぐ下に付いている間は出さない（同じ軸を2回出さない:
      // P §2 の連動）。別ウィンドウ・グラフを別ウィンドウにしている間は出す（Qt と同じ）
      applyLowerAxes() {
        const show = !!this.part || !!this.detached.graph;
        for (const g of [this.$wf, this.$dens]) if (g) g.setXAxisShown(show);
      },
      boundaryText(cond) {
        // 条件の境目の文（Qt と同じ display_view.boundary_label。同じ語の表から作る）
        const mode = X.get("MODE_LABELS", cond.mode, cond.mode || "");
        const rbw = cond.rbw_hz ? U.formatFreq(cond.rbw_hz) : "";
        const off = this.freqOff;
        if (cond.start_hz == null) return "";
        if (cond.start_hz === cond.stop_hz) return D("boundary_zero", { start: U.formatFreq(cond.start_hz + off), rbw, mode });
        return D("boundary", { start: U.formatFreq(cond.start_hz + off), stop: U.formatFreq(cond.stop_hz + off), rbw, mode });
      },
      // ---------------------------------------------------------- イベント
      onWaterfall(p) {
        if (p.range && this.$wf) this.$wf.setRange(p.range.lo, p.range.hi);
        if (p.reset || p.epoch !== this.wfEpoch) { this.fetchRows(); return; }
        const rows = p.rows || [];
        if (!rows.length) return;
        if (p.unit) this.wfUnit = p.unit;
        if (this.wfBusy) { this.wfAgain = true; return; }
        if ((p.seq || 0) - rows.length !== this.wfSeq) { this.fetchRows(); return; }
        this.wfSeq = p.seq || 0;
        if (this.wfFollow && !this.pbActive && this.$wf) this.$wf.append(rows);
      },
      onDensity(p) { if (this.$dens) this.$dens.setDensity(p); },
      // ---------------------------------------------------------- 行を取り直す（P §9.2）
      async fetchRows() {
        if (!this.$wf || (this.shownLayout !== "waterfall" && !this.pbActive && this.part !== "waterfall")) { this.wfEpoch = null; return; }
        if (this.wfBusy) { this.wfAgain = true; return; }
        this.wfBusy = true;
        this.wfAgain = false;
        const args = {};
        if (this.wfFollow && !this.pbActive) args.max_rows = Math.min(4000, this.$wf.visibleRows());
        else {
          args.max_rows = Math.min(4000, this.$wf.pixelRows());
          if (this.wfWindow) { args.t_lo = this.wfWindow[0]; args.t_hi = this.wfWindow[1]; }
        }
        try {
          const r = await GSApi.waterfallRows(args);
          this.wfInfo = { t_first: r.t_first, t_last: r.t_last };
          this.wfEpoch = r.epoch;
          this.wfSeq = r.seq || 0;
          if (r.unit) this.wfUnit = r.unit;
          if (r.range && r.range.lo != null) this.$wf.setRange(r.range.lo, r.range.hi);
          this.$wf.setRows(r.rows || [], this.wfFollow && !this.pbActive);
        } catch (e) {
          if (e.code !== "not_playing") this.note(null, e.text || X.get("ERRORS", e.code, e.code));
        }
        this.wfBusy = false;
        if (this.wfAgain) this.fetchRows();
      },
      wfResized() {
        clearTimeout(this.$wfResizeTimer);
        this.$wfResizeTimer = setTimeout(() => this.fetchRows(), 150);
      },
      // ---------------------------------------------------------- 操作
      wfTimeRange(lo, hi) {
        if (!(hi > lo)) return;
        this.wfFollow = false;
        this.wfWindow = [lo, hi];
        this.fetchRows();
      },
      wfShownSpan() {
        const rows = this.$wf ? this.$wf.shown : [];
        if (!rows.length) return null;
        const newest = rows[0];
        return [rows[rows.length - 1].t, newest.t_end != null ? newest.t_end : newest.t];
      },
      wfScroll(steps) {
        const span = this.wfWindow || this.wfShownSpan();
        if (!span) return;
        let [lo, hi] = span;
        const d = (hi - lo) * SCROLL_FRACTION * steps;
        lo += d;
        hi += d;
        const info = this.wfInfo || {};
        if (!this.pbActive && steps > 0 && info.t_last != null && hi >= info.t_last) { this.wfFollowClick(); return; }
        if (info.t_first != null && lo < info.t_first) { hi = info.t_first + (hi - lo); lo = info.t_first; }
        this.wfFollow = false;
        this.wfWindow = [lo, hi];
        this.fetchRows();
      },
      wfFollowClick() {
        this.wfWindow = null;
        this.wfFollow = !this.pbActive;
        this.fetchRows();
      },
      // 行の上のマウスの読み値（Qt のツールチップと同じ display_view.waterfall_reading）。取りに行くのは1つずつ
      async wfHover(h) {
        if (!h) { this.hover = null; this.hoverLines = null; return; }
        this.hover = { cx: h.cx, cy: h.cy };
        if (this.$wfHoverBusy) { this.$wfHoverWant = h; return; }
        this.$wfHoverBusy = true;
        const r = h.row;
        const zero = !!r.zero_span;
        const body = { t: r.t, sweeps: r.sweeps || 1, level: h.level, unit: this.wfUnit };
        if (zero) body.point = h.j; else if (h.j != null) body.freq_hz = r.start_hz + r.step_hz * h.j;
        try {
          const res = await call0("waterfall_reading", body);
          if (this.hover) this.hoverLines = res.lines || null;
        } catch (e) { this.hoverLines = null; }
        this.$wfHoverBusy = false;
        const want = this.$wfHoverWant;
        this.$wfHoverWant = null;
        if (want) this.wfHover(want);
      },
      toggleLayout(code) { this.setView({ layout: this.layoutCode === code ? "spectrum" : code }); },
      // 色の範囲の欄の文字（Qt の DbField と同じ「−76.0 dBm」の形）
      wfLevelText(v) { return v == null ? "" : `${U.minusSign(Number(v).toFixed(1))} ${this.dtab.unit || ""}`.trim(); },
      commitWfLevel(key, text) {
        let v;
        try { v = U.parseDb(String(text).replace(this.dtab.unit || "", "")); } catch (e) { this.note("wf", W0("bad_value")); return; }
        this.setView({ [key]: v }, "wf");
      },
      // ---------------------------------------------------------- 再生（P §9.4）
      pbToggle() { this.cmd(this.pbView.playing ? "playback_pause" : "playback_play"); },
      pbSlide(v) {
        clearTimeout(this.$pbSeekTimer);
        this.$pbSeekTimer = setTimeout(() => this.cmd("playback_seek", { index: Number(v) }), SEEK_MS);
      },
      // 再生している記録の切り出し（H §3.1）・CSV への書き出し（H §5）。範囲はウォーターフォールで選んだ時間（選んで
      // いなければ全体）といまの周波数の表示の範囲（Qt と同じ）。名前を付けて作り、ダウンロードする
      recordRangeDialog(command, ext, titleKey) {
        const d = new Date();
        const p = (n) => String(n).padStart(2, "0");
        const name = `gigast_${d.getFullYear()}${p(d.getMonth() + 1)}${p(d.getDate())}_${p(d.getHours())}${p(d.getMinutes())}${p(d.getSeconds())}${ext}`;
        this.openModal(D(titleKey), [{ key: "name", label: W0("file_name"), value: name }], (v) => {
          const args = { name: v.name };
          if (this.wfWindow) { args.t_lo = this.wfWindow[0]; args.t_hi = this.wfWindow[1]; }
          this.makeFile(command, args);
        });
      },
      // ---------------------------------------------------------- バンドプラン・ブックマーク（P §6・§9.5）
      centerOn(hz) { this.sweep({ center_hz: Math.round(hz) }, "center"); },
      async bandplanLoad() {
        if (this.readOnlyBlocks("bandplan_load")) return;
        const files = await root.GSPickFiles(false);
        if (!files.length) return;
        try {
          const [id] = await this.uploadAll(files);
          await this.cmd("bandplan_load", { upload_id: id }, { field: "bandplan" });
        } catch (e) { this.note("bandplan", e.text || X.get("ERRORS", e.code, e.code)); }
      },
      async bandplanSave() {
        const r = await this.cmd("bandplan_save", { name: "bandplan.json" }, { field: "bandplan" });
        if (r && r.file_id) root.GSDownload(r.file_id, r.name);
      },
      bookmarkAdd() {
        const name = (this.bmName || "").trim();
        if (!name) { this.note("bookmark", X.get("ERRORS", "bandplan_bad_name")); return; }
        let hz;
        try { hz = this.deviceHz(U.parseFreqHz(this.bmFreq || this.freqText.center)); } catch (e) { this.note("bookmark", W0("bad_freq")); return; }
        this.cmd("bookmark_add", { name, hz }, { field: "bookmark", ok: () => { this.bmName = ""; } });
      },
      addBookmarkAt(hz) {
        this.openModal(D("bookmark_add_title"), [{ key: "name", label: D("bookmark_name"), value: "" }], (v) => {
          if (v.name && v.name.trim()) this.cmd("bookmark_add", { name: v.name.trim(), hz: Math.round(hz) });
        });
      },
    },
  };

  const W0 = (k, kw) => X.w(k, kw);
  // 文を作るだけの口（命令の列を通さない: server.py の waterfall_reading）
  async function call0(name, body) {
    const res = await fetch("/api/v1/" + name, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    if (!res.ok) throw new Error(String(res.status));
    return res.json();
  }
})(window);
