// 数値の欄（設計J §3。Qt の ui_qt/fields.py と同じ振る舞い）。
// - 単位付きの自由入力。確定は Enter・Tab・欄を離れる のどれでも。Esc は打つ前の値に戻す（J §4.6）
// - ホイールと ↑↓ はカーソルのある桁を1つ増減（実機のノブの感覚）。wheelExp を決めた欄はホイールだけその桁
// - choices を渡すと、欄の右に一覧（ドロップダウン）を付ける（REF レベル: J §4.1）
// - 上段（大の段）では名前を値の上の小さな行に置く（J §4.11・S-05）。標準の段では名前を渡さず、呼ぶ側が欄の左に置く
// - 欄の下に一度きりの知らせを重ねて出し、ボタンを1つ添えられる（設計C §5.11 の 3）
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
  // スクロールする場所の中の欄は、フォーカスがあるときだけホイールで値を変える（設計J §4.10 の 1。Qt の wheel_guard と同じ決まり）。
  // フォーカスが無ければ何もせず、ホイールはその場所のスクロールに任せる。上段の欄はスクロールしない場所にあるので今まで通り
  function inScrollPlace(el) {
    const view = (el.ownerDocument && el.ownerDocument.defaultView) || root;
    for (let p = el.parentElement; p; p = p.parentElement) {
      const oy = view.getComputedStyle ? view.getComputedStyle(p).overflowY : "";
      if (oy === "auto" || oy === "scroll") return true;
    }
    return false;
  }
  function wheelIgnored(container) {
    if (!container || !container.parentElement) return false;  // 画面に置かれていない欄（試験の入れ物など）は今まで通り
    const doc = container.ownerDocument;
    const focused = !!(doc && doc.activeElement && container.contains(doc.activeElement));
    return !focused && inScrollPlace(container);
  }

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
        if (this.disabled || wheelIgnored(this.$el)) return;
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
        <div class="field-head" data-tier="S" v-if="label || $slots.head"><span class="field-name">{{ label }}</span><slot name="head"></slot></div>
        <div class="field-line" :class="{choices: !!choices}">
          <input ref="input" class="field-input" v-steady="editing ? draft : text" :disabled="disabled" spellcheck="false"
                 @input="onInput" @keydown="onKey" @blur="commit" @wheel="onWheel">
          <select v-if="choices" class="field-choices" :disabled="disabled" @change="pick" @wheel="onWheel">
            <option value="" hidden></option>
            <option v-for="c in choices" :key="c.value" :value="c.text">{{ c.text }}</option>
          </select>
        </div>
        <div v-if="note" class="note-pop" data-tier="M" @mouseenter="hovered=true" @mouseleave="hovered=false">
          <span>{{ note.text }}</span>
          <button v-if="note.action" class="note-action" @click="runAction">{{ note.action.label }}</button>
        </div>
      </div>`,
  };

  // ---- トレースの回数の欄（設計F §2。Qt の ui_qt/fields.py の CountField と同じ決まり） ----
  // 一覧は「回数なし」（回数を持てる動作のときだけ）と COUNT_CHOICES。打てば 1〜COUNT_MAX のどれでも
  const COUNT_CHOICES = [2, 4, 8, 16, 32, 64, 128, 256, 512, 1000];
  const COUNT_MAX = 10000;
  function countWords() {
    const X = root.GSTexts;
    return { none: X ? X.w("no_count") : "", suffix: X ? X.w("times_suffix") : "" };
  }
  function countText(v) {
    const w = countWords();
    return v == null ? w.none : `${v}${w.suffix}`;
  }
  // 打った文字を回数にする。{ value }（null は回数なし）か、受け付けないとき null
  function parseCount(text, allowNone) {
    const w = countWords();
    // 全角の数字も数字として読む（Qt の str.isdigit と同じ）
    let t = String(text == null ? "" : text).normalize("NFKC").trim();
    // 「回数なし」は単位の字（回）を外す前に比べる（外すと「数なし」になって見分けられない）
    if (t === "" || t === w.none.normalize("NFKC")) return allowNone ? { value: null } : null;
    const suf = w.suffix.trim();
    if (suf) t = t.split(suf).join("").trim();
    if (!/^\d+$/.test(t)) return null;
    const v = Number(t);
    return v >= 1 && v <= COUNT_MAX ? { value: v } : null;
  }
  // ↑↓・ホイールで送った回数（下は回数を持てるなら 0 = 回数なし、平均なら 1。上は COUNT_MAX）
  function stepCount(value, exp, dir, allowNone) {
    const lo = allowNone ? 0 : 1;
    const v = Math.min(Math.max((value || 0) + dir * 10 ** Math.max(exp, 0), lo), COUNT_MAX);
    return v || null;
  }

  // 打てるドロップダウン: 欄に数を打つか、右の一覧から選ぶ（NumField の一覧と同じ形）。
  // 確定は Enter・欄を離れる・一覧から選ぶ。Esc は打つ前に戻す。打っている間は状態の値で書き直さない（v-steady）。
  // 受け付けない文字は今の値に戻して invalid を出す（呼ぶ側が知らせを出す）
  const CountField = {
    name: "count-field",
    directives: { steady: Steady },
    props: {
      value: { type: Number, default: null },
      allowNone: { type: Boolean, default: true },
      disabled: { type: Boolean, default: false },
      title: String,
    },
    emits: ["commit", "invalid"],
    data() { return { editing: false, draft: "" }; },
    computed: {
      shown() { return countText(this.value); },
      items() {
        const rows = COUNT_CHOICES.map((v) => ({ key: String(v), value: v, text: countText(v) }));
        return this.allowNone ? [{ key: "none", value: null, text: countText(null) }, ...rows] : rows;
      },
    },
    methods: {
      onInput(e) { this.editing = true; this.draft = e.target.value; },
      send(v) {
        steadyEcho(this.$refs.input);
        if (v === this.value) { this.$refs.input.value = this.shown; return; }
        this.$emit("commit", v);
      },
      commit() {
        if (!this.editing) return;
        this.editing = false;
        const r = parseCount(this.draft, this.allowNone);
        this.draft = this.shown;
        if (r) { this.send(r.value); return; }
        this.$refs.input.value = this.shown;
        this.$emit("invalid");
      },
      onKey(e) {
        if (e.key === "Enter") { e.preventDefault(); this.commit(); return; }
        if (e.key === "Escape") { this.editing = false; this.draft = this.shown; e.target.value = this.shown; return; }
        if (e.key === "ArrowUp" || e.key === "ArrowDown") {
          e.preventDefault();
          const pos = e.target.selectionStart == null ? 0 : e.target.selectionStart;
          const exp = GSUnits.digitExponent(e.target.value, pos);
          this.step(exp === null ? 0 : exp, e.key === "ArrowUp" ? 1 : -1);
        }
      },
      onWheel(e) {
        if (this.disabled || wheelIgnored(this.$el)) return;
        e.preventDefault();
        this.step(0, e.deltaY < 0 ? 1 : -1);  // ホイールは 1 ずつ（一覧の行を送らない）
      },
      step(exp, dir) {
        this.editing = false;
        this.send(stepCount(this.value, exp, dir, this.allowNone));
      },
      pick(e) {
        const i = e.target.value;
        e.target.value = "";
        const it = this.items.find((x) => x.key === i);
        if (!it) return;
        this.editing = false;
        this.send(it.value);
      },
    },
    template: `
      <span class="count-field" :title="title">
        <input ref="input" class="count-input" v-steady="editing ? draft : shown" :disabled="disabled" spellcheck="false"
               @input="onInput" @keydown="onKey" @blur="commit" @wheel="onWheel">
        <select class="field-choices" :disabled="disabled" @change="pick" @wheel="onWheel">
          <option value="" hidden></option>
          <option v-for="it in items" :key="it.key" :value="it.key">{{ it.text }}</option>
        </select>
      </span>`,
  };

  // ---- 整数の欄（Qt の単位付きの QSpinBox と同じ: 単位は欄の中に「3 回」の形で出す。幅は整数の幅: J §4.11）
  // 打つのは数だけでよい（単位の字はあってもなくてもよい・全角の数字も読む）。確定は Enter・欄を離れる。Esc は打つ前に
  // 戻す。↑↓・ホイール（スクロールする場所ではフォーカスがあるときだけ）は 1 ずつ、下限・上限で止める。数でない・範囲の
  // 外の値は送らずに打つ前の値に戻す（Qt の QSpinBox が受け付けない文字を入れさせず、確定で直前の値に戻すのと同じ）
  function parseIntIn(text, suffix, lo, hi) {
    let t = String(text == null ? "" : text).normalize("NFKC").trim();
    const suf = String(suffix || "").trim();
    if (suf) t = t.split(suf).join("").trim();
    if (!/^[+-]?\d+$/.test(t)) return null;
    const v = Number(t);
    if ((lo != null && v < lo) || (hi != null && v > hi)) return null;
    return v;
  }
  function stepInt(value, dir, lo, hi) {
    let v = (value == null ? (lo != null ? lo : 0) : value) + dir;
    if (lo != null) v = Math.max(v, lo);
    if (hi != null) v = Math.min(v, hi);
    return v;
  }
  const IntField = {
    name: "int-field",
    directives: { steady: Steady },
    props: {
      value: { type: Number, default: null },
      min: { type: Number, default: null },
      max: { type: Number, default: null },
      suffix: { type: String, default: "" },  // 単位（前の空白も含めて Qt の setSuffix と同じ文字: 「 回」）
      disabled: { type: Boolean, default: false },
      title: String,
    },
    emits: ["commit"],
    data() { return { editing: false, draft: "" }; },
    computed: {
      shown() { return this.value == null ? "" : `${this.value}${this.suffix}`; },
    },
    methods: {
      onInput(e) { this.editing = true; this.draft = e.target.value; },
      send(v) {
        steadyEcho(this.$refs.input);
        if (v === this.value) { this.$refs.input.value = this.shown; return; }
        this.$emit("commit", v);
      },
      commit() {
        if (!this.editing) return;
        this.editing = false;
        const v = parseIntIn(this.draft, this.suffix, this.min, this.max);
        this.draft = this.shown;
        if (v === null) { this.$refs.input.value = this.shown; return; }
        this.send(v);
      },
      onKey(e) {
        if (e.key === "Enter") { e.preventDefault(); this.commit(); return; }
        if (e.key === "Escape") { this.editing = false; this.draft = this.shown; e.target.value = this.shown; return; }
        if (e.key === "ArrowUp" || e.key === "ArrowDown") {
          e.preventDefault();
          this.step(e.key === "ArrowUp" ? 1 : -1);
        }
      },
      onWheel(e) {
        if (this.disabled || wheelIgnored(this.$el)) return;
        e.preventDefault();
        this.step(e.deltaY < 0 ? 1 : -1);
      },
      // 打ちかけの値があればそれから刻む（Qt の QSpinBox は打った値がすぐ値になり、↑↓はそこから 1 ずつ）。範囲の外を
      // 打っていたら範囲の中へ寄せてから刻む。読めなければ確定済みの値から
      step(dir) {
        let base = this.value;
        if (this.editing) {
          const typed = parseIntIn(this.draft, this.suffix, null, null);
          if (typed !== null) {
            base = typed;
            if (this.min != null) base = Math.max(base, this.min);
            if (this.max != null) base = Math.min(base, this.max);
          }
        }
        this.editing = false;
        this.send(stepInt(base, dir, this.min, this.max));
      },
    },
    template: `
      <input ref="input" class="w-int int-input" v-steady="editing ? draft : shown" :disabled="disabled" :title="title"
             spellcheck="false" @input="onInput" @keydown="onKey" @blur="commit" @wheel="onWheel">`,
  };

  // ---- dB の打てるドロップダウン（G §3.6。REF レベルの欄（J §4.1）と同じ動き。Qt の ui_qt/fields.py の
  // DbChoiceField と同じ決まり）: 一覧から選ぶ・数を打つ・欄の上のホイールで 1dB・↑↓ はカーソルの桁。
  // 範囲の外の値は欄の下に範囲を知らせて元の値に戻す（↑↓・ホイールは端で止める）。値は小数 decimals 桁に丸める
  const BANDWIDTH_CHOICES = [3, 6, 10, 20, 30, 40, 60];
  const FLOOR_CHOICES = [3, 6, 10, 15, 20, 30];
  const REF_CHOICES = [];
  for (let v = 20; v >= -120; v -= 10) REF_CHOICES.push(v);
  // 打てる範囲（N dB 帯域幅・2つ目の N／しきい値／ピークの一覧の「床から」。Qt の fields.py の *_RANGE と同じ）
  const DB_RANGES = { bw: [0.1, 200], th: [-300, 300], floor: [0, 100] };
  // 小数 decimals 桁へ四捨五入（ちょうど半分は大きい方へ。Qt の fields.round_to と同じ）。null は丸めない
  function roundTo(v, decimals) {
    if (decimals == null) return v;
    const k = 10 ** decimals;
    return Math.floor(v * k + 0.5) / k;
  }
  // 小数2桁以上の欄（|Γ|・SWR のしきい値: 設計R §3）はその桁、整数の欄（PlutoSDR の受信の利得: 設計Q §7）は整数で
  // 出す。ほかは 0.1 まで（Qt の fields.db_value_text と同じ）
  function dbText(v, unit, decimals) {
    let t;
    if (decimals === 0) {
      t = Number(v).toFixed(0);
      t = GSUnits.minusSign(t === "-0" ? "0" : t);
    } else {
      t = decimals && decimals > 1 ? GSUnits.minusSign(Number(v).toFixed(decimals)) : GSUnits.levelText(v);
    }
    return `${t}${unit ? " " + unit : ""}`;
  }
  // 一覧の行の文字（「+20 dBm」「−40 dBm」「3 dB」）。signed はレベル（REF・しきい値）で、正の数にも + を付ける
  function dbChoiceText(v, unit, signed) {
    return GSUnits.minusSign(signed && v > 0 ? `+${v}` : `${v}`) + (unit ? ` ${unit}` : "");
  }
  // 範囲の外の値を打ったときの知らせ（Qt の fields.range_note と同じ文）
  function rangeNote(lo, hi, unit) {
    const X = root.GSTexts;
    const t = X ? X.w("db_range_note", { lo: GSUnits.minusSign(String(lo)), hi: GSUnits.minusSign(String(hi)), unit: unit || "" }) : "";
    return t.split(/\s+/).filter((s) => s).join(" ");
  }
  // 打った文字を値にする。{ value } か、受け付けないとき { note }（欄の下に出す文）
  function parseDbIn(text, spec) {
    let v;
    try {
      v = GSUnits.parseDb(spec.unit ? String(text).split(spec.unit).join("") : String(text));
    } catch (e) { v = NaN; }
    if (!Number.isFinite(v)) return { note: root.GSTexts ? root.GSTexts.w("bad_value") : "" };
    v = roundTo(v, spec.decimals);
    if ((spec.lo != null && v < spec.lo) || (spec.hi != null && v > spec.hi)) return { note: rangeNote(spec.lo, spec.hi, spec.unit) };
    return { value: v };
  }
  // ↑↓・ホイールで送った値（端で止める）
  function stepDb(value, exp, dir, spec) {
    let v = roundTo(value + dir * 10 ** exp, spec.decimals);
    if (spec.lo != null) v = Math.max(v, spec.lo);
    if (spec.hi != null) v = Math.min(v, spec.hi);
    return v;
  }

  const DbField = {
    name: "db-field",
    components: { NumField },
    props: {
      value: { type: Number, default: 0 },
      unit: { type: String, default: "dB" },
      choices: { type: Array, default: () => [] }, // 数の並び
      signed: { type: Boolean, default: false },
      lo: { type: Number, default: null },
      hi: { type: Number, default: null },
      decimals: { type: Number, default: 1 },
      label: { type: String, default: "" },
      disabled: { type: Boolean, default: false },
      title: String,
      // 一覧の先頭の数でない行（PlutoSDR の受信の利得の「自動」: 設計Q §7。Qt の DbChoiceField の special と同じ）。
      // 選ぶか同じ文字を打って確定すると special を出す。showSpecial の間は欄にその文字を出す（↑↓・ホイールは value から送る）
      special: { type: String, default: null },
      showSpecial: { type: Boolean, default: false },
    },
    emits: ["commit", "special"],
    data() { return { note: null }; },
    computed: {
      spec() { return { unit: this.unit, lo: this.lo, hi: this.hi, decimals: this.decimals }; },
      text() { return this.special && this.showSpecial ? this.special : dbText(this.value, this.unit, this.decimals); },
      items() {
        const out = this.choices.map((v) => ({ value: v, text: dbChoiceText(v, this.unit, this.signed) }));
        return this.special ? [{ value: "special", text: this.special }, ...out] : out;
      },
    },
    methods: {
      onCommit(t) {
        if (this.special && String(t).trim() === this.special) { this.$emit("special"); return; }
        const r = parseDbIn(t, this.spec);
        if (r.note !== undefined) { this.note = { text: r.note }; return; }  // 欄は元の値に戻る（NumField の確定と同じ）
        this.$emit("commit", r.value);
      },
      onStep(exp, dir) { this.$emit("commit", stepDb(this.value, exp, dir, this.spec)); },
    },
    template: `
      <num-field class="field-sm" :label="label" :text="text" :note="note" :choices="items" :wheel-exp="0"
                 :disabled="disabled" :title="title" @commit="onCommit" @step="onStep" @clear-note="note = null"></num-field>`,
  };

  // ---- 同じ列のドロップダウンの幅（J §4.11・S-02。2026-10-03 ユーザー指摘「箱の長さがそろってなくてガタガタ」）
  // 行を重ねる並び（トレースの表など）の同じ列のドロップダウンは、その列に出うる一番長い選択肢の幅に1つにそろえる。
  // いま出ている選択肢では決めない（行や動作で選択肢が変わっても幅が変わらない）。幅は標準の段のドロップダウンに
  // 全部の選択肢を入れてブラウザに測らせ、CSS の変数 --col-<名前> に置く（style.css が列の幅に使う）
  function selectWidth(texts, doc) {
    const d = doc || document;
    const probe = d.createElement("select");
    probe.style.cssText = "position:absolute;visibility:hidden;left:0;top:0;width:auto;min-width:0;max-width:none;";
    for (const t of texts) {
      const o = d.createElement("option");
      o.textContent = t;
      probe.appendChild(o);
    }
    d.body.appendChild(probe);
    const w = probe.getBoundingClientRect().width;
    probe.remove();
    return Math.ceil(w);
  }
  // cols: { 名前: 幅(px) か [選択肢の文字, ...] }
  function fitColumns(cols, doc) {
    const d = doc || document;
    for (const [name, v] of Object.entries(cols)) {
      const w = Array.isArray(v) ? selectWidth(v, d) : Math.ceil(v);
      d.documentElement.style.setProperty("--col-" + name, w + "px");
    }
  }
  // 文字の幅（px）。字は画面の本文の字の種類で、大きさだけを決める（上段の欄の幅を測る: J §4.11）
  let measureCtx = null;
  function textWidth(text, sizePx, doc) {
    const d = doc || document;
    if (!measureCtx) measureCtx = d.createElement("canvas").getContext("2d");
    measureCtx.font = `${sizePx}px ${getComputedStyle(d.body).fontFamily}`;
    return measureCtx.measureText(String(text)).width;
  }
  // :root の大きさの変数（px の数）
  function rootPx(name, doc) {
    const d = doc || document;
    return parseFloat(getComputedStyle(d.documentElement).getPropertyValue(name)) || 0;
  }

  root.GSFields = {
    NumField, CountField, IntField, DbField, Steady, COUNT_CHOICES, COUNT_MAX, countText, parseCount, stepCount,
    parseIntIn, stepInt,
    BANDWIDTH_CHOICES, FLOOR_CHOICES, REF_CHOICES, DB_RANGES, roundTo, dbText, dbChoiceText, rangeNote, parseDbIn,
    stepDb, wheelIgnored, inScrollPlace, selectWidth, fitColumns, textWidth, rootPx,
  };
})(window);
