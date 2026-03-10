"""
OpenWatch - Flask backend
Exposes publicly accessible camera feeds for privacy awareness.
"""

import ipaddress
import logging
import os
import re

import httpx
from dotenv import load_dotenv
from flask import Flask, Response, jsonify, request
from flask_cors import CORS
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

from geocoder import geocode_location
from shodan_client import SNAPSHOT_PATHS, ShodanClient
from stream_manager import get_stream_file, start_stream, stop_stream

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
# httpx logs full URLs which would expose the Shodan API key
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger(__name__)

_frontend_dir = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..",
    "frontend",
)
app = Flask(
    __name__,
    static_folder=_frontend_dir,
    static_url_path="",
)
CORS(
    app,
    origins=os.environ.get(
        "ALLOWED_ORIGIN", "http://localhost:3000"
    ),
)
app.config["MAX_CONTENT_LENGTH"] = 1 * 1024 * 1024  # 1 MB
limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=[],
    storage_uri=os.environ.get(
        "RATE_LIMIT_STORAGE_URI", "memory://"
    ),
)

_STREAM_ID_RE = re.compile(r"^[0-9a-f]{32}$")


@app.after_request
def _security_headers(response: Response) -> Response:
    # Script hash is for the inline <script> in index.html.
    # Update the hash if that block changes.
    _script_hash = (
        "'sha256-lnTF2PGyP3c5UBstXI0ZR6aUC"
        "/1ZRNI8jf1Qb62i3q0='"
    )
    _cf = "https://static.cloudflareinsights.com"
    _carto = "https://*.basemaps.cartocdn.com"
    _cdn = "https://unpkg.com https://cdn.jsdelivr.net"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        f"script-src 'self' {_script_hash} {_cdn} {_cf}; "
        f"style-src 'self' 'unsafe-inline' https://unpkg.com; "
        f"img-src 'self' data: blob: {_carto}; "
        f"connect-src 'self' {_cdn} {_carto}; "
        "media-src 'self' blob:; "
        "frame-ancestors 'none'"
    )
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-Robots-Tag"] = (
        "noindex, nofollow, noarchive, nosnippet"
    )
    origin = os.environ.get("ALLOWED_ORIGIN", "")
    if origin.startswith("https://"):
        response.headers["Strict-Transport-Security"] = (
            "max-age=31536000; includeSubDomains"
        )
    return response


@app.get("/")
def serve_frontend():
    return app.send_static_file("index.html")


def _get_shodan_key() -> str:
    """Extract Shodan API key from the request header."""
    key = request.headers.get("X-Shodan-Key", "").strip()
    if not key:
        raise ValueError(
            "Shodan API key required. "
            "Enter your key in the settings panel above."
        )
    return key


# -------------------------------------------------------------------
# Endpoints
# -------------------------------------------------------------------


