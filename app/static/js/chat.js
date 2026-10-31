// Chat with the AI agronomist + live agent trace (reasoning, tool calls, verification, sources).
import { streamEvents } from './api.js';
import { t } from './i18n.js';
import { duration, esc, fmt, renderAnswer, sourceLine, toolLabel } from './format.js';
import { speak } from './voice.js';

const log = () => document.getElementById('chat-log');
const trace = () => document.getElementById('trace-list');

let evidenceIndex = new Map(); // evidence id -> { tool, sources }
let controller = null;

function scrollToEnd(el) { el.scrollTop = el.scrollHeight; }

function addMessage(role, html) {
  const div = document.createElement('div');
  div.className = `msg ${role}`;
  div.innerHTML = html;
  log().appendChild(div);
  scrollToEnd(log());
  return div;
}

function traceItem(cls, headHtml, bodyHtml = '') {
  const li = document.createElement('li');
  li.className = `trace-item ${cls}`;
  li.innerHTML = `<div class="t-head">${headHtml}</div>${bodyHtml}`;
  trace().appendChild(li);
  li.scrollIntoView({ block: 'nearest' });
  return li;
}

function compactInput(input) {
  if (!input || typeof input !== 'object') return '';
  return Object.entries(input).filter(([, v]) => v !== null && v !== undefined)
    .map(([k, v]) => `${k}=${typeof v === 'number' ? Number(v.toFixed ? v.toFixed(3) : v) : v}`).join(', ');
}

function verificationBadge(v, selfCorrected) {
  if (!v) return '';
  if (v.ok) {
    const n = v.data_figures ? `${v.supported}/${v.data_figures} ` : '';
    return `<span class="chip low">✔ ${n}${esc(t('verified'))}</span>${selfCorrected ? ` <span class="chip info">↺ ${esc(t('self_corrected'))}</span>` : ''}`;
  }
  const bad = [...(v.unsupported || []), ...(v.unknown_citations || [])].join(', ');
  return `<span class="chip high">⚠ ${esc(bad)} ${esc(t('unverified'))}</span>`;
}

function flashEvidence(id) {
  const target = trace().querySelector(`[data-ev="${id}"]`);
  if (!target) return;
  target.scrollIntoView({ block: 'center', behavior: 'smooth' });
  target.classList.add('flash');
  setTimeout(() => target.classList.remove('flash'), 1600);
}

export function initChat(getContext) {
  const form = document.getElementById('chat-form');
  const input = document.getElementById('chat-input');
  form.addEventListener('submit', (e) => {
    e.preventDefault();
    const text = input.value.trim();
    if (text) { input.value = ''; ask(text, getContext()); }
  });
  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); form.requestSubmit(); }
  });
  log().addEventListener('click', (e) => {
    const cite = e.target.closest('.cite');
    if (cite) { document.querySelector('[data-tab="chat"]').click(); flashEvidence(cite.dataset.ev); }
    const spk = e.target.closest('.speak-btn');
    if (spk) { const ctx = getContext(); if (!speak(spk.dataset.text, ctx.speechTag)) addMessage('bot', esc(t('no_voice'))); }
  });
}

export function renderSuggestions(onPick) {
  const box = document.getElementById('suggestions');
  box.innerHTML = ['q1', 'q7', 'q2', 'q3', 'q4', 'q5', 'q6'].map((k) => `<button type="button" data-q="${k}">${esc(t(k))}</button>`).join('');
  box.onclick = (e) => { const b = e.target.closest('button'); if (b) onPick(t(b.dataset.q)); };
}

export function resetConversation() {
  evidenceIndex = new Map();
  log().innerHTML = '';
  trace().innerHTML = '';
}

