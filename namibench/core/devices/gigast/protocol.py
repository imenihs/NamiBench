"""GigaSt の32バイトの掃引要求と、1回の掃引の時間の見積もり・上限時間（設計B §7.2・設計C §5.5〜§5.7）。

周波数は Hz の int で受け取り、100Hz 単位への変換は要求を組み立てる直前だけで行う（設計B §3）。
範囲外の条件は送らずに InvalidSweep にする（設計B §2 の5: 二重の防御。本来は掃引条件モデルで止まる）。
応答の末尾4バイトは中身が未定義のダミーなので、ここでは扱わない（設計B §3 の0）。
"""
from __future__ import annotations

import math
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from ...types import AdoptedSweep, InvalidSweep, Mode, SweepSegment
from ..base import load_profile

HEADER = 0x55
REQUEST_LEN = 32
TRAILER_LEN = 4
UNIT_HZ = 100  # 機器の周波数の単位（プロトコルの桁）
STEP_UNITS_MAX = 0xFFFF
PLOT_MAX_PROTOCOL = 0xFFFF  # Plot 0 は 65536 点として流れ続けるので 1 以上に限る（T7）

# RBW のコード（180k=0・30k=1・15k=2）。これ以外のコードは送らない（T7: 意味の無い値が返る）
RBW_CODES = {180000: 0, 30000: 1, 15000: 2}

# MODE のビット（設計C §5.7）。b2 は NF の HOT/COLD、b3 はイメージ除去。定義されていないビットは立てない
MODE_TG = 0x01
MODE_SG = 0x02
MODE_B2 = 0x04
MODE_B3 = 0x08
_MODE_BASE = {
    Mode.SA: 0,
    Mode.TG: MODE_TG,
    Mode.S11: MODE_TG,  # 反射は TG を出して外付けの方向性結合器で受ける（設計E §6.5）
    Mode.SG: MODE_SG,
    Mode.NF: 0,  # NF は b2 だけを掃引ごとに切り替える
}

# 無音の上限（設計B §7.2: PC 側で見える最大の空き 244ms に余裕を見た値）
SILENCE_LIMIT_S = 1.0


@dataclass(frozen=True)
class Limits:
    """1回の要求（1掃引）の3つの上限時間（設計B §2 の2・§7.2）。"""

    first_byte_s: float
    silence_s: float
    total_s: float
    # 見積もり（上限ではない。残り時間の表示・記録に使う）
    estimated_s: float


@dataclass(frozen=True)
class DeviceSweep:
    """機器へ送る掃引（前置きを含む）の中身。GigaSt の層の外へは出さない。"""

    start_hz: int
    step_hz: int
    points: int
    sample: int
    sample_inc_units: int
    rbw_hz: int
    mode_bits: int
    sg_hz: int

    @property
    def last_hz(self) -> int:
        return self.start_hz + self.step_hz * (self.points - 1)

    @property
    def expected_bytes(self) -> int:
        """ちょうどこのバイト数のときだけ使う（設計B §3 の0）。"""
        return self.points * 2 + TRAILER_LEN


def _rbw_khz(rbw_hz: int) -> int:
    if rbw_hz not in RBW_CODES:
        raise InvalidSweep(f"未定義の RBW です: {rbw_hz} Hz")
    return rbw_hz // 1000


def sample_inc_units(rbw_hz: int) -> int:
    """Sample 増加周波数（100Hz 単位）= 5 × RBW_kHz（設計C §5.5）。"""
    return 5 * _rbw_khz(rbw_hz)


