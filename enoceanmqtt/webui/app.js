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
  if (!res.ok) {
    // surface the backend reason (data.error/data.message) so e.g. a teach-in
    // rejection shows *why* instead of a bare 'Request failed (400)'
    const reason = data.error || data.message || ('Request failed (' + res.status + ')');
    const err = new Error(reason);
    err.status = res.status;
    // keep a machine-readable code so the UI can show a localized message
    err.error_code = data.error_code;
    err.section = data.section;
    throw err;
  }
  return data;
}

/* ---------------- load + render ---------------- */
/*
 * Config is unchanged by telegram traffic and only affects the (rare) edit
 * popup + tooltips, so we fetch it once on boot and then only every
 * CONFIG_REFRESH_MS - NOT on every 2 s status poll. This avoids a second
 * /api/config round-trip 30x/minute for zero benefit.
 */
const CONFIG_REFRESH_MS = 30000;
let _lastConfigFetch = 0;
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
    const now = Date.now();
    if (now - _lastConfigFetch > CONFIG_REFRESH_MS) {
      _lastConfigFetch = now;
      loadConfig();
    }
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
    mqttEl.className = 'pill ' + (gw.mqtt ? 'ok' : 'bad');
    // just the connection state - the details go into the tooltip
    mqttEl.innerHTML = '<span class="dot"></span>' + t('mqtt') + ' ' + (gw.mqtt ? t('connected') : t('disconnected'));
    if (gw.connected) {
      // show the gateway base ID only - stable and unambiguous. The port is
      // available in the hover tooltip.
      const gwAddr = gw.base_id || '—';
      gwEl.innerHTML = '<span class="dot"></span>' + t('gateway') + ' ' + escapeHtml(String(gwAddr));
    } else if (gw.discovering) {
      // booted without a gateway - the back end is hunting for local serial
      // dongles and mDNS ser2net endpoints right now
      gwEl.innerHTML = '<span class="dot"></span>' + t('gateway') + ' ' + t('searching_gateway');
    } else {
      gwEl.innerHTML = '<span class="dot"></span>' + t('gateway') + ' ' + t('disconnected');
    }
    // hover tooltips with configured settings + transceiver diagnostics
    const cfg = state.config || {};
    if (cfg.enocean_port) gwEl.setAttribute('data-tip', 'Port: ' + cfg.enocean_port + (cfg.log_packets !== undefined ? '\nLog packets: ' + cfg.log_packets : ''));
    const dg = (state.gateway && state.gateway.diagnostics) || {};
    if (dg.chip_id) {
      const extra = ['chip: ' + dg.chip_id]
        .concat(dg.app_version ? ['app ' + dg.app_version] : [])
        .concat(dg.repeater_level !== null && dg.repeater_level !== undefined ? ['repeater ' + dg.repeater_level] : [])
        .concat(dg.duty_cycle_available !== null && dg.duty_cycle_available !== undefined ? ['TX duty ' + dg.duty_cycle_available + '%'] : [])
        .concat(dg.transmit_failures ? ['TX fails ' + dg.transmit_failures] : []);
      const cur = gwEl.getAttribute('data-tip') || '';
      gwEl.setAttribute('data-tip', (cur ? cur + '\n' : '') + extra.join('\n'));
    }
    if (cfg.mqtt_host) mqttEl.setAttribute('data-tip', 'mqtt://' + cfg.mqtt_host + (cfg.mqtt_port ? ':' + cfg.mqtt_port : ''));
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
  const keys = Object.keys(l.values).filter((k) => !k.startsWith('_') && k !== 'LRN' && k !== 'LRNB');
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
    let keys = Object.keys(hist[hist.length - 1].values || {}).filter((k) => !k.startsWith('_') && k !== 'LRN' && k !== 'LRNB');
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
  if (!svg || pts.length < 1) return;
  const W = 600, H = 180, PADL = 60, PADR = 10, PADT = 14, PADB = 26, PW = W - PADL - PADR, PH = H - PADT - PADB;
  const vals = pts.map((p) => p.v);
  const min = Math.min.apply(null, vals), max = Math.max.apply(null, vals);
  const span = (max - min) || 1;
  const t0 = pts[0].t, t1 = pts[pts.length - 1].t || (t0 + 1);
  const X = (t) => PADL + (t - t0) / (t1 - t0) * PW;
  const Y = (v) => PADT + (1 - (v - min) / span) * PH;
  const fmtV = (v) => (Math.round(v * 100) / 100).toString();
  const fmtT = (t) => { const d = new Date(t); return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }); };
  const poly = pts.length > 1 
    ? '<polyline fill="none" stroke="var(--primary)" stroke-width="2" points="' + pts.map((p) => X(p.t).toFixed(1) + ',' + Y(p.v).toFixed(1)).join(' ') + '"/>'
    : '<circle cx="' + X(pts[0].t).toFixed(1) + '" cy="' + Y(pts[0].v).toFixed(1) + '" r="5" fill="var(--primary)"/>';
  svg.innerHTML = '<rect x="0" y="0" width="' + W + '" height="' + H + '" fill="none"/>' + poly +
    // Y axis labels (start = min at bottom, end = max at top), left-aligned
    '<text x="' + (PADL - 6) + '" y="' + (Y(max) + 4) + '" fill="var(--text)" font-size="18" font-weight="600" text-anchor="end">' + escapeHtml(fmtV(max)) + '</text>' +
    '<text x="' + (PADL - 6) + '" y="' + (Y(min) + 4) + '" fill="var(--text)" font-size="18" font-weight="600" text-anchor="end">' + escapeHtml(fmtV(min)) + '</text>' +
    // X axis time labels (start at left, end at right)
    '<text x="' + PADL + '" y="' + (H - 6) + '" fill="var(--text-muted)" font-size="18" font-weight="600">' + escapeHtml(fmtT(t0)) + '</text>' +
    '<text x="' + (W - PADR) + '" y="' + (H - 6) + '" fill="var(--text)" font-size="18" font-weight="600" text-anchor="end">' + escapeHtml(fmtT(t1)) + '</text>' +
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
    '<text x="' + PAD + '" y="' + (H - 2) + '" fill="var(--text-muted)" font-size="18">0</text>' +
    '<text x="' + PAD + '" y="' + (PAD + 8) + '" fill="var(--text-muted)" font-size="18">1</text>';
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
  // the config settings are used for the tooltips and the edit popup
  try {
    const data = await api('/api/config');
    state.config = data || {};
  } catch (e) {
    toast(t('err_load_config') + e.message, 'error');
  }
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

