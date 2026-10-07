"""記録中の作業ファイル（SQLite）と、HDF5 へのまとめ・救出（設計H §2・§4・§7.2）。

- 作業ファイルは利用者のデータの場所の recordings\\active_<開始日時>.sqlite（ネットワークの共有フォルダにしない）
- PRAGMA journal_mode=WAL・synchronous=NORMAL（OFF にしない）。1掃引ごとに1トランザクションで確定する。
  アプリが落ちても、書きかけのトランザクションは次に開いたときに巻き戻り、直前の掃引までが残る
- 書き込みは書き込みスレッド（WriterThread）だけが行う。Recorder.append は待ち行列に入れて戻る（設計A §4.2）。
  1掃引の確定は最悪 140ms かかりうる（`調査/保存形式_ベンチマーク.md`）ので、通信と画面を待たせない
- 表: meta（キーと値）・segments（区間の番号と属性の JSON）・sweeps（番号・区間・区間の中の番号・開始時刻・
  raw・raw2）・overview_<k>（連続する k 掃引ごとの最大値。k が揃った塊だけを、その掃引と同じトランザクションで書く）
- raw2 は持ち方ごとの残りの生の値: イメージ除去は区間ごとの [回数, 点数] を区間の順に平らに続けたもの（raw は
  区間ごとに回を合わせた採用する値: 設計F §7.3。区間の並びは区間の属性 raw_segments）、前の4回の形は
  b3=0・1・0・1 の4回を続けたもの、前の2回の形のイメージキャンセルは b3=0 の回（raw は b3=1 の回）、
  NF は COLD（raw は HOT）
- BLOB の型は区間の生の値の形（raw_encoding: h5file.RAW_ENCODINGS）で決まる。GigaSt の AD 値は uint16、
  RTL-SDR のリニアの電力は float32（値の無い点は NaN。概観の最大値は NaN を除いて取る）
- 記録を止めたら HDF5 へまとめ、まとめ終わってから作業ファイルを消す。まとめる前に落ちたものは
  find_unfinished で見つけて、何の記録かを示して尋ねる（黙って消さない・黙って変換しない）
"""
from __future__ import annotations

import concurrent.futures
import dataclasses
import json
import math
import os
import sqlite3
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional, Sequence

import numpy as np

from ..types import Quantity, RawSweep, ValueKind
from . import ProgressFn, StorageError, WriterThread, dumps, remove_quietly
from . import raw_segments as rs
from .h5file import (
    LAYOUT_IMAGE_REJECT,
    OVERVIEW_LEVELS,
    FileHeader,
    OverviewBlock,
    RawBlock,
    SegmentInfo,
    SegmentMeta,
    SegmentSource,
    TraceData,
    adopted_values,
    raw2_tail,
    segment_meta_for,
    split_raw,
    write_h5,
)

WORK_FORMAT = "pygigast-recording-work"
WORK_VERSION = 1
WORK_PREFIX = "active_"
WORK_SUFFIX = ".sqlite"

# append の calibration の既定: 記録を始めたときの校正（FileHeader.calibration）のまま
SAME_CALIBRATION = object()


def default_recordings_dir() -> Path:
    """作業ファイルの置き場所（利用者のデータの場所）。Windows は %APPDATA%\\pyGigaSt\\recordings。"""
    appdata = os.environ.get("APPDATA")
    if appdata:
        return Path(appdata) / "pyGigaSt" / "recordings"
    return Path.home() / ".pygigast" / "recordings"


def new_recording_path(directory: Path | str, started: Optional[datetime] = None) -> Path:
    """recordings\\active_<開始日時>.sqlite。同じ秒に2つ始めたら連番を足す。"""
    started = started or datetime.now()
    d = Path(directory)
    stem = WORK_PREFIX + started.strftime("%Y%m%d_%H%M%S")
    p = d / (stem + WORK_SUFFIX)
    n = 1
    while p.exists():
        p = d / f"{stem}_{n}{WORK_SUFFIX}"
        n += 1
    return p


