// App bootstrap: farm setup, language, tabs, dashboard streaming, health indicators.
import { getJSON, streamEvents } from './api.js';
import { applyI18n, setUiLanguage, t } from './i18n.js';
import { esc } from './format.js';
import { initMap, invalidate, onFarmDrag, setFarm } from './map.js';
import { renderSection, renderSummary, startDashboard } from './dashboard.js';
import { ask, initChat, renderSuggestions, resetConversation } from './chat.js';
import { initSentinel, refreshSentinel } from './sentinel.js';
import { canListen, listen } from './voice.js';

const $ = (sel) => document.querySelector(sel);
const state = { meta: null, lat: null, lon: null, place: '', sessionId: null, lang: 'en', dashboardRun: null };

const store = {
  get(key) { try { return JSON.parse(localStorage.getItem(key)); } catch { return null; } },
  set(key, value) { try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* private mode */ } },
};

function isoDaysAgo(days) {
  const d = new Date(Date.now() - days * 86400000);
  return new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 10);
}

function speechTag() {
  return state.meta?.languages.find((l) => l.code === state.lang)?.speech || 'en-IN';
}

function farmProfile() {
  if (state.lat === null) return null;
  return {
    lat: state.lat, lon: state.lon, crop: $('#crop').value, sowing_date: $('#sowing-date').value,
    irrigation_method: $('#irrigation').value, last_irrigation_date: $('#last-irrigation').value || null,
    language: state.lang, place: state.place || null,
  };
}

function persist() {
  store.set('kk-farm', { ...farmProfile(), place: state.place });
  store.set('kk-lang', state.lang);
}

function newConversation() {
  state.sessionId = null;
  resetConversation();
}

async function pickLocation(lat, lon, fromClick) {
  state.lat = Number(lat.toFixed(5));
  state.lon = Number(lon.toFixed(5));
  const marker = setFarm(state.lat, state.lon, fromClick ? null : 12);
  if (marker && !marker.kkBound) { onFarmDrag(pickLocation); marker.kkBound = true; }
  state.place = '';
  $('#place-label').textContent = `${state.lat.toFixed(4)}, ${state.lon.toFixed(4)}`;
  newConversation();
  persist();
  try {
    const p = await getJSON(`/api/reverse?lat=${state.lat}&lon=${state.lon}`);
    state.place = [p.locality, p.district, p.state].filter(Boolean).join(', ');
    $('#place-label').textContent = state.place || $('#place-label').textContent;
    persist();
  } catch { /* coordinates are enough */ }
}

function showFormError(message) {
  const el = $('#form-error');
  el.textContent = message || '';
  el.hidden = !message;
}

async function analyse() {
  const farm = farmProfile();
  if (!farm) { showFormError(t('need_location')); return; }
  showFormError('');
  persist();
  selectTab('dashboard');
  if (state.dashboardRun) state.dashboardRun.abort();
  const run = new AbortController();
  state.dashboardRun = run;
  const btn = $('#analyse-btn');
  btn.disabled = true;
  btn.textContent = t('analysing');
  let placeName = state.place;
  try {
    await streamEvents('/api/dashboard', farm, (ev) => {
      if (ev.type === 'start') startDashboard(ev.sections);
      if (ev.type === 'section') {
        renderSection(ev);
        if (ev.tool === 'get_location_context' && ev.ok) placeName = ev.headline || placeName;
      }
      if (ev.type === 'summary') renderSummary(ev, placeName);
      if (ev.type === 'error') showFormError(ev.message);
    }, run.signal);
  } catch (err) {
    if (err.name !== 'AbortError') showFormError(err.message);
  } finally {
    btn.disabled = false;
    btn.textContent = t('analyse');
    pollHealth();
  }
}

function selectTab(name) {
  document.querySelectorAll('.tab').forEach((b) => {
    const on = b.dataset.tab === name;
    b.classList.toggle('active', on);
    b.setAttribute('aria-selected', String(on));
  });
  document.querySelectorAll('.tab-panel').forEach((p) => { p.hidden = p.id !== `panel-${name}`; });
  if (name === 'sentinel') refreshSentinel();
  invalidate();
}

function setLanguage(code) {
  state.lang = code;
  setUiLanguage(code === 'hi' ? 'hi' : 'en');
  applyI18n();
  renderSuggestions((q) => { selectTab('chat'); ask(q, chatContext()); });
  newConversation();
  persist();
}

function chatContext() {
  return {
    farm: farmProfile(), sessionId: state.sessionId, speechTag: speechTag(),
    onSession: (id) => { state.sessionId = id; },
  };
}