function openConfigEdit(filter) {
  const grid = $('config-grid');
  if (!grid || !state.config) return;
  const conf = state.config;
  let keys = Object.keys(conf).filter((k) => !CONFIG_HIDDEN.has(k));
  if (Array.isArray(filter) && filter.length) {
    // show only the relevant settings (MQTT / EnOcean gateway)
    const f = filter.map(s => s.toLowerCase());
    keys = keys.filter((k) => f.some((p) => k.toLowerCase().includes(p)));
  }
  grid.classList.add('config-two-col');
  grid.innerHTML = keys.map((k) => {
    const label = configLabel(k);
    const raw = conf[k];
    const helpKey = 'help_config_' + k;
    const helpIcon = '<span class="help-icon" data-help="' + escapeHtml(helpKey) + '">ⓘ</span>';
    if (isBoolConf(k, raw)) {
      const checked = ['1', 'true', 'yes', 'on'].includes(String(raw == null ? '' : raw).trim().toLowerCase());
      return '<div class="field config-bool"><label for="cfg-' + escapeHtml(k) + '">' + escapeHtml(label) + helpIcon + '</label>' +
        '<input type="checkbox" id="cfg-' + escapeHtml(k) + '" data-cfgkey="' + escapeHtml(k) + '"' + (checked ? ' checked' : '') + '>' +
        '<input type="hidden" data-cfgkey="' + escapeHtml(k) + '" data-boolhidden="' + escapeHtml(k) + '" value="' + (checked ? '1' : '0') + '"></div>';
    }
    return '<div class="field"><label for="cfg-' + escapeHtml(k) + '">' + escapeHtml(label) + helpIcon + '</label>' +
      '<input type="text" id="cfg-' + escapeHtml(k) + '" data-cfgkey="' + escapeHtml(k) + '" value="' + escapeHtml(String(raw)) + '"></div>';
  }).join('');
  // fill the help tooltips for the freshly generated config fields
  grid.querySelectorAll('[data-help]').forEach((el) => {
    const key = el.getAttribute('data-help');
    const tip = t(key);
    if (tip && tip !== key) el.setAttribute('data-tip', tip);
  });
  // bind checkbox change -> set hidden value (so configPayloadFromGrid reads it)
  grid.querySelectorAll('input[type=checkbox][data-cfgkey]').forEach((cb) => {
    cb.addEventListener('change', () => {
      const hidden = grid.querySelector('input[data-boolhidden="' + cb.getAttribute('data-cfgkey') + '"]');
      if (hidden) hidden.value = cb.checked ? '1' : '0';
    });
  });
  $('configedit-overlay').hidden = false;
}
$('btn-top-config')?.addEventListener('click', () => openConfigEdit());
// MQTT status pill -> MQTT settings popup
$('mqtt-status')?.addEventListener('click', () => openConfigEdit(['mqtt']));
$('mqtt-status')?.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); openConfigEdit(['mqtt']); } });
// Gateway status pill -> EnOcean gateway settings popup
$('gw-status')?.addEventListener('click', () => openConfigEdit(['enocean']));
$('gw-status')?.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); openConfigEdit(['enocean']); } });
// auto-search for an EnOcean gateway while disconnected (no separate button).
// Runs on boot and then every 5 s so a dongle plugged in later, or a ser2net
// endpoint that appears on the network, is picked up automatically. When the
// configured port is empty and a candidate is found, it is pre-filled (and
// saved, so it survives a restart).
const GW_DISCOVERY_MS = 5000;
let _gwDiscoveryTimer = null;

function _foundEndpoint(data) {
  // pick the best candidate: a real mDNS endpoint (host+port) beats a
  // generic serial candidate; never return an unusable entry.
  const mdns = (data.mdns || []).find((m) => m.host && m.port);
  if (mdns) return 'tcp:' + mdns.host + ':' + mdns.port;
  const ser = (data.serial || []).find((s) => s.candidate && s.device);
  if (ser) return ser.device;
  return null;
}

function _fillConfigPort(value) {
  // pre-fill the on-screen enocean_port field if the config modal is open
  const field = Array.from(document.querySelectorAll('#config-grid [data-cfgkey="enocean_port"]'))[0];
  if (field) field.value = value;
}

