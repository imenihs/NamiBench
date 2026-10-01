// Web のメニュー「≡」とキー操作（J §4.3.1・§4.6）。並びと語は texts の MENU_LAYOUT・MENU_ITEMS（Qt のメニューバーと同じ）。
// ここに持つのは Web のキーと、項目を押したときに呼ぶ画面の処理だけ。app.js が読み込んで合わせる。
(function (root) {
  "use strict";

  const X = root.GSTexts;
  const W = (k, kw) => X.w(k, kw);

  // キー操作の Web の初期値（J §4.6。ブラウザが先に取るキーは避けた: F5 の再読み込み・Ctrl+数字のタブ切り替え・
  // Ctrl+Shift+C の開発者ツール）。グラフにいるときだけのキーは GRAPH_ONLY
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
  const PARTS = ["graph", "markers", "tab-trace", "tab-marker", "tab-view", "tab-source"];
  // Web のメニューに無い項目（Qt だけのもの: 終了はブラウザの窓を閉じる）
  const WEB_SKIP = new Set(["quit"]);

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

  root.GSAppMenu = {
    computed: {
      menu() { return this.buildMenu(); },
      keyRows() {
        const rows = [];
        for (const sec of this.menu) for (const it of sec.items) if (it && it.keys && it.keys.length) rows.push([it.keys.map(keyLabel).join("・"), it.label]);
        for (const r of X.list("WEB_KEY_HELP")) rows.push(r);
        return rows;
      },
    },
    methods: {
      handlers() {
        return {
          open: () => this.openDialog(), save: () => this.saveDialog(), export: () => this.exportDialog(),
          png_save: () => this.pngSave(), png_copy: () => this.pngCopy(),
          record_start: () => this.cmd("record_start"), record_stop: () => this.cmd("record_stop"),
          record_finish: () => this.recordFinishDialog(),
          undo: () => this.cmd("undo", {}, { quiet: ["nothing_to_undo"] }),
          redo: () => this.cmd("redo", {}, { quiet: ["nothing_to_redo"] }),
          connect: () => this.openDevices(),
          connect_sim: () => this.cmd("connect", { backend: "simulator", serial: "SIM00001" }),
          disconnect: () => this.disconnectAndList(),
          run_cont: () => this.runCont(), run_single: () => this.runSingle(), stop: () => this.stop(),
          pause: () => this.toggleRun(),
          ref_auto: () => this.refAuto(),
          marker_peak: () => this.markerPeak("highest"), marker_peak_left: () => this.markerPeak("left"),
          marker_peak_right: () => this.markerPeak("right"), marker_peak_lower: () => this.markerPeak("lower"),
          marker_left: () => this.markerStep(-1), marker_right: () => this.markerStep(1),
          marker_here: () => this.markerHere(),
          marker_add: async () => { const r = await this.cmd("marker_add", { kind: "M", trace: 1 }); if (r && r.id) this.selectedMarker = r.id; },
          marker_center: () => this.cmd("marker_to", { id: this.selectedMarker, target: "center" }),
          marker_ref: () => this.cmd("marker_to", { id: this.selectedMarker, target: "ref" }),
          marker_span: () => this.cmd("marker_to", { id: this.selectedMarker, target: "span" }),
          marker_listen: () => this.listenStart(), listen_stop: () => this.listenStop(),
          marker_remove: () => this.cmd("marker_remove", { id: this.selectedMarker }),
          marker_clear: () => this.cmd("marker_clear"),
          marker_copy: () => this.copyMarkerTable(),
          cal_legacy: () => this.legacyDialog(),
          settings: () => this.settingsDialog(),
          help_keys: () => { this.modal = { title: X.get("MENU_ITEMS", "help_keys"), keys: true, fields: [] }; },
          help_about: () => { this.modal = { title: X.get("MENU_ITEMS", "help_about"), note: W("about", { version: this.info.version || "" }), fields: [] }; },
        };
      },
      // 使えない理由（U-08）。メニュー・ボタンで同じ文
      itemReason(code) {
        const R = (k) => X.get("REASON_TEXTS", k);
        const st = this.st || {};
        const rec = st.recording || {};
        const noMarker = this.selectedMarker ? null : R("select_marker");
        const sel = this.selMarker;
        if (code.startsWith("mode_")) return this.modeReason(code.slice(5));
        switch (code) {
          case "png_save": return this.st ? null : R("no_state");
          case "png_copy": return this.clipboardReason;
          case "save": return (st.traces || []).some((t) => t.has_values) ? null : R("no_data_to_save");
          case "record_start": return rec.active || rec.stopped ? X.get("ERRORS", "already_recording") : null;
          case "record_stop": return rec.active ? null : X.get("ERRORS", "not_recording");
          case "record_finish": return rec.active || rec.stopped ? null : X.get("ERRORS", "not_recording");
          case "undo": return st.undo && !st.undo.can_undo ? R("no_undo") : null;
          case "redo": return st.undo && !st.undo.can_redo ? R("no_redo") : null;
          case "run_cont": case "run_single": return this.connected ? null : X.get("ERRORS", "not_connected");
          case "ref_auto": return this.refAutoReason();
          case "marker_peak_left": case "marker_peak_right": case "marker_peak_lower": case "marker_left":
          case "marker_right": case "marker_center": case "marker_ref": case "marker_remove": return noMarker;
          case "marker_span": return noMarker || (sel && sel.ref ? null : R("delta_ref"));
          case "marker_listen": return this.listenStartReason;
          case "listen_stop": return this.listenView.stop_reason || null;
          default: return null;
        }
      },
      buildMenu() {
        const h = this.handlers();
        const items = (codes) => codes.filter((c) => !WEB_SKIP.has(c)).map((code) => {
          if (code === null) return null;
          const notYet = X.get("MENU_NOT_YET", code, null);
          const why = notYet ? X.get("NOT_YET", notYet) : this.itemReason(code);
          return { code, label: X.get("MENU_ITEMS", code), run: notYet ? null : h[code], why, keys: KEYS[code] || [], graphOnly: GRAPH_ONLY.has(code) };
        });
        const out = [];
        for (const [sec, codes] of X.list("MENU_LAYOUT")) {
          let list = items(codes);
          if (sec === "mode") {
            list = X.list("MODES").map(([c, l, n]) => ({
              code: "mode_" + c, label: l + X.fmt("paren", { text: n }), run: () => this.setMode(c), why: this.modeReason(c), keys: KEYS["mode_" + c] || [], graphOnly: false,
            }));
          }
          if (sec === "view") {
            // Web は部分ごとに「別ウィンドウで開く」を置く（J §4.3.2）
            list.push(null, ...PARTS.map((p) => ({
              code: "detach_" + p, label: W("detach_item", { part: W("part_" + p) }), run: () => this.detach(p),
              why: this.part ? X.get("REASON_TEXTS", "part_window") : null, keys: [], graphOnly: false,
            })));
          }
          while (list.length && list[list.length - 1] === null) list.pop();
          out.push({ title: X.get("MENU_TITLES", sec), items: list });
        }
        return out;
      },
      itemTitle(it) {
        let t = it.label;
        if (it.keys.length) t += X.fmt("paren", { text: it.keys.map(keyLabel).join("・") });
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
      keyHint(code) { const k = KEYS[code]; return k ? X.fmt("paren", { text: k.map(keyLabel).join("・") }) : ""; },
      keysText(it) { return it.keys.map(keyLabel).join("・"); },
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
    },
  };
})(window);
