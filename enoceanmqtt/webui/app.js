/* EnOceanMQTT Sensor Manager — frontend logic */
'use strict';

const state = {
  sensors: [],
  eep: [],
  learn: false,
  gateway: null
};

const $ = (id) => document.getElementById(id);
const escapeHtml = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
}[c]));

/* ---------------- toast ---------------- */
let toastTimer = null;
function toast(msg, type = 'info') {
  const el = $('toast');
  if (!el) return;
  el.textContent = msg;
  el.className = 'show ' + type;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.className = ''; }, 3200);
}

/* ---------------- api ---------------- */
async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: { 'Content-Type': 'application/json' },
    ...opts
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || ('Request failed (' + res.status + ')'));
  return data;
}

/* ---------------- load + render ---------------- */
async function loadStatus() {
  try {
    const data = await api('/api/status');
    state.sensors = data.sensors || [];
    state.eep = data.eep || [];
    state.learn = !!data.learn_mode;
    state.gateway = data.gateway || {};
    state.virtual_senders = data.virtual_senders || [];
    renderGateway();
    renderSensors();
    populateEepDatalist('f-eep-list', addMode);
    populateEepDatalist('e-eep-list', addMode);
    populateSenders(state.virtual_senders);
    applyTranslations();
    loadConfig();
  } catch (e) {
    toast(t('err_load_status') + e.message, 'error');
  }
}

function renderGateway() {
  const gw = state.gateway;
  const gwEl = $('gw-status');
  const mqttEl = $('mqtt-status');
  const baseEl = $('base-id');

  if (gw) {
    gwEl.className = 'pill ' + (gw.connected ? 'ok' : 'bad');
    gwEl.innerHTML = '<span class="dot"></span>' + t('gateway') + ' ' + (gw.connected ? t('connected') : t('offline'));
    mqttEl.className = 'pill ' + (gw.mqtt ? 'ok' : 'bad');
    mqttEl.innerHTML = '<span class="dot"></span>MQTT ' + (gw.mqtt ? t('connected') : t('offline'));
    baseEl.textContent = t('base_id') + ': ' + (gw.base_id || '—');
    // hover tooltips with configured settings
    const cfg = state.config || {};
    if (cfg.enocean_port) gwEl.setAttribute('data-tip', 'Port: ' + cfg.enocean_port + (cfg.log_packets !== undefined ? '\nLog packets: ' + cfg.log_packets : ''));
    if (cfg.mqtt_host) mqttEl.setAttribute('data-tip', 'Host: ' + cfg.mqtt_host + (cfg.mqtt_port ? ':' + cfg.mqtt_port : ''));
  }
}

function fmtLastSeen(ts) {
  if (!ts) return '—';
  const then = new Date(ts);
  if (isNaN(then)) return ts;
  const now = new Date();
  const diff = (now - then) / 1000;
  if (diff < 60) return 'just now';
  if (diff < 3600) return Math.floor(diff / 60) + 'm ago';
  if (diff < 86400) return Math.floor(diff / 3600) + 'h ago';
  return then.toLocaleString();
}

function fmtLatest(s) {
  const l = s.latest;
  if (!l || !l.values) return '—';
  const meta = l.meta || {};
  // show value + unit only; each value is a clickable graph link, e.g.
  // "170 lx · 21.44 °C · Button pressed"
  const keys = Object.keys(l.values).filter((k) => !k.startsWith('_'));
  if (!keys.length) return '—';
  return keys.map((k) => {
    let v = l.values[k];
    const m = meta[k] || {};
    let txt;
    if (m.text && m.text !== String(v)) txt = m.text;
    else if (typeof v === 'number') txt = (Math.round(v * 100) / 100).toString();
    else txt = String(v);
    const tip = m.description ?
      (m.description + (m.unit ? ' (' + m.unit + ')' : '')) : (k + (m.unit ? ' (' + m.unit + ')' : ''));
    return '<a href="#" class="val-link" data-valgraph="' + escapeHtml(s.name + '|' + k) + '" data-tip="' + escapeHtml(tip) + '">' +
      txt + (m.unit ? ' ' + m.unit : '') + '</a>';
  }).join(' · ');
}

/* ---------------- value graph ---------------- */
async function showGraph(name, field) {
  $('graph-title').textContent = 'Value history · ' + name;
  $('graph-body').innerHTML = 'Loading…';
  $('graph-overlay').hidden = false;
  try {
    const res = await api('/api/history/' + encodeURIComponent(name));
    if (!res.ok) throw new Error(res.error || 'no history');
    const hist = res.history || [];
    if (!hist.length) { $('graph-body').innerHTML = 'No values recorded yet for this device.'; return; }
    // pick the requested field (e.g. _RSSI_) or the first real (non-meta) value key
    let keys = Object.keys(hist[hist.length - 1].values || {}).filter((k) => !k.startsWith('_'));
    if (field) {
      const hasField = hist.some((h) => h.values && h.values[field] !== undefined);
      if (hasField) keys = [field];
    }
    if (!keys.length) { $('graph-body').innerHTML = 'No numeric values to plot.'; return; }
    const key = keys[0]; // (graph plots the requested/first value; all are clickable)
    const pts = hist.filter((h) => h.values && h.values[key] !== undefined)
      .map((h) => ({ t: new Date(h.ts).getTime(), v: Number(h.values[key]) }));
    const isBinary = pts.every((p) => p.v === 0 || p.v === 1);
    $('graph-body').innerHTML = '<div class="graph-wrap"><svg id="mini-graph" viewBox="0 0 600 180" preserveAspectRatio="none"><g id="mini-hover"></g></svg>' +
      '<div class="graph-hoverctl" id="mini-hoverctl" hidden></div></div>' +
      '<div class="graph-legend"><b>' + escapeHtml(key) + '</b> &middot; ' + pts.length + ' samples &middot; ' +
      (isBinary ? 'state' : 'value') + '</div>';
    if (isBinary) renderBinaryGraph('mini-graph', pts);
    else renderMiniGraph('mini-graph', pts, key);
  } catch (err) {
    $('graph-body').innerHTML = 'Failed to load history: ' + escapeHtml(err.message);
  }
}
function renderMiniGraph(svgId, pts, key) {
  const svg = document.getElementById(svgId);
  if (!svg || pts.length < 2) return;
  const W = 600, H = 180, PAD = 8;
  const vals = pts.map((p) => p.v);
  const min = Math.min.apply(null, vals), max = Math.max.apply(null, vals);
  const span = (max - min) || 1;
  const t0 = pts[0].t, t1 = pts[pts.length - 1].t || (t0 + 1);
  const X = (t) => PAD + (t - t0) / (t1 - t0) * (W - 2 * PAD);
  const Y = (v) => H - PAD - (v - min) / span * (H - 2 * PAD);
  svg.innerHTML = '<polyline fill="none" stroke="var(--primary)" stroke-width="2" points="' +
    pts.map((p) => X(p.t).toFixed(1) + ',' + Y(p.v).toFixed(1)).join(' ') + '"/>' +
    '<text x="' + PAD + '" y="' + (H - 2) + '" fill="var(--text-muted)" font-size="11">' + escapeHtml(String(min)) + '</text>' +
    '<text x="' + (W - PAD - 40) + '" y="' + (H - 2) + '" fill="var(--text-muted)" font-size="11">' + escapeHtml(String(max)) + '</text>';
  // hover: nearest sample -> dot + context box (below the graph)
  const ctl = document.getElementById('mini-hoverctl');
  const g = document.getElementById('mini-hover');
  svg.addEventListener('mousemove', (e) => {
    const r = svg.getBoundingClientRect();
    const px = (e.clientX - r.left) / r.width * W;
    let best = pts[0], bestD = Infinity;
    for (const p of pts) { const d = Math.abs(X(p.t) - px); if (d < bestD) { bestD = d; best = p; } }
    const bx = X(best.t), by = Y(best.v);
    g.innerHTML = '<circle cx="' + bx.toFixed(1) + '" cy="' + by.toFixed(1) + '" r="4" fill="var(--primary)" stroke="#fff" stroke-width="1.5"/>' +
      '<line x1="' + bx.toFixed(1) + '" y1="' + (PAD) + '" x2="' + bx.toFixed(1) + '" y2="' + (H - PAD) + '" stroke="var(--border-strong)" stroke-width="1" stroke-dasharray="3,3"/>';
    if (ctl) {
      const d = new Date(best.t);
      ctl.hidden = false;
      ctl.innerHTML = '<b>' + escapeHtml(key) + '</b>: ' + escapeHtml(String(Math.round(best.v * 100) / 100)) +
        (ctl.dataset.unit ? ' ' + ctl.dataset.unit : '') + ' &middot; ' + escapeHtml(d.toLocaleTimeString());
    }
  });
  svg.addEventListener('mouseleave', () => { g.innerHTML = ''; if (ctl) ctl.hidden = true; });
}
function renderBinaryGraph(svgId, pts) {
  const svg = document.getElementById(svgId);
  if (!svg || !pts.length) return;
  const W = 600, H = 180, PAD = 8;
  const t0 = pts[0].t, t1 = pts[pts.length - 1].t || (t0 + 1);
  const X = (t) => PAD + (t - t0) / (t1 - t0) * (W - 2 * PAD);
  const Y = (v) => H - PAD - v * (H - 2 * PAD); // 0 bottom, 1 top
  // step path: hold each state until the next sample (HA-style)
  let d = 'M' + X(pts[0].t).toFixed(1) + ',' + Y(pts[0].v).toFixed(1);
  for (let i = 0; i < pts.length; i++) {
    const x = X(pts[i].t).toFixed(1), y = Y(pts[i].v).toFixed(1);
    if (i > 0) d += ' L' + X(pts[i - 1].t).toFixed(1) + ',' + Y(pts[i - 1].v).toFixed(1);
    d += ' L' + x + ',' + y;
  }
  // extend to the right edge at the last value
  d += ' L' + (W - PAD).toFixed(1) + ',' + Y(pts[pts.length - 1].v).toFixed(1);
  svg.innerHTML =
    '<polyline fill="none" stroke="var(--success)" stroke-width="2" points="' +
    pts.map((p, i) => {
      const x = X(p.t).toFixed(1), y = Y(p.v).toFixed(1);
      return (i ? ' ' : '') + x + ',' + y;
    }).join(' ') + '"/>' +
    '<text x="' + PAD + '" y="' + (H - 2) + '" fill="var(--text-muted)" font-size="10">0</text>' +
    '<text x="' + PAD + '" y="' + (PAD + 8) + '" fill="var(--text-muted)" font-size="10">1</text>';
}
$('graph-close')?.addEventListener('click', () => { $('graph-overlay').hidden = true; });
$('graph-overlay')?.addEventListener('click', (e) => { if (e.target === $('graph-overlay')) $('graph-overlay').hidden = true; });

