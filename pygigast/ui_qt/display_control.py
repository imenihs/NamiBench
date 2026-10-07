"""メイン画面のうち、設計P の表示（ウォーターフォール・密度・記録の再生・バンドプラン）をつなぐ所。

- 表示の組み合わせ（コアの表示の設定 layout）に合わせて、グラフの下にウォーターフォールか密度表示を出す（P §2）。
  ゼロスパン（横軸が時間）の間は出さない（display_view.shown_layout。設定は変えず、メニューと表示のタブは理由付き）
- ウォーターフォールの行: 掃引ごとのイベント `waterfall` を上に足す。最初・単位が変わった・広げた・時間の範囲を選んだ・
  再生に入った／出たときは命令 `waterfall_rows` で取り直す（P §9.2）。最新に付いていく間は1行 ROW_PX 画素、時間の
  範囲を選んだ・再生中は範囲を縦いっぱいに出す（行が多ければコアが最大値でまとめる: P §8）
- 横軸はスペクトルと同じ範囲（display_view.freq_view: ライブは掃引の範囲・再生は表示の範囲。P §2）
- 行をクリック → その時刻の掃引を出す（ライブは過去のトレース、再生はカーソル: P §3・§5）
- 再生の帯（P §9.4）・スペクトルの上のバンドプランとブックマーク（P §6）
"""
from __future__ import annotations

import math
from typing import TYPE_CHECKING, Optional

from . import display_view, look, menus, texts
from .bandplan_layer import BandplanLayer
from .density_view import DensityView
from .playback_bar import PlaybackBar
from .waterfall_view import WaterfallView

if TYPE_CHECKING:
    from .main_window import MainWindow

D = texts.DISPLAY_TEXTS
# 時刻の軸の上のホイール1段でずらす量（いま出している時間の長さに対する割合）
SCROLL_FRACTION = 0.25


