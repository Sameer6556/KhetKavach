# Demo playbook

Live demonstration is **30% of the score** and the system must not crash. This file has three parts: the pre-flight checklist, a
2½-minute video script, and a 5-minute live run with backup plans.

## 1. Pre-flight checklist (T-60 min)

* [ ] Run `python scripts/smoke_test.py --llm` against your **deployed** environment variables. Every core line must show ✔.
* [ ] Open the live URL once, a minute early, so a sleeping free instance wakes up and caches warm.
* [ ] **Pick demo farms with interesting weather today.**
  * Open the dashboard for 4–5 candidate districts and keep the ones with real hazards: heavy rain, heat, a river near its 2-year level,
    a GDACS event nearby.
  * Good candidates by season:
    * Monsoon: Assam/Bihar floodplains, coastal Odisha/Andhra in cyclone season.
    * Rabi: Punjab/Haryana for cold waves and stubble-smoke AQI.
    * Pre-monsoon: Vidarbha/Rajasthan for heat.
  * Note their coordinates. Live data changes daily, so this choice is part of the prep.
* [ ] Register one farm in **Sentinel** with **your own Telegram chat ID** (message `@userinfobot` to get it). Start a chat with your bot
  first, otherwise Telegram refuses to deliver.
* [ ] Set the language picker to Hindi (or the judges' language) and test the mic once in Chrome. Allow microphone access.
* [ ] Have the README architecture diagram open in a second tab.
* [ ] Keep a phone hotspot ready in case the venue Wi-Fi fails.

## 2. Video script (2½ minutes, screen recording + voice-over)

| Time | Show | Say |
|---|---|---|
| 0:00 | Title, then the empty app | "86% of India's farms are small. Climate risk decides their season, but advice is district-level and generic. KhetKavach is a climate-risk shield for one exact field." |
| 0:12 | Tap a field on the map, crop *onion*, sowing date, **Analyse** | "I tap my field. KhetKavach runs 12 live analyses at once, on ECMWF and GFS forecasts, 30-year ERA5 normals, GloFAS river ensembles, SoilGrids, Sentinel-2, GDACS and Agmarknet." |
| 0:30 | Cards streaming in; zoom on weather + irrigation charts | "Each card is computed for this farm. The irrigation plan is an FAO-56 water balance on this field's soil. Here it says hold irrigation, rain is coming, and shows the litres per acre saved." |
| 0:55 | Flood + satellite cards | "River discharge compared with 2-, 5- and 20-year flood levels from 40 years of reanalysis. Sentinel-2 NDVI from the satellite, cloud-masked, versus last year." |
| 1:10 | Switch to chat, Hindi, press 🎙️, ask "इस हफ्ते मुझे खेत में क्या करना चाहिए?" | "Now I ask in Hindi, by voice." |
| 1:20 | Trace panel: thinking, 4 tools in parallel, evidence IDs | "The AI agent plans, calls four tools in parallel, and every result becomes evidence with a source and timestamp." |
| 1:40 | Final answer + ✔ verification badge + 🔊 | "Before I see the answer, our verifier checks every number against the evidence: 9 of 9 verified. It reads the answer aloud." |
| 1:55 | Ask "प्याज का भाव?" or a misspelt commodity → error + retry in the trace | "When a tool fails, the error carries a hint and the agent corrects itself." |
| 2:00 | Field diary tab on the phone: snap a crop photo, tap "A few" pests, save → findings appear | "Each week the farmer sends one photo. The AI compares it with last week's, flags possible problems with a likelihood, and the agent uses it in every answer." |
| 2:10 | Sentinel tab → Scan now → phone shows the Telegram alert | "Sentinel watches registered farms on its own and pushes alerts to the farmer's phone." |
| 2:25 | Architecture diagram | "Live data, real agronomy, verified AI, built to keep working when sources fail. KhetKavach." |

Record at 1080p, cursor highlighted, no edits that hide latency. Rule: no pre-recorded fakes presented as live.

## 3. Live judging run (5–6 minutes)

1. **(30 s) Problem**, then one sentence on the solution.
2. **(90 s) Dashboard** on your prepared hazard farm. Point at:
   * a hazard chip,
   * the irrigation "hold for rain" line,
   * the flood chart's return-level lines,
   * the source + timestamp footer ("all live, nothing hard-coded").
3. **(90 s) Voice question** in Hindi, with the trace panel visible. Then click a citation chip `[E3]` and show it jump to the tool call
   and its source.
4. **(45 s) Robustness**, if asked:
   * Open `/api/health` to show per-source status.
   * Explain the circuit breaker and stale cache.
   * Mention the rule-based fallback (remove the key in a local copy to show it).
5. **(45 s) Field diary:** take a photo of a real plant (or a printed photo) with the phone, save the check-in, and show the dashboard
   card comparing the stage seen with the stage expected. Then ask "What do my latest field photos show?" in chat.
6. **(45 s) Sentinel** scan → Telegram notification on the phone.

### If something goes wrong

| Symptom | What to do / say |
|---|---|
| A card says "unavailable" | "That provider is down right now. Our circuit breaker isolated it; every other card still works, and the agent is told not to guess." Show `/api/health`. |
| A card shows **stale** | "The source is down, so we serve the last good reading with its timestamp. Real data, clearly labelled." |
| The AI is slow | Keep talking over the live trace; that's the point of showing it. On Claude, set `KK_EFFORT=low` beforehand if the venue network is slow. |
| AI errors (key, rate limit) | The rule-based advisor answers automatically (yellow "Rule-based" chip). Say so; it is designed behaviour. On the Gemini free tier, leave a few seconds between questions so you stay under the per-minute limit. |
| Mic not working | Type the Hindi question, or pick a suggested question. |
| Venue internet fails | Switch to the phone hotspot. Cached cards stay visible. |