/* ---------------- configuration editor ---------------- */
// fields we never want to edit via the web UI
const CONFIG_HIDDEN = new Set(['config', 'mqtt_pwd', 'mqtt_client_id']);
// human labels for known settings
const CONFIG_LABELS = {
  enocean_port: 'EnOcean port', mqtt_host: 'MQTT host', mqtt_port: 'MQTT port',
  mqtt_prefix: 'MQTT prefix', mqtt_keepalive: 'MQTT keepalive', mqtt_user: 'MQTT user',
  mqtt_ssl: 'MQTT SSL', mqtt_debug: 'MQTT debug', log_packets: 'Log packets',
  overlay: 'Overlay', webui_port: 'Web UI port', webui_disable: 'Disable web UI',
  db_file: 'Device DB file', webui_sensor_store: 'Sensor store file',
};

async function loadConfig() {
  try {
    const data = await api('/api/config');
    state.config = data || {};
    populateConfig(state.config);
  } catch (e) {
    toast(t('err_load_config') + e.message, 'error');
  }
}

function configViewHtml(conf, keys) {
  return '<table class="config-table">' + keys.map((k) => {
    const label = CONFIG_LABELS[k] || k;
    const val = conf[k] === undefined || conf[k] === null ? '' : String(conf[k]);
    return '<tr><td>' + escapeHtml(label) + '</td><td class="mono">' + escapeHtml(val) + '</td></tr>';
  }).join('') + '</table>';
}

function populateConfig(conf) {
  const view = $('config-view');
  if (!view) return;
  const keys = Object.keys(conf || {}).filter((k) => !CONFIG_HIDDEN.has(k));
  if (!keys.length) { view.innerHTML = '<span class="card-sub">Configuration not available.</span>'; return; }
  view.innerHTML = configViewHtml(conf, keys);
}

// Edit popup: fill the grid with inputs
// boolean-ish config keys are rendered as checkboxes (values 1/0/true/false)
const CONFIG_BOOL = new Set([
  'log_packets', 'mqtt_debug', 'mqtt_ssl', 'mqtt_ssl_insecure', 'webui_disable',
  'publish_json', 'persistent', 'log_learn', 'answer', 'ignore',
  'bidirectional', 'smartack', 'learn', 'answer', 'debug', 'encrypt', 'publish_rssi',
  'publish_date',
]);
function isBoolConf(k, v) {
  if (CONFIG_BOOL.has(k)) return true;
  const sv = String(v == null ? '' : v).trim().toLowerCase();
  return ['0', '1', 'true', 'false', 'yes', 'no', 'on', 'off'].includes(sv) && (v === '' || /^(0|1|true|false|yes|no|on|off)$/i.test(String(v)));
}

function openConfigEdit() {
  const grid = $('config-grid');
  if (!grid || !state.config) return;
  const conf = state.config;
  const keys = Object.keys(conf).filter((k) => !CONFIG_HIDDEN.has(k));
  grid.classList.add('config-two-col');
  grid.innerHTML = keys.map((k) => {
    const label = CONFIG_LABELS[k] || k;
    const raw = conf[k];
    if (isBoolConf(k, raw)) {
      const checked = ['1', 'true', 'yes', 'on'].includes(String(raw == null ? '' : raw).trim().toLowerCase());
      return '<div class="field config-bool"><label for="cfg-' + escapeHtml(k) + '">' + escapeHtml(label) + '</label>' +
        '<label class="switch row"><input type="checkbox" id="cfg-' + escapeHtml(k) + '" data-cfgkey="' + escapeHtml(k) + '"' + (checked ? ' checked' : '') + '><span class="slider"></span></label>' +
        '<input type="hidden" data-cfgkey="' + escapeHtml(k) + '" data-boolhidden="' + escapeHtml(k) + '" value="' + (checked ? '1' : '0') + '"></div>';
    }
    return '<div class="field"><label for="cfg-' + escapeHtml(k) + '">' + escapeHtml(label) + '</label>' +
      '<input type="text" id="cfg-' + escapeHtml(k) + '" data-cfgkey="' + escapeHtml(k) + '" value="' + escapeHtml(String(raw)) + '"></div>';
  }).join('');
  // bind checkbox change -> set hidden value (so configPayloadFromGrid reads it)
  grid.querySelectorAll('input[type=checkbox][data-cfgkey]').forEach((cb) => {
    cb.addEventListener('change', () => {
      const hidden = grid.querySelector('input[data-boolhidden="' + cb.getAttribute('data-cfgkey') + '"]');
      if (hidden) hidden.value = cb.checked ? '1' : '0';
    });
  });
  $('configedit-overlay').hidden = false;
}
$('btn-config-edit')?.addEventListener('click', openConfigEdit);
$('configedit-cancel')?.addEventListener('click', () => { $('configedit-overlay').hidden = true; });

function configPayloadFromGrid() {
  const payload = {};
  document.querySelectorAll('#config-grid [data-cfgkey]').forEach((inp) => {
    if (inp.type === 'checkbox') return; // handled by the hidden sibling
    payload[inp.getAttribute('data-cfgkey')] = inp.value;
  });
  return payload;
}
async function saveConfigPayload(payload, andRestart) {
  try {
    const res = await api('/api/config', { method: 'POST', body: JSON.stringify(payload) });
    if (!res.ok) throw new Error(res.error || 'save failed');
    toast(t('config_saved'), 'success');
    $('configedit-overlay').hidden = true;
    state.config = Object.assign({}, state.config, payload);
    populateConfig(state.config);
    if (andRestart) {
      // tell the backend to restart (added config flag)
      try {
        await fetch('/api/restart', { method: 'POST' });
      } catch (e) { /* ignore */ }
      toast('Restarting…', 'info');
    }
  } catch (err) {
    toast(t('err_save_config') + err.message, 'error');
  }
}
$('configedit-save')?.addEventListener('click', () => saveConfigPayload(configPayloadFromGrid(), false));
$('configedit-save-restart')?.addEventListener('click', () => saveConfigPayload(configPayloadFromGrid(), true));

function fmtLastSeenFull(ts) {
  if (!ts) return '—';
  const then = new Date(ts);
  if (isNaN(then)) return ts;
  return then.toLocaleString(undefined, { dateStyle: 'full', timeStyle: 'medium' });
}
let activeDeviceCat = 'all';