def _related_files(path: Path) -> list[Path]:
    return [path, path.with_name(path.name + "-wal"), path.with_name(path.name + "-shm")]


def discard_recording(path: Path | str) -> None:
    """作業ファイルを消す（利用者が「まとめない」を選んだときだけ呼ぶ）。"""
    for p in _related_files(Path(path)):
        remove_quietly(p)


def _connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), timeout=5.0, check_same_thread=True)
    conn.execute("PRAGMA journal_mode=WAL")
    # OFF にしない（設計H §2）。NORMAL は WAL ではアプリが落ちても確定済みの掃引を失わない
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


_SCHEMA = [
    "CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
    "CREATE TABLE segments (id INTEGER PRIMARY KEY, meta TEXT NOT NULL)",
    "CREATE TABLE sweeps (id INTEGER PRIMARY KEY, segment INTEGER NOT NULL, idx INTEGER NOT NULL,"
    " t_start REAL NOT NULL, raw BLOB NOT NULL, raw2 BLOB)",
    "CREATE INDEX sweeps_t ON sweeps (t_start)",
    "CREATE UNIQUE INDEX sweeps_seg ON sweeps (segment, idx)",
] + [
    f"CREATE TABLE overview_{k} (segment INTEGER NOT NULL, block INTEGER NOT NULL, t_start REAL NOT NULL,"
    f" raw BLOB NOT NULL, PRIMARY KEY (segment, block))"
    for k in OVERVIEW_LEVELS
]


def _header_to_json(h: FileHeader) -> str:
    return dumps(dataclasses.asdict(h))


def _header_from_json(s: str) -> FileHeader:
    return FileHeader(**json.loads(s))


def _to_blob(a: np.ndarray, dtype: np.dtype) -> bytes:
    # BLOB は機種の違う PC でも読めるようリトルエンディアンで持つ
    return np.ascontiguousarray(a, dtype=np.dtype(dtype).newbyteorder("<")).tobytes()


def _from_blob(b: bytes, shape: tuple[int, ...], dtype: np.dtype) -> np.ndarray:
    """BLOB を shape（[点]・[回, 点]・イメージ除去の平らな1本）に戻す。"""
    arr = np.frombuffer(b, dtype=np.dtype(dtype).newbyteorder("<"))
    expected = int(np.prod(shape))
    if arr.shape[0] != expected:
        raise StorageError("corrupt_sweep", {"points": int(arr.shape[0]), "expected": expected})
    return arr.astype(dtype).reshape(shape)


# ---------------------------------------------------------------------------
# 記録する
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RecordingStats:
    """記録中の状態表示に出すもの（設計P §9.4: 経過時間・掃引数・ファイルの大きさ）。"""

    path: Path
    started_at: float
    sweeps_committed: int
    segments: int
    file_bytes: int
    pending: int  # 待ち行列に残っている掃引の数（まだ確定していない）
    last_error: Optional[str]
    stopped: bool


class _Acc:
    """概観の段 k の途中の塊（最大値・掃引の数・塊の最初の開始時刻）。"""

    __slots__ = ("vmax", "count", "t0", "block")

    def __init__(self) -> None:
        self.vmax: Optional[np.ndarray] = None
        self.count = 0
        self.t0 = 0.0
        self.block = 0


