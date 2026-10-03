// 信号源のタブの基準（TG の「広く取る」「いまの条件で細かく取る」・S11 のオープン → ショート・基準の一覧: 設計E §6・§6.5）と、
// トレースの表示の形式（設計R §3: リターンロス（dB）・|Γ|・SWR。位相系は位相を測れない機器では無効にして理由: R §5.1）、
// |Γ|・SWR の縦軸（下端が決まっていて 1目盛だけを選ぶ）。Qt の ui_qt/tab_source.py・tab_traces.py・main_window.py と
// 同じ並び・同じ語・同じ動き（J §5）。app.js が読み込んで合わせる。
// - 使えない理由・次に押すボタン・状態の文・形式の選択肢・縦軸の決め方はサーバーが Qt と同じ関数で作る
//   （screen_s11.s11_view・trace_format_view・axis_view・ref_quick_view）。ここは映して命令を送るだけ
(function (root) {
  "use strict";

  const X = root.GSTexts;
  const W = (k, kw) => X.w(k, kw);
  const DB_AXIS = { linear: false, unit: null, div_label: "dB/div", div_key: "db_per_div", ref_reason: null, decimals: 1,
    div_choices: [1, 2, 5, 10].map((v) => ({ value: v, text: `${v} dB` })) };

  // 1目盛を書き分けられる小数の桁（Qt の formats.decimals_for・tick_decimals と同じ: 少なくとも1桁）
  function tickDecimals(div) {
    const s = String(div);
    const d = s.includes(".") ? s.split(".")[1].length : 0;
    return Math.max(1, Math.min(3, d));
  }

  root.GSAppS11 = {
    data() {
      return {
        refs: [],
        refName: "",
        refAvg: 1,
        refSel: null,  // 一覧で選んでいる基準（Qt の一覧の選択と同じ。使う・名前・消すの相手）
        refSerial: null,
        s11Range: "wide",
        s11OpenKind: null,
      };
    },
    computed: {
      // 縦軸の決め方（サーバーの screen_s11.axis_view: dB 系か、下端の決まった |Γ|・SWR か）
      axis() { return this.ui.axis || DB_AXIS; },
      s11() { return this.ui.s11 || {}; },
      refSelected() { return this.refs.find((r) => r.id === this.refSel) || null; },
      refAnySelected() { return this.refs.some((r) => r.selected); },
      // しきい値との交点の欄の決まり（一覧・符号・範囲・桁: texts.THRESHOLD_SPECS。Qt の screen_s11.threshold_spec と同じ）。
      // 選んでいるマーカーのトレースの単位（無ければ縦軸の単位）。|Γ|・SWR はその値の一覧（例 SWR 2 以下: 設計G §3.5）
      thSpec() {
        const m = this.selMarker;
        const unit = (m && m.unit) || this.axisUnit;
        const specs = X.dict("THRESHOLD_SPECS");
        const key = Object.prototype.hasOwnProperty.call(specs, unit) && unit !== "dB" ? unit : "dB";
        return { ...(specs[key] || {}), key, unit: unit || "" };
      },
    },
    methods: {
      // 単位の種類が変わったら、しきい値の欄（切っている間の値）はその単位の初期値から（Qt と同じ）
      watchThSpec() {
        this.$watch(() => this.thSpec.key, (k, old) => {
          if (k !== old && this.thSpec.default != null) this.fn.thr = this.thSpec.default;
        });
      },
      // ---- 縦軸（|Γ|・SWR は下端が決まっていて、REF を動かす操作は使えない: 設計R §3）
      linearAmp(a, pick) {
        const ax = this.axis;
        const div = Number(pick(ax.div_key, ax.div));
        return { ref: ax.bottom + GSGraph.DIVISIONS * div, div, unit: a.unit, linear: true, decimals: tickDecimals(div) };
      },
      refBlocked() {
        const why = this.axis.ref_reason;
        if (why) this.note("ref", why);
        return !!why;
      },
      stepLinearDiv(steps) {
        const values = this.axis.div_choices.map((c) => c.value);
        let i = values.indexOf(this.amp.div);
        if (i < 0) i = values.length - 1;
        i = Math.min(Math.max(i + (steps > 0 ? 1 : -1), 0), values.length - 1);
        this.setDiv(values[i]);
      },

      // ---- トレースの表示の形式（Qt の tab_traces._TraceRow.apply_format と同じ）
      traceFormat(t) {
        return ((this.ui && this.ui.trace_format) || {})[String(t.number)] || { value: "db", items: [], reason: null, tip: "" };
      },
      traceDrawnReason(t) { return ((this.ui && this.ui.trace_drawn) || {})[String(t.number)] || null; },
      setTraceFormat(t, code) {
        if (code !== t.format) this.setTrace(t.number, "format", code);
      },

      // ---- 基準の一覧（Qt の tab_source と同じ: 一覧で選び、下の［使う］［使わない］［名前…］［消す…］）
      async loadRefs() {
        const r = await this.cmd("reference_list", {}, { field: "refs", quiet: ["device_not_selected"] });
        this.refs = (r && r.references) || [];
        if (this.refSel && !this.refs.some((x) => x.id === this.refSel)) this.refSel = null;
      },
      refWhen(ts) {
        if (!ts) return "";
        const d = new Date(ts * 1000);
        const p = (n) => String(n).padStart(2, "0");
        return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
      },
      refRowText(r) { return `${r.selected ? "● " : "   "}${r.name}  ${String(r.mode || "").toUpperCase()}  ${this.refWhen(r.taken_at)}`; },
      async takeRef(kind) {
        await this.cmd("reference_take", { kind, name: this.refName || null, averages: Number(this.refAvg) || 1 }, { field: "refs" });
      },
      // S11 のオープン・ショート（設計E §6.5）。オープンは選んだ範囲、ショートはオープンと同じ範囲（コアが合わせる）
      async takeS11(standard) {
        const args = { standard, name: this.refName || null, averages: Number(this.refAvg) || 1 };
        if (standard === "open") args.kind = this.s11Range;
        await this.cmd("reference_take", args, { field: "refs" });
      },
      // 上段の「直結を 0dB にする」（TG）／次の1段（S11: オープンを測る → ショートを測る）。送る中身はサーバーの
      // screen_model.ref_quick_view の take（Qt の ref_quick.take と同じ）
      takeQuick() {
        const d = new Date();
        const p = (n) => String(n).padStart(2, "0");
        const time = `${p(d.getHours())}:${p(d.getMinutes())}`;
        const t = this.refQuick.take || { standard: null, kind: "wide", show_zero: true };
        if (t.standard) {
          const args = { standard: t.standard, name: X.fmt("ref_s11_name", { time }), show_zero: !!t.show_zero };
          if (t.kind) args.kind = t.kind;
          this.cmd("reference_take", args);
        } else {
          this.cmd("reference_take", { kind: "wide", name: X.fmt("ref_direct_name", { time }), show_zero: true });
        }
      },
      async refAction(name, args) { await this.cmd(name, args, { field: "refs" }); this.loadRefs(); },
      refUse() { if (this.refSel) this.refAction("reference_select", { id: this.refSel }); },
      refRename() {
        const r = this.refSelected;
        if (!r) return;
        this.openModal(W("rename_ref_title"), [{ key: "name", label: W("new_name"), value: r.name }], (v) => {
          if (v.name && v.name.trim() && v.name !== r.name) this.refAction("reference_rename", { id: r.id, name: v.name.trim() });
        });
      },
      async refDelete() {
        const r = this.refSelected;
        if (!r) return;
        const extra = r.selected ? W("ref_delete_in_use") : "";
        if (!(await this.askConfirm(W("ref_delete_title"), W("ref_delete_confirm", { name: r.name }) + extra, W("do_delete")))) return;
        this.refAction("reference_delete", { id: r.id });
      },
      // 状態が届いたとき: 機器が変わったら一覧を読み直す。オープンを測ったら範囲の欄はその範囲を出す（Qt と同じ）
      onStateS11(st) {
        const serial = ((st.connection || {}).identity || {}).serial || null;
        if (serial !== this.refSerial) {
          this.refSerial = serial;
          if (serial) this.loadRefs(); else this.refs = [];
        }
        const kind = ((st.ui || {}).s11 || {}).open_kind || null;
        if (kind !== this.s11OpenKind) {
          this.s11OpenKind = kind;
          if (kind) this.s11Range = kind;
        }
      },
      // 基準を取り終えたら一覧を読み直す（オープンだけを測った段は基準ができていないので読まない: Qt と同じ）
      onNoticeS11(p) {
        const d = p.detail || {};
        if (p.code === "task_done" && d.task === "reference_take" && (d.result || {}).next !== "short") this.loadRefs();
      },
    },
  };
})(window);
