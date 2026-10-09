"""機器の一覧（設計J §4.7）と上段の「基準を取る」（設計J §4.2・E §6.2）の語と、画面に出す形。

コアが返す状態のコード（available・in_use_here …）を利用者の言葉にする（U-22）。texts.py が取り込み、
/api/v1/texts で Web へも同じ表を配る。行の文（名前・状態・詳細・次の手）は Qt と Web が同じ関数で作る
（Web はサーバーの view.py がこの関数で足す）。Qt を import しない。
"""
from __future__ import annotations

import datetime
from typing import Optional

from .texts_ui import TEMPLATES

# 機器の種類（名前を持たない行・種類の見出しに使う）
DEVICE_KINDS = {"gigast": "GigaSt", "rtlsdr": "RTL-SDR", "plutosdr": "PlutoSDR", "simulator": "シミュレータ"}

# 状態のコード → 利用者の言葉（J §4.7 の 3）
DEVICE_STATES = {
    "available": "使えます",
    "in_use_here": "使用中",
    "known": "前に使った機器",
    "busy_other_app": "ほかのアプリケーションが使っています",
    "first_time_needs_probe": "初めての機器",
    "dll_not_found": "DLL が見つかりません",
}

# 状態の色（warn は注意の色）
DEVICE_STATE_CLS = {"busy_other_app": "warn", "dll_not_found": "warn", "in_use_here": "ok"}

DEVICE_LABELS = {
    "title": "機器に接続",
    "empty": "GigaSt・RTL-SDR・PlutoSDR を USB に挿すと、ここに出ます",
    "try_sim": "シミュレータで試す",
    "connect": "接続",
    "rename": "名前…",
    "rename_title": "機器の名前",
    "rename_label": "名前",
    "rename_placeholder": "既定の名前",
    "details": "詳細",
    "choose_dll": "DLL のフォルダを選ぶ…",
    "close": "閉じる",
    "in_use_reason": "いま使っている機器です",
    "dll_folder_title": "RTL-SDR の DLL（rtlsdr.dll）のあるフォルダ",
    "dll_folder_title_plutosdr": "PlutoSDR の DLL（libiio.dll）のあるフォルダ",
}

# 繋げなかったときの次の手（J §4.7 の 5。理由の文は ERRORS）
CONNECT_NEXT_STEPS = {
    "probe_failed": "GigaSt なら、USB を挿し直してからもう一度［接続］を押してください",
    "open_failed": "USB を挿し直してからもう一度［接続］を押してください",
    "device_not_found": "一覧から選び直してください",
    "device_ambiguous": "一覧の行（番号付きのポート）から選んでください",
    "rtlsdr_dll_not_found": "［DLL のフォルダを選ぶ］で rtlsdr.dll のあるフォルダを選んでください",
    "plutosdr_dll_not_found": "［DLL のフォルダを選ぶ］で libiio.dll（64 ビット版）のあるフォルダを選んでください",
}

TEMPLATES.update({
    "device_detail_port": "ポート {port}",
    "device_detail_usb": "USB ID {usb}",
    "device_detail_serial": "シリアル番号 {serial}",
    "device_detail_dll": "探したフォルダ: {folder}",
    "device_detail_dll_default": "探したフォルダ: 自動で探す場所",
    "device_connecting": "繋いでいます…（{name}）",
    "device_connect_failed": "{name} に繋げませんでした: {reason}",
    "device_next": "{reason}。{next}",
    "auto_connected": "前回の {name} に繋ぎました",
    "status_conn_none": "● 未接続（機器 → 機器に接続… で選べます）",
    "status_conn_who": " {name}（{port}）",
    "status_conn_who_name": " {name}",
    # 上段の「基準を取る」（4-3b）。名前の既定はモードと時刻（S11 の「S11 HH:MM」と同じ形）。基準は直結に限らない
    # （ケーブル・アッテネータ・治具を入れたまま被測定物だけを外して取る）ので「直結」と名付けない
    "ref_direct_name": "TG {time}",
    "ref_quick_now": "基準: {name}（{time}）",
    "ref_quick_name": "基準: {name}",
    "ref_quick_none": "基準なし（dBm で表示）",
    "ref_quick_power_cycled": "⚠ 電源の入れ直し前",
    "ref_zero_shown": "（0dB が見えるよう REF レベルを {ref} dB にしました）",
})

