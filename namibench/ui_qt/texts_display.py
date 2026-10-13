"""表示の語（設計P: ウォーターフォール・密度・記録の再生・バンドプランとブックマーク）。

texts.py が `from .texts_display import *` で取り込み、Web へは texts の表として一緒に配る（GET /api/v1/texts）。
Qt と Web はどちらもここの語で部品を作る（J §5: 同じ語）。名前は何であるかだけ（U-24）。Qt を import しない。
"""
from __future__ import annotations

import datetime
from typing import Optional

from .texts_labels import LABELS
from .texts_settings import DETACH_PARTS as _DETACH_PARTS
from .texts_ui import MENU_ITEMS, MENU_NOT_YET, NOT_YET, REASON_TEXTS
from .texts_ui import MENU_LAYOUT as _MENU_LAYOUT

DISPLAY_TEXTS = {
    # ---- 表示のタブ
    "wf_group": "ウォーターフォール・密度",
    "waterfall": "ウォーターフォール",
    "density": "密度",
    "wf_trace": "トレース",
    "wf_color": "色の範囲",
    "auto": "自動",
    "lower": "下限",
    "upper": "上限",
    "colormap": "カラーマップ",
    "wf_rows": "保持する行数",
    "rows_item": "{n} 行",
    "density_half": "半分になる回数",
    "times_item": "{n} 回",
    "bandplan_group": "バンドプラン・ブックマーク",
    "show_bookmarks": "ブックマークを表示",
    "bandplan_load": "読み込む…",
    "bandplan_save": "書き出す…",
    "bandplan_load_title": "バンドプランを読み込む",
    "bandplan_save_title": "バンドプランを書き出す",
    "bands_count": "帯 {n} 個",
    "bandplan_file": "{name}",
    "bookmark_name": "名前",
    "to_center": "Center に",
    "no_bookmarks": "ブックマークはありません",
    "filter_json": "バンドプラン (*.json);;すべて (*)",
    # ---- グラフの右クリック
    "ctx_bookmark": "ここをブックマークに…",
    "bookmark_add_title": "ブックマークを追加",
    # ---- ウォーターフォールの上の行・再生の帯
    "latest": "最新へ",
    "whole": "全体",
    "play": "再生",
    "pause": "一時停止",
    "speed": "速さ",
    "back_to_live": "ライブに戻る",
    "extract": "記録を切り出す…",
    "export_record": "記録を CSV に書き出す…",
    "extract_title": "記録を切り出す",
    "export_record_title": "記録を CSV に書き出す",
    "resolution": "保存時の分解能 {step}",
    "position": "{time}　{i} / {n}",
    # ---- 読み値・境目・密度の秒
    "reading_merged": "{n} 回の掃引の最大",
    "boundary": "{start}〜{stop}・RBW {rbw}・{mode}",
    "boundary_zero": "ゼロスパン {start}・RBW {rbw}・{mode}",
    "boundary_ref": "基準「{name}」",
    "density_seconds": "約 {t}分",
    "seconds": "{s} 秒",
    "minutes": "{m} 分 {s} 秒",
    "hours": "{h} 時間 {m} 分",
    # ---- 状態バー（ライブか再生か: P §5）
    "status_live": "ライブ",
    "status_playback": "再生 {name}　{time}",
    # ---- 過去のトレース（行をクリックした掃引: P §3）の名前
    "past_trace": "過去の掃引 {time}",
    "past_trace_merged": "過去の掃引 {time}（{n} 回の最大）",
}

# カラーマップの名前（viridis などは固有の名前なので訳さない）
COLORMAP_NAMES = {"viridis": "viridis", "cividis": "cividis", "magma": "magma", "inferno": "inferno", "gray": "灰色"}

# 再生の速さ（コアの PLAYBACK_SPEEDS と同じ並び。キーは JSON で文字になるので文字で持つ）
PLAYBACK_SPEED_NAMES = {"1": "等倍", "2": "2 倍", "5": "5 倍", "10": "10 倍", "30": "30 倍"}

