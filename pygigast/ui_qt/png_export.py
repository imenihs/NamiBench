"""画像出力（設計I）。グラフを画面の外に描き直して PNG にする（Qt の描画）。

画面を撮らない（I §3.1）。他のウィンドウもマウスのカーソルも写らず、大きさを画面と独立に決められる。
描くもの: グラフ（見えているトレース・マーカー・目盛・凡例）、グラフの下のマーカー表、下端の帯（I §3.2）。
PNG の tEXt に、帯と同じ内容の JSON を `pygigast` のキーで入れる（I §3.3）。
保存は上書きしない名前で（I §3.4・M-08）、クリップボードへもコピーできる。

配置・帯・マーカー表の規則は core/report_layout.py にあり、Web の canvas も同じものを使う（I §3.1・§3.5）。
ここは QPainter で描くことと、PNG の塊・保存・コピーだけ。data の形は report_layout の冒頭の説明のとおり。
"""
from __future__ import annotations

import json
import math
import struct
import zlib
from datetime import datetime
from pathlib import Path
from typing import Any, Optional, Union

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QMimeData, QPointF, QRectF, Qt
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QFontMetricsF,
    QGuiApplication,
    QImage,
    QPainter,
    QPainterPath,
    QPen,
    QPolygonF,
)

from .. import __version__ as APP_VERSION
from ..core import report_layout as rl
from ..core.step_sweep import LINE_ALPHA as _STEP_LINE_ALPHA

# 規則は report_layout にある。前からこの名前で使っている呼び出し元（画面・試験）のために同じ名前で見せる
META_KEY = rl.META_KEY
META_FORMAT = rl.META_FORMAT
DIVISIONS = rl.DIVISIONS
DARK_DEFAULT = rl.DARK_DEFAULT
LIGHT_DEFAULT = rl.LIGHT_DEFAULT
LAYOUT = rl.LAYOUT
JP_FONT_FALLBACKS = rl.JP_FONT_FALLBACKS
MARKER_COLUMNS = rl.MARKER_COLUMNS
MARKER_RIGHT = rl.MARKER_RIGHT
resolve_palette = rl.resolve_palette
format_measured_at = rl.format_measured_at
legend_text = rl.legend_text
marker_table_rows = rl.marker_table_rows
_freq_decimals = rl.freq_decimals


def band_rows(data: dict, note: str = "") -> list[tuple[str, list[str]]]:
    return rl.band_rows(data, note, APP_VERSION)


def png_metadata(data: dict, note: str = "") -> dict:
    return rl.png_metadata(data, note, APP_VERSION)


def _color(pal: dict[str, str], key_or_hex: Optional[str], fallback: str = "trace1") -> QColor:
    if key_or_hex and key_or_hex.startswith("#"):
        return QColor(key_or_hex)
    return QColor(pal.get(key_or_hex or fallback, pal[fallback]))


def _stroke_pen(color: QColor, stroke: dict) -> QPen:
    """report_layout.stroke の形（幅と切れ目は画素）からペンを作る。切れ目の端は切りっぱなし（Web の canvas と同じ）。"""
    pen = QPen(color, float(stroke["width"]))
    if stroke.get("dash"):
        w = max(float(stroke["width"]), 1e-6)
        pen.setDashPattern([float(v) / w for v in stroke["dash"]])  # Qt の切れ目は線の幅に対する倍率
        pen.setCapStyle(Qt.PenCapStyle.FlatCap)
    return pen


# ---------------------------------------------------------------- PNG の塊

def _chunk(kind: bytes, body: bytes) -> bytes:
    return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)


