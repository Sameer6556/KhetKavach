"""Connector parsers must tolerate the variations real APIs produce."""

import json
from datetime import date, datetime, timezone

import httpx
import pytest

from app.db import Database
from app.http import DataClient, Fetched, SourceError
from app.sources import gdacs, mandi, nominatim, register_policies, sentinel2, soilgrids


def test_mandi_normalises_alternate_field_spellings():
    rows = mandi.normalize_records([{"State": "Punjab", "District": "Ludhiana", "Market": "Khanna", "Commodity": "Wheat",
                                     "Variety": "Dara", "Grade": "FAQ", "Arrival_Date": "30/10/2026",
                                     "Min_x0020_Price": "2,400", "Max_x0020_Price": "2600", "Modal_x0020_Price": "2,500"}])
    assert rows[0]["arrival_date"] == "2026-10-30"
    assert rows[0]["modal_price"] == 2500.0 and rows[0]["min_price"] == 2400.0


def test_mandi_commodity_aliases():
    exact, words = mandi.candidate_names("rice")
    assert "Paddy(Dhan)(Common)" in exact
    assert mandi.matches_commodity("Paddy(Dhan)(Basmati)", exact, words)
    assert not mandi.matches_commodity("Wheat", exact, words)
    assert mandi.candidate_names("tamatar")[0] == ["Tomato"]


def test_gdacs_rss_fallback_parser():
    xml = ('<rss xmlns:gdacs="http://www.gdacs.org" xmlns:georss="http://www.georss.org/georss"><channel><item>'
           '<title>Orange cyclone</title><gdacs:eventtype>TC</gdacs:eventtype><gdacs:alertlevel>Orange</gdacs:alertlevel>'
           '<georss:point>15.5 86.8</georss:point></item></channel></rss>')
    events = gdacs.parse_rss(xml)
    assert events[0]["type_name"] == "Tropical cyclone" and events[0]["lat"] == 15.5
    with pytest.raises(SourceError):
        gdacs.parse_rss("<not xml")


def test_nominatim_district_cleanup():
    place = nominatim.parse({"address": {"town": "Niphad", "state_district": "Nashik District", "state": "Maharashtra",
                                         "country_code": "in"}})
    assert place["district"] == "Nashik" and place["country_code"] == "IN"


def test_soilgrids_all_null_is_an_error():
    payload = {"properties": {"layers": [{"name": "clay", "unit_measure": {"d_factor": 10},
                                          "depths": [{"label": "0-5cm", "values": {"mean": None}}]}]}}
    with pytest.raises(SourceError, match="no soil data"):
        soilgrids.parse(payload, Fetched(payload, "u", datetime.now(timezone.utc)))


def test_sentinel_s3_hrefs_become_https_and_days_deduplicate():
    feat = lambda i, cloud: {"id": f"S{i}", "properties": {"datetime": "2026-10-25T05:35:00Z", "eo:cloud_cover": cloud},
                             "assets": {"red": {"href": "s3://bucket/a/B04.tif"}, "nir": {"href": "s3://bucket/a/B08.tif"}}}
    scenes = sentinel2.parse_scenes({"features": [feat(1, 30.0), feat(2, 5.0)]})
    assert len(scenes) == 1 and scenes[0].id == "S2"
    assert scenes[0].red == "https://bucket.s3.amazonaws.com/a/B04.tif"


async def test_stac_search_retries_without_unsupported_extensions(tmp_path):
    bodies = []

    def handler(request):
        body = json.loads(request.content)
        bodies.append(body)
        if "query" in body:
            return httpx.Response(400, json={"code": "BadRequest", "description": "query extension not supported"})
        return httpx.Response(200, json={"features": [
            {"id": "x", "properties": {"datetime": "2026-10-25T05:35:00Z", "eo:cloud_cover": 80.0},
             "assets": {"red": {"href": "https://h/B04.tif"}, "nir": {"href": "https://h/B08.tif"}}},
            {"id": "y", "properties": {"datetime": "2026-10-20T05:35:00Z", "eo:cloud_cover": 10.0},
             "assets": {"red": {"href": "https://h/B04.tif"}, "nir": {"href": "https://h/B08.tif"}}}]})

    db = Database(tmp_path / "s.db")
    dc = DataClient(db, "tests", 5.0, transport=httpx.MockTransport(handler))
    register_policies(dc)
    scenes, _ = await sentinel2.search_scenes(dc, 20.0, 74.0, date(2026, 7, 1), date(2026, 10, 30), max_cloud=60)
    assert len(bodies) == 3 and "query" not in bodies[-1]
    assert [s.id for s in scenes] == ["y"]  # cloud filter applied client-side when the server can't
    await dc.aclose()
