# KhetKavach pitch deck (7 slides)

Paste each slide into Google Slides or Canva. Use one big visual per slide: live screenshots from **your deployed app**, never mock-ups.
Speaker notes are in *italics*; aim for about 30 seconds per slide.

---

## Slide 1: Title and team

**KhetKavach (खेत कवच): a climate-risk shield for every farm**
Agentic AI that turns 10 live public data services into verified, field-specific advice, by voice, in 11 Indian languages.

Team Code Benders: `<names, institute, roles>`
Forge Gen AI Buildathon · Theme 2: Climate, Resource Infrastructure & Global Supply Resilience

*Note: "One sentence: we built an AI agronomist that never makes up a number."*

---

## Slide 2: The problem

* About 146 million farm holdings in India; **86% are small or marginal** (Agriculture Census 2015-16).
* Climate volatility (erratic monsoon, heatwaves, flash floods) now decides seasons.
* Advisories come district-level, twice a week, in bulletin form. They don't know *my* crop stage, soil or irrigation method, and they
  can't answer *"should I irrigate today or wait for rain?"*
* The data exists (ECMWF forecasts, GloFAS flood ensembles, ERA5 normals, SoilGrids, Sentinel-2, Agmarknet), but it is scattered,
  technical and in English.
* Generic chatbots hallucinate weather and prices. On a farm, one wrong number costs a crop.

*Visual: a farmer's question in Hindi next to the six portals they would have to combine.*

---

## Slide 3: The solution

1. **Tap your field** → 12 live analyses stream in: weather + IMD hazards, rain/heat vs 30-year normal, crop stress and disease weather,
   FAO-56 irrigation schedule, spray windows, river-flood outlook, soil, mandi prices, disaster alerts, air quality, satellite NDVI.
2. **Ask by voice** in Hindi, Marathi, Tamil and 8 more languages. An AI agent (Gemini or Claude) plans, calls tools in parallel and answers with
   evidence citations, and the answer is read aloud.
3. **Every number is verified**: "✔ 9/9 figures traced to live sources".
4. **Kavach Sentinel** watches registered farms 24×7 and pushes alerts to Telegram: *"Hold irrigation, 72 mm rain on Thursday; saves about
   2 lakh litres/acre."*

*Visual: dashboard screenshot + chat with the trace panel.*

---

## Slide 4: How it works

*(insert the architecture diagram from the README)*

* **Agent loop on Gemini or Claude (configurable):** plan → parallel tool calls → grounded answer → deterministic verification → one
  self-correction → stream to the farmer.
* **13 tools** over **live APIs**: Open-Meteo (ECMWF/GFS/ICON, ERA5, GloFAS, CAMS), ISRIC SoilGrids, Sentinel-2 COGs, GDACS, Agmarknet
  (data.gov.in), OSM.
* **Science layer:** FAO-56 water balance, Delta-T spray rules, IMD heat/cold-wave and heavy-rain criteria, Hutton late-blight criteria,
  Gumbel 2/5/20-year flood levels, USDA texture.
* **Resilience:** retries, circuit breakers, stale-if-error cache, self-healing connectors, rule-based fallback, history rollback.

*Note: "The LLM never touches raw data. Tools compute the facts; the LLM reasons and explains; the verifier audits."*

---

## Slide 5: Innovation (AI reasoning and guardrails)

| Typical chatbot | KhetKavach |
|---|---|
| Answers from training data | Answers only from tool evidence fetched seconds ago |
| No sources | `[E3]` citation on every fact, with source and timestamp |
| Hallucinated numbers slip through | Verifier rejects untraceable mm, °C, ₹, % before display |
| Breaks on API errors | Tool errors carry hints → agent self-corrects (e.g. commodity name) |
| Down when the LLM is down | Rule-based advisor on the same live data |
| Reactive | Autonomous Sentinel with proactive alerts |

*Demo moment: show a tool error and the retry in the trace, then the verification badge.*

---

## Slide 6: Impact, USP and scalability

* **Saves water and input cost:** rain-aware irrigation deferral shows the litres per acre saved; spray windows avoid wash-off of
  expensive chemicals.
* **Protects yield:** heat stress at flowering, frost nights, late-blight weather and flood peaks flagged days in advance.
* **Better price realisation:** best mandi vs local mandi gap in ₹/quintal.
* **Inclusive:** voice-first in 11 languages; Telegram alerts work on low-end Android phones.
* **Scales at near-zero data cost:** all sources are free and national or global. One container serves any village in India (and works
  worldwide). Caching keeps the API load low.
* **Who adopts it:** FPOs, KVKs, state agriculture departments, agri-input companies, crop insurers (field-level risk), district disaster cells.

---

## Slide 7: Roadmap and live demo summary

**Live demo (what you'll see):**

1. Tap a farm → 12 live cards stream in.
2. Ask in Hindi by voice → watch the agent plan, call 4 tools in parallel, cite and verify.
3. Mandi question with a wrong commodity → the agent self-corrects from the tool's hint.
4. Register a farm → Sentinel raises an alert → it arrives on Telegram.

**Next 3 months:**

* WhatsApp/IVR for feature phones.
* Bhashini Indic speech.
* Farm polygons.
* IMD district warnings and ISRO Bhuvan layers.
* Pest models.
* NDVI-based yield-risk score for insurers.
* Pilot with one FPO (500 farmers).

**Ask:** pilot partners and mentorship.

Links: `<live app URL>` · `<GitHub>` · `<demo video>`
