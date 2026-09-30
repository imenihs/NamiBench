"""個体の校正データ（設計D §4・§5.2、設計Q §2.2 の②）。

機種のプロファイル・表示設定とは別のファイルに、シリアル番号ごとに持つ。
新しい形式では値をすべて「足すもの」にそろえる（旧 Common-Bias は減算だったので符号を反転する）。
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

from .legacy_cal import HF_BANDS, HF_POINTS, LegacyCal

FORMAT = "pygigast-calibration"
VERSION = 1
CURRENT_FORMAT = "pygigast-calibration-current"
CURRENT_FILE = "current.json"

# 周波数特性補正の点の間隔。band の幅はその10倍（1GHz）
FREQ_STEP_HZ = 100_000_000
POINTS_PER_BAND = HF_POINTS

# 旧アプリで選べた RBW（Hz）
_RBW_180K = 180_000
_RBW_30K = 30_000
_RBW_15K = 15_000

# 旧ファイルは b3=1（イメージキャンセル OFF）のレベルで校正されている（設計K・設計B §7.2）
_LEGACY_B3_BASIS = 1

# シリアル番号はフォルダ名に使うので、区切り文字や ".." を含むものを受け付けない
_SERIAL_RE = re.compile(r"[A-Za-z0-9_-][A-Za-z0-9._-]*")


@dataclass(frozen=True)
class FreqBand:
    """周波数特性補正の1 band。values[k] は start_hz + k × step_hz の補正（dB、加算）。"""

    start_hz: int
    values: tuple[float, ...]


@dataclass(frozen=True)
class CalibrationSource:
    """校正の取得方法（設計D §4）。kind は "legacy_file"（旧ファイル読込）など。"""

    kind: str
    file_name: Optional[str] = None
    sha256: Optional[str] = None


@dataclass(frozen=True)
class NfCalibration:
    """NF 用の値（設計M で使う）。amp_raw は旧アプリの値のまま（倍率 10^(値/200)）。"""

    enr_db: float
    amp_raw: float


@dataclass(frozen=True)
class Calibration:
    """個体の校正データ。すべての補正は加算（設計D §5.2）。"""

    model: str
    serial: str
    created_at: str  # ISO 8601（時差付き）
    source: CalibrationSource
    b3_basis: int
    level_offset_db: float
    rbw_offset_db: dict[int, float]  # キーは RBW の Hz（int）
    freq_step_hz: int
    bands: tuple[FreqBand, ...]
    nf: Optional[NfCalibration] = None
    version: int = VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.freq_step_hz, int) or self.freq_step_hz <= 0:
            raise ValueError("freq_step_hz は正の int（Hz）")
        band_span = self.freq_step_hz * (POINTS_PER_BAND - 1)
        for i, b in enumerate(self.bands):
            if not isinstance(b.start_hz, int):
                raise TypeError("周波数は int（Hz）で持つ")
            # 換算は band の番号を f // band 幅で決めるので、band は 0Hz から隙間なく並ぶ前提
            if b.start_hz != i * band_span:
                raise ValueError(f"bands[{i}].start_hz は {i * band_span} であること")
            if len(b.values) != POINTS_PER_BAND:
                raise ValueError(f"bands[{i}].values は {POINTS_PER_BAND} 点であること")
        for k in self.rbw_offset_db:
            if not isinstance(k, int):
                raise TypeError("rbw_offset_db のキーは int（Hz）")

    @property
    def band_span_hz(self) -> int:
        return self.freq_step_hz * (POINTS_PER_BAND - 1)

    # ---- JSON との変換 ----

    def to_dict(self) -> dict:
        d = {
            "format": FORMAT,
            "version": self.version,
            "device": {"model": self.model, "serial": self.serial},
            "created_at": self.created_at,
            "source": {"kind": self.source.kind},
            "b3_basis": self.b3_basis,
            "level_offset_db": self.level_offset_db,
            # JSON のキーは文字列になるので Hz の整数を文字列で書く
            "rbw_offset_db": {str(k): v for k, v in self.rbw_offset_db.items()},
            "freq_response_db": {
                "step_hz": self.freq_step_hz,
                "bands": [{"start_hz": b.start_hz, "values": list(b.values)} for b in self.bands],
            },
        }
        if self.source.file_name is not None:
            d["source"]["file_name"] = self.source.file_name
        if self.source.sha256 is not None:
            d["source"]["sha256"] = self.source.sha256
        if self.nf is not None:
            d["nf"] = {"enr_db": self.nf.enr_db, "amp_raw": self.nf.amp_raw}
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Calibration":
        if d.get("format") != FORMAT:
            raise ValueError(f"format が {FORMAT} でない")
        if d.get("version") != VERSION:
            raise ValueError(f"version {d.get('version')!r} は読めない")
        src = d["source"]
        fr = d["freq_response_db"]
        nf = d.get("nf")
        return cls(
            model=str(d["device"]["model"]),
            serial=str(d["device"]["serial"]),
            created_at=str(d["created_at"]),
            source=CalibrationSource(
                kind=str(src["kind"]), file_name=src.get("file_name"), sha256=src.get("sha256")
            ),
            b3_basis=int(d["b3_basis"]),
            level_offset_db=float(d["level_offset_db"]),
            rbw_offset_db={int(k): float(v) for k, v in d["rbw_offset_db"].items()},
            freq_step_hz=_as_int_hz(fr["step_hz"]),
            bands=tuple(
                FreqBand(start_hz=_as_int_hz(b["start_hz"]), values=tuple(b["values"]))
                for b in fr["bands"]
            ),
            nf=None if nf is None else NfCalibration(enr_db=float(nf["enr_db"]), amp_raw=float(nf["amp_raw"])),
            version=int(d["version"]),
        )

    # ---- 旧ファイルからの取り込み ----

    @classmethod
    def from_legacy(
        cls,
        legacy: LegacyCal,
        model: str,
        serial: str,
        file_name: str,
        sha256: str,
        created_at: datetime | str,
    ) -> "Calibration":
        """旧ファイルの値を新しい形式にする（設計D §5.2）。

        旧ファイルにはシリアル番号が無いので、どの機器の校正かは呼ぶ側（利用者の選択）が渡す。
        """
        if isinstance(created_at, datetime):
            if created_at.tzinfo is None:
                raise ValueError("created_at は時差付きで渡す")
            created_at = created_at.isoformat(timespec="seconds")
        band_span = FREQ_STEP_HZ * (POINTS_PER_BAND - 1)
        return cls(
            model=model,
            serial=serial,
            created_at=created_at,
            source=CalibrationSource(kind="legacy_file", file_name=file_name, sha256=sha256),
            b3_basis=_LEGACY_B3_BASIS,
            # 旧 Common-Bias は減算。新しい形式は加算にそろえるので符号を反転する
            level_offset_db=_plus_zero(-legacy.common_bias),
            rbw_offset_db={_RBW_180K: 0.0, _RBW_30K: float(legacy.adj30k), _RBW_15K: float(legacy.adj15k)},
            freq_step_hz=FREQ_STEP_HZ,
            bands=tuple(
                FreqBand(start_hz=i * band_span, values=tuple(legacy.hf[i])) for i in range(HF_BANDS)
            ),
            nf=NfCalibration(enr_db=legacy.enr_x10 / 10.0, amp_raw=float(legacy.nf_amp_raw)),
        )


def _plus_zero(x: float) -> float:
    """-0.0 を 0.0 にする（JSON に "-0.0" を書かない）。"""
    return float(x) + 0.0


def _as_int_hz(v) -> int:
    """JSON の周波数を int にする。小数部のある値は受け付けない（周波数は float で持たない）。"""
    if isinstance(v, bool):
        raise TypeError("周波数は int（Hz）")
    if isinstance(v, int):
        return v
    if isinstance(v, float) and v.is_integer():
        return int(v)
    raise TypeError(f"周波数は int（Hz）: {v!r}")


# ---- 置き場所 ----


def default_root() -> Path:
    """校正データの置き場所（利用者のデータの場所）。Windows は %APPDATA%\\pyGigaSt\\calibration。"""
    appdata = os.environ.get("APPDATA")
    if appdata:
        return Path(appdata) / "pyGigaSt" / "calibration"
    return Path.home() / ".pygigast" / "calibration"


class CalibrationStore:
    """<root>/<シリアル番号>/<日時>.json に校正を足していき、current.json が使うものを指す。

    校正をやり直すたびに新しいファイルを足し、古いものは消さない・上書きしない（履歴。設計D §5.2）。
    """

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def _dir(self, serial: str) -> Path:
        if not _SERIAL_RE.fullmatch(serial or ""):
            raise ValueError(f"フォルダ名に使えないシリアル番号: {serial!r}")
        return self.root / serial

    def save(self, cal: Calibration, make_current: bool = True) -> Path:
        """新しいファイルとして保存する。既存のファイルは上書きしない。"""
        d = self._dir(cal.serial)
        d.mkdir(parents=True, exist_ok=True)
        stem = _timestamp_stem(cal.created_at)
        body = json.dumps(cal.to_dict(), ensure_ascii=False, indent=2)
        n = 0
        while True:
            name = f"{stem}.json" if n == 0 else f"{stem}-{n}.json"
            path = d / name
            try:
                # "x" は既にあれば失敗するので、同じ日時でも上書きしない
                with open(path, "x", encoding="utf-8") as f:
                    f.write(body)
                break
            except FileExistsError:
                n += 1
        if make_current:
            self.set_current(cal.serial, path.name)
        return path

    def set_current(self, serial: str, file_name: str) -> None:
        """使う校正を切り替える（履歴の中から選ぶ）。"""
        d = self._dir(serial)
        if Path(file_name).name != file_name or file_name == CURRENT_FILE:
            raise ValueError(f"校正ファイルの名前でない: {file_name!r}")
        if not (d / file_name).is_file():
            raise FileNotFoundError(d / file_name)
        tmp = d / (CURRENT_FILE + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"format": CURRENT_FORMAT, "version": 1, "file": file_name}, f)
        os.replace(tmp, d / CURRENT_FILE)

    def current_file(self, serial: str) -> Optional[Path]:
        """使っている校正のファイル。無ければ None。"""
        d = self._dir(serial)
        cur = d / CURRENT_FILE
        if not cur.is_file():
            return None
        with open(cur, encoding="utf-8") as f:
            ptr = json.load(f)
        if ptr.get("format") != CURRENT_FORMAT:
            raise ValueError(f"{CURRENT_FILE} の format が違う")
        name = ptr.get("file")
        if not isinstance(name, str) or Path(name).name != name:
            raise ValueError(f"{CURRENT_FILE} の file が不正: {name!r}")
        return d / name

    def load_current(self, serial: str) -> Optional[Calibration]:
        """使っている校正を読む。無ければ None（校正データなしで換算する: 設計D §5.5）。"""
        path = self.current_file(serial)
        if path is None:
            return None
        return load_file(path)

    def history(self, serial: str) -> list[Path]:
        """保存してある校正のファイル（名前順）。"""
        d = self._dir(serial)
        if not d.is_dir():
            return []
        return sorted(p for p in d.glob("*.json") if p.name != CURRENT_FILE)


def load_file(path: Path) -> Calibration:
    with open(path, encoding="utf-8") as f:
        return Calibration.from_dict(json.load(f))


def _timestamp_stem(created_at: str) -> str:
    """ファイル名に使う日時（Windows で使えない ":" を含めない）。例: 20260930T210000+0900。"""
    dt = datetime.fromisoformat(created_at)
    return dt.strftime("%Y%m%dT%H%M%S%z")
