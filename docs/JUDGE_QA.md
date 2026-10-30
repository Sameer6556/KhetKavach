# Judge Q&A prep (code ownership)

The rules say every member must explain the architecture, data flow and AI logic independently. Read
[ARCHITECTURE.md](ARCHITECTURE.md), then make sure each person can answer **all** of these without notes.

## Data and "no dummy data"

**Q: Where does each number on the screen come from?**
Every card footer names the live source and retrieval time, and the evidence ID links it to the agent trace. For example, rain comes from
the Open-Meteo forecast API, which blends ECMWF, GFS and ICON. Normals come from ERA5 1991-2020 via the Open-Meteo archive. River discharge
comes from GloFAS via the Open-Meteo flood API. Prices come from Agmarknet via the data.gov.in API.

**Q: Is anything hard-coded?**
Only published scientific constants, each cited in `app/agronomy/crops.py`: FAO-56 crop coefficients, stage lengths and depletion
fractions, plus IMD thresholds and disease-weather windows. No observations, prices or forecasts are stored in code. `tests/fakes.py`
contains synthetic API-shaped responses for the offline test suite only; the app never imports it (`grep -r fakes app/` returns nothing).

**Q: What happens when an API is down?**
`app/http.py` retries with backoff. After 4 consecutive failures, a circuit breaker fails fast for 60 s. If a cached response exists, it is
served and flagged stale with its timestamp. Otherwise only that card shows "unavailable", and the agent is told not to estimate the value.

## AI logic

**Q: Is this just a ChatGPT wrapper?**
No. The model never sees raw APIs. It calls 13 tools that run real computations, such as an FAO-56 water balance, Gumbel flood return
levels, Delta-T spray windows and NDVI from satellite rasters. The loop is ours (`app/agent/base.py` plus `gemini_agent.py` / `claude_agent.py`):

* parallel tool execution and pydantic validation;
* error hints for self-correction;
* a deterministic verifier with one revision;
* streaming to the UI;
* history rollback and a rule-based fallback.

**Q: How do you prevent hallucinations?**
Four layers:

1. The system prompt forbids facts that don't come from tools, and forbids computing new numbers.
2. Tools return compact facts with units, including pre-computed totals and gaps.
3. The agent must cite `[E#]`.
4. `app/agent/verifier.py` extracts every number with a unit and checks it against the evidence (Indic digits normalised). Any failure
   sends the agent back to fix only those figures, and the UI shows the result.

**Q: Show self-correction.**
Ask for mandi prices of a commodity spelled oddly. The tool returns an error whose hint lists the commodities traded today, and the agent
retries with the right name; both calls are visible in the trace. Verifier-driven revisions also appear in the trace ("Verifier: …
asking the agent to self-correct").

**Q: Which model and why?**
Either Google Gemini (free tier; default `gemini-flash-latest`) or Anthropic Claude (`claude-opus-5-5`), chosen by which key is set
(`KK_LLM_PROVIDER`). Both run the same loop design: `app/agent/base.py` holds the shared lifecycle, and `gemini_agent.py` /
`claude_agent.py` hold only the provider-specific request and response handling. Gemini keeps its thought signatures by appending
the model's content verbatim. Claude uses adaptive thinking, prompt caching and server-side refusal fallback. Swapping models never
changes the tools, the verifier or the UI.

**Q: Multilingual?**
The LLM writes the answer in the farmer's language (11 options). The browser's Web Speech API does speech-to-text and text-to-speech
with the matching locale (`hi-IN`, `ta-IN`, …). Units stay as symbols, which keeps verification language-agnostic.

## Science

**Q: How is the irrigation advice computed?**
FAO-56 single-coefficient method:

1. ETc = Kc × ET₀. ET₀ is FAO Penman-Monteith from Open-Meteo. Kc is interpolated by growth stage from the sowing date (FAO tables 11-12)
   and adjusted for local wind and humidity (FAO eq. 62).
2. The root-zone water balance starts at the last soaking rain (≥ 25 mm) or the farmer's last irrigation. It uses the field's field
   capacity and wilting point from SoilGrids.
3. Irrigation is due when depletion exceeds readily available water (p × TAW). If at least half the deficit will be met by rain in the
   next two days, we advise holding instead and show the litres saved.

**Q: How do you call a flood?**
We query 9 GloFAS cells around the farm and keep the main river cell. We fit a Gumbel distribution to the annual maxima since 1984 to get
2-, 5- and 20-year flood levels, then compare the forecast ensemble median and maximum with those levels. This mirrors GloFAS alert
classes.

**Q: Disease risk?**
Weather favourability, not diagnosis. We count hours with RH ≥ 90% (a leaf-wetness proxy) inside each disease's temperature range. For
potato and tomato we apply the Hutton criteria for late blight. The advice always says to scout the field and consult the KVK.

## Engineering

**Q: How does the UI update live?**
Server-Sent Events. Each tool completion (dashboard) and each agent step (chat) is pushed as a JSON event; heartbeats keep proxies from
closing the stream.

**Q: How did you test without burning API credits?**
78 offline tests use an httpx mock transport that answers in each API's documented response shape, scripted Claude and Gemini
stand-ins (the Gemini one built from the real `google-genai` types), and
synthetic GeoTIFFs for the satellite path. `scripts/smoke_test.py` then checks the real integrations live.

**Q: Scaling?**
The app is stateless apart from SQLite. Move to Postgres plus a shared Redis cache and run several containers behind a load balancer.
The biggest cost lever is effort and caching on the LLM side; all data sources are free.

## Who explains what (but everyone must know the whole flow)

| Member | Primary area | Files |
|---|---|---|
| `<name 1>` | Agent loop, prompts, verifier | `app/agent/*` |
| `<name 2>` | Data connectors and resilience | `app/http.py`, `app/sources/*` |
| `<name 3>` | Agronomy science and tools | `app/agronomy/*`, `app/tools/*` |
| `<name 4>` | Frontend, voice, Sentinel | `app/static/*`, `app/sentinel/*` |
