// Voice in and out with the browser's Web Speech API (no extra service or key needed).
// Chrome/Edge support speech recognition for Hindi, Marathi, Tamil, Telugu, Bengali, Kannada, Gujarati,
// Malayalam, Punjabi and Indian English; speech output depends on the voices installed on the device.
import { speechText } from './format.js';

const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;

export const canListen = Boolean(Recognition);
export const canSpeak = 'speechSynthesis' in window;

let active = null;

/** Start listening; calls onText(partialOrFinal, isFinal). Returns a stop function. */
export function listen(langTag, onText, onEnd) {
  if (!Recognition) throw new Error('unsupported');
  if (active) active.abort();
  const rec = new Recognition();
  rec.lang = langTag;
  rec.interimResults = true;
  rec.maxAlternatives = 1;
  rec.onresult = (e) => {
    let text = '';
    let final = false;
    for (let i = e.resultIndex; i < e.results.length; i += 1) {
      text += e.results[i][0].transcript;
      final = final || e.results[i].isFinal;
    }
    onText(text, final);
  };
  rec.onerror = () => onEnd && onEnd();
  rec.onend = () => { active = null; if (onEnd) onEnd(); };
  rec.start();
  active = rec;
  return () => rec.stop();
}

function pickVoice(langTag) {
  const voices = window.speechSynthesis.getVoices();
  const base = langTag.split('-')[0];
  return voices.find((v) => v.lang === langTag) || voices.find((v) => v.lang.startsWith(base)) || null;
}

/** Speak text; returns false when no voice for the language is installed. */
export function speak(text, langTag) {
  if (!canSpeak) return false;
  window.speechSynthesis.cancel();
  const voice = pickVoice(langTag);
  const clean = speechText(text);
  // Long answers are split into sentences: some engines stop after ~15 s of continuous speech.
  const parts = clean.match(/[^.!?।\n]+[.!?।]?/g) || [clean];
  for (const part of parts) {
    if (!part.trim()) continue;
    const u = new SpeechSynthesisUtterance(part.trim());
    u.lang = langTag;
    if (voice) u.voice = voice;
    u.rate = 0.95;
    window.speechSynthesis.speak(u);
  }
  return Boolean(voice);
}

export function stopSpeaking() {
  if (canSpeak) window.speechSynthesis.cancel();
}

if (canSpeak) window.speechSynthesis.getVoices(); // warm up the voice list (loads asynchronously)
