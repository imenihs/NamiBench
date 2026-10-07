"""グラフの上に描くもの（設計F §5・E §6.4・E §6.6・G §3.5）。

- TraceLayer: 測定中のトレース 1〜4（J §4.4 の色・選んだ線と重なりの順: F §9。ステップ掃引を全部重ねた結果は段ごとの
  線をトレースの色の細く薄い線で下に重ねる: 設計L §1.3）と参照トレース（ファイル。点線で一番下）、
  凡例（動作・平均の種類・回数。線の見本はそのトレースの線）、
  値なしの点は線を切る、前置き不足・校正の範囲外の点は控えめな色、比（dB）のときの 0dB の線（E §6.6）、
  基準の範囲外の区間の帯（E §6.4）
- MarkerLayer: マーカーの印と名前、オフセットのカーソル（縦線・横線）、XY カーソル（掴んで動かせる）

ここは描くだけで、コアへは何も送らない（XY カーソルを動かし終えたことだけを呼び出し元へ返す）。
"""
from __future__ import annotations

from typing import Callable, Optional

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor

from . import texts
from ..core import report_layout as rl
from ..core.palette import FILE_TRACE_ORDER
from ..core.step_sweep import LINE_ALPHA
from ..core.report_layout import legend_text
from .screen_model import file_trace_shown_at_open, nearest_point, trace_x
from .theme import trace_color

LIVE_TRACES = (1, 2, 3, 4)
# マーカーの機能の線の濃さ（G §3.6: マーカーの色の不透明度 約 35%。Web の graph.js と同じ）
FUNCTION_LINE_ALPHA = 89
# ステップ掃引を全部重ねるときの段ごとの線（設計L §1.3）: トレースの色の不透明度 約 35% の細い線（Web の graph.js・PNG と同じ）
STEP_LINE_ALPHA = int(round(LINE_ALPHA * 255))
# カーソルの名前の位置（グラフの高さ・幅に対する割合。縦線は下から、横線は左から）。オフセットのカーソルの縦線は、
# 近い線の名前が重ならないよう1本ごとに高さをずらす。名前の背景の濃さ。PNG（png_export）と Web の graph.js も同じ値
CURSOR_LABEL_V = 0.92
CURSOR_LABEL_STAGGER = 0.06
CURSOR_LABEL_H = 0.08
XY_LABEL_V = 0.95
XY_LABEL_H = 0.05
CURSOR_LABEL_FILL = 180
XY_LABEL_FILL = 200
XY_IDS = ("X1", "X2", "Y1", "Y2")
# 基準の範囲外の帯（設計E §6.4）の塗りと縁の濃さ（0〜255。注意の色）。PNG（png_export）も同じ値で描く
BAND_FILL_ALPHA = 35
BAND_EDGE_ALPHA = 90


def step_lines(steps: Optional[dict]) -> tuple[np.ndarray, np.ndarray]:
    """段ごとの線（イベント trace の steps: {x0, dx, values[段, 点]}）を、値なしで区切った1本の (x, y) にする。"""
    if not steps:
        return np.array([]), np.array([])
    rows = np.asarray(steps["values"], dtype=np.float64)
    if rows.ndim != 2 or rows.size == 0:
        return np.array([]), np.array([])
    n = rows.shape[1]
    x = float(steps["x0"]) + float(steps["dx"]) * np.arange(n, dtype=np.float64)
    xs = np.concatenate([np.append(x, np.nan) for _ in range(rows.shape[0])])
    ys = np.concatenate([np.append(r, np.nan) for r in rows])
    return xs, ys


def x_of(payload: dict) -> np.ndarray:
    """描くための横軸（float）。ゼロスパンは点の番号。読み値と同じ関数（screen_model.trace_x）。"""
    return trace_x(payload)


# 控えめな色で描く点の印（点ごとの印の名前）。NF の HOT と COLD の差が小さい点（設計M §1）も
DIM_POINT_FLAGS = ("transient", "nf_small_difference")