function deviceCategory(s) {
  const c = s.category;
  if (c === 'actor' || c === 'bidirectional') return c;
  return 'sensor';
}

function renderSensors() {
  const tbody = $('sensor-body');
  const cats = activeDeviceCat;
  const filtered = state.sensors.filter((s) => cats === 'all' || deviceCategory(s) === cats);
  const count = $('device-count');
  if (count) count.textContent = state.sensors.length + ' ' + t('device_count');

  if (!filtered.length) {
    const msg = state.sensors.length ? (cats === 'actor' ? t('no_actors') : t('no_sensors')) : t('no_devices');
    tbody.innerHTML = '<tr class="empty-row"><td colspan="8">' + msg + '</td></tr>';
    return;
  }

  tbody.innerHTML = filtered.map((s) => {
    const eep = s.eep ? escapeHtml(s.eep) : '—';
    const eepName = s.eep_name ? escapeHtml(s.eep_name) : '';
    // status: show the latest RSSI with color coding (clickable for a graph)
    const rssi = (s.latest && s.latest.values && s.latest.values._RSSI_) || null;
    let rssiHtml = '—';
    if (rssi !== null) {
      const rssiCls = rssi <= -80 ? 'rssi-bad' : (rssi <= -60 ? 'rssi-warn' : 'rssi-good');
      rssiHtml = '<a href="#" class="rssi-val ' + rssiCls + '" data-rssi="' + escapeHtml(s.name) + '" title="RSSI history">' + rssi + ' dBm</a>';
    }
    const isActor = deviceCategory(s) === 'actor';
    // Address column: for sensors show the device address; for actors show the
    // (virtual) sender id; for bidirectional devices show both.
    // fmtAddr defined globally (colon format)
    let addrHtml;
    if (isActor) {
      addrHtml = fmtAddr(s.sender);
    } else if (s.bidirectional) {
      addrHtml = fmtAddr(s.address) + '<div style="color:var(--text-muted);font-size:11px">' + t('send_label') + ' ' + fmtAddr(s.sender) + '</div>';
    } else {
      addrHtml = fmtAddr(s.address);
    }
    const source = s.source === 'dynamic' ? ' <span class="pill" style="font-size:10px;padding:1px 6px">web</span>' : '';
    const name = escapeHtml(s.name);
    const catCls = deviceCategory(s);
    const catTxt = catCls === 'actor' ? t('actor') : (catCls === 'bidirectional' ? t('bidirectional') : t('sensor'));
    const badges = [];
    if (s.smartack) badges.push('<span class="badge smartack" title="smartACK — requires fast acknowledgement">smartACK</span>');
    return `<tr data-name="${escapeHtml(s.name)}" data-cat="${catCls}">
      <td>${name}${source}</td>
      <td class="mono">${addrHtml}</td>
      <td><span class="mono">${eep}</span><a href="#" class="eep-link" data-eepviewer="${escapeHtml(eep)}" title="${t('eep_viewer')}">ⓘ</a>${eepName ? '<div style="color:var(--text-muted);font-size:11px">' + escapeHtml(translateEepName(s.eep_name)) + '</div>' : ''}</td>
      <td><span class="cat-badge ${catCls}">${catTxt}</span>${badges.join('')}</td>
      <td class="mono">${rssiHtml}</td>
      <td class="mono" data-tip="${escapeHtml(fmtLastSeenFull(s.last_seen))}">${escapeHtml(fmtLastSeen(s.last_seen))}</td>
      <td class="mono latest-val">${fmtLatest(s)}</td>
      <td><div class="row-actions">
        ${isActor ? '<button class="icon-btn teachin-btn" title="Send teach-in telegram to this actor" data-teachin="${escapeHtml(s.name)}">⤓</button>' : ''}
        <button class="icon-btn" title="${t('edit_device')}" data-edit="${escapeHtml(s.name)}">✎</button>
        <button class="icon-btn" title="${t('remove_device')}" data-del="${escapeHtml(s.name)}">✕</button>
      </div></td>
    </tr>`;
  }).join('');

  // bind delete buttons
  tbody.querySelectorAll('[data-del]').forEach((btn) => {
    btn.addEventListener('click', () => confirmRemove(btn.getAttribute('data-del')));
  });
  // bind teach-in buttons
  tbody.querySelectorAll('[data-teachin]').forEach((btn) => {
    btn.addEventListener('click', () => sendTeachIn(btn.getAttribute('data-teachin')));
  });
  // bind edit buttons
  tbody.querySelectorAll('[data-edit]').forEach((btn) => {
    btn.addEventListener('click', () => openEdit(btn.getAttribute('data-edit')));
  });
  // bind value graph links (latest values)
  tbody.querySelectorAll('[data-graph]').forEach((a) => {
    a.addEventListener('click', (e) => {
      e.preventDefault();
      showGraph(a.getAttribute('data-graph'), null);
    });
  });
  // bind RSSI links
  tbody.querySelectorAll('[data-rssi]').forEach((a) => {
    a.addEventListener('click', (e) => {
      e.preventDefault();
      showGraph(a.getAttribute('data-rssi'), '_RSSI_');
    });
  });
  // bind per-value graph links (name|key)
  tbody.querySelectorAll('[data-valgraph]').forEach((a) => {
    a.addEventListener('click', (e) => {
      e.preventDefault();
      const [name, key] = a.getAttribute('data-valgraph').split('|');
      showGraph(name, key);
    });
  });
  // bind EEP viewer icons
  tbody.querySelectorAll('[data-eepviewer]').forEach((a) => {
    a.addEventListener('click', (e) => {
      e.preventDefault();
      window.open(eepViewerUrl(a.getAttribute('data-eepviewer')), '_blank');
    });
  });
}

/* ---------------- edit device ---------------- */
let editingDevice = null;

function openEdit(name) {
  const s = state.sensors.find((x) => x.name === name);
  if (!s) return;
  editingDevice = name;
  const cat = deviceCategory(s);
  try {
  $('e-name').value = s.name;
  $('e-address').value = (s.address !== undefined && s.address !== null && s.address !== 0xFFFFFFFF)
    ? fmtAddrInput(s.address) : '';
  $('e-direction').value = s.direction || '';
  $('e-answer').value = s.answer || '';
  $('e-default_data').value = (s.default_data !== undefined && s.default_data !== null)
    ? fmtAddrInput(s.default_data) : '';
  $('e-eep-search').value = s.eep || '';
  // fields per device type: sensor=addr, actor=sender, bidirectional=both+settings
  const showAddr = cat !== 'actor';
  const showSender = cat !== 'sensor';
  $('e-addr-field').hidden = !showAddr;
  $('e-sender-field').hidden = !showSender;
  $('e-dir-field').hidden = cat !== 'bidirectional';
  $('e-answer-field').hidden = cat !== 'bidirectional';
  $('e-default-field').hidden = cat !== 'bidirectional';
  populateSenders($('e-sender'), state.virtual_senders, s.sender);
  populateEepDatalist('e-eep-list', addMode);
  updateEepInfo('e-eep-search', 'e-eep-info');
  } catch (e) {
    console.error('openEdit', name, e);
  }
  $('edit-overlay').hidden = false;
}

$('edit-cancel')?.addEventListener('click', () => { $('edit-overlay').hidden = true; editingDevice = null; });
$('edit-overlay')?.addEventListener('click', (e) => {
  if (e.target === $('edit-overlay')) { $('edit-overlay').hidden = true; editingDevice = null; }
});

$('edit-ok')?.addEventListener('click', async () => {
  if (!editingDevice) return;
  const name = $('e-name').value.trim();
  const eep = $('e-eep-search').value.trim();
  const body = { name: name, eep: eep };
  const addrVal = $('e-address').value.trim();
  const address = addrVal ? parseAddress(addrVal) : null;
  if (addrVal && address === null) return toast(t('err_bad_address'), 'error');
  if (address !== null) body.address = address;
  // actor/bidirectional: sender + settings
  const cat = deviceCategory(state.sensors.find((x) => x.name === editingDevice) || {});
  if (cat !== 'sensor') {
    const sv = parseInt($('e-sender').value, 0);
    if (!isNaN(sv)) body.sender = sv;
  }
  if (cat === 'bidirectional') {
    if ($('e-direction').value.trim() !== '') body.direction = parseInt($('e-direction').value, 10);
    if ($('e-answer').value.trim() !== '') body.answer = parseInt($('e-answer').value, 10);
    const dd = $('e-default_data').value.trim();
    if (dd) body.default_data = parseInt(dd, 0);
  }
  try {
    const res = await api('/api/sensors/' + encodeURIComponent(editingDevice), {
      method: 'PUT',
      body: JSON.stringify(body)
    });
    if (!res.ok) throw new Error(res.error || 'update failed');
    toast(t('device_updated'), 'success');
    $('edit-overlay').hidden = true;
    editingDevice = null;
    await loadStatus();
  } catch (err) {
    toast(t('err_update_device') + err.message, 'error');
  }
});

