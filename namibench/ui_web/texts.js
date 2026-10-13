// 画面の文言（U-22・J §5: Qt と同じ語）。文言の正本は ui_qt/texts.py（と texts_ui.py）の表だけ。
// サーバーが表を JSON にして渡し（GET /api/v1/texts）、状態から作る文（状態バー・注意・知らせ・失敗・マーカー表）は
// Qt と同じ関数でサーバーが作って送る（api/web/view.py）。ここは表を引くことと、文の型に差し込むことだけで、文を持たない。
(function (root) {
  "use strict";

  let T = {};

  function load(dicts) { T = dicts || {}; }
  function get(dict, code, fallback) {
    const d = T[dict] || {};
    if (Object.prototype.hasOwnProperty.call(d, code)) return d[code];
    return fallback === undefined ? code : fallback;
  }
  function has(dict, code) { return Object.prototype.hasOwnProperty.call(T[dict] || {}, code); }
  function dict(name) { return T[name] || {}; }
  function list(name) { return T[name] || []; }

  // TEMPLATES の {名前} を差し込む（texts.tpl と同じ置き換え。書式の指定は使わない）
  function fmtText(text, kw) {
    let out = String(text);
    for (const [k, v] of Object.entries(kw || {})) out = out.split("{" + k + "}").join(String(v));
    return out;
  }
  function fmt(key, kw) { return fmtText(get("TEMPLATES", key, key), kw); }
  // 部品の名前・ボタンの語（texts_labels.py の LABELS）。{名前} を差し込める
  function w(key, kw) { return fmtText(get("LABELS", key, key), kw); }

  root.GSTexts = { load, get, has, dict, list, fmt, fmtText, w };
  if (typeof module !== "undefined") module.exports = root.GSTexts;
})(typeof window !== "undefined" ? window : globalThis);
