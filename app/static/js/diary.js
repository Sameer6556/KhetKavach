// Field diary tab: weekly crop photo + quick observations, AI findings, and the history of check-ins.
import { getJSON, sendJSON } from './api.js';
import { t } from './i18n.js';
import { esc, levelChip, shortDate } from './format.js';

const $ = (sel) => document.querySelector(sel);
const LIKELIHOOD_LEVEL = { low: 'low', medium: 'moderate', high: 'high' };
let photoBlob = null;

/** Downscale in the browser before upload: faster on rural networks, and re-encoding drops EXIF/GPS. */
async function shrink(file, maxSide = 1280) {
  try {
    const bitmap = await createImageBitmap(file, { imageOrientation: 'from-image' });
    const scale = Math.min(1, maxSide / Math.max(bitmap.width, bitmap.height));
    const canvas = document.createElement('canvas');
    canvas.width = Math.round(bitmap.width * scale);
    canvas.height = Math.round(bitmap.height * scale);
    canvas.getContext('2d').drawImage(bitmap, 0, 0, canvas.width, canvas.height);
    const blob = await new Promise((resolve) => canvas.toBlob(resolve, 'image/jpeg', 0.85));
    return blob || file;
  } catch {
    return file; // the server resizes and strips metadata anyway
  }
}

function diaryQuery(farm) {
  return new URLSearchParams({ lat: farm.lat, lon: farm.lon, crop: farm.crop, sowing_date: farm.sowing_date }).toString();
}

function observationTags(obs) {
  const tags = [];
  if (obs.leaf_colour) tags.push(`🍃 ${esc(t({ green: 'leaf_green', yellowing: 'leaf_yellowing', brown_spots: 'leaf_brown', mixed: 'leaf_mixed' }[obs.leaf_colour]))}`);
  if (obs.pests_seen) tags.push(`🐛 ${esc(t({ none: 'pests_none', few: 'pests_few', many: 'pests_many' }[obs.pests_seen]))}`);
  if (obs.irrigated_on) tags.push(`💧 ${shortDate(obs.irrigated_on)}`);
  if (obs.sprayed_on) tags.push(`🧴 ${shortDate(obs.sprayed_on)}`);
  if (obs.fertilised_on) tags.push(`🌾 ${shortDate(obs.fertilised_on)}`);
  return tags.map((x) => `<span class="chip">${x}</span>`).join('');
}

function findingsHtml(a) {
  const facts = [
    a.observed_stage && a.observed_stage !== 'unsure' ? `<b>${esc(t('stage_seen'))}:</b> ${esc(a.observed_stage)}` : '',
    Number.isFinite(a.canopy_cover_pct) ? `<b>${esc(t('canopy'))}:</b> ~${a.canopy_cover_pct}%` : '',
    a.water_stress_signs && a.water_stress_signs !== 'none' && a.water_stress_signs !== 'unsure' ? `<b>${esc(t('water_stress'))}:</b> ${esc(a.water_stress_signs)}` : '',
    a.weed_pressure && !['none', 'unsure'].includes(a.weed_pressure) ? `<b>${esc(t('weeds'))}:</b> ${esc(a.weed_pressure)}` : '',
    a.photo_quality && a.photo_quality !== 'good' ? `<b>${esc(t('photo_quality'))}:</b> ${esc(a.photo_quality)}` : '',
  ].filter(Boolean).join(' · ');
  const symptoms = (a.symptoms || []).map((s) => `<li>${levelChip(LIKELIHOOD_LEVEL[s.likelihood] || 'info')} ${esc(s.name)} <span class="muted">(${esc(s.where)})</span></li>`).join('');
  const checks = (a.next_checks || []).map((c) => `<li>${esc(c)}</li>`).join('');
  return `${a.summary ? `<p><b>${esc(a.summary)}</b></p>` : ''}<div>${facts}</div>`
    + (symptoms ? `<ul>${symptoms}</ul>` : '')
    + (a.change_since_last_photo && a.change_since_last_photo !== 'no previous photo' ? `<div><b>${esc(t('change'))}:</b> ${esc(a.change_since_last_photo)}</div>` : '')
    + (checks ? `<div><b>${esc(t('next_checks'))}:</b><ul>${checks}</ul></div>` : '');
}

function entryHtml(e) {
  const status = e.analysis ? findingsHtml(e.analysis)
    : e.analysis_status === 'unavailable' ? `<p class="muted">${esc(t('ai_unavailable'))}</p>`
      : e.analysis_status === 'failed' ? `<p class="muted">${esc(t('ai_failed'))}</p>` : '';
  const note = e.observations.note ? `<div>📝 ${esc(e.observations.note)}</div>` : '';
  return `<li class="diary-entry${e.photo_url ? '' : ' no-photo'}" data-token="${esc(e.token)}">`
    + (e.photo_url ? `<img src="${esc(e.photo_url)}" alt="Crop photo, ${esc(shortDate(e.created_at))}" loading="lazy">` : '')
    + `<div class="d-body"><div class="d-head"><span>${shortDate(e.created_at)}</span>`
    + `<button type="button" class="linkish" data-act="delete">${esc(t('delete_entry'))}</button></div>`
    + `<div class="d-tags">${observationTags(e.observations)}</div>${note}${status}</div></li>`;
}