class Recorder:
    """掃引を1回ずつ SQLite の作業ファイルへ追記する。

    - append は制御スレッドから呼んでよい（待たない）。書き込みは writer のスレッドで行う
    - 区間（条件が同じ掃引の並び）は、掃引条件・生の値の持ち方・量と値の種類・ノーマライズの基準・校正の
      どれかが変わったら新しくする（設計H §3）
    - on_committed(stats) は1掃引の確定ごとに書き込みスレッドから呼ぶ。on_error(exc) は書けなかったとき
    - writer を渡さなければ自分用の書き込みスレッドを作り、stop / finish の後に閉じる
    """

    def __init__(
        self,
        path: Path | str,
        header: FileHeader,
        *,
        writer: Optional[WriterThread] = None,
        on_committed: Optional[Callable[[RecordingStats], None]] = None,
        on_error: Optional[Callable[[BaseException], None]] = None,
    ) -> None:
        self.path = Path(path)
        self.header = header
        self._own_writer = writer is None
        self._writer = writer or WriterThread("pygigast-recorder")
        self._on_committed = on_committed
        self._on_error = on_error
        self._lock = threading.Lock()  # 状態表示の数だけを守る（書き込みの状態は書き込みスレッドだけが触る）
        self._started_at = time.time()
        self._committed = 0
        self._segments = 0
        self._pending = 0
        self._last_error: Optional[str] = None
        self._stopped = False
        # ---- ここから下は書き込みスレッドだけが触る ----
        self._conn: Optional[sqlite3.Connection] = None
        self._seg_id = -1
        self._seg_key: Optional[str] = None
        self._seg_idx = 0
        self._acc: dict[int, _Acc] = {}
        self._opened: concurrent.futures.Future = self._writer.submit(self._open)

    # ---- 呼ぶ側 ----
    @property
    def started_at(self) -> float:
        """記録を始めた時刻（time.time() の秒）。読むだけ。"""
        return self._started_at

    @property
    def stopped(self) -> bool:
        """stop か finish を呼んだ後か（以後の append は受けない）。読むだけ。"""
        return self._stopped

    def wait_opened(self, timeout: Optional[float] = None) -> None:
        """作業ファイルを作り終えるまで待つ（作れなければその例外）。試験と開始の確認に使う。"""
        self._opened.result(timeout)

    def append(
        self,
        raw: RawSweep,
        *,
        quantity: Quantity | str = Quantity.POWER,
        value_kind: ValueKind | str = ValueKind.REAL,
        normalization: Optional[dict] = None,
        calibration=SAME_CALIBRATION,
    ) -> None:
        """掃引1回を待ち行列に入れる。生の値はここで写す（呼び側が後で配列を書き換えても影響しない）。"""
        if self._stopped:
            raise RuntimeError("記録は止まっている")
        cal = self.header.calibration if calibration is SAME_CALIBRATION else calibration
        meta = segment_meta_for(
            raw,
            quantity=quantity,
            value_kind=value_kind,
            normalization=normalization,
            calibration=None if cal == self.header.calibration else cal,
        )
        r1, r2 = split_raw(raw)
        dt = meta.dtype
        item = (meta, float(raw.started_at), _to_blob(r1, dt), None if r2 is None else _to_blob(r2, dt), np.array(
            adopted_values(meta.raw_layout, r1, r2), dtype=dt
        ))
        with self._lock:
            self._pending += 1
        self._writer.submit(self._write_one, item)

    def stop(self) -> concurrent.futures.Future:
        """記録を止める。待ち行列の掃引を全部確定してから作業ファイルを閉じる。Future の結果は RecordingStats。"""
        self._stopped = True
        fut = self._writer.submit(self._close)
        if self._own_writer:
            fut.add_done_callback(lambda _f: threading.Thread(target=self._writer.close, daemon=True).start())
        return fut

    def finish(
        self,
        h5_path: Path | str,
        *,
        traces: Sequence[TraceData] = (),
        markers: Optional[Sequence[dict]] = None,
        progress: Optional[ProgressFn] = None,
        cancel: Optional[threading.Event] = None,
    ) -> concurrent.futures.Future:
        """記録を止めて HDF5 へまとめる（設計H §4）。Future の結果は保存したパス。

        やめた（Cancelled）・失敗したときは作業ファイルが残る（あとで救出できる）。
        """
        self._stopped = True
        self._writer.submit(self._close)
        fut = self._writer.submit(
            convert_to_h5, self.path, h5_path, traces=traces, markers=markers, progress=progress, cancel=cancel
        )
        if self._own_writer:
            fut.add_done_callback(lambda _f: threading.Thread(target=self._writer.close, daemon=True).start())
        return fut

    def stats(self) -> RecordingStats:
        with self._lock:
            return RecordingStats(
                path=self.path,
                started_at=self._started_at,
                sweeps_committed=self._committed,
                segments=self._segments,
                file_bytes=sum(p.stat().st_size for p in _related_files(self.path) if p.exists()),
                pending=self._pending,
                last_error=self._last_error,
                stopped=self._stopped,
            )

    # ---- 書き込みスレッド ----
    def _open(self) -> None:
        if self.path.exists():
            raise FileExistsError(str(self.path))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = _connect(self.path)
        with conn:
            for sql in _SCHEMA:
                conn.execute(sql)
            conn.executemany(
                "INSERT INTO meta (key, value) VALUES (?, ?)",
                [
                    ("format", WORK_FORMAT),
                    ("version", str(WORK_VERSION)),
                    ("header", _header_to_json(self.header)),
                    ("started_at", repr(self._started_at)),
                ],
            )
        self._conn = conn

    def _write_one(self, item) -> None:
        meta, t, b1, b2, adopted = item
        try:
            if self._conn is None:
                raise StorageError("not_open")
            key = meta.key()
            new_seg = key != self._seg_key
            seg_id = self._seg_id + 1 if new_seg else self._seg_id
            idx = 0 if new_seg else self._seg_idx
            # 概観の塊は新しい状態を作ってから書き、確定できたときだけ入れ替える（失敗しても前の状態のまま）
            accs = {k: _Acc() for k in OVERVIEW_LEVELS} if new_seg else {k: _copy_acc(a) for k, a in self._acc.items()}
            emits = []
            for k, a in accs.items():
                if a.count == 0:
                    a.vmax = adopted.copy()
                    a.t0 = t
                else:
                    # 値の無い点（NaN）は除いて最大を取る（h5file と同じ: max_ignoring_nan）
                    np.fmax(a.vmax, adopted, out=a.vmax)
                a.count += 1
                if a.count == k:
                    emits.append((k, a.block, a.t0, _to_blob(a.vmax, meta.dtype)))
                    a.block += 1
                    a.count = 0
                    a.vmax = None
            with self._conn:  # 1掃引で1トランザクション
                if new_seg:
                    self._conn.execute("INSERT INTO segments (id, meta) VALUES (?, ?)", (seg_id, key))
                self._conn.execute(
                    "INSERT INTO sweeps (segment, idx, t_start, raw, raw2) VALUES (?, ?, ?, ?, ?)",
                    (seg_id, idx, t, b1, b2),
                )
                for k, block, t0, blob in emits:
                    self._conn.execute(
                        f"INSERT INTO overview_{k} (segment, block, t_start, raw) VALUES (?, ?, ?, ?)",
                        (seg_id, block, t0, blob),
                    )
            self._seg_id, self._seg_key, self._seg_idx, self._acc = seg_id, key, idx + 1, accs
            with self._lock:
                self._committed += 1
                self._segments = seg_id + 1
                self._pending -= 1
        except BaseException as e:
            with self._lock:
                self._pending -= 1
                self._last_error = f"{type(e).__name__}: {e}"
            if self._on_error is not None:
                self._on_error(e)
            return
        if self._on_committed is not None:
            self._on_committed(self.stats())

    def _close(self) -> RecordingStats:
        if self._conn is not None:
            with self._conn:
                self._conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('stopped_at', ?)", (repr(time.time()),))
            # WAL の中身を本体へ移してから閉じる（まとめる・救出するときに -wal を読まずに済む）
            self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            self._conn.close()
            self._conn = None
        return self.stats()