def marked_points(payload: dict, n: int) -> np.ndarray:
    """控えめな色で描く点: 前置き不足（設計C §5.6）と校正の範囲外（設計D §5.3）と、NF の HOT と COLD の差が
    0.5dB 未満の点（設計M §1: 値は出して印を付ける）。"""
    fl = payload.get("flags") or {}
    mark = np.zeros(n, dtype=bool)
    head = int(fl.get("transient_head_points") or 0)
    mark[: min(n, head)] = True
    oc = fl.get("out_of_cal_range")
    if oc is not None:
        oc = np.asarray(oc, dtype=bool)
        if oc.shape == (n,):
            mark |= oc
    pts = fl.get("points") or {}
    for name in DIM_POINT_FLAGS:
        a = pts.get(name)
        if a is not None and np.asarray(a).shape == (n,):
            mark |= np.asarray(a, dtype=bool)
    return mark


def _alpha(color: str, a: int) -> QColor:
    c = QColor(color)
    c.setAlpha(a)
    return c


# 画面の線の基準の幅（画素。設計F §9 の実線 = 今までの線）
SCREEN_LINE_BASE = 1.0


def line_pen(color, code: Optional[str]):
    """線の名前（設計F §9・参照トレースは reference）から画面のペン。幅と切れ目は report_layout.stroke（Web と同じ）。

    1 画素より細い線（細線）は、1 画素の線を幅の割合だけ薄く描く。ぼかして細く描くのと同じ見え方（Web の canvas・PNG は
    ぼかして描く）で、点の多いトレースでも実線に近い速さで描ける（20 万点で ぼかす 約 2 倍・薄く 約 1.3 倍: 2026-10-03 測定）。
    """
    s = rl.stroke(code, SCREEN_LINE_BASE)
    c = QColor(color)
    width = s["width"]
    if width < 1.0:
        c.setAlphaF(c.alphaF() * width)
        width = 1.0
    pen = pg.mkPen(c, width=width)
    if s["dash"]:
        pen.setDashPattern([v / width for v in s["dash"]])  # Qt の切れ目は線の幅に対する倍率
        pen.setCapStyle(Qt.PenCapStyle.FlatCap)  # 端は切りっぱなし（Web の canvas と同じ）
    return pen


def line_z(code: Optional[str], number: int) -> float:
    """測っているトレースの重なりの高さ（太い線が上・同じ段は番号の大きいほうが上: 設計F §9）。参照トレースは 5。"""
    return 10 + 5 * rl.line_style(code)["z"] + number


