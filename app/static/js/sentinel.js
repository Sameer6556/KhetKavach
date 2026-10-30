// Sentinel tab: register farms for autonomous monitoring, trigger scans, browse alerts.
import { getJSON, sendJSON } from './api.js';
import { t } from './i18n.js';
import { esc, levelChip, shortDate } from './format.js';

async function refreshFarms() {
  const list = document.getElementById('farm-list');
  try {
    const { farms } = await getJSON('/api/farms');
    list.innerHTML = farms.length ? farms.map((f) => `<li data-id="${f.id}"><div class="grow"><strong>${esc(f.name)}</strong>`
      + `<div class="muted small">${esc(f.crop)} · ${esc(f.sowing_date)} · ${f.lat.toFixed(3)}, ${f.lon.toFixed(3)}${f.telegram_chat_id ? ' · Telegram ✔' : ''}</div></div>`
      + `<button class="btn btn-ghost" data-act="scan">${esc(t('scan'))}</button><button class="btn btn-ghost" data-act="delete">${esc(t('delete'))}</button></li>`).join('')
      : `<li class="muted">${esc(t('no_farms'))}</li>`;
  } catch (err) {
    list.innerHTML = `<li class="muted">⚠ ${esc(err.message)}</li>`;
  }
}

async function refreshAlerts() {
  const list = document.getElementById('alert-list');
  try {
    const { alerts } = await getJSON('/api/alerts');
    list.innerHTML = alerts.length ? alerts.map((a) => `<li data-sev="${esc(a.severity)}"><div class="a-title">${levelChip(a.severity)} ${esc(a.title)}</div>`
      + `<div>${esc(a.message)}</div><div class="a-meta">Farm #${a.farm_id} · ${shortDate(a.valid_for)} · ${esc(a.created_at.replace('T', ' ').slice(0, 16))} UTC`
      + `${a.delivered ? ` · sent via ${esc(a.delivered)}` : ''}</div></li>`).join('')
      : `<li class="muted">${esc(t('no_alerts'))}</li>`;
  } catch (err) {
    list.innerHTML = `<li class="muted">⚠ ${esc(err.message)}</li>`;
  }
}

async function scan(url, button) {
  const label = button.textContent;
  button.disabled = true;
  button.textContent = t('scanning');
  try {
    const res = await sendJSON(url, 'POST');
    button.textContent = `${res.new_alerts} ${t('new_alerts')}`;
    await refreshAlerts();
  } catch (err) {
    button.textContent = `⚠ ${err.message}`;
  } finally {
    setTimeout(() => { button.textContent = label; button.disabled = false; }, 2500);
  }
}

export function initSentinel(getFarm) {
  document.getElementById('register-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const farm = getFarm();
    const msg = document.getElementById('register-msg');
    if (!farm) { msg.textContent = t('need_location'); return; }
    try {
      await sendJSON('/api/farms', 'POST', {
        ...farm, name: document.getElementById('farm-name').value.trim(),
        telegram_chat_id: document.getElementById('telegram-id').value.trim() || null,
      });
      msg.textContent = t('registered');
      await refreshFarms();
    } catch (err) {
      msg.textContent = `⚠ ${err.message}`;
    }
  });
  document.getElementById('scan-all').addEventListener('click', (e) => scan('/api/sentinel/scan', e.currentTarget));
  document.getElementById('farm-list').addEventListener('click', async (e) => {
    const btn = e.target.closest('button');
    if (!btn) return;
    const id = btn.closest('li').dataset.id;
    if (btn.dataset.act === 'scan') await scan(`/api/sentinel/scan?farm_id=${id}`, btn);
    if (btn.dataset.act === 'delete') { await sendJSON(`/api/farms/${id}`, 'DELETE'); await refreshFarms(); }
  });
}

export async function refreshSentinel() {
  await Promise.all([refreshFarms(), refreshAlerts()]);
}
