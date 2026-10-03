"""Web の画面へ送るものに、画面の文言を添える（J §5: Qt と Web は同じ語。同じ処理を2回書かない）。

文言を作る関数（ui_qt/texts.py・screen_model.py・marker_rows.py。どれも Qt を import しない）は Qt の画面と
同じものを使い、結果を元の中身に足して送る。コードの欄（code・reason など）は消さずに残すので、
文言を使わない利用者には今までどおりの形に見える（足すのは ui・view・text・texts の欄だけ）。

Web の画面（ui_web/）は、ここで足した文と /api/v1/texts の表を映すだけで、文を組み立てない。
"""
from __future__ import annotations

from typing import Any, Optional

from ... import __version__
from ...core import report_layout as rl
from ...ui_qt import cal_view, marker_rows, peak_view, screen_model, source_view, texts


def default_unit(st: Optional[dict]) -> str:
    """マーカー表で単位を持たない行（Y カーソル）に使う単位。Qt の display_unit と同じ順で決める。"""
    st = st or {}
    amp = st.get("amplitude") or {}
    # 電力の単位は表示の設定で dBm・dBμV・dBmV になる（設計O）。Qt の display_unit と同じく、いまの単位の名前だけ使う
    if amp.get("unit") == "dB" or (amp.get("unit") and amp.get("unit") == (st.get("view") or {}).get("level_unit_label",
                                                                                                    "dBm")):
        return amp["unit"]
    for t in st.get("traces") or []:
        if t.get("visible") and t.get("unit"):
            return t["unit"]
    return "dBm"


def state_view(st: dict) -> dict:
    """状態に `ui`（状態バー・条件の注意・RBW の選択肢・使えない理由・受信の欄・凡例）を足す。"""
    ui = screen_model.ui_view(st)
    ui["legend"] = {str(t.get("number")): rl.legend_text(texts.legend_parts(t)) for t in st.get("traces") or []}
    # 参照トレースの凡例の文字（Qt のグラフの凡例・PNG と同じ: 名前・番号・日時・機器）。id は "F1.1" の形
    ui["file_legend"] = {str(tr.get("id")): texts.file_trace_label(f, str(tr.get("id")))
                         for f in st.get("files") or [] for tr in f.get("traces") or []}
    ui["unit"] = default_unit(st)
    # ピークの一覧の欄（P §9.7）。山／谷の選択はブラウザが持つので両方（Qt と同じ peak_view.list_view）
    ui["peaks"] = peak_view.list_views(st)
    # 校正の画面（設計K）。開いているときだけ（Qt の cal_dialog と同じ cal_view.view）
    ui["cal"] = cal_view.view(st) if st.get("calibration_work") else None
    out = {**st, "ui": ui}
    if isinstance(st.get("markers"), dict):
        out["markers"] = markers_view(st["markers"], ui["unit"], texts.freq_offset_of(st))
    return out


def markers_view(snap: dict, unit: str = "dBm", freq_offset_hz: int = 0) -> dict:
    """マーカー表に `rows`（表の行。Qt の表・コピー・PNG と同じ行）と `cells`（表の文字）を足す。

    機能の行の `line` は、グラフに出す機能の線（G §3.6: 横線のレベルと縦線の周波数。Qt のグラフと同じ行から作る）。
    """
    rows = marker_rows.rows_from_snapshot(snap)
    cells = marker_rows.format_rows(rows, unit, freq_offset_hz)
    out = []
    cols = (marker_rows.COL_FREQ, marker_rows.COL_VALUE, marker_rows.COL_DFREQ, marker_rows.COL_DVALUE)
    for r, c in zip(rows, cells):
        rm = marker_rows.remove_command(r, snap)
        edits = {str(i): e for i in cols if (e := marker_rows.edit_spec(r, i, rows, snap)) is not None}
        # 行だけを消す命令と、ダブルクリックで直せるセルの決まり（G §3.7。Qt と同じ marker_rows の決まり）
        # 行の「操作」の項目（Qt の行のドロップダウンと同じ marker_rows.row_ops。None は区切り線）
        out.append({"id": r["id"], "parent": r.get("parent"), "kind": r.get("kind"), "child": bool(r.get("child")),
                    "ref": r.get("ref") if not r.get("child") else None, "cells": c, "line": r.get("line"),
                    "remove": {"cmd": rm[0], "args": rm[1]} if rm else None, "edits": edits,
                    "ops": marker_rows.row_ops(r, snap)})
    return {**snap, "rows": out, "columns": list(marker_rows.COLUMNS), "right": list(marker_rows.RIGHT)}


def devices_view(p: dict) -> dict:
    """機器の一覧（イベント devices・命令 list_devices の戻り値）に、行の文と空のときの案内を足す（J §4.7）。"""
    return {**p, **texts.devices_view(p.get("devices") or [])}


def notice_view(n: dict, state: Optional[dict]) -> dict:
    return {**n, "view": texts.notice_view(n, state)}


def progress_view(p: dict) -> dict:
    if p.get("kind") == "waiting_sweep_end":
        return {**p, "text": texts.waiting_text(p.get("remaining_s") or 0.0)}
    if p.get("kind") == "task":
        return {**p, "text": texts.task_label(p)}
    return p


