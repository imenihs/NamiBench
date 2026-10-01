"""Web の口を LAN に開くときの語（設計A §4.3.2）。Qt の設定・接続のしかたの窓・状態バー、Web の認証の画面と
≡ の「接続のしかたを表示」、`serve` のコンソールで同じ語を使う（J §5）。

texts.py が取り込み、/api/v1/texts で Web へ配る。`{名前}` は差し込み（texts.tpl と同じ置き換え）。Qt を import しない。
利用者に見せる語は「認証番号」。鍵は見せない語（URL と QR の中にだけある: A §4.3.2 の 2）。
"""
from __future__ import annotations

WEB_LABELS = {
    # ---- 設定の画面（O §1.3 の「Web の口」）
    "group": "Web の口（ブラウザから使う）",
    "enable": "Web の口を開く（この PC のブラウザから）",
    "lan": "LAN に開く（スマホや別の PC から。認証が要ります）",
    "port": "ポート番号",
    "default": "初期値に戻す",
    "show_connect": "接続のしかたを表示",
    "regenerate": "認証を作り直す",
    "regenerate_tip": "鍵と認証番号を作り直し、覚えていた全部のブラウザを切ります（もう一度 QR か認証番号で入ります）",
    "note": "暗号化（HTTPS）はしていません。同じ LAN で通信を盗み見られると、URL の鍵や認証番号が漏れることがあります。"
            "インターネットには公開しないでください。鍵はアプリを起動するたびに変わります",
    "state_off": "閉じています",
    "state_local": "開いています: {url}（この PC だけ）",
    "state_lan": "LAN に公開中: {url}",
    "state_error": "開けません: {reason}",
    # ---- 接続のしかたの窓
    "connect_title": "接続のしかた（スマホや別の PC で開く）",
    "connect_operate": "操作できる",
    "connect_view": "見るだけ",
    "connect_qr_note": "スマホのカメラで読むと開きます。開いたブラウザは、このアプリを閉じるまで覚えています",
    "copy": "コピー",
    "copied": "コピーしました",
    "code_label": "QR を使えないときは、{plain} を開いて、この認証番号を入れます",
    "code_valid": "あと {s} 秒有効",
    "code_expired": "この番号は無効になりました。窓を開き直すと新しい番号を出します",
    "no_address": "この PC の LAN のアドレスが見つかりません（LAN に繋がっているか確かめてください）",
    "lan_off": "LAN に開いていません（設定の「Web の口」で LAN に開くと使えます）",
    "close": "閉じる",
    # ---- 状態バーと相手の一覧
    "status": "Web: LAN に公開中・接続 {n}（操作 {a}・見るだけ {b}）",
    "clients_title": "Web でつながっている相手",
    "clients_empty": "つながっている相手はいません",
    "col_time": "つないだ時刻",
    "col_ip": "IP アドレス",
    "col_kind": "種類",
    "disconnect": "切断",
    "kind_operate": "操作",
    "kind_view": "見るだけ",
    # ---- ブラウザの認証の画面（ユーザー決定の文言: A §4.3.2 の 2）
    "auth_title": "pyGigaSt",
    "auth_prompt": "pyGigaSt の PC に出ている6桁の認証番号を入れてください",
    "auth_button": "入る",
    "auth_checking": "確かめています…",
    "auth_need_js": "この画面は JavaScript を使います",
    # ---- 見るだけの接続（A §4.3.2 の 4）
    "read_only": "見るだけの接続です",
    # ---- serve のコンソール
    "console_title": "pyGigaSt の Web 画面（LAN に公開中）",
    "console_local": "この PC のブラウザ: {url}",
    "console_operate": "操作できる: {url}",
    "console_view": "見るだけ: {url}",
    "console_code": "認証番号（{s} 秒有効）: {code}　— {plain} を開いて入れます",
    "console_keys": "Enter で新しい認証番号、r + Enter で認証を作り直し（全部のブラウザを切る）、Ctrl+C で終わり",
    "console_regenerated": "認証を作り直しました。前の QR・URL・認証番号は使えません",
    "console_no_address": "この PC の LAN のアドレスが見つかりません。LAN に繋がっているか確かめてください",
    "console_local_only": "pyGigaSt の Web 画面: {url}（この PC だけ。LAN に開くときは --lan）",
}

WEB_KINDS = {"operate": "操作", "view": "見るだけ"}

WEB_ERRORS = {
    "path_not_allowed": "Web の画面からはサーバーの PC のパスを指定できません（ダウンロード・アップロードを使います）",
    "read_only": "見るだけの接続です",
    "auth_required": "認証が要ります",
    "wrong_code": "認証番号が違うか、2分を過ぎています。PC に出ている今の番号を入れてください",
    "blocked": "続けて間違えたので、1分間は入力を受け付けません",
    "bad_key": "この URL は使えません（アプリを起動し直したか、認証を作り直しました）。PC の画面の QR か認証番号で入り直してください",
    "bad_host": "この宛先からは使えません",
    "bad_origin": "ほかのサイトからの操作は受け付けません",
    "bad_content_type": "送る形が違います",
    "upload_too_large": "ファイルが大きすぎます（2GB まで）",
    "upload_required": "ファイルを選んでください",
    "unknown_upload": "アップロードしたファイルが見つかりません",
    "unknown_recording": "その記録はもう一覧にありません",
    "unknown_file": "そのファイルはもうありません",
    "file_not_ready": "まだ作っています",
    "lan_off": "LAN に開いていません",
    "lan_closed": "LAN に開いていません",
    "web_port_in_use": "ポートを開けません（ほかのアプリが使っているか、もう開いています）",
}


def tpl_web(key: str, **kw) -> str:
    """WEB_LABELS の `{名前}` を差し込む（texts.tpl と同じ置き換え）。"""
    text = WEB_LABELS[key]
    for k, v in kw.items():
        text = text.replace("{" + k + "}", str(v))
    return text
