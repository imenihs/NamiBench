"""PlutoSDR の欄の中身（設計Q §7）。Qt（pluto_panel.py・tab_source.py・settings_dialog.py）と Web（state の ui.pluto）が
同じ関数を使う。Qt を import しない。

- 受信の欄（右のタブ「トレース・掃引」）: 受信の利得（打てるドロップダウン。いまの範囲の 1dB 刻みの値を打てて、一覧は
  自動と、範囲の中の 10dB 刻み（2026-10-11 ユーザー指摘「1dB 刻みの全部を並べた一覧は使いにくい」））・「利得を合わせる」・単位。
  設定の利得がいまの範囲で使えないとき（利得の範囲は LO の帯で変わる）は、使う値を利得の欄のそばに出す（U-25）。
  受信の利得の補正の表（設計Q §7.6）が無いときは、利得の欄のそばに「利得の補正なし」の印（理由はツールチップ）。
  TG で基準を選んでいる間・利得の補正を測っている間は、利得の欄と「利得を合わせる」を理由付きで使えなくする（U-08）
- 送信の欄（右のタブ「信号源」）: 送信の減衰（SG・TG の出力のレベル）と TG 高調波の平均除去（設計Q §7.7。選択肢の文字に、
  いまの条件での1回の掃引の見積もりを出す: イメージ除去の選択と同じ形。TG のときだけ選べる）
- 欄は PlutoSDR に繋いでいるときだけ出す（設計J §4.5: 別の機種の欄は出さない）
- 設定の画面の PlutoSDR のページ: 見つけた libiio.dll の場所（無い・32 ビット版なら注意の色で理由）と URI
"""
from __future__ import annotations

import math

from . import texts
from .texts_pluto import PLUTO_LABELS, TG_HARMONIC_CHOICES

# 受信の利得の一覧に並べる刻み（dB）。打てばいまの範囲の 1dB 刻みのどの値でも
GAIN_CHOICE_STEP_DB = 10


def _db(v: float) -> str:
    return f"{float(v):g}".replace("-", "−")


def tg_harmonic_view(st: dict) -> dict:
    """TG 高調波の平均除去の選択（設計Q §7.7。Qt と Web が同じものを出す）: 選択肢（文字は名前と、いまの条件での1回の
    掃引の見積もり。TG でないときは名前だけ）・いまの回数・選べないときの理由（PlutoSDR でない・TG でない）。"""
    pl = st.get("plutosdr") or {}
    est = pl.get("tg_harmonic_estimates") or {}
    codes = pl.get("tg_harmonic_choices") or [int(c) for c in TG_HARMONIC_CHOICES]
    items = [{"code": int(c), "text": texts.choice_with_seconds(TG_HARMONIC_CHOICES.get(str(c), f"{c}"), est.get(str(c)))}
             for c in codes]
    reason = None
    if not pl.get("connected"):
        reason = PLUTO_LABELS["not_pluto"]
    elif (st.get("sweep") or {}).get("mode") != "tg":
        reason = PLUTO_LABELS["tg_harmonic_not_tg"]
    return {"items": items, "value": int(pl.get("tg_harmonic_avg") or 1), "reason": reason}


def tg_harmonic_width_samples() -> list[str]:
    """選択肢の一番長い文字の見本（秒を 999.9 にしたもの。秒の桁で欄を動かさない: イメージ除去の欄と同じ）。"""
    return [texts.choice_with_seconds(name, 999.9) for name in TG_HARMONIC_CHOICES.values()]