async function sendTeachIn(name) {
  try {
    const res = await api('/api/teachin', { method: 'POST', body: JSON.stringify({ name: name }) });
    toast(res.message || t('teachin_sent'), res.ok ? 'success' : 'error');
  } catch (err) {
    toast(t('err_send_teachin') + err.message, 'error');
  }
}

function setDeviceCat(cat) {
  activeDeviceCat = cat;
  document.querySelectorAll('.device-tab[data-cat]').forEach((t) => {
    t.classList.toggle('active', t.getAttribute('data-cat') === cat);
  });
  renderSensors();
}
document.querySelectorAll('.device-tab[data-cat]').forEach((t) => {
  t.addEventListener('click', () => setDeviceCat(t.getAttribute('data-cat')));
});

/* ---------------- teach-in ---------------- */
async function setLearn(on) {
  try {
    await api('/api/learn', { method: 'POST', body: JSON.stringify({ enabled: on }) });
    state.learn = on;
    toast(on ? t('teachin_enabled') : t('teachin_disabled'), 'success');
  } catch (e) {
    toast(t('err_teachin') + e.message, 'error');
  }
}

/* ---------------- add sensor ---------------- */
let eepFilter = '';

// translated EEP description keywords (fallback: keep English)
const EEP_NAME_TR = {
  en: {},
  de: { 'Battery Powered Actuator': 'Batteriebetriebener Aktor', 'Temperature Sensor': 'Temperatursensor', 'Push Button': 'Taster', 'Rocker Switch': 'Wippschalter', 'Smoke Detector': 'Rauchmelder', 'Window Handle': 'Fenster-Griff', 'Contact': 'Kontakt', 'Switch': 'Schalter', 'Dimmer': 'Dimmer', 'Blind': 'Jalousie', 'Valve': 'Ventil', 'Occupancy': 'Präsenz', 'Illumination': 'Beleuchtung' },
  fr: { 'Battery Powered Actuator': 'Actionneur sur batterie', 'Temperature Sensor': 'Capteur de température', 'Push Button': 'Bouton-poussoir', 'Rocker Switch': 'Interrupteur à bascule', 'Smoke Detector': 'Détecteur de fumée', 'Window Handle': 'Poignée de fenêtre', 'Contact': 'Contact', 'Switch': 'Interrupteur', 'Dimmer': 'Variateur', 'Blind': 'Volet', 'Valve': 'Vanne', 'Occupancy': 'Présence', 'Illumination': 'Éclairage' },
  it: { 'Battery Powered Actuator': 'Attuatore a batteria', 'Temperature Sensor': 'Sensore di temperatura', 'Push Button': 'Pulsante', 'Rocker Switch': 'Interruttore a bilanciere', 'Smoke Detector': 'Rivelatore di fumo', 'Window Handle': 'Maniglia finestra', 'Contact': 'Contatto', 'Switch': 'Interruttore', 'Dimmer': 'Dimmer', 'Blind': 'Tapparella', 'Valve': 'Valvola', 'Occupancy': 'Presenza', 'Illumination': 'Illuminazione' },
};
function translateEepName(name) {
  const map = EEP_NAME_TR[currentLang] || {};
  if (!map || !name) return name;
  let out = name;
  for (const k in map) {
    if (out.includes(k)) out = out.replace(new RegExp(k, 'i'), map[k]);
  }
  return out;
}

let _eepOpts = [];   // full filtered list per mode, rebuilt on mode change
function populateEepDatalist(listId, mode) {
  const ul = $(listId);
  if (!ul) return;
  const catFilter = (p) => {
    if (!mode) return true;
    if (mode === 'sensor') return p.category === 'sensor';
    if (mode === 'actor') return p.category === 'actor';
    if (mode === 'bidirectional') return p.category === 'bidirectional';
    return true;
  };
  _eepOpts = (state.eep || []).filter(catFilter);
  renderEepList(ul, '');
}
function renderEepList(ul, q) {
  const query = (q || '').toLowerCase();
  const items = query
    ? _eepOpts.filter((p) => p.eep.toLowerCase().includes(query) || p.name.toLowerCase().includes(query))
    : _eepOpts;
  ul.innerHTML = items.slice(0, 300).map((p) =>
    '<li data-eep="' + escapeHtml(p.eep) + '" data-name="' + escapeHtml(p.name) + '">' +
      '<span class="eep-code">' + escapeHtml(p.eep) + '</span>' +
      '<span class="eep-name">' + escapeHtml(translateEepName(p.name)) + '</span></li>').join('');
  return items.length;
}
function initEepCombo(searchId, listId, dropId, infoId) {
  const input = $(searchId);
  const list = $(listId);
  const drop = $(dropId);
  if (!input || !list || !drop) return;
  input.addEventListener('focus', () => {
    if (!_eepOpts.length && (state.eep || []).length) populateEepDatalist(listId, addMode);
    renderEepList(list, input.value);
    drop.hidden = false;
  });
  input.addEventListener('input', () => { renderEepList(list, input.value); drop.hidden = false; updateEepInfo(searchId, infoId); });
  input.addEventListener('blur', () => setTimeout(() => { drop.hidden = true; }, 150));
  input.addEventListener('keydown', (e) => {
    if (e.key === 'ArrowDown') { e.preventDefault(); const c = list.querySelector('li'); if (c) c.focus(); }
    else if (e.key === 'Escape') drop.hidden = true;
  });
  list.addEventListener('click', (e) => {
    const li = e.target.closest('li[data-eep]');
    if (!li) return;
    input.value = li.getAttribute('data-eep');
    drop.hidden = true;
    updateEepInfo(searchId, infoId);
  });
}

function resolveEep(value) {
  const v = (value || '').trim();
  if (!v) return null;
  const hit = (state.eep || []).find((p) =>
    p.eep.toLowerCase() === v.toLowerCase() ||
    p.eep.replace(/[-:]/g, '').toLowerCase() === v.replace(/[-:]/g, '').toLowerCase() ||
    p.name.toLowerCase() === v.toLowerCase());
  return hit ? hit.eep : (v.toUpperCase().includes('-') ? v.toUpperCase() : null);
}

function eepViewerUrl(eep) {
  // EnOcean Alliance EEPViewer PDF schema:
  // https://tools.enocean-alliance.org/EEPViewer/profiles/{RORG}/{FUNC}/{TYPE}/{RORG}-{FUNC}-{TYPE}.pdf
  if (!eep) return 'https://tools.enocean-alliance.org/EEPViewer/';
  const parts = eep.split('-');
  if (parts.length === 3) {
    return 'https://tools.enocean-alliance.org/EEPViewer/profiles/' +
      parts[0] + '/' + parts[1] + '/' + parts[2] + '/' +
      parts[0] + '-' + parts[1] + '-' + parts[2] + '.pdf';
  }
  return 'https://tools.enocean-alliance.org/EEPViewer/';
}
function updateEepInfo(inputId, infoId) {
  const input = $(inputId);
  const info = $(infoId);
  if (!input || !info) return;
  const eep = resolveEep(input.value);
  if (eep) {
    info.hidden = false;
    info.onclick = () => window.open(eepViewerUrl(eep), '_blank');
  } else {
    info.hidden = true;
  }
}
function parseAddress(val) {
  // accept 'FF:80:00:00', '0xFF800000', 'FF800000' or any mix
  let s = String(val || '').trim();
  if (!s) return null;
  s = s.replace(/0x/gi, '').replace(/[^0-9a-fA-F]/g, '');
  if (s.length < 1 || s.length > 8 || !/^[0-9a-fA-F]+$/.test(s)) return null;
  return parseInt(s, 16);
}
function fmtAddr(v) {
  if (v === undefined || v === null) return '—';
  const n = Number(v);
  if (isNaN(n)) return String(v);
  const b = [(n >> 24) & 0xff, (n >> 16) & 0xff, (n >> 8) & 0xff, n & 0xff];
  return b.map((x) => x.toString(16).toUpperCase().padStart(2, '0')).join(':');
}
function fmtAddrInput(v) {
  // input-friendly colon format (lowercase ok)
  if (v === undefined || v === null) return '';
  const n = Number(v);
  if (isNaN(n)) return String(v);
  const b = [(n >> 24) & 0xff, (n >> 16) & 0xff, (n >> 8) & 0xff, n & 0xff];
  return b.map((x) => x.toString(16).toUpperCase().padStart(2, '0')).join(':');
}

