"""外部 IQ 送信の口（設計S §1）の語。texts.py が取り込み、/api/v1/texts で Web へも同じ表を配る。Qt を import しない。"""
from __future__ import annotations

from .texts_ui import TEMPLATES

EXT_TX_LABELS = {
    # 設定の画面のページ（受け付ける・ポート）
    "enable": "受け付ける",
    "port": "ポート番号",
    # 状態バーと信号源のタブの「送信（PlutoSDR）」
    "stop": "送信を停止",
    "rate": "サンプリング周波数",
    "lo": "中心周波数",
    "power": "IQ のレベル",
    "power_tip": "受信中の IQ の平均レベル（int16 の最大値 32767 に対する dBFS）。RF のレベルはこれと送信の減衰で決まります。",
    "atten_tip": "外部のアプリケーションの送信の出力のレベル（SG・TG・S11 とは別の値。0dB が最大。0.25dB ステップ）。",
    # SG・TG・S11 のモードと利得の補正を使えない理由（U-08）
    "mode_reason": "外部のアプリケーションが送信中",
}

# 止めた理由（プロトコルの stopped.reason）
EXT_TX_STOP_REASONS = {
    "client": "クライアントが終了したため",
    "user": "手動で停止",
    "device_lost": "機器の接続が切れたため",
    "app_exit": "アプリケーションを閉じるため",
    "starved": "1 秒以上 IQ を受信しなかったため",
    "protocol_error": "クライアントのプロトコル違反のため",
    "disabled": "受け付けを無効にしたため",
}

# start を断った理由（プロトコルの error.code）
EXT_TX_REFUSE_CODES = {
    "not_connected": "PlutoSDR が未接続です",
    "device_cannot_transmit": "接続中の機器は送信に対応していません",
    "mode_conflict": "SG・TG・S11 モードの間は使用できません",
    "calibrating": "校正の測定中です",
    "sample_rate_unsupported": "使用できないサンプリング周波数です",
    "lo_out_of_range": "送信の中心周波数が範囲外です",
    "already_started": "開始済みです",
}

EXT_TX_ERRORS = {
    "ext_tx_active": "外部のアプリケーションの送信中は使用できません",
}

EXT_TX_NOTICES = {
    "ext_tx_listening": "外部 IQ の受け付けを開始しました",
    "ext_tx_closed": "外部 IQ の受け付けを停止しました",
    "ext_tx_port_error": "外部 IQ のポートを開けません",
    "ext_tx_started": "外部 IQ の送信を開始しました",
    "ext_tx_stopped": "外部 IQ の送信を停止しました",
    "ext_tx_refused": "外部 IQ の開始要求を拒否しました",
    "ext_tx_rate_adjusted": "外部 IQ のサンプリング周波数に合わせて掃引の条件を変更しました",
}

TEMPLATES.update({
    "status_ext_listen": "外部 IQ 待機中 :{port}",
    "status_ext_preparing": "外部 IQ 準備中",
    "status_ext_on": "外部 IQ 出力中 {freq}",
    "status_ext_error": "外部 IQ: ポート {port} を開けません（{reason}）",
    "status_ext_underrun": "アンダーラン: {n} サンプル",
    "ext_tx_on": "出力中: 外部 IQ（{client}）",
    "ext_listening": "待機中: ポート {port}（この PC のみ）",
    "ext_stopped_state": "停止中",
    "ext_rbw_rate": "外部 IQ のサンプリング周波数（{rate}）では使用できません",
    "rtltcp_rate_paused": "rtl_tcp のクライアントのサンプリング周波数に合わせられないため、外部 IQ の送信中は送出しません",
})
