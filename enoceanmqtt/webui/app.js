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
    renderLearn();
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
  }
}

function renderLearn() {
  const btn = $('btn-learn-on');
  if (btn) btn.textContent = state.learn ? 'Stop teach-in' : 'Start teach-in';
  const card = $('learn-card');
  if (card) card.style.borderColor = state.learn ? 'rgba(255,100,200,0.5)' : '';
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
    return '<a href="#" class="val-link" data-valgraph="' + escapeHtml(s.name + '|' + k) + '" title="Graph: ' + escapeHtml(k) + '">' +
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
    $('graph-body').innerHTML = '<div class="graph-wrap"><svg id="mini-graph" viewBox="0 0 600 180" preserveAspectRatio="none"></svg></div>' +
      '<div style="margin-top:8px;color:var(--text-secondary);font-size:12px">' + escapeHtml(key) + ' · ' + pts.length + ' samples · ' +
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
  let d = pts.map((p, i) => (i ? 'L' : 'M') + X(p.t).toFixed(1) + ',' + Y(p.v).toFixed(1)).join(' ');
  svg.innerHTML = '<polyline fill="none" stroke="var(--primary)" stroke-width="2" points="' +
    pts.map((p) => X(p.t).toFixed(1) + ',' + Y(p.v).toFixed(1)).join(' ') + '"/>' +
    '<text x="' + PAD + '" y="' + (H - 2) + '" fill="var(--text-muted)" font-size="10">' + escapeHtml(String(min)) + '</text>' +
    '<text x="' + (W - PAD - 30) + '" y="' + (H - 2) + '" fill="var(--text-muted)" font-size="10">' + escapeHtml(String(max)) + '</text>';
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

function populateConfig(conf) {
  const grid = $('config-grid');
  if (!grid) return;
  if (!conf || typeof conf !== 'object') { grid.innerHTML = '<span class="card-sub">Configuration not available.</span>'; return; }
  const keys = Object.keys(conf).filter((k) => !CONFIG_HIDDEN.has(k));
  grid.innerHTML = keys.map((k) => {
    const label = CONFIG_LABELS[k] || k;
    const val = conf[k];
    return '<div class="field"><label for="cfg-' + escapeHtml(k) + '">' + escapeHtml(label) + '</label>' +
      '<input type="text" id="cfg-' + escapeHtml(k) + '" data-cfgkey="' + escapeHtml(k) + '" value="' + escapeHtml(String(val)) + '"></div>';
  }).join('');
}

$('config-form')?.addEventListener('submit', async (e) => {
  e.preventDefault();
  const payload = {};
  document.querySelectorAll('#config-grid [data-cfgkey]').forEach((inp) => {
    payload[inp.getAttribute('data-cfgkey')] = inp.value;
  });
  try {
    const res = await api('/api/config', { method: 'POST', body: JSON.stringify(payload) });
    if (!res.ok) throw new Error(res.error || 'save failed');
    toast(t('config_saved'), 'success');
  } catch (err) {
    toast(t('err_save_config') + err.message, 'error');
  }
});

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
    const fmtAddr = (v) => v !== undefined && v !== null ? '0x' + Number(v).toString(16).toUpperCase().padStart(8, '0') : '—';
    let addrHtml;
    if (isActor) {
      addrHtml = fmtAddr(s.sender);
    } else if (s.bidirectional) {
      addrHtml = fmtAddr(s.address) + '<div style="color:var(--text-muted);font-size:11px">send ' + fmtAddr(s.sender) + '</div>';
    } else {
      addrHtml = fmtAddr(s.address);
    }
    const source = s.source === 'dynamic' ? ' <span class="pill" style="font-size:10px;padding:1px 6px">web</span>' : '';
    const name = escapeHtml(s.name);
    const catCls = deviceCategory(s);
    const catTxt = catCls === 'actor' ? t('actor') : (catCls === 'bidirectional' ? t('bidirectional') : t('sensor'));
    const badges = [];
    if (s.bidirectional) badges.push('<span class="badge bidir" title="Bi-directional device">⇅ bidir</span>');
    if (s.smartack) badges.push('<span class="badge smartack" title="smartACK — requires fast acknowledgement">smartACK</span>');
    return `<tr data-name="${escapeHtml(s.name)}" data-cat="${catCls}">
      <td>${name}${source}</td>
      <td class="mono">${addrHtml}</td>
      <td><span class="mono">${eep}</span>${eepName ? '<div style="color:var(--text-muted);font-size:11px">' + eepName + '</div>' : ''}</td>
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
}

/* ---------------- edit device ---------------- */
let editingDevice = null;

function openEdit(name) {
  const s = state.sensors.find((x) => x.name === name);
  if (!s) return;
  editingDevice = name;
  $('e-name').value = s.name;
  $('e-address').value = (s.address !== undefined && s.address !== null && s.address !== 0xFFFFFFFF)
    ? '0x' + s.address.toString(16).toUpperCase().padStart(8, '0') : '';
  $('e-eep-search').value = s.eep || '';
  populateEepDatalist('e-eep-list', addMode);
  updateEepInfo('e-eep-search', 'e-eep-info');
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
  const addrVal = $('e-address').value.trim();
  const address = addrVal ? parseAddress(addrVal) : null;
  if (addrVal && address === null) return toast(t('err_bad_address'), 'error');
  try {
    const body = { name: name, eep: eep };
    if (address !== null) body.address = address;
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
    renderLearn();
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

function populateEepDatalist(datalistId, mode) {
  const dl = $(datalistId);
  if (!dl) return;
  const catFilter = (p) => {
    if (!mode) return true;
    if (mode === 'sensor') return p.category === 'sensor';
    if (mode === 'actor') return p.category === 'actor';
    if (mode === 'bidirectional') return p.category === 'bidirectional';
    return true;
  };
  const opts = (state.eep || []).filter(catFilter);
  dl.innerHTML = opts.map((p) =>
    '<option value="' + escapeHtml(p.eep) + '">' + escapeHtml(translateEepName(p.name)) + '</option>').join('');
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
  if (!eep) return 'https://www.enocean.com/en/enocean_modules/eep/';
  const parts = eep.split('-');
  if (parts.length === 3) {
    return 'https://www.enocean.com/en/enocean_modules/eep/' + parts[0] + '-' + parts[1] + '-' + parts[2] + '/';
  }
  return 'https://www.enocean.com/en/enocean_modules/eep/';
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
$('f-eep-search')?.addEventListener('input', () => updateEepInfo('f-eep-search', 'f-eep-info'));
$('e-eep-search')?.addEventListener('input', () => updateEepInfo('e-eep-search', 'e-eep-info'));

function parseAddress(val) {
  let s = String(val || '').trim();
  if (!s) return null;
  if (!/^0x[0-9a-fA-F]{1,8}$/.test(s)) return null;
  return parseInt(s, 16);
}

let addMode = 'sensor';

function setAddMode(mode) {
  addMode = mode;
  document.querySelectorAll('[data-addcat]').forEach((t) => {
    t.classList.toggle('active', t.getAttribute('data-addcat') === mode);
  });
  // sensor: address only; actor: sender only; bidirectional: both
  const showAddr = mode !== 'actor';
  const showSender = mode !== 'sensor';
  $('f-addr-field').hidden = !showAddr;
  $('f-sender-field').hidden = !showSender;
  // EEP dropdown: repopulate filtered by the chosen category
  populateEepDatalist('f-eep-list', mode);
  populateEepDatalist('e-eep-list', mode);
  if (showSender && $('f-sender')) {
    // prefill a fresh (unused) virtual sender if available
    if (!$('f-sender').value) $('f-sender').selectedIndex = 0;
  }
}
document.querySelectorAll('[data-addcat]').forEach((t) => {
  t.addEventListener('click', (e) => {
    e.preventDefault();
    setAddMode(t.getAttribute('data-addcat'));
  });
});

function populateSenders(senders) {
  const sel = $('f-sender');
  if (!sel) return;
  const used = new Set(state.sensors.filter((s) => s.sender).map((s) => s.sender));
  const opts = (senders || []).map((v) => {
    const hex = '0x' + Number(v).toString(16).toUpperCase().padStart(8, '0');
    return '<option value="' + hex + '"' + (used.has(v) ? ' disabled' : '') + '>' + hex + (used.has(v) ? ' (used)' : '') + '</option>';
  });
  sel.innerHTML = opts.length ? opts.join('') : '<option value="">(no base ID yet)</option>';
}

$('add-form')?.addEventListener('submit', async (e) => {
  e.preventDefault();
  const name = $('f-name').value.trim();
  const eep = resolveEep($('f-eep-search').value);
  if (!name) return toast(t('err_no_name'), 'error');
  if (!eep) return toast(t('err_no_eep'), 'error');

  const mode = addMode;
  const isActor = mode === 'actor';
  const isBidir = mode === 'bidirectional';
  let address, sender, cat;
  if (mode === 'sensor') {
    address = parseAddress($('f-address').value);
    if (address === null) return toast(t('err_bad_address'), 'error');
    cat = 'sensor';
  } else if (mode === 'actor') {
    address = 0xFFFFFFFF;
    sender = parseInt($('f-sender').value, 0);
    if (isNaN(sender)) return toast(t('err_no_sender'), 'error');
    cat = 'actor';
  } else { // bidirectional
    address = parseAddress($('f-address').value);
    if (address === null) return toast(t('err_bad_address'), 'error');
    sender = parseInt($('f-sender').value, 0);
    if (isNaN(sender)) return toast(t('err_no_sender'), 'error');
    cat = 'bidirectional';
  }

  try {
    const res = await api('/api/sensors', {
      method: 'POST',
      body: JSON.stringify({ name, address, eep, sender, category: cat, virtual: (mode === 'actor' || isBidir) ? 1 : 0 })
    });
    toast((cat === 'sensor' ? 'Sensor' : (cat === 'actor' ? 'Actor' : 'Bidirectional')) + ' "' + name + '" added', 'success');
    $('add-form').reset();
    setAddMode(addMode);
    await loadStatus();
  } catch (err) {
    toast(t('err_add_device') + err.message, 'error');
  }
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
  $('modal-title').textContent = 'Remove sensor?';
  $('modal-body').textContent = 'Remove "' + name + '" from the configuration? This also removes it from Home Assistant.';
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
    add_device: 'Add device', add_sub: 'Manually add a sensor (sender), an actor (receiver) or a bidirectional device',
    add_sensor: 'Add Sensor (sender)', add_actor: 'Add Actor (receiver)', add_bidir: 'Add Bidirectional',
    name: 'Name', address: 'Address', sender_id: 'Sender ID', eep_label: 'EEP (Equipment Profile)',
    all: 'All', sensors: 'Sensors', actors: 'Actors', devices: 'Devices',
    configuration: 'Configuration', config_sub: '[CONFIG] section of enoceanmqtt.conf - changes apply after restart',
    config_restart: 'A restart is required for most settings to take effect.',
    save_config: 'Save configuration',
    col_name: 'Name', col_addr: 'Address / Sender', col_type: 'Type', col_eep: 'EEP',
    col_status: 'Status', col_lastseen: 'Last seen', col_latest: 'Latest', col_rssi: 'RSSI',
    remove_device: 'Remove device', edit_device: 'Edit device',
    remove_title: 'Remove device?', remove_body: 'Are you sure you want to remove this device?',
    cancel: 'Cancel', remove: 'Remove', save: 'Save', close: 'Close',
    graph_title: 'Value history', graph_nodata: 'No data.',
    footer_info: 'HA_enoceanmqtt web configurator',
    select_eep: 'Select an EEP…',
    st_online: 'Online', st_offline: 'Offline', st_never: 'Never seen',
    actor: 'Actor', sensor: 'Sensor', bidirectional: 'Bidirectional',
    no_actors: 'No actors configured.', no_sensors: 'No sensors configured.', no_devices: 'No devices configured yet.',
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
    connected: 'connected',
    offline: 'offline',
    base_id: 'Base ID',
    eep_placeholder: 'A5-20-01, temperature, switch…',
    device_count: 'devices',
    eep_viewer: 'Open in EEP viewer',
    err_remove_device: 'Failed to remove device: ',
  },
  de: {
    subtitle: 'Web-Konfigurator', gateway: 'Gateway', mqtt: 'MQTT',
    teachin_title: 'Anlernen', teachin_desc: 'Anlernen starten, dann das gewünschte Gerät auslösen: dessen Anlern-Taste drücken (oder einfach einen normalen Schalter verwenden, z.B. einen F6-Rocker - der benötigt keine Anlern-Taste). Das Gerät wird automatisch hinzugefügt. Geräte mit 4BS- oder UTE-Lerntelegramm werden mit ihrer EEP erkannt; bidirektionale Geräte werden bestätigt.',
    teachin_hint: 'Wenn die EEP nicht Teil des Telegramms ist (z.B. RPS/F6-Schalter), wird eine Standard-EEP für den Gerätetyp zugewiesen - Sie können sie mit der Bearbeiten-Schaltfläche verfeinern. Der Anlern-Modus schaltet sich nach einem empfangenen Gerät automatisch aus.',
    start_teachin: 'Anlernen starten', stop_teachin: 'Anlernen stoppen',
    add_device: 'Gerät hinzufügen', add_sub: 'Manuell einen Sensor (Sender), einen Aktor (Empfänger) oder ein bidirektionales Gerät hinzufügen',
    add_sensor: 'Sensor hinzufügen (Sender)', add_actor: 'Aktor hinzufügen (Empfänger)', add_bidir: 'Bidirektional hinzufügen',
    name: 'Name', address: 'Adresse', sender_id: 'Sender-ID', eep_label: 'EEP (Geräteprofil)',
    all: 'Alle', sensors: 'Sensoren', actors: 'Aktoren', devices: 'Geräte',
    configuration: 'Konfiguration', config_sub: '[CONFIG]-Abschnitt von enoceanmqtt.conf - Änderungen gelten nach Neustart',
    config_restart: 'Für die meisten Einstellungen ist ein Neustart erforderlich.',
    save_config: 'Konfiguration speichern',
    col_name: 'Name', col_addr: 'Adresse / Sender', col_type: 'Typ', col_eep: 'EEP',
    col_status: 'Status', col_lastseen: 'Zuletzt gesehen', col_latest: 'Letzter Wert', col_rssi: 'RSSI',
    remove_device: 'Gerät entfernen', edit_device: 'Gerät bearbeiten',
    remove_title: 'Gerät entfernen?', remove_body: 'Sind Sie sicher, dass Sie dieses Gerät entfernen möchten?',
    cancel: 'Abbrechen', remove: 'Entfernen', save: 'Speichern', close: 'Schließen',
    graph_title: 'Werteverlauf', graph_nodata: 'Keine Daten.',
    footer_info: 'HA_enoceanmqtt Web-Konfigurator',
    select_eep: 'EEP auswählen…',
    st_online: 'Online', st_offline: 'Offline', st_never: 'Nie gesehen',
    actor: 'Aktor', sensor: 'Sensor', bidirectional: 'Bidirektional',
    no_actors: 'Keine Aktoren konfiguriert.', no_sensors: 'Keine Sensoren konfiguriert.', no_devices: 'Noch keine Geräte konfiguriert.',
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
    connected: 'verbunden',
    offline: 'offline',
    base_id: 'Basis-ID',
    eep_placeholder: 'A5-20-01, Temperatur, Schalter…',
    device_count: 'Geräte',
    eep_viewer: 'Im EEP-Viewer öffnen',
    err_remove_device: 'Gerät konnte nicht entfernt werden: ',
  },
  fr: {
    subtitle: 'Configurateur Web', gateway: 'Passerelle', mqtt: 'MQTT',
    teachin_title: 'Enseignement', teachin_desc: 'Démarrer l\'enseignement, puis déclencher l\'appareil à ajouter : appuyez sur son bouton d\'enseignement (ou utilisez simplement un interrupteur normal, par ex. un rocker F6 - il n\'a pas besoin de bouton d\'enseignement). L\'appareil est ajouté automatiquement. Les appareils utilisant un télégramme d\'apprentissage 4BS ou UTE sont reconnus avec leur EEP ; les appareils bidirectionnels sont acquittés.',
    teachin_hint: 'Si l\'EEP ne fait pas partie du télégramme (par ex. interrupteurs RPS/F6), une EEP par défaut pour le type d\'appareil est attribuée - vous pouvez l\'affiner avec le bouton Modifier. Le mode enseignement s\'arrête automatiquement après un appareil reçu.',
    start_teachin: 'Démarrer l\'enseignement', stop_teachin: 'Arrêter l\'enseignement',
    add_device: 'Ajouter un appareil', add_sub: 'Ajouter manuellement un capteur (émetteur), un actionneur (récepteur) ou un appareil bidirectionnel',
    add_sensor: 'Ajouter un capteur (émetteur)', add_actor: 'Ajouter un actionneur (récepteur)', add_bidir: 'Ajouter bidirectionnel',
    name: 'Nom', address: 'Adresse', sender_id: 'ID émetteur', eep_label: 'EEP (profil)',
    all: 'Tous', sensors: 'Capteurs', actors: 'Actionneurs', devices: 'Appareils',
    configuration: 'Configuration', config_sub: 'Section [CONFIG] de enoceanmqtt.conf - les modifications s\'appliquent après redémarrage',
    config_restart: 'Un redémarrage est nécessaire pour la plupart des réglages.',
    save_config: 'Enregistrer la configuration',
    col_name: 'Nom', col_addr: 'Adresse / Émetteur', col_type: 'Type', col_eep: 'EEP',
    col_status: 'État', col_lastseen: 'Vu pour la dernière fois', col_latest: 'Dernière valeur', col_rssi: 'RSSI',
    remove_device: 'Supprimer l\'appareil', edit_device: 'Modifier l\'appareil',
    remove_title: 'Supprimer l\'appareil ?', remove_body: 'Êtes-vous sûr de vouloir supprimer cet appareil ?',
    cancel: 'Annuler', remove: 'Supprimer', save: 'Enregistrer', close: 'Fermer',
    graph_title: 'Historique des valeurs', graph_nodata: 'Pas de données.',
    footer_info: 'HA_enoceanmqtt configurateur web',
    select_eep: 'Choisir un EEP…',
    st_online: 'En ligne', st_offline: 'Hors ligne', st_never: 'Jamais vu',
    actor: 'Actionneur', sensor: 'Capteur', bidirectional: 'Bidirectionnel',
    no_actors: 'Aucun actionneur configuré.', no_sensors: 'Aucun capteur configuré.', no_devices: 'Aucun appareil configuré.',
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
    connected: 'connecté',
    offline: 'hors ligne',
    base_id: 'ID de base',
    eep_placeholder: 'A5-20-01, température, interrupteur…',
    device_count: 'appareils',
    eep_viewer: 'Ouvrir dans la visionneuse EEP',
    err_remove_device: 'Échec de la suppression : ',
  },
  it: {
    subtitle: 'Configuratore Web', gateway: 'Gateway', mqtt: 'MQTT',
    teachin_title: 'Teach-In', teachin_desc: 'Avvia teach-in, poi attiva il dispositivo da aggiungere: premi il suo pulsante teach-in (o usa semplicemente un interruttore normale, es. un rocker F6 - non serve un pulsante teach-in). Il dispositivo viene aggiunto automaticamente. I dispositivi che usano un telegramma di apprendimento 4BS o UTE sono riconosciuti con la loro EEP; i dispositivi bidirezionali vengono confermati.',
    teachin_hint: 'Se l\'EEP non fa parte del telegramma (es. interruttori RPS/F6), viene assegnata una EEP predefinita per il tipo di dispositivo - puoi perfezionarla con il pulsante Modifica. La modalità teach-in si disattiva automaticamente dopo un dispositivo ricevuto.',
    start_teachin: 'Avvia teach-in', stop_teachin: 'Ferma teach-in',
    add_device: 'Aggiungi dispositivo', add_sub: 'Aggiungi manualmente un sensore (mittente), un attuatore (ricevitore) o un dispositivo bidirezionale',
    add_sensor: 'Aggiungi sensore (mittente)', add_actor: 'Aggiungi attuatore (ricevitore)', add_bidir: 'Aggiungi bidirezionale',
    name: 'Nome', address: 'Indirizzo', sender_id: 'ID mittente', eep_label: 'EEP (profilo)',
    all: 'Tutti', sensors: 'Sensori', actors: 'Attuatori', devices: 'Dispositivi',
    configuration: 'Configurazione', config_sub: 'Sezione [CONFIG] di enoceanmqtt.conf - le modifiche si applicano dopo il riavvio',
    config_restart: 'Per la maggior parte delle impostazioni è necessario un riavvio.',
    save_config: 'Salva configurazione',
    col_name: 'Nome', col_addr: 'Indirizzo / Mittente', col_type: 'Tipo', col_eep: 'EEP',
    col_status: 'Stato', col_lastseen: 'Ultimo visto', col_latest: 'Ultimo valore', col_rssi: 'RSSI',
    remove_device: 'Rimuovi dispositivo', edit_device: 'Modifica dispositivo',
    remove_title: 'Rimuovere il dispositivo?', remove_body: 'Sicuro di voler rimuovere questo dispositivo?',
    cancel: 'Annulla', remove: 'Rimuovi', save: 'Salva', close: 'Chiudi',
    graph_title: 'Cronologia valori', graph_nodata: 'Nessun dato.',
    footer_info: 'HA_enoceanmqtt configuratore web',
    select_eep: 'Seleziona un EEP…',
    st_online: 'Online', st_offline: 'Offline', st_never: 'Mai visto',
    actor: 'Attuatore', sensor: 'Sensore', bidirectional: 'Bidirezionale',
    no_actors: 'Nessun attuatore configurato.', no_sensors: 'Nessun sensore configurato.', no_devices: 'Nessun dispositivo configurato.',
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
    connected: 'connesso',
    offline: 'offline',
    base_id: 'ID base',
    eep_placeholder: 'A5-20-01, temperatura, interruttore…',
    device_count: 'dispositivi',
    eep_viewer: 'Apri nel visualizzatore EEP',
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
  const learnBtn = $('btn-learn-on');
  if (learnBtn) learnBtn.textContent = state.learn ? t('stop_teachin') : t('start_teachin');
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
$('btn-learn-on')?.addEventListener('click', () => setLearn(!state.learn));

/* ---------------- init ---------------- */
loadStatus();
setInterval(loadStatus, 2000);
