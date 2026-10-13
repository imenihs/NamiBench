// ファイル（保存・開く・書き出し・記録のまとめ・旧校正ファイルを選んで送る）と、Web の口の認証・見るだけ・接続のしかた（設計A §4.3.2）。
// Web の口はサーバーの PC のパスを受け取らない: 作るファイルは名前だけ渡してサーバーのアプリのデータのフォルダに
// 作らせ、終わったら file_id でダウンロードする。開くファイルはブラウザからアップロードして upload_id で渡す。
// 文言はここに書かない（texts の表とサーバーの error.text だけ）。app.js が読み込んで合わせる。
(function (root) {
  "use strict";

  const X = root.GSTexts;
  const W = (k, kw) => X.w(k, kw);
  const WL = (k, kw) => X.fmtText(X.get("WEB_LABELS", k), kw);
  // 見るだけの接続でも送れる命令（サーバーの guard.py の READ_COMMANDS と同じ）
  const READ_COMMANDS = new Set(["get_state", "get_trace", "peak_list", "reference_list", "png_layout", "mouse_reading",
    "waterfall_rows", "waterfall_reading", "density_get"]);

  function nowName() {
    const d = new Date();
    const p = (n) => String(n).padStart(2, "0");
    return `gigast_${d.getFullYear()}${p(d.getMonth() + 1)}${p(d.getDate())}_${p(d.getHours())}${p(d.getMinutes())}${p(d.getSeconds())}`;
  }

  // ファイルを選ばせる（選ばなければ空の並び）。accept は選べるファイルの種類（".json" など。省くと全部）
  function pickFiles(multiple, accept) {
    return new Promise((resolve) => {
      const input = document.createElement("input");
      input.type = "file";
      input.multiple = !!multiple;
      if (accept) input.accept = accept;
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

  // 名前を付けて作らせたファイルの処理（task_id）と、その終わりの知らせを突き合わせる。
  // 終わりの知らせ（WebSocket）は、命令の戻り（HTTP。task_id と file_id）より先に届くことがある（別の通り道で、
  // 短い処理はすぐ終わる）。まだ戻りを受けていない処理の終わりの知らせは少しだけ覚えておき、戻りを受けたときに照らす。
  // 覚えないと、その知らせは誰の処理か分からずに捨てられ、ダウンロードが始まらない
  const TASK_END = new Set(["task_done", "task_failed", "task_cancelled"]);
  const MAX_EARLY_ENDS = 32;
  function makeFileTasks() {
    const pending = new Map(); // task_id → { name }（戻りを受け、終わりを待っている）
    const early = new Map(); // task_id → 終わりの知らせ（戻りより先に届いた）
    // 終わった: 作れていればダウンロードするもの { id, name }、作れなかった・やめたなら null
    const result = (p, mine) => {
      const d = p.detail || {};
      return p.code === "task_done" && d.file_id ? { id: d.file_id, name: mine.name } : null;
    };
    return {
      // 命令の戻りを受けた。もう終わっていれば、その結果を返す
      started(taskId, file) {
        const p = early.get(taskId);
        if (p) {
          early.delete(taskId);
          return result(p, file);
        }
        pending.set(taskId, file);
        return null;
      },
      // 知らせが届いた。自分の処理の終わりなら、その結果を返す
      ended(p) {
        if (!TASK_END.has(p.code)) return null;
        const id = (p.detail || {}).task_id;
        const mine = pending.get(id);
        if (!mine) {
          early.set(id, p);
          while (early.size > MAX_EARLY_ENDS) early.delete(early.keys().next().value);
          return null;
        }
        pending.delete(id);
        return result(p, mine);
      },
    };
  }

  // QR か URL で開いたときの鍵（#k=…）。読んだらすぐアドレスから消す（画面を見せても漏れない）
  function takeKeyFromHash() {
    const m = /(?:^#|&)k=([^&]+)/.exec(location.hash || "");
    if (!m) return null;
    history.replaceState(null, "", location.pathname + location.search);
    return decodeURIComponent(m[1]);
  }

  // 表示のバンドプランの読み込み・書き出し（app_display.js）も同じ選び方・ダウンロードを使う
  root.GSPickFiles = pickFiles;
  root.GSDownload = download;
  root.GSFileTasks = makeFileTasks;

  root.GSAppFiles = {
    data() {
      return { connectInfo: null, connectLeft: 0, copiedKind: "" };
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
      // ---- ブラウザの中だけで扱うファイル（サーバーにファイルを作らない: 配色の書き出しと取り込み J §4.4.1）
      chooseFiles(multiple, accept) { return pickFiles(multiple, accept); },
      downloadText(name, text, type) {
        const a = document.createElement("a");
        a.href = URL.createObjectURL(new Blob([text], { type: type || "application/json" }));
        a.download = name;
        document.body.appendChild(a);
        a.click();
        a.remove();
        setTimeout(() => URL.revokeObjectURL(a.href), 5000);
      },
      // ---- 作るファイル（終わったらダウンロード）
      // 作らせているファイルの処理（Vue の反応の外に置く。画面には出さない）
      fileTasks() {
        if (!this.$fileTasks) this.$fileTasks = makeFileTasks();
        return this.$fileTasks;
      },
      async makeFile(command, args) {
        const r = await this.cmd(command, args);
        if (r && r.file_id) {
          const f = this.fileTasks().started(r.task_id, { name: r.name });
          if (f) download(f.id, f.name);
        }
        return r;
      },
      onNoticeFiles(p) {
        const f = this.fileTasks().ended(p);
        if (f) download(f.id, f.name);
      },
      saveDialog() {
        this.openModal(W("save_title"), [
          { key: "name", label: W("file_name"), value: nowName() + ".h5" },
          { key: "note", label: W("memo"), value: "" },
        ], (v) => this.makeFile("save", { name: v.name, note: v.note }));
      },
      // 範囲の欄は上段の Start・Stop と同じく換算後の周波数（周波数オフセット: 設計O）で出し、機器の周波数へ戻して送る。
      // 空なら全部（空の見本で示す）。欄の幅は値の種類（ファイル名は名前・周波数は値: J §4.11）
      // ゼロスパンは表示している時間の範囲を書くので、周波数の範囲の欄は理由付きで使えない（設計N §1・U-08。Qt と同じ）
      exportDialog() {
        const U = root.GSUnits;
        const s = this.sw || {};
        const zero = this.zsView.freq_only;
        this.openModal(W("export_title"), [
          { key: "name", label: W("file_name"), value: nowName() + ".csv" },
          { key: "start", label: W("export_start"), width: "w-value", placeholder: W("export_all_placeholder"), disabled: zero,
            value: s.start_hz != null && !zero ? U.freqFieldText(this.shownHz(s.start_hz)) : "" },
          { key: "stop", label: W("export_stop"), width: "w-value", placeholder: W("export_all_placeholder"), disabled: zero,
            value: s.stop_hz != null && !zero ? U.freqFieldText(this.shownHz(s.stop_hz)) : "" },
        ], (v) => {
          const args = { name: v.name };
          if (zero) { this.makeFile("export_csv", args); return; }
          try {
            if (v.start.trim()) args.start_hz = this.deviceHz(U.parseFreqHz(v.start));
            if (v.stop.trim()) args.stop_hz = this.deviceHz(U.parseFreqHz(v.stop));
          } catch (e) { this.note(null, W("bad_freq")); return; }
          this.makeFile("export_csv", args);
        });
      },
      recordFinishDialog() {
        this.openModal(W("record_finish_title"), [
          { key: "name", label: W("file_name"), value: nowName() + ".h5" },
        ], (v) => this.makeFile("record_finish", { name: v.name }));
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
      // ファイルを1つ選んで送り、upload_id を返す（旧校正ファイル: 校正の画面 app_cal.js）。選ばなければ null
      async pickUpload() {
        if (this.readOnlyBlocks("cal_legacy_load")) return null;
        const files = await pickFiles(false);
        if (!files.length) return null;
        try {
          const [id] = await this.uploadAll(files);
          return id;
        } catch (e) { this.note(null, e.text || X.get("ERRORS", e.code, e.code)); return null; }
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