export async function refreshDiary(farm) {
  const list = $('#diary-list');
  const due = $('#diary-due');
  if (!farm) {
    list.innerHTML = `<li class="muted">${esc(t('need_location'))}</li>`;
    due.hidden = true;
    return;
  }
  try {
    const { summary, entries } = await getJSON(`/api/diary?${diaryQuery(farm)}`);
    list.innerHTML = entries.length ? entries.map(entryHtml).join('') : `<li class="muted">${esc(t('diary_empty'))}</li>`;
    if (summary.next_check_in_due) {
      const overdue = summary.days_since_last_check_in >= 7;
      due.textContent = overdue ? `📷 ${t('diary_overdue')}` : `📅 ${t('diary_due')}: ${shortDate(summary.next_check_in_due)}`;
      due.hidden = false;
    } else {
      due.hidden = true;
    }
  } catch (err) {
    list.innerHTML = `<li class="muted">⚠ ${esc(err.message)}</li>`;
  }
}

function readObservations() {
  const val = (sel) => $(sel).value || undefined;
  const obs = {
    leaf_colour: document.querySelector('input[name="leaf"]:checked')?.value,
    pests_seen: document.querySelector('input[name="pests"]:checked')?.value,
    irrigated_on: val('#obs-irrigated'), sprayed_on: val('#obs-sprayed'), fertilised_on: val('#obs-fertilised'),
    note: $('#obs-note').value.trim() || undefined,
  };
  return Object.fromEntries(Object.entries(obs).filter(([, v]) => v !== undefined));
}

function resetForm() {
  $('#diary-form').reset();
  photoBlob = null;
  $('#photo-preview').hidden = true;
  $('#photo-preview').removeAttribute('src');
  $('#photo-hint').hidden = false;
}

/** getFarm() returns the current farm profile (or null); onIrrigated(date) lets the farm form learn new irrigation dates. */
export function initDiary(getFarm, onIrrigated) {
  const today = new Date(Date.now() - new Date().getTimezoneOffset() * 60000).toISOString().slice(0, 10);
  ['#obs-irrigated', '#obs-sprayed', '#obs-fertilised'].forEach((sel) => { $(sel).max = today; });

  $('#diary-photo').addEventListener('change', async (e) => {
    const file = e.target.files && e.target.files[0];
    if (!file) return;
    photoBlob = await shrink(file);
    const preview = $('#photo-preview');
    preview.src = URL.createObjectURL(photoBlob);
    preview.hidden = false;
    $('#photo-hint').hidden = true;
  });

  $('#diary-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const msg = $('#diary-msg');
    const farm = getFarm();
    if (!farm) { msg.textContent = t('need_location'); return; }
    const observations = readObservations();
    if (!photoBlob && !Object.keys(observations).length) { msg.textContent = t('diary_need_input'); return; }
    const button = $('#diary-submit');
    button.disabled = true;
    msg.textContent = t('diary_saving');
    const form = new FormData();
    form.append('farm', JSON.stringify(farm));
    form.append('observations', JSON.stringify(observations));
    if (photoBlob) form.append('photo', photoBlob, 'crop.jpg');
    try {
      const resp = await fetch('/api/diary', { method: 'POST', body: form });
      const body = await resp.json().catch(() => ({}));
      if (!resp.ok) throw new Error(typeof body.detail === 'string' ? body.detail : (body.detail || []).map((d) => d.msg).join('; ') || `HTTP ${resp.status}`);
      msg.textContent = t('diary_saved');
      if (observations.irrigated_on && onIrrigated(observations.irrigated_on)) msg.textContent += ` ${t('irrigation_updated')}`;
      resetForm();
      await refreshDiary(farm);
    } catch (err) {
      msg.textContent = `⚠ ${err.message}`;
    } finally {
      button.disabled = false;
    }
  });

  $('#diary-list').addEventListener('click', async (e) => {
    const btn = e.target.closest('[data-act="delete"]');
    if (!btn) return;
    const token = btn.closest('li').dataset.token;
    try {
      await sendJSON(`/api/diary/${encodeURIComponent(token)}`, 'DELETE');
      await refreshDiary(getFarm());
    } catch (err) {
      $('#diary-msg').textContent = `⚠ ${err.message}`;
    }
  });
}
