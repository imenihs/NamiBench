// NF 測定（設計M。Qt の ui_qt/tab_source.py の NF の欄・nf_dialog.py と同じ部品・同じ語・同じ動き: J §5）。
// - 信号源のタブの NF の欄: ENR（dB の小数）・ENR の表（CSV を読み込む・外す）・NF の増幅度（校正データの倍率）・つなぎ方の窓
// - 中身の文字と使えない理由はサーバーが Qt と同じ nf_view.nf_view で作って ui.nf で送る。ここは映して命令を送るだけ
// - ENR の表はブラウザでファイルを読み、中身を命令で送る（Web の口はパスを受け取らない: 設計A §4.3.2。Qt も中身を送る）
// - NF モードに初めて入ると、コアの知らせ nf_guide で必要な外部機器とつなぎ方の窓を1回だけ出す（U-02）
// 大きさと余白は style.css の段の決まり（J §4.11）: 標準の段、ENR の欄は値の幅（num-field）
(function (root) {
  "use strict";

  const X = root.GSTexts;
  const U = root.GSUnits;
  const N = (k) => X.get("NF_TEXTS", k);
  // 読み込めなかった理由を出しておく長さ（ミリ秒。Qt の tab_source.TABLE_NOTE_MS と同じ）
  const TABLE_NOTE_MS = 8000;

  // ファイルのバイト列を文字にする（UTF-8、読めなければ Shift_JIS。Qt の tab_source.read_text と同じ順）
  function decodeText(buf) {
    try {
      return new TextDecoder("utf-8", { fatal: true }).decode(buf).replace(/^﻿/, "");
    } catch (e) {
      return new TextDecoder("shift_jis").decode(buf);
    }
  }

  // ファイルを1つ選んで中身を読む。選ばなければ null
  function pickTextFile() {
    return new Promise((resolve) => {
      const inp = document.createElement("input");
      inp.type = "file";
      inp.accept = ".csv,.txt,text/csv,text/plain";
      inp.addEventListener("change", async () => {
        const f = inp.files && inp.files[0];
        if (!f) { resolve(null); return; }
        try {
          resolve({ name: f.name, text: decodeText(await f.arrayBuffer()) });
        } catch (e) {
          resolve({ name: f.name, error: true });
        }
      });
      inp.click();
    });
  }

  // 信号源のタブの NF の欄（並びは Qt の tab_source._build_nf と同じ）
  const NF_SECTION = `
<section class="sec" data-sec="nf">
  <h3>{{ N('group') }}</h3>
  <div class="form">
    <label>{{ N('enr') }}</label>
    <num-field data-field="enr" :text="v.enr_text" :note="a.notes.enr" :disabled="!!v.enr_reason"
               :title="v.enr_reason || v.enr_tip || ''" @clear-note="a.clearNote('enr')"
               @commit="a.nfSetEnrText($event)" @step="(e, d) => a.nfStepEnr(e, d)"></num-field>
    <template v-if="v.enr_warn"><label></label><div class="warn" data-nf="enr-warn">{{ v.enr_warn }}</div></template>
    <label>{{ N('enr_table') }}</label><span data-nf="table">{{ v.table_text }}</span>
    <template v-if="v.table_warn"><label></label><div class="warn" data-nf="table-warn">{{ v.table_warn }}</div></template>
    <template v-if="a.nfNote"><label></label><div class="warn" data-nf="table-note">{{ a.nfNote }}</div></template>
    <label></label>
    <div class="row">
      <button :disabled="!!v.load_reason" :title="v.load_reason || ''" @click="a.nfLoadTable()">{{ N('load_table') }}</button>
      <button :disabled="!!v.clear_reason" :title="v.clear_reason || ''" @click="a.cmd('nf_clear_enr_table')">{{ N('clear_table') }}</button>
    </div>
    <label>{{ N('amp') }}</label><span data-nf="amp">{{ v.amp_text }}</span>
  </div>
  <div class="row"><button @click="a.openNfGuide()">{{ N('guide_button') }}</button></div>
</section>`;

  // 必要な外部機器とつなぎ方の窓（Qt の nf_dialog.NfGuideDialog と同じ中身: 校正の画面の準備と同じ形）
  const NF_GUIDE = `
<div class="modal-back" @click="a.closeNfGuide()">
<div class="modal nfguide" @click.stop>
  <h3>{{ g.title }}</h3>
  <div class="form">
    <label>{{ g.need_label }}</label><span>{{ g.need }}</span>
    <label class="cal-top">{{ g.wiring_label }}</label>
    <div class="stack">
      <div class="cal-diagram">
        <template v-for="(b, i) in g.diagram" :key="i">
          <span class="cal-box">{{ b }}</span>
          <svg v-if="i < g.diagram.length - 1" class="cal-arrow" viewBox="0 0 28 26" width="28" height="26" aria-hidden="true">
            <line x1="4" y1="13" x2="22" y2="13" stroke="currentColor" stroke-width="1.5"/><polygon points="24,13 18,9 18,17" fill="currentColor"/></svg>
        </template>
      </div>
      <span>{{ g.wiring }}</span>
    </div>
  </div>
  <div class="modal-btns"><button class="primary" @click="a.closeNfGuide()">{{ g.close }}</button></div>
</div>
</div>`;

  const parent = { computed: { a() { return this.$root; } }, methods: { N(k) { return N(k); } } };

  root.GSWindows = root.GSWindows || {};
  root.GSWindows["gs-nf"] = {
    ...parent,
    template: NF_SECTION,
    computed: { ...parent.computed, v() { return this.a.nfView; } },
  };
  root.GSWindows["gs-nf-guide"] = {
    ...parent,
    template: NF_GUIDE,
    computed: { ...parent.computed, g() { return this.a.nfView.guide || {}; } },
  };

  root.GSAppNf = {
    data() {
      return { nfGuide: false, nfNote: "" };
    },
    computed: {
      nfView() {
        return (this.ui && this.ui.nf) || { enr_text: "", table_text: "", amp_text: "", guide: {} };
      },
      // トレースの平均の種類を NF では選べない理由（Qt の nf_view.average_type_reason と同じ: 設計M §1）
      nfAverageTypeReason() { return this.sw && this.sw.mode === "nf" ? N("average_type_nf") : null; },
    },
    methods: {
      openNfGuide() { this.nfGuide = true; },
      closeNfGuide() { this.nfGuide = false; },
      // 知らせを受けたとき（app.js の onEvent）。NF モードに初めて入った知らせなら窓を出して true
      onNoticeNf(p) {
        if (p.code !== "nf_guide") return false;
        this.nfGuide = true;
        return true;
      },
      nfSetEnrText(text) {
        let v;
        try { v = U.parseDb(String(text).replace("dB", "")); } catch (e) { this.note("enr", X.w("bad_value")); return; }
        this.cmd("nf_set_enr", { enr_db: v }, { field: "enr" });
      },
      nfStepEnr(e, d) {
        const cur = this.nfView.enr_value == null ? 0 : this.nfView.enr_value;
        this.cmd("nf_set_enr", { enr_db: Math.round((cur + d * 10 ** e) * 1000) / 1000 }, { field: "enr" });
      },
      nfFlashNote(text) {
        this.nfNote = text;
        clearTimeout(this._nfNoteTimer);
        if (text) this._nfNoteTimer = setTimeout(() => { this.nfNote = ""; }, TABLE_NOTE_MS);
      },
      async nfLoadTable() {
        if (this.readOnlyBlocks("nf_load_enr_table")) return;
        const f = await pickTextFile();
        if (!f) return;
        if (f.error) { this.nfFlashNote(X.get("ERRORS", "file_unreadable")); return; }
        try {
          await root.GSApi.call("nf_load_enr_table", { text: f.text, file_name: f.name });
          this.nfFlashNote("");
        } catch (e) {
          this.nfFlashNote(e.text || X.get("ERRORS", e.code, e.code || String(e)));
        }
      },
    },
  };
})(window);