def pluto_view(st: dict) -> dict:
    pl = st.get("plutosdr") or {}
    connected = bool(pl.get("connected"))
    reason = None if connected else PLUTO_LABELS["not_pluto"]
    lim = pl.get("gain_limits_db") or [-1, 73]
    lo, hi = float(lim[0]), float(lim[1])
    step = GAIN_CHOICE_STEP_DB
    choices = [float(g) for g in range(int(math.ceil(lo / step)) * step, int(hi) + 1, step)]
    agc = bool(pl.get("agc"))
    setting = pl.get("gain_db") if pl.get("gain_db") is not None else pl.get("gain_default_db")
    used = pl.get("gain_db_used")
    # 選んで見せる値: 設定がいまの範囲で使えればその値、使えなければ使う値（そのそばに「この範囲では … を使います」）
    shown_gain = None if agc else (used if used is not None else setting)
    # 自動の間に ↑↓・ホイールで送るときの元の値（使う値・設定の順。どちらも無ければ範囲の中ほど。いまの範囲に収める）
    base = next((float(x) for x in (shown_gain, used, setting) if x is not None), float(round((lo + hi) / 2)))
    base = min(max(base, lo), hi)
    used_text = ""
    if connected and not agc and used is not None and setting is not None and float(used) != float(setting):
        used_text = texts.tpl("pluto_gain_used", db=_db(used))
    gain_reason = reason or (PLUTO_LABELS["gain_locked_reference"] if pl.get("gain_lock") else None) \
        or (PLUTO_LABELS["calibrating"] if pl.get("calibrating") else None)
    rng = pl.get("tx_atten_range") or {"min": -89.75, "max": 0.0, "step": 0.25}
    tx_on = pl.get("tx_on")
    bits = pl.get("dll_bits")
    return {
        "shown": connected,
        "connected": connected,
        "reason": reason,
        # 利得の欄: 一覧の数（小さい順。RTL-SDR の欄と同じ）・打てる範囲・欄に出す値。自動のときは auto_text を出し、
        # ↑↓・ホイールは gain_base から送る
        "gain_choices": choices,
        "gain_lo": lo,
        "gain_hi": hi,
        "auto_text": PLUTO_LABELS["auto"],
        "agc": agc,
        "gain_db": shown_gain,
        "gain_base": float(base),
        "gain_setting": None if setting is None else float(setting),  # 同じ値を送り直さないための比べる元
        "gain_used_text": used_text,
        # 利得の欄を使えない理由（つないでいない・TG で基準を使っている・利得の補正を測っている）
        "gain_reason": gain_reason,
        # 受信の利得の補正の表が無いときの印（設計Q §7.6）と、その理由（ツールチップ）
        "gain_cal_mark": PLUTO_LABELS["gain_cal_none"] if connected and not pl.get("gain_correction") else "",
        "gain_cal_tip": PLUTO_LABELS["gain_cal_none_tip"] if connected and not pl.get("gain_correction") else "",
        "unit_text": texts.tpl("rtl_unit", unit=pl.get("level_unit") or "") if connected and pl.get("level_unit") else "",
        "fit_reason": gain_reason or (PLUTO_LABELS["fit_needs_manual"] if agc else None)
        or (PLUTO_LABELS["fit_running"] if pl.get("fitting") else None) or texts.live_lock_reason(st),
        "tx_atten_db": float(pl.get("tx_atten_db") if pl.get("tx_atten_db") is not None else -40.0),
        "tx_atten_min": float(rng["min"]),
        "tx_atten_max": float(rng["max"]),
        "tx_atten_step": float(rng["step"]),
        "tx_state": texts.tpl("pluto_tx_on", what=tx_on.upper()) if tx_on else "",
        "tg_harmonic": tg_harmonic_view(st),
        # 設定の画面の libiio.dll の行（設計J §4.12 と同じ形）
        "dll_line": pl.get("dll_path") or texts.SETTINGS_LABELS["rtlsdr_missing"],
        "dll_reason": texts.SETTINGS_LABELS["plutosdr_32bit"] if bits not in (None, 64) else "",
        "dll_warn": not pl.get("dll_path") or bits not in (None, 64),
        "dll_custom": bool(pl.get("dll_folder")),
        "uri": pl.get("uri") or "",
    }