def _copy_acc(a: _Acc) -> _Acc:
    b = _Acc()
    b.vmax = None if a.vmax is None else a.vmax.copy()
    b.count, b.t0, b.block = a.count, a.t0, a.block
    return b


# ---------------------------------------------------------------------------
# 読む（記録中の再生・救出・まとめ）
# ---------------------------------------------------------------------------


class RecordingReader:
    """作業ファイルを別の接続で読む。記録中でも読める（WAL なので書き手を止めない）。

    接続は query_only にして、読む側から書き換えない。区間ごとの掃引の数は開いたとき・refresh で数える。
    """

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        if not self.path.exists():
            raise StorageError("cannot_open", {"file": self.path.name})
        try:
            self._conn = sqlite3.connect(str(self.path), timeout=5.0, check_same_thread=False)
            self._conn.execute("PRAGMA query_only=ON")
            meta = dict(self._conn.execute("SELECT key, value FROM meta").fetchall())
        except sqlite3.DatabaseError as e:
            raise StorageError("cannot_open", {"file": self.path.name, "error": str(e)}) from e
        if meta.get("format") != WORK_FORMAT:
            self._conn.close()
            raise StorageError("not_pygigast_file", {"file": self.path.name})
        if meta.get("version") != str(WORK_VERSION):
            self._conn.close()
            raise StorageError("unsupported_version", {"what": "work_file", "version": meta.get("version")})
        self.header = _header_from_json(meta["header"])
        self.started_at = float(meta["started_at"])
        self.stopped_at = float(meta["stopped_at"]) if "stopped_at" in meta else None
        self.segments: list[SegmentInfo] = []
        self.refresh()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "RecordingReader":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def refresh(self) -> None:
        """記録中に増えた掃引・区間を読み直す。"""
        rows = self._conn.execute(
            "SELECT s.id, s.meta, COUNT(w.id), MIN(w.t_start), MAX(w.t_start)"
            " FROM segments s LEFT JOIN sweeps w ON w.segment = s.id GROUP BY s.id ORDER BY s.id"
        ).fetchall()
        segs = []
        for sid, meta, n, t0, t1 in rows:
            m = SegmentMeta(**json.loads(meta))
            segs.append(
                SegmentInfo(
                    **dataclasses.asdict(m),
                    index=int(sid),
                    n_sweeps=int(n),
                    t_first=math.nan if t0 is None else float(t0),
                    t_last=math.nan if t1 is None else float(t1),
                )
            )
        self.segments = segs

    @property
    def n_sweeps(self) -> int:
        return sum(s.n_sweeps for s in self.segments)

    def read(
        self,
        seg: int,
        sweeps: Optional[tuple[int, int]] = None,
        points: Optional[tuple[int, int]] = None,
        t_range: Optional[tuple[Optional[float], Optional[float]]] = None,
    ) -> RawBlock:
        """区間の中の番号 [s0, s1)（または開始時刻の範囲、両端を含む）と点の番号 [i0, i1) の生の値。"""
        s = self.segments[seg]
        sql = "SELECT idx, t_start, raw, raw2 FROM sweeps WHERE segment = ?"
        args: list = [s.index]
        if sweeps is not None:
            sql += " AND idx >= ? AND idx < ?"
            args += [int(sweeps[0]), int(sweeps[1])]
        if t_range is not None:
            if t_range[0] is not None:
                sql += " AND t_start >= ?"
                args.append(float(t_range[0]))
            if t_range[1] is not None:
                sql += " AND t_start <= ?"
                args.append(float(t_range[1]))
        rows = self._conn.execute(sql + " ORDER BY idx", args).fetchall()
        i0, i1 = points if points is not None else (0, s.points)
        n = len(rows)
        dt = s.dtype
        t = np.array([r[1] for r in rows], dtype=np.float64)
        raw = np.empty((n, i1 - i0), dtype=dt)
        tail = raw2_tail(s)
        full2 = np.empty((n,) + tail, dtype=dt) if tail is not None and n and rows[0][3] is not None else None
        for j, r in enumerate(rows):
            raw[j] = _from_blob(r[2], (s.points,), dt)[i0:i1]
            if full2 is not None:
                full2[j] = _from_blob(r[3], tail, dt)
        s0 = int(rows[0][0]) if n else 0
        if s.raw_layout == LAYOUT_IMAGE_REJECT:
            # 区間ごとの回を [i0, i1) に入る部分だけにする（区間の並びも切る: raw_segments.py）
            sub = rs.clip(s.raw_segments or [], i0, i1)
            raw2 = None
            if full2 is not None:
                raw2, sub = rs.clip_flat(full2, s.raw_segments or [], i0, i1)
            return RawBlock(s.index, s.raw_layout, s0, i0, t, s.freqs(i0, i1), raw, raw2, sub)
        raw2 = None if full2 is None else full2[..., i0:i1]
        return RawBlock(s.index, s.raw_layout, s0, i0, t, s.freqs(i0, i1), raw, raw2)

    def times(self, seg: int) -> np.ndarray:
        """区間（並びの位置）の掃引ごとの開始時刻（区間の中の番号の順）。"""
        s = self.segments[seg]
        rows = self._conn.execute("SELECT t_start FROM sweeps WHERE segment = ? ORDER BY idx", (s.index,)).fetchall()
        return np.array([r[0] for r in rows], dtype=np.float64)

    def overview_range(self, seg: int, k: int, b0: int, b1: int) -> np.ndarray:
        """概観の段 k の塊 [b0, b1) の値 [塊, 点]（揃った塊だけ。再生のウォーターフォール: 設計P §5）。"""
        if k not in OVERVIEW_LEVELS:
            raise ValueError(f"概観の段は {OVERVIEW_LEVELS} のどれか")
        s = self.segments[seg]
        rows = self._conn.execute(
            f"SELECT raw FROM overview_{k} WHERE segment = ? AND block >= ? AND block < ? ORDER BY block",
            (s.index, int(b0), int(b1))).fetchall()
        vals = np.empty((len(rows), s.points), dtype=s.dtype)
        for j, r in enumerate(rows):
            vals[j] = _from_blob(r[0], (s.points,), s.dtype)
        return vals

    def overview(self, seg: int, k: int, points: Optional[tuple[int, int]] = None) -> OverviewBlock:
        """概観の段 k の、k 掃引が揃った塊だけ（最後の揃っていない塊は含まない。要るなら read で読む）。"""
        if k not in OVERVIEW_LEVELS:
            raise ValueError(f"概観の段は {OVERVIEW_LEVELS} のどれか")
        s = self.segments[seg]
        i0, i1 = points if points is not None else (0, s.points)
        rows = self._conn.execute(
            f"SELECT t_start, raw FROM overview_{k} WHERE segment = ? ORDER BY block", (s.index,)
        ).fetchall()
        vals = np.empty((len(rows), i1 - i0), dtype=s.dtype)
        for j, r in enumerate(rows):
            vals[j] = _from_blob(r[1], (s.points,), s.dtype)[i0:i1]
        t = np.array([r[0] for r in rows], dtype=np.float64)
        return OverviewBlock(s.index, k, i0, t, s.freqs(i0, i1), vals)


