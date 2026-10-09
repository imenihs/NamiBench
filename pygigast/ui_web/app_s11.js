// 信号源のタブの基準（TG の「広く取る」「いまの条件で細かく取る」・基準の値（設計E §6.7）・S11 のオープン → ショート・
// 基準の一覧: 設計E §6・§6.5）と、
// 縦軸の表示の形式（設計R §3: 表示のタブの「振幅の詳細」の「S11 の表示」。リターンロス（dB）・|Γ|・SWR。縦軸に1つ。位相系は
// 位相を測れない機器では無効にして理由: R §5.1）、|Γ|・SWR の縦軸（下端が決まっていて 1目盛だけを選ぶ）、縦軸と単位が
// 違って描かない固定のトレースの「表示」のチェック（無効にして理由はツールチップ）。Qt の ui_qt/tab_source.py・
// tab_display.py・tab_traces.py・main_window.py と同じ並び・同じ語・同じ動き（J §5）。app.js が読み込んで合わせる。
// - 使えない理由・次に押すボタン・状態の文・形式の選択肢・縦軸の決め方はサーバーが Qt と同じ関数で作る
//   （screen_s11.s11_view・axis_format_view・trace_drawn_reason・axis_view・ref_quick_view）。ここは映して命令を送るだけ
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
        // 基準の値（設計E §6.7）。取るときに送る値で、画面ごとに持つ（名前・平均と同じ。Qt の tab_source.take_value）
        refValue: 0,
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
      // 「S11 の表示」の欄（サーバーの screen_s11.axis_format_view。Qt の tab_display._apply_axis_format と同じ）
      axisFormat() { return this.ui.axis_format || { value: "db", items: [], reason: null, tip: "" }; },
      refSelected() { return this.refs.find((r) => r.id === this.refSel) || null; },
      // 基準の値の欄の決まり（一覧・範囲・桁: texts.REF_VALUE_SPEC）と使えない理由（S11: サーバーの screen_s11.ref_value_view）
      refValueSpec() { return X.dict("REF_VALUE_SPEC") || {}; },
      refValueView() { return (this.ui && this.ui.ref_value) || { reason: null, tip: "" }; },
      // 「基準の値…」の使えない理由（選んでいない・S11 の基準。行の理由はサーバーの screen_s11.ref_row: Qt と同じ）
      refValueReason() {
        const r = this.refSelected;
        if (!r) return X.dict("REASON_TEXTS").select_reference;
        return r.value_reason || null;
      },
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

      // ---- 縦軸の表示の形式（Qt の tab_display._axis_format_chosen と同じ）と、描かないトレースの「表示」のチェックの理由
      //      （Qt の tab_traces._TraceRow.apply_drawn と同じ）
      setAxisFormat(code) {
        if (code !== ((this.st || {}).view || {}).s11_format) this.setView({ s11_format: code });
      },
      traceDrawnReason(t) { return ((this.ui && this.ui.trace_drawn) || {})[String(t.number)] || null; },

      // ---- 基準の一覧（Qt の tab_source と同じ: 一覧で選び、下の［使う］［使わない］［名前…］［消す…］）
      async loadRefs() {
        const r = await this.cmd("reference_list", {}, { field: "refs", quiet: ["device_not_selected"] });
        this.refs = (r && r.references) || [];
        if (this.refSel && !this.refs.some((x) => x.id === this.refSel)) this.refSel = null;
      },
      async takeRef(kind) {
        await this.cmd("reference_take", { kind, name: this.refName || null, averages: Number(this.refAvg) || 1,
          value_db: Number(this.refValue) || 0 }, { field: "refs" });
      },
      // S11 のオープン・ショート（設計E §6.5）。オープンは選んだ範囲、ショートはオープンと同じ範囲（コアが合わせる）
      async takeS11(standard) {
        const args = { standard, name: this.refName || null, averages: Number(this.refAvg) || 1 };
        if (standard === "open") args.kind = this.s11Range;
        await this.cmd("reference_take", args, { field: "refs" });
      },
      // 上段の「基準を取る」（TG）／次の1段（S11: オープンを測る → ショートを測る）。送る中身はサーバーの
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
          // 基準の値は信号源のタブの欄の値（設計E §6.7。Qt の ref_quick.take と同じ。S11 には送らない）
          this.cmd("reference_take", { kind: "wide", name: X.fmt("ref_direct_name", { time }), show_zero: true,
            value_db: Number(this.refValue) || 0 });
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
      // 一覧で選んだ基準の値を直す（測り直さない: 設計E §6.7。Qt の tab_source._edit_value と同じ）
      refEditValue() {
        const r = this.refSelected;
        if (!r || this.refValueReason) return;
        this.openModal(W("ref_value_title"), [{ key: "value", label: W("ref_value_new"), value: GSUnits.levelText(r.value_db || 0), width: "w-value" }], (v) => {
          const t = String(v.value == null ? "" : v.value).trim();
          if (!t) return;
          let db;
          try { db = GSUnits.parseDb(t); } catch (e) { this.note("refs", W("bad_value")); return; }
          if (db !== (r.value_db || 0)) this.refAction("reference_set_value", { id: r.id, value_db: db });
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
        // 一覧の行の基準の値（別の画面から直したときも: 設計E §6.7。Qt と同じ）
        if (p.code === "reference_value_changed") this.loadRefs();
      },
    },
  };
})(window);
