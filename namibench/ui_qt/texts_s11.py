"""反射（S11）の測定と、トレースの表示の形式（|Γ|・SWR・位相系）の語（設計E §6.5・設計R §3・§5.1）。

texts.py が `from .texts_s11 import *` で取り込み、Web へも同じ表で配る（GET /api/v1/texts）。LABELS・REASON_TEXTS・
TEMPLATES へ足す語と、ERRORS・NOTICES へ足す語（texts.py が足す）を持つ。`{名前}` は差し込み。Qt を import しない。
"""
from __future__ import annotations

from ..core.trace import formats
from .texts_labels import LABELS
from .texts_ui import REASON_TEXTS, TEMPLATES

# トレースの表示の形式（設計R §3。並びは選ぶ欄の並び）。db の名前は S21（TG）の名前で、S11 は S11_FORMAT_NAMES
TRACE_FORMATS = {
    formats.DB: "振幅（dB）",
    formats.GAMMA: "|Γ|",
    formats.SWR: "SWR",
    formats.PHASE: "位相（度）",
    formats.GROUP_DELAY: "群遅延",
    formats.SMITH: "スミスチャート",
    formats.POLAR: "極座標",
}
# S11 の dB の形式の名前（設計E §6.5: 測った値 ÷ 基準の dB。全反射が 0dB）
S11_FORMAT_NAMES = {formats.DB: "リターンロス（dB）"}
# 縦軸の表示の形式の欄（「S11 の表示」）の項目の名前。形式は S11 の比にだけ当たるので dB はリターンロス（dB）
AXIS_FORMAT_NAMES = {code: S11_FORMAT_NAMES.get(code, name) for code, name in TRACE_FORMATS.items()}

REASON_TEXTS.update({
    # 表示の形式（設計R §2・§5.1: 位相を測れない機器では消さずに無効にして理由）
    "format_vector": "この機器は振幅のみを測定します（位相は測定できません）",
    # 縦軸の表示の形式（S11 の比にだけ当たる。S11 モードでも S11 のトレースも無いときは選んでも何も変わらない）
    "format_s11_context": "S11 モードのときか、S11 で測定したトレースがあるときに選択できます",
    # |Γ|・SWR の縦軸（下端が決まっていて REF レベルを持たない）
    "ref_linear": "{unit} の縦軸は下端が {bottom} で、1 目盛あたりの値で範囲が決まります",
    # 縦軸と単位の違う固定のトレース（1つの目盛りに違う単位を混ぜない: 設計E §6.4 と同じ考え）。「表示」のチェックの
    # ツールチップに出す
    "trace_unit_differs": "縦軸（{axis}）と単位が異なるため（{unit}）、表示しません",
    # S11 のオープン・ショート（設計E §6.5）
    "s11_mode_only": "オープン・ショートは S11 モードで測定します",
    "s11_needs_open": "先にオープンを測定してください",
    "tg_take_in_s11": "S11 の基準はオープン・ショートの 2 段で取得します",
})

LABELS.update({
    # 縦軸の表示の形式（S11 の比: リターンロス（dB）・|Γ|・SWR。「振幅の詳細」の単位の下: 設計R §3）
    "axis_format": "S11 の表示",
    "axis_format_tip": "S11（反射）で測定したトレースの縦軸の表示形式。ビュー・参照のトレースも同じ形式で表示します。",
    # 縦軸の1目盛の大きさの欄の名前（dB は dB/div）
    "per_div": "{unit}/div",
    # S11 のオープン・ショート（設計E §6.5。1段ずつ: 次に押すボタンを強調する）
    "s11_range": "S11 の範囲",
    "s11_range_wide": "全範囲",
    "s11_range_fine": "現在の条件",
    "s11_range_tip": "オープンを測定する範囲（全範囲: TG の範囲を 1MHz ステップ）。ショートはオープンと同じ範囲で測定します。",
    "s11_open_tip": "方向性結合器の被測定物側の端子にオープンを接続してから押します。",
    # PlutoSDR（設計Q §7.8: 受信の利得を調整してから測定し、その後は利得と送信の減衰を変えない）
    "s11_open_tip_pluto": "方向性結合器の被測定物側の端子にオープンを接続してから押します。受信の利得を調整してから測定します"
                          "（その後、基準を破棄するか S11 モードを終了するまで、受信の利得と送信の減衰は変更できません）。",
    "s11_short_tip": "オープンを外してショートを接続してから押します。オープンと同じ範囲（{range}）で測定します。",
    "s11_open_done": "オープン: {time} 測定済み（{range}）",
})

