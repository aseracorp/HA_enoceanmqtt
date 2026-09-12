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
  const mqttAddr = (state.config && state.config.mqtt_host) ? state.config.mqtt_host + (state.config.mqtt_port ? ':' + state.config.mqtt_port : '') : '—';
    mqttEl.innerHTML = '<span class="dot"></span>MQTT ' + escapeHtml(String(mqttAddr));
