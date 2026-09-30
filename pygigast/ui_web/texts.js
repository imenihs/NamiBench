// コアが返す理由のコードを、画面の文言にする（U-22）。
// 表（辞書）は ui_qt/texts.py をサーバーが JSON にして渡す（GET /api/v1/texts）ので、Qt と同じ語になる。
// ここにあるのは texts.py の関数（表を組み合わせて1文にするもの）を JavaScript へ移したものだけ。
(function (root) {
  "use strict";

  const U = () => root.GSUnits;
  let T = {};

  function load(dicts) { T = dicts || {}; }
  function get(dict, code, fallback) {
    const d = T[dict] || {};
    if (Object.prototype.hasOwnProperty.call(d, code)) return d[code];
    return fallback === undefined ? code : fallback;
  }
  function has(dict, code) { return Object.prototype.hasOwnProperty.call(T[dict] || {}, code); }
  function dict(name) { return T[name] || {}; }

  function adjustmentText(adj) {
    const field = adj.field || "";
    const v = adj.adopted;
    const freqFields = new Set(T.FREQ_FIELDS || []);
    const value = freqFields.has(field) && Number.isInteger(v) ? U().formatFreq(v) : String(v);
    const reason = get("REASONS", adj.reason || "", adj.reason || "");
    return reason ? `${value} に合わせました（${reason}）` : `${value} に合わせました`;
  }

  function alternativeLabel(alt) {
    const span = alt.stop_hz - alt.start_hz;
    const center = alt.start_hz + Math.floor(span / 2);
    if (alt.rule === "keep_span") return `Span ${U().formatFreq(span)} を保って Center を ${U().formatFreq(center)} にする`;
    return `Center ${U().formatFreq(center)} を保って Span を ${U().formatFreq(span)} にする`;
  }

  function alternativeDoneText(centerHz, spanHz) {
    return `Center ${U().formatFreq(centerHz)}・Span ${U().formatFreq(spanHz)} に置き換えました（この1回だけ）`;
  }

  function ageText(seconds) {
    if (seconds === null || seconds === undefined) return "";
    const s = Math.floor(seconds);
    if (s < 60) return `${s} 秒前`;
    if (s < 3600) return `${Math.floor(s / 60)} 分前`;
    if (s < 86400) return `${Math.floor(s / 3600)} 時間前`;
    return `${Math.floor(s / 86400)} 日前`;
  }

  function rangesText(ranges) {
    return (ranges || []).map(([lo, hi]) => `${U().formatFreq(lo)}〜${U().formatFreq(hi)}`).join("・");
  }

  function traceResetText(detail) {
    const c = (detail || {}).cause || "";
    const cause = get("RESET_CAUSES", c, c);
    return cause ? `条件変更（${cause}）でリセット` : get("TRACE_NOTICES", "reset_by_condition");
  }

  function storageErrorText(err) {
    const code = (err || {}).code || "";
    return get("STORAGE_ERRORS", code, null) || get("ERRORS", code, null) || code;
  }

  function taskLabel(t) {
    const kind = t.task || t.kind || "";
    let text = get("TASK_NAMES", kind, kind);
    if (t.total) text += `  ${t.done || 0}/${t.total}`;
    text += `  経過 ${Math.floor(t.elapsed_s || 0)} 秒`;
    if (t.cancelling) text += "（やめています）";
    return text;
  }

  function noticeText(n) {
    const code = n.code || "";
    const d = n.detail || {};
    if (code === "trace_reset") {
      return `トレース ${(d.traces || []).join("・")}: ${traceResetText(d)}`;
    }
    if (code === "trace") {
      return `トレース ${d.trace}: ` + (d.codes || []).map((c) => get("TRACE_NOTICES", c, c)).join("・");
    }
    if (has("TRACE_NOTICES", code)) return (d.trace ? `トレース ${d.trace}: ` : "") + get("TRACE_NOTICES", code);
    if (code === "task_done") {
      const r = d.result || {};
      return get("TASK_DONE", d.task, d.task) + (r.path ? `: ${r.path}` : "");
    }
    if (code === "task_cancelled") return get("TASK_CANCELLED", d.task, d.task);
    if (code === "task_failed") return `${get("TASK_NAMES", d.task, d.task)}: ${storageErrorText(d.error)}`;
    if (code === "normalization") return normalizationText({ used: true, ...d }) || code;
    let text = get("NOTICES", code, code);
    if (code === "sweep_discarded") {
      const r = d.reason || "";
      text += `（${get("DISCARD_REASONS", r, r)}）`;
    }
    return text;
  }

  function errorText(e) {
    const code = e && e.code;
    if (!code) return String(e);
    let text = get("ERRORS", code, null) || get("STORAGE_ERRORS", code, null) || code;
    const d = e.detail || {};
    if (code === "legacy_unreadable" && d.line !== undefined) text += `（${d.line} 行目の ${d.field} 番目の欄）`;
    return text;
  }

  function normalizationText(n) {
    if (!n || !n.used) return "";
    const sel = n.selected;
    if (!n.applied) {
      const reason = get("NORM_REASONS", n.reason || "", n.reason || "");
      return reason ? `基準なし（受けた電力）: ${reason}` : "基準なし（受けた電力）";
    }
    const parts = [sel ? `基準: ${sel.name}` : "基準"];
    const acc = get("ACCURACY", n.accuracy || "", "");
    if (acc) parts.push(acc);
    const age = ageText(n.age_s);
    if (age) parts.push(age);
    let text = parts.join("・");
    if (n.power_cycled) text += "・⚠ 電源の入れ直し前の基準（取り直しを勧めます）";
    if (n.out_of_range_hz && n.out_of_range_hz.length) text += `・基準の範囲外: ${rangesText(n.out_of_range_hz)}`;
    return text;
  }

  // 凡例の1項目（texts.legend_parts と png_export.legend_text を合わせたもの）
  function legendText(t) {
    const action = t.action;
    const parts = [String(t.trace != null ? t.trace : t.number), get("TRACE_ACTIONS", action, action || "")];
    if (action === "average" && t.average_type) parts.push(get("AVERAGE_TYPES", t.average_type));
    if (["average", "max_hold", "min_hold"].includes(action) && t.count != null) {
      parts.push(t.count_target ? `${t.count}/${t.count_target}` : String(t.count));
    }
    if (t.axis_differs) parts.push("（いまの軸と違う）");
    return parts.filter((p) => p).join(" ");
  }

  root.GSTexts = {
    load, get, has, dict, adjustmentText, alternativeLabel, alternativeDoneText, noticeText, errorText,
    normalizationText, taskLabel, traceResetText, storageErrorText, legendText, ageText,
  };
})(window);