class TraceLayer:
    def __init__(self, plot: pg.PlotWidget, pal: dict[str, str]) -> None:
        self.plot = plot
        self.pal = pal
        self.pi = plot.getPlotItem()
        self.live: dict[int, dict] = {}
        self.files: dict[str, dict] = {}
        self.file_meta: dict[str, dict] = {}
        self.fill = False
        self._fill_level = -100.0
        self.unit = "dBm"
        for n in LIVE_TRACES:
            curve = self.pi.plot(connect="finite")
            # 印の付いた点（前置き不足・校正の範囲外）は同じ線の上に控えめな色で重ねる
            mark = self.pi.plot(connect="finite")
            # ステップ掃引を全部重ねたときの段ごとの線（設計L §1.3）。トレースの線の下に細く薄く重ねる
            steps = self.pi.plot(connect="finite")
            self.live[n] = {"curve": curve, "mark": mark, "steps": steps, "payload": None, "visible": n == 1,
                            "has_values": False, "line": rl.TRACE_LINE_DEFAULT}
            self._style_live(n)
        self.zero_line = pg.InfiniteLine(pos=0.0, angle=0, movable=False, pen=pg.mkPen(pal["zero_db"], width=1.5))
        self.zero_line.setZValue(-5)
        self.pi.addItem(self.zero_line, ignoreBounds=True)
        self.zero_line.hide()
        self._bands: list[pg.LinearRegionItem] = []
        self._band_ranges: list = []
        self.legend = pg.LegendItem(offset=(-8, 6), brush=pg.mkBrush(_alpha(pal["graph_bg"], 200)),
                                    pen=pg.mkPen(pal["axis"]), labelTextColor=pal["text"], labelTextSize="9pt")
        self.legend.setParentItem(self.pi.getViewBox())
        self._legend_key: tuple = ()

    # ---------------------------------------------------------------- 測定中のトレース

    def update_live(self, t: dict) -> None:
        n = int(t["trace"])
        e = self.live.get(n)
        if e is None:
            return
        e["payload"] = t
        e["has_values"] = True
        if t.get("visible") is not None:
            e["visible"] = bool(t["visible"])
        self._set_line(n, t.get("line"))
        self._draw_live(n)
        self._update_legend()

    # ---------------------------------------------------------------- 線（設計F §9）

    def line_of(self, n: int) -> str:
        """トレース n をいま描いている線の名前。"""
        return self.live[n]["line"]

    def _set_line(self, n: int, code: Optional[str]) -> None:
        if code and code in rl.TRACE_LINE_STYLES and code != self.live[n]["line"]:
            self.live[n]["line"] = code
            self._style_live(n)

    def _style_live(self, n: int) -> None:
        """トレース n の線を描き方（色・幅・切れ目）と重なりの高さに合わせる。印の点も同じ幅・切れ目で重ねる。"""
        e = self.live[n]
        code = e["line"]
        # 凡例の見本は曲線のペンで描く（同じ線になる）
        for item, color in ((e["curve"], trace_color(n, self.pal)), (e["mark"], self.pal["text_dim"])):
            item.setPen(line_pen(color, code))
        z = line_z(code, n)
        e["curve"].setZValue(z)
        e["mark"].setZValue(z + 0.5)
        e["steps"].setPen(pg.mkPen(_alpha(trace_color(n, self.pal), STEP_LINE_ALPHA), width=1))
        e["steps"].setZValue(z - 0.5)

    def live_unit_ok(self, n: int) -> bool:
        """1つの目盛りに違う単位を混ぜない（E §6.4・設計R §3）。縦軸と単位の違う測定中のトレースは描かない。"""
        t = self.live[n]["payload"]
        return t is None or t.get("unit", self.unit) == self.unit

    def _draw_live(self, n: int) -> None:
        e = self.live[n]
        t = e["payload"]
        if t is None or not e["visible"] or not e["has_values"] or not self.live_unit_ok(n):
            e["curve"].setData([], [])
            e["mark"].setData([], [])
            e["steps"].setData([], [])
            return
        sx, sy = step_lines(t.get("steps"))
        e["steps"].setData(sx, sy, connect="finite")
        x = x_of(t)
        y = np.asarray(t["values"], dtype=np.float64)
        m = marked_points(t, y.size)
        if m.any():
            # 印の区間は隣の点まで控えめな色でつなぐ（線の切れ目を作らない）
            edge = m | np.concatenate([m[1:], [False]]) | np.concatenate([[False], m[:-1]])
            ym = np.where(edge, y, np.nan)
            e["mark"].setData(x, ym, connect="finite")
        else:
            e["mark"].setData([], [])
        e["curve"].setData(x, y, connect="finite")
        self._apply_fill(e["curve"], n)

    def apply_trace_states(self, states: list[dict]) -> list[int]:
        """状態の traces から、表示するか・値があるかを合わせる。値を持つのに描けていない番号を返す。"""
        missing = []
        for s in states or []:
            n = int(s["number"])
            e = self.live.get(n)
            if e is None:
                continue
            changed = (e["visible"], e["has_values"]) != (bool(s["visible"]), bool(s["has_values"]))
            e["visible"] = bool(s["visible"])
            e["has_values"] = bool(s["has_values"])
            self._set_line(n, s.get("line"))
            if e["payload"] is not None:
                # 凡例の回数・動作は状態の方が新しいことがある（設定を変えた直後）
                e["payload"] = {**e["payload"], "action": s["action"], "count": s["current_count"],
                                "count_target": s["count_target"], "average_type": s["average_type_effective"],
                                "axis_differs": s["axis_differs"], "line": e["line"],
                                "step_sweep": s.get("step_sweep")}
            if changed:
                self._draw_live(n)
            if e["visible"] and e["has_values"] and e["payload"] is None:
                missing.append(n)
        self._update_legend()
        return missing

    def live_payload(self, n: int) -> Optional[dict]:
        """描いている測定中のトレースの中身（表示していない・縦軸と単位が違うものは None）。"""
        e = self.live.get(n)
        if e is None or not e["visible"] or not e["has_values"] or not self.live_unit_ok(n):
            return None
        return e["payload"]

    def any_live(self) -> Optional[dict]:
        for n in LIVE_TRACES:
            p = self.live_payload(n)
            if p is not None:
                return p
        return None

    # ---------------------------------------------------------------- 参照トレース（ファイル: F §5.4）

    def update_file(self, t: dict) -> None:
        tid = str(t["trace"])
        e = self.files.get(tid)
        if e is None:
            idx = len(self.files)
            # 色は名前（trace1〜4）も持つ。PNG は名前で渡し、選んだ配色で描く（ライト・ダークでも同じ名前の色: I §3.6）
            key = FILE_TRACE_ORDER[idx % len(FILE_TRACE_ORDER)]
            col = self.pal[key]
            # 参照トレースの線は選べない（設計F §9: 点線のまま）。測っているトレースより下（5）
            curve = self.pi.plot(pen=line_pen(col, rl.REFERENCE_LINE), connect="finite")
            curve.setZValue(5)
            e = {"curve": curve, "payload": None, "color": col, "color_key": key, "shown": True, "init": False}
            self.files[tid] = e
        e["payload"] = t
        self._init_shown(tid)
        self._draw_file(tid)
        self._update_legend()

    def _init_shown(self, tid: str) -> None:
        """重ねた直後は、保存したときに表示していたトレースだけを出す（隠していたものは一覧で選べる）。"""
        e = self.files.get(tid)
        meta = self.file_meta.get(tid)
        if e is None or e["init"] or meta is None:
            return
        tr = next((t for t in meta.get("traces") or [] if t["id"] == tid), {})
        e["shown"] = file_trace_shown_at_open(tr)  # Web と同じ決まり（screen_model）
        e["init"] = True

    def file_unit_ok(self, tid: str) -> bool:
        """1つの目盛りに比（dB）と電力（dBm）を混ぜない（E §6.4）。単位が違う参照トレースは重ねない。"""
        e = self.files.get(tid)
        return e is not None and e["payload"] is not None and e["payload"].get("unit", self.unit) == self.unit

    def file_drawn(self, tid: str) -> bool:
        e = self.files.get(tid)
        return e is not None and e["shown"] and self.file_unit_ok(tid)

    def set_unit(self, unit: str) -> None:
        if unit == self.unit:
            return
        self.unit = unit
        for tid in self.files:
            self._draw_file(tid)
        for n in LIVE_TRACES:
            self._draw_live(n)  # 縦軸と単位の違う測定中のトレースは描かない（設計R §3）
        self._update_legend()

    def _draw_file(self, tid: str) -> None:
        e = self.files[tid]
        t = e["payload"]
        if t is None or not self.file_drawn(tid):
            e["curve"].setData([], [])
            return
        e["curve"].setData(x_of(t), np.asarray(t["values"], dtype=np.float64), connect="finite")

    def set_file_shown(self, tid: str, shown: bool) -> None:
        e = self.files.get(tid)
        if e is not None:
            e["shown"] = shown
            self._draw_file(tid)
            self._update_legend()

    def apply_files(self, files: list[dict]) -> list[str]:
        """状態の files に無くなった参照トレースを外す。持っていない参照トレースの id を返す。"""
        present = {}
        for f in files or []:
            for tr in f.get("traces") or []:
                present[tr["id"]] = f
        for tid in list(self.files):
            if tid not in present:
                self.pi.removeItem(self.files.pop(tid)["curve"])
        self.file_meta = present
        for tid in self.files:
            if not self.files[tid]["init"]:
                self._init_shown(tid)
                self._draw_file(tid)
        self._update_legend()
        return [tid for tid in present if tid not in self.files]

    def file_label(self, tid: str) -> str:
        # Web の凡例（状態の ui.file_legend）と同じ関数
        return texts.file_trace_label(self.file_meta.get(tid) or {}, tid)

    # ---------------------------------------------------------------- 凡例・0dB・帯・塗りつぶし

    def legend_entries(self) -> list[tuple[object, str, dict]]:
        out = []
        for n in LIVE_TRACES:
            p = self.live_payload(n)
            if p is not None:
                out.append((self.live[n]["curve"], legend_text(texts.legend_parts(p)), p))
        for tid, e in self.files.items():
            if self.file_drawn(tid):
                out.append((e["curve"], self.file_label(tid), e["payload"]))
        return out

    def _update_legend(self) -> None:
        entries = self.legend_entries()
        # 線を変えたら見本を描き直す（見本は線のペンで描く: 設計F §9）
        key = tuple((id(c), s, p.get("line")) for c, s, p in entries)
        if key == self._legend_key:
            return
        self._legend_key = key
        self.legend.clear()
        for c, s, _ in entries:
            self.legend.addItem(c, s)
        self.legend.setVisible(bool(entries))

    def set_zero_line(self, shown: bool) -> None:
        self.zero_line.setVisible(shown)

    def restyle(self) -> None:
        """配色を変えたとき（設計J §4.4: 変えた結果はその場で画面に出る）。self.pal は画面と同じ辞書を見ている。"""
        for n in LIVE_TRACES:
            self._style_live(n)
            self._apply_fill(self.live[n]["curve"], n)
        for i, e in enumerate(self.files.values()):
            e["color_key"] = FILE_TRACE_ORDER[i % len(FILE_TRACE_ORDER)]
            e["color"] = self.pal[e["color_key"]]
            e["curve"].setPen(line_pen(e["color"], rl.REFERENCE_LINE))
        self.zero_line.setPen(pg.mkPen(self.pal["zero_db"], width=1.5))
        self.legend.setBrush(pg.mkBrush(_alpha(self.pal["graph_bg"], 200)))
        self.legend.setPen(pg.mkPen(self.pal["axis"]))
        self.legend.setLabelTextColor(self.pal["text"])
        self._legend_key = ()
        self._update_legend()
        self.set_bands(self._band_ranges)

    def set_bands(self, ranges: list) -> None:
        """基準の範囲外の区間（E §6.4: 値なしの区間を色の帯で示す）。"""
        for b in self._bands:
            self.pi.removeItem(b)
        self._bands = []
        self._band_ranges = list(ranges or [])
        for lo, hi in ranges or []:
            b = pg.LinearRegionItem(values=(lo, hi), movable=False,
                                    brush=pg.mkBrush(_alpha(self.pal["warn"], BAND_FILL_ALPHA)),
                                    pen=pg.mkPen(_alpha(self.pal["warn"], BAND_EDGE_ALPHA)))
            b.setZValue(-8)
            self.pi.addItem(b, ignoreBounds=True)
            self._bands.append(b)

    def set_fill(self, on: bool, level: float) -> None:
        self.fill = on
        self._fill_level = level
        for n in LIVE_TRACES:
            self._apply_fill(self.live[n]["curve"], n)

    def _apply_fill(self, curve, n: int) -> None:
        if self.fill:
            curve.setFillLevel(self._fill_level)
            curve.setBrush(pg.mkBrush(_alpha(trace_color(n, self.pal), 70)))
        else:
            curve.setFillLevel(None)
            curve.setBrush(None)

    def nearest_trace(self, x: float, y: float) -> Optional[int]:
        """クリック位置に縦方向で最も近い、見えているトレースの番号（G §3.2。マウスの読み値と同じ選び方）。"""
        shown = self.shown_payloads()
        hit = nearest_point(shown, x, y)
        if hit is None or not np.isfinite(float(np.asarray(shown[hit[0]]["values"])[hit[1]])):
            return None
        return int(shown[hit[0]]["trace"])

    def shown_payloads(self) -> list[dict]:
        """表示している測定中のトレース（マウスの位置の読み値を取る相手: G §2）。"""
        return [p for p in (self.live_payload(n) for n in LIVE_TRACES) if p is not None]


