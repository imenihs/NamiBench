"""外部 IQ 送信の口（設計S §1 §7）の画面に出すもの。Qt（statusbar.py・pluto_panel.py・settings_dialog.py）と Web（state の
ui.ext_tx と状態バーの項目）が同じ関数を使う。Qt を import しない。

- 状態バー: 待ち受けている間「外部 IQ 待ち受け :1235」（ポートを開けていることを見せる: rtl_tcp と同じ扱い）。start の後
  「外部 IQ 準備中」、出している間「外部 IQ 出力中 1575.42MHz」（「SG 出力中」と同じ形）。途切れがあった直後 5 秒は注意の色、
  ツールチップに途切れの累計。相手がつながっている間は隣に［送信を止める］
- 信号源のタブの「送信（PlutoSDR）」: 「出力中: 外部 IQ（{client}）」・標本化の速さ・中心の周波数・IQ の強さ・［送信を止める］。
  送信の減衰の欄は外部の送信の間はその値（SG・TG と別の値: O1）を出し、変えると外部の送信の減衰を変える
- モードの SG・TG・S11 と、使えない RBW の理由
"""
from __future__ import annotations

import time
from typing import Optional

from ..core.units import format_freq
from .texts_exttx import EXT_TX_LABELS, EXT_TX_REFUSE_CODES, EXT_TX_STOP_REASONS
from .texts_ui import TEMPLATES

# 途切れがあった後、状態バーの項目を注意の色にする秒
UNDERRUN_WARN_S = 5.0


def _tpl(key: str, **kw) -> str:
    text = TEMPLATES[key]
    for k, v in kw.items():
        text = text.replace("{" + k + "}", str(v))
    return text


def rate_text(hz) -> str:
    """標本化の速さ（2.6MS/s・4.096MS/s）。"""
    return f"{float(hz) / 1e6:g}MS/s"


def _session(st: dict) -> Optional[dict]:
    return (st.get("ext_tx") or {}).get("session")


def active(st: dict) -> bool:
    return _session(st) is not None


def status_item(st: dict, now: Optional[float] = None) -> dict:
    """状態バーの項目（key・text・cls・tip・stop）。stop は［送信を止める］を出すか。"""
    e = st.get("ext_tx") or {}
    s = e.get("session")
    item = {"key": "exttx", "text": "", "cls": "", "tip": "", "stop": False}
    if s is not None:
        if s.get("state") in ("on", "stopping"):
            item["text"] = _tpl("status_ext_on", freq=format_freq(int(s["tx_lo_hz"])))
        else:
            item["text"] = TEMPLATES["status_ext_preparing"]
        last = s.get("last_underrun_at")
        now = time.time() if now is None else now
        if last is not None and now - float(last) < UNDERRUN_WARN_S:
            item["cls"] = "warn"
        item["tip"] = _tpl("status_ext_underrun", n=int(s.get("underrun") or 0))
        item["stop"] = True
    elif e.get("listening"):
        item["text"] = _tpl("status_ext_listen", port=e.get("port"))
        item["stop"] = e.get("client") is not None
    elif e.get("enabled") and e.get("error"):
        err = e["error"]
        why = TEMPLATES["rtltcp_port_in_use"] if err.get("code") == "port_in_use" else (err.get("message") or "")
        item["text"] = _tpl("status_ext_error", port=err.get("port", e.get("port")), reason=why)
        item["cls"] = "warn"
    if item["text"] and not item["tip"]:
        item["tip"] = item["text"]
    return item


def tx_view(st: dict) -> dict:
    """信号源のタブの「送信（PlutoSDR）」の外部 IQ の部分。shown が False なら外部の行を出さない。"""
    s = _session(st)
    e = st.get("ext_tx") or {}
    if s is None:
        return {"shown": False, "state_text": "", "rate_text": "", "lo_text": "", "power_text": "",
                "atten_db": float(e.get("atten_db") if e.get("atten_db") is not None else -89.75)}
    power = s.get("power_dbfs")
    return {
        "shown": True,
        "state_text": _tpl("ext_tx_on", client=s.get("client") or "—"),
        "rate_text": rate_text(s["sample_rate_hz"]),
        "lo_text": format_freq(int(s["tx_lo_hz"])),
        "power_text": "—" if power is None else f"{float(power):.1f} dBFS".replace("-", "−"),
        "atten_db": float(s.get("tx_atten_db") if s.get("tx_atten_db") is not None else e.get("atten_db") or -89.75),
    }


def settings_view(st: dict) -> dict:
    """設定の画面のページ「外部のアプリから送信」（受け付ける・ポート・いまの様子・失敗の理由）。"""
    e = st.get("ext_tx") or {}
    text = _tpl("ext_listening", port=e.get("port")) if e.get("listening") else TEMPLATES["ext_stopped_state"]
    err = e.get("error")
    err_text = status_item(st)["text"] if err and e.get("enabled") else ""
    return {"enabled": bool(e.get("enabled")), "port": e.get("port"), "text": text, "error_text": err_text}


def mode_reason(st: dict) -> Optional[str]:
    """SG・TG・S11 のモードを使えない理由（外部の送信の間: 設計S §1 §6.2）。"""
    return EXT_TX_LABELS["mode_reason"] if active(st) else None


def rbw_rate_reason(st: dict) -> Optional[str]:
    """いまの標本化の速さで使えない RBW の理由（外部 IQ の送信の間: 設計S §1 §3.2）。"""
    sw = st.get("sweep") or {}
    if not sw.get("rbw_rate_unusable_hz"):
        return None
    return _tpl("ext_rbw_rate", rate=rate_text(sw.get("sample_rate_hz") or 0))


def notice_detail(code: str, d: dict) -> str:
    """外部 IQ の知らせの括弧の中（理由・ポート）。"""
    r = ""
    if code == "ext_tx_stopped":
        r = EXT_TX_STOP_REASONS.get(d.get("reason"), d.get("reason") or "")
    elif code == "ext_tx_refused":
        r = EXT_TX_REFUSE_CODES.get(d.get("code"), d.get("code") or "")
    elif d.get("port"):
        r = _tpl("port", port=d["port"])
    return _tpl("paren", text=r) if r else ""
