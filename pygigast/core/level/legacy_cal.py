"""旧アプリの校正ファイル（gigast.txt / 個別補正データ ba205.txt）の読込（設計D §5.1）。

読むのは 1行目の RBW 補正と全体レベル、2行目の NF 用の値、3〜14行目の周波数特性補正だけ。
15行目以降（直前掃引の値）と、表示・COM・窓位置などの設定値は読まない（設計D §2）。
旧形式への書き出しはしない（設計D §4）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# 1行を 6文字 × 16個に切る（17文字目以降の "." は捨てる）
FIELD_WIDTH = 6
FIELDS_PER_LINE = 16

# 読む行の数（1〜2行目の設定値 ＋ 3〜14行目の周波数特性補正）
REQUIRED_LINES = 14
HF_FIRST_LINE = 3  # 1始まりの行番号
HF_BANDS = 12  # band 0〜11（各 1GHz）
HF_POINTS = 11  # band の開始から 100MHz 刻みで開始＋1000MHz まで

# 1行目・2行目で読む欄（0始まり）
_LINE1_ADJ30K = 13
_LINE1_ADJ15K = 14
_LINE1_COMMON_BIAS = 15
_LINE2_ENR = 5
_LINE2_NF_AMP = 6

# VB の Val() が数として読む先頭部分: 符号1つ・数字・小数点1つ（指数や途中の空白は扱わない: 設計D §5.1 の4）
_VAL_RE = re.compile(r"[+-]?(\d*\.?\d*)")


class LegacyCalError(ValueError):
    """旧校正ファイルが読めない。取り込まない（設計D §5.1）。

    line は 1始まりの行番号、field は 0始まりの欄番号（行が足りないときは None）。
    reason は理由の種類のコード。画面の文言は画面側が決める（U-22）。
    """

    def __init__(self, reason: str, line: int | None, field: int | None, text: str = "") -> None:
        where = f"line={line}" + (f" field={field}" if field is not None else "")
        super().__init__(f"{reason} ({where}) {text!r}".rstrip())
        self.reason = reason
        self.line = line
        self.field = field
        self.text = text


@dataclass(frozen=True)
class LegacyCal:
    """旧校正ファイルから取り出した値。符号は旧アプリのまま（Common-Bias は減算）。

    新しい形式（すべて加算）への符号の変換は calibration.Calibration.from_legacy が行う。
    """

    adj30k: float  # RBW 30kHz の補正（加算）
    adj15k: float  # RBW 15kHz の補正（加算）
    common_bias: float  # 全体レベルの補正（旧アプリでは減算）
    enr_x10: float  # NF の ENR（0.1dB 単位）
    nf_amp_raw: float  # NF の増幅度。倍率は 10^(値/200)（設計M で使う）
    hf: tuple[tuple[int, ...], ...]  # 12 band × 11点の周波数特性補正（整数 dB、加算）


def vb_val(text: str) -> float:
    """VB の Val() と同じ規則で数を読む（設計D §5.1 の4）。

    先頭の空白を飛ばし、符号と数字と小数点が続く限り読み、それ以外で止める。
    何も読めなければ 0（空欄も 0）。
    """
    m = _VAL_RE.match(text.lstrip(" \t"))
    body = m.group(0) if m else ""
    digits = m.group(1) if m else ""
    if not any(c.isdigit() for c in digits):
        return 0.0
    return float(body)


def _starts_with_number(text: str) -> bool:
    """欄が数として読めるか（空欄は読めるとみなす）。3〜14行目の検査に使う（設計D §5.1）。

    符号や小数点の後に数字が1つも無いもの（"-" だけ、"abc" など）は読めないとする。
    """
    s = text.lstrip(" \t")
    if s == "":
        return True
    m = _VAL_RE.match(s)
    return bool(m) and any(c.isdigit() for c in m.group(1))


def _split_fields(line: str) -> list[str]:
    """1行を 6文字ずつ 16個に切る。行が短いときの足りない欄は空欄。"""
    return [line[i * FIELD_WIDTH:(i + 1) * FIELD_WIDTH] for i in range(FIELDS_PER_LINE)]


def _round_half_even_int(x: float) -> int:
    """旧アプリは HF を整数の配列に入れるので、Val() の実数は偶数丸めで整数になる。"""
    return int(round(x))


def parse_legacy_cal(data: bytes) -> LegacyCal:
    """旧校正ファイルのバイト列を読む。読めなければ LegacyCalError（設計D §5.1）。"""
    # 1行目の表示単位の欄に日本語が入りうるため CP932 で文字にする（固定幅は文字単位）
    text = data.decode("cp932", errors="replace")
    # 実物の行末は "." CR CR LF。\n で分け、行末の \r を全部取り除く
    lines = [ln.rstrip("\r") for ln in text.split("\n")]
    if len(lines) < REQUIRED_LINES:
        raise LegacyCalError("too_few_lines", line=len(lines) + 1, field=None)

    rows = [_split_fields(lines[i]) for i in range(REQUIRED_LINES)]

    hf: list[tuple[int, ...]] = []
    for band in range(HF_BANDS):
        line_no = HF_FIRST_LINE + band
        fields = rows[line_no - 1]
        values = []
        for idx in range(HF_POINTS):
            if not _starts_with_number(fields[idx]):
                raise LegacyCalError("not_a_number", line=line_no, field=idx, text=fields[idx])
            values.append(_round_half_even_int(vb_val(fields[idx])))
        hf.append(tuple(values))

    line1, line2 = rows[0], rows[1]
    return LegacyCal(
        adj30k=vb_val(line1[_LINE1_ADJ30K]),
        adj15k=vb_val(line1[_LINE1_ADJ15K]),
        common_bias=vb_val(line1[_LINE1_COMMON_BIAS]),
        enr_x10=vb_val(line2[_LINE2_ENR]),
        nf_amp_raw=vb_val(line2[_LINE2_NF_AMP]),
        hf=tuple(hf),
    )
