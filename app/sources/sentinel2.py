"""Copernicus Sentinel-2 L2A crop vigour (NDVI) for a farm, read straight from cloud-optimised GeoTIFFs.

Scenes are found with the public Earth Search STAC API (Element 84, AWS Open Data). For each
scene we read only a ~150 m x 150 m window around the farm from the red (B04), near-infrared
(B08) and scene-classification (SCL) COGs using HTTP range requests, mask clouds/shadows with
SCL, convert digital numbers to reflectance with the STAC `raster:bands` scale/offset, and
compute NDVI = (NIR - Red) / (NIR + Red).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from ..http import DataClient, Fetched, SourceError

log = logging.getLogger(__name__)

STAC_URL = "https://earth-search.aws.element84.com/v1/search"
COLLECTIONS = ["sentinel-2-c1-l2a", "sentinel-2-l2a"]
ASSET_KEYS = {"red": ("red", "B04", "b04"), "nir": ("nir", "B08", "b08"), "scl": ("scl", "SCL")}
# SCL classes kept as valid land observations: 4 = vegetation, 5 = bare soil (clouds, shadows, water dropped).
SCL_VALID = (4, 5)
WINDOW_HALF_PX = 7  # 15 x 15 pixels at 10 m = 150 m x 150 m around the farm point
GDAL_ENV = {
    "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",
    "CPL_VSIL_CURL_ALLOWED_EXTENSIONS": ".tif,.TIF,.tiff",
    "GDAL_HTTP_TIMEOUT": "20",
    "GDAL_HTTP_MAX_RETRY": "2",
    "GDAL_HTTP_RETRY_DELAY": "1",
    "VSI_CACHE": "TRUE",
}


@dataclass
class Scene:
    id: str
    date: str
    cloud_cover: float | None
    red: str
    nir: str
    scl: str | None
    red_scale: float
    red_offset: float
    nir_scale: float
    nir_offset: float


def _asset(assets: dict[str, Any], kind: str) -> dict[str, Any] | None:
    for key in ASSET_KEYS[kind]:
        if key in assets and assets[key].get("href"):
            return {**assets[key], "href": _https(assets[key]["href"])}
    return None


def _https(href: str) -> str:
    """Public-bucket s3:// links -> HTTPS, so GDAL reads them with plain range requests (no AWS SDK)."""
    if href.startswith("s3://"):
        bucket, _, key = href[5:].partition("/")
        return f"https://{bucket}.s3.amazonaws.com/{key}"
    return href


def _scale_offset(asset: dict[str, Any]) -> tuple[float, float]:
    bands = asset.get("raster:bands") or [{}]
    band = bands[0] if bands else {}
    return float(band.get("scale") or 0.0001), float(band.get("offset") or 0.0)


def parse_scenes(payload: dict[str, Any]) -> list[Scene]:
    best: dict[str, Scene] = {}
    for feat in (payload or {}).get("features") or []:
        assets = feat.get("assets") or {}
        props = feat.get("properties") or {}
        red, nir, scl = _asset(assets, "red"), _asset(assets, "nir"), _asset(assets, "scl")
        if not red or not nir or not props.get("datetime"):
            continue
        rs, ro = _scale_offset(red)
        ns, no = _scale_offset(nir)
        scene = Scene(
            id=str(feat.get("id")), date=str(props["datetime"])[:10], cloud_cover=props.get("eo:cloud_cover"),
            red=red["href"], nir=nir["href"], scl=scl["href"] if scl else None,
            red_scale=rs, red_offset=ro, nir_scale=ns, nir_offset=no,
        )
        # One observation per day: keep the least cloudy tile/processing of that date.
        prev = best.get(scene.date)
        if prev is None or (scene.cloud_cover or 100) < (prev.cloud_cover or 100):
            best[scene.date] = scene
    return sorted(best.values(), key=lambda s: s.date, reverse=True)


async def search_scenes(dc: DataClient, lat: float, lon: float, start: date, end: date,
                        max_cloud: float = 60.0) -> tuple[list[Scene], Fetched]:
    base: dict[str, Any] = {
        "intersects": {"type": "Point", "coordinates": [round(lon, 5), round(lat, 5)]},
        "datetime": f"{start.isoformat()}T00:00:00Z/{end.isoformat()}T23:59:59Z",
        "limit": 100,
    }
    filters = {"query": {"eo:cloud_cover": {"lt": max_cloud}},
               "sortby": [{"field": "properties.datetime", "direction": "desc"}]}
    # Self-healing: if a collection id is retired or the query/sort extensions are rejected, retry simpler.
    variants = [{"collections": COLLECTIONS, **filters}, {"collections": ["sentinel-2-l2a"], **filters},
                {"collections": ["sentinel-2-l2a"]}]
    for i, variant in enumerate(variants):
        try:
            fetched = await dc.post_json("earth-search", STAC_URL, {**base, **variant}, ttl_s=6 * 3600)
            break
        except SourceError as exc:
            if exc.status not in (400, 404, 422) or i == len(variants) - 1:
                raise
    scenes = parse_scenes(fetched.data if isinstance(fetched.data, dict) else {})
    return [s for s in scenes if s.cloud_cover is None or s.cloud_cover < max_cloud], fetched


