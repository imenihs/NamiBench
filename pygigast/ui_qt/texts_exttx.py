"""外部 IQ 送信の口（設計S §1）の語。texts.py が取り込み、/api/v1/texts で Web へも同じ表を配る。Qt を import しない。"""
from __future__ import annotations

from .texts_ui import TEMPLATES

EXT_TX_LABELS = {
    # 設定の画面のページ（受け付ける・ポート）
    "enable": "受け付ける",
    "port": "ポート番号",
    # 状態バーと信号源のタブの「送信（PlutoSDR）」
    "stop": "送信を止める",
    "rate": "標本化の速さ",
    "lo": "中心の周波数",
    "power": "IQ の強さ",
    "power_tip": "届いている IQ の平均の強さ（int16 の振り切れ 32767 に対する dBFS）。RF の強さはこれと送信の減衰で決まります",
    "atten_tip": "外部のアプリケーションの送信の出力のレベル（SG・TG・S11 とは別の値。0dB がいちばん大きい。0.25dB 刻み）",
    # SG・TG・S11 のモードと利得の補正を使えない理由（U-08）
    "mode_reason": "外部のアプリケーションが送信しています",
}

# 止めた理由（プロトコルの stopped.reason）
EXT_TX_STOP_REASONS = {
    "client": "相手が終えたため",
    "user": "止めました",
    "device_lost": "機器が切れたため",
    "app_exit": "アプリケーションを閉じるため",
    "starved": "1 秒以上 IQ が届かなかったため",
    "protocol_error": "相手の送り方が誤っていたため",
    "disabled": "受け付けを切ったため",
}

# start を断った理由（プロトコルの error.code）
EXT_TX_REFUSE_CODES = {
    "not_connected": "PlutoSDR に繋がっていません",
    "device_cannot_transmit": "いまの機器は送信できません",
    "mode_conflict": "SG・TG・S11 のモードの間は使えません",
    "calibrating": "校正を測っています",
    "sample_rate_unsupported": "使えない標本化の速さです",
    "lo_out_of_range": "送信の中心の周波数が範囲の外です",
    "already_started": "もう始めています",
}

EXT_TX_ERRORS = {
    "ext_tx_active": "外部のアプリケーションが送信している間は使えません",
}

EXT_TX_NOTICES = {
    "ext_tx_listening": "外部 IQ の受け付けを始めました",
    "ext_tx_closed": "外部 IQ の受け付けを止めました",
    "ext_tx_port_error": "外部 IQ のポートを開けません",
    "ext_tx_started": "外部 IQ の送信を始めました",
    "ext_tx_stopped": "外部 IQ の送信を止めました",
    "ext_tx_refused": "外部 IQ の開始を断りました",
    "ext_tx_rate_adjusted": "外部 IQ の標本化の速さに合わせて掃引の条件を変えました",
}

TEMPLATES.update({
    "status_ext_listen": "外部 IQ 待ち受け :{port}",
    "status_ext_preparing": "外部 IQ 準備中",
    "status_ext_on": "外部 IQ 出力中 {freq}",
    "status_ext_error": "外部 IQ: ポート {port} を開けません（{reason}）",
    "status_ext_underrun": "途切れ: {n} 標本（0 で埋めた分）",
    "ext_tx_on": "出力中: 外部 IQ（{client}）",
    "ext_listening": "待ち受け中: ポート {port}（この PC だけ）",
    "ext_stopped_state": "止まっています",
    "ext_rbw_rate": "外部 IQ の標本化の速さ（{rate}）では使えません",
    "rtltcp_rate_paused": "rtl_tcp の相手のサンプリング速度に合わせられないので、外部 IQ の送信の間は送りません",
})
