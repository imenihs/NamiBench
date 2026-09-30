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

  // トレースのバイナリ（server.py の encode_trace）を読む:
  // [見出しの長さ uint32 LE][見出し JSON][値 float32 LE]。値の NaN（値なし）は null にする（uPlot の途切れ）
  function decodeTrace(buf) {
    const dv = new DataView(buf);
    const hlen = dv.getUint32(0, true);
    const header = JSON.parse(new TextDecoder("utf-8").decode(new Uint8Array(buf, 4, hlen)));
    const values = new Float32Array(buf, 4 + hlen, header.value_count);
    header.values = values;
    return header;
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
          this.h.onEvent("trace", decodeTrace(ev.data));
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

  root.GSApi = { call, getJson, decodeTrace, EventStream, CommandError };
})(window);