async function pollHealth() {
  try {
    const h = await getJSON('/api/health');
    const ai = $('#ai-status');
    ai.className = `status-pill ${h.ai.enabled ? 'ok' : 'warn'}`;
    ai.title = h.ai.enabled ? `AI agent: ${h.ai.model} (effort ${h.ai.effort})` : 'AI key not configured: rule-based advisor active';
    const sources = Object.values(h.data_sources || {});
    const bad = sources.filter((s) => s.status !== 'ok').length;
    const data = $('#data-status');
    data.className = `status-pill ${!sources.length || !bad ? 'ok' : bad < sources.length ? 'warn' : 'bad'}`;
    data.title = sources.length ? Object.entries(h.data_sources).map(([k, s]) => `${k}: ${s.status}${s.last_error ? ` (${s.last_error})` : ''}`).join('\n') : 'No calls yet';
  } catch {
    $('#ai-status').className = 'status-pill bad';
  }
}

function initSearch() {
  const results = $('#search-results');
  $('#search-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const q = $('#place-search').value.trim();
    if (q.length < 2) return;
    results.hidden = false;
    results.innerHTML = '<li class="muted small">…</li>';
    try {
      const { results: items } = await getJSON(`/api/geocode?q=${encodeURIComponent(q)}`);
      results.innerHTML = items.length ? items.map((r, i) => `<li><button type="button" data-i="${i}">${esc(r.name)}`
        + `<span class="muted small"> · ${esc([r.district, r.state, r.country].filter(Boolean).join(', '))}</span></button></li>`).join('')
        : '<li class="muted small">No match</li>';
      results.onclick = (ev) => {
        const b = ev.target.closest('button');
        if (!b) return;
        const r = items[Number(b.dataset.i)];
        results.hidden = true;
        pickLocation(r.lat, r.lon, false);
      };
    } catch (err) {
      results.innerHTML = `<li class="muted small">⚠ ${esc(err.message)}</li>`;
    }
  });
  $('#locate-btn').addEventListener('click', () => {
    if (!navigator.geolocation) return showFormError('Geolocation not available');
    navigator.geolocation.getCurrentPosition((pos) => pickLocation(pos.coords.latitude, pos.coords.longitude, false),
      (err) => showFormError(err.message), { enableHighAccuracy: true, timeout: 10000 });
  });
}

function initMic() {
  const mic = $('#mic-btn');
  if (!canListen) { mic.title = t('mic_unsupported'); mic.disabled = true; return; }
  let stop = null;
  mic.addEventListener('click', () => {
    if (stop) { stop(); return; }
    mic.classList.add('listening');
    const input = $('#chat-input');
    stop = listen(speechTag(), (text, final) => {
      input.value = text;
      if (final && text.trim()) { stop && stop(); $('#chat-form').requestSubmit(); }
    }, () => { mic.classList.remove('listening'); stop = null; });
  });
}

async function boot() {
  initMap(pickLocation);
  try {
    state.meta = await getJSON('/api/meta');
  } catch (err) {
    showFormError(`Server unreachable: ${err.message}`);
    return;
  }
  $('#crop').innerHTML = state.meta.crops.map((c) => `<option value="${esc(c.key)}">${esc(c.name)} · ${esc(c.hindi)}</option>`).join('');
  $('#language').innerHTML = state.meta.languages.map((l) => `<option value="${esc(l.code)}">${esc(l.name)}</option>`).join('');

  const saved = store.get('kk-farm');
  const lang = store.get('kk-lang') || ((navigator.language || '').startsWith('hi') ? 'hi' : 'en');
  $('#language').value = lang;
  $('#crop').value = saved?.crop || 'wheat';
  $('#sowing-date').value = saved?.sowing_date || isoDaysAgo(20);
  $('#irrigation').value = saved?.irrigation_method || 'flood';
  $('#last-irrigation').value = saved?.last_irrigation_date || '';
  setLanguage(lang);

  if (saved && typeof saved.lat === 'number') {
    state.lat = saved.lat; state.lon = saved.lon; state.place = saved.place || '';
    const marker = setFarm(saved.lat, saved.lon, 11);
    if (marker) { onFarmDrag(pickLocation); marker.kkBound = true; }
    $('#place-label').textContent = state.place || `${saved.lat.toFixed(4)}, ${saved.lon.toFixed(4)}`;
  }

  $('#language').addEventListener('change', (e) => setLanguage(e.target.value));
  $('#farm-form').addEventListener('submit', (e) => { e.preventDefault(); analyse(); });
  ['#crop', '#sowing-date', '#irrigation', '#last-irrigation'].forEach((sel) => $(sel).addEventListener('change', () => { newConversation(); persist(); }));
  document.querySelectorAll('.tab').forEach((b) => b.addEventListener('click', () => selectTab(b.dataset.tab)));
  initSearch();
  initMic();
  initChat(chatContext);
  initSentinel(farmProfile);
  pollHealth();
  setInterval(pollHealth, 60000);
}

boot();
