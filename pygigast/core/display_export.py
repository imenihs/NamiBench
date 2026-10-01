"""表示の単位・外部補正・周波数オフセットを当てた CSV の書き出し（設計O・H §7.3）。

表示の設定が初期値（dBm・補正なし・オフセットなし）のときは storage/export.py の export_traces_csv をそのまま使う。
初期値でないときだけここで書く。形（1行目の `# meta:`・2行目の見出し・ASCII・一時ファイルから置き換え・
周波数は Hz の整数）は export_traces_csv と同じで、違うのは次の3つだけ:

- 値の列は画面と同じ単位（列の名前は `trace1_dBuV` のように ASCII の単位のコード）。外部補正を足した値
- 周波数の列 `frequency_hz` は周波数オフセットを足した周波数（画面の横軸と同じ）。機器で測った周波数は
  `measured_frequency_hz` の列に残す（どちらで測ったかを後から取り違えないため）
- `# meta:` に `display`（単位・外部補正・周波数オフセット）を書く
"""
from __future__ import annotations

import csv
import os
from pathlib import Path
from typing import Mapping, Optional, Sequence

import numpy as np

from .storage import Progress, StorageError, dumps, remove_quietly, temp_path_for
from .storage.export import CSV_FORMAT, CSV_VERSION, SPREADSHEET_MAX_ROWS, ExportResult
from .storage.h5file import TraceData
from .types import Quantity, ValueKind

_NEWLINE = "\r\n"
# 列の名前に使う単位のコード（ASCII だけで書く: H §7.3）
_ASCII_UNIT = {"dBμV": "dBuV", "dBmV": "dBmV", "dBm": "dBm", "dB": "dB"}


def _db(values: np.ndarray, quantity: Quantity) -> np.ndarray:
    v = np.abs(np.asarray(values)) if np.iscomplexobj(values) else np.asarray(values, dtype=np.float64)
    with np.errstate(divide="ignore", invalid="ignore"):
        if quantity == Quantity.POWER:
            return 10.0 * np.log10(v) + 30.0
        return 20.0 * np.log10(np.abs(v))


def _col_id(t: TraceData) -> str:
    tag = (t.info or {}).get("trace") if isinstance(t.info, dict) else None
    return str(tag) if isinstance(tag, str) else str(int(t.number))


def export_display_csv(path: Path | str, traces: Sequence[TraceData], *, meta: Mapping, shifts: Sequence[float],
                       unit_label: str, freq_offset_hz: int,
                       f_range_hz: Optional[tuple[Optional[int], Optional[int]]] = None,
                       progress=None, cancel=None) -> ExportResult:
    """表示の単位で書く。shifts はトレースごとに足す dB（view_units.level_shift）、範囲は機器の周波数（Hz）。"""
    if not traces:
        raise StorageError("no_data")
    cut = [t.cut() for t in traces]
    q = Quantity(cut[0].quantity)
    if any(Quantity(t.quantity) != q for t in cut):
        raise StorageError("unit_mismatch", {"reason": "mixed_quantity"})
    freqs = cut[0].freqs()
    for t in cut[1:]:
        f = t.freqs()
        if f.shape != freqs.shape or not np.array_equal(f, freqs):
            raise StorageError("axis_mismatch", {"trace": int(t.number)})
    lo, hi = f_range_hz or (None, None)
    mask = np.ones(freqs.shape, dtype=bool)
    if lo is not None:
        mask &= freqs >= int(lo)
    if hi is not None:
        mask &= freqs <= int(hi)
    if not mask.any():
        raise StorageError("range_empty")
    freqs = freqs[mask]
    cols = [_db(np.asarray(t.values)[mask], q) + float(s) for t, s in zip(cut, shifts)]
    unit = _ASCII_UNIT.get(unit_label, "dB")
    n = len(freqs)
    prog = Progress(n, progress, cancel)
    prog.report(0, force=True)
    tmp = temp_path_for(Path(path))
    descs = [{"number": int(t.number), "quantity": Quantity(t.quantity).value,
              "value_kind": ValueKind(t.value_kind).value, "info": t.info, "origin": t.origin} for t in cut]
    extra = {"kind": "traces", "unit": unit, "traces": descs,
             "display": {"unit": unit, "shift_db": [float(s) for s in shifts], "freq_offset_hz": int(freq_offset_hz)}}
    done = False
    fh = open(tmp, "w", encoding="ascii", newline="")
    try:
        fh.write("# meta: " + dumps({"format": CSV_FORMAT, "version": CSV_VERSION, **meta, **extra}) + _NEWLINE)
        w = csv.writer(fh, lineterminator=_NEWLINE)
        head = ["frequency_hz"] + ([] if not freq_offset_hz else ["measured_frequency_hz"])
        w.writerow(head + [f"trace{_col_id(t)}_{unit}" for t in cut])
        for i in range(n):
            if i % 4096 == 0:
                prog.check()
                prog.report(i)
            f = int(freqs[i])
            row = [str(f + int(freq_offset_hz))] + ([] if not freq_offset_hz else [str(f)])
            w.writerow(row + [f"{c[i]:.4f}" if np.isfinite(c[i]) else "" for c in cols])
        prog.check()
        fh.flush()
        os.fsync(fh.fileno())
        fh.close()
        os.replace(tmp, path)
        done = True
    finally:
        if not done:
            fh.close()
            remove_quietly(tmp)
    prog.report(n, force=True)
    warnings = ["exceeds_spreadsheet_rows"] if n + 2 > SPREADSHEET_MAX_ROWS else []
    return ExportResult(Path(path), n, warnings)