function renderDiscoveryBanner(data) {
  const banner = $('discovery-banner');
  const text = $('discovery-banner-text');
  const serialCount = $('discovery-serial-count');
  if (!banner || !text) return;
  const found = _foundEndpoint(data || {});
  const serials = (data && data.serial || []).filter((s) => s.candidate && s.device);
  const isConnected = !!(state.gateway && state.gateway.connected);
  // A port is already configured -> the banner would be noise. Only offer
  // discovered gateways while the user still has to pick one (or the port
  // is empty); the gateway pill already shows that nothing is connected.
  const hasConfiguredPort = _portConfigured();
  // hide when connected, a port is already configured, nothing found, or
  // the user dismissed this result
  if (isConnected || hasConfiguredPort || !found || _discoveryDismissed === found) {
    banner.hidden = true;
    return;
  }
  text.textContent = t('discovery_found') + ' ' + found;
  if (serials.length > 1 || (serials.length === 1 && serials[0].device !== found)) {
    serialCount.textContent = t('discovery_serial') + ': ' + serials.length;
    serialCount.hidden = false;
  } else {
    serialCount.hidden = true;
  }
  banner.hidden = false;
}

async function autoDiscover() {
  if (state.gateway && state.gateway.connected) return;   // already connected
  let data = null;
  try {
    data = await api('/api/discovery');
  } catch (e) { /* back end busy / unreachable - retry next tick */ }
  if (!data || (state.gateway && state.gateway.connected)) return;
  const found = _foundEndpoint(data);
  if (!found) { renderDiscoveryBanner(data); return; }

  // show the persistent banner with a "Use this gateway" action
  renderDiscoveryBanner(data);

  // keep the config modal pre-filled, but do NOT write config on our own -
  // the user clicks "Use this gateway" for that (no silent takeover).
  if (!_portConfigured()) {
    _fillConfigPort(found);
    toast(t('discovery_found') + ' ' + found, 'success');
  }
}
function _portConfigured() {
  // whether the user has already chosen an EnOcean port (config set + saved)
  return !!(state.config && String(state.config.enocean_port || '').trim());
}

(function gwDiscoveryLoop() {
  const tick = async () => {
    // Once a port is configured we stop hunting entirely: the banner must
    // never resurface, and poking mDNS/serial every 5s is pointless. The
    // gateway pill already shows (dis)connected state.
    if (_portConfigured() || (state.gateway && state.gateway.connected)) {
      renderDiscoveryBanner({});   // hide banner (configured / connected)
    } else {
      await autoDiscover();
    }
    _gwDiscoveryTimer = setTimeout(tick, GW_DISCOVERY_MS);
  };
  tick();
})();
$('discovery-use')?.addEventListener('click', async () => {
  const data = await api('/api/discovery');
  const found = _foundEndpoint(data || {});
  if (!found) { toast(t('discovery_none'), 'error'); return; }
  try {
    const res = await api('/api/config', { method: 'POST', body: JSON.stringify({ enocean_port: found }) });
    if (!res.ok) throw new Error(res.error || 'save failed');
    state.config = state.config || {};
    state.config.enocean_port = found;
    _fillConfigPort(found);
    $('discovery-banner').hidden = true;
    _discoveryDismissed = found;
    configSavedToast(res.restart_required === true);
    // (re)connect now - the backend applies the port live
    try { await api('/api/restart'); } catch (e) { /* backend applies live */ }
  } catch (e) {
    toast(t('err_save_config') + ' ' + e.message, 'error');
  }
});
let _discoveryDismissed = null;
$('discovery-dismiss')?.addEventListener('click', () => {
  const banner = $('discovery-banner');
  if (banner) banner.hidden = true;
  _discoveryDismissed = 'dismissed';
});
$('configedit-cancel')?.addEventListener('click', () => { $('configedit-overlay').hidden = true; });


function configSavedToast(restartRequired) {
  // settings that _apply_live_config() handles need no restart; the backend
  // tells us via the restart_required flag it computes in save_config()
  toast(restartRequired ? t('config_saved') : t('config_saved_live'), 'success');
}

