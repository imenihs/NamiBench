// 信号源のタブの SG のステップ掃引（設計L §1.3）と、グラフの内部の漏れの印（設計L §1.4）。Qt の step_panel.py・
// mark_bands.py と同じ部品・同じ語・同じ動き（J §5）。中身はサーバーが Qt と同じ関数で作って送る（ui.step・ui.leaks:
// ui_qt/source_view.py）。設定はコアに1つ（命令 set_step_sweep。Qt と同じものを見る）。直した値はその欄の下に一度
// 知らせる（M-02。文はサーバーの texts.step_notes）。進み具合とやめるは窓の下の処理の帯（app.js の tasks）
(function (root) {
  "use strict";

  const U = root.GSUnits;

  root.GSAppStep = {
    computed: {
      stepView() { return (this.ui && this.ui.step) || { combines: [], traces: [] }; },
    },
    methods: {
      stepText(key) {
        const v = this.stepView[key];
        return v == null ? "" : U.freqFieldText(v);
      },
      async setStep(args, field) {
        const r = await this.cmd("set_step_sweep", args, { field: field || "step" });
        for (const n of (r && r.texts && r.texts.step_notes) || []) this.note(n.field, n.text);
        return r;
      },
      // SG の周波数（周波数オフセットを足さない: 機器が出す周波数。SG 周波数の欄と同じ）
      commitStepFreq(key, field, text) {
        let hz;
        try { hz = U.parseFreqHz(text); } catch (e) { this.note(field, this.W("bad_freq")); return; }
        this.setStep({ [key]: hz }, field);
      },
      stepStepFreq(key, field, exp, dir) {
        const cur = this.stepView[key];
        if (cur == null) return;
        // 欄の数字は MHz なので、10^exp MHz = 10^(exp+6) Hz（SG 周波数の欄と同じ）
        const step = exp + 6 >= 0 ? 10 ** (exp + 6) : 1;
        this.setStep({ [key]: cur + dir * step }, field);
      },
      // 1段の回数（欄は int-field: 数でない・範囲の外の値は欄が送らずに戻す。Qt の QSpinBox と同じ）
      setStepPerStep(v) { this.setStep({ per_step: v }, "step_per_step"); },
      stepRun() { this.cmd("step_sweep_start", {}, { field: "step" }); },
      // 内部の漏れの印（グラフの上端の ▼。色は配色の「注意」）
      leakMarks() { return ((this.ui && this.ui.leaks) || []).map((m) => ({ ...m, fill: this.palette.warn })); },
    },
  };
})(window);
