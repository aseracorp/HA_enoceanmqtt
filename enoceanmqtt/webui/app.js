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
    renderGateway();
    renderLearn();
    renderSensors();
    initEepSearch();
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
  const toggle = $('learn-toggle');
  const stateEl = $('learn-state');
  const btn = $('btn-learn-on');
  if (toggle) toggle.checked = state.learn;
  if (stateEl) { stateEl.textContent = state.learn ? 'Teach-in ON' : 'Teach-in OFF'; stateEl.className = 'state-text' + (state.learn ? ' on' : ''); }
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
    const msg = state.sensors.length ? (cats === 'actor' ? 'No actors configured.' : 'No sensors configured.') : 'No devices configured yet.';
    tbody.innerHTML = '<tr class="empty-row"><td colspan="7">' + msg + '</td></tr>';
    return;
  }

  tbody.innerHTML = filtered.map((s) => {
    const eep = s.eep ? escapeHtml(s.eep) : '—';
    const eepName = s.eep_name ? escapeHtml(s.eep_name) : '';
    const statusCls = s.status === 'online' ? 'online' : (s.status === 'offline' ? 'offline' : 'never');
    const statusTxt = s.status === 'online' ? 'Online' : (s.status === 'offline' ? 'Offline' : 'Never seen');
    const addr = s.address !== undefined ? '0x' + s.address.toString(16).toUpperCase().padStart(8, '0') : '—';
    const source = s.source === 'dynamic' ? ' <span class="pill" style="font-size:10px;padding:1px 6px">web</span>' : '';
    const name = escapeHtml(s.name);
    const isActor = deviceCategory(s) === 'actor';
    const catTxt = isActor ? 'Actor' : 'Sensor';
    const catCls = isActor ? 'actor' : 'sensor';
    const badges = [];
    if (s.bidirectional) badges.push('<span class="badge bidir" title="Bi-directional device">⇅ bidir</span>');
    if (s.smartack) badges.push('<span class="badge smartack" title="smartACK — requires fast acknowledgement">smartACK</span>');
    return `<tr data-name="${escapeHtml(s.name)}" data-cat="${catCls}">
      <td>${name}${source}</td>
      <td class="mono">${addr}</td>
      <td><span class="mono">${eep}</span>${eepName ? '<div style="color:var(--text-muted);font-size:11px">' + eepName + '</div>' : ''}</td>
      <td><span class="cat-badge ${catCls}">${catTxt}</span>${badges.join('')}</td>
      <td><span class="status-badge ${statusCls}">${statusTxt}</span></td>
      <td class="mono">${escapeHtml(fmtLastSeen(s.last_seen))}</td>
      <td><div class="row-actions">
        ${isActor ? '<button class="icon-btn teachin-btn" title="Send teach-in telegram to this actor" data-teachin="${escapeHtml(s.name)}">⤓</button>' : ''}
        <button class="icon-btn" title="Remove device" data-del="${escapeHtml(s.name)}">✕</button>
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
}

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
  document.querySelectorAll('.device-tab').forEach((t) => {
    t.classList.toggle('active', t.getAttribute('data-cat') === cat);
  });
  renderSensors();
}
document.querySelectorAll('.device-tab').forEach((t) => {
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
  const input = $('f-eep-search');
  const list = $('f-eep-list');
  const hidden = $('f-eep');
  if (!input) return;

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

$('add-form')?.addEventListener('submit', async (e) => {
  e.preventDefault();
  const name = $('f-name').value.trim();
  const address = parseAddress($('f-address').value);
  const eep = $('f-eep').value;
  const sender = $('f-sender').value.trim();

  if (!name) return toast('Please enter a sensor name', 'error');
  if (address === null) return toast('Please enter a valid address (e.g. 0x003DD63B)', 'error');
  if (!eep) return toast('Please select an EEP from the list', 'error');

  try {
    const res = await api('/api/sensors', {
      method: 'POST',
      body: JSON.stringify({ name, address, eep, sender: sender || undefined })
    });
    toast('Sensor "' + name + '" added', 'success');
    $('add-form').reset();
    await loadStatus();
  } catch (err) {
    toast('Failed to add sensor: ' + err.message, 'error');
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
$('learn-toggle')?.addEventListener('change', (e) => setLearn(e.target.checked));
$('btn-learn-on')?.addEventListener('click', () => setLearn(!state.learn));

/* ---------------- init ---------------- */
loadStatus();
setInterval(loadStatus, 5000);
