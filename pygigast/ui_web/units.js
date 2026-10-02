// 単位付きの数値の読み書き（設計J §3。core/units.py と同じ規則を JavaScript へ移したもの）。
// 周波数は Hz の整数。float を通さない（設計A §2.1）ので、10 進の文字列から BigInt で組み立てる。
// ブラウザでは window.GSUnits、Node（試験）では module.exports で使う。
(function (root) {
  "use strict";

  const PREFIX = { "": 0, k: 3, K: 3, m: 6, M: 6, g: 9, G: 9 };
  const FREQ_RE = /^\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+))\s*([kKmMgG]?)\s*(?:[hH][zZ])?\s*$/;

  class ParseError extends Error {}

  // 10 進の文字列 num × 10^exp を、偶数丸めで整数（BigInt）にする
  function scaleRound(num, exp) {
    let sign = 1n;
    let s = num;
    if (s[0] === "+" || s[0] === "-") {
      if (s[0] === "-") sign = -1n;
      s = s.slice(1);
    }
    const dot = s.indexOf(".");
    const intPart = dot < 0 ? s : s.slice(0, dot);
    const frac = dot < 0 ? "" : s.slice(dot + 1);
    const digits = BigInt((intPart + frac) || "0");
    const scale = exp - frac.length;
    if (scale >= 0) return sign * digits * 10n ** BigInt(scale);
    const div = 10n ** BigInt(-scale);
    let q = digits / div;
    const r = digits % div;
    const twice = r * 2n;
    if (twice > div || (twice === div && q % 2n === 1n)) q += 1n;
    return sign * q;
  }

  // 周波数の文字列を Hz の整数にする。単位を省いたら defaultExp（既定は MHz）。
  // 'm' は周波数の欄では mHz ではなく MHz と読む（core/units.py と同じ）
  function parseFreqHz(text, defaultExp = 6) {
    const m = FREQ_RE.exec(String(text));
    if (!m) throw new ParseError(text);
    const exp = m[2] ? PREFIX[m[2]] : defaultExp;
    const v = scaleRound(m[1], exp);
    const n = Number(v);
    if (!Number.isSafeInteger(n)) throw new ParseError(text);
    return n;
  }

  // BigInt の整数を 10^-unitExp 倍した 10 進の文字列（端の 0 を落とす）
  function decimalString(hz, unitExp) {
    let v = BigInt(hz);
    const neg = v < 0n;
    if (neg) v = -v;
    let s = v.toString();
    if (unitExp > 0) {
      s = s.padStart(unitExp + 1, "0");
      const whole = s.slice(0, s.length - unitExp);
      let frac = s.slice(s.length - unitExp).replace(/0+$/, "");
      s = frac ? whole + "." + frac : whole;
    }
    return (neg && s !== "0" ? "-" : "") + s;
  }

  // Hz の整数を、桁を落とさずに単位付きの文字列にする（例 1234567890 → '1234.56789 MHz'）
  function formatFreq(hz, unitExp) {
    if (unitExp === undefined || unitExp === null) {
      const a = Math.abs(hz);
      unitExp = a >= 1e10 ? 9 : a >= 1e6 ? 6 : a >= 1e3 ? 3 : 0;
    }
    const unit = { 0: "Hz", 3: "kHz", 6: "MHz", 9: "GHz" }[unitExp];
    return decimalString(hz, unitExp) + " " + unit;
  }

  // 周波数の欄の表示: MHz で小数3桁以上（Qt の FreqField と同じ形）
  function freqFieldText(hz) {
    let s = decimalString(hz, 6);
    if (s.indexOf(".") < 0) s += ".000";
    else {
      const [w, f] = s.split(".");
      s = w + "." + f.padEnd(3, "0");
    }
    return s + " MHz";
  }

  // 表示の負号を見間違えない字形「−」（U+2212）にする（J §4.1。Python の units.minus_sign と同じ）
  function minusSign(text) { return String(text).replace(/-/g, "−"); }

  // レベル（dB の値）の表示: 0.1 dB まで・負号は「−」。REF の欄と縦軸の目盛で同じ形にする
  // （Python の units.level_text と同じ。単位を換えた REF の端数 116.99 などを欄の 117.0 とそろえる）
  function levelText(v) {
    const s = Number(v).toFixed(1);
    return minusSign(s === "-0.0" ? "0.0" : s);
  }

  // dB・dBm の欄。単位の文字は読み飛ばす。マイナスは「-」と「−」の両方を読む
  function parseDb(text) {
    let t = String(text).trim().replace(/−/g, "-");
    for (const suf of ["dBm", "dB", "dbm", "db"]) {
      if (t.endsWith(suf)) {
        t = t.slice(0, -suf.length).trim();
        break;
      }
    }
    if (!/^[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?$/.test(t)) throw new ParseError(text);
    return parseFloat(t);
  }

  // カーソルの左の桁が、小数点から見て 10 の何乗の位か。数字でなければ null（Qt の _digit_exponent と同じ）
  function digitExponent(text, pos) {
    let numEnd = 0;
    while (numEnd < text.length && /[0-9+\-.−]/.test(text[numEnd])) numEnd++;
    let idx = Math.min(pos, numEnd) - 1;
    if (idx < 0 || !/[0-9]/.test(text[idx])) {
      // 先頭にいるときは最上位の桁
      idx = -1;
      for (let i = 0; i < numEnd; i++) if (/[0-9]/.test(text[i])) { idx = i; break; }
      if (idx < 0) return null;
    }
    const number = text.slice(0, numEnd);
    let dot = number.indexOf(".");
    if (dot < 0) dot = number.length;
    return idx < dot ? dot - idx - 1 : dot - idx;
  }

  // 縦軸のドラッグの REF（core/units.py の drag_ref_level と同じ: REF が1目盛（dB/div）の倍数に近ければ吸い付く・
  // 量は刻まない・0.1 dB に丸める）
  const REF_DRAG_SNAP_DIV = 0.15;
  function dragRefLevel(ref0, frac, dbPerDiv, divisions = 10) {
    let ref = ref0 + frac * divisions * dbPerDiv;
    const k = pyRound(ref / dbPerDiv);
    if (Math.abs(ref - k * dbPerDiv) <= REF_DRAG_SNAP_DIV * dbPerDiv) ref = k * dbPerDiv;
    return pyRound(ref * 10) / 10 + 0;
  }
  // Python の round と同じ（.5 は偶数へ）。Math.round と違い -2.5 → -2・2.5 → 2
  function pyRound(x) {
    const f = Math.floor(x);
    const d = x - f;
    if (d > 0.5) return f + 1;
    if (d < 0.5) return f;
    return f % 2 === 0 ? f : f + 1;
  }

  const api = { ParseError, parseFreqHz, formatFreq, freqFieldText, parseDb, digitExponent, minusSign, levelText,
                dragRefLevel };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.GSUnits = api;
})(typeof window !== "undefined" ? window : globalThis);
