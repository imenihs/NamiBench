"""グラフの上に描くもの（設計F §5・E §6.4・E §6.6・G §3.5）。

- TraceLayer: 測定中のトレース 1〜4（J §4.4 の色）と参照トレース（ファイル。破線）、凡例（動作・平均の種類・回数）、
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
from ..core.report_layout import legend_text
from .theme import trace_color

LIVE_TRACES = (1, 2, 3, 4)


def x_of(payload: dict) -> np.ndarray:
    """描くための横軸（float）。ゼロスパンは点の番号。周波数の正本は Hz の整数のまま。"""
    ax = payload["axis"]
    if "freqs_hz" in ax:
        return np.asarray(ax["freqs_hz"], dtype=np.float64)
    n = int(ax["points"])
    if ax.get("step_hz", 0) == 0:
        return np.arange(n, dtype=np.float64)
    return (ax["start_hz"] + ax["step_hz"] * np.arange(n, dtype=np.int64)).astype(np.float64)


def marked_points(payload: dict, n: int) -> np.ndarray:
    """控えめな色で描く点: 前置き不足（設計C §5.6）と校正の範囲外（設計D §5.3）。"""
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
    tr = pts.get("transient")
    if tr is not None and np.asarray(tr).shape == (n,):
        mark |= np.asarray(tr, dtype=bool)
    return mark


def _alpha(color: str, a: int) -> QColor:
    c = QColor(color)
    c.setAlpha(a)
    return c


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
            col = trace_color(n, pal)
            curve = self.pi.plot(pen=pg.mkPen(col, width=1), connect="finite")
            # 印の付いた点（前置き不足・校正の範囲外）は同じ線の上に控えめな色で重ねる
            mark = self.pi.plot(pen=pg.mkPen(pal["text_dim"], width=1), connect="finite")
            curve.setZValue(10 + n)
            mark.setZValue(15 + n)
            self.live[n] = {"curve": curve, "mark": mark, "payload": None, "visible": n == 1, "has_values": False}
        self.zero_line = pg.InfiniteLine(pos=0.0, angle=0, movable=False, pen=pg.mkPen(pal["zero_db"], width=1.5))
        self.zero_line.setZValue(-5)
        self.pi.addItem(self.zero_line, ignoreBounds=True)
        self.zero_line.hide()
        self._bands: list[pg.LinearRegionItem] = []
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
        self._draw_live(n)
        self._update_legend()

    def _draw_live(self, n: int) -> None:
        e = self.live[n]
        t = e["payload"]
        if t is None or not e["visible"] or not e["has_values"]:
            e["curve"].setData([], [])
            e["mark"].setData([], [])
            return
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
            if e["payload"] is not None:
                # 凡例の回数・動作は状態の方が新しいことがある（設定を変えた直後）
                e["payload"] = {**e["payload"], "action": s["action"], "count": s["current_count"],
                                "count_target": s["count_target"], "average_type": s["average_type_effective"],
                                "axis_differs": s["axis_differs"]}
            if changed:
                self._draw_live(n)
            if e["visible"] and e["has_values"] and e["payload"] is None:
                missing.append(n)
        self._update_legend()
        return missing

    def live_payload(self, n: int) -> Optional[dict]:
        e = self.live.get(n)
        if e is None or not e["visible"] or not e["has_values"]:
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
            col = trace_color(idx + 1, self.pal)
            curve = self.pi.plot(pen=pg.mkPen(col, width=1, style=Qt.PenStyle.DashLine), connect="finite")
            curve.setZValue(5)
            e = {"curve": curve, "payload": None, "color": col, "shown": True, "init": False}
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
        info = next((t.get("info") or {} for t in meta.get("traces") or [] if t["id"] == tid), {})
        e["shown"] = bool(info.get("visible", True))
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
        f = self.file_meta.get(tid) or {}
        num = tid.split(".", 1)[-1]
        when = str(f.get("created_at") or "")[:16].replace("T", " ")
        dev = f.get("device_serial") or ""
        return " ".join(p for p in (f.get("name", tid), f"#{num}", when, dev) if p)

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
        key = tuple((id(c), s) for c, s, _ in entries)
        if key == self._legend_key:
            return
        self._legend_key = key
        self.legend.clear()
        for c, s, _ in entries:
            self.legend.addItem(c, s)
        self.legend.setVisible(bool(entries))

    def set_zero_line(self, shown: bool) -> None:
        self.zero_line.setVisible(shown)

    def set_bands(self, ranges: list) -> None:
        """基準の範囲外の区間（E §6.4: 値なしの区間を色の帯で示す）。"""
        for b in self._bands:
            self.pi.removeItem(b)
        self._bands = []
        for lo, hi in ranges or []:
            b = pg.LinearRegionItem(values=(lo, hi), movable=False, brush=pg.mkBrush(_alpha(self.pal["warn"], 35)),
                                    pen=pg.mkPen(_alpha(self.pal["warn"], 90)))
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
        """クリック位置に縦方向で最も近い、見えているトレースの番号（G §3.2）。"""
        best, best_d = None, None
        for n in LIVE_TRACES:
            p = self.live_payload(n)
            if p is None:
                continue
            xs = x_of(p)
            if xs.size == 0:
                continue
            i = int(np.clip(np.searchsorted(xs, x), 0, xs.size - 1))
            if i > 0 and abs(xs[i - 1] - x) < abs(xs[i] - x):
                i -= 1
            v = float(np.asarray(p["values"])[i])
            if not np.isfinite(v):
                continue
            d = abs(v - y)
            if best_d is None or d < best_d:
                best, best_d = n, d
        return best


class MarkerLayer:
    """マーカーの印・名前・オフセットのカーソル・XY カーソル（G §3.5）。"""

    def __init__(self, plot: pg.PlotWidget, pal: dict[str, str], on_xy_moved: Callable[[str, float], None]) -> None:
        self.pi = plot.getPlotItem()
        self.pal = pal
        self._items: list = []
        self.scatter = pg.ScatterPlotItem(size=13, symbol="t", pen=pg.mkPen(pal["graph_bg"]))
        self.scatter.setZValue(40)
        self.pi.addItem(self.scatter, ignoreBounds=True)
        self.xy: dict[str, pg.InfiniteLine] = {}
        for name, angle in (("x1_hz", 90), ("x2_hz", 90), ("y1", 0), ("y2", 0)):
            label = name[:2].upper()
            line = pg.InfiniteLine(angle=angle, movable=True, pen=pg.mkPen(pal["accent"], width=1),
                                   hoverPen=pg.mkPen(pal["accent"], width=3),
                                   label=label, labelOpts={"position": 0.95 if angle == 90 else 0.05,
                                                           "color": pal["accent"], "fill": _alpha(pal["graph_bg"], 200)})
            line.setZValue(35)
            line.hide()
            line.sigPositionChangeFinished.connect(lambda ln, k=name: on_xy_moved(k, ln.value()))
            self.pi.addItem(line, ignoreBounds=True)
            self.xy[name] = line
        self.enabled = True

    def _clear(self) -> None:
        for it in self._items:
            self.pi.removeItem(it)
        self._items = []

    def update(self, rows: list[dict], selected: Optional[str], xy: dict) -> None:
        self._clear()
        spots = []
        mcol, scol = self.pal["marker"], self.pal["accent"]
        if self.enabled:
            n_vline = 0
            for r in rows:
                kind = r.get("kind")
                if r.get("id") in ("X1", "X2", "Y1", "Y2"):
                    continue
                sel = r.get("id") == selected or r.get("parent") == selected and selected is not None
                col = scol if sel else mcol
                if kind == "marker" and r.get("freq_hz") is not None and r.get("value") is not None:
                    spots.append({"pos": (float(r["freq_hz"]), float(r["value"])), "brush": pg.mkBrush(col)})
                    lab = pg.TextItem(r["name"], color=col, anchor=(0.5, 1.25), fill=_alpha(self.pal["graph_bg"], 180))
                    lab.setPos(float(r["freq_hz"]), float(r["value"]))
                    lab.setZValue(41)
                    self.pi.addItem(lab, ignoreBounds=True)
                    self._items.append(lab)
                elif kind == "freq_cursor" and r.get("freq_hz") is not None:
                    # 近い縦線の名前が重ならないよう、名前の高さを1本ごとにずらす
                    pos = 0.92 - 0.06 * (n_vline % 3)
                    n_vline += 1
                    ln = pg.InfiniteLine(pos=float(r["freq_hz"]), angle=90, movable=False,
                                         pen=pg.mkPen(col, width=1, style=Qt.PenStyle.DashLine),
                                         label=r["name"].strip(), labelOpts={"position": pos, "color": col,
                                                                            "fill": _alpha(self.pal["graph_bg"], 180)})
                    ln.setZValue(30)
                    self.pi.addItem(ln, ignoreBounds=True)
                    self._items.append(ln)
                elif kind == "level_cursor" and r.get("value") is not None:
                    ln = pg.InfiniteLine(pos=float(r["value"]), angle=0, movable=False,
                                         pen=pg.mkPen(col, width=1, style=Qt.PenStyle.DashLine),
                                         label=r["name"].strip(), labelOpts={"position": 0.08, "color": col,
                                                                            "fill": _alpha(self.pal["graph_bg"], 180)})
                    ln.setZValue(30)
                    self.pi.addItem(ln, ignoreBounds=True)
                    self._items.append(ln)
        self.scatter.setData(spots)
        for k, line in self.xy.items():
            v = xy.get(k)
            if v is None or not self.enabled:
                line.hide()
                continue
            line.show()
            if not line.moving:
                line.setValue(float(v))
