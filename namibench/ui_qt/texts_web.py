"""ブラウザから使う（同じネットワークのスマートフォンや PC から使う）ときの語（設計A §4.3.2）。Qt の設定・接続のしかたの窓・状態バー、Web の認証の画面と
≡ の「接続のしかたを表示」、`serve` のコンソールで同じ語を使う（J §5）。

texts.py が取り込み、/api/v1/texts で Web へ配る。`{名前}` は差し込み（texts.tpl と同じ置き換え）。Qt を import しない。
利用者に見せる語は「認証番号」。鍵は見せない語（URL と QR の中にだけある: A §4.3.2 の 2）。
"""
from __future__ import annotations

WEB_LABELS = {
    # 前回は LAN に出していたが、起動したときは出さない（設計A §4.3.2。黙って切らずに知らせる）
    "lan_off_at_start_title": "ブラウザからの使用",
    "lan_off_at_start": "前回は「同じネットワークのスマートフォンや PC からも使う」が有効でしたが、"
                        "起動時は無効です（ほかの機器から見えないようにするため）。\n"
                        "使用するときは、メニュー「設定 → 設定…」の「ブラウザからの使用」で再度有効にしてください。",
    "open_settings": "設定を開く",
    # ---- 設定の画面（O §1.3 の「ブラウザから使う」）
    "enable": "この PC のブラウザから使う",
    "lan": "同じネットワークのスマートフォンや PC からも使う",
    # スマートフォンや PC からも使う間だけ、そのチェックの下に出す（U-02・U-14）
    "lan_warn": "通信は暗号化されません。インターネットに公開しないでください",
    "port": "ポート番号",
    "default": "初期値に戻す",
    "show_connect": "接続方法を表示",
    "regenerate": "認証を再作成",
    "regenerate_tip": "接続用のリンク・QR コードと認証番号を再作成し、記憶していたすべてのブラウザを切断します（再度 QR コードか認証番号で接続します）。",
    "state_off": "無効",
    "state_local": "有効: {url}（この PC のブラウザのみ）",
    "state_lan": "有効: {url}（同じネットワークのスマートフォン・PC からも）",
    "state_error": "ブラウザからの使用を有効にできません: {reason}",
    # ---- 接続方法の窓
    "connect_title": "ブラウザからの接続方法",
    "connect_operate": "操作可",
    "connect_view": "閲覧のみ",
    "copy": "コピー",
    "copied": "コピーしました",
    "code_label": "{plain} で入力する認証番号",
    "code_valid": "残り {s} 秒有効",
    "code_expired": "この番号は無効になりました。ウィンドウを再度開くと新しい番号を表示します",
    "no_address": "この PC のネットワークのアドレスが見つかりません。ネットワークへの接続を確認してください",
    "lan_off": "スマートフォンや別の PC からは使用できない設定です",
    "close": "閉じる",
    # ---- 状態バーと相手の一覧
    "status": "ブラウザから接続 {n}（操作 {a}・閲覧のみ {b}）",
    "clients_title": "接続中のブラウザ",
    "clients_empty": "接続中のブラウザはありません",
    "col_time": "接続時刻",
    "col_ip": "IP アドレス",
    "col_kind": "種類",
    "disconnect": "切断",
    "kind_operate": "操作",
    "kind_view": "閲覧のみ",
    # ---- ブラウザの認証の画面（ユーザー決定の文言: A §4.3.2 の 2）
    "auth_title": "NamiBench",
    "auth_prompt": "NamiBench の PC に出ている6桁の認証番号を入れてください",
    "auth_button": "入る",
    "auth_checking": "確認中…",
    "auth_need_js": "この画面は JavaScript を使います",
    # ---- 閲覧のみの接続（A §4.3.2 の 4）
    "read_only": "閲覧のみの接続です",
    # ---- serve のコンソール
    "console_title": "NamiBench をブラウザから使用（同じネットワークのスマートフォンや PC からも使用できます）",
    "console_local": "この PC のブラウザ: {url}",
    "console_operate": "操作可: {url}",
    "console_view": "閲覧のみ: {url}",
    "console_code": "認証番号（{s} 秒有効）: {code}　— {plain} を開いて入力します",
    "console_keys": "Enter で新しい認証番号、r + Enter で認証を再作成（すべてのブラウザを切断）、Ctrl+C で終了",
    "console_regenerated": "認証を再作成しました。以前の QR コード・リンク・認証番号は使用できません",
    "console_no_address": "この PC のネットワークのアドレスが見つかりません。ネットワークへの接続を確認してください",
    "console_local_only": "NamiBench をブラウザから使用: {url}（この PC のみ。スマートフォンや別の PC からも使用するときは --lan を付けて起動）",
}

WEB_KINDS = {"operate": "操作", "view": "閲覧のみ"}

WEB_ERRORS = {
    "path_not_allowed": "ブラウザからは NamiBench の PC のフォルダを指定できません（ダウンロード・アップロードを使用します）",
    "read_only": "閲覧のみの接続です",
    "auth_required": "認証が必要です",
    "wrong_code": "認証番号が正しくないか、有効期限の 2 分を過ぎました。PC に表示中の番号を入力してください",
    "blocked": "続けて誤入力したため、1 分間は入力を受け付けません",
    "bad_key": "このリンクは使用できません（アプリケーションを再起動したか、認証を再作成しました）。PC の画面の QR コードか認証番号で接続し直してください",
    "bad_host": "この宛先からは使用できません",
    "bad_origin": "ほかのサイトからの操作は受け付けません",
    "bad_content_type": "送信データの形式が正しくありません",
    "upload_too_large": "ファイルが大きすぎます（上限 2GB）",
    "upload_required": "ファイルを選択してください",
    "unknown_upload": "アップロードしたファイルが見つかりません",
    "unknown_recording": "その記録は既に一覧にありません",
    "unknown_file": "そのファイルは既にありません",
    "file_not_ready": "作成中です",
    "lan_off": "スマートフォンや別の PC からは使用できない設定です",
    "lan_closed": "スマートフォンや別の PC からは使用できない設定です",
    "web_port_in_use": "ポートを開けません（ほかのアプリケーションが使用中か、開始済みです）",
}


def tpl_web(key: str, **kw) -> str:
    """WEB_LABELS の `{名前}` を差し込む（texts.tpl と同じ置き換え）。"""
    text = WEB_LABELS[key]
    for k, v in kw.items():
        text = text.replace("{" + k + "}", str(v))
    return text
