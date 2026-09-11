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
    initEepSearch();
    populateSenders(state.virtual_senders);
    applyTranslations();
    loadConfig();
  } catch (e) {
    toast('Failed to load status: ' + e.message, 'error');
  }
}

function renderGateway() {
  const gw = state.gateway;
  const gwEl = $('gw-status');
  const mqttEl = $('mqtt-status');
  const baseEl = $('base-id');

  if (gw) {
    gwEl.className = 'pill ' + (gw.connected ? 'ok' : 'bad');
    gwEl.innerHTML = '<span class="dot"></span>Gateway ' + (gw.connected ? 'connected' : 'offline');
    mqttEl.className = 'pill ' + (gw.mqtt ? 'ok' : 'bad');
    mqttEl.innerHTML = '<span class="dot"></span>MQTT ' + (gw.mqtt ? 'connected' : 'offline');
    baseEl.textContent = 'Base ID: ' + (gw.base_id || '—');
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
  // show all real (non-underscore) values, e.g. "ILL 170 lx · TMP 21°C · OCC on"
  const keys = Object.keys(l.values).filter((k) => !k.startsWith('_'));
  if (!keys.length) return '—';
  return keys.map((k) => {
    let v = l.values[k];
    const m = meta[k] || {};
    let txt;
    if (m.text && m.text !== String(v)) txt = m.text;
    else if (typeof v === 'number') txt = (Math.round(v * 100) / 100).toString();
    else txt = String(v);
    return (m.description ? m.description.split(' ')[0] : k) + ' ' + txt + (m.unit ? ' ' + m.unit : '');
  }).join(' · ');
}

/* ---------------- value graph ---------------- */
async function showGraph(name) {
  $('graph-title').textContent = 'Value history · ' + name;
  $('graph-body').innerHTML = 'Loading…';
  $('graph-overlay').hidden = false;
  try {
    const res = await api('/api/history/' + encodeURIComponent(name));
    if (!res.ok) throw new Error(res.error || 'no history');
    const hist = res.history || [];
    if (!hist.length) { $('graph-body').innerHTML = 'No values recorded yet for this device.'; return; }
    // pick the first real (non-meta) value key
    const keys = Object.keys(hist[hist.length - 1].values || {}).filter((k) => !k.startsWith('_'));
    if (!keys.length) { $('graph-body').innerHTML = 'No numeric values to plot.'; return; }
    const key = keys[0]; // (graph plots the first numeric value; all are in the table)
    const pts = hist.filter((h) => h.values && h.values[key] !== undefined)
      .map((h) => ({ t: new Date(h.ts).getTime(), v: Number(h.values[key]) }));
    $('graph-body').innerHTML = '<div class="graph-wrap"><svg id="mini-graph" viewBox="0 0 600 180" preserveAspectRatio="none"></svg></div>' +
      '<div style="margin-top:8px;color:var(--text-secondary);font-size:12px">' + escapeHtml(key) + ' · ' + pts.length + ' samples</div>';
    renderMiniGraph('mini-graph', pts, key);
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
    toast('Failed to load configuration: ' + e.message, 'error');
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
    toast('Configuration saved - restart required', 'success');
  } catch (err) {
    toast('Failed to save configuration: ' + err.message, 'error');
  }
});

let activeDeviceCat = 'all';

function deviceCategory(s) {
  return s.category === 'actor' ? 'actor' : 'sensor';
}

