"""ブラウザの画面だけで使う、設定の画面・キーの割り当て・プリセット・並びの語（設計O・J §4.4・§4.6・§4.3.2）。

texts.py が `from .texts_web_settings import *` で取り込み、/api/v1/texts で Web へ配る。
Qt と同じ物を指す語は texts_settings.py の表を使い、ここにはブラウザに特有の語だけを置く。Qt を import しない。
"""
from __future__ import annotations

# ブラウザに特有の語（設定の画面・キーの割り当て・並び）
WEB_SETTINGS_TEXTS: dict[str, str] = {}
