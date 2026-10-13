"""保存ファイル（HDF5、拡張子 .h5）の書き込みと読み出し（設計H §7.1・§3.1）。

構成（設計H §7.1）:

    /                     属性: format, version, app_version, created_at（時差付き）, timezone,
                          device（JSON: model・serial）, profile（JSON）, calibration（JSON。無ければ "null"）, note
    /segments/0000/       条件が同じ区間（連番）
        属性: conditions（採用済みの掃引条件の JSON: 設計C §5.8）, start_hz, step_hz, points,
              quantity, value_kind, raw_encoding, raw_layout, normalization（JSON。無ければ "null"）,
              raw_extra（JSON。換算に要る機器の値。RTL-SDR の利得など。要らない形では属性ごと無い）,
              nf（JSON。NF の区間だけ: 使った ENR・増幅度。あとで NF を計算し直せるように: 設計M・H §3）
        raw      [掃引, 点] uint16   chunk (256, 512)・gzip 1。生の値の形（raw_encoding）で型が決まる:
                                     gigast_ad_u16 は AD 値の uint16、rtlsdr_power_w はリニアの電力の float32
                                     （値の無い点は NaN）。型はデータセット自身が持つので、読む側は属性を見ずに読める
        raw_segments/<番号> [掃引, 回数, 点数]  イメージ除去（正確・スピード: 設計F §7.3）のとき。区間ごとの生の値
                                     （b3 の並びの順）。属性 point0（区間の先頭の点）・points・passes・b3。raw は区間ごとに
                                     回を合わせた採用する値（合わせ方が機種のプロファイルの雑音の床と帯によるので、
                                     表示した値をそのまま持つ）
        raw_passes [掃引, 4, 点] uint16  前の4回の形のイメージキャンセル（読むだけ: 設計F §6）。b3=0・1・0・1 の順
        raw_b3   [掃引, 点] uint16   前の2回の形のイメージキャンセル（読むだけ）。raw は b3=1、raw_b3 は b3=0 の回
        raw_hot / raw_cold           NF のとき。raw は raw_hot を指すソフトリンク（読む側の経路を1つにする）
        t_start  [掃引] float64      各掃引の開始時刻（UNIX 時刻）
        overview/max_4 … max_1024    [ceil(掃引/k), 点] 連続する k 掃引ごとの最大値（採用した値で。NaN は除いて取り、
                                     全部 NaN の点だけ NaN）
    /traces/<番号>/       保存したときに表示していたトレース（設計F §5.1）
    /markers              属性 markers に JSON（設計G §3.1）

- 生の値は前置きを切り落とし、指定の Stop までで切ったもの（設計C §5.3: 保存は Start〜Stop で切る）
- 書くときは一時ファイルに書いてから名前を付け替える。やめた・失敗したら一時ファイルを消す（設計H §2・§4.1）
- 周波数の一部・時間の一部だけを読める（設計H §3.1）。点の番号は start_hz と step_hz から、
  掃引の番号は t_start から求める
- トレースとマーカーの中身は他のモジュール（設計F・G）が決めるので、ここでは TraceData と
  JSON にできる dict の並びとして受ける
"""
from __future__ import annotations

import dataclasses
import json
import math
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterator, Optional, Sequence

import h5py
import numpy as np

from ... import __version__ as APP_VERSION
from ..types import FreqAxis, Quantity, RawSweep, ValueKind
from . import raw_segments as rs
from . import (
    READABLE_CONDITIONS_VERSIONS,
    Progress,
    ProgressFn,
    StorageError,
    adopted_from_dict,
    adopted_to_dict,
    dumps,
    points_within_stop,
    remove_quietly,
    temp_path_for,
)

FILE_FORMAT = "pygigast-record"
FILE_VERSION = 1
MARKERS_VERSION = 1
TRACE_INFO_VERSION = 1
H5_SUFFIX = ".h5"

CHUNK = (256, 512)
GZIP_LEVEL = 1
OVERVIEW_LEVELS = (4, 16, 64, 256, 1024)
# 書くときに一度に扱う掃引の数。概観のどの段（最大 1024）でも割り切れるようにする
BLOCK_ROWS = 1024

# 生の値の持ち方（区間の属性 raw_layout）
LAYOUT_SINGLE = "single"  # 1回（イメージ除去なし・RTL-SDR）
# イメージ除去（正確・スピード。設計F §7.3）: raw = 区間ごとに回を合わせた採用する値、raw_segments = 区間ごとの生の値
LAYOUT_IMAGE_REJECT = "image_reject_segments"
# 前の2回の形（読むだけ）: raw = b3=1 の回、raw_b3 = b3=0 の回。採用した値は点ごとの min
LAYOUT_IMAGE_CANCEL = "image_cancel"
# 前の4回の形（読むだけ。設計F §6）: raw = 4回を合わせた採用する値、raw_passes = 4回の生の値 [掃引, 回, 点]
LAYOUT_IMAGE_CANCEL_4 = "image_cancel_4"
LAYOUT_NF = "nf"  # raw_hot・raw_cold。raw は raw_hot（RawSweep と同じ約束: 設計M）

_SECOND_NAME = {LAYOUT_IMAGE_CANCEL: "raw_b3", LAYOUT_NF: "raw_cold", LAYOUT_IMAGE_CANCEL_4: "raw_passes"}
# raw2 が回の軸を持つ形とその回数（raw2 は [掃引, 回, 点]。それ以外の形の raw2 は [掃引, 点]）
_PASSES = {LAYOUT_IMAGE_CANCEL_4: 4}
# 区間ごとのデータセットの入れ物（イメージ除去）
RAW_SEGMENTS_GROUP = "raw_segments"


def passes_of(layout: str) -> Optional[int]:
    """raw2 が回の軸を持つ形ならその回数、持たない形なら None。"""
    return _PASSES.get(layout)


def raw2_tail(meta: "SegmentMeta") -> Optional[tuple[int, ...]]:
    """1掃引の raw2 の形（掃引の軸を除く）。raw2 を持たない形は None。

    2回の形・NF は (点,)、前の4回の形は (4, 点)、イメージ除去は (全部の区間の 回数 × 点数 の和,)（raw_segments.py）。
    """
    if meta.raw_layout == LAYOUT_IMAGE_REJECT:
        return (rs.width(meta.raw_segments or []),)
    if meta.raw_layout not in _SECOND_NAME:
        return None
    k = passes_of(meta.raw_layout)
    return (meta.points,) if k is None else (k, meta.points)


