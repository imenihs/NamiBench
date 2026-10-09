// マーカー表の上のボタンの並び・行の「操作」・ピークの一覧（設計G §3.4・§3.5・§3.8・P §9.7）。Qt の
// ui_qt/marker_table.py・tab_markers.py と同じ並び・同じ語・同じ動き（J §5）。app.js が読み込んで合わせる。
// - 探すもの「山／谷」はボタンの並びの頭の切り替え。ピークの一覧とマーカーのピーク探しが同じ選択を使う（U-09）。
//   選択はブラウザごとの覚え書き（設計O §1.1。localStorage の "pygigast.polarity"）。別ウィンドウ（ピークの一覧だけの
//   窓）にも効くよう、ほかの窓で変えたら storage の知らせで読み直す
// - 探すボタン・メニューの項目は場所を変えず、選んだほうで名前が変わる（表は texts の SEARCH_BUTTONS・DIP_MENU_ITEMS）
// - 行の「操作」の項目と、一覧の欄の名前・見つけた数の行・差の列の文字はサーバーが Qt と同じ関数で作る
//   （marker_rows.row_ops・peak_view.list_view・peak_view.result_view）。ここは映すだけで文を持たない
(function (root) {
  "use strict";

  const X = root.GSTexts;
  const W = (k, kw) => X.w(k, kw);
  const STORE = "pygigast.polarity";
  const POLARITIES = ["peak", "dip"];
  // 名前が選択で変わるボタン（Qt の peak_view.SEARCH_CODES と同じ並び）
  const SEARCH_CODES = ["marker_peak", "marker_peak_left", "marker_peak_right", "marker_peak_lower", "marker_add"];

  function loadPolarity() {
    let v = null;
    try { v = localStorage.getItem(STORE); } catch (e) { v = null; }
    return POLARITIES.includes(v) ? v : "peak";
  }

  root.GSAppMarkers = {
    data() {
      return {
        polarity: loadPolarity(),
        peakList: null,  // 結果（命令 peak_list の戻り値に、サーバーが文字 texts.peak_list を足したもの）
        peakTrace: 1,
        peakMargin: 10,
        topN: 3,  // 上位 N 個（Qt の top_n と同じ初期値と範囲 1〜8。欄は int-field）
      };
    },
    computed: {
      polarities() { return POLARITIES; },
      searchCodes() { return SEARCH_CODES; },
      // ピークの一覧の欄（基準・欄の名前とツールチップ・差の列・探せない理由）。サーバーの peak_view.list_view
      pkView() {
        const v = (this.ui.peaks || {})[this.polarity];
        return v || { base: "noise_floor", label: W("from_floor"), tip: W("floor_tip"), diff_header: W("col_height_db"), reason: null };
      },
    },
    methods: {
      // ------------------------------------------------------------ 山／谷（G §3.8）
      watchPolarityStore() {
        window.addEventListener("storage", (e) => { if (e.key === STORE) this.polarity = loadPolarity(); });
      },
      setPolarity(p) {
        this.polarity = POLARITIES.includes(p) ? p : "peak";
        try { localStorage.setItem(STORE, this.polarity); } catch (e) { /* 残せなくてもこの画面では効く */ }
      },
      togglePolarity() { this.setPolarity(this.polarity === "peak" ? "dip" : "peak"); },
      polarityTip(p) { return X.get("POLARITY_TIPS", p) + this.keyHint("marker_polarity"); },
      searchLabel(code) { return (X.dict("SEARCH_BUTTONS")[this.polarity] || {})[code] || ""; },
      searchTip(code) { return ((X.dict("SEARCH_TIPS")[this.polarity] || {})[code] || "") + this.keyHint(code); },
      // メニューの項目の名前（谷のときは探す項目を谷の語に: Qt の peak_view.menu_label と同じ）
      menuLabel(code) {
        if (this.polarity === "dip" && X.has("DIP_MENU_ITEMS", code)) return X.get("DIP_MENU_ITEMS", code);
        return X.get("MENU_ITEMS", code);
      },

      // ------------------------------------------------------------ 探す・置く（Qt の main_window._peak・_marker_add_peak）
      // 載せるトレースは見えている最初のトレース（Qt の _first_visible_trace と同じ）
      firstVisibleTrace() {
        for (const t of (this.st && this.st.traces) || []) if (t.visible && t.has_values) return t.number;
        return 1;
      },
      async markerPeak(direction) {
        const args = { direction, trace: this.firstVisibleTrace(), polarity: this.polarity };
        if (this.selectedMarker) args.id = this.selectedMarker;
        const r = await this.cmd("marker_peak", args);
        if (r && r.id) this.selectedMarker = r.id;
      },
      async markerAddPeak() {
        const r = await this.cmd("marker_add", { kind: "M", trace: this.firstVisibleTrace(), polarity: this.polarity });
        if (r && r.id) this.selectedMarker = r.id;
      },
      // 「全部消す」: 何本消えるかを言ってから聞く（Qt の _marker_clear と同じ: U-18）
      async markerClear() {
        const n = (this.markers.markers || []).length;
        if (n && !(await this.askConfirm(X.get("MENU_ITEMS", "marker_clear"), W("marker_clear_confirm", { n }), W("do_clear_all")))) return;
        this.cmd("marker_clear");
      },

      // ------------------------------------------------------------ 行の「操作」（Qt の行のドロップダウンと右クリック）
      // 項目はサーバーの marker_rows.row_ops（null は区切り線）。ボタンの下か、右クリックした位置に開く
      openRowOps(r, ev) {
        if (!r.ops || !r.ops.length) return;
        this.rowClick(r);
        let x = ev.clientX;
        let y = ev.clientY;
        let above = y;
        if (ev.type === "click") {
          const b = ev.currentTarget.getBoundingClientRect();
          x = b.left;
          y = b.bottom;
          above = b.top;  // 下に入らなければボタンの上に開く
        }
        const items = r.ops.map((it) => (it ? { label: it.text, why: it.why, run: () => this.cmd(it.cmd, it.args) } : { sep: true }));
        this.showCtx({ x, y, above, items });
      },

      // ------------------------------------------------------------ ピークの一覧（P §9.7）
      async getPeakList() {
        const r = await this.cmd("peak_list", { trace: Number(this.peakTrace), floor_margin_db: this.peakMargin, polarity: this.polarity }, { field: "peaks" });
        if (r) this.peakList = r;
      },
      peakText() { return (this.peakList && this.peakList.texts && this.peakList.texts.peak_list) || { found: "", diff_header: this.pkView.diff_header, rows: [] }; },
      // 「P に登録」は一覧の向き（山・谷）の P にする
      registerPeak(row) {
        const pl = this.peakList || {};
        this.cmd("marker_register_peak", { trace: pl.trace, freq_hz: row.freq_hz, polarity: pl.polarity || "peak" }, { field: "peaks" });
      },
      async topPeaks() {
        const n = parseInt(this.topN, 10);
        if (!(n > 0)) { this.note("peaks", X.get("ERRORS", "not_integer")); return; }
        await this.cmd("marker_top_peaks", { trace: Number(this.peakTrace), n, polarity: this.polarity }, { field: "peaks" });
      },
    },
  };
})(window);