class DisplayControl:
    def __init__(self, win: "MainWindow") -> None:
        self.win = win
        self.waterfall = WaterfallView(win.pal, self._reading)
        self.waterfall.plot.setToolTip(texts.LABELS["waterfall_tip"])
        self.density = DensityView(win.pal)
        self.bar = PlaybackBar(win.call)
        self.bands = BandplanLayer(win.plot, win.pal, self.center_on)
        self.epoch: Optional[int] = None
        self.seq = 0
        self.unit = "dBm"
        self.follow = True
        self.window: Optional[tuple[float, float]] = None
        self.playback = False
        self.layout = "spectrum"
        self._busy = False
        self._again = False
        self._restored = False
        self.rows_result: dict = {}
        ws = self.waterfall.graph_sig
        ws.range_selected.connect(win._on_range_selected)
        ws.freq_wheel.connect(win._on_freq_wheel)
        ds = self.density.graph_sig
        ds.range_selected.connect(win._on_range_selected)
        ds.freq_wheel.connect(win._on_freq_wheel)
        ds.level_wheel.connect(win._on_level_wheel)
        ds.level_drag.connect(win._on_level_drag)
        s = self.waterfall.sig
        s.picked.connect(self._pick)
        s.time_range.connect(self._time_range)
        s.time_scroll.connect(self._scroll)
        s.resized.connect(self.fetch)
        self.waterfall.follow_btn.clicked.connect(self._follow_clicked)

    def wire_parts(self) -> None:
        """部分の並び（docks.Parts）を作った後に、別ウィンドウのボタンをつなぐ。"""
        self.waterfall.detach_btn.clicked.connect(lambda: self.win.parts.detach("waterfall"))
        self.density.detach_btn.clicked.connect(lambda: self.win.parts.detach("density"))
        parts = self.win.parts
        for det in (parts.graph, *parts.lower.values()):
            det.on_change = self.apply_axes
        self.apply_axes()

    def apply_axes(self) -> None:
        """ウォーターフォール・密度表示の周波数軸は、スペクトルのすぐ下に付いている間は出さない（同じ軸を2回出さない:
        P §2 の連動）。どちらかを別の窓に出したら出す。"""
        parts = self.win.parts
        for key, view in (("waterfall", self.waterfall), ("density", self.density)):
            det = parts.lower.get(key)
            show = parts.graph.detached or (det is not None and det.detached)
            pi = view.plot.getPlotItem()
            pi.showAxis("bottom", bool(show))
            # 別の窓に出している間は「別ウィンドウ」を出さない（Web の別ウィンドウの画面と同じ）
            view.detach_btn.setVisible(not (det is not None and det.detached))
            # 軸を出さない間は下に余白（縦軸の一番下の目盛の数字を切らない）
            pi.layout.setContentsMargins(0, 2, 8, 0 if show else 8)

    # ---------------------------------------------------------------- 状態

    def apply_state(self, st: dict) -> None:
        win = self.win
        lay = display_view.shown_layout(st)
        win.parts.set_layout(lay)
        if not self._restored:
            self._restored = True
            win.parts.restore_lower()
        changed_layout = lay != self.layout
        self.layout = lay
        fv = display_view.freq_view(st)
        if fv:
            zero = fv["step_hz"] == 0
            x0, x1 = (0, max(1, fv["points"] - 1)) if zero else (fv["start_hz"], fv["stop_hz"])
            off = texts.freq_offset_of(st)
            self.waterfall.set_x(x0, x1, zero, off)
            self.density.set_x(x0, x1, off, zero)
            # 横軸の名前はスペクトルと同じ（周波数オフセットで換算中なら軸の名前で示す: 設計O）
            for plot in (self.waterfall.plot, self.density.plot):
                if zero:
                    plot.getPlotItem().setLabel("bottom", texts.LABELS["axis_zero_span"], units="")
                else:
                    plot.getPlotItem().setLabel("bottom", look.freq_axis_label(st), units="Hz")
            self.apply_axes()  # 名前を付けると軸が出るので、出すかを決め直す
        pal = st.get("palette") or {}
        cmap = pal.get("colormap") or "viridis"
        self.waterfall.set_colormap(cmap)
        self.density.set_colormap(cmap)
        wf = st.get("waterfall") or {}
        rng = wf.get("range") or {}
        if rng.get("lo") is not None:
            self.waterfall.set_range(rng["lo"], rng["hi"])
        pb = st.get("playback") or {}
        active = bool(pb.get("active"))
        if active != self.playback:
            self.playback = active
            self.follow = not active
            self.window = None
            self.fetch()
        elif wf.get("epoch") != self.epoch or (changed_layout and lay == "waterfall"):
            self.fetch()
        self.waterfall.set_cursor(pb.get("t") if active else self._past_time(st))
        self.bar.apply(display_view.playback_view(st))
        self.density.set_reason(display_view.density_reason(st))
        bv = display_view.bandplan_view(st)
        self.bands.set_items(bv["bands"], bv["bookmarks"])
        if self.playback:
            label, why = D["whole"], (texts.REASON_TEXTS["wf_whole"] if self.window is None else None)
        else:
            label, why = D["latest"], (texts.REASON_TEXTS["wf_following"] if self.follow else None)
        self.waterfall.set_follow_button(label, why)
        a = win.actions
        chosen = display_view.layout_of(st)
        lay_why = display_view.layout_reasons(st)
        for code, key in (("view_waterfall", "waterfall"), ("view_density", "density")):
            if code in a:
                a[code].setCheckable(True)
                a[code].setChecked(chosen == key)
                menus.set_reason(a[code], lay_why[key])  # ゼロスパンの間は選べない（理由付き: U-08）
        why = None if self.playback else texts.REASON_TEXTS["not_playing"]
        for code in ("playback_extract", "playback_export_csv"):
            if code in a:
                menus.set_reason(a[code], why)
        reasons = display_view.part_reasons(st)
        for key in ("waterfall", "density"):
            act = win.findChild(type(a["view_colors"]), "detach_" + key)
            if act is not None:
                menus.set_reason(act, reasons[key])

    def apply_y(self, ref: float, div: float, unit: str, decimals: int = 1) -> None:
        self.density.set_y(ref, div, unit, decimals)

    @staticmethod
    def _past_time(st: dict) -> Optional[float]:
        for f in st.get("files") or []:
            if f.get("kind") == "past":
                return f.get("t")
        return None

    def restyle(self) -> None:
        self.waterfall.pal = self.win.pal
        self.density.pal = self.win.pal
        self.bands.pal = self.win.pal
        self.waterfall.style_axes()
        self.density.style_axes()
        self.bands.update()

    # ---------------------------------------------------------------- イベント

    def on_event(self, kind: str, p: dict) -> None:
        if kind == "density":
            self.density.set_density(p)
            return
        if kind != "waterfall":
            return
        if p.get("range"):
            self.waterfall.set_range(p["range"]["lo"], p["range"]["hi"])
        if p.get("reset") or p.get("epoch") != self.epoch:
            self.fetch()
            return
        rows = p.get("rows") or []
        if not rows:
            return
        if p.get("unit"):
            self.unit = p["unit"]
        if self._busy:
            self._again = True
            return
        if p.get("seq", 0) - len(rows) != self.seq:
            self.fetch()  # 行が欠けた（取り直す）
            return
        self.seq = int(p.get("seq", 0))
        if self.follow and not self.playback:
            self.waterfall.append(rows)

    # ---------------------------------------------------------------- 行を取り直す

    def fetch(self) -> None:
        if self.layout != "waterfall" and not self.playback:
            # 出していないときは取らない（出したときに取り直す）。番号だけ合わせ直す
            self.epoch = None
            return
        if self._busy:
            self._again = True
            return
        self._busy = True
        self._again = False
        kw: dict = {}
        if self.follow and not self.playback:
            kw["max_rows"] = min(4000, self.waterfall.visible_rows())
        else:
            kw["max_rows"] = min(4000, self.waterfall.pixel_rows())
            if self.window is not None:
                kw["t_lo"], kw["t_hi"] = self.window

        def done(r: dict) -> None:
            self._busy = False
            self.rows_result = r
            self.epoch = r.get("epoch")
            self.seq = int(r.get("seq") or 0)
            if r.get("unit"):
                self.unit = r["unit"]
            if r.get("range"):
                self.waterfall.set_range(r["range"]["lo"], r["range"]["hi"])
            self.waterfall.set_rows(r.get("rows") or [], self.follow and not self.playback)
            if self._again:
                self.fetch()

        def failed(_e) -> None:
            # 失敗したときも次の取り直しを止めない（命令の失敗は call が知らせる）
            self._busy = False

        self.win.call("waterfall_rows", on_ok=done, on_error=failed, quiet_codes=("not_playing",), **kw)

    # ---------------------------------------------------------------- 操作

    def _pick(self, t: float, pos) -> None:
        self.win.call("waterfall_pick", t=float(t), pos=list(pos) if pos else None)

    def _time_range(self, t_lo: float, t_hi: float) -> None:
        if t_hi <= t_lo:
            return
        self.follow = False
        self.window = (t_lo, t_hi)
        self.fetch()
        self.apply_state(self.win.state)

    def _shown_span(self) -> Optional[tuple[float, float]]:
        rows = self.waterfall.shown_rows
        if not rows:
            return None
        return float(rows[-1]["t"]), float(rows[0].get("t_end", rows[0]["t"]))

    def _scroll(self, steps: int) -> None:
        span = self.window or self._shown_span()
        if span is None:
            return
        lo, hi = span
        d = (hi - lo) * SCROLL_FRACTION * steps
        lo, hi = lo + d, hi + d
        r = self.rows_result
        t_last = r.get("t_last")
        t_first = r.get("t_first")
        if not self.playback and steps > 0 and t_last is not None and hi >= t_last:
            self._follow_clicked()
            return
        if t_first is not None and lo < t_first:
            lo, hi = t_first, t_first + (hi - lo)
        self.follow = False
        self.window = (lo, hi)
        self.fetch()
        self.apply_state(self.win.state)

    def _follow_clicked(self) -> None:
        self.window = None
        self.follow = not self.playback
        self.fetch()
        self.apply_state(self.win.state)

    # ---------------------------------------------------------------- 再生している記録の切り出し・書き出し（H §3.1・§5）

    def _range_kw(self) -> dict:
        """範囲: ウォーターフォールで選んだ時間（選んでいなければ記録の全体）。周波数は省く（コアがいまの表示の範囲にする）。"""
        return {"t_lo": self.window[0], "t_hi": self.window[1]} if self.window else {}

    def extract(self) -> None:
        from .files import H5_FILTER, default_name

        f = self.win.files
        path = self.win.ask_save_path(D["extract_title"], str(f.last_dir() / default_name(".h5")), H5_FILTER)
        if path:
            f._remember(path)
            self.win.call("playback_extract", path=str(path), **self._range_kw())

    def export_record(self) -> None:
        from .files import CSV_FILTER, default_name

        f = self.win.files
        path = self.win.ask_save_path(D["export_record_title"], str(f.last_dir() / default_name(".csv")), CSV_FILTER)
        if path:
            f._remember(path)
            self.win.call("playback_export_csv", path=str(path), **self._range_kw())

    def center_on(self, hz: int) -> None:
        """ブックマークの周波数を Center にする（P §6。再生中は表示の範囲の中心: 測り直さない）。"""
        self.win._set_sweep(center_hz=int(hz), field=self.win.f_center)

    def add_bookmark(self, hz: int) -> None:
        name = self.win.ask_text(D["bookmark_add_title"], D["bookmark_name"])
        if name and name.strip():
            self.win.call("bookmark_add", name=name.strip(), hz=int(hz))

    def toggle_layout(self, code: str) -> None:
        """メニュー「表示」のウォーターフォール・密度表示: 選ぶ／もう一度でスペクトルのみ（1操作: P §2）。"""
        cur = display_view.layout_of(self.win.state)
        self.win.call("set_view", layout="spectrum" if cur == code else code)

    # ---------------------------------------------------------------- 読み値

    def _reading(self, row: dict, x: float, level: Optional[float]) -> list[str]:
        zero = bool(row.get("zero_span"))
        step = float(row["step_hz"]) or 1.0
        j = int(math.floor((x - float(row["start_hz"])) / step + 0.5))  # Web の Math.round と同じ
        freq = None if zero else int(row["start_hz"]) + int(row["step_hz"]) * j
        return display_view.waterfall_reading(row["t"], int(row.get("sweeps") or 1), freq, level, self.unit,
                                              texts.freq_offset_of(self.win.state), j if zero else None)
