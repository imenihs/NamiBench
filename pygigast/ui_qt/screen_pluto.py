"""PlutoSDR の欄の中身（設計Q §7）。Qt（pluto_panel.py・tab_source.py・settings_dialog.py）と Web（state の ui.pluto）が
同じ関数を使う。Qt を import しない。

- 受信の欄（右のタブ「トレース・掃引」）: 受信の利得（自動と、いまの範囲で使える 1dB 刻みの値）・「利得を合わせる」・単位。
  設定の利得がいまの範囲で使えないとき（利得の範囲は LO の帯で変わる）は、使う値を利得の欄のそばに出す（U-25）
- 送信の欄（右のタブ「信号源」）: 送信の減衰（SG・TG の出力のレベル）
- 欄は PlutoSDR に繋いでいるときだけ出す（設計J §4.5: 別の機種の欄は出さない）
- 設定の画面の PlutoSDR のページ: 見つけた libiio.dll の場所（無い・32 ビット版なら注意の色で理由）と URI
"""
from __future__ import annotations

from . import texts
from .texts_pluto import PLUTO_LABELS


def _db(v: float) -> str:
    return f"{float(v):g}".replace("-", "−")


def pluto_view(st: dict) -> dict:
    pl = st.get("plutosdr") or {}
    connected = bool(pl.get("connected"))
    reason = None if connected else PLUTO_LABELS["not_pluto"]
    lim = pl.get("gain_limits_db") or [-1, 73]
    gains = [{"value": None, "text": PLUTO_LABELS["auto"]}]
    gains += [{"value": float(g), "text": texts.tpl("rtl_gain_step", db=_db(g))} for g in range(int(lim[0]), int(lim[1]) + 1)]
    agc = bool(pl.get("agc"))
    setting = pl.get("gain_db") if pl.get("gain_db") is not None else pl.get("gain_default_db")
    used = pl.get("gain_db_used")
    # 選んで見せる値: 設定がいまの範囲で使えればその値、使えなければ使う値（そのそばに「この範囲では … を使います」）
    shown_gain = None if agc else (used if used is not None else setting)
    used_text = ""
    if connected and not agc and used is not None and setting is not None and float(used) != float(setting):
        used_text = texts.tpl("pluto_gain_used", db=_db(used))
    rng = pl.get("tx_atten_range") or {"min": -89.75, "max": 0.0, "step": 0.25}
    tx_on = pl.get("tx_on")
    bits = pl.get("dll_bits")
    return {
        "shown": connected,
        "connected": connected,
        "reason": reason,
        "gains": gains,
        "agc": agc,
        "gain_db": shown_gain,
        "gain_used_text": used_text,
        "unit_text": texts.tpl("rtl_unit", unit=pl.get("level_unit") or "") if connected and pl.get("level_unit") else "",
        "fit_reason": reason or (PLUTO_LABELS["fit_needs_manual"] if agc else None)
        or (PLUTO_LABELS["fit_running"] if pl.get("fitting") else None) or texts.live_lock_reason(st),
        "tx_atten_db": float(pl.get("tx_atten_db") if pl.get("tx_atten_db") is not None else -40.0),
        "tx_atten_min": float(rng["min"]),
        "tx_atten_max": float(rng["max"]),
        "tx_atten_step": float(rng["step"]),
        "tx_state": texts.tpl("pluto_tx_on", what=tx_on.upper()) if tx_on else "",
        # 設定の画面の libiio.dll の行（設計J §4.12 と同じ形）
        "dll_line": pl.get("dll_path") or texts.SETTINGS_LABELS["rtlsdr_missing"],
        "dll_reason": texts.SETTINGS_LABELS["plutosdr_32bit"] if bits not in (None, 64) else "",
        "dll_warn": not pl.get("dll_path") or bits not in (None, 64),
        "dll_custom": bool(pl.get("dll_folder")),
        "uri": pl.get("uri") or "",
    }