# 使えない理由（U-08）
REASON_TEXTS.update({
    "layout_waterfall": "表示の組み合わせで「＋ウォーターフォール」を選ぶと使えます",
    "layout_density": "表示の組み合わせで「＋密度」を選ぶと使えます",
    "wf_following": "最新の掃引を出しています",
    "wf_whole": "記録の全体を出しています",
    "wf_auto_on": "色の範囲を自動で追っています",
    "playback_active": "再生中です（ライブに戻ると使えます）",
    # ゼロスパン（横軸が時間: 設計N §1）の間は、周波数の横軸のウォーターフォール・密度を出さない
    "layout_zero_span": "ゼロスパン（横軸が時間）では使えません",
    # |Γ|・SWR の行（設計R §3）は縦軸と同じ範囲で色を付ける（自動・下限・上限は dB の行のもの）
    "wf_linear_axis": "|Γ|・SWR は縦軸と同じ範囲で色を付けます",
})

# 失敗の文（texts.ERRORS へ足す）
DISPLAY_ERRORS = {
    "playback_active": "再生中です（ライブに戻ると使えます）",
    "not_playing": "再生していません",
    "playback_empty": "記録に掃引がありません",
    "playback_one_at_a_time": "記録の再生は1つずつです",
    "no_waterfall_row": "その時刻の行がありません",
    "wf_range_order": "下限は上限より 1 dB 以上低くしてください",
    "bandplan_not_format": "バンドプランのファイルではありません",
    "bandplan_not_json": "バンドプランのファイルを JSON として読めません",
    "bandplan_unreadable": "バンドプランのファイルを読めません",
    "bandplan_unsupported_version": "このバンドプランの版は読めません",
    "bandplan_bad_name": "名前が無いか長すぎます",
    "bandplan_bad_freq": "周波数は Hz の整数で書いてください",
    "bandplan_band_order": "帯の開始は終了より低くしてください",
    "bandplan_bad_color": "帯の色は #rrggbb の形で書いてください",
    "bandplan_too_many": "帯とブックマークが多すぎます",
    "unknown_bookmark": "そのブックマークはもうありません",
    "playback_not_saved_file": "記録中の作業ファイルからは切り出せません（まとめた保存ファイルを開いてください）",
    "playback_zero_span": "ゼロスパンの区間がある記録は再生できません",
    "playback_nf": "NF の区間がある記録は再生できません",
    "playback_listening": "音を聞いている間は記録を再生できません（聞くのをやめてから開いてください）",
    "playback_zero_span_live": "ゼロスパンの間は記録を再生できません（Span を 0 より大きくしてから開いてください）",
}

# 長い処理（texts の TASK_* へ足す）: 再生している記録の切り出し（H §3.1）・CSV への書き出し（H §5）
DISPLAY_TASKS = {
    "names": {"playback_extract": "切り出しています", "playback_export_csv": "記録を書き出しています"},
    "done": {"playback_extract": "切り出しました", "playback_export_csv": "記録を書き出しました"},
    "failed": {"playback_extract": "切り出せませんでした", "playback_export_csv": "記録を書き出せませんでした"},
    "cancelled": {"playback_extract": "切り出しをやめました（書きかけのファイルは消しました）",
                  "playback_export_csv": "書き出しをやめました（書きかけのファイルは消しました）"},
}

# ノーマライズの理由（texts.NORM_REASONS へ足す）
DISPLAY_NORM_REASONS = {
    "playback_reference_missing": "記録した基準が見つかりません",
}

# 一度きりの知らせ（texts.NOTICES へ足す。文を組み立てるものは display_notice_text）
DISPLAY_NOTICES = {
    "playback_started": "記録を再生します",
    "playback_closed": "ライブに戻りました",
    "playback_end": "記録の終わりまで再生しました",
    "playback_read_failed": "記録を読めませんでした",
    "bandplan_unreadable": "バンドプランのファイルを読めないので、空で始めました",
}
_STARTED_PARTS = {
    "name": "「{name}」・{n} 回の掃引",
    "live_stopped": "ライブの掃引を止めました",
    "layout_changed": "ウォーターフォールを出しました",
    "reference_missing": "記録した基準「{name}」が見つからないので、受けた電力で出します",
    "segment_unreadable": "換算できない区間があります",
}

# 蓄積を捨てた理由（texts.RESET_CAUSES へ足す）
DISPLAY_RESET_CAUSES = {"playback": "再生"}

