# Submission form answers (copy-paste ready)

## 1. Problem statement

**Target issue & context.**

* India's 146 million farm holdings are 86% small or marginal (Agriculture Census 2015-16). Their seasons are increasingly decided by
  climate volatility: erratic monsoon onset and breaks, heatwaves at flowering, cold waves, flash floods and river overflow.
* Official agromet advisories are issued for districts or blocks, twice a week, as bulletins. They cannot account for a specific
  field's crop stage, soil, irrigation method or nearby river, and farmers cannot ask them a follow-up question.
* The data needed for field-level decisions is public but fragmented across technical portals. That includes ECMWF/GFS forecasts, ERA5
  climate normals, GloFAS river ensembles, SoilGrids, Sentinel-2 imagery, GDACS alerts and Agmarknet prices. Generic AI chatbots answer
  such questions from training data and hallucinate numbers.
* The result: avoidable irrigation and spray losses, unmanaged heat/flood/disease risk, and distress sales.

**Target audience.**

* Primary: smallholder farmers, reached by voice in their own language.
* Also: Farmer Producer Organisations (FPOs), Krishi Vigyan Kendra and agri-extension staff, and district disaster-management cells.

## 2. Solution description

**High-level overview.** KhetKavach is an agentic climate-risk shield for a single field. From one map tap it runs 12 live analyses: IMD
weather hazards; rain/heat versus the 1991-2020 normal; crop-specific heat, cold and disease weather; an FAO-56 irrigation schedule
with rain-aware deferral; pesticide spray windows; a GloFAS river-flood outlook; soil; mandi prices; nearby disasters; air quality; and
Sentinel-2 NDVI.

An LLM agent (Google Gemini by default for our deployment; Anthropic Claude also supported) handles questions in 11 Indian
languages, by voice. It:

1. plans which tools to use and calls them in parallel;
2. recovers from tool errors using the hints they return;
3. answers with `[E#]` evidence citations.

A deterministic grounding verifier then checks every data figure in the answer against the evidence and triggers one self-correction
if anything is untraceable.

A weekly **field diary** makes the advice personal. The farmer sends one crop photo and a few taps (leaf colour, pests seen,
irrigation/spray/fertiliser dates). The AI compares it with last week's photo and returns structured findings (stage seen vs
expected, canopy cover, possible symptoms with a likelihood, what to check), which the agent then uses as cited evidence.

An autonomous Sentinel re-analyses registered farms every few hours and pushes de-duplicated alerts in the farmer's language to
Telegram. If the LLM is unavailable, a rule-based advisor answers from the same live data.

**Technical architecture.**

* **Stack:** Python 3.12, FastAPI (REST + Server-Sent Events), SQLite. A shared agent core with two provider loops: Google Gen AI SDK (Gemini function calling with thought summaries) and the
  Anthropic Python SDK (Claude streaming tool use), the latter using
  adaptive thinking, prompt caching and server-side refusal fallbacks. Vanilla-JS single-page app with Leaflet, Chart.js and the Web
  Speech API. One Docker container.
* **Live data:**
  * Open-Meteo: ECMWF IFS/NCEP GFS/DWD ICON forecast, ERA5 archive, GloFAS v4 flood, CAMS air quality, geocoding
  * ISRIC SoilGrids 2.0
  * Copernicus Sentinel-2 L2A COGs via the Earth Search STAC API (read with rasterio)
  * GDACS
  * Agmarknet via data.gov.in
  * OSM Nominatim
* **Models and science:**
  * Gemini or Claude for planning and language.
  * FAO-56: Kc × ET₀, climate-adjusted Kc, and a root-zone water balance with stress coefficient Ks.
  * Stull wet-bulb and Delta-T spray rules.
  * IMD heavy-rain, heat-wave, cold-wave and rainfall-departure criteria.
  * Hutton late-blight criteria.
  * Gumbel EV1 2/5/20-year flood return levels from 40 years of reanalysis.
  * USDA texture triangle; Saxton-Rawls pedotransfer.
  * NDVI with scene-classification cloud masking.
* **Resilience:**
  * Retries with backoff, per-source circuit breakers, a stale-if-error cache and request coalescing.
  * Self-healing connectors, pydantic validation of every tool call, and history rollback.
  * A rule-based fallback.
  * 62 automated offline tests, CI, and a live smoke-test script.

## 3. Prototype link

`<Hugging Face Spaces / Render URL>`

## 4. Demo video

`<YouTube / Loom / Drive link>` (2½ minutes, script in `docs/DEMO_SCRIPT.md`)

## 5. GitHub repository

https://github.com/Sameer6556/code-benders

## 6. Pitch deck

`<Google Slides / Canva / PDF link>` (content in `docs/PITCH_DECK.md`)