function configPayloadFromGrid() {
  const payload = {};
  document.querySelectorAll('#config-grid [data-cfgkey]').forEach((inp) => {
    if (inp.type === 'checkbox') return; // handled by the hidden sibling
    payload[inp.getAttribute('data-cfgkey')] = inp.value;
  });
  return payload;
}
async function saveConfigPayload(payload) {
  try {
    const res = await api('/api/config', { method: 'POST', body: JSON.stringify(payload) });
    if (!res.ok) throw new Error(res.error || 'save failed');
    configSavedToast(res.restart_required === true);
    $('configedit-overlay').hidden = true;
    state.config = Object.assign({}, state.config, payload);
  } catch (err) {
    toast(t('err_save_config') + err.message, 'error');
  }
}
$('configedit-save')?.addEventListener('click', () => saveConfigPayload(configPayloadFromGrid()));

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
    let addrHtml;
    if (isActor) {
      addrHtml = fmtAddr(s.sender);
    } else if (s.bidirectional) {
      addrHtml = fmtAddr(s.address) + '<div style="color:var(--text-muted);font-size:11px">' + t('send_label') + ' ' + fmtAddr(s.sender) + '</div>';
    } else {
      addrHtml = fmtAddr(s.address);
    }
    const source = s.source === 'dynamic' ? ' <span class="pill" style="font-size:10px;padding:1px 6px">web</span>' : '';
    // show the friendly name when present (spaces etc.); s.name stays the
    // sanitized lookup key used by the row buttons / API calls
    const name = escapeHtml(s.friendly_name || s.name);
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
        ${isActor ? '<button class="icon-btn teachin-btn" title="Send teach-in telegram to this actor" data-teachin="' + escapeHtml(s.name) + '">⤓</button>' : ''}
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
  // The edit field holds the friendly name (spaces, the form the user
  // originally typed). Slashes map to spaces, and the backend maps them
  // back to '/' when storing the MQTT topic base, so the broker grouping
  // is preserved losslessly on save while the field stays readable.
  $('e-name').value = s.friendly_name || s.name;
  updateEntityPreview('e-name', 'e-name-preview');
  $('e-address').value = (s.address !== undefined && s.address !== null && s.address !== 0xFFFFFFFF)
    ? fmtAddr(s.address, true) : '';
  $('e-eep-search').value = s.eep || '';
  // sensor=Name/Address/EEP; actor=Name/Sender/EEP; bidirectional=Name/Address/Sender/EEP
  const showAddr = cat !== 'actor';
  const showSender = cat !== 'sensor';
  $('e-addr-field').hidden = !showAddr;
  $('e-sender-field').hidden = !showSender;
  populateSenders($('e-sender'), state.virtual_senders, s.sender);
  if (s.sender !== undefined && s.sender !== null) {
    const selE = $('e-sender');
    if (selE) selE.value = fmtAddr(s.sender);
  }
  populateEepDatalist('e-eep-list', cat);
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
  const eepInput = $('e-eep-search').value.trim();
  const eep = resolveEep(eepInput);
  if (!isValidName(name)) return toast(t('err_bad_name'), 'error');
  if (!eep) return toast(t('err_no_eep'), 'error');
  // the name keeps '/' (MQTT topic grouping); the backend stores it as the
  // topic base and derives the HA friendly name (slash -> space) + entity id.
  // Only send the name when it actually changed - editing just the EEP or
  // address must not silently rewrite an existing pretty friendly name.
  const mqtt = slugifyMqttName(name);
  if (!mqtt) return toast(t('err_bad_name'), 'error');
  const body = { eep: eep };
  if (mqtt !== editingDevice) { body.friendly_name = name; body.name = mqtt; }
  const addrVal = $('e-address').value.trim();
  const address = parseAddress(addrVal);
  const cat = deviceCategory(state.sensors.find((x) => x.name === editingDevice) || {});
  if (cat === 'sensor') {
    // sensors must keep a valid (non-broadcast) device address
    if (address === null || address === 0xFFFFFFFF) return toast(t('err_bad_address'), 'error');
    body.address = address;
  } else if (addrVal) {
    // actor/bidirectional: address optional; if given it must parse
    if (address === null) return toast(t('err_bad_address'), 'error');
    body.address = address;
  }
  // actor/bidirectional: sender + settings
  if (cat !== 'sensor') {
    const sv = parseSender($('e-sender').value);
    // for actors a sender is required; for bidirectional it is optional
    if (cat === 'actor') {
      if (sv === null || sv === undefined || isNaN(sv)) return toast(t('err_no_sender'), 'error');
      body.sender = sv;
    } else if (sv !== null && sv !== undefined && !isNaN(sv)) {
      body.sender = sv;
    }
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
    toast(t('err_update_device') + friendlyError(err), 'error');
  }
});

