"""PlutoSDR（設計Q §7）の語。texts.py が取り込み、/api/v1/texts で Web へも同じ表を配る。Qt を import しない。"""
from __future__ import annotations

from .texts_ui import TEMPLATES

PLUTO_LABELS = {
    "group": "受信（PlutoSDR）",
    "gain": "受信の利得",
    "auto": "自動",
    "gain_tip": "利得を変えると値の意味が変わるので、トレースの蓄積を捨てます。REF レベルとは連動しません。"
                "使える範囲は周波数で変わります（一覧はいまの範囲で使える値）",
    "fit_gain": "利得を合わせる",
    "fit_gain_tip": "いまの条件で、見えている信号が振り切れないいちばん大きい利得を選びます。選んだらトレースの蓄積を捨てます",
    "fit_needs_manual": "自動の間は使えません（利得を手動の値にしてから押します）",
    "fit_running": "利得を合わせています",
    "tx_group": "送信（PlutoSDR）",
    "tx_atten": "送信の減衰",
    "tx_atten_tip": "SG・TG の出力のレベル（0dB がいちばん大きい。0.25dB 刻み）。SG・TG の間に変えるとトレースの蓄積を捨てます",
    "not_pluto": "PlutoSDR に繋ぐと使えます",
    # 受信の利得の補正（設計Q §7.6）
    "gain_cal_none": "利得の補正なし",
    "gain_cal_none_tip": "この PlutoSDR の受信の利得の補正がありません。利得を変えると、同じ信号の値が動きます"
                         "（校正の「PlutoSDR の利得の補正…」で測ります）",
    "gain_locked_reference": "TG で基準を使っている間は変えられません（基準と同じ利得で測ります）",
    "calibrating": "利得の補正を測っています",
}

PLUTO_ERRORS = {
    "plutosdr_dll_not_found": "PlutoSDR の DLL（libiio.dll）が見つかりません",
    "plutosdr_not_connected": "PlutoSDR に繋がっていません",
    "reference_wide_fft": "この機器の基準はいまの条件で取ります（RBW が違う基準は当たらないため）",
    "plutosdr_gain_reference_in_use": "TG で基準を使っている間は受信の利得を変えられません（基準と同じ利得で測ります）",
}

PLUTO_NOTICES = {
    "plutosdr_setting_failed": "PlutoSDR に利得・送信の減衰を入れられませんでした（測定は続けます）",
}

PLUTO_SETTINGS = {
    "plutosdr_dll": "libiio.dll",
    "plutosdr_32bit": "64 ビット版の libiio が要ります",
    "plutosdr_choose_title": "libiio.dll（64 ビット版）のあるフォルダ",
    "plutosdr_uri": "URI",
    "plutosdr_uri_tip": "USB の RNDIS で繋ぐ PlutoSDR は ip:192.168.2.1。USB で見つかる機器は一覧に自動で出ます",
}

TEMPLATES.update({
    "pluto_gain_used": "この範囲では {db} dB を使います",
    "pluto_tx_on": "出力中: {what}",
    "status_cal_pluto": "補正: PlutoSDR 未校正・単位 {unit}",
    "status_cal_pluto_gain": "補正: PlutoSDR 利得の補正 {at}・単位 {unit}",
})