class _SqliteSegment(SegmentSource):
    def __init__(self, reader: RecordingReader, pos: int) -> None:
        seg = reader.segments[pos]
        self._r = reader
        self._pos = pos
        self._seg = seg
        self.meta = SegmentMeta(**{f.name: getattr(seg, f.name) for f in dataclasses.fields(SegmentMeta)})
        self.n_sweeps = seg.n_sweeps

    def blocks(self, rows: int):
        for s0 in range(0, self.n_sweeps, rows):
            b = self._r.read(self._pos, (s0, min(s0 + rows, self.n_sweeps)))
            if len(b.t_start) != min(rows, self.n_sweeps - s0):
                raise StorageError("corrupt_sweep", {"segment": self._seg.index, "from": s0})
            yield b.t_start, b.raw, b.raw2


# ---------------------------------------------------------------------------
# まとめる・救出する
# ---------------------------------------------------------------------------


def convert_to_h5(
    sqlite_path: Path | str,
    h5_path: Path | str,
    *,
    traces: Sequence[TraceData] = (),
    markers: Optional[Sequence[dict]] = None,
    progress: Optional[ProgressFn] = None,
    cancel: Optional[threading.Event] = None,
    delete_source: bool = True,
) -> Path:
    """作業ファイルを HDF5 の保存ファイルへまとめる（設計H §2・§7.2）。書き込みスレッドで呼ぶ。

    一時ファイルに書いて名前を付け替え、**付け替えが済んでから**作業ファイルを消す。やめた・失敗したら
    HDF5 の一時ファイルを消し、作業ファイルは残す。掃引が1つも無ければ StorageError("no_data")（作業ファイルは残す）。
    """
    sqlite_path = Path(sqlite_path)
    reader = RecordingReader(sqlite_path)
    try:
        if reader.n_sweeps == 0:
            raise StorageError("no_data", {"file": sqlite_path.name})
        sources = [_SqliteSegment(reader, i) for i, s in enumerate(reader.segments) if s.n_sweeps > 0]
        extra = {"recording": {"started_at": reader.started_at, "stopped_at": reader.stopped_at}}
        out = write_h5(
            h5_path, reader.header, sources, traces, markers, progress=progress, cancel=cancel, extra_root_attrs=extra
        )
    finally:
        reader.close()
    if delete_source:
        discard_recording(sqlite_path)
    return out


