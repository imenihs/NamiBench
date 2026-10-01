// 聞く機能の音を鳴らす（設計Q §6.8: 復調はコア、鳴らすのは画面。Web はブラウザの Web Audio）。
// イベント audio（48kHz・float32・モノラル。api.js が読む）を、途切れないよう順に並べて鳴らす。
// 遅れをためない: 鳴らす予定が MAX_AHEAD_S より先まで詰まったら、その塊は捨てる。
(function (root) {
  "use strict";

  const MAX_AHEAD_S = 0.5;
  const START_DELAY_S = 0.05;

  class Player {
    constructor() {
      this.ctx = null;
      this.gain = null;
      this.next = 0;
      this.volume = 0.5;
    }
    // ブラウザは利用者の操作の後でないと音を出さないことがある。止められていれば true
    get blocked() { return !!this.ctx && this.ctx.state === "suspended"; }
    get available() { return typeof (root.AudioContext || root.webkitAudioContext) === "function"; }
    _open(rate) {
      const AC = root.AudioContext || root.webkitAudioContext;
      if (!AC) return false;
      if (this.ctx && this.ctx.sampleRate === rate) return true;
      this.close();
      try { this.ctx = new AC({ sampleRate: rate }); } catch (e) { this.ctx = new AC(); }
      this.gain = this.ctx.createGain();
      this.gain.gain.value = this.volume;
      this.gain.connect(this.ctx.destination);
      this.next = 0;
      return true;
    }
    play(samples, rate) {
      if (!this._open(rate)) return;
      const ctx = this.ctx;
      if (ctx.state === "suspended") { ctx.resume().catch(() => {}); return; }
      const now = ctx.currentTime;
      if (this.next < now) this.next = now + START_DELAY_S;
      if (this.next - now > MAX_AHEAD_S) return; // 詰まっている: 捨てる
      const buf = ctx.createBuffer(1, samples.length, rate);
      buf.copyToChannel(samples, 0);
      const src = ctx.createBufferSource();
      src.buffer = buf;
      src.connect(this.gain);
      src.start(this.next);
      this.next += samples.length / rate;
    }
    resume() { if (this.ctx && this.ctx.state === "suspended") this.ctx.resume().catch(() => {}); }
    setVolume(v) {
      this.volume = Math.max(0, Math.min(1, Number(v)));
      if (this.gain) this.gain.gain.value = this.volume;
    }
    close() {
      if (this.ctx) this.ctx.close().catch(() => {});
      this.ctx = null;
      this.gain = null;
      this.next = 0;
    }
  }

  root.GSAudio = { Player };
})(window);
