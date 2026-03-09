"""
Geocoding via OpenStreetMap Nominatim (no API key required).
"""

import logging

import httpx

log = logging.getLogger(__name__)

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
HEADERS = {"User-Agent": "OpenWatch/1.0 (privacy-awareness-tool)"}


async def geocode_location(
    query: str,
) -> dict | None:
    """
    Convert a zip code, city name, or address string to lat/lng.

    Returns a dict with keys: lat, lng, display_name
    Returns None if the location could not be resolved.
    """
    params = {
        "q": query,
        "format": "json",
        "limit": 1,
        "addressdetails": 0,
    }

    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            resp = await client.get(
                NOMINATIM_URL,
                params=params,
                headers=HEADERS,
            )
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            log.error("Nominatim request failed: %s", exc)
            return None

        try:
            results = resp.json()
        except ValueError:
            log.error("Nominatim returned invalid JSON")
            return None

    if not results:
        log.warning("Nominatim returned no results for: %s", query)
        return None

    top = results[0]
    return {
        "lat": float(top["lat"]),
        "lng": float(top["lon"]),
        "display_name": top.get("display_name", query),
    }