@dataclass(frozen=True)
class RawEncoding:
    """生の値の形（RawSweep.encoding）ごとの持ち方（設計H §7.1・設計Q §6.1）。

    - dtype: ファイル（HDF5 のデータセット・SQLite の BLOB）に持つ型
    - extra_keys: 換算に要るので区間の属性 raw_extra に持つ RawSweep.extra の項目。区間の中で同じ値
      （利得を変えたら値の意味が変わる: 設計Q §6.5）なので、違えば新しい区間になる。掃引ごとに変わる
      項目（振り切れの割合・中心を合わせ直した回数など）は換算に使わないので持たない
    - nan_is_no_value: NaN を値の無い点とする形か（読み戻すとき extra の flags.no_value を作り直す）
    """

    dtype: np.dtype
    extra_keys: tuple[str, ...] = ()
    nan_is_no_value: bool = False


ENCODING_GIGAST = "gigast_ad_u16"
ENCODING_RTLSDR = "rtlsdr_power_w"
RAW_ENCODINGS = {
    ENCODING_GIGAST: RawEncoding(np.dtype(np.uint16)),
    # 振り切れの正弦波を 1.0 とするリニアの電力（devices/rtlsdr/levels.py）。レベルへの換算に利得と
    # 個体の校正（根か区間の calibration）と、PlutoSDR の利得の補正の点ごとの値（gain_corr_db）が要る。どう測ったか（窓・FFT の点数・平均の回数・ppm・
    # PlutoSDR の TG 高調波の平均除去の回数 tg_harmonic_avg: 設計Q §7.7・PlutoSDR の基準の補正で読み戻した基準の周波数
    # xo_correction_hz: 設計Q §7.10。補正の ppm は ppm）も残す
    ENCODING_RTLSDR: RawEncoding(
        np.dtype(np.float32),
        extra_keys=("gain_db", "gain_mode", "ppm", "window", "fft_n", "averages", "sample_rate_hz",
                    "dc_delta_hz", "passband_corrected", "half_usable_hz", "realtime", "fixed_center",
                    "gain_corr_db", "tg_harmonic_avg", "xo_correction_hz"),
        nan_is_no_value=True,
    ),
}
# 表に無い形は今までどおり uint16 として扱う（この表を作る前のファイルはすべて gigast_ad_u16）
_DEFAULT_ENCODING = RAW_ENCODINGS[ENCODING_GIGAST]


def encoding_info(name: str) -> RawEncoding:
    return RAW_ENCODINGS.get(str(name), _DEFAULT_ENCODING)


def can_store(name: str) -> bool:
    """保存・記録に生の値を入れられる形か。"""
    return str(name) in RAW_ENCODINGS


# ---------------------------------------------------------------------------
# 入れ物
# ---------------------------------------------------------------------------


def _local_now_iso() -> tuple[str, str]:
    now = datetime.now().astimezone()
    tz = now.strftime("%z")
    return now.isoformat(timespec="seconds"), f"{tz[:3]}:{tz[3:]}"


@dataclass
class FileHeader:
    """ファイル全体の属性（設計H §3・§7.1）。created_at / timezone を省くといまの時刻と地域の時差。"""

    device_model: str
    device_serial: str
    profile: dict = field(default_factory=dict)
    calibration: Optional[dict] = None  # 当てた校正データの写し（設計D §5.2 の JSON）
    note: str = ""
    app_version: str = APP_VERSION
    created_at: str = ""
    timezone: str = ""

    def __post_init__(self) -> None:
        if not self.created_at or not self.timezone:
            iso, tz = _local_now_iso()
            self.created_at = self.created_at or iso
            self.timezone = self.timezone or tz


@dataclass
class SegmentMeta:
    """区間の属性（設計H §7.1）。calibration は区間の途中で校正が変わったときだけ入れる（無ければ根の属性）。"""

    conditions: dict
    start_hz: int
    step_hz: int
    points: int
    quantity: str
    value_kind: str
    raw_encoding: str
    raw_layout: str = LAYOUT_SINGLE
    normalization: Optional[dict] = None
    calibration: Optional[dict] = None
    raw_extra: Optional[dict] = None  # 換算に要る機器の値（RawEncoding.extra_keys）。要らない形は None
    # イメージ除去の区間の並び [{"point0", "points", "passes", "b3"}, …]（raw_layout が image_reject_segments のとき）
    raw_segments: Optional[list] = None
    # NF の区間の ENR・増幅度（{"enr": {...}, "amp_raw": ...}。NF でない区間は None: 設計M）
    nf: Optional[dict] = None

    def key(self) -> str:
        """同じ区間かを比べるための文字列（どれか1つでも違えば新しい区間: 設計H §3）。"""
        return dumps(dataclasses.asdict(self))

    @property
    def dtype(self) -> np.dtype:
        return encoding_info(self.raw_encoding).dtype

    def freqs(self, i0: int = 0, i1: Optional[int] = None) -> np.ndarray:
        i1 = self.points if i1 is None else i1
        return np.int64(self.start_hz) + np.int64(self.step_hz) * np.arange(i0, i1, dtype=np.int64)


@dataclass
class SegmentInfo(SegmentMeta):
    """読んだ区間。index は区間の番号、n_sweeps は掃引の数。"""

    index: int = 0
    n_sweeps: int = 0
    t_first: float = math.nan
    t_last: float = math.nan


