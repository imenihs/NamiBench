// 設定の画面（O §1.3）・プリセットの窓（O §1.2）・確かめる窓の形（Vue の部品）。index.html を 1000 行の内に
// 収めるために、窓の形をここへ分けた。状態と処理は親（app.js に合わせた app_settings.js ほか）が持ち、
// 部品は親（$root）を `a` として映すだけ。文言はここに書かない（texts の表を a.S・a.P・a.T・a.W で引く）。
// 並びと語は Qt の settings_dialog.py・settings_colors.py・settings_keys.py・presets_dialog.py と同じ。
(function (root) {
  "use strict";

  const parent = { computed: { a() { return this.$root; } } };

  // ---- 設定の画面。ページは SETTINGS_PAGES の順（接続・配色・通知・キー操作・保存・ブラウザから使う・rtl_tcp・RTL-SDR）
  const SETTINGS = `
<div class="modal-back settings-back" @click="a.closeSettings()">
<div class="modal setwin" @click.stop>
  <h3>{{ a.T('SETTINGS_LABELS', 'title') }}</h3>
  <p class="modal-note">{{ a.T('SETTINGS_LABELS', 'note') }}</p>
  <nav class="tabs setwin-tabs">
    <button v-for="pg in a.settingsPages" :key="pg[0]" :class="{on: page === pg[0]}" @click="a.setPage(pg[0])">{{ pg[1] }}</button>
  </nav>
  <div class="setwin-body" :data-page="page">
    <template v-if="page === 'connection'">
      <h4>{{ a.S('known_group') }}</h4>
      <table class="mtable" v-if="a.knownDevices.length"><tbody>
        <tr v-for="r in a.knownDevices" :key="r.serial">
          <td>{{ a.knownName(r) }}</td><td class="dim">{{ r.last ? a.S('known_last') : '' }}</td>
          <td class="ops"><button class="small" @click="a.forgetDevice(r)">{{ a.S('forget') }}</button></td>
        </tr>
      </tbody></table>
      <div class="dim" v-else>{{ a.S('known_none') }}</div>
      <div class="row"><button class="small" @click="a.openDevices()">{{ a.S('open_devices') }}</button></div>
      <h4>{{ a.S('manual_group') }}</h4>
      <div class="row">
        <span>{{ a.S('manual_port') }}</span>
        <input class="mid" v-steady="a.manualPort" :placeholder="a.S('manual_placeholder')" @change="a.setManualPort($event.target.value)">
        <button class="small" @click="a.connectManual()">{{ a.S('manual_connect') }}</button>
        <button class="small" :title="a.S('reset_tip')" @click="a.setManualPort('')">{{ a.S('reset') }}</button>
      </div>
      <div class="dim">{{ a.S('manual_note') }}</div>
      <div class="fnote" v-if="a.notes.settings_conn">{{ a.notes.settings_conn.text }}</div>
    </template>

    <template v-else-if="page === 'display'">
      <h4>{{ a.S('palette_group') }}</h4>
      <div>{{ pv.base_text }}</div>
      <div class="row">
        <button @click="a.resetPalette('light')">{{ a.S('palette_reset_light') }}</button>
        <button @click="a.resetPalette('dark')">{{ a.S('palette_reset_dark') }}</button>
      </div>
      <div class="dim">{{ a.S('palette_note') }}</div>
      <div class="pal-scroll"><table class="mtable pal"><tbody>
        <tr v-for="r in pv.rows" :key="r.name" :class="{changed: r.changed}">
          <td class="mark" :title="r.tip">{{ r.changed ? a.S('palette_changed_mark') : '' }}</td>
          <td class="grow">{{ r.label }}</td>
          <td><input type="color" :value="r.color" :title="a.S('palette_pick_title', {name: r.label})" @change="a.setColor(r.name, $event.target.value)"></td>
          <td><input class="hex" v-steady="r.color" :placeholder="a.S('palette_hex')" :title="a.S('palette_hex')" @change="a.typedColor(r, $event)"></td>
          <td><button class="small" :disabled="!r.changed" :title="a.S('palette_row_reset_tip')" @click="a.setColor(r.name, r.default)">{{ a.S('reset') }}</button></td>
        </tr>
      </tbody></table></div>
      <div class="fnote" v-if="a.notes.settings_palette">{{ a.notes.settings_palette.text }}</div>
      <div class="warn" v-if="pv.warnings.length"><div v-for="w in pv.warnings" :key="w">{{ w }}</div></div>
      <div class="dim" v-else>{{ pv.ok_text }}</div>
    </template>

    <template v-else-if="page === 'notify'">
      <h4>{{ a.V('notify_group') }}</h4>
      <label class="inline" :title="a.V('notify_ratio_tip')"><input type="checkbox" :checked="a.notifyRatio" @change="a.setNotify('ratio_display', $event.target.checked)"> {{ a.V('notify_ratio') }}</label>
    </template>

    <template v-else-if="page === 'keys'">
      <h4>{{ a.S('keys_group') }}</h4>
      <div class="dim">{{ a.S('keys_note') }}</div>
      <div class="dim">{{ a.S('keys_shift_note') }}</div>
      <div class="keys-scroll"><table class="mtable keys">
        <thead><tr><th>{{ a.S('keys_col_command') }}</th><th>{{ a.S('keys_col_key') }}</th><th>{{ a.S('keys_col_default') }}</th></tr></thead>
        <tbody>
          <tr v-for="r in a.keyRows" :key="r.code" :class="{sel: r.code === a.keySel}" :data-code="r.code"
              @click="a.keySel = r.code" @dblclick="a.keySel = r.code; a.startCapture()">
            <td>{{ r.name }}</td>
            <td :title="r.changed ? a.S('keys_changed_tip') : ''">{{ r.changed ? a.S('palette_changed_mark') + ' ' : '' }}{{ r.key }}</td>
            <td class="dim">{{ r.def }}</td>
          </tr>
        </tbody>
      </table></div>
      <div class="row">
        <button class="small" :class="{primary: a.keyCapture}" :disabled="!a.keySel" @click="a.startCapture()">{{ a.S('keys_assign') }}</button>
        <button class="small" :disabled="!a.keySel" @click="a.clearKey()">{{ a.S('keys_clear') }}</button>
        <button class="small" :disabled="!a.keySel" @click="a.resetKeyRow()">{{ a.S('keys_reset_row') }}</button>
        <button class="small" @click="a.resetAllKeys()">{{ a.S('keys_reset_all') }}</button>
      </div>
      <div class="keymsg" :class="a.keyMsg.warn ? 'warn' : ''" v-if="a.keyMsg">{{ a.keyMsg.text }}</div>
    </template>

    <template v-else-if="page === 'save'">
      <h4>{{ a.S('save_group') }}</h4>
      <div class="form">
        <label>{{ a.S('png_size') }}</label>
        <div class="row"><select :value="a.pngPrefs.size" @change="a.setPng('size', $event.target.value)">
            <option v-for="o in a.pngSizes" :key="o.value" :value="o.value">{{ o.text }}</option></select>
          <button class="small" :title="a.S('reset_tip')" @click="a.resetPng('size')">{{ a.S('reset') }}</button></div>
        <label>{{ a.S('png_palette') }}</label>
        <div class="row"><select :value="a.pngPalette" @change="a.setPng('palette', $event.target.value)">
            <option v-for="(l, c) in a.Td('PNG_PALETTES')" :key="c" :value="c">{{ l }}</option></select>
          <button class="small" :title="a.S('reset_tip')" @click="a.resetPng('palette')">{{ a.S('reset') }}</button></div>
      </div>
      <p class="dim">{{ a.S('png_palette_note') }}</p>
      <p class="dim">{{ a.S('save_web_note') }}</p>
    </template>

    <template v-else-if="page === 'web'">
      <p class="dim">{{ a.S('web_only_pc') }}</p>
      <div class="row"><button class="small" :disabled="!!a.connectReason()" :title="a.connectReason() || ''" @click="a.closeSettings(); a.showConnect()">{{ a.T('MENU_ITEMS', 'web_connect') }}</button></div>
    </template>

    <template v-else-if="page === 'rtltcp'">
      <h4>{{ a.T('SETTINGS_LABELS', 'rtltcp_group') }}</h4>
      <div class="form">
        <label></label><label class="inline"><input type="checkbox" :checked="a.rtltcpView.enabled" @change="a.setRtltcp({enabled: $event.target.checked})"> {{ a.T('SETTINGS_LABELS', 'rtltcp_enable') }}</label>
        <label>{{ a.T('SETTINGS_LABELS', 'rtltcp_port') }}</label><input class="num" v-steady="a.rtltcpView.port" @change="a.setRtltcpPort($event.target.value)">
        <label>{{ a.T('SETTINGS_LABELS', 'rtltcp_bind') }}</label>
        <select :value="a.rtltcpView.bind" @change="a.setRtltcp({bind: $event.target.value})"><option v-for="(l, c) in a.Td('RTLTCP_BINDS')" :key="c" :value="c">{{ l }}</option></select>
      </div>
      <div>{{ a.rtltcpView.text }}</div>
      <div class="dim" v-if="a.rtltcpView.dropped_text">{{ a.rtltcpView.dropped_text }}</div>
      <div class="warn" v-if="a.rtltcpView.error_text">{{ a.rtltcpView.error_text }}</div>
      <div class="fnote" v-if="a.notes.rtltcp">{{ a.notes.rtltcp.text }}</div>
      <div class="row"><button class="small" @click="a.setRtltcp({enabled: false, port: 1234, bind: 'local'})">{{ a.T('SETTINGS_LABELS', 'rtltcp_default') }}</button></div>
      <p class="dim">{{ a.T('SETTINGS_LABELS', 'rtltcp_note') }}</p>
    </template>

    <template v-else-if="page === 'rtlsdr'">
      <h4>{{ a.T('SETTINGS_LABELS', 'rtlsdr_group') }}</h4>
      <div :class="a.rtlView.dll_found ? '' : 'warn'">{{ a.rtlView.dll_found ? a.T('SETTINGS_LABELS', 'rtlsdr_found') : a.T('SETTINGS_LABELS', 'rtlsdr_missing') }}</div>
      <div class="dim">{{ a.rtlView.dll_text }}</div>
      <p class="dim">{{ a.W('dll_folder_web') }}</p>
    </template>
  </div>
  <div class="modal-btns"><button @click="a.closeSettings()">{{ a.T('SETTINGS_LABELS', 'close') }}</button></div>
</div>
</div>`;

  // ---- プリセットの窓（一覧と、呼び出す・名前・消す・上書き・新しく保存）
  const PRESETS = `
<div class="modal-back settings-back" @click="a.closePresets()">
<div class="modal presetwin" @click.stop>
  <h3>{{ a.P('title') }}</h3>
  <p class="modal-note">{{ a.P('note') }}</p>
  <div class="dim" v-if="w.ready && !w.rows.length">{{ a.P('none') }}</div>
  <div class="preset-scroll" v-if="w.rows.length"><table class="mtable"><tbody>
    <tr v-for="r in w.rows" :key="r.id" :class="{sel: r.id === w.sel}" @click="w.sel = r.id" @dblclick="w.sel = r.id; a.presetLoad()">
      <td>{{ r.name }}</td><td class="dim">{{ r.summary }}</td>
    </tr>
  </tbody></table></div>
  <div class="warn" v-if="w.broken">{{ a.P('broken', {n: w.broken}) }}</div>
  <div class="row">
    <button :disabled="!sel" :title="a.P('load_tip')" @click="a.presetLoad()">{{ a.P('load') }}</button>
    <button :disabled="!sel" @click="a.presetRename()">{{ a.P('rename') }}</button>
    <button :disabled="!sel" @click="a.presetDelete()">{{ a.P('delete') }}</button>
    <button :disabled="!sel" @click="a.presetOverwrite()">{{ a.P('overwrite') }}</button>
  </div>
  <div class="dim">{{ a.P('load_tip') }}</div>
  <div class="row"><button @click="a.presetSaveNew()">{{ a.P('save_new') }}</button></div>
  <div class="fnote" v-if="a.notes.presets">{{ a.notes.presets.text }}</div>
  <div class="modal-btns"><button @click="a.closePresets()">{{ a.T('SETTINGS_LABELS', 'close') }}</button></div>
</div>
</div>`;

  // ---- 確かめる窓（何が失われるか・何とぶつかるかを言ってから聞く: U-18・M-02）。知らせるだけの窓（info）は
  // ［閉じる］と「次から出さない」のチェック（J §4.10 の 3。Qt の QMessageBox と同じ形）
  const CONFIRM = `
<div class="modal-back confirm-back" @click="a.answerConfirm(false)">
<div class="modal confirm" @click.stop>
  <h3>{{ b.title }}</h3>
  <p>{{ b.text }}</p>
  <label v-if="b.check" class="inline"><input type="checkbox" v-model="b.checked"> {{ b.check }}</label>
  <div class="modal-btns" v-if="b.info">
    <button class="primary" @click="a.answerConfirm(false)">{{ a.T('SETTINGS_LABELS', 'close') }}</button>
  </div>
  <div class="modal-btns" v-else>
    <button class="primary" @click="a.answerConfirm(true)">{{ b.ok || a.W('confirm_ok') }}</button>
    <button @click="a.answerConfirm(false)">{{ a.W('cancel') }}</button>
  </div>
</div>
</div>`;

  root.GSWindows = {
    "gs-settings": {
      ...parent,
      template: SETTINGS,
      computed: {
        ...parent.computed,
        page() { return (this.a.settingsWin || {}).page || "connection"; },
        pv() { return this.a.paletteView; },
      },
    },
    "gs-presets": {
      ...parent,
      template: PRESETS,
      computed: {
        ...parent.computed,
        w() { return this.a.presetsWin || { rows: [], broken: 0, sel: null, ready: false }; },
        sel() { return this.a.presetSel(); },
      },
    },
    "gs-confirm": {
      ...parent,
      template: CONFIRM,
      computed: { ...parent.computed, b() { return this.a.confirmBox || {}; } },
    },
  };
})(window);