async function sendTeachIn(name) {
  try {
    const res = await api('/api/teachin', { method: 'POST', body: JSON.stringify({ name: name }) });
    if (res.ok) {
      toast(t('teachin_sent'), 'success');
    } else {
      toast(t('err_send_teachin') + (res.message || t('teachin_failed')), 'error');
    }
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
    if (mode === 'sensor') return p.category !== 'bidirectional';
    // every EEP can be used as an actor - we can simulate any EEP (even a
    // sensor one) by sending it from a virtual sender.
    if (mode === 'actor') return true;
    if (mode === 'bidirectional') return p.category === 'bidirectional';
    return true;
  };
  _eepOpts = (state.eep || []).filter(catFilter);
  renderEepList(ul, '');
}
function renderEepList(ul, q) {
  if (!ul.children.length) {
    ul.innerHTML = _eepOpts.map((p) =>
      '<li data-eep="' + escapeHtml(p.eep) + '" data-name="' + escapeHtml(p.name) + '" data-aliases="' + escapeHtml((p.aliases || []).join(' ')) + '" title="' + escapeHtml(translateEepName(p.name)) + '">' +
        '<span class="eep-code">' + escapeHtml(p.eep) + '</span>' +
        '<span class="eep-name">' + escapeHtml(translateEepName(p.name)) + '</span></li>').join('');
  }
  const query = (q || '').toLowerCase();
  let count = 0;
  ul.querySelectorAll('li').forEach((li) => {
    const eep = li.getAttribute('data-eep').toLowerCase();
    const name = li.getAttribute('data-name').toLowerCase();
    const aliases = (li.getAttribute('data-aliases') || '').toLowerCase();
    const match = !query || eep.includes(query) || name.includes(query) || aliases.includes(query);
    li.hidden = !match;
    if (match) count++;
  });
  return count;
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

/* ---------------- custom select (sender ID, language) ----------------
   Native <select> is kept in the DOM (hidden) so all existing code that
   reads/writes sel.value or re-renders sel.innerHTML keeps working. The
   visible trigger + dropdown list mirror the native options and stay in
   sync both ways (MutationObserver catches populateSenders() re-renders).
   Visual language mirrors the EEP combobox (.eep-combo / .eep-dropdown). */
function initCustomSelect(selectId) {
  const sel = $(selectId);
  if (!sel || sel.dataset.csInit === '1') return;
  sel.dataset.csInit = '1';

  const wrap = document.createElement('div');
  wrap.className = 'custom-select' + (selectId === 'lang-select' ? ' cs-lang-select' : ' cs-sender-select');
  sel.parentNode.insertBefore(wrap, sel);

  const trigger = document.createElement('button');
  trigger.type = 'button';
  trigger.className = 'cs-trigger';
  trigger.setAttribute('aria-haspopup', 'listbox');
  wrap.appendChild(trigger);

  const list = document.createElement('ul');
  list.className = 'cs-list';
  list.setAttribute('role', 'listbox');
  wrap.appendChild(list);

  // hide the native select (kept for value/change + innerHTML re-renders)
  sel.classList.add('cs-native');
  sel.setAttribute('aria-hidden', 'true');
  sel.tabIndex = -1;

  /* update trigger text when the native value changes externally */
  const syncTrigger = () => {
    const o = sel.selectedOptions && sel.selectedOptions[0];
    trigger.textContent = (o ? o.textContent : sel.value) || '—';
  };

  /* rebuild <li> list + trigger from the current <option>s */
  const rebuild = () => {
    const cur = sel.value;
    const opts = Array.from(sel.querySelectorAll('option'));
    list.innerHTML = opts.map((o) => {
      const label = o.textContent;
      const dis = o.disabled;
      const cls = dis ? ' disabled' : (o.value === cur || o.selected ? ' selected' : '');
      return '<li data-value="' + escapeHtml(o.value) + '" class="' + cls.trim() + '">' +
        '<span>' + escapeHtml(label) + '</span></li>';
    }).join('');
    syncTrigger();
  };
  const open = () => { syncTrigger(); list.hidden = false; };
  const close = () => { list.hidden = true; };
  syncTrigger();
  rebuild();
  close();

  /* re-render when populateSenders() replaces sel.innerHTML */
  const mo = new MutationObserver(() => rebuild());
  mo.observe(sel, { childList: true, subtree: true, attributes: true, attributeFilter: ['disabled', 'selected'] });
  sel.addEventListener('change', syncTrigger);

  trigger.addEventListener('click', (e) => {
    e.stopPropagation();
    if (list.hidden) open(); else close();
  });
  trigger.addEventListener('keydown', (e) => {
    if (e.key === 'ArrowDown' || e.key === 'Enter' || e.key === ' ') {
      e.preventDefault(); open();
      const first = list.querySelector('li:not(.disabled)');
      if (first) first.focus();
    } else if (e.key === 'Escape') {
      close(); trigger.focus();
    }
  });
  list.addEventListener('click', (e) => {
    const li = e.target.closest('li');
    if (!li || li.classList.contains('disabled')) return;
    sel.value = li.dataset.value;
    sel.dispatchEvent(new Event('change', { bubbles: true }));
    rebuild();
    close();
    trigger.focus();
  });
  list.addEventListener('keydown', (e) => {
    const items = Array.from(list.querySelectorAll('li:not(.disabled)'));
    if (!items.length) return;
    const i = items.indexOf(e.target);
    if (e.key === 'ArrowDown') { e.preventDefault(); items[(i + 1) % items.length].focus(); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); items[(i - 1 + items.length) % items.length].focus(); }
    else if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); e.target.click(); }
    else if (e.key === 'Escape') { e.preventDefault(); close(); trigger.focus(); }
  });
  // close on outside click / Esc anywhere once open
  document.addEventListener('click', (e) => {
    if (!wrap.contains(e.target)) close();
  }, true);
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && !list.hidden) { e.preventDefault(); close(); trigger.focus(); }
  });
  wrap.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') { close(); trigger.focus(); }
  });
}

function resolveEep(value) {
  // Only accept an EEP from the catalog - a free-form 'XX-YY-ZZ' that is not
  // in state.eep is NOT a valid equipment profile and must be rejected.
  // Search matches the EEP code AND the description/name (substring, e.g.
  // typing "temperature" lists every temperature profile).
  const v = (value || '').trim();
  if (!v) return null;
  const vl = v.toLowerCase();
  const norm = v.replace(/[-:]/g, '').toLowerCase();
  const hits = (state.eep || []).filter((p) =>
    p.eep.toLowerCase() === vl ||
    p.eep.replace(/[-:]/g, '').toLowerCase() === norm ||
    (p.name && p.name.toLowerCase().includes(vl)) ||
    (p.aliases && p.aliases.some((a) => String(a).toLowerCase() === vl || String(a).toLowerCase().includes(vl))));
  if (!hits.length) return null;
  // exact EEP code wins; otherwise the first (best) description match
  const exact = hits.find((p) => p.eep.toLowerCase() === vl || p.eep.replace(/[-:]/g, '').toLowerCase() === norm);
  return (exact || hits[0]).eep;
}

// The user-facing device name ("friendly name") is free text: spaces and
// most printable characters are allowed (e.g. "Living Room Temp").
// The Home Assistant entity_id / MQTT topic base is derived from it by
// slugifyEntityId() and must not be empty.
function isValidName(name) {
  const n = String(name || '').trim();
  return n.length > 0;
}

// Mirror of Home Assistant's slugify() (separator '_'): lowercase, any
// character outside [a-z0-9_-] becomes '_', runs collapse, edges trimmed.
function slugifyEntityId(name) {
  // Home Assistant entity_ids allow only [a-z0-9_], so '-' and anything
  // else outside that set becomes '_' here too.
  let s = String(name || '').trim().toLowerCase();
  s = s.replace(/[^a-z0-9_]+/g, '_');
  s = s.replace(/_+/g, '_').replace(/^_+|_+$/g, '');
  return s;
}

