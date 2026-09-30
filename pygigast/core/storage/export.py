"""範囲を選んで他のアプリへ書き出す（設計H §5・§7.3）。書き出したファイルはアプリで読み戻さない。

- CSV（1回分・トレース）: 1行目 `# meta: {…}`、2行目が見出し `frequency_hz,trace<番号>_<単位>,…`、以下は数値
- CSV（記録の時間範囲）: 既定は1行＝1掃引（`time_iso,time_unix,<周波数1>,…`）。縦長（`time_unix,frequency_hz,value`）も選べる
- Touchstone: 値が複素数のときだけ。実数（振幅だけ）のトレースは位相 0 を測った値として書くことになるので断る
- 文字は ASCII だけで書く（メタの JSON も \\uXXXX に逃がす）。BOM を付けない。ASCII だけなら Excel はどの
  文字コードの設定でも同じに読め、Python は encoding を気にせず csv モジュールで読める。
  BOM を付けると、BOM を知らない読み手では1行目が "# meta" で始まらなくなり、コメントとして飛ばせない
- 周波数は Hz の整数のまま書く（float を通さない: 設計A §2.1）
- 一時ファイルに書いてから名前を付け替える。やめたら一時ファイルを消す（設計H §4.1）
"""
from __future__ import annotations

import csv
import io
import os
from dataclasses import dataclass, field
from datetime import datetime, tzinfo
from pathlib import Path
from typing import Iterable, Mapping, Optional, Sequence

import numpy as np

from ..types import Quantity, ValueKind
from . import Progress, ProgressFn, StorageError, dumps, remove_quietly, temp_path_for
from .h5file import TraceData

CSV_FORMAT = "pygigast-csv"
CSV_VERSION = 1

# 量の種類ごとに選べる単位。リニアの値（W・振幅比）はそのまま、dB 系は変換する
UNITS = {Quantity.POWER: ("dBm", "W"), Quantity.RATIO: ("dB", "lin")}
DEFAULT_UNIT = {Quantity.POWER: "dBm", Quantity.RATIO: "dB"}

# 表計算の上限（列 16384・行 1048576）。超えても書くが、知らせのコードを返す
SPREADSHEET_MAX_COLUMNS = 16384
SPREADSHEET_MAX_ROWS = 1048576

LAYOUT_WIDE = "wide"
LAYOUT_LONG = "long"

_NEWLINE = "\r\n"


@dataclass
class ExportResult:
    path: Path
    rows: int
    warnings: list[str] = field(default_factory=list)


def convert_unit(values: np.ndarray, quantity: Quantity | str, unit: str) -> np.ndarray:
    """リニアの値を選んだ単位にする。量の種類に合わない単位は StorageError("unit_mismatch")。

    電力（W）: dBm = 10·log10(W) + 30。比（振幅比、複素数なら振幅）: dB = 20·log10(|v|)。
    """
    q = Quantity(quantity)
    if unit not in UNITS[q]:
        raise StorageError("unit_mismatch", {"quantity": q.value, "unit": unit})
    v = np.abs(np.asarray(values)) if np.iscomplexobj(values) else np.asarray(values, dtype=np.float64)
    with np.errstate(divide="ignore", invalid="ignore"):
        if unit == "dBm":
            return 10.0 * np.log10(v) + 30.0
        if unit == "dB":
            return 20.0 * np.log10(np.abs(v))
    return v.astype(np.float64)


def _fmt(unit: str):
    # dB 系は小数4桁（設計H §7.3）。リニアは桁が大きく変わるので有効数字 10 桁の指数表記
    spec = "{:.4f}" if unit in ("dBm", "dB") else "{:.9e}"

    def f(x: float) -> str:
        # 0 W の log など、数にならない値は空欄（Excel で文字として混ざらないように）
        return spec.format(x) if np.isfinite(x) else ""

    return f


class _AtomicText:
    """一時ファイルへ ASCII で書き、commit で名前を付け替える。commit しなければ一時ファイルを消す。"""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.tmp = temp_path_for(self.path)
        self.fh: io.TextIOWrapper = open(self.tmp, "w", encoding="ascii", newline="")
        self._done = False

    def commit(self) -> None:
        self.fh.flush()
        os.fsync(self.fh.fileno())
        self.fh.close()
        os.replace(self.tmp, self.path)
        self._done = True

    def __enter__(self) -> "_AtomicText":
        return self

    def __exit__(self, *exc) -> None:
        if not self._done:
            self.fh.close()
            remove_quietly(self.tmp)


def _meta_line(meta: Mapping, extra: Mapping) -> str:
    return "# meta: " + dumps({"format": CSV_FORMAT, "version": CSV_VERSION, **meta, **extra}) + _NEWLINE