def sample_for(step_hz: int, rbw_hz: int) -> int:
    """Sample = round(Step_100Hz / (5 × RBW_kHz))（設計C §5.5）。

    Python の round は偶数丸めで、旧アプリの VB の Math.Round と同じ結果になる。
    """
    if step_hz < 0:
        raise InvalidSweep(f"Step が負の値です: {step_hz} Hz")
    return round((step_hz // UNIT_HZ) / sample_inc_units(rbw_hz))


def uses_source(mode: Mode) -> bool:
    """信号源（TG・SG）を出すモードか。1点あたりの時間が SA より長い（T12 の SG と 2026-10-01 の TG の実測）。"""
    return _MODE_BASE[Mode(mode)] & (MODE_TG | MODE_SG) != 0


def timing(profile: dict, step_hz: int, rbw_hz: int, mode: Mode = Mode.SA) -> tuple[float, float]:
    """1掃引の時間の見積もり (初バイトまで[s], 1点あたり[s])（設計B §7.2。係数はプロファイル）。"""
    s = sample_for(step_hz, rbw_hz)
    t = profile["timing"]
    fa, fb = t["first_byte_s"]
    key = "per_point_s_source" if uses_source(mode) and "per_point_s_source" in t else "per_point_s"
    pa, pb = t[key]
    return fa + fb * s, pa + pb * s


def timing_fn(profile: dict) -> Callable[[int, int, Mode], tuple[float, float]]:
    """掃引条件モデルに渡す見積もりの関数 (step_hz, rbw_hz, mode) -> (初バイトまで, 1点あたり)。"""

    def fn(step_hz: int, rbw_hz: int, mode: Mode = Mode.SA) -> tuple[float, float]:
        return timing(profile, step_hz, rbw_hz, mode)

    return fn


def limits(profile: dict, step_hz: int, rbw_hz: int, device_points: int, passes: int = 1,
           mode: Mode = Mode.SA) -> Limits:
    """1回の要求の3つの上限時間（設計B §7.2）。

    - 初バイトまで: 見積もり ×2 ＋ 0.5s
    - 無音: 1s
    - 全体: 見積もりの合計 ×2 ＋ 1s
    上限は要求1回ごとに当てる。passes（イメージ除去・NF の回数）は estimated_s にだけ掛ける。
    """
    first, per_point = timing(profile, step_hz, rbw_hz, mode)
    one = first + device_points * per_point
    return Limits(
        first_byte_s=first * 2 + 0.5,
        silence_s=SILENCE_LIMIT_S,
        total_s=one * 2 + 1.0,
        estimated_s=one * passes,
    )


def _mode_bits(mode: Mode, b3: int, b2: int) -> int:
    if b3 not in (0, 1) or b2 not in (0, 1):
        raise InvalidSweep(f"b3・b2 は 0 か 1 である必要があります: b3={b3} b2={b2}")
    if b2 and mode != Mode.NF:
        raise InvalidSweep("b2（NF の HOT）は NF モードでのみ使用できます")
    return _MODE_BASE[Mode(mode)] | (MODE_B2 if b2 else 0) | (MODE_B3 if b3 else 0)


def device_sweep(adopted: AdoptedSweep, profile: dict, b3: int, b2: int = 0,
                 segment: Optional[SweepSegment] = None) -> DeviceSweep:
    """採用済みの掃引条件から、機器へ送る掃引（前置きを足したもの）を作り、検証する。

    機器の Start = 利用者の Start − 前置き × Step、機器の点数 = 点数 + 前置き（設計C §5.6）。
    segment を渡すと、その区間（axis の点 [point0, point0 + points)）を区間の前置きで送る（設計F §7.2）。
    """
    step = adopted.axis.step_hz
    if segment is None:
        p0, n, pre = 0, adopted.axis.points, adopted.preroll_points
    else:
        p0, n, pre = segment.point0, segment.points, segment.preroll_points
        if p0 < 0 or n < 1 or p0 + n > adopted.axis.points:
            raise InvalidSweep(f"区間が軸の範囲外です: {p0}〜{p0 + n} / {adopted.axis.points}")
    if pre < 0:
        raise InvalidSweep(f"前置きの点数が負の値です: {pre}")
    _check_rbw_rule(adopted, profile)
    if adopted.mode == Mode.SG and adopted.sg_hz is None:
        raise InvalidSweep("SG モードには SG の周波数が必要です")
    return make_device_sweep(
        profile,
        start_hz=adopted.axis.start_hz + (p0 - pre) * step,
        step_hz=step,
        points=n + pre,
        rbw_hz=adopted.rbw_hz,
        mode_bits=_mode_bits(adopted.mode, b3, b2),
        sg_hz=adopted.sg_hz,
    )


def _check_rbw_rule(adopted: AdoptedSweep, profile: dict) -> None:
    """RBW を選べない条件で 180kHz 以外が来たら送らない（プロファイルの rbw の決まり。設計C §5.4）。"""
    r = profile["rbw"]
    if adopted.rbw_hz == r["fixed_hz"]:
        return
    if adopted.mode.value not in r["selectable_modes"]:
        raise InvalidSweep(f"{adopted.mode.value} モードの RBW は {r['fixed_hz']} Hz 固定です")
    if adopted.span_hz > r["selectable_span_max_hz"]:
        raise InvalidSweep(f"Span {adopted.span_hz} Hz では RBW は {r['fixed_hz']} Hz 固定です")


def make_device_sweep(profile: dict, start_hz: int, step_hz: int, points: int, rbw_hz: int,
                      mode_bits: int, sg_hz: Optional[int]) -> DeviceSweep:
    """機器へ送る掃引を検証して作る。範囲外は InvalidSweep（設計B §2 の5）。

    確認の掃引（設計B §7.3）のように、採用済みの掃引条件を経ない要求もここを通す。
    """
    f = profile["freq"]
    for name, v in (("start", start_hz), ("step", step_hz)):
        if not isinstance(v, int) or isinstance(v, bool):
            raise InvalidSweep(f"{name} は int（Hz）である必要があります: {v!r}")
        if v % UNIT_HZ:
            raise InvalidSweep(f"{name} が {UNIT_HZ} Hz の倍数ではありません: {v}")
    if step_hz < 0 or step_hz // UNIT_HZ > STEP_UNITS_MAX:
        raise InvalidSweep(f"Step が範囲外です: {step_hz} Hz")
    if not (1 <= points <= min(f["points_device_max"], PLOT_MAX_PROTOCOL)):
        raise InvalidSweep(f"機器へ送る点数が範囲外です: {points}")
    if not (f["min_hz"] <= start_hz <= f["max_hz"]):
        raise InvalidSweep(f"Start が範囲外です: {start_hz} Hz")
    last = start_hz + step_hz * (points - 1)
    if last > f["max_hz"]:
        raise InvalidSweep(f"最後の点が上限を超えます: {last} Hz")
    if mode_bits & ~(MODE_TG | MODE_SG | MODE_B2 | MODE_B3):
        raise InvalidSweep(f"未定義の MODE ビットです: 0x{mode_bits:02X}")
    if (mode_bits & MODE_TG) and (mode_bits & MODE_SG):
        raise InvalidSweep("TG と SG は同時に使用できません")
    if sg_hz is None:
        sg_hz = 0  # SG を出さないときの SG の欄（機器は SG ビットが無ければ使わない）
    else:
        sg = profile["source"]["sg"]
        if not isinstance(sg_hz, int) or isinstance(sg_hz, bool):
            raise InvalidSweep(f"SG は int（Hz）である必要があります: {sg_hz!r}")
        if not (sg["min_hz"] <= sg_hz <= sg["max_hz"]):
            raise InvalidSweep(f"SG が範囲外です: {sg_hz} Hz")
        if sg_hz % sg["unit_hz"]:
            raise InvalidSweep(f"SG が {sg['unit_hz']} Hz の倍数ではありません: {sg_hz}")
    if (mode_bits & MODE_SG) and sg_hz == 0:
        raise InvalidSweep("SG ビットを立てるときは SG の周波数が必要です")
    return DeviceSweep(
        start_hz=start_hz,
        step_hz=step_hz,
        points=points,
        sample=sample_for(step_hz, rbw_hz),
        sample_inc_units=sample_inc_units(rbw_hz),
        rbw_hz=rbw_hz,
        mode_bits=mode_bits,
        sg_hz=sg_hz,
    )


def pack(ds: DeviceSweep) -> bytes:
    """32バイトに詰める（リトルエンディアン。19〜31 は 0）。

    START は 31bit でマスクする（負の周波数はこの形で正しく通る: T4）。
    """
    start_units = (ds.start_hz // UNIT_HZ) & 0x7FFFFFFF
    body = struct.pack(
        "<BIHHHHBBI",
        HEADER,
        start_units,
        ds.step_hz // UNIT_HZ,
        ds.points,
        ds.sample,
        ds.sample_inc_units,
        RBW_CODES[ds.rbw_hz],
        ds.mode_bits,
        ds.sg_hz // UNIT_HZ,
    )
    return body + bytes(REQUEST_LEN - len(body))


def unpack(req: bytes) -> DeviceSweep:
    """32バイトを読み戻す（シミュレータ・記録の読み取り用）。"""
    if len(req) != REQUEST_LEN or req[0] != HEADER:
        raise ValueError("32バイトの掃引要求ではない")
    _, start_u, step_u, plot, sample, inc, rbw_code, mode, sg_u = struct.unpack("<BIHHHHBBI", req[:19])
    if start_u & 0x40000000:  # 31bit の負の値
        start_u -= 0x80000000
    rbw_hz = {v: k for k, v in RBW_CODES.items()}.get(rbw_code, -1)
    return DeviceSweep(start_u * UNIT_HZ, step_u * UNIT_HZ, plot, sample, inc, rbw_hz, mode, sg_u * UNIT_HZ)


def build_request(adopted: AdoptedSweep, b3: int, b2: int = 0, profile: Optional[dict] = None) -> bytes:
    """採用済みの掃引条件から32バイトの要求を作る（前置きを含む。範囲外は InvalidSweep）。"""
    if profile is None:
        profile = default_profile()
    return pack(device_sweep(adopted, profile, b3, b2))


_DEFAULT_PROFILE: Optional[dict] = None


def default_profile() -> dict:
    """同梱の GigaSt v5 の機種のプロファイル（読むのは1回だけ。書き換えないこと）。"""
    global _DEFAULT_PROFILE
    if _DEFAULT_PROFILE is None:
        _DEFAULT_PROFILE = load_profile(Path(__file__).with_name("profile.json"))
    return _DEFAULT_PROFILE


def preroll_points_for(profile: dict, step_hz: int, rbw_hz: int, mode: Mode = Mode.SA) -> int:
    """前置きの点数 N_pre = ceil(過渡の時間 / 1点あたりの時間)（設計C §5.6。掃引条件モデル・試験用）。"""
    _, per_point = timing(profile, step_hz, rbw_hz, mode)
    return math.ceil(profile["sweep_quirks"]["transient_s"] / per_point)


def timing_mode(ds: DeviceSweep) -> Mode:
    """送る掃引の MODE から、時間の見積もりに使うモード（信号源を出すか）を決める。"""
    return Mode.TG if ds.mode_bits & (MODE_TG | MODE_SG) else Mode.SA