@dataclass
class TraceData:
    """保存・書き出し・比較に使うトレース（設計F §5.1 の中身のうち、ファイルに持つもの）。

    - values はリニア（電力は W、比は振幅比。複素数なら複素数）。保存は float32 / complex64
    - 周波数軸は axis（等間隔）か freqs_hz（不等間隔。int64）のどちらか
    - display_start_hz / display_stop_hz があれば、保存・書き出しはその範囲で切る（設計C §5.3）
    - info は動作・表示するか・平均の目標回数と今の回数・平均の種類・最後に更新した時刻など（JSON にできる dict）
    - flags は点ごとの印（bool の配列。"transient" など）
    - origin は読んだときに入れる出どころ（ファイル名・測定日時・機器。比較の凡例: 設計H §6）
    """

    number: int
    values: np.ndarray
    quantity: Quantity
    value_kind: ValueKind
    axis: Optional[FreqAxis] = None
    freqs_hz: Optional[np.ndarray] = None
    display_start_hz: Optional[int] = None
    display_stop_hz: Optional[int] = None
    info: dict = field(default_factory=dict)
    flags: dict = field(default_factory=dict)
    profile: dict = field(default_factory=dict)
    origin: dict = field(default_factory=dict)

    def freqs(self) -> np.ndarray:
        if self.freqs_hz is not None:
            return np.asarray(self.freqs_hz, dtype=np.int64)
        if self.axis is None:
            raise ValueError("axis か freqs_hz のどちらかが要る")
        return self.axis.freqs()

    def cut(self) -> "TraceData":
        """表示の範囲（display_start_hz〜display_stop_hz）の点だけにしたもの。"""
        f = self.freqs()
        lo = -(2**63) if self.display_start_hz is None else self.display_start_hz
        hi = 2**63 - 1 if self.display_stop_hz is None else self.display_stop_hz
        keep = (f >= lo) & (f <= hi)
        if keep.all():
            return self
        idx = np.nonzero(keep)[0]
        axis = None
        freqs = None
        if self.axis is not None and self.freqs_hz is None and len(idx):
            axis = FreqAxis(int(f[idx[0]]), self.axis.step_hz, len(idx))
        else:
            freqs = f[keep]
        return dataclasses.replace(
            self,
            values=np.asarray(self.values)[keep],
            axis=axis,
            freqs_hz=freqs,
            flags={k: np.asarray(v)[keep] for k, v in self.flags.items()},
        )


class SegmentSource:
    """書く区間の中身。blocks は (t_start, raw, raw2) を BLOCK_ROWS 行ずつ返す。

    raw2 は持ち方ごとの残りの生の値か None。形は [掃引, *raw2_tail(meta)]。
    """

    meta: SegmentMeta
    n_sweeps: int

    def blocks(self, rows: int) -> Iterator[tuple[np.ndarray, np.ndarray, Optional[np.ndarray]]]:
        raise NotImplementedError


class MemorySegment(SegmentSource):
    """メモリにある区間（1回分の保存に使う）。"""

    def __init__(self, meta: SegmentMeta, t_start: np.ndarray, raw: np.ndarray, raw2: Optional[np.ndarray] = None):
        self.meta = meta
        self._t = np.asarray(t_start, dtype=np.float64).reshape(-1)
        self._raw = np.asarray(raw, dtype=meta.dtype).reshape(len(self._t), meta.points)
        tail = raw2_tail(meta)
        self._raw2 = None if raw2 is None or tail is None else \
            np.asarray(raw2, dtype=meta.dtype).reshape((len(self._t),) + tail)
        self.n_sweeps = len(self._t)

    def blocks(self, rows: int):
        for s in range(0, self.n_sweeps, rows):
            e = min(s + rows, self.n_sweeps)
            yield self._t[s:e], self._raw[s:e], None if self._raw2 is None else self._raw2[s:e]


# ---------------------------------------------------------------------------
# RawSweep から区間の属性と生の値を作る（記録と1回分の保存で共通）
# ---------------------------------------------------------------------------


def layout_of(raw: RawSweep) -> str:
    # NF を先に見る（NF はイメージ除去をしない: 設計F §7.1）
    if raw.raw_hot is not None or raw.raw_cold is not None:
        return LAYOUT_NF
    if raw.raw_segments is not None:
        return LAYOUT_IMAGE_REJECT
    if raw.raw_passes is not None:
        return LAYOUT_IMAGE_CANCEL_4
    if raw.raw_b3_0 is not None:
        return LAYOUT_IMAGE_CANCEL
    return LAYOUT_SINGLE


def _raw_values(a: np.ndarray, n: int, enc: RawEncoding) -> np.ndarray:
    arr = np.asarray(a)
    if enc.dtype == np.uint16:
        # AD 値は整数のまま持つ（float から丸めて入れない）
        if arr.dtype != np.uint16:
            raise TypeError(f"生の値は uint16 で渡す（{arr.dtype} が来た）")
    elif arr.dtype.kind != "f":
        raise TypeError(f"生の値は浮動小数で渡す（{arr.dtype} が来た）")
    else:
        arr = arr.astype(enc.dtype)
    if arr.shape[0] < n:
        raise ValueError(f"生の値の点数 {arr.shape[0]} が保存する点数 {n} より少ない")
    return arr[:n]


def split_raw(raw: RawSweep) -> tuple[np.ndarray, Optional[np.ndarray]]:
    """保存する (raw, raw2)。Stop までで切る（設計C §5.3）。raw2 は持ち方ごとの残りの回（無ければ None）。"""
    n = points_within_stop(raw.adopted)
    lay = layout_of(raw)
    enc = encoding_info(raw.encoding)
    if lay == LAYOUT_NF:
        if raw.raw_hot is None or raw.raw_cold is None:
            raise ValueError("NF は raw_hot と raw_cold の両方が要る")
        return _raw_values(raw.raw_hot, n, enc), _raw_values(raw.raw_cold, n, enc)
    if lay == LAYOUT_IMAGE_REJECT:
        for s in raw.raw_segments:
            if enc.dtype == np.uint16 and np.asarray(s.values).dtype != np.uint16:
                raise TypeError(f"生の値は uint16 で渡す（{np.asarray(s.values).dtype} が来た）")
        regions = rs.regions_of(raw.raw_segments, n)
        rs.check(regions, n)
        # raw は表示した採用する値（区間ごとの合わせ方は機種のプロファイルの雑音の床と帯による: 設計F §7.1）
        return _raw_values(raw.raw, n, enc), rs.flatten(raw.raw_segments, regions).astype(enc.dtype)
    if lay == LAYOUT_IMAGE_CANCEL_4:
        passes = np.asarray(raw.raw_passes)
        if passes.ndim != 2 or passes.shape[0] != _PASSES[lay]:
            raise ValueError(f"イメージキャンセルの生の値は [{_PASSES[lay]}, 点]: {passes.shape}")
        if enc.dtype == np.uint16 and passes.dtype != np.uint16:
            raise TypeError(f"生の値は uint16 で渡す（{passes.dtype} が来た）")
        if passes.shape[1] < n:
            raise ValueError(f"生の値の点数 {passes.shape[1]} が保存する点数 {n} より少ない")
        # raw は表示した採用する値（4回の合わせ方は機種のプロファイルの雑音の床による: 設計F §6）
        return _raw_values(raw.raw, n, enc), passes[:, :n].astype(enc.dtype)
    if lay == LAYOUT_IMAGE_CANCEL:
        if raw.raw_b3_1 is None:
            raise ValueError("イメージキャンセルは raw_b3_0 と raw_b3_1 の両方が要る")
        # raw は b3=1 の回（イメージキャンセルなしのときと同じ回）。採用した値（min）は読むときに作り直せる
        return _raw_values(raw.raw_b3_1, n, enc), _raw_values(raw.raw_b3_0, n, enc)
    return _raw_values(raw.raw, n, enc), None


