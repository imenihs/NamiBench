"""信号源のタブの「ステップ掃引」（設計L §1.3。SG の欄のすぐ下: SG の周波数を動かす機能なので SG の隣に置く）。

- 欄: SG 開始・SG 終了・刻み（SG の周波数。単位付きの自由入力・桁のホイール: J §3）・1段の回数・重ね方・結果のトレース
- 見積もり: 「40 段 × 約 0.6 秒 × 4 回」（時間を約束しない: U-20）
- 「ステップ掃引を始める」: 使えないときは理由付きで無効（SG モードでない・未接続・段が多すぎる・している間: U-08）。
  進み具合と「やめる」は窓の下の処理の帯（H §4.1）、終わり方は状態バー（U-19）
- 設定はコアに1つ（命令 set_step_sweep。Qt と Web が同じものを見る）。直した値はその欄の下に一度知らせる（M-02）
- 中身は source_view.step_view（Web と同じ）。大きさと余白は sizes.py の決まり（J §4.11）。名前は欄の左（S-05）
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtWidgets import QComboBox, QGroupBox, QLabel, QPushButton, QSpinBox

from . import sizes, source_view, texts, widget_sync
from .fields import FreqField
from .tab_traces import set_enabled

if TYPE_CHECKING:
    from .main_window import MainWindow

L = texts.LABELS


class StepPanel(QGroupBox):
    def __init__(self, win: "MainWindow") -> None:
        super().__init__(L["step_group"])
        self.win = win
        form = sizes.form(self)
        self.f_start = FreqField(L["step_start"], lambda hz: self._set(start_hz=hz), big=False)
        self.f_stop = FreqField(L["step_stop"], lambda hz: self._set(stop_hz=hz), big=False)
        self.f_step = FreqField(L["step_step"], lambda hz: self._set(step_hz=hz), big=False)
        for f in (self.f_start, self.f_stop, self.f_step):
            form.addRow(f.name, f)
        self.per_step = QSpinBox()
        self.per_step.setSuffix(L["times_suffix"])
        sizes.set_width(self.per_step, "int")
        self.per_step.editingFinished.connect(lambda: self._set(per_step=int(self.per_step.value())))
        form.addRow(L["step_per_step"], self.per_step)
        self.combine = QComboBox()
        self.combine.activated.connect(lambda i: self._set(combine=self.combine.itemData(i)))
        form.addRow(L["step_combine"], self.combine)
        self.trace = QComboBox()
        self.trace.activated.connect(lambda i: self._set(trace=int(self.trace.itemData(i))))
        form.addRow(L["step_trace"], self.trace)
        self.estimate = QLabel("")
        self.estimate.setObjectName("dim")
        form.addRow(self.estimate)
        self.run = QPushButton(L["step_run"])
        self.run.clicked.connect(lambda: win.call("step_sweep_start"))
        form.addRow(sizes.buttons_row(self.run))
        # 選択肢は表から（並びと語は Web と同じ: source_view.step_view）
        v = source_view.step_view({})
        widget_sync.set_items(self.combine, [(c["text"], c["code"]) for c in v["combines"]])
        widget_sync.set_items(self.trace, [(t["text"], t["value"]) for t in v["traces"]])
        self.per_step.setRange(v["per_step_min"], v["per_step_max"])
        widget_sync.fit_combo(self.combine)
        widget_sync.fit_combo(self.trace)

    # ---------------------------------------------------------------- 命令

    def _set(self, **kw) -> None:
        self.win.call("set_step_sweep", on_ok=self._adjusted, **kw)

    def _adjusted(self, r: dict) -> None:
        """直した値を、その欄の下に一度知らせる（M-02）。"""
        fields = {"step_start": self.f_start, "step_stop": self.f_stop, "step_step": self.f_step}
        for n in source_view.adjusted_notes(r):
            w = fields.get(n["field"])
            if w is not None:
                w.flash_note(n["text"])
            else:
                self.win.status_note(n["text"])

    # ---------------------------------------------------------------- 状態を映す

    def apply_state(self, st: dict) -> None:
        v = source_view.step_view(st)
        self.f_start.set_hz(int(v["start_hz"]))
        self.f_stop.set_hz(int(v["stop_hz"]))
        self.f_step.set_hz(int(v["step_hz"]))
        widget_sync.set_value(self.per_step, int(v["per_step"]))
        widget_sync.set_data(self.combine, v["combine"])
        widget_sync.set_data(self.trace, int(v["trace"]))
        self.estimate.setText(v["estimate"])
        for w in (self.f_start, self.f_stop, self.f_step, self.per_step, self.combine, self.trace):
            set_enabled(w, v["field_reason"])
        set_enabled(self.run, v["reason"], L["step_run_tip"])
