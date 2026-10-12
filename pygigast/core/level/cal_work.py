"""校正作業の計算と、作業中の値（設計K §4.1〜§4.5）。

補正値は利用者に足し引きさせず、ここで計算する（設計K §2・M-10）。スレッド・入出力を持たない純粋な計算だけ
（測る掃引の段取りは instrument_cal.py）。

- 周波数特性と全体のレベル（K §4.2）: 0〜12000MHz の 100MHz おき 121 周波数。表の欄ごとの「当てる補正」（全体の
  レベル＋その欄の補正）を測り、500MHz の欄を全体のレベル、ほかの欄はそこからの差として持つ。測っていない・飛ばした欄は
  前の値のまま（当てる補正が変わらないように持ち直す）。band の境目（1000・2000…11000MHz）は欄が2つ（下の band の
  最後・上の band の最初）あり、値も違う。境目ちょうどのトーンの山は下の band の側に出るので、境目は2行に分け、下の
  band の欄は境目の 0.5MHz 下、上の band の欄は 0.5MHz 上で測って、それぞれその欄だけに入れる（K §4.2.1）
- RBW（K §4.3）: 180kHz を基準に、30・15kHz のパワー平均のピークの差を補正にする
- NF の増幅度（K §4.4）: 6dB のアッテネータの有無の HOT−COLD の差が 6.0dB になる値（旧アプリと同じ意味の amp_raw）
- 旧ファイル（K §4.5・設計D §5.6）: 取り込む値を、校正作業で測った値と比べて大きく違う所に印を付ける
- PlutoSDR の利得の補正（設計Q §7.6）: 周波数ごとの利得の補正の列（level/pluto_gain.py）。GigaSt の校正データとは
  別の表（PlutoSDR の個体ごと）なので、前の値・測った列・保存するものを別に持つ
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

import numpy as np

from . import pluto_gain as pg
from .calibration import FREQ_STEP_HZ, POINTS_PER_BAND, Calibration, CalibrationSource, FreqBand, NfCalibration
from .legacy_cal import HF_BANDS

# ---------------------------------------------------------------- 何を校正するか（K §4.1 の 1）

KIND_FREQ = "freq"  # 周波数特性と全体のレベル
KIND_RBW = "rbw"
KIND_NF = "nf"  # NF の増幅度
KIND_LEGACY = "legacy"  # 旧校正ファイルの取り込み（K §4.5）
KIND_PLUTO_GAIN = "pluto_gain"  # PlutoSDR の利得の補正（設計Q §7.6）
KINDS = (KIND_FREQ, KIND_RBW, KIND_NF, KIND_PLUTO_GAIN, KIND_LEGACY)

# 校正作業で作った校正データの取得方法（設計D §4 の「校正作業」）
SOURCE_KIND = "calibration_work"
SOURCE_LEGACY = "legacy_file"

# ---------------------------------------------------------------- 周波数特性（K §4.2）

GRID_STEP_HZ = FREQ_STEP_HZ
BAND_SPAN_HZ = FREQ_STEP_HZ * (POINTS_PER_BAND - 1)
GRID_POINTS = HF_BANDS * (POINTS_PER_BAND - 1) + 1  # 0〜12000MHz の 121 周波数（点の一覧の行は grid_rows: 境目は2行）
# 0MHz の点は旧アプリと同じく 10MHz で測る（局発の漏れの外）
ZERO_POINT_MEASURE_HZ = 10_000_000
# band の境目の欄を測る位置（境目からのずれ）。境目ちょうどのトーンの山は下の band の側に出て、上の band の欄の値が
# 読めない（K §4.2.1）
EDGE_OFFSET_HZ = 500_000
# 境目の2行のどちらの band の欄か
SIDE_LOWER = "lower"
SIDE_UPPER = "upper"
# 全体のレベルにする点（旧 Common-Bias。旧アプリの手順の「SG=500MHz」）
LEVEL_POINT_HZ = 500_000_000
# 1点を測る掃引: Span 1MHz・RBW 180kHz・30回のパワー平均（T12: 1回のピークは偏る）・イメージ除去なし（b3=1）
SPAN_HZ = 1_000_000
FREQ_RBW_HZ = 180_000
AVERAGES = 30
B3_BASIS = 1
# ピークがノイズフロアからこれ未満なら「信号が見つからない」（K §4.2。仮の値: 設計ドラフトの未決）
PEAK_ABOVE_FLOOR_DB = 20.0
# いまの補正との差がこれを超えたら、入れる前に確かめる（K §4.2。仮の値 ±6dB）
CONFIRM_DIFF_DB = 6.0
SG_LEVEL_DEFAULT_DBM = -10.0
CABLE_LOSS_DEFAULT_DB = 0.0
# 入れられる範囲（設計に無い。外部 SG が普通に出す範囲と、ケーブル・アッテネータの損失として置いた値）
SG_LEVEL_RANGE_DBM = (-80.0, 10.0)
CABLE_LOSS_RANGE_DB = (0.0, 30.0)

# ---------------------------------------------------------------- RBW（K §4.3）

RBW_BASE_HZ = 180_000
RBW_LIST_HZ = (180_000, 30_000, 15_000)
RBW_FREQ_HZ = 500_000_000
# 周波数による差を確かめるために、選べば測る周波数（補正の値は 500MHz で決める）
RBW_EXTRA_HZ = (100_000_000, 1_500_000_000, 3_000_000_000)

# ---------------------------------------------------------------- NF の増幅度（K §4.4）

NF_ATT_DB = 6.0
# HOT と COLD の差がこれ未満ならノイズ源の ON・OFF が効いていない（M §1 の「差が 0.5dB 未満」）
NF_MIN_Y_DB = 0.5
NF_FREQ_DEFAULT_HZ = 1_000_000_000
NF_FREQ_RANGE_HZ = (1_000_000, 12_000_000_000)
NF_WITH_ATT = "with_att"
NF_WITHOUT_ATT = "without_att"
NF_STATES = (NF_WITH_ATT, NF_WITHOUT_ATT)

# ---------------------------------------------------------------- 確かめるときの「差が大きい」（K §4.1 の 3・設計D §5.6）

# 項目の種類 → dB（NF の増幅度は amp_raw の値）。周波数特性は K §4.2 の確かめる閾値と同じ。RBW は 1 回のピークの
# 偏り（約 2dB: T12）を見つけられる幅。NF の増幅度は、6dB の差の読みが 0.5dB 違う大きさ（200·log10(6.5/6) ≈ 7）
DIFF_LIMIT = {"freq": CONFIRM_DIFF_DB, "level": CONFIRM_DIFF_DB, "rbw": 1.0, "nf_amp": 7.0, "nf_enr": 1.0}

# 状態の点の状況
POINT_NONE = "none"
POINT_MEASURED = "measured"
POINT_SKIPPED = "skipped"


def grid_freqs() -> list[int]:
    return [i * GRID_STEP_HZ for i in range(GRID_POINTS)]


def measure_freq(freq_hz: int, side: Optional[str] = None) -> int:
    """その行を測る周波数（SG に出させる周波数）。0MHz の点は 10MHz、band の境目の行は境目の 0.5MHz 下・上
    （K §4.2.1）。"""
    if side == SIDE_LOWER:
        return int(freq_hz) - EDGE_OFFSET_HZ
    if side == SIDE_UPPER:
        return int(freq_hz) + EDGE_OFFSET_HZ
    return ZERO_POINT_MEASURE_HZ if freq_hz == 0 else int(freq_hz)


@dataclass(frozen=True)
class GridRow:
    """校正の点の一覧の1行: 表の欄1つ（band, k）と、それを測る周波数。"""
    freq_hz: int  # 表の周波数（100MHz おき）
    band: int
    k: int
    side: Optional[str]  # band の境目の行は SIDE_LOWER / SIDE_UPPER、ほかは None
    measure_hz: int

    @property
    def cell(self) -> tuple[int, int]:
        return self.band, self.k

    @property
    def item(self) -> str:
        """記録と比べる項目の名前。境目の行は欄の側を付ける（2行が別の値を持つ）。"""
        return f"freq:{self.freq_hz}" if self.side is None else f"freq:{self.freq_hz}:{self.side}"


def grid_rows() -> list[GridRow]:
    """校正の点の一覧（K §4.2・§4.2.1）。band の境目は下の band の欄・上の band の欄の2行（この順）。"""
    out = []
    for f in grid_freqs():
        b, k = point_cell(f)
        if k == 0 and b > 0:
            out.append(GridRow(f, b - 1, POINTS_PER_BAND - 1, SIDE_LOWER, measure_freq(f, SIDE_LOWER)))
            out.append(GridRow(f, b, 0, SIDE_UPPER, measure_freq(f, SIDE_UPPER)))
        else:
            out.append(GridRow(f, b, k, None, measure_freq(f)))
    return out


def item_cell(item: str) -> tuple[int, int]:
    """項目 "freq:<Hz>"・"freq:<Hz>:lower|upper" の欄。側が無い境目は上の band の欄（換算と同じ: point_cell）。"""
    parts = item.split(":")
    f = int(parts[1])
    side = parts[2] if len(parts) > 2 else None
    if side == SIDE_LOWER:
        b, k = point_cell(f)
        if k != 0 or b == 0:
            raise ValueError(f"境目でない周波数: {item}")
        return b - 1, POINTS_PER_BAND - 1
    if side not in (None, SIDE_UPPER):
        raise ValueError(f"知らない項目: {item}")
    return point_cell(f)


def point_cell(freq_hz: int) -> tuple[int, int]:
    """その点の値として見せる欄。band の境目では次の band の最初の欄（換算と同じ: 設計D §5.2）。
    12000MHz は最後の band の最後の欄（その周波数ちょうどは表の外で補正 0 だが、表の値はこの欄にある）。"""
    b = min(int(freq_hz) // BAND_SPAN_HZ, HF_BANDS - 1)
    return b, (int(freq_hz) - b * BAND_SPAN_HZ) // GRID_STEP_HZ


def _grid_ok(cal: Calibration) -> bool:
    return cal.freq_step_hz == FREQ_STEP_HZ and len(cal.bands) == HF_BANDS


def totals_grid(cal: Optional[Calibration]) -> np.ndarray:
    """欄ごとの「当てる補正」（全体のレベル＋周波数特性）。校正データが無ければ 0（補正なし: 設計D §5.5）。"""
    if cal is None or not _grid_ok(cal):
        return np.zeros((HF_BANDS, POINTS_PER_BAND), dtype=np.float64)
    return np.array([b.values for b in cal.bands], dtype=np.float64) + float(cal.level_offset_db)


def total_at(cal: Optional[Calibration], freq_hz: int) -> float:
    """その周波数の欄（境目は上の band の欄）の当てる補正（dB）。"""
    return cell_total(cal, point_cell(freq_hz))


def cell_total(cal: Optional[Calibration], cell: tuple[int, int]) -> float:
    """その欄の当てる補正（dB）。"""
    return float(totals_grid(cal)[cell])


def correction_from_peak(sg_level_dbm: float, cable_loss_db: float, peak_dbm: float) -> float:
    """補正値 =（SG のレベル − ケーブルの損失）−（補正なしの測定値）（K §4.2）。"""
    return (float(sg_level_dbm) - float(cable_loss_db)) - float(peak_dbm)


def rbw_offsets(peaks_dbm: dict[int, float]) -> dict[int, float]:
    """RBW ごとの補正 = 180kHz のピーク − その RBW のピーク（その RBW の読みに足すと 180kHz の読みになる）。"""
    base = float(peaks_dbm[RBW_BASE_HZ])
    return {int(r): base - float(v) for r, v in peaks_dbm.items() if int(r) != RBW_BASE_HZ}


def nf_amp_raw(diff_db: float) -> float:
    """6dB のアッテネータの有無の HOT−COLD の差（倍率なし）から、差が 6.0dB になる amp_raw（倍率 10^(値/200)）。"""
    if not (diff_db > 0.0):
        raise ValueError("差は正であること")
    return 200.0 * math.log10(NF_ATT_DB / float(diff_db))


def _r(x: float) -> float:
    """保存する dB は 0.01dB に丸める（-0.0 を書かない）。"""
    return round(float(x), 2) + 0.0


def build(
    base: Optional[Calibration],
    *,
    model: str,
    serial: str,
    created_at: str,
    source: CalibrationSource,
    totals: Optional[dict[tuple[int, int], float]] = None,
    rbw: Optional[dict[int, float]] = None,
    nf_amp: Optional[float] = None,
    work: Optional[dict] = None,
) -> Calibration:
    """前の校正に、測った値を差し替えた新しい校正データ（K §2: やり直した点だけ差し替える）。

    totals は 欄（band, k）→ 当てる補正（その欄だけに入れる。band の境目の2つの欄は別々に測る: K §4.2.1）。
    500MHz を測ったときだけ、その値を全体のレベルに
    して、ほかの欄はそこからの差として持ち直す（測っていない欄の当てる補正は変わらない）。500MHz を測っていないときは
    全体のレベルを前の値のままにする: 全体のレベルは表の外（TG・S11 と表の上端より上の周波数）にも効くので、
    500MHz の欄の差の分だけ動かすと、測っていない所の値が変わる（RBW・NF だけの校正でも変わってしまう）
    """
    grid = totals_grid(base)
    for (b, k), t in (totals or {}).items():
        grid[int(b), int(k)] = float(t)
    # 当てる補正を先に 0.01dB に丸めてから分ける（全体のレベルと差を別々に丸めると、足した値が測った値から
    # 0.01dB ずれる）
    grid = np.round(grid, 2)
    lb, lk = point_cell(LEVEL_POINT_HZ)
    if (lb, lk) in (totals or {}):
        level = _r(grid[lb, lk])
    else:
        level = _r(base.level_offset_db) if base is not None else 0.0
    values = np.round(grid - level, 2)
    rbw_off = dict(base.rbw_offset_db) if base is not None else {RBW_BASE_HZ: 0.0}
    for r, v in (rbw or {}).items():
        rbw_off[int(r)] = _r(v)
    rbw_off.setdefault(RBW_BASE_HZ, 0.0)
    nf = base.nf if base is not None else None
    if nf_amp is not None:
        nf = NfCalibration(enr_db=nf.enr_db if nf is not None else None, amp_raw=_r(nf_amp))
    return Calibration(
        model=model,
        serial=serial,
        created_at=created_at,
        source=source,
        b3_basis=B3_BASIS,
        level_offset_db=_r(level),
        rbw_offset_db=rbw_off,
        freq_step_hz=FREQ_STEP_HZ,
        bands=tuple(FreqBand(start_hz=i * BAND_SPAN_HZ, values=tuple(_r(v) for v in values[i]))
                    for i in range(HF_BANDS)),
        nf=nf,
        work=work,
    )


# ---------------------------------------------------------------- 比べる（K §4.1 の 3・設計D §5.6）

def item_value(cal: Optional[Calibration], item: str) -> Optional[float]:
    """項目（"level"・"rbw:30000"・"nf_amp"・"nf_enr"・"freq:<Hz>[:lower|upper]"）の値。無ければ None。"""
    if item.startswith("freq:"):
        return cell_total(cal, item_cell(item)) if cal is not None else None
    if cal is None:
        return None
    if item == "level":
        return float(cal.level_offset_db)
    if item.startswith("rbw:"):
        v = cal.rbw_offset_db.get(int(item[4:]))
        return None if v is None else float(v)
    if item == "nf_amp":
        return None if cal.nf is None else float(cal.nf.amp_raw)
    if item == "nf_enr":
        return None if cal.nf is None or cal.nf.enr_db is None else float(cal.nf.enr_db)
    raise ValueError(f"知らない項目: {item}")


def item_kind(item: str) -> str:
    """差の閾値の種類（DIFF_LIMIT のキー）。"""
    if item.startswith("freq:"):
        return "freq"
    if item.startswith("rbw:"):
        return "rbw"
    return item


def is_big(item: str, before: Optional[float], after: Optional[float]) -> bool:
    if before is None or after is None:
        return False
    return abs(after - before) > DIFF_LIMIT[item_kind(item)] + 1e-9


def measured_items(history: list[Calibration]) -> dict[str, float]:
    """校正作業で測った値の最新（項目 → 値）。古いものから並んだ履歴の新しい方から拾う（設計D §5.6 の「実測」）。"""
    out: dict[str, float] = {}
    for cal in reversed(history):
        if cal.source.kind != SOURCE_KIND or not isinstance(cal.work, dict):
            continue
        for m in cal.work.get("measured") or []:
            try:
                out.setdefault(str(m["item"]), float(m["after"]))
            except (KeyError, TypeError, ValueError):
                continue
    return out


# 種類ごとに確かめる画面へ並べる項目（周波数特性の点の一覧は points で別に並べる）
ITEMS_OF_KIND = {
    KIND_FREQ: ("level",),
    KIND_RBW: ("rbw:30000", "rbw:15000"),
    KIND_NF: ("nf_amp",),
    KIND_LEGACY: ("level", "rbw:30000", "rbw:15000", "nf_enr", "nf_amp"),
    KIND_PLUTO_GAIN: (),  # 利得の補正の表は GigaSt の校正データの項目を持たない（pluto の欄で別に並べる）
}

# PlutoSDR の利得の補正の確かめる表の項目（表の中の、絶対値がいちばん大きい補正）
PLUTO_ITEM = "pluto_gain_max"


# ---------------------------------------------------------------- 作業中の値

@dataclass
class Point:
    row: GridRow  # 表の欄と測る周波数
    status: str = POINT_NONE
    total_db: Optional[float] = None  # 測った当てる補正
    peak_dbm: Optional[float] = None  # 補正なしの測定値（30回のパワー平均のピーク）
    at: Optional[str] = None
    # 測ったときの SG のレベル・ケーブルの損失（途中で変えても、その点はそのときの値で計算した）
    sg_level_dbm: Optional[float] = None
    cable_loss_db: Optional[float] = None

    @property
    def freq_hz(self) -> int:
        return self.row.freq_hz

    @property
    def measure_hz(self) -> int:
        return self.row.measure_hz


class CalSession:
    """校正の画面1つ分の作業（K §4.1）。保存するまで校正データは何も変わらない。

    base は作業を始めたときの校正（「前の値」）。measured は校正作業で測った値の最新（旧ファイルの警告に使う）。
    """

    def __init__(self, kind: str, model: Optional[str], serial: Optional[str], base: Optional[Calibration],
                 base_file: Optional[str], measured: Optional[dict[str, float]] = None) -> None:
        if kind not in KINDS:
            raise ValueError(f"知らない種類: {kind}")
        self.kind = kind
        self.sg_level_dbm = SG_LEVEL_DEFAULT_DBM
        self.cable_loss_db = CABLE_LOSS_DEFAULT_DB
        self.rbw_extra = False
        self.nf_freq_hz = NF_FREQ_DEFAULT_HZ
        self.ext_atten_db = pg.EXT_ATTEN_DEFAULT_DB
        self.saved: Optional[dict] = None
        # PlutoSDR の利得の補正: つないでいる PlutoSDR（model, serial）・その前の表・測る周波数
        self.pluto_model: Optional[str] = None
        self.pluto_serial: Optional[str] = None
        self.pluto_base: Optional[pg.GainCorrection] = None
        self.pluto_grid: list[int] = pg.grid_freqs()
        self.rebase(model, serial, base, base_file, measured)

    # ---- 前の値と、作業の値を空にする

    def rebase(self, model: Optional[str], serial: Optional[str], base: Optional[Calibration],
               base_file: Optional[str], measured: Optional[dict[str, float]] = None) -> None:
        self.model = model
        self.serial = serial
        self.base = base
        self.base_file = base_file
        self.measured = dict(measured or {})
        self.clear()

    def clear(self) -> None:
        self.points = [Point(r) for r in grid_rows()]
        self.current = 0
        self.pending: Optional[dict] = None
        self.message: Optional[dict] = None
        self.rbw_result: Optional[list[dict]] = None
        self.nf_y: dict[str, Optional[float]] = {s: None for s in NF_STATES}
        self.legacy: Optional[dict] = None
        self.pluto_rows: dict[int, pg.GainRow] = {}  # 周波数 → 測った列
        self.pluto_meta: dict[int, dict] = {}  # 周波数 → 測ったときの記録（受信の LO・使わなかった利得）

    def set_pluto(self, model: Optional[str], serial: Optional[str], base: Optional[pg.GainCorrection],
                  grid: Optional[list[int]] = None) -> None:
        """つないでいる PlutoSDR とその前の表（利得の補正: 設計Q §7.6）。別の機器に変わったら測った列を捨てる。"""
        if serial != self.pluto_serial:
            self.pluto_rows, self.pluto_meta = {}, {}
        self.pluto_model, self.pluto_serial, self.pluto_base = model, serial, base
        if grid is not None:
            self.pluto_grid = [int(f) for f in grid]

    def add_pluto_row(self, row: pg.GainRow, meta: dict) -> None:
        self.pluto_rows[int(row.freq_hz)] = row
        self.pluto_meta[int(row.freq_hz)] = dict(meta)
        self.message = None

    def set_kind(self, kind: str) -> None:
        if kind not in KINDS:
            raise ValueError(f"知らない種類: {kind}")
        self.kind = kind
        self.saved = None
        self.clear()

    # ---- 保存していない作業

    def unsaved(self) -> int:
        """閉じると失う作業の数（U-18）。周波数特性は測った点の数、ほかは測った（読み込んだ）なら 1。"""
        if self.kind == KIND_FREQ:
            return sum(1 for p in self.points if p.status == POINT_MEASURED) + (1 if self.pending else 0)
        if self.kind == KIND_RBW:
            return 1 if self.rbw_result else 0
        if self.kind == KIND_NF:
            return 1 if any(v is not None for v in self.nf_y.values()) else 0
        if self.kind == KIND_PLUTO_GAIN:
            return len(self.pluto_rows)
        return 1 if self.legacy else 0

    def savable(self) -> bool:
        if self.kind == KIND_PLUTO_GAIN:
            return self.pluto_serial is not None and bool(self.pluto_rows)
        if self.serial is None or self.model is None:
            return False
        if self.kind == KIND_FREQ:
            return any(p.status == POINT_MEASURED for p in self.points)
        if self.kind == KIND_RBW:
            return bool(self.rbw_result)
        if self.kind == KIND_NF:
            return self.nf_amp() is not None
        return self.legacy is not None

    # ---- 周波数特性の点

    def select(self, index: int) -> None:
        if not (0 <= index < len(self.points)):
            raise ValueError("点の番号が範囲の外")
        self.current = index
        self.message = None

    def _advance(self) -> None:
        """次の点へ進む: いまの点の後ろでまだ測っていない（飛ばしていない）点。無ければ前から探す。"""
        n = len(self.points)
        for step in range(1, n + 1):
            i = (self.current + step) % n
            if self.points[i].status == POINT_NONE:
                self.current = i
                return

    def point_result(self, index: int, peak_dbm: float, at: str) -> bool:
        """1点を測った結果。補正値を計算し、いまの補正との差が大きければ確かめ待ちにする（True を返す）。"""
        p = self.points[index]
        total = correction_from_peak(self.sg_level_dbm, self.cable_loss_db, peak_dbm)
        before = cell_total(self.base, p.row.cell)
        self.message = None
        cond = (self.sg_level_dbm, self.cable_loss_db)
        if self.base is not None and abs(total - before) > CONFIRM_DIFF_DB:
            self.pending = {"index": index, "total_db": total, "peak_dbm": float(peak_dbm), "at": at,
                            "before_db": before, "diff_db": total - before, "cond": cond}
            return True
        self._commit(index, total, peak_dbm, at, cond)
        return False

    def _commit(self, index: int, total: float, peak_dbm: float, at: str, cond: tuple) -> None:
        p = self.points[index]
        p.status, p.total_db, p.peak_dbm, p.at = POINT_MEASURED, float(total), float(peak_dbm), at
        p.sg_level_dbm, p.cable_loss_db = cond
        self.current = index
        self._advance()

    def confirm(self, accept: bool) -> None:
        pend = self.pending
        if pend is None:
            raise ValueError("確かめ待ちの点が無い")
        self.pending = None
        if accept:
            self._commit(pend["index"], pend["total_db"], pend["peak_dbm"], pend["at"], pend["cond"])

    def skip(self) -> None:
        """いまの点を飛ばす（前の値のまま。印を付ける: K §4.2）。"""
        p = self.points[self.current]
        p.status, p.total_db, p.peak_dbm, p.at = POINT_SKIPPED, None, None, None
        p.sg_level_dbm = p.cable_loss_db = None
        self.message = None
        self._advance()

    # ---- RBW・NF・旧ファイル

    def set_rbw(self, freqs: list[dict]) -> None:
        """freqs: [{"freq_hz", "peaks_dbm": {RBW: dBm}}]。補正は 500MHz で決める（ほかの周波数は確かめる用）。"""
        self.rbw_result = [{"freq_hz": int(f["freq_hz"]), "peaks_dbm": {int(r): float(v) for r, v in f["peaks_dbm"].items()}}
                           for f in freqs]
        self.message = None

    def rbw_new(self) -> Optional[dict[int, float]]:
        for f in self.rbw_result or []:
            if f["freq_hz"] == RBW_FREQ_HZ:
                return rbw_offsets(f["peaks_dbm"])
        return None

    def set_nf(self, state: str, y_db: float) -> None:
        if state not in NF_STATES:
            raise ValueError(f"知らない状態: {state}")
        self.nf_y[state] = float(y_db)
        self.message = None

    def nf_diff(self) -> Optional[float]:
        a, b = self.nf_y[NF_WITHOUT_ATT], self.nf_y[NF_WITH_ATT]
        return None if a is None or b is None else a - b

    def nf_amp(self) -> Optional[float]:
        d = self.nf_diff()
        return nf_amp_raw(d) if d is not None and d > 0 else None

    def set_legacy(self, cal: Calibration, file_name: str, sha256: str) -> None:
        self.legacy = {"cal": cal, "file_name": file_name, "sha256": sha256}
        self.message = None

    # ---- 新しい校正データ

    def _previous(self) -> Optional[dict]:
        if self.base is None:
            return None
        return {"file": self.base_file, "created_at": self.base.created_at, "source": self.base.source.kind}

    def pluto_candidate(self, created_at: str) -> Optional[pg.GainCorrection]:
        """保存すると足す PlutoSDR の利得の補正の表。測っていない周波数は前の表の列のまま（K §2: やり直した所だけ
        差し替える）。測った列が無ければ None。"""
        if self.kind != KIND_PLUTO_GAIN or not self.savable():
            return None
        base = self.pluto_base
        rows = [self.pluto_rows[f] for f in sorted(self.pluto_rows)]
        work = {
            "kind": KIND_PLUTO_GAIN,
            "previous": None if base is None else {"created_at": base.created_at},
            "ext_atten_db": self.ext_atten_db,
            "measured": [{"freq_hz": r.freq_hz, "ref_gain_db": r.ref_gain_db,
                          "before_db": _r(pg.extreme_at(base, r.freq_hz)), "after_db": _r(r.extreme()),
                          **self.pluto_meta.get(r.freq_hz, {})} for r in rows],
            "not_measured_hz": [f for f in self.pluto_grid if f not in self.pluto_rows],
        }
        return pg.GainCorrection(model=str(self.pluto_model), serial=str(self.pluto_serial), created_at=created_at,
                                 source={"kind": pg.SOURCE_KIND}, rows=pg.merged_rows(base, rows), work=work)

    def candidate(self, created_at: str) -> Optional[Calibration]:
        """保存すると足す校正データ（保存していなければ何も変わらない: K §4.1 の 4）。作れなければ None。"""
        if not self.savable() or self.kind == KIND_PLUTO_GAIN:
            return None
        base = self.base
        common = {"model": self.model, "serial": self.serial, "created_at": created_at}
        work: dict = {"kind": self.kind, "previous": self._previous()}
        if self.kind == KIND_LEGACY:
            lc: Calibration = self.legacy["cal"]
            return Calibration(model=self.model, serial=self.serial, created_at=created_at,
                               source=CalibrationSource(kind=SOURCE_LEGACY, file_name=self.legacy["file_name"],
                                                        sha256=self.legacy["sha256"]),
                               b3_basis=lc.b3_basis, level_offset_db=lc.level_offset_db,
                               rbw_offset_db=dict(lc.rbw_offset_db), freq_step_hz=lc.freq_step_hz, bands=lc.bands,
                               nf=lc.nf, work=work)
        src = CalibrationSource(kind=SOURCE_KIND)
        if self.kind == KIND_FREQ:
            done = [p for p in self.points if p.status == POINT_MEASURED]
            work["measured"] = [{"item": p.row.item, "before": _r(cell_total(base, p.row.cell)),
                                 "after": _r(p.total_db), "peak_dbm": _r(p.peak_dbm), "at": p.at,
                                 "measure_hz": p.measure_hz,
                                 "sg_level_dbm": p.sg_level_dbm, "cable_loss_db": p.cable_loss_db} for p in done]
            work["skipped"] = [p.row.item for p in self.points if p.status == POINT_SKIPPED]
            work["conditions"] = {"span_hz": SPAN_HZ, "rbw_hz": FREQ_RBW_HZ, "averages": AVERAGES, "b3": B3_BASIS}
            return build(base, source=src, totals={p.row.cell: p.total_db for p in done}, work=work, **common)
        if self.kind == KIND_RBW:
            new = self.rbw_new() or {}
            work["measured"] = [{"item": f"rbw:{r}", "before": item_value(base, f"rbw:{r}"), "after": _r(v)}
                                for r, v in sorted(new.items(), reverse=True)]
            work["rbw"] = [{"freq_hz": f["freq_hz"], "peaks_dbm": {str(r): _r(v) for r, v in f["peaks_dbm"].items()}}
                           for f in self.rbw_result or []]
            work["conditions"] = {"span_hz": SPAN_HZ, "averages": AVERAGES, "b3": B3_BASIS}
            return build(base, source=src, rbw=new, work=work, **common)
        amp = self.nf_amp()
        work["measured"] = [{"item": "nf_amp", "before": item_value(base, "nf_amp"), "after": _r(amp)}]
        work["nf"] = {"freq_hz": self.nf_freq_hz, "with_att_db": _r(self.nf_y[NF_WITH_ATT]),
                      "without_att_db": _r(self.nf_y[NF_WITHOUT_ATT]), "diff_db": _r(self.nf_diff()),
                      "span_hz": SPAN_HZ, "averages": AVERAGES}
        return build(base, source=src, nf_amp=amp, work=work, **common)

    def after_save(self, cal, file_name: str, measured: Optional[dict[str, float]] = None) -> None:
        """保存した。保存したものが次の作業の「前の値」になる。"""
        if isinstance(cal, pg.GainCorrection):
            self.pluto_base = cal
            self.saved = {"file": file_name, "created_at": cal.created_at, "kind": self.kind}
            self.clear()
            return
        self.base = cal
        self.base_file = file_name
        if measured is not None:
            self.measured = dict(measured)
        self.saved = {"file": file_name, "created_at": cal.created_at, "kind": self.kind}
        self.clear()

    # ---- 状態（画面へ）

    def snapshot(self, task_id: Optional[str] = None, device_reason: Optional[str] = None) -> dict:
        """画面へ渡す作業の中身。値は数のまま（文言は画面: U-22）。"""
        cand = self.candidate("") if self.savable() else None
        show_points = self.kind in (KIND_FREQ, KIND_LEGACY)
        legacy_cal = self.legacy["cal"] if self.legacy else None
        after_cal = legacy_cal if self.kind == KIND_LEGACY else cand
        points = []
        if show_points:
            bg = totals_grid(self.base)
            ag = totals_grid(after_cal) if after_cal is not None else bg
            for i, p in enumerate(self.points):
                item = p.row.item
                cell = p.row.cell
                before, after = float(bg[cell]), float(ag[cell])
                row = {"index": i, "freq_hz": p.freq_hz, "side": p.row.side, "measure_hz": p.measure_hz,
                       "status": p.status,
                       "before_db": before, "after_db": after, "big": is_big(item, before, after)}
                if self.kind == KIND_LEGACY:
                    row.update(self._legacy_check(item, after if legacy_cal is not None else None))
                points.append(row)
        items = []
        for item in ITEMS_OF_KIND[self.kind]:
            before = item_value(self.base, item)
            after = item_value(after_cal, item) if after_cal is not None else before
            row = {"item": item, "before": before, "after": after, "big": is_big(item, before, after)}
            if self.kind == KIND_LEGACY:
                row.update(self._legacy_check(item, after if legacy_cal is not None else None))
            items.append(row)
        base = None
        if self.base is not None:
            base = {"created_at": self.base.created_at, "file": self.base_file,
                    "source": {"kind": self.base.source.kind, "file_name": self.base.source.file_name}}
        pluto = self.kind == KIND_PLUTO_GAIN
        if pluto:
            pb = self.pluto_base
            base = None if pb is None else {"created_at": pb.created_at, "file": None,
                                            "source": {"kind": str(pb.source.get("kind")), "file_name": None}}
            items = self._pluto_items()
        nf_diff = self.nf_diff()
        return {
            "kind": self.kind,
            "model": self.pluto_model if pluto else self.model,
            "serial": self.pluto_serial if pluto else self.serial,
            "device_reason": device_reason,
            "base": base,
            "inputs": {"sg_level_dbm": self.sg_level_dbm, "cable_loss_db": self.cable_loss_db,
                       "rbw_extra": self.rbw_extra, "nf_freq_hz": self.nf_freq_hz,
                       "ext_atten_db": self.ext_atten_db},
            "current": self.current,
            "points": points,
            "items": items,
            "rbw": None if not self.rbw_result else [
                {"freq_hz": f["freq_hz"], "peaks_dbm": {str(r): v for r, v in f["peaks_dbm"].items()},
                 "offsets_db": {str(r): v for r, v in rbw_offsets(f["peaks_dbm"]).items()}}
                for f in self.rbw_result],
            "nf": {"freq_hz": self.nf_freq_hz, "with_att_db": self.nf_y[NF_WITH_ATT],
                   "without_att_db": self.nf_y[NF_WITHOUT_ATT], "diff_db": nf_diff, "amp_raw": self.nf_amp()},
            "pending": None if self.pending is None else {k: self.pending[k] for k in
                                                          ("index", "total_db", "before_db", "diff_db")},
            "message": self.message,
            "legacy": None if not self.legacy else {"file_name": self.legacy["file_name"],
                                                    "sha256": self.legacy["sha256"]},
            "pluto": self._pluto_snapshot() if pluto else None,
            "unsaved": self.unsaved(),
            "savable": self.savable(),
            "saved": self.saved,
            "task_id": task_id,
        }

    def _pluto_after(self) -> Optional[pg.GainCorrection]:
        return self.pluto_candidate("") if self.pluto_rows else None

    def _pluto_items(self) -> list[dict]:
        """確かめる表の項目: 表の中の、絶対値がいちばん大きい補正（前・新）。"""
        def worst(cal: Optional[pg.GainCorrection]) -> Optional[float]:
            if cal is None or not cal.rows:
                return None
            return max((r.extreme() for r in cal.rows), key=abs)

        before = worst(self.pluto_base)
        after_cal = self._pluto_after()
        after = worst(after_cal) if after_cal is not None else before
        big = before is not None and after is not None and abs(after - before) > pg.BIG_DIFF_DB + 1e-9
        return [{"item": PLUTO_ITEM, "before": before, "after": after, "big": big}]

    def _pluto_snapshot(self) -> dict:
        """PlutoSDR の利得の補正の作業: 測る周波数ごとの行（前・新は、その周波数の絶対値がいちばん大きい補正）。"""
        base = self.pluto_base
        rows = []
        for i, f in enumerate(self.pluto_grid):
            before = pg.extreme_at(base, f)
            r = self.pluto_rows.get(f)
            meta = self.pluto_meta.get(f, {})
            after = r.extreme() if r is not None else before
            rows.append({
                "index": i, "freq_hz": f, "status": POINT_MEASURED if r is not None else POINT_NONE,
                "before_db": before, "after_db": after,
                "big": r is not None and base is not None and abs(after - before) > pg.BIG_DIFF_DB + 1e-9,
                "gain_lo_db": None if r is None else min(r.gains_db),
                "gain_hi_db": None if r is None else max(r.gains_db),
                "ref_gain_db": None if r is None else r.ref_gain_db,
                "min_db": None if r is None else min(r.corr_db),
                "max_db": None if r is None else max(r.corr_db),
                "tx_atten_db": [] if r is None else list(r.tx_atten_db),
                "dropped": len(meta.get("dropped_db") or []),
            })
        return {"rows": rows, "measured": len(self.pluto_rows),
                "total": len(self.pluto_grid), "has_base": base is not None}

    def _legacy_check(self, item: str, value: Optional[float]) -> dict:
        """旧ファイルの値と、校正作業で測った値の比べ（設計D §5.6）。測った値が無ければ比べない。"""
        m = self.measured.get(item)
        if m is None or value is None:
            return {"measured": m, "warn": False}
        return {"measured": m, "warn": abs(value - m) > DIFF_LIMIT[item_kind(item)] + 1e-9}
