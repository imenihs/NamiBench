// ファイル（保存・開く・書き出し・記録のまとめ・旧校正ファイル）と、Web の口の認証・見るだけ・接続のしかた（設計A §4.3.2）。
// Web の口はサーバーの PC のパスを受け取らない: 作るファイルは名前だけ渡してサーバーのアプリのデータのフォルダに
// 作らせ、終わったら file_id でダウンロードする。開くファイルはブラウザからアップロードして upload_id で渡す。
// 文言はここに書かない（texts の表とサーバーの error.text だけ）。app.js が読み込んで合わせる。
(function (root) {
  "use strict";

  const X = root.GSTexts;
  const W = (k, kw) => X.w(k, kw);
  const WL = (k, kw) => X.fmtText(X.get("WEB_LABELS", k), kw);
  // 見るだけの接続でも送れる命令（サーバーの guard.py の READ_COMMANDS と同じ）
  const READ_COMMANDS = new Set(["get_state", "get_trace", "peak_list", "reference_list", "png_layout", "mouse_reading"]);

  function nowName() {
    const d = new Date();
    const p = (n) => String(n).padStart(2, "0");
    return `gigast_${d.getFullYear()}${p(d.getMonth() + 1)}${p(d.getDate())}_${p(d.getHours())}${p(d.getMinutes())}${p(d.getSeconds())}`;
  }

  // ファイルを選ばせる（選ばなければ空の並び）
  function pickFiles(multiple) {
    return new Promise((resolve) => {
      const input = document.createElement("input");
      input.type = "file";
      input.multiple = !!multiple;
      input.addEventListener("change", () => resolve(Array.from(input.files || [])));
      input.click();
    });
  }

  // ファイルそのものを本文にして送る（multipart にしない: A §4.3.2 の 3 の表）
  async function upload(file) {
    let res;
    try {
      res = await fetch("/api/v1/uploads?name=" + encodeURIComponent(file.name), {
        method: "PUT", headers: { "Content-Type": "application/octet-stream" }, body: file,
      });
    } catch (e) {
      throw new GSApi.CommandError({ error: { code: "server_unreachable", field: null, detail: { message: String(e) } } }, 0);
    }
    let body = null;
    try { body = await res.json(); } catch (e) { body = null; }
    if (!res.ok) throw new GSApi.CommandError(body, res.status);
    return body;
  }

  function download(fileId, name) {
    const a = document.createElement("a");
    a.href = "/api/v1/files/" + encodeURIComponent(fileId);
    if (name) a.download = name;
    document.body.appendChild(a);
    a.click();
    a.remove();
  }

  // QR か URL で開いたときの鍵（#k=…）。読んだらすぐアドレスから消す（画面を見せても漏れない）
  function takeKeyFromHash() {
    const m = /(?:^#|&)k=([^&]+)/.exec(location.hash || "");
    if (!m) return null;
    history.replaceState(null, "", location.pathname + location.search);
    return decodeURIComponent(m[1]);
  }

  root.GSAppFiles = {
    data() {
      return { pendingFiles: {}, connectInfo: null, connectLeft: 0, copiedKind: "" };
    },
    computed: {
      access() { return (this.info && this.info.access) || { kind: "operate", local: true }; },
      readOnly() { return this.access.kind === "view"; },
      readOnlyReason() { return this.readOnly ? WL("read_only") : null; },
      canCopyText() { return !!(window.isSecureContext && navigator.clipboard && navigator.clipboard.writeText); },
    },
    methods: {
      // 起動のときに app.js が呼ぶ。すでに入れている画面でも、新しい鍵（見るだけ → 操作など）を受ける
      async authFromHash() {
        const key = takeKeyFromHash();
        if (!key) return;
        try { await GSApi.call("auth", { key }); } catch (e) { this.note(null, e.text || e.code); }
      },
      // 見るだけの接続では、読むだけの命令のほかは送らずに理由を出す（A §4.3.2 の 4）
      readOnlyBlocks(name) {
        if (!this.readOnly || READ_COMMANDS.has(name)) return false;
        this.note(null, this.readOnlyReason);
        return true;
      },
      // ---- 作るファイル（終わったらダウンロード）
      async makeFile(command, args) {
        const r = await this.cmd(command, args);
        if (r && r.file_id) this.pendingFiles = { ...this.pendingFiles, [r.task_id]: { id: r.file_id, name: r.name } };
        return r;
      },
      onNoticeFiles(p) {
        const d = p.detail || {};
        const mine = this.pendingFiles[d.task_id];
        if (!mine) return;
        const rest = { ...this.pendingFiles };
        delete rest[d.task_id];
        this.pendingFiles = rest;
        if (p.code === "task_done" && d.file_id) download(d.file_id, mine.name);
      },
      saveDialog() {
        this.openModal(W("save_title"), [
          { key: "name", label: W("file_name"), value: nowName() + ".h5" },
          { key: "note", label: W("memo"), value: "" },
        ], (v) => this.makeFile("save", { name: v.name, note: v.note }), W("download_note"));
      },
      // 範囲の欄は上段の Start・Stop と同じく換算後の周波数（周波数オフセット: 設計O）で出し、機器の周波数へ戻して送る
      exportDialog() {
        const U = root.GSUnits;
        const s = this.sw || {};
        this.openModal(W("export_title"), [
          { key: "name", label: W("file_name"), value: nowName() + ".csv" },
          { key: "start", label: W("export_start"), value: s.start_hz != null ? U.freqFieldText(this.shownHz(s.start_hz)) : "" },
          { key: "stop", label: W("export_stop"), value: s.stop_hz != null ? U.freqFieldText(this.shownHz(s.stop_hz)) : "" },
        ], (v) => {
          const args = { name: v.name };
          try {
            if (v.start.trim()) args.start_hz = this.deviceHz(U.parseFreqHz(v.start));
            if (v.stop.trim()) args.stop_hz = this.deviceHz(U.parseFreqHz(v.stop));
          } catch (e) { this.note(null, W("bad_freq")); return; }
          this.makeFile("export_csv", args);
        }, W("export_note"));
      },
      recordFinishDialog() {
        this.openModal(W("record_finish_title"), [
          { key: "name", label: W("file_name"), value: nowName() + ".h5" },
        ], (v) => this.makeFile("record_finish", { name: v.name }), W("download_note"));
      },
      // ---- 開くファイル（アップロードしてから命令）
      async uploadAll(files) {
        const ids = [];
        for (const f of files) {
          this.note(null, W("uploading", { name: f.name }));
          ids.push((await upload(f)).upload_id);
        }
        return ids;
      },
      async openDialog() {
        if (this.readOnlyBlocks("open")) return;
        const files = await pickFiles(true);
        if (!files.length) return;
        try {
          const ids = await this.uploadAll(files);
          await this.cmd("open", { uploads: ids });
        } catch (e) { this.note(null, e.text || X.get("ERRORS", e.code, e.code)); }
      },
      async legacyDialog() {
        if (this.readOnlyBlocks("import_legacy_calibration")) return;
        const serial = (this.conn.identity || {}).serial;
        if (!serial) { this.note(null, W("legacy_need_device")); return; }
        this.openModal(W("legacy_title"), [], async () => {
          const files = await pickFiles(false);
          if (!files.length) return;
          try {
            const [id] = await this.uploadAll(files);
            await this.cmd("import_legacy_calibration", { upload_id: id, serial }, { ok: () => this.note(null, W("legacy_done")) });
          } catch (e) { this.note(null, e.text || X.get("ERRORS", e.code, e.code)); }
        }, W("legacy_note", { serial }));
      },
      // ---- 接続のしかた（操作できる接続のブラウザから: A §4.3.2 の 2）
      connectReason() {
        if (this.readOnly) return this.readOnlyReason;
        if (!this.info.lan) return X.get("WEB_LABELS", "lan_off");
        return null;
      },
      async showConnect() {
        const why = this.connectReason();
        if (why) { this.note(null, why); return; }
        try {
          const r = await GSApi.call("access_info", {});
          this.connectInfo = r;
          this.copiedKind = "";
          this.connectLeft = r.valid_s || 0;
          clearInterval(this.connectTimer);
          this.connectTimer = setInterval(() => {
            this.connectLeft = Math.max(0, this.connectLeft - 1);
            if (!this.connectLeft || !this.connectInfo) clearInterval(this.connectTimer);
          }, 1000);
        } catch (e) { this.note(null, e.text || e.code); }
      },
      closeConnect() { this.connectInfo = null; clearInterval(this.connectTimer); },
      async copyUrl(kind) {
        try {
          await navigator.clipboard.writeText(this.connectInfo.urls[kind]);
          this.copiedKind = kind;
        } catch (e) { this.note(null, W("copy_failed_why", { why: String(e) })); }
      },
      WL,
    },
  };
})(window);
