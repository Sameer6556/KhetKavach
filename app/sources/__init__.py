"""Connectors for live public data sources. Each module does I/O + parsing only; analysis lives in `app.agronomy`."""

from ..http import DataClient, SourcePolicy

# Per-source politeness/timeout policies (rate limits from each provider's published terms).
POLICIES: dict[str, SourcePolicy] = {
    "open-meteo-forecast": SourcePolicy(timeout_s=20, retries=2),
    "open-meteo-archive": SourcePolicy(timeout_s=40, retries=2),
    "open-meteo-flood": SourcePolicy(timeout_s=30, retries=2),
    "open-meteo-air": SourcePolicy(timeout_s=20, retries=2),
    "open-meteo-geocoding": SourcePolicy(timeout_s=10, retries=2),
    "soilgrids": SourcePolicy(timeout_s=30, retries=2, min_interval_s=1.0),
    "nominatim": SourcePolicy(timeout_s=10, retries=1, min_interval_s=1.1),
    "data-gov-in": SourcePolicy(timeout_s=25, retries=2),
    "gdacs": SourcePolicy(timeout_s=20, retries=1),
    "earth-search": SourcePolicy(timeout_s=25, retries=2),
}


def register_policies(dc: DataClient) -> None:
    for name, policy in POLICIES.items():
        dc.register(name, policy)
