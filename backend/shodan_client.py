"""
Shodan API client for discovering publicly exposed camera streams.

Only queries for devices that respond without authentication.
Does not attempt to bypass any access controls.
"""

import logging
import math
from concurrent.futures import ThreadPoolExecutor, as_completed

import httpx

log = logging.getLogger(__name__)

SHODAN_SEARCH_URL = "https://api.shodan.io/shodan/host/search"

# Query strategy: target cameras that are actually accessible, not just
# cameras that exist.
#
# Query A — cameras Shodan confirmed accessible at index time (no auth).
#   has_screenshot:true means Shodan's own crawler could load the feed.
#   Port filter keeps results to HTTP/camera ports without over-restricting
#   to the narrow `category:webcam` tag (sparse outside major metros).
SCREENSHOT_QUERY = "has_screenshot:true port:80,8080,8081,8888"

# Query B — software that defaults to no authentication.
#   MJPG-Streamer (Raspberry Pi/DIY), Yawcam, WebcamXP, webcam 7 are all
#   consumer/hobbyist tools that ship with auth disabled.
#   All bare-string terms — Shodan rejects OR across mixed filter types
#   (e.g. http.title: mixed with unqualified strings).
OPEN_SOFTWARE_QUERY = (
    '"MJPG-Streamer" OR "yawcam" OR "webcamXP" OR "webcam 7"'
)

# Query C — RTSP, excluding enterprise brands that are almost always
#   password-protected in real deployments.
RTSP_QUERY = (
    "port:554,8554 -product:hikvision -product:dahua -product:axis"
)

# Fields to request from Shodan.
# `screenshot` includes base64-encoded image data Shodan captured at
# index time — used directly in the UI, no proxy required.
RESULT_FIELDS = (
    "ip_str,port,hostnames,org,product,version,"
    "info,location,transport,timestamp,tags,data,screenshot"
)

# Known camera brands/products for classification
KNOWN_BRANDS = {
    "hikvision": "Hikvision",
    "dahua": "Dahua",
    "axis": "Axis",
    "foscam": "Foscam",
    "vivotek": "Vivotek",
    "bosch": "Bosch",
    "hanwha": "Hanwha",
    "avigilon": "Avigilon",
    "panasonic": "Panasonic",
    "sony": "Sony",
    "mobotix": "Mobotix",
    "milestone": "Milestone",
    "yawcam": "Yawcam",
    "webcamxp": "WebcamXP",
    "mjpg-streamer": "MJPG-Streamer",
    "netcam": "Netcam",
}

# Thumbnail paths to attempt per brand/generic
SNAPSHOT_PATHS = {
    "Hikvision": [
        "/ISAPI/Streaming/channels/101/picture",
        "/Streaming/Channels/101/picture",
    ],
    "Dahua": [
        "/cgi-bin/snapshot.cgi",
        "/cgi-bin/snapshot.cgi?channel=1",
    ],
    "Axis": [
        "/axis-cgi/jpg/image.cgi",
        "/axis-cgi/bitmap/image.bmp",
    ],
    "Foscam": [
        "/cgi-bin/CGIProxy.fcgi?cmd=snapPicture2",
    ],
    "Yawcam": [
        "/out.jpg",
    ],
    "MJPG-Streamer": [
        "/?action=snapshot",
        "/snapshot.jpg",
    ],
    "_generic": [
        "/snapshot.jpg",
        "/image.jpg",
        "/img/snapshot.cgi",
        "/cgi-bin/nph-update.cgi",
        "/webcapture.jpg",
        "/video.jpg",
        "/tmpfs/snap.jpg",
        "/cgi-bin/snapshot.cgi",
        "/onvif/snapshot",
        "/shot.jpg",
        "/cam/realmonitor",
    ],
}

MILES_TO_KM = 1.60934