// The stored MQTT topic base: lowercase, '/' KEPT (it groups the sensor in
// the broker), spaces and anything else outside [a-z0-9_/] become '_',
// '_' hugging '/' is dropped so 'a / b' -> 'a/b'.
// "Lights/Kitchen Temp" -> "lights/kitchen_temp".
function slugifyMqttName(name) {
  let s = String(name || '').trim().toLowerCase();
  s = s.replace(/[^a-z0-9_/]+/g, '_');
  s = s.replace(/_+/g, '_');
  s = s.replace(/_\/+/g, '/').replace(/\/_+/g, '/');
  s = s.replace(/\/{2,}/g, '/');
  return s.replace(/^[_\ /]+|[_\ /]+$/g, '');
}

// The Home Assistant friendly name for a typed name: the typed name is
// preserved verbatim (slashes kept - they are part of the name). Only
// whitespace is trimmed/collapsed. "test/test test" -> "test/test test".
function friendlyFromMqtt(name) {
  return String(name || '').replace(/ +/g, ' ').trim();
}

// The entity_id the device will get in Home Assistant: 'e2m_' + the
// slugified friendly name (entity-level keys get the field appended).
function entityIdPreview(name, field) {
  let slug = slugifyEntityId(name);
  if (!slug) return 'e2m_...';
  const base = 'e2m_' + slug;
  return base + (field ? '_' + field : '');
}