class MarkerLayer:
    """マーカーの印・名前・オフセットのカーソル・XY カーソル（G §3.5）。"""

    def __init__(self, plot: pg.PlotWidget, pal: dict[str, str], on_xy_moved: Callable[[str, float], None]) -> None:
        self.pi = plot.getPlotItem()
        self.pal = pal
        # 名前・カーソルの線・機能の線は掃引ごとに作っては捨てず、使い回す（使わない分は隠す）。取り除いた部品を Python の
        # 側で手放して壊すと、グラフの部品（pyqtgraph）がまだ参照していて落ちることがあった（試験の access violation）
        self._labels: list[pg.TextItem] = []
        self._vlines: list[pg.InfiniteLine] = []
        self._hlines: list[pg.InfiniteLine] = []
        self._used = {"labels": 0, "vlines": 0, "hlines": 0, "flines": 0}
        self.scatter = pg.ScatterPlotItem(size=13, symbol="t", pen=pg.mkPen(pal["graph_bg"]))
        self.scatter.setZValue(40)
        self.pi.addItem(self.scatter, ignoreBounds=True)
        self.xy: dict[str, pg.InfiniteLine] = {}
        for name, angle in (("x1_hz", 90), ("x2_hz", 90), ("y1", 0), ("y2", 0)):
            label = name[:2].upper()
            line = pg.InfiniteLine(angle=angle, movable=True, pen=pg.mkPen(pal["accent"], width=1),
                                   hoverPen=pg.mkPen(pal["accent"], width=3),
                                   label=label, labelOpts={"position": XY_LABEL_V if angle == 90 else XY_LABEL_H,
                                                           "color": pal["accent"],
                                                           "fill": _alpha(pal["graph_bg"], XY_LABEL_FILL)})
            line.setZValue(35)
            line.hide()
            line.sigPositionChangeFinished.connect(lambda ln, k=name: on_xy_moved(k, ln.value()))
            self.pi.addItem(line, ignoreBounds=True)
            self.xy[name] = line
        self.enabled = True
        # マーカーの機能の線を出すか（G §3.6。コアの表示の設定 view.function_lines を look.apply_view が入れる）
        self.function_lines = True
        self.function_items: list[pg.InfiniteLine] = []
        # 縦軸の単位（画面が入れる）。単位の違うトレースに載ったマーカー・カーソル・機能の線は描かない（設計R §3:
        # そのトレースを描いていないため。表には出る）
        self.unit: Optional[str] = None

    def _take(self, pool: str, make) -> object:
        """使い回しの部品を1つ取る（足りなければ作ってグラフに足す）。"""
        items = {"labels": self._labels, "vlines": self._vlines, "hlines": self._hlines,
                 "flines": self.function_items}[pool]
        i = self._used[pool]
        self._used[pool] = i + 1
        if i == len(items):
            it = make()
            self.pi.addItem(it, ignoreBounds=True)
            items.append(it)
        it = items[i]
        it.show()
        return it

    def _clear(self) -> None:
        for k in self._used:
            self._used[k] = 0

    def _hide_unused(self) -> None:
        for pool, items in (("labels", self._labels), ("vlines", self._vlines), ("hlines", self._hlines),
                            ("flines", self.function_items)):
            for it in items[self._used[pool]:]:
                it.hide()

    def _cursor_line(self, pool: str, angle: int, pos: float, col: str, name: str, label_pos: float) -> None:
        def make() -> pg.InfiniteLine:
            ln = pg.InfiniteLine(angle=angle, movable=False, label=name,
                                 labelOpts={"position": label_pos, "color": col,
                                            "fill": _alpha(self.pal["graph_bg"], CURSOR_LABEL_FILL)})
            ln.setZValue(30)
            return ln

        ln = self._take(pool, make)
        ln.setValue(pos)
        ln.setPen(pg.mkPen(col, width=1, style=Qt.PenStyle.DashLine))
        ln.label.setFormat(name)
        ln.label.setColor(col)
        ln.label.fill = pg.mkBrush(_alpha(self.pal["graph_bg"], CURSOR_LABEL_FILL))
        ln.label.setPosition(label_pos)

    def _function_lines(self, r: dict, col: str) -> None:
        """マーカーの機能の結果の線（G §3.6）: 横線 = レベル、縦線 = 交点の周波数（交点が無い側は出さない）。

        マーカーの色を薄く（不透明度 約 35%）した点線。つまんで動かせず（movable=False）、読み値の表にも出さない
        （XY カーソルとは別物）。マーカーとオフセットのカーソルより下に置く。
        """
        line = r.get("line")
        if not line:
            return
        pen = pg.mkPen(_alpha(col, FUNCTION_LINE_ALPHA), width=1, style=Qt.PenStyle.DotLine)
        for pos, angle in [(float(line["level"]), 0)] + [(float(f), 90) for f in line.get("freqs_hz") or []]:
            def make() -> pg.InfiniteLine:
                ln = pg.InfiniteLine(movable=False)
                ln.setZValue(25)
                return ln

            ln = self._take("flines", make)
            ln.setAngle(angle)
            ln.setValue(pos)
            ln.setPen(pen)

    def restyle(self) -> None:
        """配色を変えたとき。印と名前は次の update で新しい色になる（画面が続けて呼ぶ）。"""
        self.scatter.setPen(pg.mkPen(self.pal["graph_bg"]))
        for line in self.xy.values():
            line.setPen(pg.mkPen(self.pal["accent"], width=1))
            line.setHoverPen(pg.mkPen(self.pal["accent"], width=3))
            line.label.setColor(self.pal["accent"])
            line.label.fill = pg.mkBrush(_alpha(self.pal["graph_bg"], 200))

    def update(self, rows: list[dict], selected: Optional[str], xy: dict) -> None:
        self._clear()
        spots = []
        mcol, scol = self.pal["marker"], self.pal["accent"]
        if self.enabled:
            n_vline = 0
            for r in rows:
                kind = r.get("kind")
                if r.get("id") in XY_IDS:
                    continue
                if self.unit and r.get("unit") and r["unit"] != self.unit:
                    continue  # 縦軸と単位の違う（描いていない）トレースのもの
                sel = r.get("id") == selected or r.get("parent") == selected and selected is not None
                col = scol if sel else mcol
                if kind == "marker" and r.get("freq_hz") is not None and r.get("value") is not None:
                    spots.append({"pos": (float(r["freq_hz"]), float(r["value"])), "brush": pg.mkBrush(col)})

                    def make_label() -> pg.TextItem:
                        t = pg.TextItem("", anchor=(0.5, 1.25))
                        t.setZValue(41)
                        return t

                    lab = self._take("labels", make_label)
                    lab.setText(r["name"], color=col)
                    lab.fill = pg.mkBrush(_alpha(self.pal["graph_bg"], 180))
                    lab.setPos(float(r["freq_hz"]), float(r["value"]))
                elif kind == "freq_cursor" and r.get("freq_hz") is not None:
                    # 近い縦線の名前が重ならないよう、名前の高さを1本ごとにずらす
                    pos = CURSOR_LABEL_V - CURSOR_LABEL_STAGGER * (n_vline % 3)
                    n_vline += 1
                    self._cursor_line("vlines", 90, float(r["freq_hz"]), col, r["name"].strip(), pos)
                elif kind == "level_cursor" and r.get("value") is not None:
                    self._cursor_line("hlines", 0, float(r["value"]), col, r["name"].strip(), CURSOR_LABEL_H)
                elif kind == "function" and self.function_lines:
                    self._function_lines(r, col)
        self._hide_unused()
        self.scatter.setData(spots)
        for k, line in self.xy.items():
            v = xy.get(k)
            if v is None or not self.enabled:
                line.hide()
                continue
            line.show()
            if not line.moving:
                line.setValue(float(v))