REF_QUICK_LABELS = {
    "button": "基準を取る",
    "tip": "TG の範囲を 1MHz 刻みで1回測り、0dB の基準にする",
    "cancel": "やめる",
    "taking_reason": "基準を取っています",
}


def port_shown(kind) -> bool:
    """ポート（COM 番号）を画面に出す機種か。RTL-SDR（rtlsdr:0）とシミュレータのポートは内部の名前（J §4.9）。"""
    return kind == "gigast"


def device_name(d: dict) -> str:
    """一覧の行・状態の名前。付けた名前 → 既定の名前（機種名＋シリアル番号の末尾4桁） → 種類の名前。"""
    return d.get("name") or d.get("default_name") or DEVICE_KINDS.get(d.get("kind"), d.get("kind") or "")


def _tpl(key: str, **kw) -> str:
    text = TEMPLATES[key]
    for k, v in kw.items():
        text = text.replace("{" + k + "}", str(v))
    return text


def device_details(d: dict) -> str:
    """行の詳細（たたんでおく: COM 番号・USB ID・シリアル番号・USB の説明）。"""
    if d.get("state") == "dll_not_found":
        folder = d.get("dll_folder")
        return _tpl("device_detail_dll", folder=folder) if folder else TEMPLATES["device_detail_dll_default"]
    parts = []
    if d.get("port") and port_shown(d.get("kind")):
        parts.append(_tpl("device_detail_port", port=d["port"]))
    if d.get("usb_id"):
        parts.append(_tpl("device_detail_usb", usb=d["usb_id"]))
    if d.get("serial"):
        parts.append(_tpl("device_detail_serial", serial=d["serial"]))
    if d.get("description"):
        parts.append(str(d["description"]))
    return "・".join(parts)


def device_row_view(d: dict) -> dict:
    """1行の出し方: 名前・状態の文と色・詳細・押せるもの（connect / choose_dll）と押せない理由。"""
    state = d.get("state") or ""
    action = "choose_dll" if state == "dll_not_found" else "connect"
    return {
        "name": device_name(d),
        "state_text": DEVICE_STATES.get(state, state),
        "state_cls": DEVICE_STATE_CLS.get(state, ""),
        "details": device_details(d),
        "action": action,
        "action_label": DEVICE_LABELS["choose_dll"] if action == "choose_dll" else DEVICE_LABELS["connect"],
        "action_reason": DEVICE_LABELS["in_use_reason"] if state == "in_use_here" else None,
        "renamable": bool(d.get("serial")) and d.get("kind") != "simulator",
    }


def is_device_row(d: dict) -> bool:
    """挿した機器の行か（シミュレータと DLL の行を除く）。無ければ空のときの案内を出す。"""
    return d.get("kind") != "simulator" and d.get("state") != "dll_not_found"


def devices_view(devices: list[dict]) -> dict:
    """一覧の画面の中身: 行（シミュレータは空のときの［シミュレータで試す］で出すので、挿した機器が
    あるときだけ行に並べる）と、空のときの案内を出すか。"""
    real = [d for d in devices if is_device_row(d)]
    rows = [d for d in devices if d.get("kind") != "simulator" or real]
    return {"rows": [{**d, "view": device_row_view(d)} for d in rows], "empty": not real,
            "simulator": next((d for d in devices if d.get("kind") == "simulator"), None)}


def connect_failed_text(name: str, reason: str, code: Optional[str]) -> str:
    """繋げなかった理由と次の手（J §4.7 の 5）。"""
    nxt = CONNECT_NEXT_STEPS.get(code or "")
    text = _tpl("device_next", reason=reason, next=nxt) if nxt else reason
    return _tpl("device_connect_failed", name=name, reason=text) if name else text


def hhmm(ts) -> str:
    """取得の時刻。今日なら「12:24」、別の日なら日付も付ける（「09/30 12:24」: 時刻だけだと前の日と取り違える）。"""
    try:
        t = datetime.datetime.fromtimestamp(float(ts))
    except (TypeError, ValueError, OSError, OverflowError):
        return ""
    return t.strftime("%H:%M") if t.date() == datetime.date.today() else t.strftime("%m/%d %H:%M")