@dataclass(frozen=True)
class UnfinishedRecording:
    """まとめ終わっていない作業ファイル（起動時に救出を尋ねる材料: 設計H §4）。"""

    path: Path
    started_at: float
    n_sweeps: int
    t_first: float
    t_last: float
    stopped: bool  # 止めた記録（まとめる前に落ちた・まとめをやめた）か、記録中に落ちたか
    device_model: str
    error: Optional[str] = None  # 開けなかったときの理由（消さずに示す）

    @property
    def duration_s(self) -> float:
        return 0.0 if self.n_sweeps == 0 else self.t_last - self.t_first


def find_unfinished(directory: Path | str, exclude: Sequence[Path | str] = ()) -> list[UnfinishedRecording]:
    """フォルダの中のまとめ終わっていない作業ファイルを、開始日時の順に並べる。記録中のもの（exclude）は除く。"""
    d = Path(directory)
    if not d.is_dir():
        return []
    skip = {Path(p).resolve() for p in exclude}
    out: list[UnfinishedRecording] = []
    for p in sorted(d.glob(WORK_PREFIX + "*" + WORK_SUFFIX)):
        if p.resolve() in skip:
            continue
        try:
            with RecordingReader(p) as r:
                segs = [s for s in r.segments if s.n_sweeps]
                out.append(
                    UnfinishedRecording(
                        path=p,
                        started_at=r.started_at,
                        n_sweeps=r.n_sweeps,
                        t_first=min((s.t_first for s in segs), default=math.nan),
                        t_last=max((s.t_last for s in segs), default=math.nan),
                        stopped=r.stopped_at is not None,
                        device_model=r.header.device_model,
                    )
                )
        except (StorageError, sqlite3.DatabaseError, KeyError, ValueError) as e:
            out.append(UnfinishedRecording(p, p.stat().st_mtime, 0, math.nan, math.nan, False, "", f"{type(e).__name__}: {e}"))
    out.sort(key=lambda u: u.started_at)
    return out