let addMode = 'sensor';

function populateSenders(senders, selOrNull, selected) {
  const sel = selOrNull || $('f-sender');
  if (!sel) return;
  const used = new Set(state.sensors.filter((s) => s.sender).map((s) => s.sender));
  const opts = (senders || []).map((v) => {
    const hex = fmtAddr(v);
    const isUsed = used.has(v);
    const isSel = (selected !== undefined && selected !== null && Number(selected) === v);
    return '<option value="' + hex + '"' + (isUsed && !isSel ? ' disabled' : '') + (isSel ? ' selected' : '') + '>' + hex + (isUsed && !isSel ? ' (used)' : '') + '</option>';
  });
  sel.innerHTML = opts.length ? opts.join('') : '<option value="">(no base ID yet)</option>';
}

/* ---------------- Add-device modals ---------------- */
function firstFreeSender() {
  const used = new Set(state.sensors.filter((s) => s.sender).map((s) => s.sender));
  for (const v of (state.virtual_senders || [])) {
    if (!used.has(v)) return v;
  }
  return (state.virtual_senders || [])[0] || null;
}
function openAddModal(kind) {
  if (kind === 'sensor') {
    populateSenders($('f-sender'), state.virtual_senders);
    populateEepDatalist('f-eep-list', 'sensor');
    $('addsensor-overlay').hidden = false;
  } else if (kind === 'actor') {
    populateSenders($('aa-sender'), state.virtual_senders);
    const fsA = firstFreeSender();
    if (fsA && $('aa-sender')) $('aa-sender').value = fmtAddr(fsA);
    populateEepDatalist('aa-eep-list', 'actor');
    $('addactor-overlay').hidden = false;
  } else if (kind === 'bidirectional') {
    populateSenders($('ab-sender'), state.virtual_senders);
    const fsB = firstFreeSender();
    if (fsB && $('ab-sender')) $('ab-sender').value = fmtAddr(fsB);
    populateEepDatalist('ab-eep-list', 'bidirectional');
    $('addbidir-overlay').hidden = false;
  }
}
document.querySelectorAll('.add-option').forEach((el) => {
  el.addEventListener('click', (e) => {
    e.preventDefault();
    const cat = e.target.closest('[data-addcat]').getAttribute('data-addcat');
    if (cat) openAddModal(cat);
  });
});

/* ---- Sensor modal: teach-in / manual ---- */
function asReset() {
  $('as-name').value = ''; $('as-address').value = ''; $('as-eep').value = '';
  $('as-prompt').hidden = true; $('as-success').hidden = true; $('as-teachin-btn').hidden = false;
}
async function enableTeachIn() {
  await api('/api/learn', { method: 'POST', body: JSON.stringify({ enabled: true }) });
}
$('as-teachin-btn')?.addEventListener('click', async () => {
  $('as-teachin-btn').hidden = true;
  $('as-prompt').hidden = false;
  $('as-prompt-state').textContent = t('teachin_active');
  // capture-only teach-in: fills the dialog, does NOT add the device yet
  await api('/api/teachin/capture', { method: 'POST', body: JSON.stringify({}) });
  for (let i = 0; i < 90; i++) {
    await new Promise((r) => setTimeout(r, 1000));
    const cap = await api('/api/teachin/captured');
    if (cap && cap.ok && cap.device) {
      const d = cap.device;
      $('as-name').value = 'learn_' + Number(d.address).toString(16).toLowerCase();
      $('as-address').value = fmtAddrInput(d.address);
      $('as-eep').value = d.eep || '';
      $('as-prompt').hidden = true; $('as-success').hidden = false;
      toast('Sensor detected - please confirm the name', 'success');
      return;
    }
  }
  await api('/api/teachin/capture/stop', { method: 'POST', body: JSON.stringify({}) });
  $('as-prompt-state').textContent = 'No telegram received.';
  $('as-teachin-btn').hidden = false;
});
$('as-cancel')?.addEventListener('click', () => { $('addsensor-overlay').hidden = true; asReset(); });
$('as-save')?.addEventListener('click', async () => {
  const name = $('as-name').value.trim();
  const address = parseAddress($('as-address').value);
  const eep = resolveEep($('as-eep').value);
  if (!name) return toast(t('err_no_name'), 'error');
  if (address === null) return toast(t('err_bad_address'), 'error');
  if (!eep) return toast(t('err_no_eep'), 'error');
  const res = await api('/api/sensors', { method: 'POST', body: JSON.stringify({ name, address, eep, category: 'sensor', virtual: 0 }) });
  if (!res.ok) return toast(t('err_add_device') + (res.error || ''), 'error');
  toast(t('sensor_added'), 'success');
  $('addsensor-overlay').hidden = true; asReset();
  await loadStatus();
});

/* ---- Actor modal ---- */
$('aa-cancel')?.addEventListener('click', () => { $('addactor-overlay').hidden = true; });
$('aa-teachin')?.addEventListener('click', async () => {
  const name = $('aa-name').value.trim();
  const sender = parseInt($('aa-sender').value, 0);
  const eep = resolveEep($('aa-eep').value);
  if (!name) return toast(t('err_no_name'), 'error');
  if (isNaN(sender)) return toast(t('err_no_sender'), 'error');
  if (!eep) return toast(t('err_no_eep'), 'error');
  // save first, then send teach-in telegram to the actor
  const res = await api('/api/sensors', { method: 'POST', body: JSON.stringify({ name, address: 0xFFFFFFFF, eep, sender, category: 'actor', virtual: 1 }) });
  if (!res.ok) return toast(t('err_add_device') + (res.error || ''), 'error');
  const t2 = await api('/api/teachin', { method: 'POST', body: JSON.stringify({ name: name }) });
  toast(t2.message || t('teachin_sent'), t2.ok ? 'success' : 'error');
  toast(t('actor_added'), 'success');
  $('addactor-overlay').hidden = true;
  await loadStatus();
});
$('aa-save')?.addEventListener('click', async () => {
  const name = $('aa-name').value.trim();
  const sender = parseInt($('aa-sender').value, 0);
  const eep = resolveEep($('aa-eep').value);
  if (!name) return toast(t('err_no_name'), 'error');
  if (isNaN(sender)) return toast(t('err_no_sender'), 'error');
  if (!eep) return toast(t('err_no_eep'), 'error');
  const res = await api('/api/sensors', { method: 'POST', body: JSON.stringify({ name, address: 0xFFFFFFFF, eep, sender, category: 'actor', virtual: 1 }) });
  if (!res.ok) return toast(t('err_add_device') + (res.error || ''), 'error');
  toast(t('actor_added'), 'success');
  $('addactor-overlay').hidden = true;
  await loadStatus();
});

/* ---- Bidirectional modal ---- */
$('ab-cancel')?.addEventListener('click', () => { $('addbidir-overlay').hidden = true; });
$('ab-teachin')?.addEventListener('click', async () => {
  $('ab-teachin').disabled = true;
  $('ab-hint').querySelector('p').textContent = t('teachin_active');
  await enableTeachIn();
  for (let i = 0; i < 60; i++) {
    await new Promise((r) => setTimeout(r, 1000));
    const known = new Set(state.sensors.map((s) => s.name));
    const data = await api('/api/status');
    const fresh = (data.sensors || []).find((s) => s.name.startsWith('enoceanmqtt/learn_') && !known.has(s.name));
    if (fresh) {
      $('ab-name').value = fresh.name.replace(/^.*\/learn_/, 'learn_');
      $('ab-address').value = fmtAddrInput(fresh.address);
      $('ab-eep').value = fresh.eep || '';
      $('ab-hint').querySelector('p').textContent = 'Device detected - confirm details.';
      $('ab-teachin').disabled = false;
      await api('/api/learn', { method: 'POST', body: JSON.stringify({ enabled: false }) });
      toast('Bidirectional device detected', 'success');
      return;
    }
  }
  $('ab-hint').querySelector('p').textContent = 'No telegram received.';
  $('ab-teachin').disabled = false;
});
$('ab-save')?.addEventListener('click', async () => {
  const name = $('ab-name').value.trim();
  const address = parseAddress($('ab-address').value);
  const sender = parseInt($('ab-sender').value, 0);
  const eep = resolveEep($('ab-eep').value);
  if (!name) return toast(t('err_no_name'), 'error');
  if (address === null) return toast(t('err_bad_address'), 'error');
  if (isNaN(sender)) return toast(t('err_no_sender'), 'error');
  if (!eep) return toast(t('err_no_eep'), 'error');
  const res = await api('/api/sensors', { method: 'POST', body: JSON.stringify({ name, address, eep, sender, category: 'bidirectional', virtual: 1, direction: 1, answer: 1 }) });
  if (!res.ok) return toast(t('err_add_device') + (res.error || ''), 'error');
  toast(t('bidir_added'), 'success');
  $('addbidir-overlay').hidden = true;
  await loadStatus();
});

