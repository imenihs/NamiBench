"""ゼロスパンの CSV の書き出し（設計N §1・H §7.3）。横軸は時間（推定）。

形は storage/export.py の CSV と同じ決まり（1行目の `# meta:`・2行目の見出し・ASCII・一時ファイルから置き換え）で、
違うのは列だけ:

- `time_s`: 画面の横軸と同じ時間（秒。1回の掃引は表示する最初の点から、流す表示はいちばん新しい点を 0 にした負の値）
- `time_unix`: 各点の時刻（UNIX 秒。掃引の開始時刻＋（前置き＋点番号）×1点あたりの時間の推定: 設計N §1）
- `sweep`: 何回目の掃引の点か（掃引と掃引の間は途切れていて、連続した時間の記録ではないことを列で示す）
- 値の列は画面と同じ単位（`trace1_dBm` など。外部補正を足した値）。値の無い点は空欄

`# meta:` の `time_axis` に推定であること・1点あたりの時間・表示する長さを書く。
"""
from __future__ import annotations

import csv
import os
from pathlib import Path
from typing import Mapping, Sequence

import numpy as np

from .storage import Progress, StorageError, dumps, remove_quietly, temp_path_for
from .storage.export import CSV_FORMAT, CSV_VERSION, SPREADSHEET_MAX_ROWS, ExportResult

_NEWLINE = "\r\n"
_ASCII_UNIT = {"dBμV": "dBuV", "dBmV": "dBmV", "dBm": "dBm", "dB": "dB"}


def export_zero_span_csv(path: Path | str, columns: Sequence[dict], *, meta: Mapping, progress=None,
                         cancel=None) -> ExportResult:
    """columns はトレースごとの {"number", "unit", "x_us", "t", "sweep_no", "index", "level_db"}（値は表示の単位）。

    点はトレースをまたいで（掃引の番号, 掃引の中の点の番号）で合わせる。どれかのトレースにある点は全部1行にする。
    """
    if not columns:
        raise StorageError("no_data")
    keys = [np.asarray(c["sweep_no"], dtype=np.int64) * 1_000_000 + np.asarray(c["index"], dtype=np.int64)
            for c in columns]
    all_keys = np.concatenate(keys)
    if all_keys.size == 0:
        raise StorageError("range_empty")
    uniq, first = np.unique(all_keys, return_index=True)  # キーの順
    x_all = np.concatenate([np.asarray(c["x_us"], dtype=np.int64) for c in columns])[first]
    t_all = np.concatenate([np.asarray(c["t"], dtype=np.float64) for c in columns])[first]
    sw_all = np.concatenate([np.asarray(c["sweep_no"], dtype=np.int64) for c in columns])[first]
    by_key = []
    for c, k in zip(columns, keys):
        v = np.full(uniq.size, np.nan)
        v[np.searchsorted(uniq, k)] = np.asarray(c["level_db"], dtype=np.float64)
        by_key.append(v)
    # 行は時間の順
    order = np.argsort(x_all, kind="stable")
    x_all, t_all, sw_all = x_all[order], t_all[order], sw_all[order]
    cols = [v[order] for v in by_key]
    n = int(uniq.size)
    prog = Progress(n, progress, cancel)
    prog.report(0, force=True)
    units = [_ASCII_UNIT.get(c["unit"], "dB") for c in columns]
    tmp = temp_path_for(Path(path))
    extra = {"kind": "zero_span", "traces": [{"number": int(c["number"]), "unit": u} for c, u in zip(columns, units)]}
    done = False
    fh = open(tmp, "w", encoding="ascii", newline="")
    try:
        fh.write("# meta: " + dumps({"format": CSV_FORMAT, "version": CSV_VERSION, **meta, **extra}) + _NEWLINE)
        w = csv.writer(fh, lineterminator=_NEWLINE)
        w.writerow(["time_s", "time_unix", "sweep"] + [f"trace{int(c['number'])}_{u}" for c, u in zip(columns, units)])
        for i in range(n):
            if i % 4096 == 0:
                prog.check()
                prog.report(i)
            row = [f"{x_all[i] / 1e6:.6f}", f"{t_all[i]:.6f}", str(int(sw_all[i]))]
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
