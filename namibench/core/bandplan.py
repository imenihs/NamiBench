"""バンドプランとブックマーク（設計P §6・§9.5）。

- バンドプラン: 周波数軸に沿って帯（例: アマチュアバンドの区分）を色で重ねる。ブックマーク: 周波数1点に名前
- 中身は利用者が編集できる JSON のファイル。公的な資料は同梱しない（権利を確かめていない: P §9.1）。形と空の見本だけを
  同梱し（bandplan_sample.json）、利用者が作って読み込む。読み込んだ中身はアプリのデータの bandplan.json に写して持つ
  （Qt と Web で共有・次の起動で戻す）。ブックマークは画面からも足せる・消せる（同じファイルへ書く）
- 周波数は Hz の整数（設計A §2.1）。色は #rrggbb

形::

    {"format": "pygigast-bandplan", "version": 1,
     "bands": [{"name": "430MHz 帯", "start_hz": 430000000, "stop_hz": 440000000, "color": "#4fa3ff"}],
     "bookmarks": [{"name": "ビーコン", "hz": 434500000}]}

読めないものは開かずに理由（どの行の何か）を返す（知らない版も開かない: 設計H §7.1 と同じ）。Qt を import しない。
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Optional

FORMAT = "pygigast-bandplan"
VERSION = 1
SAMPLE_PATH = Path(__file__).parent / "bandplan_sample.json"
# 名前の長さと数の上限（打ち間違いで画面が使えなくなるのを防ぐ。設計に値は無い）
MAX_NAME_CHARS = 60
MAX_ITEMS = 2000
FREQ_LIMIT_HZ = 1_000_000_000_000  # 1THz（周波数オフセットと同じ上限）

_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")


class BandplanError(ValueError):
    """読めない。code は理由の種類、detail はどこが（画面の文言は画面側: U-22）。"""

    def __init__(self, code: str, detail: Optional[dict] = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail or {}


def empty() -> dict:
    return {"format": FORMAT, "version": VERSION, "bands": [], "bookmarks": []}


def _name(v, where: dict) -> str:
    if not isinstance(v, str) or not v.strip():
        raise BandplanError("bandplan_bad_name", where)
    v = v.strip()
    if len(v) > MAX_NAME_CHARS:
        raise BandplanError("bandplan_bad_name", where)
    return v


def _hz(v, where: dict) -> int:
    if isinstance(v, bool) or not isinstance(v, int) or abs(v) > FREQ_LIMIT_HZ:
        raise BandplanError("bandplan_bad_freq", where)
    return int(v)


def check_name(name) -> str:
    return _name(name, {"field": "name"})


def check_hz(hz) -> int:
    return _hz(hz, {"field": "hz"})


def parse(d) -> dict:
    """読んだ JSON（dict）を確かめて、きれいな形にする。だめなら BandplanError。"""
    if not isinstance(d, dict) or d.get("format") != FORMAT:
        raise BandplanError("bandplan_not_format")
    if d.get("version") != VERSION:
        raise BandplanError("bandplan_unsupported_version", {"version": d.get("version")})
    bands_in, marks_in = d.get("bands", []), d.get("bookmarks", [])
    if not isinstance(bands_in, list) or not isinstance(marks_in, list):
        raise BandplanError("bandplan_not_format")
    if len(bands_in) + len(marks_in) > MAX_ITEMS:
        raise BandplanError("bandplan_too_many", {"max": MAX_ITEMS})
    bands = []
    for i, b in enumerate(bands_in):
        where = {"kind": "band", "index": i + 1}
        if not isinstance(b, dict):
            raise BandplanError("bandplan_not_format", where)
        name = _name(b.get("name"), where)
        lo, hi = _hz(b.get("start_hz"), where), _hz(b.get("stop_hz"), where)
        if lo >= hi:
            raise BandplanError("bandplan_band_order", {**where, "name": name})
        color = b.get("color")
        if not isinstance(color, str) or not _HEX.match(color):
            raise BandplanError("bandplan_bad_color", {**where, "name": name})
        bands.append({"name": name, "start_hz": lo, "stop_hz": hi, "color": color.lower()})
    marks = []
    for i, m in enumerate(marks_in):
        where = {"kind": "bookmark", "index": i + 1}
        if not isinstance(m, dict):
            raise BandplanError("bandplan_not_format", where)
        marks.append({"name": _name(m.get("name"), where), "hz": _hz(m.get("hz"), where)})
    marks.sort(key=lambda m: m["hz"])
    return {"format": FORMAT, "version": VERSION, "bands": bands, "bookmarks": marks}


def load(path: Path | str) -> dict:
    """ファイルを読んで確かめる。読めない・JSON でないときも BandplanError。"""
    p = Path(path)
    try:
        text = p.read_text(encoding="utf-8-sig")
    except OSError as e:
        raise BandplanError("bandplan_unreadable", {"file": p.name, "message": str(e)}) from e
    try:
        d = json.loads(text)
    except ValueError as e:
        raise BandplanError("bandplan_not_json", {"file": p.name, "message": str(e)}) from e
    return parse(d)


def dumps(plan: dict) -> str:
    """利用者が読んで直せる形（字下げ・日本語のまま）。"""
    return json.dumps(plan, ensure_ascii=False, indent=2) + "\n"


def save(path: Path | str, plan: dict) -> Path:
    """一時ファイルに書いてから置き換える（途中で落ちても前の中身が残る: 設計O §1.1）。"""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".partial")
    tmp.write_text(dumps(parse(plan)), encoding="utf-8")
    os.replace(tmp, p)
    return p


def bands_in(plan: dict, lo_hz: int, hi_hz: int) -> list[dict]:
    """[lo, hi] に重なる帯。"""
    return [b for b in plan.get("bands") or [] if b["stop_hz"] >= lo_hz and b["start_hz"] <= hi_hz]


def bookmarks_in(plan: dict, lo_hz: int, hi_hz: int) -> list[dict]:
    return [m for m in plan.get("bookmarks") or [] if lo_hz <= m["hz"] <= hi_hz]
