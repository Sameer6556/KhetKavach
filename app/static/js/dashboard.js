// Risk dashboard: one card per live analysis, filled in as each result streams in.
/* global Chart */
import { t } from './i18n.js';
import { duration, esc, fmt, levelChip, shortDate, sourceLine, timeOf, TOOL_META } from './format.js';
import { showDisasterEvents, showRiverCell } from './map.js';

const grid = () => document.getElementById('dash-grid');
const charts = new Map();
const WIDE = new Set(['get_weather_outlook', 'get_irrigation_plan']);

function cssVar(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

function chartDefaults() {
  Chart.defaults.color = cssVar('--ink-2');
  Chart.defaults.borderColor = cssVar('--line');
  Chart.defaults.font.family = getComputedStyle(document.body).fontFamily;
  Chart.defaults.font.size = 11;
  Chart.defaults.plugins.legend.labels.boxWidth = 10;
  Chart.defaults.maintainAspectRatio = false;
}

function facts(items) {
  const cells = items.filter(([, v]) => v !== undefined && v !== null && v !== '' && v !== '–')
    .map(([k, v]) => `<div class="fact"><div class="k">${esc(k)}</div><div class="v">${v}</div></div>`);
  return cells.length ? `<div class="facts">${cells.join('')}</div>` : '';
}

function canvas(id) {
  return `<div class="chart-box"><canvas id="${id}" aria-label="chart"></canvas></div>`;
}

function drawChart(id, config) {
  const el = document.getElementById(id);
  if (!el || typeof Chart === 'undefined') return;
  charts.get(id)?.destroy();
  charts.set(id, new Chart(el, config));
}

// ------------------------------------------------------------------------------------------ renderers
const R = {
  get_location_context(d) {
    const now = d.now || {};
    return {
      html: facts([
        ['Place', esc(d.locality || '–')], ['District', esc(d.district || '–')], ['State', esc(d.state || '–')],
        ['Elevation', fmt(d.elevation_m, 'm', 0)],
        ['Now', now.temperature_c != null ? `${fmt(now.temperature_c, '°C')} · ${fmt(now.humidity_pct, '%', 0)}` : null],
        ['Local time', esc(d.local_time ? d.local_time.slice(11) : '–')],
      ]),
    };
  },

  get_weather_outlook(d, chart) {
    const s = d.summary || {};
    const hazards = (d.hazards || []).map((h) => `<li><strong>${esc(h.hazard)}</strong> · ${shortDate(h.date)}</li>`).join('');
    const rows = (d.daily || []).map((r) => `<tr><td>${shortDate(r.date)}</td><td>${esc(r.weather || '')}</td>`
      + `<td>${fmt(r.tmax_c)}° / ${fmt(r.tmin_c)}°</td><td>${fmt(r.rain_mm, 'mm')}</td><td>${fmt(r.rain_chance_pct, '%', 0)}</td></tr>`).join('');
    return {
      html: facts([['Rain, next 7 d', fmt(s.rain_total_mm, 'mm')], ['Rainy days', fmt(s.rainy_days, '', 0)],
        ['Hottest', fmt(s.max_tmax_c, '°C')], ['Coldest night', fmt(s.min_tmin_c, '°C')]])
        + (hazards ? `<ul>${hazards}</ul>` : '')
        + canvas('chart-weather')
        + `<div class="scroll"><table><thead><tr><th>Day</th><th>Sky</th><th>Max/Min</th><th>Rain</th><th>Chance</th></tr></thead><tbody>${rows}</tbody></table></div>`,
      after() {
        const days = (chart && chart.daily) || [];
        drawChart('chart-weather', {
          data: {
            labels: days.map((x) => shortDate(x.date)),
            datasets: [
              { type: 'bar', label: 'Rain (mm)', data: days.map((x) => x.rain), yAxisID: 'y1', backgroundColor: cssVar('--info') + '99' },
              { type: 'line', label: 'Normal rain/day', data: days.map((x) => x.normal_rain), yAxisID: 'y1', borderColor: cssVar('--info'), borderDash: [2, 3], pointRadius: 0 },
              { type: 'line', label: 'Max °C', data: days.map((x) => x.tmax), borderColor: cssVar('--high'), tension: 0.3, pointRadius: 2 },
              { type: 'line', label: 'Min °C', data: days.map((x) => x.tmin), borderColor: cssVar('--brand'), tension: 0.3, pointRadius: 2 },
              { type: 'line', label: 'Normal max', data: days.map((x) => x.normal_tmax), borderColor: cssVar('--high'), borderDash: [5, 4], pointRadius: 0 },
              { type: 'line', label: 'Normal min', data: days.map((x) => x.normal_tmin), borderColor: cssVar('--brand'), borderDash: [5, 4], pointRadius: 0 },
            ],
          },
          options: { interaction: { mode: 'index', intersect: false },
            scales: { y: { title: { display: true, text: '°C' } }, y1: { position: 'right', beginAtZero: true, grid: { drawOnChartArea: false }, title: { display: true, text: 'mm' } } } },
        });
      },
    };
  },

  get_crop_risk(d) {
    const items = [];
    (d.heat_stress_days || []).forEach((x) => items.push(`🔥 ${shortDate(x.date)}: ${fmt(x.tmax_c, '°C')} ≥ ${fmt(x.crop_threshold_c, '°C', 0)}${x.critical_stage ? ' (flowering stage)' : ''}${x.imd ? ` · IMD ${esc(x.imd)}` : ''}`));
    (d.cold_stress_days || []).forEach((x) => items.push(`❄️ ${shortDate(x.date)}: ${fmt(x.tmin_c, '°C')}${x.frost_risk ? ' · frost risk' : ''}`));
    (d.heavy_rain_days || []).forEach((x) => items.push(`🌧️ ${shortDate(x.date)}: ${fmt(x.rain_mm, 'mm')} (${esc(x.imd_category)})`));
    (d.late_blight_hutton_periods || []).forEach((x) => items.push(`🍂 Late blight weather (Hutton) ${shortDate(x.date)}`));
    (d.worker_humid_heat || []).forEach((x) => items.push(`🥵 Humid heat ${shortDate(x.date)}: wet-bulb ${fmt(x.wet_bulb_max_c, '°C')} (${esc(x.level)})`));
    const diseases = (d.disease_weather || []).map((x) => `<li>${esc(x.disease)} ${levelChip(x.risk === 'low' ? 'low' : x.risk)}`
      + `${x.favourable_days.length ? ` <span class="muted small">${x.favourable_days.map((f) => shortDate(f.date)).join(', ')}</span>` : ''}</li>`).join('');
    return {
      html: facts([['Stage', esc(d.stage_now)], ['Day', fmt(d.days_after_sowing, '', 0)],
        ['Heat limit', fmt(d.thresholds?.heat_stress_tmax_c, '°C', 0)], ['Cold limit', fmt(d.thresholds?.cold_stress_tmin_c, '°C', 0)]])
        + (items.length ? `<ul>${items.map((i) => `<li>${i}</li>`).join('')}</ul>` : '<p>No heat, cold or heavy-rain stress in the next 7 days.</p>')
        + (diseases ? `<div><strong>Disease weather (7 days)</strong><ul>${diseases}</ul></div>` : ''),
    };
  },

  get_irrigation_plan(d, chart) {
    const sw = d.soil_water_today || {};
    const nxt = d.next_irrigation;
    const defer = (d.rain_deferrals || [])[0];
    return {
      html: facts([
        ['Stage', esc(d.stage)], ['Root-zone depletion', fmt(sw.depletion_pct, '%', 0)], ['Soil status', esc(sw.status)],
        ['Next irrigation', nxt ? `${shortDate(nxt.date)} · ${fmt(nxt.net_mm, 'mm')} net` : 'not needed soon'],
        ['Apply', nxt && nxt.gross_mm ? `${fmt(nxt.gross_mm, 'mm')} (${fmt(nxt.litres_per_acre, 'L/acre', 0)})` : null],
        ['Rain, next 7 d', fmt(d.next_7_days?.rain_mm, 'mm')], ['Crop water use, 7 d', fmt(d.next_7_days?.crop_water_use_mm, 'mm')],
      ])
        + (defer ? `<p>☔ <strong>Hold irrigation on ${shortDate(defer.date)}</strong>: ${fmt(defer.rain_next_2_days_mm, 'mm')} rain expected in the next 2 days`
          + `${d.water_saved_by_waiting_for_rain_litres_per_acre ? ` (saves about ${fmt(d.water_saved_by_waiting_for_rain_litres_per_acre, 'L/acre', 0)})` : ''}.</p>` : '')
        + canvas('chart-irrigation')
        + `<p class="muted small">FAO-56 water balance since ${esc(d.balance_started_from)}.</p>`,
      after() {
        const days = (chart && chart.days) || [];
        drawChart('chart-irrigation', {
          data: {
            labels: days.map((x) => shortDate(x.date)),
            datasets: [
              { type: 'line', label: 'Root-zone depletion %', data: days.map((x) => x.depletion_pct), borderColor: cssVar('--accent'), tension: 0.25,
                pointRadius: days.map((x) => (x.action === 'irrigate' ? 6 : 1)), pointStyle: days.map((x) => (x.action === 'irrigate' ? 'triangle' : 'circle')),
                segment: { borderDash: (ctx) => (days[ctx.p1DataIndex]?.forecast ? [5, 4] : undefined) } },
              { type: 'line', label: 'Irrigate above (RAW)', data: days.map((x) => x.raw_pct), borderColor: cssVar('--severe'), borderDash: [3, 3], pointRadius: 0 },
              { type: 'bar', label: 'Rain (mm)', data: days.map((x) => x.rain_mm), yAxisID: 'y1', backgroundColor: cssVar('--info') + '88' },
            ],
          },
          options: { interaction: { mode: 'index', intersect: false },
            scales: { y: { min: 0, max: 100, title: { display: true, text: '% depleted' } }, y1: { position: 'right', beginAtZero: true, grid: { drawOnChartArea: false }, title: { display: true, text: 'mm' } } } },
        });
      },
    };
  },

  get_spray_windows(d, chart) {
    const wins = (d.good_windows || []).slice(0, 4).map((w) => `<li><strong>${shortDate(w.start)} ${timeOf(w.start)}–${timeOf(w.end)}</strong>`
      + ` · wind ${fmt(w.avg_wind_kmh, 'km/h')} · ΔT ${fmt(w.avg_delta_t_c, '°C')}</li>`).join('');
    const hours = (chart && chart.hourly) || [];
    const strip = hours.slice(0, 72).map((h) => `<span class="${esc(h.status)}" title="${esc(h.time.replace('T', ' '))}: ${esc(h.status)}"></span>`).join('');
    const blockers = Object.entries(d.main_blockers || {}).map(([k, v]) => `${esc(k)} (${v} h)`).join(', ');
    return {
      html: (wins ? `<ul>${wins}</ul>` : '<p>No fully suitable window. Avoid spraying unless urgent.</p>')
        + `<div class="spray-strip" aria-label="Next 72 hours">${strip}</div>`
        + `<div class="legend"><span><i style="background:var(--low)"></i>good</span><span><i style="background:var(--moderate)"></i>marginal</span><span><i style="background:var(--severe)"></i>unsuitable/night</span></div>`
        + (blockers ? `<p class="muted small">Main blockers: ${blockers}</p>` : ''),
    };
  },

  get_climate_anomaly(d) {
    const line = (x, label) => (x ? [label, `${fmt(x.rain_mm, 'mm')} vs ${fmt(x.normal_mm, 'mm')}`
      + (x.departure_pct != null ? ` (${x.departure_pct > 0 ? '+' : ''}${fmt(x.departure_pct, '%', 0)})` : '')] : [label, null]);
    const cats = [d.last_30_days, d.next_7_days, d.next_16_days].filter(Boolean)
      .map((x) => `<li>${esc(x.period)}: <strong>${esc(x.imd_category)}</strong> · percentile ${fmt(x.percentile_vs_1991_2020, '', 0)}</li>`).join('');
    const tdep = d.tmax_next_7_days;
    return {
      html: facts([line(d.last_30_days, 'Last 30 days'), line(d.next_7_days, 'Next 7 days'), line(d.next_16_days, 'Next 16 days'),
        ['Max temp vs normal (7 d)', tdep ? `${tdep.departure_c > 0 ? '+' : ''}${fmt(tdep.departure_c, '°C')}` : null]])
        + (cats ? `<ul>${cats}</ul>` : ''),
    };
  },

  get_flood_risk(d, chart) {
    const rl = d.return_levels_m3s || {};
    showRiverCell(d.river_cell);
    return {
      html: facts([['Peak (median)', `${fmt(d.forecast_peak?.median_m3s, 'm³/s', 0)} · ${shortDate(d.forecast_peak?.date)}`],
        ['Ensemble max', fmt(d.forecast_peak?.ensemble_max_m3s, 'm³/s', 0)], ['2-yr flood level', fmt(rl['2_year'], 'm³/s', 0)],
        ['5-yr / 20-yr', `${fmt(rl['5_year'], '', 0)} / ${fmt(rl['20_year'], 'm³/s', 0)}`], ['River cell', fmt(d.river_cell?.distance_km, 'km away')]])
        + `<p>${esc(d.reason)}</p>` + canvas('chart-flood'),
      after() {
        const days = (chart && chart.days) || [];
        const lv = (chart && chart.return_levels) || {};
        const flat = (v) => days.map(() => v);
        drawChart('chart-flood', {
          type: 'line',
          data: {
            labels: days.map((x) => shortDate(x.date)),
            datasets: [
              { label: 'p25', data: days.map((x) => x.p25), borderWidth: 0, pointRadius: 0 },
              { label: 'p25–p75', data: days.map((x) => x.p75), borderWidth: 0, pointRadius: 0, fill: '-1', backgroundColor: cssVar('--info') + '33' },
              { label: 'Median', data: days.map((x) => x.median), borderColor: cssVar('--info'), pointRadius: 0, tension: 0.2 },
              { label: 'Ensemble max', data: days.map((x) => x.max), borderColor: cssVar('--info'), borderDash: [4, 4], pointRadius: 0 },
              ...(lv['2_year'] ? [{ label: '2-yr', data: flat(lv['2_year']), borderColor: cssVar('--moderate'), pointRadius: 0, borderWidth: 1 }] : []),
              ...(lv['5_year'] ? [{ label: '5-yr', data: flat(lv['5_year']), borderColor: cssVar('--high'), pointRadius: 0, borderWidth: 1 }] : []),
              ...(lv['20_year'] ? [{ label: '20-yr', data: flat(lv['20_year']), borderColor: cssVar('--severe'), pointRadius: 0, borderWidth: 1 }] : []),
            ],
          },
          options: { plugins: { legend: { labels: { filter: (item) => item.text !== 'p25' } } }, scales: { y: { beginAtZero: true, title: { display: true, text: 'm³/s' } } } },
        });
      },
    };
  },

  get_soil_profile(d) {
    return {
      html: facts([['Texture', esc(d.texture_class || '–')],
        ['Sand / silt / clay', d.sand_pct != null ? `${fmt(d.sand_pct, '', 0)} / ${fmt(d.silt_pct, '', 0)} / ${fmt(d.clay_pct, '%', 0)}` : null],
        ['pH', d.ph != null ? `${fmt(d.ph)} · ${esc(d.ph_class)}` : null],
        ['Organic carbon', d.organic_carbon_pct != null ? `${fmt(d.organic_carbon_pct, '%', 2)} · ${esc(d.organic_carbon_class)}` : null],
        ['Water holding', fmt(d.available_water_mm_per_m, 'mm/m', 0)], ['CEC', fmt(d.cec_cmol_per_kg, 'cmol/kg')]]),
    };
  },

  get_mandi_prices(d) {
    const rows = (d.best_markets || []).map((m) => `<tr><td>${esc(m.market)}</td><td>${esc(m.district)}</td><td><strong>₹${fmt(m.modal_price, '', 0)}</strong></td>`
      + `<td>${m.min_price != null ? `₹${fmt(m.min_price, '', 0)}–${fmt(m.max_price, '', 0)}` : ''}</td></tr>`).join('');
    const local = (d.district_markets || [])[0];
    const gap = d.best_vs_local_gap;
    return {
      html: facts([['Commodity', esc(d.commodity)], ['Arrival date', shortDate(d.latest_arrival_date)],
        ['State median', d.state_modal_median != null ? `₹${fmt(d.state_modal_median, '/q', 0)}` : null],
        ['Local market', local ? `${esc(local.market)} · ₹${fmt(local.modal_price, '/q', 0)}` : null]])
        + (gap && gap.amount > 0 ? `<p>Best market pays ₹${fmt(gap.amount, '', 0)}/q (${fmt(gap.pct, '%', 0)}) more than ${esc(gap.local_market)}; check transport cost.</p>` : '')
        + `<div class="scroll"><table><thead><tr><th>Market</th><th>District</th><th>Modal</th><th>Range</th></tr></thead><tbody>${rows}</tbody></table></div>`
        + '<p class="muted small">Prices in ₹ per quintal (Agmarknet).</p>',
    };
  },

  get_disaster_alerts(d, chart) {
    showDisasterEvents((chart && chart.events) || []);
    const items = (d.nearby_events || []).map((e) => `<li>${levelChip(e.alert_level === 'Red' ? 'severe' : e.alert_level === 'Orange' ? 'high' : 'low')} `
      + `<strong>${esc(e.type_name)}</strong>: ${esc(e.name)} · ${fmt(e.distance_km, 'km', 0)}${e.url ? ` · <a href="${esc(e.url)}" target="_blank" rel="noopener">report</a>` : ''}</li>`).join('');
    return {
      html: (items ? `<ul>${items}</ul>` : `<p>No active GDACS events within ${fmt(d.radius_km, 'km', 0)}.</p>`)
        + `<p class="muted small">Worldwide Orange/Red events now: ${fmt(d.global_orange_or_red_events, '', 0)}</p>`,
    };
  },

  get_air_quality(d) {
    return {
      html: facts([['US AQI now', `${fmt(d.us_aqi_now, '', 0)} · ${esc(d.category_now || '')}`], ['PM2.5', fmt(d.pm2_5_ugm3, 'µg/m³')],
        ['PM10', fmt(d.pm10_ugm3, 'µg/m³')], ['Max AQI, 72 h', fmt(d.max_us_aqi_next_72h, '', 0)]]),
    };
  },

  get_satellite_crop_health(d, chart) {
    const ly = d.same_period_last_year;
    return {
      html: facts([['Latest NDVI', `${fmt(d.latest?.ndvi, '', 2)} · ${shortDate(d.latest?.date)}`], ['Canopy', esc(d.latest?.class || '')],
        ['Change vs previous', d.change_since_previous != null ? `${d.change_since_previous > 0 ? '+' : ''}${fmt(d.change_since_previous, '', 2)}` : null],
        ['vs last year', ly ? `${ly.difference > 0 ? '+' : ''}${fmt(ly.difference, '', 2)} (${fmt(ly.ndvi, '', 2)})` : null]])
        + canvas('chart-ndvi'),
      after() {
        const obs = (chart && chart.observations) || [];
        const last = chart && chart.last_year;
        drawChart('chart-ndvi', {
          type: 'line',
          data: {
            labels: obs.map((o) => shortDate(o.date)),
            datasets: [{ label: 'NDVI (cloud-free)', data: obs.map((o) => o.ndvi_mean), borderColor: cssVar('--low'), backgroundColor: cssVar('--low') + '33', fill: true, tension: 0.25 },
              ...(last ? [{ label: `Last year (${shortDate(last.date)})`, data: obs.map(() => last.ndvi_mean), borderColor: cssVar('--muted'), borderDash: [4, 4], pointRadius: 0 }] : [])],
          },
          options: { scales: { y: { min: 0, max: 1 } } },
        });
      },
    };
  },
};

// ------------------------------------------------------------------------------------------ cards
function cardFor(tool) {
  return grid().querySelector(`[data-tool="${tool}"]`);
}

export function startDashboard(sections) {
  chartDefaults();
  charts.forEach((c) => c.destroy());
  charts.clear();
  document.getElementById('dash-empty').hidden = true;
  const g = grid();
  g.hidden = false;
  g.innerHTML = '';
  const tpl = document.getElementById('tpl-card');
  for (const tool of sections) {
    const node = tpl.content.firstElementChild.cloneNode(true);
    node.dataset.tool = tool;
    if (WIDE.has(tool)) node.classList.add('wide');
    node.querySelector('.card-icon').textContent = TOOL_META[tool]?.icon || '•';
    node.querySelector('.card-title').textContent = TOOL_META[tool]?.label || tool;
    node.querySelector('.level-chip').outerHTML = '<span class="spinner" aria-label="loading"></span>';
    node.querySelector('.card-body').innerHTML = '<div class="skeleton"></div><div class="skeleton" style="width:70%"></div>';
    g.appendChild(node);
  }
}

export function renderSection(ev) {
  const card = cardFor(ev.tool);
  if (!card) return;
  card.dataset.level = ev.ok ? ev.level : 'unknown';
  card.querySelector('.card-head .spinner, .card-head .chip')?.remove();
  card.querySelector('.card-head').insertAdjacentHTML('beforeend', levelChip(ev.ok ? ev.level : 'unknown'));
  const body = card.querySelector('.card-body');
  const headline = card.querySelector('.card-headline');
  if (!ev.ok) {
    headline.textContent = ev.error?.message || 'Unavailable';
    body.innerHTML = ev.error?.hint ? `<p class="muted small">${esc(ev.error.hint)}</p>` : '';
    card.querySelector('.card-foot').innerHTML = `<span>${esc(ev.tool)} · ${duration(ev.duration_ms)}</span>`;
    return;
  }
  headline.textContent = ev.headline || '';
  const view = (R[ev.tool] || (() => ({ html: '' })))(ev.data || {}, ev.chart);
  const notes = (ev.notes || []).slice(0, 2).map((n) => `<p class="muted small">${esc(n)}</p>`).join('');
  body.innerHTML = view.html + notes;
  card.querySelector('.card-foot').innerHTML = `<span class="ev">${esc(ev.evidence_id || '')}</span>${sourceLine(ev.sources)}`
    + `<span>${duration(ev.duration_ms)}</span>`;
  if (view.after) view.after();
}

export function renderSummary(ev, placeName) {
  const box = document.getElementById('dash-summary');
  const chips = (ev.top_risks || []).map((r) => `${levelChip(r.level)} <span>${esc(TOOL_META[r.tool]?.label || r.tool)}</span>`);
  box.innerHTML = `<span class="place">${esc(placeName || '')}</span>`
    + `<span class="muted">${esc(t('top_risks'))}:</span><span class="chips">${chips.length ? chips.join(' ') : esc(t('no_major_risks'))}</span>`
    + `<span class="muted small">${esc(t('updated'))} ${new Date().toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit' })}</span>`;
  box.hidden = false;
}