/* ---------------- themed tooltip ---------------- */
(function () {
  let tipEl = null;
  function showTip(text, x, y) {
    if (!tipEl) {
      tipEl = document.createElement('div');
      tipEl.className = 'ui-tooltip';
      document.body.appendChild(tipEl);
    }
    tipEl.textContent = text;
    tipEl.style.display = 'block';
    const rect = tipEl.getBoundingClientRect();
    let left = x + 12;
    if (left + rect.width > window.innerWidth - 8) left = x - rect.width - 12;
    let top = y + 14;
    if (top + rect.height > window.innerHeight - 8) top = y - rect.height - 10;
    tipEl.style.left = left + 'px';
    tipEl.style.top = top + 'px';
  }
  function hideTip() { if (tipEl) tipEl.style.display = 'none'; }
  document.addEventListener('mouseover', (e) => {
    const t = (e.target && e.target.closest) ? e.target.closest('[data-tip]') : null;
    if (t && t.getAttribute('data-tip') && t.getAttribute('data-tip') !== '—') {
      const r = t.getBoundingClientRect();
      showTip(t.getAttribute('data-tip'), r.left, r.bottom);
    }
  });
  document.addEventListener('mouseout', (e) => {
    if (e.target && e.target.closest && e.target.closest('[data-tip]')) hideTip();
  });
})();

/* ---------------- remove sensor ---------------- */
let pendingRemove = null;
function confirmRemove(name) {
  pendingRemove = name;
  $('modal-title').textContent = t('remove_title') + ' (' + name + ')';
  $('modal-body').textContent = t('remove_body');
  $('modal-overlay').hidden = false;
}
$('modal-cancel')?.addEventListener('click', () => { $('modal-overlay').hidden = true; pendingRemove = null; });
$('modal-overlay')?.addEventListener('click', (e) => {
  if (e.target === $('modal-overlay')) { $('modal-overlay').hidden = true; pendingRemove = null; }
});
$('modal-ok')?.addEventListener('click', async () => {
  const name = pendingRemove;
  $('modal-overlay').hidden = true;
  pendingRemove = null;
  if (!name) return;
  try {
    await api('/api/sensors/' + encodeURIComponent(name), { method: 'DELETE' });
    toast(t('device_removed') + ' "' + name + '"', 'success');
    await loadStatus();
  } catch (err) {
    toast(t('err_remove_device') + err.message, 'error');
  }
});