# raw_extra のうち点ごとの値を持つ項目（測った点の頭から並ぶ）。範囲を切り出すときは同じ点で切る
POINT_EXTRA_KEYS = ("gain_corr_db",)


def clip_raw_extra(extra: Optional[dict], lo: int, hi: int) -> Optional[dict]:
    """区間の点 [lo, hi) を切り出したときの raw_extra（点ごとの項目だけ同じ点で切る）。"""
    if not extra:
        return extra
    out = dict(extra)
    for k in POINT_EXTRA_KEYS:
        if isinstance(out.get(k), list):
            out[k] = out[k][lo:hi]
    return out


def raw_extra_for(raw: RawSweep) -> Optional[dict]:
    """区間の属性 raw_extra に持つ RawSweep.extra の項目（RawEncoding.extra_keys）。要らない形は None。"""
    keys = encoding_info(raw.encoding).extra_keys
    if not keys:
        return None
    ex = raw.extra or {}
    return {k: ex[k] for k in keys if k in ex}


def segment_meta_for(
    raw: RawSweep,
    *,
    quantity: Quantity | str = Quantity.POWER,
    value_kind: ValueKind | str = ValueKind.REAL,
    normalization: Optional[dict] = None,
    calibration: Optional[dict] = None,
    nf: Optional[dict] = None,
) -> SegmentMeta:
    a = raw.adopted
    n = points_within_stop(a)
    lay = layout_of(raw)
    return SegmentMeta(
        conditions=adopted_to_dict(a),
        start_hz=int(a.axis.start_hz),
        step_hz=int(a.axis.step_hz),
        points=n,
        quantity=Quantity(quantity).value,
        value_kind=ValueKind(value_kind).value,
        raw_encoding=str(raw.encoding),
        raw_layout=lay,
        normalization=normalization,
        calibration=calibration,
        raw_extra=raw_extra_for(raw),
        raw_segments=rs.regions_of(raw.raw_segments, n) if lay == LAYOUT_IMAGE_REJECT else None,
        nf=nf if lay == LAYOUT_NF else None,
    )


def max_ignoring_nan(a: np.ndarray, axis: int, keepdims: bool = False) -> np.ndarray:
    """概観の最大値。値の無い点（NaN）は除いて取り、全部 NaN のときだけ NaN（整数の形では max と同じ）。"""
    return np.fmax.reduce(a, axis=axis, keepdims=keepdims)


def adopted_values(layout: str, raw: np.ndarray, raw2: Optional[np.ndarray]) -> np.ndarray:
    """採用した生の値（概観・表示に使う）。前の2回の形のイメージキャンセルは点ごとの min（設計D §5.3: AD の
    段階で取る）。イメージ除去・前の4回の形は raw が合わせた値そのもの。"""
    if layout == LAYOUT_IMAGE_CANCEL and raw2 is not None:
        return np.minimum(raw, raw2)
    return raw


# ---------------------------------------------------------------------------
# 書く
# ---------------------------------------------------------------------------


def _chunk(n_rows: int, n_cols: int) -> tuple[int, int]:
    # 大きさを変えないデータセットは chunk をデータより大きくできないので、小さい区間では縮める
    return (max(1, min(CHUNK[0], n_rows)), max(1, min(CHUNK[1], n_cols)))


