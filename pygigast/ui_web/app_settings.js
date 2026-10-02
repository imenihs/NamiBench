// 設定の画面・プリセットの窓・表示の設定（単位・外部補正・周波数オフセット・描き方）・配色・並び（設計O・
// J §4.4・§4.6・§4.3.2）。Qt の settings_dialog.py・settings_colors.py・settings_keys.py・presets_dialog.py・
// tab_display.py・look.py と同じ欄・同じ語・同じ決まり。窓の形（テンプレート）は app_windows.js。
// - 変えたらすぐ効く（「適用」を置かない）。戻したいときは各項目の「初期値に戻す」
// - 配色・表示の設定・覚えた機器・プリセットはコアが持つ（Qt と共有）。キーの割り当て・PNG の大きさと配色・
//   手動の COM 番号・並び・窓で知らせるか（「次から出さない」）はブラウザごと（localStorage。設計O §1.1）
// - 単位の欄は実際に使っている単位を出し、単位が変わったら欄の下に一度、比の表示に変わったら窓で知らせる（J §4.10）
// - 周波数オフセットは表示だけ: 画面の周波数 = 機器の周波数 + オフセット。機器へ送る値は機器の周波数（設計O）
// app.js が読み込んで合わせる。文言はここに書かない（texts の表とサーバーが足した文だけ）。
(function (root) {
  "use strict";

  const X = root.GSTexts;
  const S = (k, kw) => X.fmtText(X.get("SETTINGS_TEXTS", k), kw);
  const P = (k, kw) => X.fmtText(X.get("PRESET_TEXTS", k), kw);
  const V = (k, kw) => X.fmtText(X.get("VIEW_TEXTS", k), kw);
  const PNG_STORE = "pygigast.png";
  const MANUAL_STORE = "pygigast.manual_port";
  const LAYOUT_STORE = "pygigast.layout";
  // 窓で知らせるか（{窓の名前: false} が「次から出さない」。ブラウザごと: Qt の画面の覚え書きと同じ扱い）
  const NOTIFY_STORE = "pygigast.notify";
  // 欄の下の一度きりの知らせを出しておく長さ（Qt の tab_display.UNIT_NOTE_MS と同じ）
  const FIELD_NOTE_MS = 6000;
  // 単位の欄で「基準との比」を表す値（Qt の screen_model.RATIO_UNIT と同じ。選んで送るものではない）
  const RATIO_UNIT = "ratio";
  // PNG の大きさの選択肢と初期値（Qt の settings_dialog.PNG_SIZES と同じ）
  const PNG_SIZES = [[1600, 1000], [1920, 1080], [1280, 800], [2560, 1600]];
  // PNG の配色: ユーザー指定（screen。画面の配色そのもの）／ライト／ダーク。既定はユーザー指定（Qt の
  // core/report_layout.PNG_PALETTE_CHOICES・PNG_PALETTE_DEFAULT と同じ: I §3.6）
  const PNG_PALETTE_CHOICES = ["screen", "light", "dark"];
  const PNG_DEFAULTS = { size: "1600x1000", palette: "screen" };
  // 並びの初期値（分割比 % と右のパネルの幅。Qt の docks.py の既定に合わせた値）
  const LAYOUT_DEFAULTS = { splitPct: 70, sidePx: 340 };
  // 配色の名前 → 画面の CSS の変数
  const CSS_VARS = {
    graph_bg: "--graph-bg", panel_bg: "--panel-bg", text: "--text", text_dim: "--text-dim", accent: "--accent",
    warn: "--warn", grid: "--grid", axis: "--axis", running: "--running", marker: "--marker",
  };
  const HEX = /^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$/;

  function readJson(key, fallback) {
    try {
      const v = JSON.parse(localStorage.getItem(key) || "null");
      return v && typeof v === "object" ? v : fallback;
    } catch (e) { return fallback; }
  }
  function writeStore(key, value) {
    try {
      if (value === null) localStorage.removeItem(key);
      else localStorage.setItem(key, typeof value === "string" ? value : JSON.stringify(value));
    } catch (e) { /* 残せなくてもこの画面では効く */ }
  }
  function luminance(hex) {
    const h = hex.replace("#", "");
    const ch = [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16) / 255).map((c) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4));
    return 0.2126 * ch[0] + 0.7152 * ch[1] + 0.0722 * ch[2];
  }
  function expandHex(t) {
    const v = t.trim().toLowerCase();
    return v.length === 4 ? "#" + v.slice(1).split("").map((c) => c + c).join("") : v;
  }

  root.GSAppSettings = {
    data() {
      let manual = "";
      try { manual = localStorage.getItem(MANUAL_STORE) || ""; } catch (e) { manual = ""; }
      return {
        settingsWin: null, // { page }
        presetsWin: null, // { rows, broken, sel, ready }
        confirmBox: null, // { title, text, resolve }
        keySel: null,
        keyCapture: false,
        keyMsg: null, // { text, warn }
        manualPort: manual,
        pngPrefs: { ...PNG_DEFAULTS, ...readJson(PNG_STORE, {}) },
        notifyPrefs: readJson(NOTIFY_STORE, {}),
      };
    },
    computed: {
      viewOpts() { return (this.st && this.st.view) || {}; },
      // 単位の欄（J §4.10 の 2）。中身はサーバーの screen_model.unit_view（Qt と同じ）: 実際に使っている単位・
      // いま選べない単位と理由。状態が届く前は設定の単位だけ
      unitView() {
        const u = this.ui.level;
        if (u) return u;
        const code = this.viewOpts.level_unit || "dBm";
        return { ratio: false, current: code, choices: Object.entries(this.levelUnits).map(([c, t]) => ({ code: c, text: t, enabled: true, tip: "" })), tip: V("level_unit_tip") };
      },
      // 比の表示に変わったら窓で知らせるか（初期値: 知らせる）
      notifyRatio() { return this.notifyPrefs.ratio_display !== false; },
      // トレースの蓄積の決まり（設計F §8。コアの設定で Qt と共有）
      traceOpts() { return (this.st && this.st.trace_options) || {}; },
      // 周波数オフセット（Hz）。画面に出す周波数 = 機器の周波数 + これ
      freqOff() { return Number(this.viewOpts.freq_offset_hz || 0); },
      // 使わないとき（0）は Qt の欄と同じく「0.000 MHz」を出す
      freqOffText() { return this.viewOpts.freq_offset_hz ? GSUnits.formatFreq(this.viewOpts.freq_offset_hz) : GSUnits.freqFieldText(0); },
      drawFill() { return this.viewOpts.draw_style === "fill"; },
      levelUnits() { return this.viewOpts.level_units || { dBm: "dBm" }; },
      extText() { const v = Number(this.viewOpts.ext_db || 0); return GSUnits.minusSign(String(v)); },
      // 配色の行（読みやすさの知らせは行ごと: U-25。中身はサーバーの screen_model.palette_view で Qt と同じ）
      paletteView() { return this.ui.palette || { rows: [], changed_n: 0 }; },
      knownDevices() { return (this.st && this.st.known_devices) || []; },
      pngSizes() { return PNG_SIZES.map(([w, h]) => ({ value: `${w}x${h}`, text: `${w}×${h}` })); },
      // 選んでいる PNG の配色。読めない覚え書きは既定（ユーザー指定）
      pngPalette() { return PNG_PALETTE_CHOICES.includes(this.pngPrefs.palette) ? this.pngPrefs.palette : PNG_DEFAULTS.palette; },
      settingsPages() { return Object.entries(X.dict("SETTINGS_PAGES")); },
      // キー操作のページの行（操作・いまのキー・初期値・効く所）。Qt の settings_keys.KeysPage.refresh と同じ中身:
      // グラフにいるときだけ効くことは「効く所」の列に出す（U-12・U-24）。Shift で10点動くことは命令の名前（メニューと同じ）が言う。
      // 名前は app_menu.js の keyRows（キー操作の一覧の窓の行）と分ける（同じ名前だと後から合わせた方で上書きされ、
      // 一覧の窓が空になっていた）
      keyTableRows() {
        const d = X.dict("WEB_KEY_DEFAULTS");
        const graphOnly = X.list("GRAPH_ONLY_KEYS");
        return GSKeys.commands().map((code) => {
          const changed = Object.prototype.hasOwnProperty.call(this.keyOverrides, code) && (d[code] || "") !== this.keyOverrides[code];
          const key = GSKeys.keyLabel(this.keymap[code]);
          const where = S(graphOnly.includes(code) ? "keys_where_graph" : "keys_where_any");
          return { code, name: GSKeys.commandName(code), key, def: GSKeys.keyLabel(d[code] || ""), where, changed };
        });
      },
      // 「ブラウザから使う」のページ（Qt と同じ欄。変えるのは pyGigaSt の PC の画面: U-08）。いまこのページを出している
      // Web の口の様子: 使えている（このブラウザが開いている）・同じネットワークからも使えるか・ポート番号。
      // 様子の文の URL はこのブラウザが開いている URL。同じネットワークからも使えるのに、このブラウザがこの PC の中の
      // 宛先（127.0.0.1 など）で開いているときは、ほかの機器が使う URL をここでは知らないので文を出さない
      // （接続のしかたの窓が出す。知らない URL を書かない）
      webView() {
        const WL = (k, kw) => X.fmtText(X.get("WEB_LABELS", k), kw);
        const lan = !!(this.info && this.info.lan);
        const url = `${location.protocol}//${location.host}/`;
        const loopback = ["127.0.0.1", "localhost", "[::1]"].includes(location.hostname);
        const text = lan ? (loopback ? "" : WL("state_lan", { url })) : WL("state_local", { url });
        return { lan, port: location.port || "", text, warn: lan };
      },
    },
    methods: {
      S, P, V,
      // ------------------------------------------------------------ 確かめる窓（何が失われるかを言ってから聞く: U-18）
      // ok は押したら何が起きるかの語（省くと「続ける」。「OK」にしない）
      askConfirm(title, text, ok) {
        return new Promise((resolve) => { this.confirmBox = { title, text, ok, resolve }; });
      },
      // 知らせるだけの窓（［閉じる］と「次から出さない」）。閉じたら { checked } で返す
      showInfo(title, text, check, key) {
        return new Promise((resolve) => { this.confirmBox = { title, text, check, key, checked: false, info: true, resolve }; });
      },
      answerConfirm(ok) {
        const b = this.confirmBox;
        this.confirmBox = null;
        if (b) b.resolve(b.info ? { checked: !!b.checked } : ok);
      },
      // ---- 単位が変わった・比の表示に変わった知らせ（J §4.10 の 3・4。文と出し方はサーバーの texts.notice_view）
      onViewNotice(v) {
        if (!v) return;
        if (v.field) this.flashNote(v.field, v.text);
        const m = v.modal;
        // 窓は元の画面だけが出す（別ウィンドウにした部分も同じ知らせを受けるので、窓の数だけ重ねない）
        if (!m || this.part || this.notifyPrefs[m.key] === false) return;
        const b = this.confirmBox;
        if (b && b.info && b.key === m.key) { b.text = m.text; return; }
        if (b) return; // ほかの確かめる窓に答えている間は重ねない（状態バーには出ている）
        this.showInfo(m.title, m.text, m.check, m.key).then((r) => { if (r.checked) this.setNotify(m.key, false); });
      },
      flashNote(field, text) {
        this.note(field, text);
        clearTimeout((this.$noteTimers || (this.$noteTimers = {}))[field]);
        this.$noteTimers[field] = setTimeout(() => { if (this.notes[field] && this.notes[field].text === text) this.clearNote(field); }, FIELD_NOTE_MS);
      },
      setNotify(key, on) {
        const p = { ...this.notifyPrefs };
        if (on) delete p[key]; else p[key] = false;
        this.notifyPrefs = p;
        writeStore(NOTIFY_STORE, Object.keys(p).length ? p : null);
      },
      setUnit(code) { if (code !== RATIO_UNIT) this.setView({ level_unit: code }, "unit"); },
      setTraceOptions(args) { return this.cmd("set_trace_options", args, { field: "sg" }); },

      // ------------------------------------------------------------ 設定の画面（O §1.3）
      openSettings(page) {
        this.menuOpen = false;
        this.settingsWin = { page: page || (this.settingsWin && this.settingsWin.page) || "connection" };
        this.keyMsg = null;
        this.keyCapture = false;
        if (!this.keySel) this.keySel = GSKeys.commands()[0];
      },
      closeSettings() { this.settingsWin = null; this.keyCapture = false; },
      setPage(p) { this.settingsWin = { ...this.settingsWin, page: p }; this.keyCapture = false; },

      // ---- 接続（覚えた機器・手動の COM 番号）
      knownName(r) { return r.name ? r.name + X.fmt("paren", { text: r.serial }) : r.serial; },
      async forgetDevice(r) {
        if (!(await this.askConfirm(S("forget_title"), S("forget_confirm", { name: r.name || r.serial }), X.w("do_forget")))) return;
        this.cmd("forget_device", { serial: r.serial }, { field: "settings_conn" });
      },
      setManualPort(v) { this.manualPort = String(v).trim(); writeStore(MANUAL_STORE, this.manualPort); },
      connectManual() {
        const port = String(this.manualPort).trim().toUpperCase();
        if (!/^COM\d+$/.test(port)) { this.note("settings_conn", S("manual_bad")); return; }
        this.setManualPort(port);
        this.clearNote("settings_conn");
        this.cmd("connect", { backend: "gigast", port }, { field: "settings_conn" });
      },

      // ---- 表示（配色: J §4.4）。変えた色は状態で全部の画面へ届き、その場で描き直す
      setColor(name, color) {
        const c = expandHex(String(color));
        if (!HEX.test(c)) { this.note("settings_palette", X.get("ERRORS", "bad_color")); return; }
        this.clearNote("settings_palette");
        this.cmd("palette_set", { colors: { [name]: c } }, { field: "settings_palette" });
      },
      typedColor(row, ev) {
        const t = String(ev.target.value).trim();
        if (!t || expandHex(t) === row.color) { ev.target.value = row.color; return; }
        if (!HEX.test(t)) { this.note("settings_palette", X.get("ERRORS", "bad_color")); ev.target.value = row.color; return; }
        this.setColor(row.name, t);
      },
      // ライト・ダークのボタン: 変えた色があるときだけ、何個失われるかを言ってから聞く（J §4.4）
      async resetPalette(base) {
        const n = this.paletteView.changed_n || 0;
        if (n > 0) {
          const ok = await this.askConfirm(S("palette_reset_title"), S("palette_reset_confirm", { n, base: X.get("PALETTE_BASES", base) }), X.w("do_restore"));
          if (!ok) return;
        }
        this.cmd("palette_reset", { base });
      },
      // 画面の配色（コアの palette）を CSS の変数とグラフへ写す。別ウィンドウも同じ状態を受けて同じ色になる
      applyLook() {
        const c = this.palette;
        if (!c || !c.panel_bg) return;
        const key = JSON.stringify(c);
        if (key === this.$lookKey) return;
        this.$lookKey = key;
        const s = document.documentElement.style;
        for (const [name, v] of Object.entries(CSS_VARS)) if (c[name]) s.setProperty(v, c[name]);
        s.colorScheme = luminance(c.panel_bg) > 0.4 ? "light" : "dark";
        if (this.$graph) this.$graph.setColors(c);
        this.redraw();
      },

      // ---- キー操作（J §4.6）。決まりは app_keys.js（Qt の keymap.py と同じ）
      startCapture() {
        if (!this.keySel) return;
        // 押したボタンにフォーカスを残すと、Space・Enter でまた押されるので外す
        if (document.activeElement && document.activeElement.blur) document.activeElement.blur();
        this.keyCapture = true;
        this.keyMsg = { text: S("keys_waiting"), warn: false };
      },
      // キーを押して割り当てる間は、押したキーを操作に使わずここで受ける（Esc でやめる）
      captureKey(e) {
        if (GSKeys.isModifierOnly(e)) return;
        e.preventDefault();
        e.stopPropagation();
        this.keyCapture = false;
        if (e.key === "Escape" && !e.ctrlKey && !e.altKey && !e.shiftKey) { this.keyMsg = null; return; }
        this.assignKey(this.keySel, GSKeys.keyOf(e));
      },
      async assignKey(code, key) {
        const why = GSKeys.reservedReason(key);
        if (why) { this.keyMsg = { text: S("keys_reserved", { key: GSKeys.keyLabel(key), why }), warn: true }; return false; }
        const other = GSKeys.conflict(this.keymap, code, key);
        if (other) {
          const q = S("keys_conflict", { key: GSKeys.keyLabel(key), other: GSKeys.commandName(other), command: GSKeys.commandName(code) });
          if (!(await this.askConfirm(S("keys_conflict_title"), q, X.w("do_reassign")))) { this.keyMsg = null; return false; }
        }
        this.setKeyOverrides(GSKeys.assign(this.keyOverrides, code, key, other));
        this.keyMsg = { text: S("keys_assigned", { command: GSKeys.commandName(code), key: GSKeys.keyLabel(key) }), warn: false };
        return true;
      },
      clearKey() { if (this.keySel) this.setKeyOverrides(GSKeys.assign(this.keyOverrides, this.keySel, "")); },
      async resetKeyRow() {
        const code = this.keySel;
        if (!code) return;
        let ov = { ...this.keyOverrides };
        delete ov[code];
        const def = X.dict("WEB_KEY_DEFAULTS")[code] || "";
        const other = def ? GSKeys.conflict(GSKeys.effective(ov), code, def) : null;
        if (other) {
          // 初期値のキーをほかの命令に付け替えていたら、聞いてからそちらを外す（黙って2つに付けない）
          const q = S("keys_conflict", { key: GSKeys.keyLabel(def), other: GSKeys.commandName(other), command: GSKeys.commandName(code) });
          if (!(await this.askConfirm(S("keys_conflict_title"), q, X.w("do_reassign")))) return;
          ov = GSKeys.assign(ov, other, "");
        }
        this.setKeyOverrides(ov);
      },
      async resetAllKeys() {
        const n = GSKeys.changedCount(this.keyOverrides);
        if (!n) return;
        if (!(await this.askConfirm(S("keys_reset_all_title"), S("keys_reset_all_confirm", { n }), X.w("do_reset_all")))) return;
        this.setKeyOverrides({});
      },

      // ---- 保存（PNG の大きさと配色。ブラウザごと）
      setPng(k, v) {
        this.pngPrefs = { ...this.pngPrefs, [k]: v };
        // 選んだ項目だけを覚える（選んでいない項目は初期値に従う。Qt の画面の覚え書きと同じ）
        writeStore(PNG_STORE, { ...readJson(PNG_STORE, {}), [k]: v });
      },
      resetPng(k) { this.setPng(k, PNG_DEFAULTS[k]); },
      pngSizeWH() {
        const m = /^(\d+)x(\d+)$/.exec(this.pngPrefs.size || "");
        return m ? [Number(m[1]), Number(m[2])] : PNG_SIZES[0];
      },
      // PNG の配色（I §3.6）: ユーザー指定（既定。画面の配色そのもの・変えた色を含む）／ライト／ダークの初期値
      pngColors() {
        const p = this.pngPalette;
        if (p === "screen") return this.palette;
        return (this.colors && this.colors[p]) || this.palette;
      },

      // ------------------------------------------------------------ 表示の設定（設計O）
      setView(args, field) { return this.cmd("set_view", args, { field: field || "view" }); },
      commitExt(text) {
        const t = String(text).trim();
        if (t === "") { this.setView({ ext_db: null }); return; }
        let v;
        try { v = GSUnits.parseDb(t); } catch (e) { this.note("view", V("bad_db")); return; }
        this.setView({ ext_db: v });
      },
      // 周波数オフセット（負も可。空と 0 は使わない）。単位を省いたら MHz（周波数の欄と同じ）
      commitOffset(text) {
        const t = String(text).trim().replace(/−/g, "-");
        if (t === "") { this.setView({ freq_offset_hz: 0 }, "offset"); return; }
        let hz;
        try { hz = GSUnits.parseFreqHz(t); } catch (e) { this.note("offset", X.w("bad_freq")); return; }
        this.clearNote("offset");
        this.setView({ freq_offset_hz: hz }, "offset");
      },
      // 欄の周波数 ⇔ 機器の周波数（Center・Start・Stop・XY の X・右クリックの周波数。Span と SG は換算しない）
      shownHz(hz) { return hz == null ? hz : hz + this.freqOff; },
      deviceHz(hz) { return hz - this.freqOff; },

      // ------------------------------------------------------------ プリセット（O §1.2）
      openPresets() {
        this.menuOpen = false;
        this.presetsWin = { rows: [], broken: 0, sel: null, ready: false };
        this.clearNote("presets");
        this.reloadPresets();
      },
      closePresets() { this.presetsWin = null; },
      async reloadPresets() {
        const r = await this.cmd("preset_list", {}, { field: "presets" });
        if (!r || !this.presetsWin) return;
        const rows = r.rows || [];
        const sel = rows.some((x) => x.id === this.presetsWin.sel) ? this.presetsWin.sel : (rows[0] || {}).id || null;
        this.presetsWin = { ...this.presetsWin, rows, broken: r.broken || 0, sel, ready: true };
      },
      presetSel() { const w = this.presetsWin; return w ? w.rows.find((x) => x.id === w.sel) || null : null; },
      async presetLoad() {
        const p = this.presetSel();
        if (!p) return;
        const r = await this.cmd("preset_load", { id: p.id }, { field: "presets" });
        if (r) { this.note(null, P("loaded", { name: r.name })); this.clearNote("presets"); }
      },
      presetSaveNew(after) {
        this.menuOpen = false;
        this.openModal(P("title"), [{ key: "name", label: P("name_prompt"), value: "" }], async (v) => {
          const name = String(v.name).trim();
          const r = await this.cmd("preset_save", { name }, { field: this.presetsWin ? "presets" : null });
          if (!r) return;
          this.note(null, P("saved", { name: r.name || name }));
          if (this.presetsWin) { this.presetsWin.sel = r.id || this.presetsWin.sel; this.reloadPresets(); }
          if (after) after();
        });
      },
      presetRename() {
        const p = this.presetSel();
        if (!p) return;
        this.openModal(P("title"), [{ key: "name", label: P("name_prompt"), value: p.name }], async (v) => {
          const name = String(v.name).trim();
          if (!name || name === p.name) return;
          if (await this.cmd("preset_rename", { id: p.id, name }, { field: "presets" })) this.reloadPresets();
        });
      },
      async presetDelete() {
        const p = this.presetSel();
        if (!p || !(await this.askConfirm(P("title"), P("delete_confirm", { name: p.name }), X.w("do_delete")))) return;
        if (await this.cmd("preset_delete", { id: p.id }, { field: "presets" })) this.reloadPresets();
      },
      async presetOverwrite() {
        const p = this.presetSel();
        if (!p || !(await this.askConfirm(P("title"), P("overwrite_confirm", { name: p.name }), X.w("do_overwrite")))) return;
        const r = await this.cmd("preset_save", { name: p.name, overwrite_id: p.id }, { field: "presets" });
        if (r) { this.note(null, P("saved", { name: p.name })); this.reloadPresets(); }
      },

      // ------------------------------------------------------------ 並び（J §4.3.2）
      // 切り離した窓を全部閉じて元の位置へ戻し、分割比と右のパネルの幅を初期に戻す（窓を見失ったとき）
      layoutReset() {
        this.menuOpen = false;
        for (const w of Object.values(this.detachedWins || {})) if (w && !w.closed) w.close();
        this.pollDetached();
        this.splitPct = LAYOUT_DEFAULTS.splitPct;
        this.sidePx = LAYOUT_DEFAULTS.sidePx;
        writeStore(LAYOUT_STORE, null);
        this.note(null, X.get("LAYOUT_TEXTS", "layout_reset_done"));
      },
    },
  };
})(window);
