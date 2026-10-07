// Web の口の利用者側（設計A §4.3）。命令は POST、状態とイベントは WebSocket。
// 命令の名前・引数・戻り値は Qt が使う同じプロセスの口と同じ。
(function (root) {
  "use strict";

  class CommandError extends Error {
    constructor(body, status) {
      const e = (body && body.error) || { code: "http_" + status, field: null, detail: {} };
      super(e.code);
      this.code = e.code;
      this.field = e.field;
      this.detail = e.detail || {};
      // 画面の文（サーバーが Qt と同じ texts.error_text で作って足す: view.py）
      this.text = e.text || "";
      // 繋げなかったときの理由と次の手（機器の一覧に出す: J §4.7）
      this.next = e.text_next || this.text;
      this.status = status;
    }
  }

  async function call(name, args = {}) {
    let res;
    try {
      res = await fetch("/api/v1/" + encodeURIComponent(name), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(args),
      });
    } catch (e) {
      throw new CommandError({ error: { code: "server_unreachable", field: null, detail: { message: String(e) } } }, 0);
    }
    let body = null;
    try { body = await res.json(); } catch (e) { body = null; }
    if (!res.ok) throw new CommandError(body, res.status);
    return body;
  }

  async function getJson(path) {
    const res = await fetch(path);
    if (!res.ok) throw new CommandError(null, res.status);
    return res.json();
  }

  // トレースと音のバイナリ（server.py の encode_trace・encode_audio）を読む:
  // [見出しの長さ uint32 LE][見出し JSON][値 float32 LE]。値の NaN（値なし）は null にする（uPlot の途切れ）
  function decodeTrace(buf) {
    const dv = new DataView(buf);
    const hlen = dv.getUint32(0, true);
    const header = JSON.parse(new TextDecoder("utf-8").decode(new Uint8Array(buf, 4, hlen)));
    // 密度の升目（設計P §9.3）は uint8、ほかは float32
    const values = header.value_type === "uint8"
      ? new Uint8Array(buf, 4 + hlen, header.value_count) : new Float32Array(buf, 4 + hlen, header.value_count);
    header.values = values;
    if (header.kind === "waterfall") {
      // ウォーターフォールの行（設計P §8: server.py の encode_waterfall）。値は行を古い順につないだもの
      let k = 0;
      for (const r of header.rows || []) { r.values = values.subarray(k, k + r.n); k += r.n; }
    }
    return header;
  }

  // ウォーターフォールの行を取り直す（命令 waterfall_rows。戻り値はイベントと同じバイナリ）
  async function waterfallRows(args) {
    let res;
    try {
      res = await fetch("/api/v1/waterfall_rows", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(args) });
    } catch (e) {
      throw new CommandError({ error: { code: "server_unreachable", field: null, detail: { message: String(e) } } }, 0);
    }
    if (!res.ok) {
      let body = null;
      try { body = await res.json(); } catch (e) { body = null; }
      throw new CommandError(body, res.status);
    }
    return decodeTrace(await res.arrayBuffer());
  }

  // イベントの購読。切れたら繋ぎ直す（サーバーの再起動・スリープからの復帰）
  class EventStream {
    constructor(handlers) {
      this.h = handlers;
      this.ws = null;
      this.closed = false;
      this.retryMs = 500;
    }
    open() {
      const proto = location.protocol === "https:" ? "wss:" : "ws:";
      const ws = new WebSocket(proto + "//" + location.host + "/api/v1/events");
      ws.binaryType = "arraybuffer";
      this.ws = ws;
      ws.onopen = () => { this.retryMs = 500; this.h.onOpen && this.h.onOpen(); };
      ws.onmessage = (ev) => {
        if (typeof ev.data === "string") {
          const m = JSON.parse(ev.data);
          this.h.onEvent(m.kind, m.payload);
        } else {
          // バイナリはトレース・聞く機能の音・ウォーターフォールの行・密度（見出しの kind で分ける）
          const m = decodeTrace(ev.data);
          this.h.onEvent(["audio", "waterfall", "density"].includes(m.kind) ? m.kind : "trace", m);
        }
      };
      ws.onclose = () => {
        this.h.onClose && this.h.onClose();
        if (this.closed) return;
        setTimeout(() => this.open(), this.retryMs);
        this.retryMs = Math.min(this.retryMs * 2, 5000);
      };
    }
    close() {
      this.closed = true;
      if (this.ws) this.ws.close();
    }
  }

  // 次の描き直しで1回だけ fn を呼ぶ（同じ key で何度呼んでも1回）。窓が隠れて requestAnimationFrame が止まっていても
  // FRAME_FALLBACK_MS で呼ぶ（別ウィンドウ・裏のタブでも状態を映す）
  const FRAME_FALLBACK_MS = 100;
  function onFrame(owner, key, fn) {
    if (owner[key]) return;
    let done = false;
    const run = () => {
      if (done) return;
      done = true;
      owner[key] = null;
      fn();
    };
    owner[key] = { raf: requestAnimationFrame(run), timer: setTimeout(run, FRAME_FALLBACK_MS) };
  }
  root.GSOnFrame = onFrame;

  root.GSApi = { call, getJson, decodeTrace, waterfallRows, EventStream, CommandError };
})(window);
