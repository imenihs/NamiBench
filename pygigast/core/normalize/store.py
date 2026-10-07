"""基準のファイルへの保存・読込・一覧（設計E §4・§6.1）。

置き場所のフォルダは呼ぶ側が渡す（利用者のデータの場所の references/<シリアル番号>/ など）。
1つの基準を1つの HDF5 ファイルにする（保存ファイルと数値の配列の形式をそろえる: 設計E §6.1・設計H §7）。
周波数は int64 のまま書き、float にしない（設計A §2.1）。使えない点は NaN のまま書く。
書くときは一時ファイルに書いてから置き換える（書きかけで落ちても前の基準が残る）。
to_dict / from_dict は中身の検査と試験のための辞書の形（ファイルの形ではない）。
"""
from __future__ import annotations

import json

import h5py
import math
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np

from ..sweep import image_reject as ir
from ..types import FreqAxis, Mode
from .reference import Reference, ReferenceSegment, check_value_db

FORMAT = "pygigast-reference"
# 2 でイメージキャンセル（区間の image_cancel 真偽）をイメージ除去（image_reject）に置き換えた（設計F §7.3）。
# 1 は読むときに true → "fast"、false → "off" に読み替える。
# 3 で基準の値（value_db: 設計E §6.7）を足した。2 以前は 0dB として読む。値を知らない前の版のアプリが 3 を読むと
# 基準の値の分だけ違う数を出すので、版を上げて読ませない
VERSION = 3
READABLE_VERSIONS = (1, 2, 3)
SUFFIX = ".h5"
# id はファイル名に使うので、フォルダの外を指す文字を許さない
_ID_RE = re.compile(r"^[0-9A-Za-z_-]{1,64}$")


class ReferenceFileError(ValueError):
    """基準のファイルとして読めない（形式・版が違う・中身が壊れている）。"""


@dataclass(frozen=True)
class ReferenceInfo:
    """一覧の1行（中身の数値は読まない）。"""

    ref_id: str
    name: str
    serial: str
    model: str
    mode: Mode
    taken_at: Optional[float]
    path: Path
    value_db: float = 0.0  # 基準の値（設計E §6.7）


def _int(v: object, what: str) -> int:
    if isinstance(v, bool) or not isinstance(v, int):
        raise ReferenceFileError(f"{what} が int でない")
    return v


def to_dict(ref: Reference) -> dict:
    return {
        "format": FORMAT,
        "version": VERSION,
        "id": ref.ref_id,
        "name": ref.name,
        "model": ref.model,
        "serial": ref.serial,
        "mode": ref.mode.value,
        "band_edges_hz": [int(e) for e in ref.band_edges_hz],
        "value_db": float(ref.value_db),
        "segments": [
            {
                "start_hz": int(s.axis.start_hz),
                "step_hz": int(s.axis.step_hz),
                "points": int(s.axis.points),
                "power_w": [None if not math.isfinite(v) else float(v) for v in s.power_w.tolist()],
                "image_reject": str(s.image_reject),
                "rbw_hz": int(s.rbw_hz),
                "connected_at": float(s.connected_at),
                "taken_at": float(s.taken_at),
                "kind": s.kind,
                "averages": int(s.averages),
            }
            for s in ref.segments
        ],
    }


def from_dict(d: dict) -> Reference:
    if not isinstance(d, dict) or d.get("format") != FORMAT:
        raise ReferenceFileError("基準のファイルでない")
    if d.get("version") not in READABLE_VERSIONS:
        raise ReferenceFileError(f"対応していない版: {d.get('version')}")
    _check_id(str(d.get("id", "")))
    try:
        segs = []
        for s in d["segments"]:
            axis = FreqAxis(_int(s["start_hz"], "start_hz"), _int(s["step_hz"], "step_hz"), _int(s["points"], "points"))
            power = np.array([np.nan if v is None else float(v) for v in s["power_w"]], dtype=np.float64)
            segs.append(
                ReferenceSegment(
                    axis=axis,
                    power_w=power,
                    image_reject=_reject_of(s),
                    rbw_hz=_int(s["rbw_hz"], "rbw_hz"),
                    connected_at=float(s["connected_at"]),
                    taken_at=float(s["taken_at"]),
                    kind=str(s.get("kind", "fine")),
                    averages=int(s.get("averages", 1)),
                )
            )
        return Reference(
            name=str(d["name"]),
            model=str(d.get("model", "")),
            serial=str(d["serial"]),
            mode=Mode(d["mode"]),
            band_edges_hz=tuple(_int(e, "band_edges_hz") for e in d.get("band_edges_hz", [])),
            segments=segs,
            ref_id=str(d["id"]),
            value_db=_value_of(d),
        )
    except (KeyError, TypeError, ValueError) as e:
        if isinstance(e, ReferenceFileError):
            raise
        raise ReferenceFileError(f"中身が壊れている: {e}") from e


def _value_of(d: dict) -> float:
    """基準の値（設計E §6.7）。2 以前の版には無いので 0dB。数でない・範囲の外は壊れている（ValueError）。"""
    v = d.get("value_db", 0.0)
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        v = float(v)
    return check_value_db(v)