@app.post("/api/search")
@limiter.limit("5/minute")
def search_cameras():
    """
    Geocode the given location and query Shodan for nearby
    cameras. Returns camera metadata only — no credentials are
    tested or bypassed.
    """
    body = request.get_json(silent=True) or {}
    location = (body.get("location") or "").strip()
    try:
        radius_miles = float(body.get("radius_miles", 5.0))
        max_results = int(body.get("max_results", 100))
    except ValueError, TypeError:
        return jsonify(
            error=(
                "'radius_miles' and 'max_results' "
                "must be numbers."
            )
        ), 400

    if not location or len(location) < 2 or len(location) > 200:
        return jsonify(
            error="'location' must be 2–200 characters."
        ), 400
    if not (0.5 <= radius_miles <= 50.0):
        return jsonify(
            error="'radius_miles' must be between 0.5 and 50."
        ), 400
    max_results = max(10, min(200, max_results))

    # Optional: caller may supply up to 5 custom base query
    # strings. The geo: filter is still appended server-side.
    base_queries: list[str] | None = None
    queries_raw = body.get("queries")
    if queries_raw is not None:
        if not isinstance(queries_raw, list):
            return jsonify(
                error="'queries' must be a list of strings."
            ), 400
        cleaned: list[str] = []
        for q in queries_raw[:5]:
            if not isinstance(q, str):
                return jsonify(
                    error="Each query must be a string."
                ), 400
            q = q.strip()
            if not q:
                continue
            if len(q) > 300:
                return jsonify(
                    error="Query string too long (max 300 chars)."
                ), 400
            cleaned.append(q)
        if cleaned:
            base_queries = cleaned

    coords = geocode_location(location)
    if coords is None:
        return jsonify(
            error=f"Could not geocode location: '{location}'. "
            "Try a city name, zip code, or full address."
        ), 400

    try:
        api_key = _get_shodan_key()
    except ValueError as exc:
        return jsonify(error=str(exc)), 401

    try:
        shodan = ShodanClient(api_key)
        cameras = shodan.search_cameras(
            lat=coords["lat"],
            lng=coords["lng"],
            radius_miles=radius_miles,
            city_hint=location,
            max_results=max_results,
            base_queries=base_queries,
        )
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        if status == 401:
            return jsonify(
                error="Invalid Shodan API key."
            ), 502
        if status == 403:
            return jsonify(
                error=(
                    "Shodan membership required. "
                    "The geo: radius filter is only available "
                    "on paid Shodan plans. "
                    "Upgrade at shodan.io."
                ),
                upgrade_required=True,
            ), 402
        if status == 429:
            return jsonify(
                error=(
                    "Shodan rate limit reached. "
                    "Try again shortly."
                )
            ), 429
        return jsonify(
            error=f"Shodan API error: {status}"
        ), 502
    except httpx.HTTPError as exc:
        return jsonify(
            error=f"Shodan request failed: {exc}"
        ), 502

    return jsonify(
        center=coords,
        cameras=cameras,
        query_location=location,
        radius_miles=radius_miles,
        total=len(cameras),
    )


def _proxy_enabled() -> bool:
    return (
        os.environ.get("PROXY_ENABLED", "false").lower()
        == "true"
    )


@app.get("/api/proxy/image")
@limiter.limit("30/minute")
def proxy_image():
    """
    Proxy a still image from a camera, trying multiple snapshot
    paths. Only fetches images that respond without credentials.
    Does not attempt authentication of any kind.

    NOTE: Each call tries up to ~11 snapshot paths (brand-specific
    + generic) against the target, so one inbound request fans out
    to multiple outbound requests. The 30/min rate limit on the
    client side bounds this amplification.
    """
    if not _proxy_enabled():
        return jsonify(
            error="Live proxy is disabled. "
            "Set PROXY_ENABLED=true in backend/.env to enable."
        ), 503

    ip = request.args.get("ip", "")
    brand = request.args.get("brand", "_generic")

    try:
        port = int(request.args.get("port", "80"))
    except ValueError:
        return jsonify(error="Invalid port."), 400
    if not (1 <= port <= 65535):
        return jsonify(error="Invalid port."), 400

    try:
        _reject_private_ip(ip)
    except ValueError as exc:
        return jsonify(error=str(exc)), 400

    scheme = "https" if port == 443 else "http"
    port_str = "" if port in (80, 443) else f":{port}"
    base = f"{scheme}://{ip}{port_str}"

    paths = (
        SNAPSHOT_PATHS.get(brand, [])
        + SNAPSHOT_PATHS["_generic"]
    )

    resp = _try_snapshot_paths(base, paths)

    if resp is None:
        return jsonify(error="No accessible image found."), 502

    if resp.status_code in (401, 403, 407):
        return jsonify(
            error=(
                "Stream requires authentication "
                "— not displayed."
            )
        ), 403

    content_type = resp.headers.get(
        "content-type", "image/jpeg"
    )
    return Response(
        resp.content,
        content_type=content_type,
        headers={"Cache-Control": "no-store"},
    )


def _try_snapshot_paths(
    base: str, paths: list[str]
) -> httpx.Response | None:
    """Try snapshot paths and return the first usable response."""
    with httpx.Client(
        timeout=4.0,
        # SECURITY: must stay False — redirects could target
        # internal IPs, bypassing _reject_private_ip().
        follow_redirects=False,
    ) as client:
        for path in paths:
            try:
                resp = client.get(
                    f"{base}{path}",
                    headers={"User-Agent": "Mozilla/5.0"},
                )
            except httpx.HTTPError:
                continue
            if resp.status_code in (401, 403, 407):
                return resp
            if resp.status_code == 200:
                ct = resp.headers.get("content-type", "")
                if ct.startswith(("image/", "multipart/")):
                    return resp
    return None


