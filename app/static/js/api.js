// Thin client for the KhetKavach API: JSON helpers and a POST-capable Server-Sent-Events reader.

function errorMessage(status, body) {
  if (body && Array.isArray(body.detail)) {
    return body.detail.map((d) => `${(d.loc || []).slice(1).join('.') || 'input'}: ${d.msg}`).join('; ');
  }
  if (body && typeof body.detail === 'string') return body.detail;
  return `Request failed (HTTP ${status})`;
}

async function parse(resp) {
  let body = null;
  try { body = await resp.json(); } catch { /* non-JSON error page */ }
  if (!resp.ok) throw new Error(errorMessage(resp.status, body));
  return body;
}

export async function getJSON(url) {
  return parse(await fetch(url, { headers: { Accept: 'application/json' } }));
}

export async function sendJSON(url, method, payload) {
  return parse(await fetch(url, {
    method,
    headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
    body: payload === undefined ? undefined : JSON.stringify(payload),
  }));
}

/** POST `payload` and call onEvent(obj) for every `data:` line of the event stream. */
export async function streamEvents(url, payload, onEvent, signal) {
  const resp = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
    body: JSON.stringify(payload),
    signal,
  });
  if (!resp.ok || !resp.body) {
    let body = null;
    try { body = await resp.json(); } catch { /* ignore */ }
    throw new Error(errorMessage(resp.status, body));
  }
  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let cut;
    while ((cut = buffer.indexOf('\n\n')) >= 0) {
      const chunk = buffer.slice(0, cut);
      buffer = buffer.slice(cut + 2);
      for (const line of chunk.split('\n')) {
        if (!line.startsWith('data: ')) continue; // ":" lines are heartbeats
        try { onEvent(JSON.parse(line.slice(6))); } catch (err) { console.warn('Bad event', err); }
      }
    }
  }
}
