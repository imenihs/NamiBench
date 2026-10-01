// キーの割り当て（設計J §4.6）。Qt の ui_qt/keymap.py と同じ決まりを、Web の表（texts の WEB_KEY_DEFAULTS・
// WEB_RESERVED_KEYS・KEY_RESERVED_REASONS・GRAPH_ONLY_KEYS・SHIFT_STEP_KEYS）で持つ。
// - 割り当てはブラウザごと（設計O §1.1）。初期値から変えた分だけを localStorage の "pygigast.keys" に持つ
// - 命令ごとに1つのキー（無しも可）。マーカーを左右へ動かすキーは Shift を付けたキーも同じ操作（10点ずつ）
// - ぶつかるキーは黙って上書きしない（画面が聞く: M-02）。ブラウザが先に取るキーは理由を出して断る（U-08）
// - 別ウィンドウ（J §4.3.2）にも同じ割り当てが効くよう、ほかの窓で変えたら storage の知らせで読み直す
// app.js が読み込んで合わせる。文言はここに書かない。
(function (root) {
  "use strict";

  const X = root.GSTexts;
  const STORE = "pygigast.keys";
  // 画面に出すキーの名前（矢印は記号で出す）
  const KEY_NAMES = { ArrowLeft: "←", ArrowRight: "→", ArrowUp: "↑", ArrowDown: "↓" };
  // Web では使えない命令（Qt だけのもの: 終了はブラウザの窓を閉じる）
  const WEB_SKIP = new Set(["quit"]);
  const MODIFIERS = new Set(["Control", "Shift", "Alt", "Meta", "AltGraph", "OS"]);

  // 押したキーを "Ctrl+Alt+Shift+K" の形にする。文字のキーの Shift は、Ctrl・Alt と一緒のときだけ名前に入れる
  // （Shift だけでは文字が変わるだけなので、同じキーとして扱う）
  function keyOf(e) {
    let k = e.key.length === 1 ? e.key.toUpperCase() : e.key;
    if (e.code && /^Digit\d$/.test(e.code)) k = e.code.slice(5); // Alt+数字で文字が変わる配列でも数字で見る
    if (k === " ") k = "Space";
    const mods = [];
    if (e.ctrlKey || e.metaKey) mods.push("Ctrl");
    if (e.altKey) mods.push("Alt");
    if (e.shiftKey && (e.key.length !== 1 || e.ctrlKey || e.altKey || e.metaKey)) mods.push("Shift");
    return [...mods, k].join("+");
  }
  function isModifierOnly(e) { return MODIFIERS.has(e.key); }
  function keyLabel(k) { return k ? k.split("+").map((p) => KEY_NAMES[p] || p).join("+") : X.get("SETTINGS_TEXTS", "keys_none"); }

  // 割り当てられる命令（メニューの並び。モードはモードの段に。Qt の keymap.commands と同じ順）
  function commands() {
    const out = [];
    for (const [sec, codes] of X.list("MENU_LAYOUT")) {
      if (sec === "mode") for (const m of X.list("MODES")) out.push("mode_" + m[0]);
      for (const c of codes) if (c !== null && !WEB_SKIP.has(c) && !out.includes(c)) out.push(c);
    }
    return out;
  }
  function commandName(code) {
    if (code.startsWith("mode_")) {
      const m = X.list("MODES").find((x) => x[0] === code.slice(5));
      if (m) return m[1] + X.fmt("paren", { text: m[2] });
    }
    return X.get("MENU_ITEMS", code);
  }
  function defaults() { return X.dict("WEB_KEY_DEFAULTS"); }
  function loadOverrides() {
    let raw = {};
    try { raw = JSON.parse(localStorage.getItem(STORE) || "{}"); } catch (e) { raw = {}; }
    const known = new Set(commands());
    const out = {};
    if (raw && typeof raw === "object") for (const [k, v] of Object.entries(raw)) if (known.has(k) && typeof v === "string") out[k] = v;
    return out;
  }
  function saveOverrides(ov) {
    try { localStorage.setItem(STORE, JSON.stringify(ov)); } catch (e) { /* 残せなくてもこの画面では効く */ }
  }
  function effective(ov) {
    const d = defaults();
    const out = {};
    for (const c of commands()) out[c] = Object.prototype.hasOwnProperty.call(ov, c) ? ov[c] : (d[c] || "");
    return out;
  }
  // その命令に付けるキーの並び。マーカーを動かすキーは Shift 付きも足す
  function sequences(code, key) {
    if (!key) return [];
    if (X.list("SHIFT_STEP_KEYS").includes(code) && !key.split("+").includes("Shift")) {
      const parts = key.split("+");
      const base = parts.pop();
      return [key, [...parts, "Shift", base].join("+")];
    }
    return [key];
  }
  function reservedReason(key) {
    const code = X.get("WEB_RESERVED_KEYS", key, null);
    return code ? X.get("KEY_RESERVED_REASONS", code) : null;
  }
  // key を code に付けるとき、同じキー（Shift 付きの分を含む）を使っているほかの命令
  function conflict(km, code, key) {
    const want = new Set(sequences(code, key));
    for (const [other, k] of Object.entries(km)) {
      if (other !== code && sequences(other, k).some((s) => want.has(s))) return other;
    }
    return null;
  }
  // code に key を付けた新しい覚え書き。takeFrom があればその命令のキーを外す（付け替え）
  function assign(ov, code, key, takeFrom) {
    const d = defaults();
    const out = { ...ov };
    const pairs = [[code, key]];
    if (takeFrom) pairs.push([takeFrom, ""]);
    for (const [c, k] of pairs) {
      if ((d[c] || "") === k) delete out[c];
      else out[c] = k;
    }
    return out;
  }
  function changedCount(ov) {
    const d = defaults();
    return Object.entries(ov).filter(([c, k]) => (d[c] || "") !== k).length;
  }

  root.GSKeys = { keyOf, keyLabel, isModifierOnly, commands, commandName, effective, sequences, reservedReason, conflict, assign, changedCount };

  root.GSAppKeys = {
    data() {
      return { keyOverrides: loadOverrides() };
    },
    computed: {
      // 命令 → いまのキー（"" は無し）
      keymap() { void this.textsReady; return effective(this.keyOverrides); },
      // 命令 → 受けるキーの並び（Shift 付きの分を含む）。メニューの項目の横・ツールチップ・キーの処理が同じものを見る
      keySeqs() {
        const out = {};
        for (const [c, k] of Object.entries(this.keymap)) out[c] = sequences(c, k);
        return out;
      },
    },
    methods: {
      // 別ウィンドウ・別のタブで割り当てを変えたら読み直す（同じブラウザの窓は同じ割り当て）
      watchKeyStore() {
        window.addEventListener("storage", (e) => { if (e.key === STORE) this.keyOverrides = loadOverrides(); });
      },
      setKeyOverrides(ov) {
        this.keyOverrides = ov;
        saveOverrides(ov);
      },
      keyLabel,
    },
  };
})(window);