def _haversine_km(
    lat1: float, lng1: float, lat2: float, lng2: float
) -> float:
    """Great-circle distance in kilometres between two points."""
    r = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lng2 - lng1)
    a = (
        math.sin(dphi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2)
        * math.sin(dlam / 2) ** 2
    )
    return 2 * r * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _classify_brand(match: dict) -> str:
    """
    Infer camera brand from Shodan product/info/data fields.
    Returns a human-readable brand string or 'Unknown'.
    """
    product = (match.get("product") or "").lower()
    info = (match.get("info") or "").lower()
    data = (match.get("data") or "").lower()
    combined = f"{product} {info} {data}"

    for key, label in KNOWN_BRANDS.items():
        if key in combined:
            return label
    return "Unknown"


def _build_thumbnail_url(
    ip: str, port: int, brand: str, transport: str
) -> str | None:
    """
    Return the most-likely snapshot URL for this camera.
    Only generates HTTP(S) URLs — RTSP streams are not proxied.
    """
    if port in (554, 8554) or transport == "udp":
        return None

    scheme = "https" if port == 443 else "http"
    port_str = "" if port in (80, 443) else f":{port}"
    base = f"{scheme}://{ip}{port_str}"

    paths = (
        SNAPSHOT_PATHS.get(brand, []) + SNAPSHOT_PATHS["_generic"]
    )
    if not paths:
        return None
    return f"{base}{paths[0]}"


def _parse_match(
    match: dict, center_lat: float, center_lng: float
) -> dict:
    """Extract and normalise relevant fields from a Shodan match."""
    loc = match.get("location") or {}
    cam_lat = loc.get("latitude")
    cam_lng = loc.get("longitude")

    distance_km: float | None = None
    distance_miles: float | None = None
    if cam_lat is not None and cam_lng is not None:
        distance_km = _haversine_km(
            center_lat, center_lng, cam_lat, cam_lng
        )
        distance_miles = distance_km / MILES_TO_KM

    ip = match.get("ip_str", "")
    port = match.get("port", 80)
    brand = _classify_brand(match)
    transport = match.get("transport", "tcp")

    thumbnail_url = _build_thumbnail_url(ip, port, brand, transport)

    shot = match.get("screenshot") or {}
    screenshot_b64: str | None = None
    screenshot_mime: str = "image/jpeg"
    if shot.get("data"):
        screenshot_b64 = shot["data"]
        screenshot_mime = shot.get("mime", "image/jpeg")

    return {
        "ip": ip,
        "port": port,
        "brand": brand,
        "product": match.get("product") or "",
        "version": match.get("version") or "",
        "org": match.get("org") or "",
        "hostnames": match.get("hostnames") or [],
        "transport": transport,
        "lat": cam_lat,
        "lng": cam_lng,
        "city": loc.get("city") or "",
        "country": loc.get("country_name") or "",
        "distance_km": (
            round(distance_km, 2)
            if distance_km is not None
            else None
        ),
        "distance_miles": (
            round(distance_miles, 2)
            if distance_miles is not None
            else None
        ),
        "screenshot_b64": screenshot_b64,
        "screenshot_mime": screenshot_mime,
        "thumbnail_url": thumbnail_url,
        "timestamp": match.get("timestamp") or "",
        "tags": match.get("tags") or [],
    }


def _mask_key(key: str) -> str:
    """Return a redacted form of the API key safe for logging."""
    if len(key) <= 4:
        return "****"
    return f"****{key[-4:]}"


