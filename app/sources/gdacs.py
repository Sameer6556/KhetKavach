"""GDACS (Global Disaster Alert and Coordination System, UN/EC JRC) live disaster events.

Primary: the GDACS JSON event API. Fallback: the long-standing GDACS RSS feed, so the alert
check keeps working if one interface changes or is down.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import date, timedelta
from typing import Any

from ..http import DataClient, Fetched, SourceError

JSON_URL = "https://www.gdacs.org/gdacsapi/api/events/geteventlist/SEARCH"
RSS_URL = "https://www.gdacs.org/xml/rss.xml"
EVENT_TYPES = {"EQ": "Earthquake", "TC": "Tropical cyclone", "FL": "Flood", "VO": "Volcano",
               "DR": "Drought", "WF": "Wildfire", "TS": "Tsunami"}
NS = {"gdacs": "http://www.gdacs.org", "geo": "http://www.w3.org/2003/01/geo/wgs84_pos#",
      "georss": "http://www.georss.org/georss"}


def parse_json(payload: dict[str, Any]) -> list[dict[str, Any]]:
    events = []
    for feat in (payload or {}).get("features") or []:
        p = feat.get("properties") or {}
        lat = lon = None
        geom = feat.get("geometry") or {}
        if geom.get("type") == "Point" and len(geom.get("coordinates") or []) >= 2:
            lon, lat = geom["coordinates"][0], geom["coordinates"][1]
        elif len(feat.get("bbox") or []) == 4:
            b = feat["bbox"]
            lon, lat = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
        if lat is None or lon is None:
            continue
        etype = str(p.get("eventtype") or "").upper()
        events.append({
            "type": etype, "type_name": EVENT_TYPES.get(etype, etype),
            "name": p.get("name") or p.get("eventname") or EVENT_TYPES.get(etype, "Event"),
            "alert_level": str(p.get("alertlevel") or "").capitalize() or None,
            "country": p.get("country"),
            "from": str(p.get("fromdate") or "")[:10] or None, "to": str(p.get("todate") or "")[:10] or None,
            "severity": (p.get("severitydata") or {}).get("severitytext"),
            "url": (p.get("url") or {}).get("report") if isinstance(p.get("url"), dict) else p.get("url"),
            "lat": float(lat), "lon": float(lon),
        })
    return events


def parse_rss(xml_text: str) -> list[dict[str, Any]]:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise SourceError("gdacs", f"unreadable RSS feed ({exc})", retryable=False) from None
    events = []
    for item in root.iter("item"):
        lat = item.findtext("geo:Point/geo:lat", namespaces=NS)
        lon = item.findtext("geo:Point/geo:long", namespaces=NS)
        if lat is None or lon is None:
            point = (item.findtext("georss:point", namespaces=NS) or "").split()
            if len(point) == 2:
                lat, lon = point
        if lat is None or lon is None:
            continue
        etype = (item.findtext("gdacs:eventtype", namespaces=NS) or "").upper()
        events.append({
            "type": etype, "type_name": EVENT_TYPES.get(etype, etype),
            "name": item.findtext("title"),
            "alert_level": (item.findtext("gdacs:alertlevel", namespaces=NS) or "").capitalize() or None,
            "country": item.findtext("gdacs:country", namespaces=NS),
            "from": (item.findtext("gdacs:fromdate", namespaces=NS) or "")[:16] or None,
            "to": (item.findtext("gdacs:todate", namespaces=NS) or "")[:16] or None,
            "severity": item.findtext("gdacs:severity", namespaces=NS),
            "url": item.findtext("link"),
            "lat": float(lat), "lon": float(lon),
        })
    return events


async def fetch_events(dc: DataClient, days_back: int = 14) -> tuple[list[dict[str, Any]], Fetched]:
    today = date.today()
    params = {
        "eventlist": "EQ;TC;FL;VO;DR;WF", "alertlevel": "green;orange;red",
        "fromdate": (today - timedelta(days=days_back)).isoformat(), "todate": today.isoformat(),
    }
    try:
        fetched = await dc.get("gdacs", JSON_URL, params, ttl_s=30 * 60)
        events = parse_json(fetched.data if isinstance(fetched.data, dict) else {})
        if events or (isinstance(fetched.data, dict) and "features" in fetched.data):
            return events, fetched
    except SourceError:
        pass
    fetched = await dc.get("gdacs", RSS_URL, ttl_s=30 * 60, as_text=True)
    return parse_rss(fetched.data), fetched