def compute_ndvi(red_dn: Any, nir_dn: Any, scl: Any | None, scene: Scene) -> dict[str, Any] | None:
    """Pure NDVI computation on numpy arrays (unit-tested). Returns None when too cloudy."""
    import numpy as np

    red = red_dn.astype("float32") * scene.red_scale + scene.red_offset
    nir = nir_dn.astype("float32") * scene.nir_scale + scene.nir_offset
    valid = (red_dn > 0) & (nir_dn > 0) & (red > 0) & (nir > 0)
    if scl is not None:
        valid &= np.isin(scl, SCL_VALID)
    clear_fraction = float(valid.mean()) if valid.size else 0.0
    if clear_fraction < 0.5:
        return None
    ndvi = (nir[valid] - red[valid]) / (nir[valid] + red[valid])
    return {
        "date": scene.date,
        "ndvi_mean": round(float(np.mean(ndvi)), 3),
        "ndvi_p25": round(float(np.percentile(ndvi, 25)), 3),
        "ndvi_p75": round(float(np.percentile(ndvi, 75)), 3),
        "clear_fraction": round(clear_fraction, 2),
        "scene_cloud_cover": scene.cloud_cover,
        "scene_id": scene.id,
    }


def _read_scene(scene: Scene, lat: float, lon: float) -> dict[str, Any] | None:
    """Blocking COG window read (runs in a worker thread)."""
    import rasterio
    from rasterio.enums import Resampling
    from rasterio.warp import transform as warp_transform
    from rasterio.windows import Window, bounds, from_bounds, intersection

    with rasterio.Env(**GDAL_ENV):
        with rasterio.open(scene.red) as src:
            xs, ys = warp_transform("EPSG:4326", src.crs, [lon], [lat])
            row, col = src.index(xs[0], ys[0])
            win = Window(col - WINDOW_HALF_PX, row - WINDOW_HALF_PX, 2 * WINDOW_HALF_PX + 1, 2 * WINDOW_HALF_PX + 1)
            win = intersection(win, Window(0, 0, src.width, src.height))
            red = src.read(1, window=win)
            win_bounds = bounds(win, src.transform)
        with rasterio.open(scene.nir) as src:
            nir = src.read(1, window=win)  # B08 shares B04's 10 m grid
        scl = None
        if scene.scl:
            with rasterio.open(scene.scl) as src:
                scl_win = from_bounds(*win_bounds, transform=src.transform).round_offsets().round_lengths()
                scl = src.read(1, window=scl_win, out_shape=red.shape, resampling=Resampling.nearest)
    return compute_ndvi(red, nir, scl, scene)


async def ndvi_series(dc: DataClient, lat: float, lon: float, *, wanted: int = 5, max_scenes: int = 10,
                      lookback_days: int = 120) -> dict[str, Any]:
    """Recent cloud-free NDVI observations plus the same period last year for comparison."""
    try:
        import rasterio  # noqa: F401  (optional dependency check)
    except ImportError as exc:
        raise SourceError("earth-search", "rasterio is not installed; satellite analysis disabled",
                          retryable=False) from exc

    today = date.today()
    scenes, fetched = await search_scenes(dc, lat, lon, today - timedelta(days=lookback_days), today)
    last_year_mid = today - timedelta(days=365)
    ly_scenes, _ = await search_scenes(dc, lat, lon, last_year_mid - timedelta(days=25),
                                       last_year_mid + timedelta(days=25), max_cloud=40)

    sem = asyncio.Semaphore(4)

    async def read(scene: Scene) -> dict[str, Any] | None:
        async with sem:
            try:
                return await asyncio.to_thread(_read_scene, scene, lat, lon)
            except Exception as exc:  # one bad COG must not sink the whole series
                log.info("NDVI read failed for %s: %s", scene.id, exc)
                return None

    recent = [r for r in await asyncio.gather(*(read(s) for s in scenes[:max_scenes])) if r][:wanted]
    last_year = None
    for r in await asyncio.gather(*(read(s) for s in sorted(ly_scenes, key=lambda s: s.cloud_cover or 100)[:3])):
        if r:
            last_year = r
            break
    return {"recent": sorted(recent, key=lambda r: r["date"]), "last_year": last_year,
            "scenes_considered": len(scenes), "fetched": fetched}
