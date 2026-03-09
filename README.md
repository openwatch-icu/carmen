# OpenWatch

**Privacy awareness tool** — surfaces publicly accessible IP camera feeds
indexed by [Shodan](https://shodan.io) near any given location. Shows how
many cameras exist within reach of any address, with zero authentication
bypassed.

> **Educational / research use only.**
> Do not use this tool to surveil others. Unauthorised access to computer
> systems is illegal in most jurisdictions.

---

## How it works

1. **Geocoding** — location input is resolved to lat/lng via OpenStreetMap
   Nominatim (no API key required).
2. **Shodan query** — three parallel queries target cameras that are
   actually accessible: screenshot-confirmed devices, open-by-default
   software (MJPG-Streamer, Yawcam, etc.), and RTSP streams. Results are
   filtered by `geo:` radius and deduplicated.
3. **Brand detection** — Shodan's `product` and `data` fields are
   pattern-matched against known camera manufacturers.
4. **Thumbnail proxy** — the backend proxies snapshots via brand-specific
   paths. Devices that respond with `401/403` are skipped — no credential
   probing occurs.
5. **RTSP streaming** — for port-554 cameras, ffmpeg transcodes the RTSP
   feed to HLS segments served to the browser via HLS.js.
6. **Frontend** — Leaflet.js renders a dark CartoDB basemap with pins for
   each discovered device and a card grid with thumbnails.

---

## Prerequisites

- Python 3.14+
- A [Shodan](https://shodan.io) API key (the `geo:` radius filter requires
  a paid membership plan)
- `ffmpeg` and `ffprobe` on PATH (required for RTSP streaming only)

---

## Quick start

### Local development

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python main.py                    # Flask dev server on :8000
```

Open `http://localhost:8000` — the backend serves the frontend directly.

Your Shodan API key is entered in the browser's settings panel and sent
per-request via the `X-Shodan-Key` header. Nothing is stored server-side.

### Docker

```bash
docker compose up --build         # serves on :8000
```

### Environment variables

| Variable                 | Default                 | Purpose                               |
| ------------------------ | ----------------------- | ------------------------------------- |
| `ALLOWED_ORIGIN`         | `http://localhost:8000` | CORS allowed origin                   |
| `PROXY_ENABLED`          | `false`                 | Enable image proxy and RTSP streaming |
| `MAX_CONCURRENT_STREAMS` | `10`                    | Cap on simultaneous ffmpeg processes  |
| `FLASK_DEBUG`            | `false`                 | Flask debug mode                      |

---

## API

All endpoints are prefixed with `/api`. The Shodan API key is passed via
the `X-Shodan-Key` request header.

| Method   | Path                                | Purpose                                              |
| -------- | ----------------------------------- | ---------------------------------------------------- |
| `POST`   | `/api/search`                       | Geocode location + Shodan query, returns camera list |
| `GET`    | `/api/proxy/image?ip=&port=&brand=` | Proxy a JPEG snapshot (SSRF-protected)               |
| `POST`   | `/api/stream/start`                 | Start RTSP→HLS transcoding                           |
| `GET`    | `/api/stream/<id>/<file>`           | Serve HLS playlist/segments                          |
| `DELETE` | `/api/stream/<id>`                  | Stop transcoding                                     |
| `GET`    | `/api/health`                       | Health check                                         |

### `POST /api/search`

```json
{
  "location": "90210",
  "radius_miles": 5.0,
  "max_results": 100
}
```

---

## Limitations

- Shodan's `geo:` radius filter requires a **Membership** or higher plan.
  Free API keys return results without the radius constraint.
  [Get a key here](https://account.shodan.io/register)
- Results are bounded by what Shodan has indexed — no live network scanning
  is performed.
- Shodan's free tier is rate-limited and returns a small result set.

---

## License

[GNU Affero General Public License v3.0](LICENSE)