/* ---------------- i18n ---------------- */
const I18N = {
  en: {
    subtitle: 'Web Configurator', gateway: 'Gateway', mqtt: 'MQTT',
    teachin_title: 'Teach-In', teachin_desc: 'Start teach-in, then trigger the device you want to add: press its teach-in button (or simply use a regular switch, e.g. an F6 rocker - it does not need a teach-in button). The device is added automatically. Devices using a 4BS or UTE learn telegram are recognised with their EEP; bi-directional devices are acknowledged.',
    teachin_hint: 'If the EEP is not part of the telegram (e.g. RPS/F6 switches), a default EEP for the device type is assigned - you can fine-tune it with the edit button. Teach-in mode automatically turns off after a device is received.',
    start_teachin: 'Start teach-in', stop_teachin: 'Stop teach-in',
    add_device: 'Add device', add_sub: 'Choose what kind of device to add',
    add_sensor: 'Sensor', add_actor: 'Actor', add_bidir: 'Bidirectional', add: 'Add',
    teachin_active: 'Teach-in active - press the button on your sensor…',
    sensor_name: 'Sensor name', cancel: 'Cancel', save_sensor: 'Save sensor',
    desc_sensor: 'Measuring device (sender)', desc_actor: 'Receiving device', desc_bidir: 'Sends and receives',
    name: 'Name', address: 'Address', sender_id: 'Sender ID', eep_label: 'EEP (Equipment Profile)',
    all: 'All', sensors: 'Sensors', actors: 'Actors', devices: 'Devices',
    configuration: 'Configuration', config_sub: '[CONFIG] section of enoceanmqtt.conf',
    edit_config: 'Edit configuration',
    save_config: 'Save configuration',
    col_name: 'Name', col_addr: 'Address / Sender', col_type: 'Type', col_eep: 'EEP',
    col_status: 'Status', col_lastseen: 'Last seen', col_latest: 'Latest', col_rssi: 'RSSI',
    remove_device: 'Remove device', edit_device: 'Edit device',
    remove_title: 'Remove device?', remove_body: 'Are you sure you want to remove this device?',
    remove: 'Remove', save: 'Save', close: 'Close',
    graph_title: 'Value history', graph_nodata: 'No data.',
    footer_info: 'HA_enoceanmqtt web configurator',
    select_eep: 'Select an EEP…',
    st_online: 'Online', st_offline: 'Offline', st_never: 'Never seen',
    actor: 'Actor', sensor: 'Sensor', bidirectional: 'Bidirectional',
    no_actors: 'No actors configured.', no_sensors: 'No sensors configured.', no_devices: 'No devices configured yet.',
    connected: 'connected', offline: 'offline', base_id: 'Base ID',
    eep_placeholder: 'A5-20-01, temperature, switch…', device_count: 'devices',
    eep_viewer: 'Open in EEP viewer', send_label: 'send',
    err_load_status: 'Failed to load status: ', err_load_config: 'Failed to load configuration: ',
    config_saved: 'Configuration saved - restart required', err_save_config: 'Failed to save configuration: ',
    err_bad_address: 'Please enter a valid address (e.g. 0x003DD63B)',
    device_updated: 'Device updated', err_update_device: 'Failed to update device: ',
    teachin_sent: 'Teach-in sent', err_send_teachin: 'Failed to send teach-in: ',
    teachin_enabled: 'Teach-in enabled - press the button on your device', teachin_disabled: 'Teach-in disabled',
    err_teachin: 'Failed to change teach-in: ',
    err_no_name: 'Please enter a name', err_no_eep: 'Please select an EEP from the list',
    err_no_sender: 'Please select a sender ID',
    sensor_added: 'Sensor added', actor_added: 'Actor added', bidir_added: 'Bidirectional added',
    err_add_device: 'Failed to add device: ', device_removed: 'Device removed',
    manual_hint: 'or fill in the fields below manually.',
    manual_hint: 'oder füllen Sie die Felder unten manuell aus.',
    manual_hint: 'ou remplissez les champs ci-dessous manuellement.',
    manual_hint: 'oppure compila i campi qui sotto manualmente.',
    send_teachin: 'Send teach-in',
    save_actor: 'Save actor',
    save_device: 'Save device',
    manual_hint: 'or fill in the fields below manually.',
    send_teachin: 'Teach-In senden',
    save_actor: 'Aktor speichern',
    save_device: 'Gerät speichern',
    manual_hint: 'oder füllen Sie die Felder unten manuell aus.',
    send_teachin: 'Envoyer l\'enseignement',
    save_actor: 'Enregistrer l\'actionneur',
    save_device: 'Enregistrer l\'appareil',
    manual_hint: 'ou remplissez les champs ci-dessous manuellement.',
    send_teachin: 'Invia teach-in',
    save_actor: 'Salva attuatore',
    save_device: 'Salva dispositivo',
    manual_hint: 'oppure compila i campi qui sotto manualmente.',
    err_remove_device: 'Failed to remove device: ',
  },
  de: {
    subtitle: 'Web-Konfigurator', gateway: 'Gateway', mqtt: 'MQTT',
    teachin_title: 'Anlernen', teachin_desc: 'Anlernen starten, dann das gewünschte Gerät auslösen: dessen Anlern-Taste drücken (oder einfach einen normalen Schalter verwenden, z.B. einen F6-Rocker - der benötigt keine Anlern-Taste). Das Gerät wird automatisch hinzugefügt. Geräte mit 4BS- oder UTE-Lerntelegramm werden mit ihrer EEP erkannt; bidirektionale Geräte werden bestätigt.',
    teachin_hint: 'Wenn die EEP nicht Teil des Telegramms ist (z.B. RPS/F6-Schalter), wird eine Standard-EEP für den Gerätetyp zugewiesen - Sie können sie mit der Bearbeiten-Schaltfläche verfeinern. Der Anlern-Modus schaltet sich nach einem empfangenen Gerät automatisch aus.',
    start_teachin: 'Anlernen starten', stop_teachin: 'Anlernen stoppen',
    add_device: 'Gerät hinzufügen', add_sub: 'Wählen Sie, welche Art von Gerät hinzugefügt werden soll',
    add_sensor: 'Sensor', add_actor: 'Aktor', add_bidir: 'Bidirektional', add: 'Hinzufügen',
    teachin_active: 'Anlernen aktiv - Taste am Sensor drücken…',
    sensor_name: 'Sensorname', cancel: 'Abbrechen', save_sensor: 'Sensor speichern',
    desc_sensor: 'Messgerät (Sender)', desc_actor: 'Empfangsgerät', desc_bidir: 'Sendet und empfängt',
    name: 'Name', address: 'Adresse', sender_id: 'Sender-ID', eep_label: 'EEP (Geräteprofil)',
    all: 'Alle', sensors: 'Sensoren', actors: 'Aktoren', devices: 'Geräte',
    configuration: 'Konfiguration', config_sub: '[CONFIG]-Abschnitt von enoceanmqtt.conf',
    edit_config: 'Konfiguration bearbeiten',
    save_config: 'Konfiguration speichern',
    col_name: 'Name', col_addr: 'Adresse / Sender', col_type: 'Typ', col_eep: 'EEP',
    col_status: 'Status', col_lastseen: 'Zuletzt gesehen', col_latest: 'Letzter Wert', col_rssi: 'RSSI',
    remove_device: 'Gerät entfernen', edit_device: 'Gerät bearbeiten',
    remove_title: 'Gerät entfernen?', remove_body: 'Sind Sie sicher, dass Sie dieses Gerät entfernen möchten?',
    remove: 'Entfernen', save: 'Speichern', close: 'Schließen',
    graph_title: 'Werteverlauf', graph_nodata: 'Keine Daten.',
    footer_info: 'HA_enoceanmqtt Web-Konfigurator',
    select_eep: 'EEP auswählen…',
    st_online: 'Online', st_offline: 'Offline', st_never: 'Nie gesehen',
    actor: 'Aktor', sensor: 'Sensor', bidirectional: 'Bidirektional',
    no_actors: 'Keine Aktoren konfiguriert.', no_sensors: 'Keine Sensoren konfiguriert.', no_devices: 'Noch keine Geräte konfiguriert.',
    connected: 'verbunden', offline: 'offline', base_id: 'Basis-ID',
    eep_placeholder: 'A5-20-01, Temperatur, Schalter…', device_count: 'Geräte',
    eep_viewer: 'Im EEP-Viewer öffnen', send_label: 'Senden',
    err_load_status: 'Status konnte nicht geladen werden: ', err_load_config: 'Konfiguration konnte nicht geladen werden: ',
    config_saved: 'Konfiguration gespeichert - Neustart erforderlich', err_save_config: 'Konfiguration konnte nicht gespeichert werden: ',
    err_bad_address: 'Bitte eine gültige Adresse eingeben (z.B. 0x003DD63B)',
    device_updated: 'Gerät aktualisiert', err_update_device: 'Gerät konnte nicht aktualisiert werden: ',
    teachin_sent: 'Anlernen gesendet', err_send_teachin: 'Anlernen konnte nicht gesendet werden: ',
    teachin_enabled: 'Anlernen aktiviert - Taste am Gerät drücken', teachin_disabled: 'Anlernen deaktiviert',
    err_teachin: 'Anlernen konnte nicht geändert werden: ',
    err_no_name: 'Bitte einen Namen eingeben', err_no_eep: 'Bitte eine EEP aus der Liste wählen',
    err_no_sender: 'Bitte eine Sender-ID wählen',
    sensor_added: 'Sensor hinzugefügt', actor_added: 'Aktor hinzugefügt', bidir_added: 'Bidirektionales Gerät hinzugefügt',
    err_add_device: 'Gerät konnte nicht hinzugefügt werden: ', device_removed: 'Gerät entfernt',
    err_remove_device: 'Gerät konnte nicht entfernt werden: ',
  },
  fr: {
    subtitle: 'Configurateur Web', gateway: 'Passerelle', mqtt: 'MQTT',
    teachin_title: 'Enseignement', teachin_desc: 'Démarrer l\'enseignement, puis déclencher l\'appareil à ajouter : appuyez sur son bouton d\'enseignement (ou utilisez simplement un interrupteur normal, par ex. un rocker F6 - il n\'a pas besoin de bouton d\'enseignement). L\'appareil est ajouté automatiquement. Les appareils utilisant un télégramme d\'apprentissage 4BS ou UTE sont reconnus avec leur EEP ; les appareils bidirectionnels sont acquittés.',
    teachin_hint: 'Si l\'EEP ne fait pas partie du télégramme (par ex. interrupteurs RPS/F6), une EEP par défaut pour le type d\'appareil est attribuée - vous pouvez l\'affiner avec le bouton Modifier. Le mode enseignement s\'arrête automatiquement après un appareil reçu.',
    start_teachin: 'Démarrer l\'enseignement', stop_teachin: 'Arrêter l\'enseignement',
    add_device: 'Ajouter un appareil', add_sub: 'Choisissez le type d\'appareil à ajouter',
    add_sensor: 'Capteur', add_actor: 'Actionneur', add_bidir: 'Bidirectionnel', add: 'Ajouter',
    teachin_active: 'Enseignement actif - appuyez sur le bouton du capteur…',
    sensor_name: 'Nom du capteur', cancel: 'Annuler', save_sensor: 'Enregistrer le capteur',
    desc_sensor: 'Appareil de mesure (émetteur)', desc_actor: 'Appareil récepteur', desc_bidir: 'Émet et reçoit',
    name: 'Nom', address: 'Adresse', sender_id: 'ID émetteur', eep_label: 'EEP (profil)',
    all: 'Tous', sensors: 'Capteurs', actors: 'Actionneurs', devices: 'Appareils',
    configuration: 'Configuration', config_sub: 'Section [CONFIG] de enoceanmqtt.conf',
    edit_config: 'Modifier la configuration',
    save_config: 'Enregistrer la configuration',
    col_name: 'Nom', col_addr: 'Adresse / Émetteur', col_type: 'Type', col_eep: 'EEP',
    col_status: 'État', col_lastseen: 'Vu pour la dernière fois', col_latest: 'Dernière valeur', col_rssi: 'RSSI',
    remove_device: 'Supprimer l\'appareil', edit_device: 'Modifier l\'appareil',
    remove_title: 'Supprimer l\'appareil ?', remove_body: 'Êtes-vous sûr de vouloir supprimer cet appareil ?',
    remove: 'Supprimer', save: 'Enregistrer', close: 'Fermer',
    graph_title: 'Historique des valeurs', graph_nodata: 'Pas de données.',
    footer_info: 'HA_enoceanmqtt configurateur web',
    select_eep: 'Choisir un EEP…',
    st_online: 'En ligne', st_offline: 'Hors ligne', st_never: 'Jamais vu',
    actor: 'Actionneur', sensor: 'Capteur', bidirectional: 'Bidirectionnel',
    no_actors: 'Aucun actionneur configuré.', no_sensors: 'Aucun capteur configuré.', no_devices: 'Aucun appareil configuré.',
    connected: 'connecté', offline: 'hors ligne', base_id: 'ID de base',
    eep_placeholder: 'A5-20-01, température, interrupteur…', device_count: 'appareils',
    eep_viewer: 'Ouvrir dans la visionneuse EEP', send_label: 'envoyer',
    err_load_status: 'Échec du chargement de l\'état : ', err_load_config: 'Échec du chargement de la configuration : ',
    config_saved: 'Configuration enregistrée - redémarrage requis', err_save_config: 'Échec de l\'enregistrement : ',
    err_bad_address: 'Veuillez saisir une adresse valide (ex. 0x003DD63B)',
    device_updated: 'Appareil mis à jour', err_update_device: 'Échec de la mise à jour : ',
    teachin_sent: 'Enseignement envoyé', err_send_teachin: 'Échec de l\'envoi : ',
    teachin_enabled: 'Enseignement activé - appuyez sur le bouton', teachin_disabled: 'Enseignement désactivé',
    err_teachin: 'Échec du changement d\'enseignement : ',
    err_no_name: 'Veuillez saisir un nom', err_no_eep: 'Veuillez choisir un EEP dans la liste',
    err_no_sender: 'Veuillez choisir un ID émetteur',
    sensor_added: 'Capteur ajouté', actor_added: 'Actionneur ajouté', bidir_added: 'Appareil bidirectionnel ajouté',
    err_add_device: 'Échec de l\'ajout : ', device_removed: 'Appareil supprimé',
    err_remove_device: 'Échec de la suppression : ',
  },
  it: {
    subtitle: 'Configuratore Web', gateway: 'Gateway', mqtt: 'MQTT',
    teachin_title: 'Teach-In', teachin_desc: 'Avvia teach-in, poi attiva il dispositivo da aggiungere: premi il suo pulsante teach-in (o usa semplicemente un interruttore normale, es. un rocker F6 - non serve un pulsante teach-in). Il dispositivo viene aggiunto automaticamente. I dispositivi che usano un telegramma di apprendimento 4BS o UTE sono riconosciuti con la loro EEP; i dispositivi bidirezionali vengono confermati.',
    teachin_hint: 'Se l\'EEP non fa parte del telegramma (es. interruttori RPS/F6), viene assegnata una EEP predefinita per il tipo di dispositivo - puoi perfezionarla con il pulsante Modifica. La modalità teach-in si disattiva automaticamente dopo un dispositivo ricevuto.',
    start_teachin: 'Avvia teach-in', stop_teachin: 'Ferma teach-in',
    add_device: 'Aggiungi dispositivo', add_sub: 'Scegli il tipo di dispositivo da aggiungere',
    add_sensor: 'Sensore', add_actor: 'Attuatore', add_bidir: 'Bidirezionale', add: 'Aggiungi',
    teachin_active: 'Teach-in attivo - premi il pulsante sul sensore…',
    sensor_name: 'Nome sensore', cancel: 'Annulla', save_sensor: 'Salva sensore',
    desc_sensor: 'Dispositivo di misura (mittente)', desc_actor: 'Dispositivo ricevente', desc_bidir: 'Invia e riceve',
    name: 'Nome', address: 'Indirizzo', sender_id: 'ID mittente', eep_label: 'EEP (profilo)',
    all: 'Tutti', sensors: 'Sensori', actors: 'Attuatori', devices: 'Dispositivi',
    configuration: 'Configurazione', config_sub: 'Sezione [CONFIG] di enoceanmqtt.conf',
    edit_config: 'Modifica configurazione',
    save_config: 'Salva configurazione',
    col_name: 'Nome', col_addr: 'Indirizzo / Mittente', col_type: 'Tipo', col_eep: 'EEP',
    col_status: 'Stato', col_lastseen: 'Ultimo visto', col_latest: 'Ultimo valore', col_rssi: 'RSSI',
    remove_device: 'Rimuovi dispositivo', edit_device: 'Modifica dispositivo',
    remove_title: 'Rimuovere il dispositivo?', remove_body: 'Sicuro di voler rimuovere questo dispositivo?',
    remove: 'Rimuovi', save: 'Salva', close: 'Chiudi',
    graph_title: 'Cronologia valori', graph_nodata: 'Nessun dato.',
    footer_info: 'HA_enoceanmqtt configuratore web',
    select_eep: 'Seleziona un EEP…',
    st_online: 'Online', st_offline: 'Offline', st_never: 'Mai visto',
    actor: 'Attuatore', sensor: 'Sensore', bidirectional: 'Bidirezionale',
    no_actors: 'Nessun attuatore configurato.', no_sensors: 'Nessun sensore configurato.', no_devices: 'Nessun dispositivo configurato.',
    connected: 'connesso', offline: 'offline', base_id: 'ID base',
    eep_placeholder: 'A5-20-01, temperatura, interruttore…', device_count: 'dispositivi',
    eep_viewer: 'Apri nel visualizzatore EEP', send_label: 'invio',
    err_load_status: 'Impossibile caricare lo stato: ', err_load_config: 'Impossibile caricare la configurazione: ',
    config_saved: 'Configurazione salvata - riavvio richiesto', err_save_config: 'Impossibile salvare la configurazione: ',
    err_bad_address: 'Inserisci un indirizzo valido (es. 0x003DD63B)',
    device_updated: 'Dispositivo aggiornato', err_update_device: 'Impossibile aggiornare il dispositivo: ',
    teachin_sent: 'Teach-in inviato', err_send_teachin: 'Impossibile inviare teach-in: ',
    teachin_enabled: 'Teach-in attivato - premi il pulsante', teachin_disabled: 'Teach-in disattivato',
    err_teachin: 'Impossibile modificare teach-in: ',
    err_no_name: 'Inserisci un nome', err_no_eep: 'Seleziona una EEP dalla lista',
    err_no_sender: 'Seleziona un ID mittente',
    sensor_added: 'Sensore aggiunto', actor_added: 'Attuatore aggiunto', bidir_added: 'Dispositivo bidirezionale aggiunto',
    err_add_device: 'Impossibile aggiungere il dispositivo: ', device_removed: 'Dispositivo rimosso',
    err_remove_device: 'Impossibile rimuovere il dispositivo: ',
  },
};