def iter_png_chunks(png: bytes):
    """(種類, 中身) を順に返す。署名と CRC を確かめる。"""
    if png[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("PNG ではない")
    pos = 8
    while pos < len(png):
        (n,) = struct.unpack(">I", png[pos:pos + 4])
        kind = png[pos + 4:pos + 8]
        body = png[pos + 8:pos + 8 + n]
        (crc,) = struct.unpack(">I", png[pos + 8 + n:pos + 12 + n])
        if zlib.crc32(kind + body) & 0xFFFFFFFF != crc:
            raise ValueError(f"CRC が合わない: {kind!r}")
        yield kind, body
        pos += 12 + n


def add_text_chunk(png: bytes, key: str, text: str) -> bytes:
    """IEND の前に tEXt を足す。tEXt は Latin-1 なので、text は ASCII にしてから渡す。"""
    body = key.encode("latin-1") + b"\x00" + text.encode("latin-1")
    iend = png.rfind(b"IEND") - 4
    if iend < 8:
        raise ValueError("IEND が無い")
    return png[:iend] + _chunk(b"tEXt", body) + png[iend:]


def read_png_metadata(png: bytes) -> Optional[dict]:
    """tEXt の `pygigast` を JSON として読む。無ければ None。"""
    for kind, body in iter_png_chunks(png):
        if kind == b"tEXt":
            key, _, value = body.partition(b"\x00")
            if key == META_KEY.encode():
                return json.loads(value.decode("latin-1"))
    return None


def _json_default(o: Any) -> Any:
    if hasattr(o, "tolist"):
        return o.tolist()
    if isinstance(o, datetime):
        return o.isoformat()
    return str(o)


# ---------------------------------------------------------------- 描画

class _Painter:
    """1枚の画像を描く。区画の高さは中身から決め、残りをグラフに使う（見切れを作らない）。"""

    def __init__(self, img: QImage, data: dict, pal: dict[str, str], note: str) -> None:
        self.img = img
        self.data = data
        self.pal = pal
        self.note = note
        self.w, self.h = img.width(), img.height()
        # 配置の数（文字の大きさ・行の高さ・余白・帯・表）は Web と同じ規則から（I §3.1）
        self.lay = rl.layout(data, (self.w, self.h), note, APP_VERSION)
        self.px = self.lay["font_px"]
        # 画面と同じ書体を先に。日本語の書体が無い環境でも豆腐にならないよう、候補を並べる
        self.font = QFont(QGuiApplication.font())
        self.font.setFamilies([QGuiApplication.font().family(), *JP_FONT_FALLBACKS])
        self.font.setPixelSize(round(self.px))
        self.small = QFont(self.font)
        self.small.setPixelSize(round(self.px * 0.9))
        self.fm = QFontMetricsF(self.font)
        self.fms = QFontMetricsF(self.small)
        self.lh = self.lay["line_px"]
        # 行の中の文字の位置（行の高さと書体の高さの差を上下に分ける）
        self.base = (self.lh - self.fm.height()) / 2 + self.fm.ascent()
        self.margin = self.lay["margin_px"]
        self.gap = self.lay["gap_px"]
        self.p = QPainter(img)
        self.p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        self.p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)

    def c(self, key: str) -> QColor:
        return QColor(self.pal[key])

    # ---- 区画の配分
    def run(self) -> None:
        inner_w = self.w - 2 * self.margin
        band = self._flow_band(inner_w)
        band_h = len(band) * self.lh + (2 * self.gap if band else 0)
        table, cols, rows_per_col, table_h = self._fit_table(inner_w)

        band_top = self.h - band_h
        self.p.fillRect(QRectF(0, band_top, self.w, band_h), self.c("panel_bg"))
        self.p.setPen(QPen(self.c("axis"), 1))
        self.p.drawLine(QPointF(0, band_top), QPointF(self.w, band_top))
        self._draw_band(band, band_top + self.gap)

        table_top = band_top - self.gap - table_h
        if table:
            self._draw_table(table, cols, rows_per_col, QRectF(self.margin, table_top, inner_w, table_h))
        graph_bottom = (table_top - self.gap) if table else (band_top - self.gap)
        self._draw_graph(QRectF(self.margin, self.margin, inner_w, graph_bottom - self.margin))
        self.p.end()

    # ---- 下端の帯
    def _flow_band(self, width: float) -> list[tuple[str, list[str]]]:
        """段ごとに、項目の切れ目で折り返した行の並び。1項目が幅を超えるときは文字で切って折り返す。"""
        lines: list[tuple[str, list[str]]] = []
        sep = self.fm.horizontalAdvance("あ") * LAYOUT["item_gap"]
        for color, items in self.lay["band"]:
            cur: list[str] = []
            cur_w = 0.0
            for it in items:
                for piece in self._split_to_width(it, width):
                    iw = self.fm.horizontalAdvance(piece)
                    if cur and cur_w + sep + iw > width:
                        lines.append((color, cur))
                        cur, cur_w = [], 0.0
                    cur_w = iw if not cur else cur_w + sep + iw
                    cur.append(piece)
            if cur:
                lines.append((color, cur))
        return lines

    def _split_to_width(self, text: str, width: float) -> list[str]:
        out, cur = [], ""
        for ch in text:
            if cur and self.fm.horizontalAdvance(cur + ch) > width:
                out.append(cur)
                cur = ""
            cur += ch
        return out + [cur] if cur else out

    def _draw_band(self, lines: list[tuple[str, list[str]]], top: float) -> None:
        self.p.setFont(self.font)
        sep = self.fm.horizontalAdvance("あ") * LAYOUT["item_gap"]
        y = top
        for color, items in lines:
            self.p.setPen(self.c(color))
            x = self.margin
            for it in items:
                self.p.drawText(QPointF(x, y + self.base), it)
                x += self.fm.horizontalAdvance(it) + sep
            y += self.lh

    # ---- マーカー表
    def _fit_table(self, width: float):
        t = self.lay["table"]
        if not t["rows"]:
            return [], 1, 0, 0.0
        return t["rows"], t["cols"], t["rows_per_col"], (t["rows_per_col"] + 1) * self.lh

    def _draw_table(self, rows: list[list[str]], cols: int, rows_per_col: int, rect: QRectF) -> None:
        self.p.setFont(self.font)
        pad = self.fm.horizontalAdvance("あ")
        col_gap = pad * 2
        block_w = (rect.width() - col_gap * (cols - 1)) / cols
        # 列の幅は中身の最大幅（全ブロックで共通にして縦に揃える）
        widths = [max(self.fm.horizontalAdvance(r[i]) for r in rows + [list(MARKER_COLUMNS)]) for i in range(len(MARKER_COLUMNS))]
        need = sum(widths) + pad * (len(widths) - 1)
        scale_font = need > block_w
        if scale_font:
            # 幅が足りないときは表の文字だけ縮める（切らない）
            f = QFont(self.font)
            f.setPixelSize(max(8, int(self.font.pixelSize() * block_w / need)))
            self.p.setFont(f)
            fm = QFontMetricsF(f)
            pad = fm.horizontalAdvance("あ")
            widths = [max(fm.horizontalAdvance(r[i]) for r in rows + [list(MARKER_COLUMNS)]) for i in range(len(MARKER_COLUMNS))]
        else:
            fm = self.fm
        for b in range(cols):
            chunk = rows[b * rows_per_col:(b + 1) * rows_per_col]
            if not chunk:
                continue
            x0 = rect.left() + b * (block_w + col_gap)
            self._draw_table_block([list(MARKER_COLUMNS)] + chunk, widths, pad, x0, rect.top(), fm)

    def _draw_table_block(self, rows, widths, pad, x0, y0, fm: QFontMetricsF) -> None:
        y = y0
        for ri, row in enumerate(rows):
            self.p.setPen(self.c("text_dim" if ri == 0 else "text"))
            x = x0
            for i, cell in enumerate(row):
                tw = fm.horizontalAdvance(cell)
                tx = x + widths[i] - tw if MARKER_RIGHT[i] else x
                if i == len(row) - 1 and cell and ri > 0:
                    self.p.setPen(self.c("warn"))
                self.p.drawText(QPointF(tx, y + fm.ascent()), cell)
                x += widths[i] + pad
            if ri == 0:
                self.p.setPen(QPen(self.c("grid"), 1))
                self.p.drawLine(QPointF(x0, y + self.lh - 1), QPointF(x - pad, y + self.lh - 1))
            y += self.lh

    # ---- グラフ
    def _draw_graph(self, rect: QRectF) -> None:
        c = self.data.get("conditions") or {}
        unit = self.lay["unit"]
        ref = float(c.get("ref_level", 0.0))
        div = float(c.get("db_per_div", 10.0))
        bottom = ref - DIVISIONS * div
        zero_span = c.get("step_hz") == 0
        if zero_span:
            # ゼロスパン: 横軸は点の番号（画面と同じ: 設計N）
            start, stop = 0.0, float(max(1, int(c.get("points") or 2) - 1))
        else:
            start = float(c.get("start_hz", 0.0))
            stop = float(c.get("stop_hz", start + 1.0))
        if stop <= start:
            stop = start + 1.0

        # 枠と軸の文字の置き場所・横軸の数字の間引きは Web と同じ決まり（report_layout.graph_axes）
        ax = self.lay["axes"]
        plot = QRectF(ax["plot_left"], ax["plot_top"], ax["plot_right"] - ax["plot_left"], 0.0)
        plot.setBottom(rect.bottom() - ax["bottom_room"])
        if plot.height() < ax["min_plot_h"]:
            plot.setBottom(plot.top() + ax["min_plot_h"])

        def fx(f: float) -> float:
            return plot.left() + (f - start) / (stop - start) * plot.width()

        def fy(v: float) -> float:
            return plot.top() + (ref - v) / (ref - bottom) * plot.height()

        self.p.fillRect(plot, self.c("graph_bg"))
        # 目盛線（縦も横も 10 区画。目盛の数字はこの線の上: AXES）
        self.p.setPen(QPen(self.c("grid"), 1))
        for i in range(DIVISIONS + 1):
            y = plot.top() + plot.height() * i / DIVISIONS
            self.p.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))
            x = plot.left() + plot.width() * i / DIVISIONS
            self.p.drawLine(QPointF(x, plot.top()), QPointF(x, plot.bottom()))
        # 0dB の線（比のトレースのとき）
        if unit == "dB" and bottom <= 0.0 <= ref:
            self.p.setPen(QPen(self.c("zero_db"), 1))
            self.p.drawLine(QPointF(plot.left(), fy(0.0)), QPointF(plot.right(), fy(0.0)))
        self.p.setPen(QPen(self.c("axis"), 1))
        self.p.drawRect(plot)

        # 縦軸の数字（絶対値: M-06）。右端をそろえ、目盛線の高さの真ん中に置く
        self.p.setFont(self.font)
        self.p.setPen(self.c("text"))
        mid = (self.fm.ascent() - self.fm.descent()) / 2
        for i, s in enumerate(self.lay["y_labels"]):
            y = plot.top() + plot.height() * i / DIVISIONS
            self.p.drawText(QPointF(ax["y_label_right"] - self.fm.horizontalAdvance(s), y + mid), s)
        # 単位は縦軸の数字の上（凡例と同じ段の左端）
        self.p.setPen(self.c("text_dim"))
        self.p.drawText(QPointF(ax["unit_left"], rect.top() + self.base), unit)

        self._draw_x_axis(plot)
        self._draw_legend(QRectF(ax["legend_left"], rect.top(), plot.right() - ax["legend_left"], self.lh))

        # トレースと印は枠の中だけに描く（横軸は表示の Start–Stop で切る）。重ねる順は画面と同じ（参照トレースが下、
        # 太い線が上、同じ段は番号順: 設計F §9）
        self.p.save()
        self.p.setClipRect(plot)
        shown = [tr for tr in self.data.get("traces") or [] if tr.get("visible", True)]
        for tr in sorted(shown, key=lambda t: rl.draw_order_key(t.get("line"), t.get("number"))):
            self._draw_trace(tr, fx, fy, start, stop, index=zero_span)
        self.p.restore()
        self._draw_markers(plot, fx, fy, start, stop, ref, bottom)

    def _draw_x_axis(self, plot: QRectF) -> None:
        """横軸の数字（10 区画の線の上を x_every ごと）と、その下の見出し（単位）。置き場所は report_layout.graph_axes。"""
        ax = self.lay["axes"]
        labels = self.lay["x_labels"]
        step_px = plot.width() / DIVISIONS
        self.p.setFont(self.font)
        self.p.setPen(self.c("text"))
        y = plot.bottom() + ax["x_label_top"] + self.base
        for i in range(0, DIVISIONS + 1, ax["x_every"]):
            s = labels[i]
            tw = self.fm.horizontalAdvance(s)
            # 両端の数字は画像の外に出さない
            x = min(max(plot.left() + step_px * i - tw / 2, 0.0), ax["x_label_max_right"] - tw)
            self.p.drawText(QPointF(x, y), s)
        title = self.lay["x_title"]
        tw = self.fm.horizontalAdvance(title)
        self.p.setPen(self.c("text_dim"))
        self.p.drawText(QPointF(plot.center().x() - tw / 2, plot.bottom() + ax["x_title_top"] + self.base), title)

    def _draw_legend(self, rect: QRectF) -> None:
        self.p.setFont(self.font)
        x = rect.left()
        sw = self.fm.horizontalAdvance("あ") * self.lay["legend_sample"]
        for item in self.lay["legend"]:
            s = item["text"]
            col = _color(self.pal, item["color"])
            yc = rect.top() + self.lh / 2
            if x + sw + self.fm.horizontalAdvance(s) > rect.right() + 1:
                break  # 凡例は1段。あふれる分は描かない（トレースは最大4本なので実際には起きない想定）
            # 線の見本はそのトレースの線（設計F §9。太さと切れ目は report_layout が決める: Web と同じ）
            self.p.setPen(_stroke_pen(col, item["stroke"]))
            self.p.drawLine(QPointF(x, yc), QPointF(x + sw * 0.8, yc))
            self.p.setPen(self.c("text"))
            self.p.drawText(QPointF(x + sw, rect.top() + self.base), s)
            x += sw + self.fm.horizontalAdvance(s) + self.fm.horizontalAdvance("あ") * 2

    def _draw_trace(self, tr: dict, fx, fy, start: float, stop: float, index: bool = False) -> None:
        """index は横軸が点の番号（ゼロスパン）のとき。"""
        vals = tr.get("values_db")
        if vals is None:
            return
        vals = [float(v) for v in (vals.tolist() if hasattr(vals, "tolist") else vals)]
        n = int(tr.get("points") or len(vals))
        n = min(n, len(vals))
        if index:
            f0, st = 0.0, 1.0
        else:
            f0 = float(tr.get("start_hz", start))
            st = float(tr.get("step_hz", (stop - start) / max(1, n - 1)))
        flags = tr.get("flags")
        flags = list(flags.tolist() if hasattr(flags, "tolist") else flags) if flags is not None else None
        col = _color(self.pal, tr.get("color") or f"trace{tr.get('number', 1)}")
        if tr.get("steps"):
            # 段ごとの線（ゼロスパンでは x0・dx が点の番号で、横軸と同じ）
            self._draw_step_lines(tr["steps"], col, fx, fy)
        # そのトレースの線（設計F §9）。基準の幅は Web の PNG と同じ（report_layout.png_line_base）
        line = rl.stroke(tr.get("line"), self.lay["line_base_px"])
        # 範囲の1点外側までは描いて、枠の端まで線をつなぐ
        lo = max(0, int(math.floor((start - f0) / st)) - 1) if st > 0 else 0
        hi = min(n, int(math.ceil((stop - f0) / st)) + 2) if st > 0 else n
        segs: list[tuple[bool, QPainterPath]] = []
        cur: Optional[QPainterPath] = None
        cur_flag = None
        prev: Optional[QPointF] = None
        for i in range(lo, hi):
            v = vals[i]
            if math.isnan(v):
                cur, prev = None, None
                continue
            pt = QPointF(fx(f0 + i * st), fy(v))
            fl = bool(flags[i]) if flags is not None and i < len(flags) else False
            if cur is None or fl != cur_flag:
                cur = QPainterPath(prev if prev is not None else pt)
                cur_flag = fl
                segs.append((fl, cur))
            cur.lineTo(pt)
            prev = pt
        for fl, path in segs:
            self.p.setPen(_stroke_pen(self.c("text_dim") if fl else col, line))
            self.p.setBrush(Qt.BrushStyle.NoBrush)
            self.p.drawPath(path)

    def _draw_step_lines(self, steps: dict, col: QColor, fx, fy) -> None:
        """ステップ掃引を全部重ねた段ごとの線（設計L §1.3）: トレースの色の不透明度 約 35% の細い線（画面と同じ）。"""
        c = QColor(col)
        c.setAlphaF(_STEP_LINE_ALPHA)
        pen = QPen(c, max(1.0, self.lay["line_base_px"] * 0.5))
        self.p.setPen(pen)
        self.p.setBrush(Qt.BrushStyle.NoBrush)
        x0, dx = float(steps["x0"]), float(steps["dx"])
        for row in steps["values"]:
            paths: list[QPainterPath] = []
            path: Optional[QPainterPath] = None
            for i, v in enumerate(row):
                v = float(v)
                if math.isnan(v):
                    path = None  # 値の無い点で線を切る
                    continue
                pt = QPointF(fx(x0 + i * dx), fy(v))
                if path is None:
                    path = QPainterPath(pt)
                    paths.append(path)
                else:
                    path.lineTo(pt)
            for p in paths:
                self.p.drawPath(p)

    def _draw_markers(self, plot: QRectF, fx, fy, start, stop, top, bottom) -> None:
        self.p.setFont(self.small)
        mcol = self.c("marker")
        size = max(8.0, self.px * 0.8)
        # マーカーの機能の線（G §3.6。画面と同じ薄い点線。マーカーの印より先に描いて下に置く）
        line_col = QColor(mcol)
        line_col.setAlpha(89)
        self.p.setPen(QPen(line_col, 1, Qt.PenStyle.DotLine))
        for m in self.data.get("markers") or []:
            ln = m.get("line") if m.get("kind") == "function" else None
            if not ln or ln.get("level") is None:
                continue
            lv = ln["level"]
            if bottom <= lv <= top:
                y = fy(lv)
                self.p.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))
            for f in ln.get("freqs_hz") or []:
                if start <= f <= stop:
                    x = fx(f)
                    self.p.drawLine(QPointF(x, plot.top()), QPointF(x, plot.bottom()))
        for m in self.data.get("markers") or []:
            kind = m.get("kind", "marker")
            f, v, name = m.get("freq_hz"), m.get("value"), str(m.get("name", ""))
            if kind == "freq_cursor":
                if f is None or not (start <= f <= stop):
                    continue
                x = fx(f)
                self.p.setPen(QPen(mcol, 1, Qt.PenStyle.DashLine))
                self.p.drawLine(QPointF(x, plot.top()), QPointF(x, plot.bottom()))
                self._label(name.strip(), QPointF(x + 3, plot.top() + self.fms.ascent() + 2), plot)
            elif kind == "level_cursor":
                if v is None or not (bottom <= v <= top):
                    continue
                y = fy(v)
                self.p.setPen(QPen(mcol, 1, Qt.PenStyle.DashLine))
                self.p.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))
                tw = self.fms.horizontalAdvance(name.strip())
                self._label(name.strip(), QPointF(plot.right() - tw - 3, y - 3), plot)
            else:
                if f is None or v is None or not (start <= f <= stop):
                    continue
                x = fx(f)
                y = min(max(fy(v), plot.top()), plot.bottom())
                tri = QPolygonF([QPointF(x, y - 1), QPointF(x - size / 2, y - 1 - size), QPointF(x + size / 2, y - 1 - size)])
                self.p.setPen(QPen(self.c("graph_bg"), 1))
                self.p.setBrush(QBrush(mcol))
                self.p.drawPolygon(tri)
                self.p.setBrush(Qt.BrushStyle.NoBrush)
                tw = self.fms.horizontalAdvance(name)
                ly = y - 1 - size - 3
                if ly - self.fms.ascent() < plot.top():
                    ly = y + self.fms.ascent() + 3  # 上に余地が無ければ点の下へ
                self._label(name, QPointF(x - tw / 2, ly), plot)

    def _label(self, text: str, at: QPointF, plot: QRectF) -> None:
        """グラフの上の文字。背景を敷いてトレースと重なっても読めるようにし、枠からはみ出させない。"""
        tw = self.fms.horizontalAdvance(text)
        x = min(max(at.x(), plot.left() + 2), plot.right() - tw - 2)
        box = QRectF(x - 2, at.y() - self.fms.ascent() - 1, tw + 4, self.fms.height() + 2)
        bg = self.c("graph_bg")
        bg.setAlpha(210)
        self.p.fillRect(box, bg)
        self.p.setPen(self.c("marker"))
        self.p.drawText(QPointF(x, at.y()), text)