# 部分の名前（別ウィンドウ: J §4.3.2）と、ウォーターフォールの縦軸（隠れた操作を作らない: U-12。軸のツールチップ）
LABELS.update({
    "part_waterfall": "ウォーターフォール",
    "part_density": "密度表示",
    "axis_time": "時刻",
    "axis_time_tip": "ドラッグ: その時間の範囲を出す\nホイール: 前後へずらす",
    "waterfall_tip": "クリック: その時刻の掃引をスペクトルに出す\nShift を押しながら横にドラッグ: その範囲を Start/Stop に",
})

# 別ウィンドウで開ける部分にウォーターフォールと密度表示を足す（グラフの後ろ。J §4.3.2: 設計P を作るときに足す）
DETACH_PARTS = _DETACH_PARTS[:1] + ("waterfall", "density") + _DETACH_PARTS[1:]

# メニュー「ファイル」に、再生している記録の切り出し（H §3.1）と CSV への書き出し（H §5）を足す（「CSV に書き出す…」の後ろ）。
# 範囲はウォーターフォールで選んだ時間（選んでいなければ全体）と、いまの周波数の表示の範囲
MENU_ITEMS.update({"playback_extract": DISPLAY_TEXTS["extract"], "playback_export_csv": DISPLAY_TEXTS["export_record"]})


def _with_playback_items(layout):
    out = []
    for sec, codes in layout:
        if sec == "file" and "playback_extract" not in codes:
            i = codes.index("export") + 1
            codes = codes[:i] + ("playback_extract", "playback_export_csv") + codes[i:]
        out.append((sec, codes))
    return tuple(out)


MENU_LAYOUT = _with_playback_items(_MENU_LAYOUT)
REASON_TEXTS["not_playing"] = "記録を再生していません（記録のファイルを開くと使えます）"

# 作った機能を「まだ使えません」の表から外す（U-08 の理由は作るまでのもの）
for _k in ("waterfall", "density", "bandplan", "view"):
    NOT_YET.pop(_k, None)
for _k in ("view_waterfall", "view_density"):
    MENU_NOT_YET.pop(_k, None)


def clock_text(t: Optional[float], tenths: bool = False) -> str:
    """UNIX 秒を、この PC の時刻 HH:MM:SS（tenths で 0.1 秒まで）に。"""
    if t is None:
        return ""
    d = datetime.datetime.fromtimestamp(float(t))
    s = d.strftime("%H:%M:%S")
    return s + f".{d.microsecond // 100000}" if tenths else s


def span_text(seconds: Optional[float]) -> str:
    """長さを「11 秒」「3 分 20 秒」「1 時間 5 分」に。"""
    if seconds is None:
        return ""
    s = max(0, int(round(float(seconds))))
    if s < 60:
        return DISPLAY_TEXTS["seconds"].replace("{s}", str(s))
    if s < 3600:
        return DISPLAY_TEXTS["minutes"].replace("{m}", str(s // 60)).replace("{s}", str(s % 60))
    return DISPLAY_TEXTS["hours"].replace("{h}", str(s // 3600)).replace("{m}", str(s % 3600 // 60))


def display_notice_text(code: str, d: dict) -> Optional[str]:
    """表示の知らせの文（組み立てるもの）。表示の知らせでなければ None。"""
    if code == "playback_started":
        parts = [_STARTED_PARTS["name"].replace("{name}", str(d.get("name") or "")).replace(
            "{n}", str(d.get("sweeps") or 0))]
        if d.get("live_stopped"):
            parts.append(_STARTED_PARTS["live_stopped"])
        if d.get("layout_changed"):
            parts.append(_STARTED_PARTS["layout_changed"])
        for p in d.get("problems") or []:
            if p.get("code") == "playback_reference_missing":
                parts.append(_STARTED_PARTS["reference_missing"].replace("{name}", str(p.get("name") or "")))
            elif p.get("code") == "playback_segment_unreadable":
                parts.append(_STARTED_PARTS["segment_unreadable"])
        # 同じ知らせを2回並べない（区間ごとに同じ基準が見つからないとき）
        seen: list[str] = []
        for p in parts:
            if p not in seen:
                seen.append(p)
        return DISPLAY_NOTICES["playback_started"] + "（" + "・".join(seen) + "）"
    if code == "playback_read_failed" and d.get("message"):
        return DISPLAY_NOTICES[code] + "（" + str(d["message"]) + "）"
    if code in DISPLAY_NOTICES:
        return DISPLAY_NOTICES[code]
    return None
