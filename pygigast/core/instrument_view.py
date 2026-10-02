"""計測器の本体のうち、表示の設定・配色・プリセット・覚えた機器（設計O・J §4.4）。Instrument に混ぜる。

- 表示の設定（view）: 単位 dBm／dBμV／dBmV・外部補正 dB・周波数オフセット・描き方（線・塗りつぶし）・
  表示の組み合わせ・マーカーの機能の線を出すか（G §3.6）。換算そのものは core/view_units.py。測った値と機器へ
  送るものは変えない
  - 単位・外部補正を変えたら、REF レベル・XY カーソルの Y・しきい値も同じだけずらす（画面の絵は変わらず、
    数字だけ新しい単位になる。実機の計測器と同じ）。いまの縦軸が比（ノーマライズ中の dB）のときはずらさない
- 配色（palette）: ユーザー設定の1つ。ライト・ダークは初期値に戻す命令（確認は画面: 変えた色の数は状態にある）
- プリセット（O §1.2）: モード・掃引条件・振幅・トレースの設定・表示の設定。校正とノーマライズの基準・
  マーカーは入れない。呼び出しは1回の命令で、元に戻せる
- 表示の設定・配色・プリセットの呼び出しは「元に戻す」に積む（設計J §3・§4.4）。積むのは変えた部分だけ
  （instrument_display.py の _undo_snapshot の keys）
"""
from __future__ import annotations

import dataclasses
from typing import Optional

from . import view_units
from .instrument_common import CommandError, adjustments_to_list, adopted_to_dict
from .palette import BASES, Palette, normalize_color
from .presets import PresetError, PresetStore
from .types import Mode, Quantity, SweepRequest

_UNSET = object()