def _ensure_app() -> None:
    # 文字を描くには QGuiApplication が要る。アプリの中から呼ぶときは既にある
    if QGuiApplication.instance() is None:
        _ensure_app.app = QGuiApplication([])  # type: ignore[attr-defined]


def render_png(data: dict, size: tuple[int, int] = (1600, 1000),
               palette: Union[str, dict, None] = "light", note: str = "") -> bytes:
    """data（冒頭の形）から PNG のバイト列を作る。画面は撮らない（I §3.1）。

    palette: "light"／"dark"／色の組（ユーザー指定 = 画面の配色）。画面から作るときの選び方と既定（ユーザー指定）は
    report_layout.png_palette（I §3.6）。ここの既定 "light" は画面を持たない呼び出し（試験の見本）のためのもの。
    note: 利用者のメモ（1行）。空なら帯に出さない（U-15）。
    """
    _ensure_app()
    w, h = int(size[0]), int(size[1])
    if w < 320 or h < 240:
        raise ValueError(f"画像が小さすぎる: {w}x{h}")
    pal = resolve_palette(palette)
    img = QImage(w, h, QImage.Format.Format_RGB32)
    img.fill(QColor(pal["graph_bg"]))
    _Painter(img, data, pal, note).run()

    buf = QByteArray()
    dev = QBuffer(buf)
    dev.open(QIODevice.OpenModeFlag.WriteOnly)
    if not img.save(dev, "PNG"):
        raise RuntimeError("PNG に書けなかった")
    dev.close()
    meta = json.dumps(png_metadata(data, note), ensure_ascii=True, default=_json_default)
    return add_text_chunk(bytes(buf.data()), META_KEY, meta)


