"""End-to-end tool tests against the offline fake upstream (same response shapes as the live APIs)."""

from datetime import date, timedelta

import pytest

from tests.conftest import FARM

SOWN = (date.today() - timedelta(days=50)).isoformat()


async def run(executor, name, **args):
    result = await executor.run(name, args)
    assert result.ok, result.error
    return result.output


async def test_weather_outlook_flags_imd_heavy_rain(executor):
    out = await run(executor, "get_weather_outlook", **FARM, days=7)
    assert out.data["summary"]["days"] == 7
    assert any(h["hazard"] == "heavy rain" for h in out.data["hazards"])
    assert out.level in ("high", "severe")
    assert "normal_tmax_c" in out.data["daily"][0]
    assert len(out.chart["daily"]) == 16


async def test_climate_anomaly_uses_1991_2020_normals(executor):
    out = await run(executor, "get_climate_anomaly", **FARM)
    assert out.data["last_30_days"]["days"] == 30
    assert out.data["next_7_days"]["imd_category"]
    assert out.data["tmax_next_7_days"]["normal_tmax_c"] is not None


async def test_soil_profile_converts_units(executor):
    out = await run(executor, "get_soil_profile", **FARM)
    d = out.data
    assert d["texture_class"] == "clay"
    # Thickness-weighted 0-30 cm mean of 7.9 (0-5), 8.4 (5-15), 8.9 (15-30) after the d_factor of 10.
    assert d["ph"] == pytest.approx((7.9 * 5 + 8.4 * 10 + 8.9 * 15) / 30, abs=0.05)
    assert d["field_capacity_vol_pct"] > d["wilting_point_vol_pct"]
    assert d["available_water_mm_per_m"] > 100


async def test_irrigation_plan_runs_fao56_balance(executor):
    out = await run(executor, "get_irrigation_plan", **FARM, crop="onion", sowing_date=SOWN, irrigation_method="drip")
    d = out.data
    assert "soaking rain" in d["balance_started_from"]
    assert 0 <= d["soil_water_today"]["depletion_pct"] <= 100
    assert len(d["daily"]) == 7
    assert d["application_efficiency_pct"] == 90


async def test_spray_windows(executor):
    out = await run(executor, "get_spray_windows", **FARM, hours=72)
    assert out.data["good_windows"], out.data
    assert len(out.chart["hourly"]) > 24


async def test_crop_risk_late_blight_and_disease(executor):
    out = await run(executor, "get_crop_risk", **FARM, crop="potato", sowing_date=SOWN)
    d = out.data
    assert "late_blight_hutton_periods" in d
    assert d["heavy_rain_days"]
    assert out.level in ("high", "severe")


async def test_flood_risk_picks_main_river_and_return_levels(executor):
    out = await run(executor, "get_flood_risk", **FARM)
    d = out.data
    assert d["river_cell"]["cells_compared"] == 9
    assert d["years_of_record"] >= 30
    rl = d["return_levels_m3s"]
    assert rl["2_year"] < rl["5_year"] < rl["20_year"]
    assert d["alert_level"] in ("low", "watch", "moderate", "high", "severe")


async def test_mandi_prices_rank_markets_and_local_gap(executor):
    out = await run(executor, "get_mandi_prices", commodity="pyaz")  # Hindi alias -> Onion
    d = out.data
    assert d["state"] == "Maharashtra"
    assert d["best_markets"][0]["market"] == "Mumbai"
    assert d["district_markets"][0]["district"] == "Nashik"
    assert d["best_vs_local_gap"]["amount"] == 2600 - 2250


async def test_mandi_self_heals_filter_syntax(executor, upstream):
    upstream.mandi_filter_style = "keyword"
    out = await run(executor, "get_mandi_prices", commodity="onion", state="Maharashtra")
    assert out.data["markets_reporting"] == 5


async def test_mandi_unknown_commodity_returns_hint(executor):
    res = await executor.run("get_mandi_prices", {"commodity": "saffron", "state": "Maharashtra"})
    assert not res.ok
    assert "Onion" in res.error["hint"]  # lists what is traded today, so the agent can retry


async def test_disaster_alerts_within_radius(executor):
    out = await run(executor, "get_disaster_alerts", **FARM, radius_km=500)
    assert out.data["nearby_count"] == 1
    assert out.level == "high"  # Orange flood event nearby


async def test_air_quality(executor):
    out = await run(executor, "get_air_quality", **FARM)
    assert out.data["us_aqi_now"] == 158 and out.data["category_now"] == "unhealthy"


async def test_satellite_ndvi_from_cogs(executor):
    out = await run(executor, "get_satellite_crop_health", **FARM)
    d = out.data
    assert d["latest"]["ndvi"] == pytest.approx(0.62, abs=0.02)
    assert d["change_since_previous"] == pytest.approx(0.07, abs=0.03)
    assert d["same_period_last_year"]["ndvi"] == pytest.approx(0.58, abs=0.02)


async def test_location_and_geocode(executor):
    loc = await run(executor, "get_location_context", **FARM)
    assert loc.data["district"] == "Nashik" and loc.data["state"] == "Maharashtra"
    geo = await run(executor, "geocode_place", place="Nashik")
    assert geo.data["matches"][0]["lat"] == pytest.approx(19.997, abs=0.01)


async def test_invalid_inputs_are_reported_not_raised(executor):
    res = await executor.run("get_irrigation_plan", {"lat": 200, "lon": 74, "crop": "onion", "sowing_date": "yesterday"})
    assert not res.ok and res.error["problems"]
    res = await executor.run("get_crop_risk", {**FARM, "crop": "dragonfruit", "sowing_date": SOWN})
    assert not res.ok and "Use one of" in res.error["hint"]
    assert not (await executor.run("no_such_tool", {})).ok


async def test_source_outage_degrades_gracefully(executor, upstream):
    upstream.fail_hosts.add("rest.isric.org")
    res = await executor.run("get_soil_profile", FARM)
    assert not res.ok and "unavailable" in res.error["message"]
    # The irrigation plan still works, with a clearly flagged soil assumption.
    out = await run(executor, "get_irrigation_plan", **FARM, crop="wheat", sowing_date=SOWN)
    assert any("assumed" in n for n in out.notes)


async def test_evidence_ledger_records_every_success(executor):
    await run(executor, "get_air_quality", **FARM)
    await run(executor, "get_soil_profile", **FARM)
    ids = [e.id for e in executor.ledger.all()]
    assert ids == ["E1", "E2"]
    assert executor.ledger.get("E2").sources[0]["name"].startswith("ISRIC")