class ViewMixin:
    """表示の設定・配色・プリセット・覚えた機器（Instrument に混ぜる）。"""

    def _init_view(self, st: Optional[dict]) -> None:
        self._view_opts = view_units.restore((st or {}).get("view"))
        self._palette = Palette.from_saved(self._settings.get("theme"))
        self._presets = PresetStore(self._root / "presets")

    def _view_state_to_save(self) -> dict:
        return {"view": dict(self._view_opts)}

    def _save_palette_setting(self) -> None:
        """配色は Qt と Web が共有する設定。変えたらすぐファイルにも書く（落ちても次の起動で同じ色）。"""
        self._settings.set("theme", self._palette.to_saved())
        try:
            self._settings.save()
        except OSError as e:
            self._notice("settings_unwritable", {"message": str(e)})

    # ------------------------------------------------------------------ 状態

    def _view_snapshot(self) -> dict:
        o = self._view_opts
        return {
            "view": {**o, "level_unit_label": view_units.LEVEL_UNITS.get(o["level_unit"], o["level_unit"]),
                     "level_units": dict(view_units.LEVEL_UNITS), "draw_styles": list(view_units.DRAW_STYLES)},
            "palette": self._palette.state(),
            "known_devices": self._known_devices_state(),
        }

    def _level_shift(self, quantity: Optional[Quantity], profile: Optional[dict]) -> tuple[float, Optional[str]]:
        """トレースの値に足す dB と、出す単位の名前（view_units.level_shift）。"""
        from .instrument_display import unit_for

        return view_units.level_shift(unit_for(quantity, profile), self._view_opts)

    # ------------------------------------------------------------------ 表示の設定

    def _power_shift_now(self, opts: dict) -> float:
        """いまの機器の電力の読み値に足す dB（REF などをずらす量を決めるため）。"""
        from .instrument_display import unit_for

        return view_units.level_shift(unit_for(Quantity.POWER, self._profile), opts)[0]

    def _set_view_opts(self, new: dict, shift_levels: bool = True) -> None:
        """表示の設定を置き換える。単位・外部補正が変わり、いまの縦軸が電力なら、レベルの数字を同じだけずらす。"""
        from .instrument_display import unit_for

        old = self._view_opts
        delta = self._power_shift_now(new) - self._power_shift_now(old)
        self._view_opts = dict(new)
        if self._amplitude.get("unit") not in (None, "dB"):
            # 縦軸の単位を決めてあれば（マーカーから REF に・など）、電力の単位を新しい単位の名前にする
            self._amplitude["unit"] = view_units.level_shift(unit_for(Quantity.POWER, self._profile), new)[1]
        if delta and self._last_quantity == Quantity.POWER:
            if shift_levels:
                ref = self._amplitude.get("ref_level")
                self._amplitude["ref_level"] = round((0.0 if ref is None else float(ref)) + delta, 2)
            xy = self._markers.xy
            for k in ("y1", "y2"):
                if getattr(xy, k) is not None:
                    setattr(xy, k, round(getattr(xy, k) + delta, 2))
            for m in self._markers.markers():
                if m.threshold is not None:
                    m.threshold = dataclasses.replace(m.threshold, level=round(m.threshold.level + delta, 2))
            if self._markers.peak_threshold is not None:
                self._markers.peak_threshold = round(self._markers.peak_threshold + delta, 2)
        # 値の単位が変わるので、トレース・マーカー・REF の自動を出し直す
        self._emit_visible_traces()
        for e in self._files.values():
            for tid in e["traces"]:
                try:
                    self._emit("trace", self._file_trace_payload(tid))
                except CommandError:
                    pass
        if new.get("level_unit") != old.get("level_unit"):
            # 単位の設定が変わったら、どこから変わっても（この画面・別の画面・プリセット・元に戻す）全部の画面が
            # 一度知らせる。いまの表示が比でも知らせる（J §4.10 の 4）。起動で戻すときはここを通らない
            self._notice("level_unit_changed", {"unit": new.get("level_unit"), "previous": old.get("level_unit")})

    def _cmd_set_view(self, level_unit=_UNSET, ext_db=_UNSET, freq_offset_hz=_UNSET, draw_style=_UNSET,
                      layout=_UNSET, function_lines=_UNSET) -> dict:
        kw = {k: v for k, v in (("level_unit", level_unit), ("ext_db", ext_db), ("freq_offset_hz", freq_offset_hz),
                                ("draw_style", draw_style), ("layout", layout), ("function_lines", function_lines))
              if v is not _UNSET}
        for k in view_units.DEFAULTS:
            if k in kw and kw[k] is None:
                kw[k] = view_units.DEFAULTS[k]  # null は初期値に戻す（設定の画面の「初期値に戻す」）
        bad = view_units.check_options(**kw)
        if bad:
            code = "not_integer" if bad == "freq_offset_hz" and not isinstance(kw[bad], int) else "bad_value"
            raise CommandError(code, bad, {"value": kw[bad]})
        if "ext_db" in kw:
            kw["ext_db"] = float(kw["ext_db"])
        new = {**self._view_opts, **kw}
        if new != self._view_opts:
            before = self._undo_snapshot(("view",))
            self._set_view_opts(new)
            self._record_undo(before)
            self._changed()
        return {"view": dict(self._view_opts), "amplitude": dict(self._amplitude)}

    # ------------------------------------------------------------------ 配色（設計J §4.4）

    def _cmd_palette_set(self, colors: dict) -> dict:
        """色を変える（{名前: "#rrggbb"}）。読めない名前・色は変えずに失敗にする。"""
        if not isinstance(colors, dict) or not colors:
            raise CommandError("bad_value", "colors")
        clean = {}
        for k, v in colors.items():
            c = normalize_color(v)
            if k not in self._palette.colors or c is None:
                raise CommandError("bad_color", "colors", {"name": k, "value": v})
            clean[k] = c
        if all(self._palette.colors[k] == c for k, c in clean.items()):
            return {"palette": self._palette.state()}
        before = self._undo_snapshot(("palette",))
        self._palette.colors.update(clean)
        self._record_undo(before)
        self._save_palette_setting()
        self._changed()
        return {"palette": self._palette.state()}

    def _cmd_palette_reset(self, base: str) -> dict:
        """ユーザー設定の各色を、ライト（ダーク）の初期値で上書きする。何色を失うかの確認は画面が行う。"""
        if base not in BASES:
            raise CommandError("bad_value", "base", {"choices": list(BASES)})
        before = self._undo_snapshot(("palette",))
        lost = len(self._palette.changed())
        self._palette = Palette(base)
        self._record_undo(before)
        self._save_palette_setting()
        self._changed()
        return {"palette": self._palette.state(), "lost": lost}

    # ------------------------------------------------------------------ プリセット（設計O §1.2）

    def _preset_content(self) -> dict:
        from .instrument_display import trace_settings

        a = self._adopted
        return {
            "sweep": {"mode": a.mode.value, "start_hz": a.start_hz, "stop_hz": a.stop_hz,
                      "rbw_preference_hz": a.rbw_preference_hz, "points_setting": a.points_setting,
                      "image_cancel": a.image_cancel, "sg_hz": a.sg_hz, "span_lock_hz": a.span_lock_hz},
            "traces": [trace_settings(t) for t in self._traces],
            "amplitude": {k: self._amplitude.get(k) for k in ("ref_level", "db_per_div", "unit")},
            "view": dict(self._view_opts),
        }

    def _preset_error(self, e: PresetError) -> CommandError:
        field = "name" if e.code.startswith("preset_name") else "id"
        return CommandError(e.code, field, e.detail)

    def _cmd_preset_list(self) -> dict:
        rows, broken = self._presets.list()
        return {"presets": rows, "broken": broken}

    def _cmd_preset_save(self, name: str, overwrite_id: Optional[str] = None) -> dict:
        a = self._adopted
        summary = {"mode": a.mode.value, "start_hz": a.start_hz, "stop_hz": a.stop_hz, "rbw_hz": a.rbw_hz}
        try:
            return self._presets.save(name, self._preset_content(), summary, overwrite_id)
        except PresetError as e:
            raise self._preset_error(e) from e
        except OSError as e:
            raise CommandError("preset_unwritable", None, {"message": str(e)}) from e

    def _cmd_preset_rename(self, id: str, name: str) -> dict:  # noqa: A002 — 命令の引数名（Web と同じ）
        try:
            return self._presets.rename(id, name)
        except PresetError as e:
            raise self._preset_error(e) from e

    def _cmd_preset_delete(self, id: str) -> dict:  # noqa: A002
        try:
            self._presets.delete(id)
        except PresetError as e:
            raise self._preset_error(e) from e
        return {}

    def _preset_adopted(self, sw: dict):
        """プリセットの掃引条件を、いまの機器の掃引条件モデルに通す。RBW が無い機器なら RBW だけ既定にする。"""
        base = self._adopted
        req = SweepRequest(
            start_hz=int(sw["start_hz"]), stop_hz=int(sw["stop_hz"]), edited=("start_hz", "stop_hz"),
            rbw_preference_hz=sw.get("rbw_preference_hz"), points=sw.get("points_setting") or 0,
            mode=Mode(sw.get("mode", "sa")), sg_hz=sw.get("sg_hz"), image_cancel=bool(sw.get("image_cancel")),
            span_lock=bool(sw.get("span_lock_hz")), one_shot=True,
        )
        try:
            new = self._model.apply(req, base)
        except ValueError:
            new = self._model.apply(dataclasses.replace(req, rbw_preference_hz=None), base)
        if sw.get("span_lock_hz"):
            new = dataclasses.replace(new, span_lock_hz=int(sw["span_lock_hz"]))
        elif new.span_lock_hz is not None:
            new = self._model.apply(SweepRequest(span_lock=False), new)
        return new

    def _cmd_preset_load(self, id: str) -> dict:  # noqa: A002
        """いまの状態をプリセットで置き換える（1回の命令。Ctrl+Z で戻せる）。"""
        from .instrument_display import apply_trace_settings

        self._check_not_taking_reference()
        try:
            d = self._presets.get(id)
        except PresetError as e:
            raise self._preset_error(e) from e
        c = d["content"]
        try:
            new = self._preset_adopted(c.get("sweep") or {})
        except (KeyError, ValueError, TypeError) as e:
            raise CommandError("preset_unreadable", "id", {"id": id, "message": str(e)}) from e
        self._rtlsdr_check_condition(new)
        before = self._undo_snapshot(("sweep", "view", "amplitude"))
        self._set_view_opts(view_units.restore(c.get("view")), shift_levels=False)
        amp = c.get("amplitude") or {}
        for k in ("ref_level", "db_per_div", "unit"):
            self._amplitude[k] = amp.get(k)
        for td in c.get("traces") or []:
            try:
                apply_trace_settings(self._traces[int(td["number"])], td)
            except (KeyError, TypeError, ValueError):
                pass
        self._set_adopted(new)
        self._record_undo(before)
        self._emit_visible_traces()
        self._changed()
        return {"name": d["name"], "adopted": adopted_to_dict(new), "adjustments": adjustments_to_list(new)}

    # ------------------------------------------------------------------ 覚えた機器（設定の画面の「接続」）

    def _known_devices_state(self) -> list[dict]:
        last = self._settings.last_serial()
        out = []
        for s in self._settings.known_serials():
            name = None
            for kind in ("gigast", "rtlsdr"):
                name = name or self._settings.device_name(f"{kind}:{s}")
            out.append({"serial": s, "name": name, "last": s == last})
        return out

    def _cmd_forget_device(self, serial: str) -> dict:
        """覚えた機器から外す（次の起動で自動で繋がない。繋いでいる機器は切らない）。"""
        if serial not in self._settings.known_serials():
            raise CommandError("device_not_found", "serial", {"serial": serial})
        self._settings.forget_device(serial)
        self._changed()
        return {"known_devices": self._known_devices_state()}
