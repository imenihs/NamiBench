// 数値の欄（設計J §3。Qt の ui_qt/fields.py と同じ振る舞い）。
// - 単位付きの自由入力。確定は Enter・Tab・欄を離れる のどれでも。Esc は打つ前の値に戻す（J §4.6）
// - ホイールと ↑↓ はカーソルのある桁を1つ増減（実機のノブの感覚）。wheelExp を決めた欄はホイールだけその桁
// - choices を渡すと、欄の右に一覧（ドロップダウン）を付ける（REF レベル: J §4.1）
// - 名前を上、値を下。欄の下に一度きりの知らせを重ねて出し、ボタンを1つ添えられる（設計C §5.11 の 3）
(function (root) {
  "use strict";

  const NOTE_SHOW_MS = 4000;
  // ボタンを添えた知らせは、読んで押すまでの時間を取る。マウスが知らせの上にある間は消さない
  const NOTE_ACTION_SHOW_MS = 10000;

  const NumField = {
    name: "num-field",
    props: {
      label: String,
      text: String, // いまの値の表示
      note: Object, // { text, action: { label, run } } か null
      big: { type: Boolean, default: false },
      disabled: { type: Boolean, default: false },
      title: String,
      // 一覧から選べる値（[{ value, text }]。REF レベル: J §4.1）。選ぶと text を確定する
      choices: { type: Array, default: null },
      // ホイールで増減する桁を決めておく（REF レベルは 0 = 1dB）。null はカーソルのある桁
      wheelExp: { type: Number, default: null },
    },
    emits: ["commit", "step", "clear-note"],
    data() {
      return { editing: false, draft: "", hovered: false, timer: null };
    },
    watch: {
      text(v) { if (!this.editing) this.draft = v; },
      note(n) { this.armTimer(n); },
    },
    created() { this.draft = this.text || ""; },
    methods: {
      armTimer(n) {
        clearTimeout(this.timer);
        if (!n) return;
        this.timer = setTimeout(() => this.expire(), n.action ? NOTE_ACTION_SHOW_MS : NOTE_SHOW_MS);
      },
      expire() {
        if (this.hovered) { this.timer = setTimeout(() => this.expire(), 1000); return; }
        this.$emit("clear-note");
      },
      onInput(e) { this.editing = true; this.draft = e.target.value; },
      commit() {
        if (!this.editing) return;
        this.editing = false;
        const t = this.draft;
        this.draft = this.text;
        this.$emit("commit", t);
      },
      onKey(e) {
        if (e.key === "Enter") { e.preventDefault(); this.commit(); return; }
        if (e.key === "Escape") { this.editing = false; this.draft = this.text; e.target.value = this.text; return; }
        if (e.key === "ArrowUp" || e.key === "ArrowDown") {
          e.preventDefault();
          this.step(e.target, e.key === "ArrowUp" ? 1 : -1);
        }
      },
      onWheel(e) {
        if (this.disabled) return;
        e.preventDefault();
        const dir = e.deltaY < 0 ? 1 : -1;
        if (this.wheelExp !== null) { this.editing = false; this.$emit("step", this.wheelExp, dir); return; }
        this.step(this.$refs.input, dir);
      },
      pick(e) {
        const t = e.target.value;
        e.target.value = "";
        if (t !== "") { this.editing = false; this.$emit("commit", t); }
      },
      step(input, dir) {
        const pos = input.selectionStart == null ? 0 : input.selectionStart;
        const exp = GSUnits.digitExponent(input.value, pos);
        if (exp === null) return;
        this.editing = false;
        this.$emit("step", exp, dir);
        // 桁の位置を保つ（値の文字数が変わったら右端からの位置で合わせる）
        const fromEnd = input.value.length - pos;
        this.$nextTick(() => {
          const p = Math.max(0, input.value.length - fromEnd);
          try { input.setSelectionRange(p, p); } catch (err) { /* 欄が消えた */ }
        });
      },
      runAction() {
        const a = this.note && this.note.action;
        this.$emit("clear-note");
        if (a) a.run();
      },
    },
    template: `
      <div class="field" :class="{big}" :title="title">
        <div class="field-head"><span class="field-name">{{ label }}</span><slot name="head"></slot></div>
        <div class="field-line">
          <input ref="input" class="field-input" :value="editing ? draft : text" :disabled="disabled" spellcheck="false"
                 @input="onInput" @keydown="onKey" @blur="commit" @wheel="onWheel">
          <select v-if="choices" class="field-choices" :disabled="disabled" @change="pick" @wheel="onWheel">
            <option value="" hidden></option>
            <option v-for="c in choices" :key="c.value" :value="c.text">{{ c.text }}</option>
          </select>
        </div>
        <div v-if="note" class="note-pop" @mouseenter="hovered=true" @mouseleave="hovered=false">
          <span>{{ note.text }}</span>
          <button v-if="note.action" class="note-action" @click="runAction">{{ note.action.label }}</button>
        </div>
      </div>`,
  };

  root.GSFields = { NumField };
})(window);
