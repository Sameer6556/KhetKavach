from datetime import date, timedelta

import pytest

from app.agronomy import climate, hydrology, soil, spray, stress
from app.agronomy.crops import CROPS, get_crop
from app.agronomy.water import adjust_kc, crop_stage, run_water_balance, wind_10m_to_2m


def test_wet_bulb_matches_stull_reference():
    # Stull (2011) worked example: 20 C, 50% RH -> 13.7 C
    assert spray.wet_bulb_stull(20, 50) == pytest.approx(13.7, abs=0.1)
    assert spray.delta_t(20, 50) == pytest.approx(6.3, abs=0.1)


def test_spray_hour_rules():
    assert spray.assess_hour("t", 24, 60, 8, 12, 0.0, 10, True).status == "good"
    assert spray.assess_hour("t", 24, 60, 8, 12, 1.0, 80, True).status == "unsuitable"  # rain wash-off
    assert spray.assess_hour("t", 24, 60, 25, 40, 0.0, 10, True).status == "unsuitable"  # wind drift
    assert spray.assess_hour("t", 24, 60, 8, 12, 0.0, 10, False).status == "unsuitable"  # night
    assert spray.assess_hour("t", 33, 40, 8, 12, 0.0, 10, True).status in ("marginal", "unsuitable")


def test_spray_windows_group_consecutive_good_hours():
    n = 10
    times = [f"2026-11-01T{h:02d}:00" for h in range(6, 6 + n)]
    res = spray.spray_windows(times, [24] * n, [60] * n, [8] * n, [12] * n, [0.0] * n, [5] * n, [1] * n, 0, n)
    assert res["windows"][0]["hours"] == n


def test_kc_interpolation_and_stages():
    wheat = get_crop("gehun")  # alias -> wheat
    sow = date(2026, 11, 1)
    assert crop_stage(wheat, sow, sow - timedelta(days=3)).stage == "pre_sowing"
    assert crop_stage(wheat, sow, sow + timedelta(days=5)).kc == pytest.approx(0.30)
    mid_dev = crop_stage(wheat, sow, sow + timedelta(days=15 + 12)).kc  # halfway through development
    assert 0.30 < mid_dev < 1.15
    assert crop_stage(wheat, sow, sow + timedelta(days=60)).stage == "mid_season"
    assert crop_stage(wheat, sow, sow + timedelta(days=200)).stage == "harvest_ready"


def test_fao_kc_climate_adjustment_direction():
    assert adjust_kc(1.15, 2.0, 45.0, 1.0) == pytest.approx(1.15)
    assert adjust_kc(1.15, 4.0, 25.0, 1.0) > 1.15  # windy and dry -> higher Kc
    assert wind_10m_to_2m(10.0) == pytest.approx(2.08, abs=0.02)


def test_water_balance_irrigates_when_dry_and_holds_for_rain():
    onion = CROPS["onion"]
    start = date.today() - timedelta(days=10)
    dates = [(start + timedelta(days=i)).isoformat() for i in range(20)]
    rain = [30.0] + [0.0] * 19
    et0 = [6.0] * 20
    plan = run_water_balance(onion, start - timedelta(days=60), dates, rain, et0, 10, 0.38, 0.23, "drip")
    assert "soaking rain" in plan.initialised_from
    assert plan.next_irrigation is not None
    assert plan.next_irrigation["gross_mm"] == pytest.approx(plan.next_irrigation["net_mm"] / 0.9, rel=0.01)

    rain_late = list(rain)
    for i in range(11, 20):
        rain_late[i] = 40.0
    held = run_water_balance(onion, start - timedelta(days=60), dates, rain_late, et0, 10, 0.38, 0.23, "drip")
    assert held.next_irrigation is None or held.deferrals


def test_rainfed_never_schedules_irrigation():
    plan = run_water_balance(CROPS["pearl_millet"], date.today() - timedelta(days=40),
                             [(date.today() + timedelta(days=i)).isoformat() for i in range(10)],
                             [0.0] * 10, [7.0] * 10, 0, 0.3, 0.15, "rainfed")
    assert plan.next_irrigation is None


def test_imd_categories():
    assert stress.imd_rain_category(70) == "heavy"
    assert stress.imd_rain_category(150) == "very heavy"
    assert stress.imd_rain_category(210) == "extremely heavy"
    assert stress.imd_rain_category(20) is None
    assert stress.imd_heat_wave(41.0, 36.0, hilly=False) == "heat wave"
    assert stress.imd_heat_wave(46.0, None, hilly=False) == "heat wave"
    assert stress.imd_heat_wave(43.5, 36.0, hilly=False) == "severe heat wave"
    assert stress.imd_cold_wave(3.5, 9.0, hilly=False) == "cold wave"
    assert stress.imd_cold_wave(1.5, 9.0, hilly=False) == "severe cold wave"
    assert climate.imd_category(160, 100) == ("large excess", 60.0)
    assert climate.imd_category(70, 100) == ("deficient", -30.0)
    assert climate.imd_category(1, 2)[0] == "climatologically dry period"


def test_hutton_criteria():
    times = [f"2026-11-0{d}T{h:02d}:00" for d in (1, 2, 3) for h in range(24)]
    temp = [12.0] * len(times)
    rh = [95.0 if h < 8 else 70.0 for _ in (1, 2, 3) for h in range(24)]
    periods = stress.hutton_periods(times, temp, rh)
    assert [p["date"] for p in periods] == ["2026-11-02", "2026-11-03"]
    assert stress.hutton_periods(times, [8.0] * len(times), rh) == []


def test_gumbel_return_levels_and_flood_classes():
    maxima = [1000, 1200, 900, 1500, 1300, 1100, 1700, 950, 1400, 1250, 1600, 1050]
    levels = hydrology.gumbel_return_levels(maxima)
    assert levels[2] < levels[5] < levels[20]
    assert hydrology.classify_flood(levels[5] + 1, None, levels)["level"] == "high"
    assert hydrology.classify_flood(10, levels[2] + 1, levels)["level"] == "watch"
    assert hydrology.classify_flood(10, 10, levels)["level"] == "low"


def test_soil_texture_and_pedotransfer():
    assert soil.usda_texture(40, 40, 20) == "loam"
    assert soil.usda_texture(20, 30, 50) == "clay"
    assert soil.usda_texture(90, 5, 5) == "sand"
    assert soil.usda_texture(10, 85, 5) == "silt"
    fc, wp = soil.saxton_rawls(40, 20, 1.0)
    assert fc == pytest.approx(0.271, abs=0.005) and wp == pytest.approx(0.132, abs=0.005)
    assert soil.organic_carbon_class(4.0) == "low" and soil.organic_carbon_class(9.0) == "high"


def test_climatology_window_and_normals():
    times, tmax, tmin, rain = [], [], [], []
    d = date(1991, 1, 1)
    while d <= date(2020, 12, 31):
        times.append(d.isoformat())
        tmax.append(30.0)
        tmin.append(18.0)
        rain.append(2.0 if d.month == 7 else 0.0)
        d += timedelta(days=1)
    clim = climate.Climatology(times, tmax, tmin, rain)
    assert clim.normal_temperature(date(2026, 3, 1)) == (30.0, 18.0)
    july = clim.window_rain(date(2026, 7, 1), 10)
    assert july["mean_mm"] == 20.0 and july["years"] == 30
    anomaly = climate.rain_anomaly(clim, date(2026, 7, 1), 10, 40.0)
    assert anomaly["imd_category"] == "large excess" and anomaly["departure_pct"] == 100.0
