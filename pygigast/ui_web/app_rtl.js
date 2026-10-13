// 受信（RTL-SDR: 設計Q §6.5・§6.6）・マーカーの位置を聞く（Q §6.8）・アプリ設定の rtl_tcp と DLL（Q §4.3・O §1.3）。
// Qt の rtl_panel.py・listen.py・settings_dialog.py と同じ欄・同じ語。中身（選択肢・理由・文）はサーバーが
// state の ui に入れて送る（screen_model.py: Qt と同じ関数）。app.js が読み込んで合わせる。
(function (root) {
  "use strict";

  const X = root.GSTexts;

  root.GSAppRtl = {
    data() {
      return {
        rtlMarker: "",
        rtlTrueFreq: "",
        rtlTrueDbm: "-30",
        rtlResult: "",
        listenMode: "am",
        volume: 50,
        audioBlocked: false,
      };
    },
    computed: {
      rtlView() { return this.ui.rtl || { reason: "", gains: [] }; },
      rtlNoMarker() { return (this.markers.markers || []).length ? null : X.get("RTL_LABELS", "no_marker"); },
      rtlGainValue() {
        const v = this.rtlView;
        return v.agc || v.gain_db == null ? "agc" : String(v.gain_db);
      },
      listenView() { return this.ui.listen || { reason: X.get("LISTEN_LABELS", "not_iq"), stop_reason: X.get("REASON_TEXTS", "not_listening") }; },
      listenStartReason() {
        return this.listenView.reason || (this.selectedMarker ? null : X.get("REASON_TEXTS", "select_marker"));
      },
      listenText() {
        const v = this.listenView;
        if (!v.active) return "";
        let t = v.now_text;
        if (!this.$audio || !this.$audio.available) t += "\n" + X.fmt("audio_none");
        else if (this.audioBlocked) t += "\n" + X.fmt("audio_blocked");
        return t;
      },
      rtltcpView() { return this.ui.rtltcp || {}; },
      // 外部 IQ 送信の口（設計S §1。中身は screen_exttx.py: Qt の pluto_panel・settings_dialog・statusbar と同じ）
      extView() { return this.ui.ext_tx || { tx: { shown: false }, settings: { enabled: false, port: 1235, text: "", error_text: "" } }; },
      // PlutoSDR の受信・送信の欄（設計Q §7。中身は screen_pluto.pluto_view: Qt の pluto_panel.py と同じ）
      plutoView() {
        return this.ui.pluto || { reason: "", gain_choices: [], gain_lo: null, gain_hi: null, auto_text: "",
                                  tx_atten_choices: [], tx_atten_min: null, tx_atten_max: null };
      },
      // 利得の欄の値（自動のときは欄が「自動」を出し、↑↓・ホイールはこの値から送る）
      plutoGainValue() {
        const v = this.plutoView;
        return v.agc || v.gain_db == null ? Number(v.gain_base) || 0 : Number(v.gain_db);
      },
      // TG 高調波の平均除去の選択（設計Q §7.7。中身は screen_pluto.tg_harmonic_view: Qt の PlutoTxPanel と同じ）
      tgHarmView() { return this.plutoView.tg_harmonic || { items: [], value: 1, reason: X.get("PLUTO_LABELS", "not_pluto") }; },
    },
    methods: {
      onStateRtl(p) {
        const ui = p.ui || {};
        const lst = ui.listen || {};
        if (lst.active && lst.mode) this.listenMode = lst.mode;
        if (!lst.active && this.$audio) this.$audio.close();
        // 校正のマーカーの欄は、選んでいるマーカー（無ければ最初のマーカー）を既定にする
        const ids = ((p.markers || {}).markers || []).map((m) => m.id);
        if (!ids.includes(this.rtlMarker)) this.rtlMarker = ids.includes(this.selectedMarker) ? this.selectedMarker : (ids[0] || "");
        if (!this.rtlTrueFreq && this.rtlMarker) {
          const m = ((p.markers || {}).markers || []).find((x) => x.id === this.rtlMarker);
          if (m && m.freq_hz != null) this.rtlTrueFreq = GSUnits.freqFieldText(m.freq_hz);
        }
      },
      // ---- 受信
      setGain(value) {
        if (value === "agc") this.cmd("rtlsdr_set", { agc: true }, { field: "rtl" });
        else this.cmd("rtlsdr_set", { gain_db: Number(value), agc: false }, { field: "rtl" });
      },
      setPpm(text) {
        const v = Number(String(text).trim());
        if (!Number.isInteger(v)) { this.note("rtl", X.get("ERRORS", "not_integer")); return; }
        this.cmd("rtlsdr_set", { ppm: v }, { field: "rtl" });
      },
      async calPpm() {
        let hz;
        try { hz = GSUnits.parseFreqHz(this.rtlTrueFreq); } catch (e) { this.note("rtlcal", X.w("bad_freq")); return; }
        const r = await this.cmd("rtlsdr_calibrate_ppm", { marker_id: this.rtlMarker, true_freq_hz: hz }, { field: "rtlcal" });
        if (r && r.texts) { this.rtlResult = r.texts.done; this.note(null, r.texts.done); }
      },
      async calLevel() {
        let v;
        try { v = GSUnits.parseDb(String(this.rtlTrueDbm).replace("dBm", "")); } catch (e) { this.note("rtlcal", X.w("bad_value")); return; }
        const r = await this.cmd("rtlsdr_calibrate_level", { marker_id: this.rtlMarker, true_dbm: v }, { field: "rtlcal" });
        if (r && r.texts) { this.rtlResult = r.texts.done; this.note(null, r.texts.done); }
      },
      // ---- PlutoSDR
      // 同じ値は送り直さない（利得を送るとトレースの蓄積を捨てる。Qt の pluto_panel と同じ）
      setPlutoGain(value) {
        const v = this.plutoView;
        if (value === "agc") {
          if (!v.agc) this.cmd("plutosdr_set", { agc: true }, { field: "pluto" });
          return;
        }
        if (!v.agc && v.gain_setting === Number(value)) return;
        this.cmd("plutosdr_set", { gain_db: Number(value), agc: false }, { field: "pluto" });
      },
      // 送信の減衰の欄（db-field: 打った・選んだ・↑↓・ホイールで送った値）。外部 IQ の送信の間は外部の送信の減衰
      // （SG・TG と別の値: 設計S §1 の O1。Qt の PlutoTxPanel と同じ）
      sendTxAtten(v) {
        if (this.plutoView.tx_atten_ext) this.cmd("ext_tx_atten", { atten_db: v }, { field: "plutotx" });
        else this.cmd("plutosdr_set", { tx_atten_db: v }, { field: "plutotx" });
      },
      // 同じ回数は送り直さない（Qt の pluto_panel と同じ）
      setTgHarmonic(code) {
        const n = Number(code);
        if (n === this.tgHarmView.value) return;
        this.cmd("plutosdr_set", { tg_harmonic_avg: n }, { field: "plutotx" });
      },
      // ---- 聞く
      listenStart() {
        const why = this.listenStartReason;
        if (why) { this.note(null, why); return; }
        this.ensureAudio();
        this.cmd("listen_start", { marker_id: this.selectedMarker, mode: this.listenMode }, { field: "listen" });
      },
      listenModeChanged() {
        // 聞いている間に方式を変えたら、同じマーカーで聞き直す
        const v = this.listenView;
        if (v.active && v.marker_id) this.cmd("listen_start", { marker_id: v.marker_id, mode: this.listenMode }, { field: "listen" });
      },
      listenStop() { this.cmd("listen_stop"); },
      ensureAudio() {
        // 押した操作の中で作ると、ブラウザが音を許す
        if (!this.$audio) this.$audio = new GSAudio.Player();
        this.$audio.setVolume(this.volume / 100);
        this.$audio.resume();
      },
      setVolume(v) {
        this.volume = Number(v);
        if (this.$audio) this.$audio.setVolume(this.volume / 100);
      },
      onAudio(p) {
        if (!this.$audio) this.$audio = new GSAudio.Player();
        this.$audio.play(p.values, p.rate || 48000);
        const b = this.$audio.blocked;
        if (b !== this.audioBlocked) this.audioBlocked = b;
      },
      // ---- アプリ設定の rtl_tcp のページ（O §1.3。変えたらすぐ効く。窓の形は app_windows.js）
      setRtltcp(args) { this.cmd("rtltcp_set", args, { field: "rtltcp" }); },
      // ---- アプリ設定の「外部のアプリから送信」（設計S §1 §7。Qt の settings_dialog と同じ欄）
      setExtTx(args) { this.cmd("ext_tx_set", args, { field: "ext_tx" }); },
      setExtTxPort(text) {
        const v = Number(String(text).trim());
        if (!Number.isInteger(v)) { this.note("ext_tx", X.get("ERRORS", "not_integer")); return; }
        this.setExtTx({ port: v });
      },
      setRtltcpPort(text) {
        const v = Number(String(text).trim());
        if (!Number.isInteger(v)) { this.note("rtltcp", X.get("ERRORS", "not_integer")); return; }
        this.setRtltcp({ port: v });
      },
    },
  };
})(window);
