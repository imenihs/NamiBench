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

  // 状態を映す入力の値（v-steady="値"。:value の代わりに使う。Qt の ui_qt/widget_sync.py と同じ決まり）。
  // - フォーカスがある間は書き直さない（選んだ範囲・カーソル・打っている途中の文字を消さない）
  // - 表示が変わらないときは書かない
  // - 書かなかった値は、フォーカスが外れたときに最新の値で入れる（打って確定したときは確定の戻りを待つ）
  // - この欄で確定した（Enter・change）・桁を送った値の戻りは、フォーカスがあっても映す。次に触ると解く
  function steadyPut(el) {
    const s = el._svValue == null ? "" : String(el._svValue);
    if (el.value === s) return;
    const doc = el.ownerDocument || (typeof document !== "undefined" ? document : null);
    if (doc && doc.activeElement === el && el.selectionStart != null) {
      // 送った桁の位置を保つ（値の文字数が変わったら右端からの位置で合わせる）
      const fromEnd = el.value.length - el.selectionStart;
      el.value = s;
      const p = Math.max(0, s.length - fromEnd);
      try { el.setSelectionRange(p, p); } catch (err) { /* 範囲を持たない欄 */ }
      return;
    }
    el.value = s;
  }
  function steadyFocused(el) {
    const doc = el.ownerDocument || (typeof document !== "undefined" ? document : null);
    return !!doc && doc.activeElement === el;
  }
  const Steady = {
    mounted(el, b) {
      el._svValue = b.value;
      el._svEcho = false;
      el._svEdited = false;
      steadyPut(el);
      el.addEventListener("focus", () => { el._svEcho = false; el._svEdited = false; });
      el.addEventListener("input", () => { el._svEcho = false; el._svEdited = true; });
      el.addEventListener("mousedown", () => { el._svEcho = false; });
      el.addEventListener("keydown", (e) => {
        if (e.key === "Enter" || e.key === "ArrowUp" || e.key === "ArrowDown") return;
        el._svEcho = false;
      });
      el.addEventListener("change", () => { el._svEcho = true; });
      el.addEventListener("blur", () => {
        const edited = el._svEdited;
        el._svEcho = false;
        el._svEdited = false;
        // 打った値は確定した（change）。戻りの値は次に映すときに入る。打っていなければ最新の値を入れる
        if (!edited) steadyPut(el);
      });
    },
    updated(el, b) {
      el._svValue = b.value;
      if (!steadyFocused(el) || el._svEcho) steadyPut(el);
    },
  };
  // 確定・桁の送りの戻りを、フォーカスがあっても映す（NumField が Enter・↑↓・ホイール・一覧で送ったとき）
  function steadyEcho(el) {
    if (!el) return;
    el._svEcho = true;
    el._svEdited = false;
  }

  const NumField = {
    name: "num-field",
    directives: { steady: Steady },
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
        steadyEcho(this.$refs.input);
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
        if (this.wheelExp !== null) {
          this.editing = false;
          steadyEcho(this.$refs.input);
          this.$emit("step", this.wheelExp, dir);
          return;
        }
        this.step(this.$refs.input, dir);
      },
      pick(e) {
        const t = e.target.value;
        e.target.value = "";
        if (t !== "") { this.editing = false; steadyEcho(this.$refs.input); this.$emit("commit", t); }
      },
      step(input, dir) {
        const pos = input.selectionStart == null ? 0 : input.selectionStart;
        const exp = GSUnits.digitExponent(input.value, pos);
        if (exp === null) return;
        this.editing = false;
        steadyEcho(input);  // 戻りの値を映すとき、桁の位置を右端からの位置で保つ（steadyPut）
        this.$emit("step", exp, dir);
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
          <input ref="input" class="field-input" v-steady="editing ? draft : text" :disabled="disabled" spellcheck="false"
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

  root.GSFields = { NumField, Steady };
})(window);
