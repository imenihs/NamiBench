// ゼロスパンの時間軸と流す表示（設計N §1。Qt の ui_qt/time_axis.py・tab_display の欄・tab_markers の時間の欄と同じ）。
// 横軸の範囲・名前・欄の中身はサーバーが Qt と同じ関数で作る（ui.x_axis・ui.zero_span: screen_model）。
// ここはトレースの横軸の位置の計算と、時間の文字だけを持つ。位置はマイクロ秒の整数（マーカー・XY カーソルと同じ）
(function (root) {
  "use strict";

  const U = root.GSUnits;

  // トレースの横軸の位置（screen_model.trace_x と同じ）: 時間軸はマイクロ秒、不等間隔の周波数、等間隔の周波数
  function traceX(ax) {
    if (ax.kind === "time") return (i) => ax.start_us + ax.step_us * i;
    if (ax.freqs_hz) return (i) => ax.freqs_hz[i];
    return (i) => ax.start_hz + ax.step_hz * i;
  }

  // x に最も近い点の番号（等間隔の軸だけ。不等間隔・幅 0 の軸は null）
  function indexAt(ax, x) {
    if (ax.kind === "time") return ax.step_us ? Math.round((x - ax.start_us) / ax.step_us) : null;
    if (ax.freqs_hz || !ax.step_hz) return null;
    return Math.round((x - ax.start_hz) / ax.step_hz);
  }

  root.GSZeroSpan = { traceX, indexAt };

  root.GSAppZeroSpan = {
    computed: {
      // ゼロスパンの欄（表示する長さ）と、時間軸で使わない欄の理由（サーバーの screen_model.zero_span_view）
      zsView() {
        return (this.ui && this.ui.zero_span) || { time: false, items: [], value: 10, reason: null, freq_only: null };
      },
    },
    methods: {
      // 横軸の位置の文字: 換算後の周波数（設計O）、ゼロスパンは時間（ms）。Qt の screen_model.x_text と同じ
      xText(x) { return this.zsView.time ? U.formatTimeUs(x) : U.formatFreq(this.shownHz(Math.round(x))); },
      setZeroSpanLength(v) { this.setView({ zero_span_s: Number(v) }, "view_zero"); },
      // 位置の差（ΔX）の文字: 周波数、ゼロスパンは時間
      fmtX(v) { return v == null ? "" : this.zsView.time ? U.formatTimeUs(v) : U.formatFreq(v); },
      // オフセットの種類: ゼロスパンでは周波数を選べないので、選んであってもレベル（Qt の tab_markers と同じ）
      offType() { return this.zsView.time && this.off.type === "freq" ? "level" : this.off.type; },
    },
  };
})(window);