@app.post("/api/stream/start")
@limiter.limit("5/minute")
def stream_start():
    """
    Start an RTSP→HLS transcoding session for a camera.
    Probes candidate RTSP URLs and spawns ffmpeg if one responds.
    Returns a stream_id used to fetch HLS segments.
    """
    if not _proxy_enabled():
        return jsonify(
            error="Live streaming is disabled. "
            "Set PROXY_ENABLED=true in backend/.env to enable."
        ), 503

    body = request.get_json(silent=True) or {}
    ip = (body.get("ip") or "").strip()
    brand = (body.get("brand") or "_generic").strip()

    try:
        port = int(body.get("port", 554))
        assert 1 <= port <= 65535
    except TypeError, ValueError, AssertionError:
        return jsonify(error="Invalid port."), 400

    try:
        _reject_private_ip(ip)
    except ValueError as exc:
        return jsonify(error=str(exc)), 400

    stream_id = start_stream(
        ip, port, brand, request.remote_addr or ""
    )
    if stream_id is None:
        return jsonify(
            error="Could not connect to RTSP stream. "
            "Camera may be offline, auth-protected, "
            "or unreachable."
        ), 502

    return jsonify(stream_id=stream_id)


@app.get("/api/stream/<stream_id>/<filename>")
@limiter.limit("120/minute")
def stream_file(stream_id: str, filename: str):
    """Serve HLS playlist or segment files for an active stream."""
    if not _STREAM_ID_RE.match(stream_id):
        return jsonify(error="Invalid stream ID."), 400

    if (
        not filename.replace(".", "")
        .replace("-", "")
        .isalnum()
    ):
        return jsonify(error="Invalid filename."), 400
    if not (
        filename.endswith(".m3u8")
        or filename.endswith(".ts")
    ):
        return jsonify(error="Invalid file type."), 400

    data = get_stream_file(stream_id, filename)
    if data is None:
        return jsonify(
            error="Stream or segment not found."
        ), 404

    mime = (
        "application/vnd.apple.mpegurl"
        if filename.endswith(".m3u8")
        else "video/mp2t"
    )

    return Response(
        data,
        content_type=mime,
        headers={"Cache-Control": "no-cache"},
    )


@app.delete("/api/stream/<stream_id>")
@limiter.limit("10/minute")
def stream_stop(stream_id: str):
    """Stop and clean up an active stream."""
    if not _STREAM_ID_RE.match(stream_id):
        return jsonify(error="Invalid stream ID."), 400

    stop_stream(stream_id)
    return jsonify(status="stopped")


@app.get("/api/health")
def health():
    return jsonify(
        status="ok", proxy_enabled=_proxy_enabled()
    )


# -------------------------------------------------------------------
# Helpers
# -------------------------------------------------------------------


def _reject_private_ip(ip: str) -> None:
    """
    Refuse to proxy requests to RFC-1918 / loopback addresses.
    Prevents SSRF against internal networks.
    """
    lower = ip.lower()
    if lower in ("localhost", "::1") or lower.endswith(".local"):
        raise ValueError("Private addresses not allowed.")

    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        raise ValueError(
            "Only IP addresses are accepted."
        ) from None

    # Unwrap IPv4-mapped IPv6 (e.g. ::ffff:127.0.0.1) so the
    # private/loopback checks below apply to the inner IPv4.
    if (
        isinstance(addr, ipaddress.IPv6Address)
        and addr.ipv4_mapped is not None
    ):
        addr = addr.ipv4_mapped

    if (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_reserved
        or addr.is_multicast
        or addr.is_unspecified
    ):
        raise ValueError("Private addresses not allowed.")


# -------------------------------------------------------------------
# Entry point
# -------------------------------------------------------------------

if __name__ == "__main__":
    debug = (
        os.environ.get("FLASK_DEBUG", "false").lower() == "true"
    )
    app.run(host="0.0.0.0", port=8000, debug=debug)