export async function ask(question, ctx) {
  if (!ctx.farm) { addMessage('bot', esc(t('need_location'))); return; }
  if (controller) controller.abort();
  controller = new AbortController();
  const send = document.getElementById('send-btn');
  send.disabled = true;

  addMessage('user', esc(question));
  const bot = addMessage('bot', '<span class="typing"><i></i><i></i><i></i></span>');
  traceItem('notice', `❓ ${esc(question.slice(0, 120))}`);

  let draft = '';
  let thinkingItem = null;
  const tools = new Map();

  const handlers = {
    session(ev) { ctx.onSession(ev.session_id); },
    step(ev) { draft = ''; thinkingItem = null; if (ev.n > 1) traceItem('', `<span class="muted">${esc(t('step'))} ${ev.n}</span>`); },
    thinking(ev) {
      if (!thinkingItem) thinkingItem = traceItem('thinking', `🧠 ${esc(t('thinking'))}`, '<div class="t-body"></div>');
      const body = thinkingItem.querySelector('.t-body');
      body.textContent += ev.delta;
      body.scrollTop = body.scrollHeight;
    },
    text(ev) {
      draft += ev.delta;
      bot.innerHTML = `<div class="draft">${renderAnswer(draft)}</div>`;
      scrollToEnd(log());
    },
    tool_planned(ev) {
      if (!tools.has(ev.id)) tools.set(ev.id, traceItem('', `<span class="spinner"></span> <span class="grow">${esc(toolLabel(ev.name))}</span>`));
    },
    tool_start(ev) {
      const head = `<span class="spinner"></span> <span class="grow">${esc(toolLabel(ev.name))}</span>`;
      const body = `<div class="t-sub">${esc(compactInput(ev.input))}</div>`;
      const item = tools.get(ev.id);
      if (item) item.innerHTML = `<div class="t-head">${head}</div>${body}`;
      else tools.set(ev.id, traceItem('', head, body));
      draft = '';
    },
    tool_end(ev) {
      const item = tools.get(ev.id) || traceItem('', '');
      item.className = `trace-item ${ev.ok ? 'ok' : 'err'}`;
      if (ev.evidence_id) {
        item.dataset.ev = ev.evidence_id;
        evidenceIndex.set(ev.evidence_id, { tool: ev.name, sources: ev.sources });
      }
      const status = ev.ok ? `✔ <span class="chip info">${esc(ev.evidence_id)}</span>` : '✖';
      const detail = ev.ok
        ? `<div class="t-sub">${esc(ev.headline || '')}</div><div class="t-sub">${sourceLine(ev.sources)}</div>`
        : `<div class="t-sub">${esc(ev.error?.message || 'failed')}${ev.error?.hint ? ` · hint: ${esc(ev.error.hint)}` : ''}</div>`;
      const sub = item.querySelector('.t-sub');
      item.innerHTML = `<div class="t-head">${status} <span class="grow">${esc(toolLabel(ev.name))}</span><span class="muted">${duration(ev.duration_ms)}</span></div>`
        + (sub ? sub.outerHTML : '') + detail;
    },
    verification(ev) {
      const r = ev.report || {};
      traceItem('verify', `🔎 Verifier: ${esc([...(r.unsupported || []), ...(r.unknown_citations || [])].join(', ') || 'missing citations')}`,
        `<div class="t-sub">Not traceable to tool evidence; asking the agent to self-correct…</div>`);
      bot.innerHTML = '<span class="typing"><i></i><i></i><i></i></span>';
    },
    notice(ev) { traceItem('notice', `ℹ️ ${esc(ev.message)}`); },
    error(ev) { bot.innerHTML = `<p>⚠ ${esc(ev.message)}</p>`; },
    final(ev) {
      (ev.evidence || []).forEach((e) => evidenceIndex.set(e.id, { tool: e.tool, sources: e.sources }));
      const ids = new Set(evidenceIndex.keys());
      const mode = ev.mode === 'ai' ? `${t('mode_ai')}${ev.model ? ` · ${ev.model}` : ''}` : t('mode_rules');
      const usage = ev.usage ? ` · ${fmt((ev.usage.input_tokens + ev.usage.cache_read_tokens) / 1000, 'k in', 1)} / ${fmt(ev.usage.output_tokens / 1000, 'k out', 1)} tokens · ${ev.usage.tool_calls} tools` : '';
      bot.innerHTML = renderAnswer(ev.text, ids)
        + `<div class="meta"><span class="chip ${ev.mode === 'ai' ? 'info' : 'stale'}">${esc(mode)}</span>${verificationBadge(ev.verification, ev.self_corrected)}`
        + `<button type="button" class="speak-btn" title="Read aloud" data-text="${esc(ev.text)}">🔊</button><span class="muted">${esc(usage)}</span></div>`;
      traceItem('verify', `🔎 ${ev.verification?.ok ? '✔' : '⚠'} ${esc(t('verified'))}: ${ev.verification?.supported ?? 0}/${ev.verification?.data_figures ?? 0}`,
        `<div class="t-sub">${esc(t('evidence'))}: ${[...ids].join(', ') || '–'}</div>`);
      scrollToEnd(log());
      if (document.getElementById('autospeak').checked) speak(ev.text, ctx.speechTag);
    },
  };

  try {
    await streamEvents('/api/chat', { session_id: ctx.sessionId, message: question, farm: ctx.farm },
      (ev) => (handlers[ev.type] || (() => {}))(ev), controller.signal);
  } catch (err) {
    if (err.name !== 'AbortError') bot.innerHTML = `<p>⚠ ${esc(err.message)}</p>`;
  } finally {
    send.disabled = false;
    controller = null;
  }
}