def _range_mask(freqs: np.ndarray, f_range_hz: Optional[tuple[Optional[int], Optional[int]]]) -> np.ndarray:
    lo, hi = f_range_hz or (None, None)
    m = np.ones(freqs.shape, dtype=bool)
    if lo is not None:
        m &= freqs >= int(lo)
    if hi is not None:
        m &= freqs <= int(hi)
    return m


def _trace_desc(t: TraceData) -> dict:
    return {
        "number": int(t.number),
        "quantity": Quantity(t.quantity).value,
        "value_kind": ValueKind(t.value_kind).value,
        "info": t.info,
        "origin": t.origin,
    }


# ---------------------------------------------------------------------------
# CSV（1回分・トレース）
# ---------------------------------------------------------------------------


def export_traces_csv(
    path: Path | str,
    traces: Sequence[TraceData],
    *,
    meta: Mapping,
    unit: Optional[str] = None,
    f_range_hz: Optional[tuple[Optional[int], Optional[int]]] = None,
    progress: Optional[ProgressFn] = None,
    cancel=None,
) -> ExportResult:
    """トレースを周波数の範囲（Hz の int、両端を含む）で CSV にする。

    meta は条件・校正・基準・機器・日時の JSON にできる dict（呼び側が作る）。トレースごとの量・値の種類・
    出どころはここで足す。周波数の点が違うトレースは同じ表に並べられないので StorageError("axis_mismatch")。
    量の種類が違うトレースは同じ単位で出せないので StorageError("unit_mismatch")。
    """
    if not traces:
        raise StorageError("no_data")
    cut = [t.cut() for t in traces]
    q = Quantity(cut[0].quantity)
    if any(Quantity(t.quantity) != q for t in cut):
        raise StorageError("unit_mismatch", {"reason": "mixed_quantity"})
    unit = unit or DEFAULT_UNIT[q]
    freqs = cut[0].freqs()
    for t in cut[1:]:
        f = t.freqs()
        if f.shape != freqs.shape or not np.array_equal(f, freqs):
            raise StorageError("axis_mismatch", {"trace": int(t.number)})
    mask = _range_mask(freqs, f_range_hz)
    if not mask.any():
        raise StorageError("range_empty")
    freqs = freqs[mask]
    cols = [convert_unit(np.asarray(t.values)[mask], q, unit) for t in cut]
    n = len(freqs)
    prog = Progress(n, progress, cancel)
    prog.report(0, force=True)
    fmt = _fmt(unit)
    with _AtomicText(path) as out:
        out.fh.write(_meta_line(meta, {"kind": "traces", "unit": unit, "traces": [_trace_desc(t) for t in cut]}))
        w = csv.writer(out.fh, lineterminator=_NEWLINE)
        w.writerow(["frequency_hz"] + [f"trace{int(t.number)}_{unit}" for t in cut])
        for i in range(n):
            if i % 4096 == 0:
                prog.check()
                prog.report(i)
            w.writerow([str(int(freqs[i]))] + [fmt(c[i]) for c in cols])
        prog.check()
        out.commit()
    prog.report(n, force=True)
    warnings = ["exceeds_spreadsheet_rows"] if n + 2 > SPREADSHEET_MAX_ROWS else []
    return ExportResult(Path(path), n, warnings)


# ---------------------------------------------------------------------------
# CSV（記録の時間範囲）
# ---------------------------------------------------------------------------