class ShodanClient:
    def __init__(self, api_key: str):
        if not api_key:
            raise ValueError(
                "SHODAN_API_KEY is not set. "
                "Copy backend/.env.example to backend/.env "
                "and add your key."
            )
        self._key = api_key
        self._masked_key = _mask_key(api_key)

    def _run_single_query(
        self, query: str
    ) -> list[dict[str, object]]:
        """Execute one Shodan query and return raw matches."""
        log.info("Shodan query: %s", query)
        params = {
            "key": self._key,
            "query": query,
            "fields": RESULT_FIELDS,
            "minify": "false",
            "page": 1,
        }
        try:
            with httpx.Client(timeout=20.0) as client:
                resp = client.get(
                    SHODAN_SEARCH_URL, params=params
                )
                resp.raise_for_status()
            try:
                payload = resp.json()
            except ValueError:
                log.error(
                    "Shodan returned invalid JSON for '%s'",
                    query,
                )
                return []
            batch: list[dict[str, object]] = payload.get(
                "matches", []
            )
            log.info(
                "Shodan query '%s' returned %d matches (key: %s)",
                query,
                len(batch),
                self._masked_key,
            )
            return batch
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            log.error(
                "Shodan API error %s for query '%s' (key: %s)",
                status,
                query,
                self._masked_key,
            )
            if status in (401, 403, 429):
                raise
            return []
        except httpx.HTTPError as exc:
            log.error(
                "Shodan request failed for '%s' (key: %s): %s",
                query,
                self._masked_key,
                type(exc).__name__,
            )
            return []

    def _run_queries(
        self, queries: list[str]
    ) -> list[dict[str, object]]:
        """Execute Shodan queries concurrently and return combined matches."""
        if len(queries) == 1:
            return self._run_single_query(queries[0])

        matches: list[dict[str, object]] = []
        with ThreadPoolExecutor(
            max_workers=len(queries)
        ) as pool:
            futures = {
                pool.submit(self._run_single_query, q): q
                for q in queries
            }
            for future in as_completed(futures):
                matches.extend(future.result())
        return matches

    def search_cameras(
        self,
        lat: float,
        lng: float,
        radius_miles: float,
        city_hint: str = "",
        max_results: int = 100,
        base_queries: list[str] | None = None,
    ) -> list[dict]:
        """
        Search Shodan for publicly exposed cameras near (lat, lng).

        Queries target cameras that are actually accessible (no
        auth), not just cameras that exist. Falls back to city:
        filter if geo: returns nothing.

        Args:
            lat: Centre latitude.
            lng: Centre longitude.
            radius_miles: Search radius in miles.
            city_hint: City name for fallback query.
            max_results: Cap on returned results.
            base_queries: Optional override for default Shodan
                queries. The geo: filter is still appended.

        Returns:
            List of camera metadata dicts.
        """
        radius_km = radius_miles * MILES_TO_KM
        geo_filter = (
            f"geo:{lat:.4f},{lng:.4f},{max(1, int(radius_km))}"
        )

        active_queries = (
            base_queries
            if base_queries
            else [
                SCREENSHOT_QUERY,
                OPEN_SOFTWARE_QUERY,
                RTSP_QUERY,
            ]
        )
        geo_queries = [
            f"{q} {geo_filter}" for q in active_queries
        ]

        matches = self._run_queries(geo_queries)

        # Fallback: if geo: returned nothing (thin coverage or
        # plan limitation), retry first query with city name.
        if not matches and city_hint:
            fallback_base = active_queries[0]
            fallback = f'{fallback_base} city:"{city_hint}"'
            log.info(
                "geo: returned 0 results, "
                "trying city fallback: %s",
                fallback,
            )
            matches = self._run_queries([fallback])

        # Deduplicate by ip:port
        seen: set[str] = set()
        unique: list[dict[str, object]] = []
        for m in matches:
            key = f"{m.get('ip_str')}:{m.get('port')}"
            if key not in seen:
                seen.add(key)
                unique.append(m)
        matches = unique
        log.info("Total unique matches: %d", len(matches))

        cameras = []
        for match in matches[:max_results]:
            try:
                parsed = _parse_match(match, lat, lng)
                cameras.append(parsed)
            except Exception as exc:  # noqa: BLE001
                log.warning("Failed to parse match: %s", exc)

        cameras.sort(
            key=lambda c: (
                c["distance_km"]
                if c["distance_km"] is not None
                else float("inf")
            )
        )
        return cameras