// Show the Home Assistant friendly name + entity_id that will be derived
// from the typed name in a <small> preview element next to the name input.
// The typed name may contain '/' (MQTT topic grouping): it becomes a space
// in the HA friendly name and stays / becomes '_' in the entity id.
function updateEntityPreview(inputId, previewId) {
  const input = $(inputId);
  const preview = $(previewId);
  if (!input || !preview) return;
  const name = input.value.trim();
  if (!name) {
    preview.textContent = '';
    return;
  }
  const friendly = friendlyFromMqtt(name);
  const entity = entityIdPreview(friendly);
  preview.textContent = '→ HA: ' + friendly + ' · e2m: ' + entity;
  preview.title = 'Home Assistant friendly name · entity id';
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
function parseSender(val) {
  // sender IDs are colon-formatted (e.g. '89:AB:CD:EF'); reuse the address
  // parser so the full 32-bit value is kept (parseInt would truncate at ':')
  return parseAddress(val);
}
function defaultActorName(sender) {
  // Auto-generate a stable, unique name when the user left the name empty so
  // saving + teach-in still work (the backend requires a non-empty name).
  // Use the sender ID when available (readable, stable), else a timestamp hex.
  if (sender !== null && sender !== undefined && !isNaN(sender)
      && Number(sender) >= 0 && Number(sender) <= 0xFFFFFFFF) {
    return 'actor_' + (Number(sender) >>> 0).toString(16).padStart(8, '0');
  }
  return 'actor_' + Date.now().toString(16);
}
function fmtAddr(v, emptyForNull = false) {
  if (v === undefined || v === null) return emptyForNull ? '' : '—';
  const n = Number(v);
  if (isNaN(n)) return String(v);
  const b = [(n >> 24) & 0xff, (n >> 16) & 0xff, (n >> 8) & 0xff, n & 0xff];
  return b.map((x) => x.toString(16).toUpperCase().padStart(2, '0')).join(':');
}

let addMode = 'sensor';

function populateSenders(selectOrList, listOrSelect, selected) {
  // accepts either (select, senders) or (senders) - the latter refreshes all
  // sender dropdowns; selected (optional) preselects an option in |target|
  let list, target;
  if (Array.isArray(selectOrList)) {
    list = selectOrList;
    target = (listOrSelect && listOrSelect.tagName === 'SELECT') ? listOrSelect : null;
  } else {
    target = (selectOrList && selectOrList.tagName === 'SELECT') ? selectOrList : null;
    list = Array.isArray(listOrSelect) ? listOrSelect
      : (Array.isArray(state && state.virtual_senders) ? state.virtual_senders : []);
  }
  const targets = target
    ? [target]
    : ['f-sender', 'aa-sender', 'ab-sender', 'e-sender'].map((id) => $(id)).filter(Boolean);
  targets.forEach((t) => fillSenderSelect(t, list, t === target ? selected : undefined));
}
function fillSenderSelect(sel, list, selected) {
  if (!sel) return;
  const used = new Set(state.sensors.filter((s) => s.sender).map((s) => s.sender));
  const opts = list.map((v) => {
    const hex = fmtAddr(v);
    const isUsed = used.has(v);
    const isSel = (selected !== undefined && selected !== null && Number(selected) === v);
    return '<option value="' + hex + '"' + (isUsed && !isSel ? ' disabled' : '') + (isSel ? ' selected' : '') + '>' + hex + (isUsed && !isSel ? ' (used)' : '') + '</option>';
  });
  sel.innerHTML = opts.length ? opts.join('') : '<option value="">(no sender IDs available)</option>';
  sel.disabled = false;
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
  if (asTeachInActive) {
    asStopTeachIn('');
    $('as-prompt').hidden = true;
  }
}
async function enableTeachIn() {
  await api('/api/learn', { method: 'POST', body: JSON.stringify({ enabled: true }) });
}
let asTeachInActive = false;
function asStopTeachIn(message) {
  asTeachInActive = false;
  api('/api/teachin/capture/stop', { method: 'POST', body: JSON.stringify({}) }).catch(() => {});
  const btn = $('as-teachin-btn');
  if (btn) {
    btn.classList.remove('stopping');
    btn.textContent = t('start_teachin');
    btn.setAttribute('data-i18n', 'start_teachin');
  }
  if (message) $('as-prompt-state').textContent = message;
}
$('as-teachin-btn')?.addEventListener('click', async () => {
  if (asTeachInActive) {
    // already capturing -> cancel
    asStopTeachIn('Teach-in cancelled.');
    $('as-prompt').hidden = true;
    return;
  }
  asTeachInActive = true;
  const btn = $('as-teachin-btn');
  if (btn) {
    btn.classList.add('stopping');
    btn.textContent = t('stop_teachin');
    btn.setAttribute('data-i18n', 'stop_teachin');
  }
  $('as-prompt').hidden = false;
  $('as-prompt-state').textContent = t('teachin_active');
  // capture-only teach-in: fills the dialog, does NOT add the device yet
  await api('/api/teachin/capture', { method: 'POST', body: JSON.stringify({}) });
  for (let i = 0; i < 90; i++) {
    await new Promise((r) => setTimeout(r, 1000));
    if (!asTeachInActive) return; // cancelled during wait
    const cap = await api('/api/teachin/captured');
    if (cap && cap.ok && cap.device) {
      const d = cap.device;
      $('as-name').value = 'learn_' + Number(d.address).toString(16).toLowerCase();
      $('as-address').value = fmtAddr(d.address, true);
      $('as-eep').value = d.eep || '';
      asStopTeachIn('');
      $('as-prompt').hidden = true; $('as-success').hidden = false;
      toast(t('sensor_detected'), 'success');
      return;
    }
  }
  asStopTeachIn('No telegram received.');
});
$('as-cancel')?.addEventListener('click', () => { $('addsensor-overlay').hidden = true; asReset(); });
$('as-save')?.addEventListener('click', async () => {
  const name = $('as-name').value.trim();
  const address = parseAddress($('as-address').value);
  const eep = resolveEep($('as-eep').value);
  if (!isValidName(name)) return toast(t('err_bad_name'), 'error');
  if (address === null) return toast(t('err_bad_address'), 'error');
  if (!eep) return toast(t('err_no_eep'), 'error');
  const res = await api('/api/sensors', { method: 'POST', body: JSON.stringify({ friendly_name: name, name: slugifyMqttName(name), address, eep, category: 'sensor', virtual: 0 }) });
  if (!res.ok) return toast(t('err_add_device') + (res.error || ''), 'error');
  toast(t('sensor_added'), 'success');
  $('addsensor-overlay').hidden = true; asReset();
  await loadStatus();
});

/* ---- Actor modal ---- */
$('aa-cancel')?.addEventListener('click', () => { $('addactor-overlay').hidden = true; });
$('aa-teachin')?.addEventListener('click', async () => {
  const sender = parseSender($('aa-sender').value);
  const eep = resolveEep($('aa-eep').value);
  if (sender === null || isNaN(sender)) return toast(t('err_no_sender'), 'error');
  if (!eep) return toast(t('err_no_eep'), 'error');
  // "Send teach-in" must NOT create a device - only transmit the telegram.
  // The actor learns the gateway from a 4BS/U TE teach-in sent on the chosen
  // virtual sender; it can be added later via "Save".
  const res = await api('/api/teachin', { method: 'POST', body: JSON.stringify({ sender, eep, address: 0xFFFFFFFF, category: 'actor' }) });
  if (!res.ok) return toast(t('err_send_teachin') + (res.message || t('teachin_failed')), 'error');
  toast(t('teachin_sent'), 'success');
});
$('aa-save')?.addEventListener('click', async () => {
  let name = $('aa-name').value.trim();
  const sender = parseSender($('aa-sender').value);
  const eep = resolveEep($('aa-eep').value);
  if (sender === null || isNaN(sender)) return toast(t('err_no_sender'), 'error');
  if (!eep) return toast(t('err_no_eep'), 'error');
  if (!name) name = defaultActorName(sender);
  if (!isValidName(name)) return toast(t('err_bad_name'), 'error');
  const res = await api('/api/sensors', { method: 'POST', body: JSON.stringify({ friendly_name: name, name: slugifyMqttName(name), address: 0xFFFFFFFF, eep, sender, category: 'actor', virtual: 1 }) });
  if (!res.ok) return toast(t('err_add_device') + (res.error || ''), 'error');
  toast(t('actor_added'), 'success');
  $('addactor-overlay').hidden = true;
  await loadStatus();
});

/* ---- Bidirectional modal ---- */
$('ab-cancel')?.addEventListener('click', () => { $('addbidir-overlay').hidden = true; });
let abTeachInActive = false;
function abStopTeachIn(message) {
  abTeachInActive = false;
  api('/api/learn', { method: 'POST', body: JSON.stringify({ enabled: false }) }).catch(() => {});
  const btn = $('ab-teachin');
  if (btn) {
    btn.classList.remove('stopping');
    btn.textContent = t('start_teachin');
    btn.setAttribute('data-i18n', 'start_teachin');
  }
  if (message) $('ab-hint').querySelector('p').textContent = message;
}
$('ab-teachin')?.addEventListener('click', async () => {
  if (abTeachInActive) {
    // already in teach-in mode -> cancel
    abStopTeachIn('Teach-in cancelled.');
    return;
  }
  abTeachInActive = true;
  const btn = $('ab-teachin');
  if (btn) {
    btn.classList.add('stopping');
    btn.textContent = t('stop_teachin');
    btn.setAttribute('data-i18n', 'stop_teachin');
  }
  $('ab-hint').querySelector('p').textContent = t('teachin_active');
  await enableTeachIn();
  for (let i = 0; i < 60; i++) {
    await new Promise((r) => setTimeout(r, 1000));
    if (!abTeachInActive) return; // cancelled during wait
    const known = new Set(state.sensors.map((s) => s.name));
    const data = await api('/api/status');
    const fresh = (data.sensors || []).find((s) => s.name.startsWith('enoceanmqtt/learn_') && !known.has(s.name));
    if (fresh) {
      $('ab-name').value = fresh.name.replace(/^.*\/learn_/, 'learn_');
      $('ab-address').value = fmtAddr(fresh.address, true);
      $('ab-eep').value = fresh.eep || '';
      abStopTeachIn('Device detected - confirm details.');
      toast('Bidirectional device detected', 'success');
      return;
    }
  }
  abStopTeachIn('No telegram received.');
});
$('ab-save')?.addEventListener('click', async () => {
  let name = $('ab-name').value.trim();
  const address = parseAddress($('ab-address').value);
  const sender = parseSender($('ab-sender').value);
  const eep = resolveEep($('ab-eep').value);
  if (address === null) return toast(t('err_bad_address'), 'error');
  if (sender === null || isNaN(sender)) return toast(t('err_no_sender'), 'error');
  if (!eep) return toast(t('err_no_eep'), 'error');
  if (!name) name = defaultActorName(sender);
  if (!isValidName(name)) return toast(t('err_bad_name'), 'error');
  const res = await api('/api/sensors', { method: 'POST', body: JSON.stringify({ friendly_name: name, name: slugifyMqttName(name), address, eep, sender, category: 'bidirectional', virtual: 1, direction: 1, answer: 1 }) });
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
    toast(t('err_remove_device') + friendlyError(err), 'error');
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
function friendlyError(err) {
  // backend errors come with a machine-readable code; translate when known,
  // otherwise fall back to the backend-provided English text.
  if (err && err.error_code === 'config_file_sensor') {
    const section = err.section || '';
    const key = 'err_config_file_sensor';
    const localized = langDict(currentLang) && langDict(currentLang)[key];
    if (localized) return localized.replace('{section}', section);
  }
  return err ? err.message : '';
}
function applyTranslations() {
  document.querySelectorAll('[data-i18n]').forEach((el) => {
    // elements set dynamically must never be overwritten by translations
    if (el.id === 'graph-body' || el.id === 'modal-title' || el.id === 'modal-body') return;
    const key = el.getAttribute('data-i18n');
    if (key && t(key)) {
      // Preserve any .help-icon child (it carries the field description) -
      // setting textContent directly would wipe it out.
      const helpIcons = Array.from(el.querySelectorAll('.help-icon'))
        .map((h) => ({ help: h.getAttribute('data-help'), tip: h.getAttribute('data-tip'), cls: h.className, ch: h.textContent }));
      el.textContent = t(key);
      helpIcons.forEach((hi) => {
        const span = document.createElement('span');
        span.className = hi.cls; span.setAttribute('data-help', hi.help);
        if (hi.tip) span.setAttribute('data-tip', hi.tip);
        span.textContent = hi.ch;
        el.appendChild(span);
      });
    }
  });
  document.querySelectorAll('[data-ph]').forEach((el) => {
    const key = el.getAttribute('data-ph');
    if (key && t(key)) el.setAttribute('placeholder', t(key));
  });
  document.querySelectorAll('.eep-info').forEach((el) => {
    el.title = t('eep_viewer');
  });
  // help icons: data-help holds a translation key; the tooltip (data-tip) is
  // filled with the translated description.
  document.querySelectorAll('[data-help]').forEach((el) => {
    const key = el.getAttribute('data-help');
    const tip = t(key);
    if (tip && tip !== key) el.setAttribute('data-tip', tip);
  });
}
$('lang-select')?.addEventListener('change', (e) => setLang(e.target.value));
// live entity-id preview under the name inputs (edit + add device modals)
['e-name', 'as-name', 'aa-name', 'ab-name'].forEach((id) => {
  $(id)?.addEventListener('input', () => {
    const previewId = id + '-preview';
    updateEntityPreview(id, previewId);
  });
});
// collapsible cards (e.g. Configuration)
document.querySelectorAll('.card.collapsible > .card-header').forEach((h) => {
  h.addEventListener('click', () => {
    h.parentElement.classList.toggle('collapsed');
  });
});
(function () {
  try {
    const saved = localStorage.getItem('enm-lang');
    if (saved === 'de' || saved === 'fr' || saved === 'it' || saved === 'en') {
      currentLang = saved;
    } else {
      // no cached language -> detect from the browser (e.g. 'de-DE' -> 'de')
      const nav = (navigator.language || navigator.languages?.[0] || 'en').toLowerCase();
      currentLang = (nav.startsWith('de') || nav.startsWith('fr') || nav.startsWith('it')) ? nav.slice(0, 2) : 'en';
      try { localStorage.setItem('enm-lang', currentLang); } catch (e) { /* ignore */ }
    }
    const sel = $('lang-select');
    if (sel) sel.value = currentLang;
  } catch (e) { /* ignore */ }
  // translate immediately so field labels + help icons get their tooltips on first paint
  applyTranslations();
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
// custom selects: themed replacement for the sender-ID dropdowns and the
// language switcher (styling mirrors the EEP combobox).
initCustomSelect('aa-sender');
initCustomSelect('ab-sender');
initCustomSelect('e-sender');
initCustomSelect('lang-select');
loadStatus();
setInterval(loadStatus, 2000);
