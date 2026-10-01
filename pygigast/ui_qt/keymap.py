"""キーの割り当て（設計J §4.6）の決まり。Qt を import しない（試験と、表の形を Web と合わせるため）。

- 割り当ては画面ごと（Qt は Qt の設定、Web はブラウザの保存領域: 設計O §1.1）。ここは Qt の分を扱い、
  Web は同じ表（texts の WEB_KEY_DEFAULTS・WEB_RESERVED_KEYS）を使って ui_web/app_keys.js で同じ決まりを持つ
- 命令ごとに1つのキー（無しも可）。初期値は texts の QT_KEY_DEFAULTS。利用者が変えた分だけを持つ（overrides）
- マーカーを左右へ動かすキーは、Shift を付けたキーも同じ操作（10点ずつ）にする
- ぶつかるキー（ほかの命令が使っているキー）は黙って上書きしない。ぶつかる相手を返し、画面が聞く（M-02）
- 割り当てられないキー（欄の移動・窓を閉じる・メニューのアクセスキー）は理由を返す
"""
from __future__ import annotations

import json
from typing import Optional

from . import texts


def commands() -> list[str]:
    """割り当てられる命令（メニューの並び。モードはモードの段に）。"""
    out: list[str] = []
    for sec, codes in texts.MENU_LAYOUT:
        if sec == "mode":
            out += [f"mode_{c}" for c, _l, _n, _w in texts.MODES]
        out += [c for c in codes if c is not None and c not in out]
    return out


def command_name(code: str) -> str:
    if code.startswith("mode_"):
        c = code[5:]
        return f"{texts.MODE_LABELS.get(c, c)}（{texts.MODE_NAMES.get(c, c)}）"
    return texts.MENU_ITEMS.get(code, code)


def load_overrides(raw) -> dict[str, str]:
    """設定に書いた形（JSON の文字か dict）から読む。読めない分は捨てる（初期値のまま）。"""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return {}
    if not isinstance(raw, dict):
        return {}
    known = set(commands())
    return {k: v for k, v in raw.items() if k in known and isinstance(v, str)}


def dump_overrides(overrides: dict[str, str]) -> str:
    return json.dumps(overrides, ensure_ascii=False, sort_keys=True)


def effective(overrides: dict[str, str], defaults: Optional[dict] = None) -> dict[str, str]:
    """命令 → キー（"" はキー無し）。"""
    d = texts.QT_KEY_DEFAULTS if defaults is None else defaults
    return {c: overrides.get(c, d.get(c, "")) for c in commands()}


def sequences(code: str, key: str) -> tuple[str, ...]:
    """その命令に付けるキーの並び。マーカーを動かすキーは Shift 付きも足す。"""
    if not key:
        return ()
    if code in texts.SHIFT_STEP_KEYS and not key.startswith("Shift+"):
        return (key, "Shift+" + key)
    return (key,)


def reserved_reason(key: str, reserved: Optional[dict] = None) -> Optional[str]:
    r = texts.QT_RESERVED_KEYS if reserved is None else reserved
    code = r.get(key)
    if code is None and key in ("Ctrl", "Shift", "Alt", "Meta", "Control"):
        code = "modifier_only"
    return texts.KEY_RESERVED_REASONS[code] if code else None


def conflict(keymap: dict[str, str], code: str, key: str) -> Optional[str]:
    """key を code に付けるとき、同じキー（Shift 付きの分を含む）を使っているほかの命令。"""
    wanted = set(sequences(code, key))
    for other, k in keymap.items():
        if other != code and wanted & set(sequences(other, k)):
            return other
    return None


def assign(overrides: dict[str, str], code: str, key: str, defaults: Optional[dict] = None,
           take_from: Optional[str] = None) -> dict[str, str]:
    """code に key を付けた新しい overrides。take_from があればその命令のキーを外す（付け替え）。"""
    d = texts.QT_KEY_DEFAULTS if defaults is None else defaults
    out = dict(overrides)
    for c, k in ((code, key),) + (((take_from, ""),) if take_from else ()):
        if d.get(c, "") == k:
            out.pop(c, None)
        else:
            out[c] = k
    return out


def changed(overrides: dict[str, str], defaults: Optional[dict] = None) -> list[str]:
    """初期値から変えた命令（「全部を初期値に戻す」の確認で数を言う）。"""
    d = texts.QT_KEY_DEFAULTS if defaults is None else defaults
    return [c for c, k in overrides.items() if d.get(c, "") != k]


def key_text(key: str) -> str:
    """画面に出すキーの名前（無ければ「（なし）」）。"""
    return key or texts.SETTINGS_TEXTS["keys_none"]