def export_record_csv(
    path: Path | str,
    *,
    freqs_hz: np.ndarray,
    blocks: Iterable[tuple[np.ndarray, np.ndarray]],
    total_sweeps: int,
    quantity: Quantity | str,
    meta: Mapping,
    unit: Optional[str] = None,
    layout: str = LAYOUT_WIDE,
    tz: Optional[tzinfo] = None,
    progress: Optional[ProgressFn] = None,
    cancel=None,
) -> ExportResult:
    """記録の時間範囲を CSV にする（設計H §7.3）。

    blocks は (各掃引の開始時刻 [n], リニアの値 [n, 点]) の並び。範囲の切り出しと換算は呼び側が行う
    （H5Record.read_range・RecordingReader.read で読み、level で換算したもの）。freqs_hz は点の周波数（int64）。
    layout は "wide"（1行＝1掃引。既定）か "long"（1行＝1点）。time_iso は tz（省くといまの地域）の時差付き。
    進み具合は (書いた掃引の数, total_sweeps, 経過秒)。
    """
    q = Quantity(quantity)
    unit = unit or DEFAULT_UNIT[q]
    if unit not in UNITS[q]:
        raise StorageError("unit_mismatch", {"quantity": q.value, "unit": unit})
    if layout not in (LAYOUT_WIDE, LAYOUT_LONG):
        raise ValueError(f"layout は {LAYOUT_WIDE!r} か {LAYOUT_LONG!r}")
    freqs = np.asarray(freqs_hz)
    if freqs.dtype.kind not in "iu":
        raise TypeError("周波数は整数（Hz）の配列で渡す")
    freqs = freqs.astype(np.int64)
    tz = tz or datetime.now().astimezone().tzinfo
    prog = Progress(total_sweeps, progress, cancel)
    prog.report(0, force=True)
    fmt = _fmt(unit)
    f_txt = [str(int(f)) for f in freqs]
    done = 0
    rows = 0
    with _AtomicText(path) as out:
        out.fh.write(_meta_line(meta, {"kind": "record", "layout": layout, "unit": unit, "quantity": q.value}))
        w = csv.writer(out.fh, lineterminator=_NEWLINE)
        if layout == LAYOUT_WIDE:
            w.writerow(["time_iso", "time_unix"] + f_txt)
        else:
            w.writerow(["time_unix", "frequency_hz", "value"])
        for t, vals in blocks:
            prog.check()
            t = np.asarray(t, dtype=np.float64)
            v = convert_unit(vals, q, unit)
            if v.shape != (len(t), len(freqs)):
                raise ValueError(f"値の形 {v.shape} が (掃引 {len(t)}, 点 {len(freqs)}) と違う")
            for j in range(len(t)):
                tu = f"{t[j]:.6f}"
                if layout == LAYOUT_WIDE:
                    iso = datetime.fromtimestamp(t[j], tz).isoformat(timespec="milliseconds")
                    w.writerow([iso, tu] + [fmt(x) for x in v[j]])
                    rows += 1
                else:
                    w.writerows([tu, f_txt[i], fmt(v[j, i])] for i in range(len(freqs)))
                    rows += len(freqs)
                done += 1
                if j % 64 == 0:
                    prog.check()
                    prog.report(done)
        prog.check()
        out.commit()
    prog.report(done, force=True)
    warnings: list[str] = []
    if layout == LAYOUT_WIDE and len(freqs) + 2 > SPREADSHEET_MAX_COLUMNS:
        warnings.append("exceeds_spreadsheet_columns")
    if rows + 2 > SPREADSHEET_MAX_ROWS:
        warnings.append("exceeds_spreadsheet_rows")
    return ExportResult(Path(path), rows, warnings)


# ---------------------------------------------------------------------------
# Touchstone（複素数のときだけ）
# ---------------------------------------------------------------------------

_S2P_ORDER = ("S11", "S21", "S12", "S22")


def export_touchstone(
    path: Path | str,
    params: Mapping[str, TraceData],
    *,
    meta: Mapping,
    f_range_hz: Optional[tuple[Optional[int], Optional[int]]] = None,
    z0_ohm: float = 50.0,
) -> ExportResult:
    """Touchstone（版 1.x。実部・虚部）で書く。params は {"S11": トレース} で .s1p、S11・S21・S12・S22 の4つで .s2p。

    - 値が複素数でないトレースがあれば StorageError("touchstone_needs_complex")（位相を作らない: 設計H §7.3）
    - 比（S パラメータ）でないトレースがあれば StorageError("touchstone_needs_ratio")
    - 上の2通り以外の組み合わせは StorageError("touchstone_incomplete_parameters")（無いパラメータを 0 で埋めない）
    """
    for name, t in params.items():
        if ValueKind(t.value_kind) != ValueKind.COMPLEX:
            raise StorageError("touchstone_needs_complex", {"parameter": name})
        if Quantity(t.quantity) != Quantity.RATIO:
            raise StorageError("touchstone_needs_ratio", {"parameter": name})
    keys = set(params)
    if keys == {"S11"}:
        order: tuple[str, ...] = ("S11",)
    elif keys == set(_S2P_ORDER):
        order = _S2P_ORDER
    else:
        raise StorageError("touchstone_incomplete_parameters", {"parameters": sorted(keys)})
    cut = {k: params[k].cut() for k in order}
    freqs = cut[order[0]].freqs()
    for k in order[1:]:
        if not np.array_equal(cut[k].freqs(), freqs):
            raise StorageError("axis_mismatch", {"parameter": k})
    mask = _range_mask(freqs, f_range_hz)
    if not mask.any():
        raise StorageError("range_empty")
    freqs = freqs[mask]
    vals = [np.asarray(cut[k].values, dtype=np.complex128)[mask] for k in order]
    with _AtomicText(path) as out:
        out.fh.write("! meta: " + dumps({"format": "pygigast-touchstone", "version": 1, **meta}) + _NEWLINE)
        out.fh.write(f"# HZ S RI R {z0_ohm:g}{_NEWLINE}")
        for i in range(len(freqs)):
            cells = [str(int(freqs[i]))]
            for v in vals:
                cells += [f"{v[i].real:.9e}", f"{v[i].imag:.9e}"]
            out.fh.write(" ".join(cells) + _NEWLINE)
        out.commit()
    return ExportResult(Path(path), len(freqs))
