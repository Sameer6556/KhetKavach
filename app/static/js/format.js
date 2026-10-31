// Formatting helpers: safe HTML, a tiny markdown renderer with citation chips, labels and icons.
import { t } from './i18n.js';

export const TOOL_META = {
  get_location_context: { icon: '📍', label: 'Location' },
  get_weather_outlook: { icon: '🌦️', label: 'Weather outlook' },
  get_climate_anomaly: { icon: '📊', label: 'Rain & heat vs normal' },
  get_soil_profile: { icon: '🟫', label: 'Soil profile' },
  get_irrigation_plan: { icon: '💧', label: 'Irrigation plan' },
  get_spray_windows: { icon: '🧴', label: 'Spray windows' },
  get_crop_risk: { icon: '🌱', label: 'Crop stress & disease' },
  get_flood_risk: { icon: '🌊', label: 'River flood' },
  get_mandi_prices: { icon: '₹', label: 'Mandi prices' },
  get_disaster_alerts: { icon: '⚠️', label: 'Disaster alerts' },
  get_air_quality: { icon: '🌫️', label: 'Air quality' },
  get_satellite_crop_health: { icon: '🛰️', label: 'Satellite crop vigour' },
  geocode_place: { icon: '🔎', label: 'Place search' },
  get_field_diary: { icon: '📔', label: 'Field diary' },
};

export function toolLabel(name) {
  const m = TOOL_META[name];
  return m ? `${m.icon} ${m.label}` : name;
}

export function esc(value) {
  return String(value ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

export function levelChip(level) {
  const lv = level || 'unknown';
  return `<span class="chip ${esc(lv)}">${esc(t(`level_${lv}`))}</span>`;
}

export function fmt(value, unit = '', digits = 1) {
  if (value === null || value === undefined || Number.isNaN(value)) return '–';
  const n = typeof value === 'number' ? Number(value.toFixed(digits)) : value;
  return `${typeof n === 'number' ? n.toLocaleString('en-IN') : esc(n)}${unit ? ` ${unit}` : ''}`;
}

export function shortDate(iso) {
  if (!iso) return '–';
  const d = new Date(`${iso.slice(0, 10)}T00:00:00`);
  return Number.isNaN(d.getTime()) ? esc(iso) : d.toLocaleDateString('en-IN', { weekday: 'short', day: 'numeric', month: 'short' });
}

export function timeOf(iso) {
  return iso && iso.length >= 16 ? iso.slice(11, 16) : '';
}

export function sourceLine(sources) {
  return (sources || []).map((s) => {
    const when = s.retrieved_at ? new Date(s.retrieved_at).toLocaleString('en-IN', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' }) : '';
    const badge = s.stale ? ` <span class="chip stale">${esc(t('stale'))}</span>` : (s.cached ? ` · ${esc(t('cached'))}` : '');
    return `<span class="src">${esc(s.name)}${when ? ` · ${esc(when)}` : ''}${badge}</span>`;
  }).join('');
}

export function duration(ms) {
  if (ms === null || ms === undefined) return '';
  return ms < 1000 ? `${Math.round(ms)} ms` : `${(ms / 1000).toFixed(1)} s`;
}

/** Minimal, XSS-safe markdown: paragraphs, bullet lists, **bold**, and [E#] citation chips. */
export function renderAnswer(text, evidenceIds = new Set()) {
  const lines = esc(text).split(/\n/);
  const out = [];
  let list = false;
  const inline = (s) => s
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/\[\s*(E\d+(?:\s*[,;]\s*E\d+)*)\s*\]/g, (_, ids) => ids.split(/\s*[,;]\s*/)
      .map((id) => `<button type="button" class="cite${evidenceIds.has(id) ? '' : ' bad'}" data-ev="${id}">${id}</button>`).join(' '));
  for (const raw of lines) {
    const line = raw.trim();
    const bullet = line.match(/^(?:[-*•]|\d+[.)])\s+(.*)$/);
    if (bullet) {
      if (!list) { out.push('<ul>'); list = true; }
      out.push(`<li>${inline(bullet[1])}</li>`);
      continue;
    }
    if (list) { out.push('</ul>'); list = false; }
    if (!line) continue;
    const heading = line.match(/^#{1,4}\s+(.*)$/);
    out.push(heading ? `<p><strong>${inline(heading[1])}</strong></p>` : `<p>${inline(line)}</p>`);
  }
  if (list) out.push('</ul>');
  return out.join('');
}

/** Plain text for speech: no citations, no markdown symbols. */
export function speechText(text) {
  return text.replace(/\[\s*E\d+(?:\s*[,;]\s*E\d+)*\s*\]/g, '').replace(/[*#•]/g, '').replace(/\s{2,}/g, ' ').trim();
}