def error_view(err: dict) -> dict:
    """失敗の形（A §4.3）の error に `text` を足す。"""
    e = err.get("error") or {}

    class _E:
        code = e.get("code")
        detail = e.get("detail") or {}

    text = texts.error_text(_E) if e.get("code") else ""
    out = {**e, "text": text}
    if e.get("code") in texts.CONNECT_NEXT_STEPS:
        # 繋げなかったときの理由と次の手（J §4.7 の 5。名前は画面が差し込む: TEMPLATES の device_connect_failed）
        out["text_next"] = texts.connect_failed_text("", text, e.get("code"))
    return {**err, "error": out}


def result_view(command: str, args: dict, result: Any, freq_offset_hz: int = 0) -> Any:
    """命令の戻り値に、画面がそのまま出す文を `texts` として足す（調整の知らせ・反対の規則・校正の結果）。

    freq_offset_hz は表示の周波数オフセット（設計O）。調整の知らせの周波数を欄と同じ換算後の値で出す。
    プリセットの一覧には行の文（名前と中身の要約。Qt の窓と同じ screen_model.preset_rows）を、保存した配色の一覧にも
    行の文（screen_model.palette_rows）を `rows` で足す。
    """
    if not isinstance(result, dict):
        return result
    if command == "preset_list" and "presets" in result:
        return {**result, "rows": screen_model.preset_rows(result.get("presets") or [])}
    if command == "palette_list" and "palettes" in result:
        # 保存した配色の一覧の行（名前・読みやすさの知らせ。Qt の設定の画面と同じ screen_model.palette_rows: J §4.4.1）
        return {**result, "rows": screen_model.palette_rows(result)}
    t: dict = {}
    if result.get("adjustments"):
        t["adjustments"] = [{"field": a.get("field"), "text": texts.adjustment_text(a, freq_offset_hz),
                             "field_name": texts.FIELD_NAMES.get(a.get("field"), a.get("field")),
                             "rule": a.get("reason") in texts.RULE_REASONS} for a in result["adjustments"]]
    if result.get("alternative"):
        t["alternative_label"] = texts.alternative_label(result["alternative"])
    if args.get("one_shot") and isinstance(result.get("adopted"), dict) and "center_hz" in result["adopted"]:
        a = result["adopted"]
        t["alternative_done"] = texts.alternative_done_text(a["center_hz"], a["span_hz"])
    if command == "list_devices" and "devices" in result:
        return devices_view(result)
    if command == "set_step_sweep" and result.get("adjusted"):
        # ステップ掃引の設定を直した知らせ（欄ごと。Qt の step_panel と同じ source_view.adjusted_notes: 設計L §1.3）
        t["step_notes"] = source_view.adjusted_notes(result)
    if command == "peak_list" and "peaks" in result:
        # 見つけた数の行・差の列の名前・行の文字（Qt のピークの一覧と同じ peak_view.result_view: P §9.7）
        t["peak_list"] = peak_view.result_view(result, freq_offset_hz)
    if command == "palette_import" and "name" in result:
        # 取り込んだ知らせ（読みにくい組があればその場で言う: J §4.4.1。Qt と同じ文）
        t["done"] = screen_model.palette_import_text(result)
    if command == "rtlsdr_calibrate_ppm" and "ppm" in result:
        t["done"] = screen_model.ppm_done_text(result)
    elif command == "rtlsdr_calibrate_level" and "level_offset_db" in result:
        t["done"] = screen_model.level_done_text(result)
    return {**result, "texts": t} if t else result


def png_layout(st: dict, req: dict) -> dict:
    """Web の PNG の配置（I §3.1: 配置はコアの report_layout に1つ。Qt の png_export も同じ関数で描く）。

    req: {"width", "height", "note", "ref_level", "db_per_div", "unit", "files": [{"label", "color"}]}
    Qt と同じく、帯の中身は状態から（report_layout.build_data）、凡例は見えているトレース、
    マーカー表は Qt と同じ行、注意は上段の注意と同じ文。
    """
    sw = st.get("sweep") or {}
    # 凡例の線の見本はトレースの線（設計F §9）。参照トレースは画面と同じ参照トレースの線
    traces = [{**texts.legend_parts(t), "color": f"trace{t['number']}", "visible": True, "line": t.get("line")}
              for t in st.get("traces") or [] if t.get("visible") and t.get("has_values")]
    traces += [{"number": "", "action": "", "count": None, "label": str(f.get("label", "")),
                "color": f.get("color") or "trace1", "visible": True, "line": rl.REFERENCE_LINE}
               for f in req.get("files") or []]
    rows = marker_rows.png_rows(marker_rows.rows_from_snapshot(st.get("markers") or {}),
                                lines=marker_rows.function_lines_on(st))
    data = rl.build_data(st, ref_level=float(req.get("ref_level", 0.0)), db_per_div=float(req.get("db_per_div", 10.0)),
                         unit=str(req.get("unit") or "dBm"),
                         mode_name=texts.MODE_NAMES.get(sw.get("mode"), sw.get("mode")), traces=traces,
                         markers=rows, warnings=screen_model.png_warnings(st), app_version=__version__,
                         norm_reasons=texts.NORM_REASONS)
    size = (int(req.get("width", 1600)), int(req.get("height", 1000)))
    return rl.layout(data, size, str(req.get("note") or ""), __version__)