function renderSensors() {
  const tbody = $('sensor-body');
  const cats = activeDeviceCat;
  const filtered = state.sensors.filter((s) => cats === 'all' || deviceCategory(s) === cats);
  const count = $('device-count');
  if (count) count.textContent = state.sensors.length + ' device' + (state.sensors.length === 1 ? '' : 's');

  if (!filtered.length) {
    const msg = state.sensors.length ? (cats === 'actor' ? t('no_actors') : t('no_sensors')) : t('no_devices');
    tbody.innerHTML = '<tr class="empty-row"><td colspan="8">' + msg + '</td></tr>';
    return;
  }

  tbody.innerHTML = filtered.map((s) => {
    const eep = s.eep ? escapeHtml(s.eep) : '—';
    const eepName = s.eep_name ? escapeHtml(s.eep_name) : '';
    const statusCls = s.status === 'online' ? 'online' : (s.status === 'offline' ? 'offline' : 'never');
    const statusTxt = s.status === 'online' ? t('st_online') : (s.status === 'offline' ? t('st_offline') : t('st_never'));
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
    const catTxt = isActor ? t('actor') : t('sensor');
    const catCls = isActor ? 'actor' : 'sensor';
    const badges = [];
    if (s.bidirectional) badges.push('<span class="badge bidir" title="Bi-directional device">⇅ bidir</span>');
    if (s.smartack) badges.push('<span class="badge smartack" title="smartACK — requires fast acknowledgement">smartACK</span>');
    return `<tr data-name="${escapeHtml(s.name)}" data-cat="${catCls}">
      <td>${name}${source}</td>
      <td class="mono">${addrHtml}</td>
      <td><span class="mono">${eep}</span>${eepName ? '<div style="color:var(--text-muted);font-size:11px">' + eepName + '</div>' : ''}</td>
      <td><span class="cat-badge ${catCls}">${catTxt}</span>${badges.join('')}</td>
      <td><span class="status-badge ${statusCls}">${statusTxt}</span></td>
      <td class="mono">${escapeHtml(fmtLastSeen(s.last_seen))}</td>
      <td class="mono latest-val"><a href="#" data-graph="${escapeHtml(s.name)}" title="Show history graph">${escapeHtml(fmtLatest(s))}</a></td>
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
  $('e-eep').value = s.eep || '';
  $('e-eep-list').classList.remove('open');
  $('edit-overlay').hidden = false;
}

$('edit-cancel')?.addEventListener('click', () => { $('edit-overlay').hidden = true; editingDevice = null; });
$('edit-overlay')?.addEventListener('click', (e) => {
  if (e.target === $('edit-overlay')) { $('edit-overlay').hidden = true; editingDevice = null; }
});

$('edit-ok')?.addEventListener('click', async () => {
  if (!editingDevice) return;
  const name = $('e-name').value.trim();
  const eep = $('e-eep').value.trim();
  const addrVal = $('e-address').value.trim();
  const address = addrVal ? parseAddress(addrVal) : null;
  if (addrVal && address === null) return toast('Please enter a valid address (e.g. 0x003DD63B)', 'error');
  try {
    const body = { name: name, eep: eep };
    if (address !== null) body.address = address;
    const res = await api('/api/sensors/' + encodeURIComponent(editingDevice), {
      method: 'PUT',
      body: JSON.stringify(body)
    });
    if (!res.ok) throw new Error(res.error || 'update failed');
    toast('Device updated', 'success');
    $('edit-overlay').hidden = true;
    editingDevice = null;
    await loadStatus();
  } catch (err) {
    toast('Failed to update device: ' + err.message, 'error');
  }
});

async function sendTeachIn(name) {
  try {
    const res = await api('/api/teachin', { method: 'POST', body: JSON.stringify({ name: name }) });
    toast(res.message || 'Teach-in sent', res.ok ? 'success' : 'error');
  } catch (err) {
    toast('Failed to send teach-in: ' + err.message, 'error');
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
    toast(on ? 'Teach-in enabled — press the button on your sensor' : 'Teach-in disabled', 'success');
  } catch (e) {
    toast('Failed to ' + (on ? 'enable' : 'disable') + ' teach-in: ' + e.message, 'error');
  }
}

/* ---------------- add sensor ---------------- */
function initEepSearch() {
  bindEepSearch('f-eep-search', 'f-eep-list', 'f-eep');
}

function bindEepSearch(inputId, listId, hiddenId) {
  const input = $(inputId);
  const list = $(listId);
  const hidden = $(hiddenId);
  if (!input || !list || !hidden) return;

  input.addEventListener('input', () => {
    const q = input.value.trim();
    hidden.value = '';
    if (q.length < 2) { list.classList.remove('open'); return; }
    const matches = state.eep.filter((p) =>
      (p.eep || '').toLowerCase().includes(q.toLowerCase()) ||
      (p.name || '').toLowerCase().includes(q.toLowerCase())
    ).slice(0, 50);
    list.innerHTML = matches.length
      ? matches.map((p) => `<div class="eep-option" data-eep="${escapeHtml(p.eep)}">
          <span class="eep-name">${escapeHtml(p.name)}</span>
          <span class="eep-code">${escapeHtml(p.eep)}</span>
        </div>`).join('')
      : '<div class="eep-empty">No matching EEP found</div>';
    list.classList.add('open');

    list.querySelectorAll('.eep-option').forEach((opt) => {
      opt.addEventListener('click', () => {
        input.value = opt.getAttribute('data-eep');
        hidden.value = opt.getAttribute('data-eep');
        list.classList.remove('open');
      });
    });
  });

  input.addEventListener('blur', () => setTimeout(() => list.classList.remove('open'), 150));
}

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
  const isActor = mode === 'actor';
  $('f-addr-field').hidden = isActor;
  $('f-sender-field').hidden = !isActor;
  if (isActor && $('f-sender')) {
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
  const eep = $('f-eep').value;
  if (!name) return toast('Please enter a name', 'error');
  if (!eep) return toast('Please select an EEP from the list', 'error');

  const isActor = addMode === 'actor';
  let address, sender;
  if (isActor) {
    address = 0xFFFFFFFF;
    sender = parseInt($('f-sender').value, 0);
    if (isNaN(sender)) return toast('Please select a virtual sender ID', 'error');
  } else {
    address = parseAddress($('f-address').value);
    if (address === null) return toast('Please enter a valid address (e.g. 0x003DD63B)', 'error');
  }

  try {
    const res = await api('/api/sensors', {
      method: 'POST',
      body: JSON.stringify({ name, address, eep, sender, category: isActor ? 'actor' : 'sensor', virtual: isActor ? 1 : 0 })
    });
    toast((isActor ? 'Actor' : 'Sensor') + ' "' + name + '" added', 'success');
    $('add-form').reset();
    setAddMode(addMode);
    await loadStatus();
  } catch (err) {
    toast('Failed to add device: ' + err.message, 'error');
  }
});

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
    toast('Sensor "' + name + '" removed', 'success');
    await loadStatus();
  } catch (err) {
    toast('Failed to remove sensor: ' + err.message, 'error');
  }
});

/* ---------------- i18n ---------------- */
const I18N = {
  en: {
    subtitle: 'Web Configurator', gateway: 'Gateway', mqtt: 'MQTT',
    teachin_title: 'Teach-In', add_device: 'Add device',
    add_sensor: 'Add Sensor (sender)', add_actor: 'Add Actor (receiver)',
    all: 'All', sensors: 'Sensors', actors: 'Actors', devices: 'Devices',
    configuration: 'Configuration', add_device_btn: 'Add device',
    col_name: 'Name', col_addr: 'Address / Sender', col_type: 'Type',
    col_status: 'Status', col_lastseen: 'Last seen', col_latest: 'Latest',
    start_teachin: 'Start teach-in', stop_teachin: 'Stop teach-in',
    remove_device: 'Remove device', edit_device: 'Edit device',
    save_config: 'Save configuration',
 
    st_online: 'Online',
    st_offline: 'Offline',
    st_never: 'Never seen',
    actor: 'Actor',
    sensor: 'Sensor',
    no_actors: 'No actors configured.',
    no_sensors: 'No sensors configured.',
    no_devices: 'No devices configured yet.' },
  de: {
    subtitle: 'Web-Konfigurator', gateway: 'Gateway', mqtt: 'MQTT',
    teachin_title: 'Teach-In', add_device: 'Gerät hinzufügen',
    add_sensor: 'Sensor hinzufügen (Sender)', add_actor: 'Aktor hinzufügen (Empfänger)',
    all: 'Alle', sensors: 'Sensoren', actors: 'Aktoren', devices: 'Geräte',
    configuration: 'Konfiguration', add_device_btn: 'Gerät hinzufügen',
    col_name: 'Name', col_addr: 'Adresse / Sender', col_type: 'Typ',
    col_status: 'Status', col_lastseen: 'Zuletzt gesehen', col_latest: 'Letzter Wert',
    start_teachin: 'Teach-In starten', stop_teachin: 'Teach-In stoppen',
    remove_device: 'Gerät entfernen', edit_device: 'Gerät bearbeiten',
    save_config: 'Konfiguration speichern',
 
    st_online: 'Online',
    st_offline: 'Offline',
    st_never: 'Nie gesehen',
    actor: 'Aktor',
    sensor: 'Sensor',
    no_actors: 'Keine Aktoren konfiguriert.',
    no_sensors: 'Keine Sensoren konfiguriert.',
    no_devices: 'Noch keine Geräte konfiguriert.' },
  fr: {
    subtitle: 'Configurateur Web', gateway: 'Passerelle', mqtt: 'MQTT',
    teachin_title: 'Enseignement', add_device: 'Ajouter un appareil',
    add_sensor: 'Ajouter un capteur (émetteur)', add_actor: 'Ajouter un actionneur (récepteur)',
    all: 'Tous', sensors: 'Capteurs', actors: 'Actionneurs', devices: 'Appareils',
    configuration: 'Configuration', add_device_btn: 'Ajouter un appareil',
    col_name: 'Nom', col_addr: 'Adresse / Émetteur', col_type: 'Type',
    col_status: 'État', col_lastseen: 'Vu pour la dernière fois', col_latest: 'Dernière valeur',
    start_teachin: 'Démarrer l\'enseignement', stop_teachin: 'Arrêter l\'enseignement',
    remove_device: 'Supprimer l\'appareil', edit_device: 'Modifier l\'appareil',
    save_config: 'Enregistrer la configuration',
 
    st_online: 'En ligne',
    st_offline: 'Hors ligne',
    st_never: 'Jamais vu',
    actor: 'Actionneur',
    sensor: 'Capteur',
    no_actors: 'Aucun actionneur configuré.',
    no_sensors: 'Aucun capteur configuré.',
    no_devices: 'Aucun appareil configuré.' },
  it: {
    subtitle: 'Configuratore Web', gateway: 'Gateway', mqtt: 'MQTT',
    teachin_title: 'Teach-In', add_device: 'Aggiungi dispositivo',
    add_sensor: 'Aggiungi sensore (mittente)', add_actor: 'Aggiungi attuatore (ricevitore)',
    all: 'Tutti', sensors: 'Sensori', actors: 'Attuatori', devices: 'Dispositivi',
    configuration: 'Configurazione', add_device_btn: 'Aggiungi dispositivo',
    col_name: 'Nome', col_addr: 'Indirizzo / Mittente', col_type: 'Tipo',
    col_status: 'Stato', col_lastseen: 'Ultimo visto', col_latest: 'Ultimo valore',
    start_teachin: 'Avvia teach-in', stop_teachin: 'Ferma teach-in',
    remove_device: 'Rimuovi dispositivo', edit_device: 'Modifica dispositivo',
    save_config: 'Salva configurazione',
 
    st_online: 'Online',
    st_offline: 'Offline',
    st_never: 'Mai visto',
    actor: 'Attuatore',
    sensor: 'Sensore',
    no_actors: 'Nessun attuatore configurato.',
    no_sensors: 'Nessun sensore configurato.',
    no_devices: 'Nessun dispositivo configurato.' },
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
  const learnBtn = $('btn-learn-on');
  if (learnBtn) learnBtn.textContent = state.learn ? t('stop_teachin') : t('start_teachin');
}
$('lang-select')?.addEventListener('change', (e) => setLang(e.target.value));
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
initEepSearch();
bindEepSearch('e-eep', 'e-eep-list', 'e-eep');
loadStatus();
setInterval(loadStatus, 2000);