TEMPLATES.update({
    # 上段の S11 の基準（オープン・ショート）で作る基準の名前
    "ref_s11_name": "S11 {time}",
    "s11_open_quick": "オープン {time}",
})

# 上段の S11 のボタン（J §4.2: 基準を取るは TG・S11 のとき1クリックで届く所。S11 は次の1段）
S11_QUICK_LABELS = {
    "open": "オープンを測定",
    "short": "ショートを測定",
    "open_tip": "方向性結合器の被測定物側の端子にオープンを接続してから押します。TG の範囲を 1MHz ステップで測定します。",
    "short_tip": "オープンを外してショートを接続してから押します。オープンと同じ範囲で測定し、続けて 1 回測定して表示します。",
    # PlutoSDR（FFT の機器は現在の条件で取得する: RBW が違う基準は当たらない。受信の利得を調整してから測定する: 設計Q §7.8）
    "open_tip_pluto": "方向性結合器の被測定物側の端子にオープンを接続してから押します。受信の利得を調整してから、現在の条件で"
                      "測定します。",
}

S11_ERRORS = {
    "format_needs_phase": "この機器は振幅のみを測定します（位相は測定できません）",
    "format_not_yet": "位相の表示は未対応です",
    "ref_level_linear": "|Γ|・SWR の縦軸には REF レベルがありません（下端は固定です）",
    "s11_standard_required": "S11 はオープンかショートを選択して測定します",
}

# 進み具合の行（長い処理: H §4.1）。S11 の基準はどちらの標準器を測っているかを言う
S11_TASK_NAMES = {"open": "オープンを測定中", "short": "ショートを測定中",
                  # PlutoSDR のオープンの前の利得の調整（設計Q §7.8。同じ作業の最初の段）
                  "open_fit": "オープンの測定前に受信の利得を調整中"}

S11_NOTICES = {
    "s11_open_discarded": "測定したオープンを破棄しました。オープンから測定し直してください",
}

# 測ったオープンを捨てた理由（設計E §6.5: オープンとショートは同じ点・同じ条件で測る）
S11_OPEN_DISCARD_REASONS = {
    "mode": "S11 モードの終了",
    "power_cycled": "機器の電源の再投入",
    "conditions": "イメージ除去の変更",
    "axis": "周波数軸の変更",
}

# しきい値との交点の欄（G §3.6 の打てるドロップダウン）の一覧・範囲・桁・初期値（画面の語ではない決まり。Qt と Web が
# 同じ表を使う）。dB 系は REF レベルと同じ一覧（+20〜−120・符号付き・小数1桁）、|Γ|・SWR はその値（例: S11 で SWR 2
# 以下の帯域: 設計G §3.5）
THRESHOLD_SPECS = {
    "dB": {"choices": list(range(20, -121, -10)), "signed": True, "lo": -300.0, "hi": 300.0, "decimals": 1,
           "default": -20.0},
    formats.UNIT_SWR: {"choices": [1.2, 1.5, 2.0, 2.5, 3.0, 5.0, 10.0], "signed": False, "lo": 1.0,
                       "hi": formats.SWR_MAX, "decimals": 2, "default": 2.0},
    formats.UNIT_GAMMA: {"choices": [0.05, 0.1, 0.2, 0.3, 0.5], "signed": False, "lo": 0.0, "hi": 1.0, "decimals": 2,
                         "default": 0.3},
}

# マーカーとマウスの位置の読み値の注意（G §3.3）: SWR の上限に当てた点
SWR_LIMIT_NOTE = f"SWR {formats.SWR_MAX:g} 以上（反射が基準とほぼ同じか大きい）"
