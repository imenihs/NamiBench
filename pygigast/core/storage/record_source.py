"""記録を同じ口で読む（設計P §5・設計H §3.1）: 保存ファイル（HDF5）と記録中の作業ファイル（SQLite）。

- 再生（P §5）と、記録中に画面の行の上限を超えた古い行を読み直す（P §3・§9.2）のに使う
- 読むのは書き込みスレッドだけ（設計A §4.2: 制御スレッドはディスクを待たない）
- 全体の概観は、行数が画面に収まる段（4〜1024 掃引ごとの最大値: H §7.1）を選んで読み、残りは呼ぶ側が最大値で
  まとめる（waterfall.select_rows）。SQLite の概観は揃った塊だけなので、最後の揃っていない分は生の値を読む
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np

from ..types import RawSweep
from . import StorageError
from .h5file import OVERVIEW_LEVELS, H5Record, SegmentInfo, raw_sweep_of
from .recording import WORK_SUFFIX, RecordingReader


@dataclass
class AdoptedRows:
    """区間から読んだ行（採用した生の値。まとめた行は列ごとの最大値）。"""

    segment: int
    t_first: np.ndarray  # 各行の最初の掃引の開始時刻
    t_last: np.ndarray  # 各行の最後の掃引の開始時刻
    first_index: np.ndarray  # 各行の最初の掃引の番号（区間の中）
    sweeps: np.ndarray  # 各行がまとめた掃引の数
    values: np.ndarray  # [行, 点]


class RecordSource:
    """記録のファイル（.h5 か作業ファイルの .sqlite）。開くのは見出しと時刻だけ。"""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.kind = "work" if self.path.name.endswith(WORK_SUFFIX) else "h5"
        if self.kind == "work":
            self._r = RecordingReader(self.path)
        else:
            self._r = H5Record(self.path)
        self.header = self._r.header
        self._times: dict[int, np.ndarray] = {}

    def close(self) -> None:
        self._r.close()

    def refresh(self) -> None:
        """記録中の作業ファイルなら増えた掃引を読み直す。"""
        if self.kind == "work":
            self._r.refresh()
            self._times.clear()

    @property
    def segments(self) -> list[SegmentInfo]:
        return [s for s in self._r.segments]

    @property
    def n_sweeps(self) -> int:
        return sum(s.n_sweeps for s in self._r.segments)

    def root_attr(self, key: str) -> Optional[str]:
        if self.kind != "h5":
            return None
        v = self._r._f.attrs.get(key)  # noqa: SLF001 — 根の属性を読むだけ
        return v.decode("utf-8") if isinstance(v, bytes) else (None if v is None else str(v))

    def times(self, pos: int) -> np.ndarray:
        """区間（並びの位置 pos）の掃引ごとの開始時刻。"""
        if pos not in self._times:
            if self.kind == "h5":
                self._times[pos] = np.asarray(self._r.t_start(pos), dtype=np.float64)
            else:
                self._times[pos] = self._r.times(pos)
        return self._times[pos]

    def raw_sweep(self, pos: int, i: int) -> RawSweep:
        s = self._r.segments[pos]
        if not 0 <= i < s.n_sweeps:
            raise StorageError("range_empty", {"segment": pos, "index": i})
        if self.kind == "h5":
            return self._r.raw_sweep(pos, i)
        return raw_sweep_of(s, self._r.read(pos, (i, i + 1)))

    def rows(self, pos: int, s0: int, s1: int, max_rows: int) -> AdoptedRows:
        """区間の掃引 [s0, s1) を、多くても max_rows 行くらいで読む（概観の段を使い、最後の端は生の値）。"""
        s = self._r.segments[pos]
        s0, s1 = max(0, int(s0)), min(s.n_sweeps, int(s1))
        t = self.times(pos)
        if s1 <= s0:
            z = np.zeros(0)
            return AdoptedRows(pos, z, z, z.astype(int), z.astype(int), np.zeros((0, s.points), dtype=s.dtype))
        need = -(-(s1 - s0) // max(1, int(max_rows)))
        k = 1
        for lv in OVERVIEW_LEVELS:
            if lv <= need:
                k = lv
        if k == 1:
            v = self._r.read(pos, (s0, s1)).adopted()
            idx = np.arange(s0, s1)
            return AdoptedRows(pos, t[s0:s1], t[s0:s1], idx, np.ones(s1 - s0, dtype=int), np.asarray(v))
        b0, b1 = s0 // k, -(-s1 // k)
        vals, nblocks = self._overview(pos, k, b0, b1)
        firsts = [(b0 + j) * k for j in range(nblocks)]
        lasts = [min(s.n_sweeps, f + k) - 1 for f in firsts]
        parts_v = [vals]
        f_out, l_out, n_out = list(firsts), list(lasts), [l - f + 1 for f, l in zip(firsts, lasts)]
        covered = (b0 + nblocks) * k
        if covered < s1:
            # 揃っていない最後の塊（作業ファイル）は生の値を読む
            tail = self._r.read(pos, (covered, s1)).adopted()
            parts_v.append(np.asarray(tail))
            f_out += list(range(covered, s1))
            l_out += list(range(covered, s1))
            n_out += [1] * (s1 - covered)
        v = np.concatenate(parts_v, axis=0) if len(parts_v) > 1 else parts_v[0]
        fi = np.asarray(f_out, dtype=int)
        li = np.asarray(l_out, dtype=int)
        return AdoptedRows(pos, t[fi], t[li], fi, np.asarray(n_out, dtype=int), np.asarray(v))

    def _overview(self, pos: int, k: int, b0: int, b1: int) -> tuple[np.ndarray, int]:
        if self.kind == "h5":
            ds = self._r._group(pos)["overview"][f"max_{k}"]  # noqa: SLF001 — 求めた塊だけを読む（H §3.1）
            b1 = min(b1, int(ds.shape[0]))
            v = ds[b0:b1, :]
            return np.asarray(v), max(0, b1 - b0)
        v = self._r.overview_range(pos, k, b0, b1)
        return v, int(v.shape[0])

    def locate(self, t: float) -> Optional[tuple[int, int]]:
        """時刻 t に最も近い掃引の (区間, 番号)。掃引が1つも無ければ None。"""
        best: Optional[tuple[float, int, int]] = None
        for pos, s in enumerate(self._r.segments):
            if not s.n_sweeps:
                continue
            ts = self.times(pos)
            i = int(np.searchsorted(ts, t))
            for j in (i - 1, i):
                if 0 <= j < len(ts):
                    d = abs(float(ts[j]) - t)
                    if best is None or d < best[0]:
                        best = (d, pos, j)
        return None if best is None else (best[1], best[2])

    def span(self) -> tuple[float, float]:
        firsts = [s.t_first for s in self._r.segments if s.n_sweeps]
        lasts = [s.t_last for s in self._r.segments if s.n_sweeps]
        if not firsts:
            return math.nan, math.nan
        return min(firsts), max(lasts)