# ---------------------------------------------------------------- 保存とコピー

def default_png_name(when: Optional[datetime] = None) -> str:
    """`gigast_YYYYMMDD_HHMMSS.png`（I §3.4）。"""
    return (when or datetime.now()).strftime("gigast_%Y%m%d_%H%M%S.png")


def save_png(path: Union[str, Path], png_bytes: bytes, when: Optional[datetime] = None) -> Path:
    """PNG を保存し、実際に書いたパスを返す。上書きしない（I §3.4・M-08）。

    path がフォルダなら `gigast_YYYYMMDD_HHMMSS.png`（when、省略時は今）をその中に作る。
    同じ名前があれば `_1`、`_2` … を付ける。作るのは排他で行い、同時に保存しても上書きしない。
    """
    p = Path(path)
    if p.is_dir():
        p = p / default_png_name(when)
    p.parent.mkdir(parents=True, exist_ok=True)
    stem, suffix = p.stem, p.suffix or ".png"
    i = 0
    while True:
        cand = p.with_name(f"{stem}{'' if i == 0 else f'_{i}'}{suffix}")
        try:
            with open(cand, "xb") as f:
                f.write(png_bytes)
            return cand
        except FileExistsError:
            i += 1


def copy_png_to_clipboard(png_bytes: bytes) -> None:
    """クリップボードへ画像として置く。貼り先が PNG を受け取れるなら tEXt 付きの PNG のまま渡る。"""
    _ensure_app()
    img = QImage.fromData(png_bytes, "PNG")
    if img.isNull():
        raise ValueError("PNG を読めない")
    mime = QMimeData()
    mime.setImageData(img)
    mime.setData("image/png", QByteArray(png_bytes))
    QGuiApplication.clipboard().setMimeData(mime)