def _reject_of(s: dict) -> str:
    """区間のイメージ除去。前の版の image_cancel は読み替える（設計F §7.3）。どちらも無ければ KeyError（壊れている）。"""
    v = ir.read_choice(s)
    if v is None:
        raise KeyError("image_reject")
    return v


def _check_id(ref_id: str) -> None:
    if not _ID_RE.match(ref_id):
        raise ReferenceFileError(f"基準の id に使えない文字がある: {ref_id!r}")


def path_for(directory: Path | str, ref: Reference) -> Path:
    """ファイル名は基準の id（名前は利用者が変えられ、使えない文字も入りうるので使わない）。"""
    _check_id(ref.ref_id)
    return Path(directory) / f"{ref.ref_id}{SUFFIX}"


def save_reference(directory: Path | str, ref: Reference) -> Path:
    d = Path(directory)
    d.mkdir(parents=True, exist_ok=True)
    path = path_for(d, ref)
    fd, tmp = tempfile.mkstemp(prefix=".tmp_", suffix=SUFFIX, dir=d)
    os.close(fd)
    try:
        _write_h5(tmp, ref)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return path


def _write_h5(path: str, ref: Reference) -> None:
    d = to_dict(ref)
    with h5py.File(path, "w") as f:
        for k in ("format", "version", "id", "name", "model", "serial", "mode"):
            f.attrs[k] = d[k]
        f.attrs["value_db"] = float(d["value_db"])
        f.attrs["band_edges_hz"] = np.asarray(d["band_edges_hz"], dtype=np.int64)
        g = f.create_group("segments")
        for i, seg in enumerate(ref.segments):
            sg = g.create_group(str(i))
            sg.create_dataset("power_w", data=np.asarray(seg.power_w, dtype=np.float64))
            sg.attrs["start_hz"] = np.int64(seg.axis.start_hz)
            sg.attrs["step_hz"] = np.int64(seg.axis.step_hz)
            sg.attrs["points"] = np.int64(seg.axis.points)
            sg.attrs["image_reject"] = str(seg.image_reject)
            sg.attrs["rbw_hz"] = np.int64(seg.rbw_hz)
            sg.attrs["connected_at"] = float(seg.connected_at)
            sg.attrs["taken_at"] = float(seg.taken_at)
            sg.attrs["kind"] = seg.kind
            sg.attrs["averages"] = np.int64(seg.averages)


def _attr(v):
    """HDF5 の属性を Python の値に戻す（int64 は int、bytes は str）。"""
    if isinstance(v, (bytes, np.bytes_)):
        return v.decode("utf-8")
    if isinstance(v, np.bool_):
        return bool(v)
    if isinstance(v, np.integer):
        return int(v)
    if isinstance(v, np.floating):
        return float(v)
    if isinstance(v, np.ndarray):
        return [int(x) for x in v.tolist()] if v.dtype.kind in "iu" else v.tolist()
    return v


def load_reference(path: Path | str) -> Reference:
    try:
        with h5py.File(path, "r") as f:
            d = {k: _attr(v) for k, v in f.attrs.items()}
            if d.get("format") != FORMAT:
                raise ReferenceFileError("基準のファイルでない")
            if d.get("version") not in READABLE_VERSIONS:
                raise ReferenceFileError(f"対応していない版: {d.get('version')}")
            segs = []
            g = f.get("segments")
            for key in sorted(g.keys(), key=int) if g is not None else []:
                sg = g[key]
                a = {k: _attr(v) for k, v in sg.attrs.items()}
                a["power_w"] = np.asarray(sg["power_w"][()], dtype=np.float64)
                segs.append(a)
    except OSError as e:
        raise ReferenceFileError(f"HDF5 として読めない: {e}") from e
    except (KeyError, TypeError, ValueError) as e:
        if isinstance(e, ReferenceFileError):
            raise
        raise ReferenceFileError(f"中身が壊れている: {e}") from e
    d["segments"] = segs
    return from_dict(d)


def list_references(directory: Path | str) -> list[ReferenceInfo]:
    """フォルダの基準を、取得日時の新しい順に並べる。読めないファイルは飛ばす。"""
    d = Path(directory)
    if not d.is_dir():
        return []
    out: list[ReferenceInfo] = []
    for p in d.glob(f"*{SUFFIX}"):
        if p.name.startswith(".tmp_"):
            continue
        try:
            ref = load_reference(p)
        except (OSError, ReferenceFileError):
            continue
        out.append(ReferenceInfo(ref.ref_id, ref.name, ref.serial, ref.model, ref.mode, ref.taken_at, p, ref.value_db))
    out.sort(key=lambda i: (i.taken_at is None, -(i.taken_at or 0.0)))
    return out


def delete_reference(directory: Path | str, ref_id: str) -> bool:
    _check_id(ref_id)
    p = Path(directory) / f"{ref_id}{SUFFIX}"
    try:
        p.unlink()
        return True
    except FileNotFoundError:
        return False