let currentLang = 'en';
function setLang(lang) {
  currentLang = I18N[lang] ? lang : 'en';
  try { localStorage.setItem('enm-lang', currentLang); } catch (e) { /* ignore */ }
  applyTranslations();
}
function t(key) {
  return (I18N[currentLang] && I18N[currentLang][key]) || (I18N.en[key] || key);
}
function applyTranslations() {
  document.querySelectorAll('[data-i18n]').forEach((el) => {
    // elements set dynamically must never be overwritten by translations
    if (el.id === 'graph-body' || el.id === 'modal-title' || el.id === 'modal-body') return;
    const key = el.getAttribute('data-i18n');
    if (key && t(key)) el.textContent = t(key);
  });
  document.querySelectorAll('[data-ph]').forEach((el) => {
    const key = el.getAttribute('data-ph');
    if (key && t(key)) el.setAttribute('placeholder', t(key));
  });
  document.querySelectorAll('.eep-info').forEach((el) => {
    el.title = t('eep_viewer');
  });
}
$('lang-select')?.addEventListener('change', (e) => setLang(e.target.value));
// collapsible cards (e.g. Configuration)
document.querySelectorAll('.card.collapsible > .card-header').forEach((h) => {
  h.addEventListener('click', () => {
    h.parentElement.classList.toggle('collapsed');
  });
});
(function () {
  try {
    const saved = localStorage.getItem('enm-lang');
    currentLang = I18N[saved] ? saved : 'en';
    const sel = $('lang-select');
    if (sel) sel.value = currentLang;
  } catch (e) { /* ignore */ }
})();

/* ---------------- theme ---------------- */
function currentTheme() {
  return document.documentElement.dataset.theme === 'light' ? 'light' : 'dark';
}
function setTheme(theme) {
  document.documentElement.dataset.theme = theme;
  try { localStorage.setItem('enm-theme', theme); } catch (e) { /* ignore */ }
}
/* keep the toggle in sync when the OS preference changes and the user
   has not made an explicit choice */
let themeExplicit = false;
try { themeExplicit = localStorage.getItem('enm-theme') !== null; } catch (e) { /* ignore */ }
window.matchMedia('(prefers-color-scheme: light)').addEventListener?.('change', (e) => {
  if (!themeExplicit) setTheme(e.matches ? 'light' : 'dark');
});
$('theme-toggle')?.addEventListener('click', () => {
  themeExplicit = true;
  setTheme(currentTheme() === 'dark' ? 'light' : 'dark');
});

/* ---------------- events ---------------- */
/* ---------------- init ---------------- */
// wire the EEP combos (add modals + edit modal)
initEepCombo('as-eep', 'as-eep-list', 'as-eep-drop', 'as-eep-info');
initEepCombo('aa-eep', 'aa-eep-list', 'aa-eep-drop', 'aa-eep-info');
initEepCombo('ab-eep', 'ab-eep-list', 'ab-eep-drop', 'ab-eep-info');
initEepCombo('e-eep-search', 'e-eep-list', 'e-eep-drop', 'e-eep-info');
loadStatus();
setInterval(loadStatus, 2000);
