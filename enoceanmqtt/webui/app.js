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

  if (gw) {
    gwEl.className = 'pill ' + (gw.connected ? 'ok' : 'bad');
    const gwAddr = (state.config && state.config.enocean_port) ? state.config.enocean_port : (gw.base_id || '—');
    gwEl.innerHTML = '<span class="dot"></span>' + t('gateway') + ' ' + escapeHtml(String(gwAddr)) +
      (gw.base_id ? ' (' + escapeHtml(String(gw.base_id)) + ')' : '');
    mqttEl.className = 'pill ' + (gw.mqtt ? 'ok' : 'bad');
    const mqttAddr = (state.config && state.config.mqtt_host) ? state.config.mqtt_host + (state.config.mqtt_port ? ':' + state.config.mqtt_port : '') : '—';
    mqttEl.innerHTML = '<span class="dot"></span>MQTT ' + escapeHtml(String(mqttAddr));
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
    $('graph-body').innerHTML = '<div class="graph-wrap"><svg id="mini-graph" viewBox="0 0 600 180" preserveAspectRatio="none"></svg>' +
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
  const W = 600, H = 180, PADL = 46, PADR = 10, PADT = 10, PADB = 22, PW = W - PADL - PADR, PH = H - PADT - PADB;
  const vals = pts.map((p) => p.v);
  const min = Math.min.apply(null, vals), max = Math.max.apply(null, vals);
  const span = (max - min) || 1;
  const t0 = pts[0].t, t1 = pts[pts.length - 1].t || (t0 + 1);
  const X = (t) => PADL + (t - t0) / (t1 - t0) * PW;
  const Y = (v) => PADT + (1 - (v - min) / span) * PH;
  const fmtV = (v) => (Math.round(v * 100) / 100).toString();
  const fmtT = (t) => { const d = new Date(t); return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }); };
  svg.innerHTML = '<rect x="0" y="0" width="' + W + '" height="' + H + '" fill="none"/>' +
    '<polyline fill="none" stroke="var(--primary)" stroke-width="2" points="' +
    pts.map((p) => X(p.t).toFixed(1) + ',' + Y(p.v).toFixed(1)).join(' ') + '"/>' +
    // Y axis labels (start = min at bottom, end = max at top), left-aligned
    '<text x="' + (PADL - 6) + '" y="' + (Y(max) + 4) + '" fill="var(--text-muted)" font-size="12" text-anchor="end">' + escapeHtml(fmtV(max)) + '</text>' +
    '<text x="' + (PADL - 6) + '" y="' + (Y(min) + 4) + '" fill="var(--text-muted)" font-size="12" text-anchor="end">' + escapeHtml(fmtV(min)) + '</text>' +
    // X axis time labels (start at left, end at right)
    '<text x="' + PADL + '" y="' + (H - 6) + '" fill="var(--text-muted)" font-size="12">' + escapeHtml(fmtT(t0)) + '</text>' +
    '<text x="' + (W - PADR) + '" y="' + (H - 6) + '" fill="var(--text-muted)" font-size="12" text-anchor="end">' + escapeHtml(fmtT(t1)) + '</text>' +
    '<g id="mini-hover"></g>';
  // hover: nearest sample -> dot + context box (below the graph)
  const ctl = document.getElementById('mini-hoverctl');
  const g = document.getElementById('mini-hover');
  svg.addEventListener('mousemove', (e) => {
    const r = svg.getBoundingClientRect();
    const px = (e.clientX - r.left) / r.width * W;
    let best = pts[0], bestD = Infinity;
    for (const p of pts) { const d = Math.abs(X(p.t) - px); if (d < bestD) { bestD = d; best = p; } }
    const bx = X(best.t), by = Y(best.v);
    if (g) g.innerHTML = '<circle cx="' + bx.toFixed(1) + '" cy="' + by.toFixed(1) + '" r="4" fill="var(--primary)" stroke="#fff" stroke-width="1.5"/>' +
      '<line x1="' + bx.toFixed(1) + '" y1="' + PADT + '" x2="' + bx.toFixed(1) + '" y2="' + (H - PADB) + '" stroke="var(--border-strong)" stroke-width="1" stroke-dasharray="3,3"/>';
    if (ctl) {
      const d = new Date(best.t);
      ctl.hidden = false;
      ctl.innerHTML = '<b>' + escapeHtml(key) + '</b>: ' + escapeHtml(String(Math.round(best.v * 100) / 100)) +
        (ctl.dataset.unit ? ' ' + ctl.dataset.unit : '') + ' &middot; ' + escapeHtml(d.toLocaleTimeString());
    }
  });
  svg.addEventListener('mouseleave', () => { if (g) g.innerHTML = ''; if (ctl) ctl.hidden = true; });
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
// translated config labels (de/fr/it) - falls back to English
const CONFIG_LABELS_TR = {
  de: { enocean_port: 'EnOcean-Port', mqtt_host: 'MQTT-Host', mqtt_port: 'MQTT-Port', mqtt_prefix: 'MQTT-Präfix', mqtt_keepalive: 'MQTT-Keepalive', mqtt_user: 'MQTT-Benutzer', mqtt_ssl: 'MQTT-SSL', log_packets: 'Pakete protokollieren', overlay: 'Overlay', webui_port: 'WebUI-Port', webui_disable: 'WebUI deaktivieren', db_file: 'Geräte-DB-Datei', webui_sensor_store: 'Sensor-Speicher' },
  fr: { enocean_port: 'Port EnOcean', mqtt_host: 'Hôte MQTT', mqtt_port: 'Port MQTT', mqtt_prefix: 'Préfixe MQTT', mqtt_keepalive: 'Keepalive MQTT', mqtt_user: 'Utilisateur MQTT', mqtt_ssl: 'MQTT SSL', log_packets: 'Journaliser les paquets', overlay: 'Overlay', webui_port: 'Port WebUI', webui_disable: 'Désactiver WebUI', db_file: 'Fichier DB', webui_sensor_store: 'Stockage capteurs' },
  it: { enocean_port: 'Porta EnOcean', mqtt_host: 'Host MQTT', mqtt_port: 'Porta MQTT', mqtt_prefix: 'Prefisso MQTT', mqtt_keepalive: 'Keepalive MQTT', mqtt_user: 'Utente MQTT', mqtt_ssl: 'MQTT SSL', log_packets: 'Registra pacchetti', overlay: 'Overlay', webui_port: 'Porta WebUI', webui_disable: 'Disabilita WebUI', db_file: 'File DB', webui_sensor_store: 'Archivio sensori' },
};
function configLabel(key) {
  const tr = CONFIG_LABELS_TR[currentLang] || {};
  return tr[key] || CONFIG_LABELS[key] || key;
}

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
  // 2 columns of name|value pairs -> 4 cells per row
  const rows = [];
  for (let i = 0; i < keys.length; i += 2) {
    const cells = [];
    for (let j = 0; j < 2; j++) {
      const k = keys[i + j];
      if (!k) { cells.push('<td></td><td></td>'); continue; }
      const label = configLabel(k);
      const val = conf[k] === undefined || conf[k] === null ? '' : String(conf[k]);
      cells.push('<td class="k">' + escapeHtml(label) + '</td><td class="mono">' + escapeHtml(val) + '</td>');
    }
    rows.push('<tr>' + cells.join('') + '</tr>');
  }
  return '<table class="config-table config-4col">' + rows.join('') + '</table>';
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
    const label = configLabel(k);
    const raw = conf[k];
    if (isBoolConf(k, raw)) {
      const checked = ['1', 'true', 'yes', 'on'].includes(String(raw == null ? '' : raw).trim().toLowerCase());
      return '<div class="field config-bool"><label for="cfg-' + escapeHtml(k) + '">' + escapeHtml(label) + '</label>' +
        '<input type="checkbox" id="cfg-' + escapeHtml(k) + '" data-cfgkey="' + escapeHtml(k) + '"' + (checked ? ' checked' : '') + '>' +
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
$('btn-top-config')?.addEventListener('click', openConfigEdit);
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
  // fields per device type: sensor=Name/Address/EEP; actor=Name/Sender/EEP;
  // bidirectional=Name/Address/Sender/EEP (direction/answer/default_data are
  // filled automatically from the stored values, not shown for editing).
  const showAddr = cat !== 'actor';
  const showSender = cat !== 'sensor';
  $('e-addr-field').hidden = !showAddr;
  $('e-sender-field').hidden = !showSender;
  $('e-dir-field').hidden = true;
  $('e-answer-field').hidden = true;
  $('e-default-field').hidden = true;
  populateSenders($('e-sender'), state.virtual_senders, s.sender);
  if (s.sender !== undefined && s.sender !== null) {
    const selE = $('e-sender');
    if (selE) selE.value = fmtAddr(s.sender);
  }
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
  // never trust the argument - coerce to an array
  const list = (Array.isArray(senders) ? senders
    : (Array.isArray(state && state.virtual_senders) ? state.virtual_senders : []));
  const used = new Set(state.sensors.filter((s) => s.sender).map((s) => s.sender));
  const opts = list.map((v) => {
    const hex = fmtAddr(v);
    const isUsed = used.has(v);
    const isSel = (selected !== undefined && selected !== null && Number(selected) === v);
    return '<option value="' + hex + '"' + (isUsed && !isSel ? ' disabled' : '') + (isSel ? ' selected' : '') + '>' + hex + (isUsed && !isSel ? ' (used)' : '') + '</option>';
  });
  sel.innerHTML = opts.length ? opts.join('') : '<option value="">(no base ID yet)</option>';
  sel.disabled = opts.length === 0;
}

/* ---------------- Add-device modals ---------------- */
function firstFreeSender() {
  const list = (Array.isArray(state && state.virtual_senders) ? state.virtual_senders : []);
  const used = new Set((state.sensors || []).filter((s) => s.sender).map((s) => s.sender));
  for (const v of list) {
    if (!used.has(v)) return v;
  }
  return list[0] || null;
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


let currentLang = 'en';
function setLang(lang) {
  currentLang = (lang === 'de' || lang === 'fr' || lang === 'it' || lang === 'en') ? lang : 'en';
  try { localStorage.setItem('enm-lang', currentLang); } catch (e) { /* ignore */ }
  applyTranslations();
}
function langDict(code) {
  return (typeof window !== 'undefined' && window['LANG_' + code]) || {};
}
function t(key) {
  // fallback: language dict, then English, then the key itself
  const d = langDict(currentLang);
  if (d && d[key] !== undefined) return d[key];
  const en = langDict('en');
  if (en && en[key] !== undefined) return en[key];
  return key;
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
    currentLang = (saved === 'de' || saved === 'fr' || saved === 'it') ? saved : 'en';
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
