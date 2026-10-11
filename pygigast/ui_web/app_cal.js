// 校正の画面（設計K §4.1〜§4.5。Qt の ui_qt/cal_dialog.py と同じ手順・同じ語・同じ動き: J §5）。
// 準備 → 測る（旧ファイルは読み込む）→ 確かめる → 保存。補正値はコアが計算する（利用者に足し引きさせない: K §2）。
// 作業の値はコアの状態 calibration_work にあり、文字・表の行・使えない理由はサーバーが Qt と同じ cal_view.view で作って
// 状態の ui.cal で送る。ここは映して、押されたら命令を送るだけ（文を組み立てない）。
// - 窓の題は開いたメニューの項目の名前（U-24）。何を校正するかを準備で変えると題も変わる
// - 測った値があるのに閉じる・切り替えるときは、何が失われるかを言って聞く。測った点だけ保存することも選べる（U-18）
// - 測るのは数秒〜数十秒。進み具合とやめるボタンを出し、終わり方をその手順のそばに出す（U-19・U-25）
// 大きさと余白は style.css の段の決まり（J §4.11）: 窓の中は標準の段、数値の欄は値の幅（num-field）
(function (root) {
  "use strict";

  const X = root.GSTexts;
  const U = root.GSUnits;
  const C = (k, kw) => X.fmtText(X.get("CAL_TEXTS", k), kw);
  const KINDS = ["freq", "rbw", "nf", "legacy"];
  const PREPARE = 0;
  const MEASURE = 1;
  const CHECK = 2;
  const SAVED = 3;

  // 点の一覧（周波数特性の測る・確かめる・旧ファイル）。列は Qt の cal_dialog と同じ
  const CAL = `
<div class="modal-back settings-back" @click="a.calClose()">
<div class="modal calwin" @click.stop @keydown="a.calKey($event)">
  <h3>{{ v.title }}</h3>
  <div class="cal-steps row nowrap">
    <template v-for="(s, i) in v.steps" :key="i"><span v-if="i" class="dim">›</span><span :class="i === w.page ? 'cal-step-on' : 'dim'">{{ s }}</span></template>
  </div>
  <div class="calwin-body" :data-page="w.page">
    <div class="form" v-if="w.page === 0">
      <label>{{ C('device') }}</label>
      <div class="row"><span>{{ v.device }}</span><span class="warn" v-if="v.device_reason">{{ v.device_reason }}</span></div>
      <label>{{ C('current_cal') }}</label><span>{{ v.base }}</span>
      <label class="cal-top">{{ C('kind_group') }}</label>
      <div class="stack cal-kinds">
        <label class="inline" v-for="k in v.kinds" :key="k.code"><input type="radio" name="cal_kind" :value="k.code"
          :checked="k.code === v.kind" :disabled="v.measuring" @change="a.calChooseKind(k.code, $event)">{{ k.label }}</label>
      </div>
      <label>{{ C('need') }}</label><span>{{ v.need }}</span>
      <template v-if="v.wiring">
        <label class="cal-top">{{ C('wiring') }}</label>
        <div class="stack">
          <div class="cal-diagram">
            <template v-for="(b, i) in v.diagram" :key="i">
              <span class="cal-box">{{ b }}</span>
              <svg v-if="i < v.diagram.length - 1" class="cal-arrow" viewBox="0 0 28 26" width="28" height="26" aria-hidden="true">
                <line x1="4" y1="13" x2="22" y2="13" stroke="currentColor" stroke-width="1.5"/><polygon points="24,13 18,9 18,17" fill="currentColor"/></svg>
            </template>
          </div>
          <span>{{ v.wiring }}</span>
        </div>
      </template>
    </div>

    <template v-else-if="w.page === 1">
      <div class="cal-freq" v-if="v.kind === 'freq'">
        <div class="cal-points" tabindex="0" @keydown="a.calPointKey($event)">
          <table class="mtable">
            <thead><tr><th class="r">{{ v.headers.freq }}</th><th class="r">{{ v.headers.before }}</th><th class="r">{{ v.headers.after }}</th><th class="fill">{{ v.headers.state }}</th></tr></thead>
            <tbody>
              <tr v-for="p in v.points" :key="p.index" :class="{sel: p.current}" @click="a.calSelect(p.index)">
                <td class="r">{{ p.freq }}</td><td class="r">{{ p.before }}</td><td class="r">{{ p.after }}</td>
                <td class="fill" :class="{warn: p.big}" :title="p.big ? v.big_tip : ''">{{ p.big ? C('big_mark') + ' ' : '' }}{{ p.state }}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <div class="stack cal-side">
          <div class="form">
            <label>{{ C('sg_level') }}</label>
            <num-field :text="a.calDbText(v.inputs.sg_level_dbm, 'dBm')" :note="a.notes.cal_sg" :disabled="v.measuring"
                       @clear-note="a.clearNote('cal_sg')" @commit="a.calDb('sg_level_dbm', 'cal_sg', 'dBm', $event)"
                       @step="(e, d) => a.calSet({sg_level_dbm: v.inputs.sg_level_dbm + d * 10 ** e}, 'cal_sg')"></num-field>
            <label>{{ C('cable_loss') }}</label>
            <num-field :text="a.calDbText(v.inputs.cable_loss_db, 'dB')" :note="a.notes.cal_loss" :disabled="v.measuring"
                       @clear-note="a.clearNote('cal_loss')" @commit="a.calDb('cable_loss_db', 'cal_loss', 'dB', $event)"
                       @step="(e, d) => a.calSet({cable_loss_db: v.inputs.cable_loss_db + d * 10 ** e}, 'cal_loss')"></num-field>
          </div>
          <div class="cal-instruction">{{ v.instruction }}</div>
          <div class="row">
            <button :disabled="!!v.measure_reason" :title="v.measure_reason || ''" @click="a.calMeasure()">{{ C('measure') }}</button>
            <button :disabled="!!v.skip_reason" :title="v.skip_reason || ''" @click="a.calSkip()">{{ C('skip') }}</button>
          </div>
          <div class="row nowrap cal-progress" v-if="v.measuring"><progress :max="prog.total || 1" :value="prog.done"></progress><span>{{ prog.text }}</span>
            <button @click="a.calCancel()">{{ a.W('cancel') }}</button></div>
          <div class="warn" v-if="v.message">{{ v.message }}</div>
        </div>
      </div>

      <div class="stack cal-fill" v-else-if="v.kind === 'rbw'">
        <label class="inline"><input type="checkbox" :checked="v.inputs.rbw_extra" :disabled="v.measuring" @change="a.calSet({rbw_extra: $event.target.checked})">{{ C('rbw_extra') }}</label>
        <button :disabled="!!v.measure_reason" :title="v.measure_reason || ''" @click="a.calMeasure()">{{ C('start') }}</button>
        <div class="row nowrap cal-progress" v-if="v.measuring"><progress :max="prog.total || 1" :value="prog.done"></progress><span>{{ prog.text }}</span>
          <button @click="a.calCancel()">{{ a.W('cancel') }}</button></div>
        <div class="warn" v-if="v.message">{{ v.message }}</div>
        <div class="cal-scroll"><table class="mtable cal-rbw">
          <thead><tr><th class="r" v-for="(hd, i) in v.rbw_headers" :key="i">{{ hd }}</th></tr></thead>
          <tbody><tr v-for="r in v.rbw_rows" :key="r.freq_hz" :class="{'cal-used': r.used}"><td class="r" v-for="(c, i) in r.cells" :key="i">{{ c }}</td></tr></tbody>
        </table></div>
      </div>

      <div class="stack" v-else-if="v.kind === 'nf'">
        <div class="form">
          <label>{{ C('nf_freq') }}</label>
          <num-field :text="a.calFreqText(v.inputs.nf_freq_hz)" :note="a.notes.cal_nf" :disabled="v.measuring"
                     @clear-note="a.clearNote('cal_nf')" @commit="a.calFreq($event)"
                     @step="(e, d) => a.calSet({nf_freq_hz: v.inputs.nf_freq_hz + d * (e + 6 >= 0 ? 10 ** (e + 6) : 1)}, 'cal_nf')"></num-field>
        </div>
        <div class="cal-nf">
          <template v-for="r in v.nf_rows" :key="r.state">
            <span>{{ r.text }}</span>
            <button :disabled="!!v.measure_reason" :title="v.measure_reason || ''" @click="a.calMeasure(r.state)">{{ C('measure') }}</button>
            <span>{{ r.value }}</span>
          </template>
        </div>
        <div v-if="v.nf_result">{{ v.nf_result }}</div>
        <div class="row nowrap cal-progress" v-if="v.measuring"><progress :max="prog.total || 1" :value="prog.done"></progress><span>{{ prog.text }}</span>
          <button @click="a.calCancel()">{{ a.W('cancel') }}</button></div>
        <div class="warn" v-if="v.message">{{ v.message }}</div>
      </div>

      <div class="stack" v-else>
        <div class="form">
          <label>{{ C('file') }}</label>
          <div class="row"><span>{{ v.legacy_file }}</span>
            <button :disabled="!!v.measure_reason" :title="v.measure_reason || ''" @click="a.calPickFile()">{{ C('choose_file') }}</button></div>
        </div>
        <div class="warn" v-if="v.message">{{ v.message }}</div>
      </div>
    </template>

    <div class="stack cal-fill" v-else-if="w.page === 2">
      <table class="mtable cal-items">
        <thead><tr><th>{{ v.headers.item }}</th><th class="r">{{ C('graph_before') }}</th><th class="r">{{ C('graph_after') }}</th>
          <th class="r">{{ C('col_diff') }}</th><th class="r" v-if="v.legacy">{{ v.headers.measured }}</th><th></th></tr></thead>
        <tbody><tr v-for="it in v.items" :key="it.item">
          <td>{{ it.name }}</td><td class="r">{{ it.before }}</td><td class="r">{{ it.after }}</td>
          <td class="r" :class="{warn: it.big || it.warn}">{{ it.diff }}</td><td class="r" v-if="v.legacy">{{ it.measured }}</td>
          <td class="fill" :class="{warn: it.big || it.warn}" :title="it.warn || (it.big ? v.big_tip : '')">{{ it.warn || (it.big ? C('big_mark') : '') }}</td>
        </tr></tbody>
      </table>
      <template v-if="v.graph">
        <div class="cal-graph-wrap">
          <div class="cal-legend"><span class="cal-leg-before">{{ v.graph.before_label }}</span><span class="cal-leg-after">{{ v.graph.after_label }}</span></div>
          <div class="cal-graph" ref="graph"></div>
        </div>
        <div class="cal-scroll cal-check-points"><table class="mtable">
          <thead><tr><th class="r">{{ v.headers.freq }}</th><th class="r">{{ v.headers.before }}</th><th class="r">{{ v.headers.after }}</th>
            <th class="r">{{ v.headers.diff }}</th><th class="r" v-if="v.legacy">{{ v.headers.measured }}</th><th></th></tr></thead>
          <tbody><tr v-for="p in v.points" :key="p.index">
            <td class="r">{{ p.freq }}</td><td class="r">{{ p.before }}</td><td class="r">{{ p.after }}</td><td class="r">{{ p.diff }}</td>
            <td class="r" v-if="v.legacy">{{ p.measured }}</td>
            <td class="fill" :class="{warn: p.big || p.warn}" :title="p.warn || (p.big ? v.big_tip : '')">{{ p.warn || (p.big ? C('big_mark') : '') }}</td>
          </tr></tbody>
        </table></div>
      </template>
      <div class="dim" v-if="v.save_reason">{{ v.save_reason }}</div>
    </div>

    <div v-else>{{ v.saved_text }}</div>
  </div>
  <div class="modal-btns cal-btns">
    <button :disabled="!backOk" @click="a.calBack()">{{ C('back') }}</button>
    <button :disabled="!!nextReason" :title="nextReason || ''" @click="a.calNext()">{{ nextLabel }}</button>
    <button class="cal-close" @click="a.calClose()">{{ C('close') }}</button>
  </div>
</div>
</div>`;

  root.GSAppCal = {
    data() {
      return { calWin: null }; // { page, asked }（手順の位置は画面ごと。作業の値はコア）
    },
    computed: {
      calView() { return (this.ui && this.ui.cal) || null; },
    },
    methods: {
      C(k, kw) { return C(k, kw); },
      openCal(kind) {
        if (this.readOnlyBlocks("cal_open")) return;
        // 窓は作業ができてから出す（Qt と同じ）: コアは校正データを読んでから作業を作るので、それより前に届いた状態には
        // 作業が無い。それを「別の画面で閉じた」と取り違えないよう、作業を一度見るまでは閉じない（opened）
        if (!this.calWin) this.calWin = { page: PREPARE, asked: null, opened: false, pointsSig: null };
        // 戻り値は開くの命令が終わるまでの Promise（試験が、命令が届く前に別の画面で閉じる形を作らないために待つ）
        return this.cmd("cal_open", { kind }).then((r) => {
          if (!r && this.calWin && !this.calWin.opened) this.calWin = null; // 開けなかった（知らせは cmd が出す）
        });
      },
      // 状態が届いたとき（app.js の onEvent）。作業が閉じた（別の画面で閉じた）ら窓も閉じる。確かめ待ちの点は1回だけ聞く
      onStateCal(p) {
        const v = (p.ui || {}).cal;
        const w = this.calWin;
        if (!w) return;
        if (!v) {
          if (w.opened) this.calWin = null;
          return;
        }
        w.opened = true;
        if (w.kind && w.kind !== v.kind && (w.page === MEASURE || w.page === CHECK)) w.page = PREPARE;
        w.kind = v.kind;
        if (v.pending && w.asked !== v.pending.index) {
          w.asked = v.pending.index;
          this.askConfirm(v.pending.title, v.pending.text, v.pending.ok).then((ok) => {
            this.cmd("cal_confirm", { accept: !!ok }, { quiet: ["calibration_no_pending"] });
          });
        } else if (!v.pending) {
          w.asked = null;
        }
        this.calRevealSel(v);
      },
      // 選んでいる点の行へ寄せる。寄せるのは点の一覧の中身か手順の位置が変わったときだけ（Qt と同じ。状態のたびに寄せると、
      // 手でスクロールした一覧が掃引のたびに戻される）。手順の位置は状態の間にも変わる（戻る・次へ。一覧は作り直されて
      // 先頭に戻る）ので、そこからも呼ぶ
      calRevealSel(v) {
        const w = this.calWin;
        if (!w || !v) return;
        const sig = JSON.stringify([w.page, (v.points || []).map((p) => [p.freq, p.before, p.after, p.state, p.big, p.current])]);
        if (sig === w.pointsSig) return;
        w.pointsSig = sig;
        this.$nextTick(() => {
          const row = document.querySelector(".calwin .cal-points tr.sel");
          if (row) row.scrollIntoView({ block: "nearest" });
        });
      },
      calSet(args, field) { return this.cmd("cal_set", args, { field: field || null }); },
      calDbText(v, unit) { return `${Number(v).toFixed(1)} ${unit}`; },
      calFreqText(hz) { return U.freqFieldText(hz); },
      calDb(key, field, unit, text) {
        let v;
        try { v = U.parseDb(String(text).replace(unit, "")); } catch (e) { this.note(field, X.w("bad_value")); return; }
        this.calSet({ [key]: v }, field);
      },
      calFreq(text) {
        let hz;
        try { hz = U.parseFreqHz(text); } catch (e) { this.note("cal_nf", X.w("bad_freq")); return; }
        this.calSet({ nf_freq_hz: hz }, "cal_nf");
      },
      calSelect(i) {
        const v = this.calView;
        if (!v || v.measuring || v.pending) return;
        const cur = (v.points.find((p) => p.current) || {}).index;
        if (i !== cur && i >= 0 && i < v.points.length) this.calSet({ current: i });
      },
      // 点の一覧で ↑↓（Qt の表と同じくキーボードで点を選ぶ: C-02）
      calPointKey(e) {
        if (e.key !== "ArrowUp" && e.key !== "ArrowDown") return;
        e.preventDefault();
        const v = this.calView;
        const cur = (v.points.find((p) => p.current) || {}).index || 0;
        this.calSelect(cur + (e.key === "ArrowUp" ? -1 : 1));
      },
      // Enter で「測る」（K §4.2）。欄の中の Enter は欄の確定（U-13）なので、欄にいないときだけ
      calKey(e) {
        if (e.key !== "Enter" || e.target.closest("input, textarea, select, button")) return;
        const v = this.calView;
        if (this.calWin.page === MEASURE && v && v.kind === "freq" && !v.measure_reason) {
          e.preventDefault();
          this.calMeasure();
        }
      },
      calMeasure(state) { this.cmd("cal_measure", state ? { state } : {}); },
      calSkip() { this.cmd("cal_skip"); },
      calCancel() {
        const v = this.calView;
        if (v && v.task_id) this.cmd("cancel_task", { task_id: v.task_id }, { quiet: ["unknown_task"] });
      },
      async calChooseKind(kind, ev) {
        const v = this.calView;
        if (!v || kind === v.kind) return;
        if (v.unsaved && !(await this.askConfirm(v.title, v.switch_question, C("do_switch")))) {
          if (ev && ev.target) ev.target.checked = false;
          const cur = document.querySelector(`.calwin input[name=cal_kind][value=${v.kind}]`);
          if (cur) cur.checked = true;
          return;
        }
        this.cmd("cal_set_kind", { kind, discard: true });
      },
      async calPickFile() {
        const id = await this.pickUpload();
        if (id) this.cmd("cal_legacy_load", { upload_id: id });
      },
      calBack() {
        const w = this.calWin;
        if (w && (w.page === MEASURE || w.page === CHECK)) w.page -= 1;
        this.calRevealSel(this.calView);
      },
      async calNext() {
        const w = this.calWin;
        if (!w) return;
        if (w.page === PREPARE || w.page === MEASURE) w.page += 1;
        else if (w.page === CHECK) {
          const r = await this.cmd("cal_save");
          if (r && this.calWin) this.calWin.page = SAVED;
        } else w.page = PREPARE;
        this.calRevealSel(this.calView);
      },
      // 閉じる（✕・Escape・閉じる・外を押す）。測った値があれば、何が失われるかを言って聞く（U-18）
      async calClose() {
        const v = this.calView;
        if (v && v.unsaved) {
          const ans = await this.askConfirm(v.title, v.close_question, v.close_save_label,
            { alt: C("do_close_discard"), okDisabled: !v.close_savable });
          if (!ans) return;
          if (ans === true && !(await this.cmd("cal_save"))) return;
        }
        await this.cmd("cal_close");
        this.calWin = null;
      },
    },
  };

  // 確かめるグラフ（前と新・差が大きい点に印。Qt の cal_dialog の pyqtgraph と同じ線と印）
  function drawGraph(el, g, pal) {
    const xs = g.freqs_mhz;
    const marks = xs.map(() => null);
    for (const i of g.marks) marks[i] = g.after[i];
    const opts = {
      width: Math.max(200, el.clientWidth), height: Math.max(120, el.clientHeight),
      padding: [10, 14, 0, 0],
      legend: { show: false },
      cursor: { show: false },
      scales: { x: { time: false, auto: false, range: () => [0, g.x_max] } },
      axes: [
        { label: g.x_label, stroke: pal.text_dim, grid: { stroke: pal.grid }, ticks: { stroke: pal.axis },
          values: (u, vals) => vals.map((x) => String(x)) },
        { label: g.y_label, stroke: pal.text_dim, grid: { stroke: pal.grid }, ticks: { stroke: pal.axis },
          values: (u, vals) => vals.map((x) => U.minusSign(String(x))) },
      ],
      series: [
        {},
        { stroke: pal.text_dim, width: 2, dash: [6, 4], points: { show: false } },
        { stroke: pal.accent, width: 2, points: { show: false } },
        { stroke: pal.warn, width: 0, paths: () => null, points: { show: true, size: 9, fill: pal.warn, stroke: pal.warn } },
      ],
    };
    return new root.uPlot(opts, [xs, g.before, g.after, marks], el);
  }

  root.GSWindows = root.GSWindows || {};
  root.GSWindows["gs-cal"] = {
    template: CAL,
    computed: {
      a() { return this.$root; },
      v() { return this.$root.calView || {}; },
      w() { return this.$root.calWin || { page: PREPARE }; },
      prog() {
        const v = this.v;
        const live = (v.task_id && this.a.progress[v.task_id]) || null;
        if (live) return { done: live.done, total: live.total, text: live.text };
        return v.progress || { done: 0, total: 0, text: "" };
      },
      backOk() { return (this.w.page === MEASURE || this.w.page === CHECK) && !this.v.measuring; },
      nextLabel() {
        const p = this.w.page;
        return p === CHECK ? C("save") : p === SAVED ? C("again") : C("next");
      },
      nextReason() {
        const v = this.v;
        const p = this.w.page;
        if (p === PREPARE) return v.next_reason || null;
        if (p === MEASURE) return v.measuring ? C("measuring") : null;
        if (p === CHECK) return v.save_reason || (v.measuring ? C("measuring") : null);
        return null;
      },
      graphKey() {
        const g = this.v.graph;
        return this.w.page === CHECK && g ? JSON.stringify([g.before, g.after, g.marks, this.a.palette.accent]) : "";
      },
    },
    watch: {
      graphKey() { this.$nextTick(() => this.redraw()); },
    },
    mounted() { this.$nextTick(() => this.redraw()); },
    beforeUnmount() { if (this.plot) this.plot.destroy(); },
    methods: {
      C(k, kw) { return C(k, kw); },
      redraw() {
        if (this.plot) { this.plot.destroy(); this.plot = null; }
        const el = this.$refs.graph;
        if (!el || !this.v.graph || !root.uPlot) return;
        this.plot = drawGraph(el, this.v.graph, this.a.palette);
      },
    },
  };
})(window);