def _ds(g: h5py.Group, name: str, rows: int, cols: int, dtype, passes: Optional[int] = None) -> h5py.Dataset:
    if passes is None:
        shape, chunks = (rows, cols), _chunk(rows, cols)
    else:
        # 回の軸を持つ形（[掃引, 回, 点]）。1つの chunk の要素の数を2次元のときと同じくらいに抑える
        r, c = _chunk(rows, cols)
        shape, chunks = (rows, passes, cols), (max(1, r // passes), passes, c)
    return g.create_dataset(
        name, shape=shape, dtype=dtype, chunks=chunks, compression="gzip", compression_opts=GZIP_LEVEL
    )


def _write_root_attrs(f: h5py.File, header: FileHeader, extra: Optional[dict]) -> None:
    f.attrs["format"] = FILE_FORMAT
    f.attrs["version"] = np.int64(FILE_VERSION)
    f.attrs["app_version"] = header.app_version
    f.attrs["created_at"] = header.created_at
    f.attrs["timezone"] = header.timezone
    f.attrs["device"] = dumps({"model": header.device_model, "serial": header.device_serial})
    f.attrs["profile"] = dumps(header.profile)
    f.attrs["calibration"] = dumps(header.calibration)
    f.attrs["note"] = header.note
    for k, v in (extra or {}).items():
        f.attrs[k] = dumps(v)


def _write_segment(g: h5py.Group, src: SegmentSource, prog: Progress, done: int) -> int:
    m = src.meta
    n, p = src.n_sweeps, m.points
    g.attrs["conditions"] = dumps(m.conditions)
    g.attrs["start_hz"] = np.int64(m.start_hz)
    g.attrs["step_hz"] = np.int64(m.step_hz)
    g.attrs["points"] = np.int64(p)
    g.attrs["quantity"] = m.quantity
    g.attrs["value_kind"] = m.value_kind
    g.attrs["raw_encoding"] = m.raw_encoding
    g.attrs["raw_layout"] = m.raw_layout
    g.attrs["normalization"] = dumps(m.normalization)
    if m.calibration is not None:
        g.attrs["calibration"] = dumps(m.calibration)
    if m.raw_extra is not None:
        # 要らない形（GigaSt）では属性を作らない（この属性の無い今までのファイルと同じ中身のまま）
        g.attrs["raw_extra"] = dumps(m.raw_extra)
    if m.nf is not None:
        g.attrs["nf"] = dumps(m.nf)  # NF の区間だけ（ほかの区間の中身は今までと同じ）

    dt = m.dtype
    seg_ds: list[h5py.Dataset] = []
    d2 = None
    if m.raw_layout == LAYOUT_NF:
        d1 = _ds(g, "raw_hot", n, p, dt)
        g["raw"] = h5py.SoftLink(d1.name)
        d2 = _ds(g, "raw_cold", n, p, dt)
    elif m.raw_layout == LAYOUT_IMAGE_REJECT:
        # 区間ごとのデータセット（設計F §7.3）。属性に区間の先頭の点・点数・回数・b3 の並び
        d1 = _ds(g, "raw", n, p, dt)
        rs.check(m.raw_segments, p)
        sg = g.create_group(RAW_SEGMENTS_GROUP)
        for k, r in enumerate(m.raw_segments):
            d = _ds(sg, f"{k:02d}", n, int(r["points"]), dt, int(r["passes"]))
            d.attrs["point0"] = np.int64(r["point0"])
            d.attrs["points"] = np.int64(r["points"])
            d.attrs["passes"] = np.int64(r["passes"])
            d.attrs["b3"] = np.asarray(r["b3"], dtype=np.int64)
            seg_ds.append(d)
    else:
        d1 = _ds(g, "raw", n, p, dt)
        d2 = (_ds(g, _SECOND_NAME[m.raw_layout], n, p, dt, passes_of(m.raw_layout))
              if m.raw_layout in _SECOND_NAME else None)
    t_ds = g.create_dataset("t_start", shape=(n,), dtype=np.float64)
    ov = g.create_group("overview")
    ov_ds = {k: _ds(ov, f"max_{k}", -(-n // k), p, dt) for k in OVERVIEW_LEVELS}

    row = 0
    for t, raw, raw2 in src.blocks(BLOCK_ROWS):
        prog.check()
        rows = len(t)
        if raw.shape != (rows, p):
            raise ValueError(f"区間の点数が合わない: {raw.shape} / ({rows}, {p})")
        d1[row : row + rows] = raw
        if seg_ds:
            if raw2 is None:
                raise ValueError("区間ごとの生の値が無い")
            for d, part in zip(seg_ds, rs.split(raw2, m.raw_segments)):
                d[row : row + rows] = part
        if d2 is not None:
            if raw2 is None:
                raise ValueError("2回目の生の値が無い")
            if raw2.shape != (rows,) + d2.shape[1:]:
                raise ValueError(f"残りの回の生の値の形が合わない: {raw2.shape} / {d2.shape}")
            d2[row : row + rows] = raw2
        t_ds[row : row + rows] = t
        # 概観: ブロックの始まりは BLOCK_ROWS の倍数なので、どの k でも区切りがそろう
        adopted = adopted_values(m.raw_layout, raw, raw2)
        for k, ds in ov_ds.items():
            full = rows // k * k
            parts = []
            if full:
                parts.append(max_ignoring_nan(adopted[:full].reshape(-1, k, p), axis=1))
            if full < rows:  # 区間の最後の半端（その区間の最後のブロックでだけ起きる）
                parts.append(max_ignoring_nan(adopted[full:], axis=0, keepdims=True))
            vals = np.concatenate(parts, axis=0) if len(parts) > 1 else parts[0]
            r0 = row // k
            ds[r0 : r0 + len(vals)] = vals
        row += rows
        done += rows
        prog.report(done)
    if row != n:
        raise ValueError(f"区間の掃引の数が合わない: {row} / {n}")
    return done


def _write_trace(g: h5py.Group, tr: TraceData) -> None:
    tr = tr.cut()
    vals = np.asarray(tr.values)
    kind = ValueKind(tr.value_kind)
    dtype = np.complex64 if kind == ValueKind.COMPLEX else np.float32
    g.create_dataset("values", data=vals.astype(dtype))
    if tr.freqs_hz is not None or tr.axis is None:
        g.create_dataset("frequency_hz", data=tr.freqs().astype(np.int64))
    else:
        g.attrs["start_hz"] = np.int64(tr.axis.start_hz)
        g.attrs["step_hz"] = np.int64(tr.axis.step_hz)
        g.attrs["points"] = np.int64(tr.axis.points)
    g.attrs["number"] = np.int64(tr.number)
    g.attrs["quantity"] = Quantity(tr.quantity).value
    g.attrs["value_kind"] = kind.value
    g.attrs["info"] = dumps({"version": TRACE_INFO_VERSION, **tr.info})
    g.attrs["profile"] = dumps(tr.profile)
    if tr.flags:
        fg = g.create_group("flags")
        for name, arr in tr.flags.items():
            fg.create_dataset(name, data=np.asarray(arr, dtype=bool))


def write_h5(
    path: Path | str,
    header: FileHeader,
    segments: Sequence[SegmentSource] = (),
    traces: Sequence[TraceData] = (),
    markers: Optional[Sequence[dict]] = None,
    *,
    progress: Optional[ProgressFn] = None,
    cancel=None,
    extra_root_attrs: Optional[dict] = None,
) -> Path:
    """保存ファイルを書く。一時ファイルに書き、終わったら名前を付け替える。

    progress は (書いた掃引の数, 全体の掃引の数, 経過秒)。cancel（threading.Event）が立ったら
    一時ファイルを消して Cancelled を投げる。既にある path は、書き終わるまで元のまま残る。
    """
    path = Path(path)
    tmp = temp_path_for(path)
    total = sum(s.n_sweeps for s in segments)
    prog = Progress(total, progress, cancel)
    prog.report(0, force=True)
    ok = False
    try:
        with h5py.File(tmp, "w") as f:
            _write_root_attrs(f, header, extra_root_attrs)
            sg = f.create_group("segments")
            done = 0
            for i, src in enumerate(segments):
                done = _write_segment(sg.create_group(f"{i:04d}"), src, prog, done)
            tg = f.create_group("traces")
            for tr in traces:
                _write_trace(tg.create_group(str(int(tr.number))), tr)
            mg = f.create_group("markers")
            mg.attrs["markers"] = dumps({"version": MARKERS_VERSION, "markers": list(markers or [])})
            prog.check()
            f.flush()
        # 閉じてから中身をディスクへ出し、名前を付け替える（途中で落ちても前のファイルか新しいファイルのどちらか）
        with open(tmp, "rb+") as fh:
            os.fsync(fh.fileno())
        os.replace(tmp, path)
        ok = True
    finally:
        if not ok:
            remove_quietly(tmp)
    prog.report(total, force=True)
    return path


def save_snapshot(
    path: Path | str,
    header: FileHeader,
    raw: Optional[RawSweep] = None,
    traces: Sequence[TraceData] = (),
    markers: Optional[Sequence[dict]] = None,
    *,
    quantity: Quantity | str = Quantity.POWER,
    value_kind: ValueKind | str = ValueKind.REAL,
    normalization: Optional[dict] = None,
    nf: Optional[dict] = None,
) -> Path:
    """1回分の保存（Ctrl+S。設計H §4）: 最後の掃引1回の区間＋トレース＋マーカー（設計H §7.1）。"""
    segs: list[SegmentSource] = []
    if raw is not None:
        meta = segment_meta_for(raw, quantity=quantity, value_kind=value_kind, normalization=normalization, nf=nf)
        r1, r2 = split_raw(raw)
        segs.append(MemorySegment(meta, np.array([raw.started_at]), r1[None, :], None if r2 is None else r2[None, :]))
    return write_h5(path, header, segs, traces, markers)


def default_file_name(when: Optional[datetime] = None) -> str:
    """保存の既定のファイル名 gigast_YYYYMMDD_HHMMSS.h5（設計H §4・§7.1）。"""
    when = when or datetime.now()
    return when.strftime("gigast_%Y%m%d_%H%M%S") + H5_SUFFIX


# ---------------------------------------------------------------------------
# 読む
# ---------------------------------------------------------------------------


@dataclass
class RawBlock:
    """区間から読んだ範囲。sweep0 / point0 は区間の中の先頭の掃引・点の番号。

    raw2 は持ち方ごとの残りの回: 2回の形は [掃引, 点]、前の4回の形は [掃引, 4, 点]、イメージ除去は
    [掃引, 全部の区間の 回数 × 点数 の和]（区間の並びは raw_segments。point0 は読んだ範囲の先頭からの番号）。
    """

    segment: int
    layout: str
    sweep0: int
    point0: int
    t_start: np.ndarray
    freqs_hz: np.ndarray
    raw: np.ndarray
    raw2: Optional[np.ndarray] = None
    raw_segments: Optional[list] = None

    def adopted(self) -> np.ndarray:
        return adopted_values(self.layout, self.raw, self.raw2)


@dataclass
class OverviewBlock:
    """概観の段。values の i 行目は掃引 i*k 〜 (i+1)*k−1 の最大（k=1 は生の値そのもの）。"""

    segment: int
    k: int
    point0: int
    t_start: np.ndarray  # 各行の最初の掃引の開始時刻
    freqs_hz: np.ndarray
    values: np.ndarray


def _loads(s) -> object:
    if isinstance(s, bytes):
        s = s.decode("utf-8")
    return json.loads(s)


def _attr_str(attrs, key: str, default: str = "") -> str:
    v = attrs.get(key, default)
    return v.decode("utf-8") if isinstance(v, bytes) else str(v)


def raw_sweep_of(s: "SegmentInfo", b: "RawBlock") -> RawSweep:
    """区間 s から読んだ掃引1回分（b の 0 行目）を RawSweep に戻す（換算は呼び側が level の LevelConverter で行う）。

    保存ファイル（H5Record）と記録中の作業ファイル（recording.RecordingReader）の両方が使う（読む側の経路を1つにする）。
    adopted.axis は保存した点（Stop までで切ったもの）に合わせる。duration_s は保存していないので NaN。
    extra は区間の raw_extra（RTL-SDR の利得など換算に要るもの）。NaN を値の無い点とする形では
    flags.no_value を NaN から作り直す（機器の層が返したときと同じ形にする）。
    """
    adopted = adopted_from_dict(s.conditions)
    adopted = dataclasses.replace(adopted, axis=FreqAxis(s.start_hz, s.step_hz, s.points))
    r1 = b.raw[0]
    r2 = None if b.raw2 is None else b.raw2[0]
    enc = encoding_info(s.raw_encoding)
    extra = dict(s.raw_extra or {})
    if enc.dtype.kind == "f":
        r1 = r1.astype(np.float64)
        r2 = None if r2 is None else r2.astype(np.float64)
    if enc.nan_is_no_value:
        extra["flags"] = {"no_value": np.isnan(adopted_values(s.raw_layout, r1, r2))}
    kw: dict = {}
    if s.raw_layout == LAYOUT_IMAGE_REJECT:
        kw = {"raw_segments": rs.to_segments(r2, b.raw_segments or [])}
    elif s.raw_layout == LAYOUT_IMAGE_CANCEL_4:
        kw = {"raw_passes": r2}
        extra.setdefault("pass_b3", [0, 1, 0, 1])
    elif s.raw_layout == LAYOUT_IMAGE_CANCEL:
        kw = {"raw_b3_1": r1, "raw_b3_0": r2}
    elif s.raw_layout == LAYOUT_NF:
        kw = {"raw_hot": r1, "raw_cold": r2}
    return RawSweep(
        adopted=adopted,
        raw=adopted_values(s.raw_layout, r1, r2),
        encoding=s.raw_encoding,
        started_at=float(b.t_start[0]),
        duration_s=math.nan,
        transient_head_points=adopted.transient_head_points,
        extra=extra,
        **kw,
    )


class H5Record:
    """保存ファイルを開いて、必要な範囲だけ読む（設計H §3.1）。

    開くのは属性と t_start だけ（数 ms）。生の値・概観は求めた範囲の chunk だけを読む。
    知らない形式・版のファイルは開かずに StorageError（not_pygigast_file / unsupported_version）。
    """

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        try:
            self._f = h5py.File(self.path, "r")
        except OSError as e:
            raise StorageError("cannot_open", {"file": self.path.name, "error": str(e)}) from e
        try:
            self._check()
            self.header = self._read_header()
            self.segments = self._read_segments()
        except BaseException:
            self._f.close()
            raise

    # ---- 開く・閉じる ----
    def close(self) -> None:
        self._f.close()

    def __enter__(self) -> "H5Record":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def _check(self) -> None:
        if _attr_str(self._f.attrs, "format") != FILE_FORMAT:
            raise StorageError("not_pygigast_file", {"file": self.path.name})
        ver = int(self._f.attrs.get("version", -1))
        if ver != FILE_VERSION:
            raise StorageError("unsupported_version", {"what": "file", "version": ver})

    def _read_header(self) -> FileHeader:
        a = self._f.attrs
        dev = _loads(a["device"]) or {}
        return FileHeader(
            device_model=dev.get("model", ""),
            device_serial=dev.get("serial", ""),
            profile=_loads(a["profile"]) or {},
            calibration=_loads(a["calibration"]),
            note=_attr_str(a, "note"),
            app_version=_attr_str(a, "app_version"),
            created_at=_attr_str(a, "created_at"),
            timezone=_attr_str(a, "timezone"),
        )

    def _read_segments(self) -> list[SegmentInfo]:
        out: list[SegmentInfo] = []
        sg = self._f.get("segments")
        if sg is None:
            return out
        for i, name in enumerate(sorted(sg.keys())):
            g = sg[name]
            a = g.attrs
            cond = _loads(a["conditions"])
            if not isinstance(cond, dict) or cond.get("version") not in READABLE_CONDITIONS_VERSIONS:
                raise StorageError("unsupported_version", {"what": "conditions", "segment": name})
            t = g["t_start"]
            n = int(t.shape[0])
            regions = None
            if _attr_str(a, "raw_layout", LAYOUT_SINGLE) == LAYOUT_IMAGE_REJECT:
                sg_raw = g[RAW_SEGMENTS_GROUP]
                regions = [rs.region(int(d.attrs["point0"]), int(d.attrs["points"]),
                                     [int(x) for x in np.asarray(d.attrs["b3"]).tolist()])
                           for d in (sg_raw[k] for k in sorted(sg_raw.keys()))]
            out.append(
                SegmentInfo(
                    conditions=cond,
                    start_hz=int(a["start_hz"]),
                    step_hz=int(a["step_hz"]),
                    points=int(a["points"]),
                    quantity=_attr_str(a, "quantity"),
                    value_kind=_attr_str(a, "value_kind"),
                    raw_encoding=_attr_str(a, "raw_encoding"),
                    raw_layout=_attr_str(a, "raw_layout", LAYOUT_SINGLE),
                    normalization=_loads(a["normalization"]),
                    calibration=_loads(a["calibration"]) if "calibration" in a else None,
                    raw_extra=_loads(a["raw_extra"]) if "raw_extra" in a else None,
                    raw_segments=regions,
                    nf=_loads(a["nf"]) if "nf" in a else None,
                    index=i,
                    n_sweeps=n,
                    t_first=float(t[0]) if n else math.nan,
                    t_last=float(t[n - 1]) if n else math.nan,
                )
            )
        return out

    def _group(self, seg: int) -> h5py.Group:
        return self._f["segments"][f"{seg:04d}"]

    # ---- 範囲を番号にする ----
    def t_start(self, seg: int) -> np.ndarray:
        return self._group(seg)["t_start"][()]

    def point_range(self, seg: int, f_lo_hz: Optional[int] = None, f_hi_hz: Optional[int] = None) -> tuple[int, int]:
        """周波数の範囲 [f_lo, f_hi]（両端を含む）に入る点の番号 [i0, i1)。Hz の整数で計算する。"""
        s = self.segments[seg]
        if s.step_hz == 0:  # ゼロスパン: 全点が同じ周波数
            inside = (f_lo_hz is None or f_lo_hz <= s.start_hz) and (f_hi_hz is None or s.start_hz <= f_hi_hz)
            return (0, s.points) if inside else (0, 0)
        i0 = 0 if f_lo_hz is None else max(0, -(-(int(f_lo_hz) - s.start_hz) // s.step_hz))
        i1 = s.points if f_hi_hz is None else min(s.points, (int(f_hi_hz) - s.start_hz) // s.step_hz + 1)
        return (i0, max(i0, i1))

    def sweep_range(self, seg: int, t_lo: Optional[float] = None, t_hi: Optional[float] = None) -> tuple[int, int]:
        """開始時刻が [t_lo, t_hi]（両端を含む）に入る掃引の番号 [s0, s1)。"""
        t = self.t_start(seg)
        s0 = 0 if t_lo is None else int(np.searchsorted(t, t_lo, side="left"))
        s1 = len(t) if t_hi is None else int(np.searchsorted(t, t_hi, side="right"))
        return (s0, max(s0, s1))

    # ---- 読む ----
    def read(
        self, seg: int, sweeps: Optional[tuple[int, int]] = None, points: Optional[tuple[int, int]] = None
    ) -> RawBlock:
        """掃引の番号 [s0, s1)・点の番号 [i0, i1) の生の値。その範囲の chunk だけを読む。"""
        s = self.segments[seg]
        g = self._group(seg)
        s0, s1 = sweeps if sweeps is not None else (0, s.n_sweeps)
        i0, i1 = points if points is not None else (0, s.points)
        raw = g["raw"][s0:s1, i0:i1]
        if s.raw_layout == LAYOUT_IMAGE_REJECT:
            # 区間ごとのデータセットから [i0, i1) に入る部分だけ読み、平らな1本にする（raw_segments.py）
            sg_raw = g[RAW_SEGMENTS_GROUP]
            parts = []
            for k, r in enumerate(s.raw_segments or []):
                a = max(int(r["point0"]), i0)
                b = min(int(r["point0"]) + int(r["points"]), i1)
                if b > a:
                    parts.append(sg_raw[f"{k:02d}"][s0:s1, :, a - int(r["point0"]): b - int(r["point0"])])
            sub = rs.clip(s.raw_segments or [], i0, i1)
            raw2 = rs.join(parts) if parts else np.zeros((raw.shape[0], 0), dtype=raw.dtype)
            return RawBlock(seg, s.raw_layout, s0, i0, g["t_start"][s0:s1], s.freqs(i0, i1), raw, raw2, sub)
        second = _SECOND_NAME.get(s.raw_layout)
        raw2 = None
        if second and second in g:
            ds2 = g[second]
            raw2 = ds2[s0:s1, :, i0:i1] if ds2.ndim == 3 else ds2[s0:s1, i0:i1]
        return RawBlock(seg, s.raw_layout, s0, i0, g["t_start"][s0:s1], s.freqs(i0, i1), raw, raw2)

    def read_range(
        self,
        seg: int,
        t_range: Optional[tuple[Optional[float], Optional[float]]] = None,
        f_range_hz: Optional[tuple[Optional[int], Optional[int]]] = None,
    ) -> RawBlock:
        """時刻の範囲・周波数の範囲（Hz の int）で読む（設計H §3.1 の「周波数の一部の拡大」「時間の一部の抜き出し」）。"""
        sw = self.sweep_range(seg, *(t_range or (None, None)))
        pt = self.point_range(seg, *(f_range_hz or (None, None)))
        return self.read(seg, sw, pt)

    def choose_overview(self, seg: int, max_rows: int) -> int:
        """全体を max_rows 行以内で見るための段 k（1 は生の値）。足りなければ一番粗い段。"""
        n = self.segments[seg].n_sweeps
        if n <= max_rows:
            return 1
        for k in OVERVIEW_LEVELS:
            if -(-n // k) <= max_rows:
                return k
        return OVERVIEW_LEVELS[-1]

    def overview(self, seg: int, k: int, points: Optional[tuple[int, int]] = None) -> OverviewBlock:
        """概観の段 k（1 なら採用した生の値）を点の番号 [i0, i1) で読む。"""
        s = self.segments[seg]
        i0, i1 = points if points is not None else (0, s.points)
        t = self.t_start(seg)
        if k == 1:
            vals = self.read(seg, None, (i0, i1)).adopted()
            return OverviewBlock(seg, 1, i0, t, s.freqs(i0, i1), vals)
        if k not in OVERVIEW_LEVELS:
            raise ValueError(f"概観の段は {OVERVIEW_LEVELS} のどれか")
        vals = self._group(seg)["overview"][f"max_{k}"][:, i0:i1]
        return OverviewBlock(seg, k, i0, t[::k], s.freqs(i0, i1), vals)

    def raw_sweep(self, seg: int, i: int) -> RawSweep:
        """掃引1回分を RawSweep に戻す（換算は呼び側が level の LevelConverter で行う）。

        adopted.axis は保存した点（Stop までで切ったもの）に合わせる。duration_s は保存していないので NaN。
        extra は区間の raw_extra（RTL-SDR の利得など換算に要るもの）。NaN を値の無い点とする形では
        flags.no_value を NaN から作り直す（機器の層が返したときと同じ形にする）。
        """
        return raw_sweep_of(self.segments[seg], self.read(seg, (i, i + 1)))

    def traces(self) -> list[TraceData]:
        """保存したトレース（参照トレースとして重ねる: 設計F §5.4・設計H §6）。origin に出どころを入れる。"""
        out: list[TraceData] = []
        tg = self._f.get("traces")
        if tg is None:
            return out
        origin = {
            "file": self.path.name,
            "created_at": self.header.created_at,
            "device_model": self.header.device_model,
            "device_serial": self.header.device_serial,
        }
        for name in sorted(tg.keys(), key=lambda x: int(x)):
            g = tg[name]
            a = g.attrs
            info = _loads(a["info"]) or {}
            if info.pop("version", None) != TRACE_INFO_VERSION:
                raise StorageError("unsupported_version", {"what": "trace", "trace": name})
            kind = ValueKind(_attr_str(a, "value_kind"))
            vals = g["values"][()]
            vals = vals.astype(np.complex128 if kind == ValueKind.COMPLEX else np.float64)
            axis = None
            freqs = None
            if "frequency_hz" in g:
                freqs = g["frequency_hz"][()].astype(np.int64)
            else:
                axis = FreqAxis(int(a["start_hz"]), int(a["step_hz"]), int(a["points"]))
            flags = {k: g["flags"][k][()] for k in g["flags"].keys()} if "flags" in g else {}
            out.append(
                TraceData(
                    number=int(a["number"]),
                    values=vals,
                    quantity=Quantity(_attr_str(a, "quantity")),
                    value_kind=kind,
                    axis=axis,
                    freqs_hz=freqs,
                    info=info,
                    flags=flags,
                    profile=_loads(a["profile"]) or {},
                    origin=dict(origin),
                )
            )
        return out

    def markers(self) -> list[dict]:
        mg = self._f.get("markers")
        if mg is None or "markers" not in mg.attrs:
            return []
        d = _loads(mg.attrs["markers"])
        if not isinstance(d, dict) or d.get("version") != MARKERS_VERSION:
            raise StorageError("unsupported_version", {"what": "markers"})
        return list(d.get("markers", []))


# ---------------------------------------------------------------------------
# 切り出し（設計H §3.1: 選んだ時間・周波数の範囲を新しい保存ファイルに）
# ---------------------------------------------------------------------------


class _H5RangeSegment(SegmentSource):
    def __init__(self, rec: H5Record, seg: int, sw: tuple[int, int], pt: tuple[int, int]) -> None:
        s = rec.segments[seg]
        self._rec, self._seg, self._sw, self._pt = rec, seg, sw, pt
        self.meta = SegmentMeta(
            conditions=s.conditions,
            start_hz=s.start_hz + s.step_hz * pt[0],
            step_hz=s.step_hz,
            points=pt[1] - pt[0],
            quantity=s.quantity,
            value_kind=s.value_kind,
            raw_encoding=s.raw_encoding,
            raw_layout=s.raw_layout,
            normalization=s.normalization,
            calibration=s.calibration,
            raw_extra=clip_raw_extra(s.raw_extra, pt[0], pt[1]),
            raw_segments=rs.clip(s.raw_segments, pt[0], pt[1]) if s.raw_segments is not None else None,
            nf=s.nf,
        )
        self.n_sweeps = sw[1] - sw[0]

    def blocks(self, rows: int):
        for s0 in range(self._sw[0], self._sw[1], rows):
            b = self._rec.read(self._seg, (s0, min(s0 + rows, self._sw[1])), self._pt)
            yield b.t_start, b.raw, b.raw2


def extract_h5(
    src: Path | str,
    dst: Path | str,
    *,
    t_range: Optional[tuple[Optional[float], Optional[float]]] = None,
    f_range_hz: Optional[tuple[Optional[int], Optional[int]]] = None,
    progress: Optional[ProgressFn] = None,
    cancel=None,
) -> Path:
    """時間・周波数の範囲を切り出して新しい保存ファイルにする。範囲に掃引が無ければ StorageError("range_empty")。

    トレース・マーカーは記録全体から作ったもので、切り出した範囲の値ではないので写さない。
    根の属性 extracted_from に元のファイル名・作成日時・範囲を書く。
    """
    with H5Record(src) as rec:
        parts: list[SegmentSource] = []
        for s in rec.segments:
            sw = rec.sweep_range(s.index, *(t_range or (None, None)))
            pt = rec.point_range(s.index, *(f_range_hz or (None, None)))
            if sw[1] > sw[0] and pt[1] > pt[0]:
                parts.append(_H5RangeSegment(rec, s.index, sw, pt))
        if not parts:
            raise StorageError("range_empty")
        extra = {
            "extracted_from": {
                "file": rec.path.name,
                "created_at": rec.header.created_at,
                "t_range": list(t_range) if t_range else None,
                "f_range_hz": list(f_range_hz) if f_range_hz else None,
            }
        }
        return write_h5(dst, rec.header, parts, progress=progress, cancel=cancel, extra_root_attrs=extra)

