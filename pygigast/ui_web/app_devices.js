// 機器の一覧（設計J §4.7。Qt の device_list.py と同じ並び・同じ語）。app.js が読み込んで合わせる。
// - 一覧を自動で開くのは繋がっていないときだけ（ページを開いた時点・利用者が切断したとき）。抜けたとき
//   （切断・再接続待ち）は開かない。いつでもメニュー「機器 → 機器に接続…」から開ける
// - 行の文（名前・状態・詳細・押せるもの）と空のときの案内はサーバーが足す（view.devices_view: Qt と同じ関数）
// - 挿し抜きで一覧が自動で変わる（イベント devices）。開いている間は list_devices の watch_s で見続けてもらう
// - 繋いでいる最中と、失敗の理由と次の手を一覧の窓に出す
(function (root) {
  "use strict";

  const X = root.GSTexts;
  // 開いている間、コアに一覧を見続けてもらう長さ（秒）と、延ばし直す間隔（ミリ秒）
  const WATCH_S = 10;
  const RENEW_MS = 5000;

  root.GSAppDevices = {
    data() {
      return { devWin: null, devAutoChecked: false };
    },
    methods: {
      D(code) { return X.get("DEVICE_LABELS", code); },
      onStateDevices(p) {
        const state = (p.connection || {}).state;
        if (!this.devAutoChecked) {
          // ページを開いた時点で未接続なら一覧を開く（再接続待ちは開かない）
          this.devAutoChecked = true;
          if (state === "disconnected") this.openDevices();
        }
        // 別の画面（Qt など）から繋いだら、繋がっていないから開いていた一覧を閉じる
        if (this.devWin && !this.devWin.busy && state === "connected" && this.devPrevState !== "connected") this.closeDevices();
        this.devPrevState = state;
      },
      openDevices() {
        if (!this.devWin) this.devWin = { rows: [], empty: false, simulator: null, status: "", warn: false, busy: null, open: {} };
        this.refreshDevices();
        clearInterval(this.devTimer);
        this.devTimer = setInterval(() => this.refreshDevices(), RENEW_MS);
      },
      closeDevices() {
        clearInterval(this.devTimer);
        this.devWin = null;
      },
      async refreshDevices() {
        const r = await this.cmd("list_devices", { watch_s: WATCH_S });
        if (r) this.onDevices(r);
      },
      onDevices(p) {
        if (!this.devWin) return;
        this.devWin = { ...this.devWin, rows: p.rows || [], empty: !!p.empty, simulator: p.simulator || null };
      },
      async connectDevice(d) {
        const w = this.devWin;
        if (!w || w.busy) return;
        const name = (d.view && d.view.name) || X.get("DEVICE_KINDS", d.kind);
        this.devWin = { ...w, busy: d.id || d.kind, status: X.fmt("device_connecting", { name }), warn: false };
        try {
          await GSApi.call("connect", { backend: d.kind, serial: d.serial || null, port: d.port });
          this.closeDevices();
        } catch (e) {
          if (!this.devWin) return;
          this.devWin = { ...this.devWin, busy: null, status: X.fmt("device_connect_failed", { name, reason: e.next || e.code }), warn: true };
          this.refreshDevices();
        }
      },
      deviceAction(d) {
        if (d.state === "dll_not_found") this.chooseDll();
        else this.connectDevice(d);
      },
      toggleDetails(id) { this.devWin = { ...this.devWin, open: { ...this.devWin.open, [id]: !this.devWin.open[id] } }; },
      renameDevice(d) {
        this.openModal(this.D("rename_title"), [{ key: "name", label: this.D("rename_label"), width: "w-name", placeholder: this.D("rename_placeholder"), value: d.custom_name || d.view.name }],
          (v) => this.cmd("rename_device", { kind: d.kind, serial: d.serial, name: String(v.name).trim() || null }));
      },
      chooseDll() {
        // ブラウザからは PC のフォルダを指定できない（サーバーが path_not_allowed で断る）ので、変える場所を知らせる
        this.openModal(this.D("dll_folder_title"), [], null, X.w("dll_folder_web"));
      },
      disconnectAndList() { this.cmd("disconnect", {}, { ok: () => this.openDevices() }); },
    },
  };
})(window);
