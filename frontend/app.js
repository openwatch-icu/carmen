/* ============================================================
   OpenWatch — frontend logic
   ============================================================ */

// Empty string = same origin (works in production and local dev).
// Override via localStorage for split-origin dev setups:
//   localStorage.setItem('openwatch_api_base', 'http://localhost:8000')
const API_BASE = localStorage.getItem('openwatch_api_base') || '';

// Default Shodan base queries — match backend/shodan_client.py constants.
// The geo: filter is appended server-side; these are base strings only.
const DEFAULT_QUERIES = [
  'has_screenshot:true port:80,8080,8081,8888',
  '"MJPG-Streamer" OR "yawcam" OR "webcamXP" OR "webcam 7"',
  'port:554,8554 -product:hikvision -product:dahua -product:axis',
];

// Whether the backend proxy is enabled (fetched from /api/health on load).
// When false, live snapshot/stream buttons are hidden; only Shodan-cached
// screenshots are shown, and no direct connections to cameras are made.
let proxyEnabled = false;
fetch(`${API_BASE}/api/health`)
  .then(r => r.json())
  .then(d => { proxyEnabled = !!d.proxy_enabled; })
  .catch(() => {});

// ----- Live access consent gate (session-only) -----
let _liveConsent = false;
let _consentQueue = [];    // pending callbacks

const _overlay  = document.getElementById('consent-overlay');
const _checkBox = document.getElementById('consent-check');
const _acceptBtn = document.getElementById('consent-accept');
const _cancelBtn = document.getElementById('consent-cancel');

_checkBox.addEventListener('change', () => {
  _acceptBtn.disabled = !_checkBox.checked;
});

_cancelBtn.addEventListener('click', () => {
  _overlay.hidden = true;
  _consentQueue = [];
});

_acceptBtn.addEventListener('click', () => {
  _liveConsent = true;
  _overlay.hidden = true;
  const pending = _consentQueue.splice(0);
  pending.forEach(fn => fn());
});

function requireConsent(callback) {
  if (_liveConsent) { callback(); return; }
  _consentQueue.push(callback);
  if (!_overlay.hidden) return;  // already showing
  _checkBox.checked = false;
  _acceptBtn.disabled = true;
  _overlay.hidden = false;
}

// API key management
const apikeyInput  = document.getElementById('apikey-input');
const apikeyStatus = document.getElementById('apikey-status');
const APIKEY_STORAGE_KEY = 'openwatch_shodan_key';

function loadApiKey() {
  const saved = localStorage.getItem(APIKEY_STORAGE_KEY) || '';
  apikeyInput.value = saved;
  updateKeyStatus(saved);
  return saved;
}

function updateKeyStatus(key) {
  if (key) {
    apikeyStatus.textContent = 'saved';
    apikeyStatus.className = 'apikey-status saved';
  } else {
    apikeyStatus.textContent = 'not set';
    apikeyStatus.className = 'apikey-status empty';
  }
}

apikeyInput.addEventListener('input', () => {
  const key = apikeyInput.value.trim();
  if (key) {
    localStorage.setItem(APIKEY_STORAGE_KEY, key);
  } else {
    localStorage.removeItem(APIKEY_STORAGE_KEY);
  }
  updateKeyStatus(key);
});

loadApiKey();

// DOM refs
const searchInput  = document.getElementById('search-input');
const radiusSelect = document.getElementById('radius-select');
const limitSelect  = document.getElementById('limit-select');
const searchBtn    = document.getElementById('search-btn');
const statusBar    = document.getElementById('status-bar');
const statusDot    = document.getElementById('status-dot');
const statusText   = document.getElementById('status-text');
const cameraGrid   = document.getElementById('camera-grid');
const gridHeader   = document.getElementById('grid-header');
const gridSort     = document.getElementById('grid-sort');
const imgFilter    = document.getElementById('img-filter');
const gridCount    = document.getElementById('grid-count');

// Advanced query panel elements
const advToggle = document.getElementById('adv-toggle');
const advArrow  = document.getElementById('adv-arrow');
const advPanel  = document.getElementById('adv-panel');
const advReset  = document.getElementById('adv-reset');
const qInputs   = [
  document.getElementById('q-screenshot'),
  document.getElementById('q-software'),
  document.getElementById('q-rtsp'),
];

// Pre-fill inputs with defaults
qInputs.forEach((el, i) => { el.value = DEFAULT_QUERIES[i]; });

function markModified() {
  qInputs.forEach((el, i) => {
    el.classList.toggle('modified', el.value.trim() !== DEFAULT_QUERIES[i]);
  });
}

qInputs.forEach(el => el.addEventListener('input', markModified));

advToggle.addEventListener('click', () => {
  const open = advPanel.style.display !== 'none';
  advPanel.style.display = open ? 'none' : 'block';
  advArrow.classList.toggle('open', !open);
});

advReset.addEventListener('click', () => {
  qInputs.forEach((el, i) => { el.value = DEFAULT_QUERIES[i]; });
  markModified();
});

// Returns the current queries if any differ from defaults, else null
// (null tells the backend to use its own defaults — no extra JSON payload).
function getQueries() {
  const vals = qInputs.map(el => el.value.trim()).filter(Boolean);
  if (!vals.length) return null;
  const changed = vals.some((v, i) => v !== DEFAULT_QUERIES[i]);
  return changed ? vals : null;
}

// ---------------------------------------------------------------
// Map setup
// ---------------------------------------------------------------
const map = L.map('map', {
  center: [39.8283, -98.5795], // geographic centre of continental US
  zoom: 4,
  zoomControl: true,
});

L.tileLayer(
  'https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png',
  {
    attribution: '&copy; OpenStreetMap &amp; CartoDB',
    subdomains: 'abcd',
    maxZoom: 19,
  }
).addTo(map);

// Custom camera marker icon
function makeIcon(accessible) {
  const color = accessible ? '#22cc22' : '#cc6600';
  const svg = `
    <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24"
         viewBox="0 0 24 24" fill="none">
      <circle cx="12" cy="12" r="10"
        fill="#0f0f0f" stroke="${color}" stroke-width="1.5"/>
      <circle cx="12" cy="12" r="4" fill="${color}" opacity="0.85"/>
      <line x1="12" y1="2" x2="12" y2="6"
        stroke="${color}" stroke-width="1.5"/>
      <line x1="22" y1="12" x2="18" y2="12"
        stroke="${color}" stroke-width="1.5"/>
      <line x1="12" y1="22" x2="12" y2="18"
        stroke="${color}" stroke-width="1.5"/>
      <line x1="2" y1="12" x2="6" y2="12"
        stroke="${color}" stroke-width="1.5"/>
    </svg>`;
  return L.divIcon({
    html: svg,
    className: '',
    iconSize: [24, 24],
    iconAnchor: [12, 12],
    popupAnchor: [0, -14],
  });
}

const centerIcon = L.divIcon({
  html: `<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16"
              viewBox="0 0 16 16">
    <circle cx="8" cy="8" r="6" fill="none"
      stroke="#cc2200" stroke-width="1.5"/>
    <circle cx="8" cy="8" r="2" fill="#cc2200"/>
  </svg>`,
  className: '',
  iconSize: [16, 16],
  iconAnchor: [8, 8],
});

let markers      = [];
let centerMarker = null;
let searchCircle = null;

function clearMap() {
  markers.forEach(m => map.removeLayer(m));
  markers = [];
  if (centerMarker) { map.removeLayer(centerMarker); centerMarker = null; }
  if (searchCircle) { map.removeLayer(searchCircle); searchCircle = null; }
}

function plotCameras(cameras, center, radiusMiles) {
  clearMap();

  const radiusM = radiusMiles * 1609.34;
  searchCircle = L.circle([center.lat, center.lng], {
    radius: radiusM,
    color: '#cc2200',
    weight: 1,
    opacity: 0.4,
    fill: false,
    dashArray: '4 4',
  }).addTo(map);

  centerMarker = L.marker([center.lat, center.lng], {
    icon: centerIcon,
    zIndexOffset: 1000,
  }).addTo(map).bindPopup(
    `<span class="popup-brand">Search centre</span><br>` +
    `<span class="popup-ip">${escHtml(center.display_name)}</span>`
  );

  cameras.forEach((cam, idx) => {
    if (cam.lat == null || cam.lng == null) return;

    const hasThumb = !!cam.thumbnail_url;
    const m = L.marker([cam.lat, cam.lng], {
      icon: makeIcon(hasThumb),
    }).addTo(map);

    const distLabel = cam.distance_miles != null
      ? `${cam.distance_miles.toFixed(1)} mi`
      : 'unknown dist.';

    m.bindPopup(
      `<span class="popup-brand">${escHtml(cam.brand)}</span><br>` +
      `<span class="popup-ip">${escHtml(cam.ip)}:${escHtml(String(cam.port))}</span><br>` +
      `<span class="popup-dist">${distLabel}</span>`
    );

    m.on('click', () => highlightCard(idx));
    markers.push(m);
  });

  // Fit bounds to circle + a little padding
  const bounds = searchCircle.getBounds().pad(0.1);
  map.fitBounds(bounds);
}

// ---------------------------------------------------------------
// Status helpers
// ---------------------------------------------------------------
function setStatus(state, html) {
  statusDot.className = 'status-dot dot-' + state;
  statusText.innerHTML = html;
}

function setLoading(on) {
  searchBtn.disabled = on;
  if (on) {
    const spinner = '<span class="spinner"></span>';
    statusText.innerHTML = spinner + '&nbsp; Scanning...';
    statusDot.className = 'status-dot dot-idle';
  }
}

// ---------------------------------------------------------------
// Camera grid
// ---------------------------------------------------------------
let _allCameras = [];
let _lastCenter = { lat: 0, lng: 0 };
let _lastRadius = 5;

function getFilteredCameras() {
  if (imgFilter.checked) {
    return _allCameras.filter(c => !!c.screenshot_b64);
  }
  return _allCameras;
}

function applyAndRender() {
  const filtered  = getFilteredCameras();
  const sorted    = sortCameras(filtered, gridSort.value);
  plotCameras(sorted, _lastCenter, _lastRadius);
  renderGrid(sorted);
}

function renderGrid(cameras) {
  const total     = _allCameras.length;
  const showing   = cameras.length;
  gridHeader.style.display = (total > 0) ? 'flex' : 'none';
  gridCount.textContent =
    showing < total ? `(${showing} / ${total})` : `(${total})`;

  if (!cameras.length) {
    cameraGrid.innerHTML =
      `<div class="state-msg">
        <div class="state-title">No cameras found.</div>
        Try increasing the search radius or a different location.
      </div>`;
    return;
  }

  cameraGrid.innerHTML = '';
  cameras.forEach((cam, idx) => buildCard(cam, idx));
}

function buildCard(cam, idx) {
  const card = document.createElement('div');
  card.className = 'camera-card';
  card.dataset.idx = idx;

  // Thumbnail area
  const thumbWrap = document.createElement('div');
  thumbWrap.className = 'thumb-wrap';

  const isRtsp = cam.port === 554 || cam.port === 8554;
  const hasShodanShot = !!cam.screenshot_b64;

  if (hasShodanShot) {
    // Shodan captured this at index time — show it directly, no proxy
    const dataUrl =
      `data:${escHtml(cam.screenshot_mime)};base64,${cam.screenshot_b64}`;
    thumbWrap.innerHTML = `
      <img src="${dataUrl}" alt="camera feed" />
      <span class="stream-badge badge-live">INDEXED</span>`;
  } else if (isRtsp) {
    if (proxyEnabled) {
      thumbWrap.innerHTML = `
        <button class="stream-load-btn" id="slb-${idx}"
          title="Attempt live RTSP stream via HLS">
          <span class="play-icon">▶</span>
          <span>LOAD STREAM</span>
          <span class="card-port-hint">port ${escHtml(String(cam.port))} · RTSP</span>
        </button>
        <span class="stream-badge badge-rtsp">RTSP</span>`;
      setTimeout(() => {
        const btn = document.getElementById(`slb-${idx}`);
        if (btn) btn.addEventListener('click', e => {
          e.stopPropagation();
          requireConsent(() => loadRtspStream(idx, cam));
        });
      }, 0);
    } else {
      thumbWrap.innerHTML = `
        <div class="thumb-placeholder">
          <span class="cam-icon">⬛</span>
          <span class="no-stream-label">Live stream disabled</span>
        </div>
        <span class="stream-badge badge-rtsp">RTSP</span>`;
    }
  } else if (cam.thumbnail_url) {
    if (proxyEnabled) {
      // No Shodan screenshot — show click-to-load placeholder.
      // Live proxy requires consent, so don't auto-fetch.
      const proxyUrl = buildProxyUrl(cam);
      thumbWrap.innerHTML = `
        <button class="stream-load-btn" id="tlb-${idx}"
          title="Load live snapshot via proxy">
          <span class="play-icon">📷</span>
          <span>LOAD SNAPSHOT</span>
          <span class="card-port-hint">port ${escHtml(String(cam.port))}</span>
        </button>
        <img id="ti-${idx}" class="hidden" alt="camera feed" />
        <span class="stream-badge badge-live hidden" id="tb-${idx}">LIVE</span>`;
      setTimeout(() => {
        const btn = document.getElementById(`tlb-${idx}`);
        if (btn) btn.addEventListener('click', e => {
          e.stopPropagation();
          btn.innerHTML = '<span class="spinner"></span>';
          btn.disabled = true;
          requireConsent(() => loadThumbnail(idx, proxyUrl));
        });
      }, 0);
    } else {
      thumbWrap.innerHTML = `
        <div class="thumb-placeholder">
          <span class="cam-icon">⬛</span>
          <span class="no-stream-label">Live view disabled</span>
        </div>
        <span class="stream-badge badge-no-feed">CACHED ONLY</span>`;
    }
  } else {
    thumbWrap.innerHTML = `
      <div class="thumb-placeholder">
        <span class="cam-icon">⬛</span>
        <span class="no-stream-label">No feed available</span>
      </div>
      <span class="stream-badge badge-no-feed">OFFLINE</span>`;
  }

  const distLabel = cam.distance_miles != null
    ? `${cam.distance_miles.toFixed(2)} mi`
    : '—';

  const orgStr = cam.org
    ? cam.org.substring(0, 32) + (cam.org.length > 32 ? '…' : '')
    : 'Unknown org';

  const tagsHtml = (cam.tags || []).slice(0, 4)
    .map(t => `<span class="tag">${escHtml(t)}</span>`).join('');

  const meta = document.createElement('div');
  meta.className = 'card-meta';
  meta.innerHTML = `
    <div class="card-row">
      <span class="card-brand">${escHtml(cam.brand)}</span>
      <span class="card-dist">${distLabel}</span>
    </div>
    <div class="card-ip">${escHtml(cam.ip)}:${escHtml(String(cam.port))}
      &nbsp;<span class="card-transport">${escHtml(cam.transport.toUpperCase())}</span>
    </div>
    <div class="card-org">${escHtml(orgStr)}</div>
    ${tagsHtml ? `<div class="card-tags">${tagsHtml}</div>` : ''}`;

  card.appendChild(thumbWrap);
  card.appendChild(meta);

  card.addEventListener('click', () => {
    highlightCard(idx);
    if (cam.lat != null && cam.lng != null) {
      const m = markers.find(
        mk => Math.abs(mk.getLatLng().lat - cam.lat) < 0.0001
           && Math.abs(mk.getLatLng().lng - cam.lng) < 0.0001
      );
      if (m) {
        map.setView([cam.lat, cam.lng], Math.max(map.getZoom(), 14));
        m.openPopup();
      }
    }
  });

  cameraGrid.appendChild(card);
}

function loadThumbnail(idx, proxyUrl) {
  const loading = document.getElementById(`tl-${idx}`);
  const img     = document.getElementById(`ti-${idx}`);
  const badge   = document.getElementById(`tb-${idx}`);
  if (!img) return;

  img.onload = () => {
    if (loading) loading.remove();
    img.classList.remove('hidden');
    if (badge) badge.classList.remove('hidden');
  };

  img.onerror = () => {
    // Replace the whole thumb with placeholder
    const wrap = img.closest('.thumb-wrap');
    if (!wrap) return;
    wrap.innerHTML = `
      <div class="thumb-placeholder">
        <span class="cam-icon">⬛</span>
        <span class="no-stream-label">Feed unavailable</span>
      </div>
      <span class="stream-badge badge-no-feed">OFFLINE</span>`;
  };

  img.src = proxyUrl;
}

// Active HLS instances keyed by card idx — for cleanup
const _hlsInstances = {};

async function loadRtspStream(idx, cam) {
  const wrap = document.querySelector(
    `.camera-card[data-idx="${idx}"] .thumb-wrap`
  );
  if (!wrap) return;

  // Show spinner while probing / starting ffmpeg
  wrap.innerHTML = `
    <div class="thumb-loading">
      <span class="spinner"></span>
    </div>
    <span class="stream-badge badge-rtsp">CONNECTING</span>`;

  let streamId;
  try {
    const resp = await fetch(`${API_BASE}/api/stream/start`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        ip: cam.ip, port: cam.port, brand: cam.brand
      }),
    });
    if (!resp.ok) {
      const err = (await resp.json().catch(() => ({}))).error
        || `HTTP ${resp.status}`;
      throw new Error(err);
    }
    streamId = (await resp.json()).stream_id;
  } catch (err) {
    wrap.innerHTML = `
      <div class="stream-error">
        ${escHtml(err.message)}
      </div>
      <span class="stream-badge badge-no-feed">FAILED</span>`;
    return;
  }

  const playlistUrl =
    `${API_BASE}/api/stream/${streamId}/playlist.m3u8`;

  wrap.innerHTML = `
    <video class="stream-video" id="sv-${idx}"
      autoplay muted playsinline></video>
    <span class="stream-badge badge-live">LIVE</span>`;

  const video = document.getElementById(`sv-${idx}`);

  // Destroy any previous HLS instance for this card
  if (_hlsInstances[idx]) {
    _hlsInstances[idx].destroy();
    delete _hlsInstances[idx];
  }

  if (Hls.isSupported()) {
    const hls = new Hls({
      lowLatencyMode: true,
      backBufferLength: 4,
    });
    _hlsInstances[idx] = hls;
    hls.loadSource(playlistUrl);
    hls.attachMedia(video);
    hls.on(Hls.Events.ERROR, (_e, data) => {
      if (data.fatal) {
        hls.destroy();
        wrap.innerHTML = `
          <div class="stream-error">Stream lost connection.</div>
          <span class="stream-badge badge-no-feed">LOST</span>`;
        fetch(`${API_BASE}/api/stream/${streamId}`,
          { method: 'DELETE' }).catch(() => {});
      }
    });
  } else if (video.canPlayType('application/vnd.apple.mpegurl')) {
    // Safari native HLS
    video.src = playlistUrl;
  } else {
    wrap.innerHTML = `
      <div class="stream-error">
        Browser does not support HLS playback.
      </div>
      <span class="stream-badge badge-no-feed">UNSUPPORTED</span>`;
  }
}

function buildProxyUrl(cam) {
  if (!cam.thumbnail_url) return null;
  try {
    // Pass brand so the proxy can iterate all known paths for this device
    return `${API_BASE}/api/proxy/image` +
      `?ip=${encodeURIComponent(cam.ip)}` +
      `&port=${cam.port}` +
      `&brand=${encodeURIComponent(cam.brand)}`;
  } catch (_) {
    return null;
  }
}

function highlightCard(idx) {
  document.querySelectorAll('.camera-card').forEach(c => {
    c.classList.remove('highlighted');
  });
  const card = document.querySelector(`.camera-card[data-idx="${idx}"]`);
  if (card) {
    card.classList.add('highlighted');
    card.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }
}

function sortCameras(cameras, by) {
  const copy = [...cameras];
  if (by === 'distance') {
    copy.sort((a, b) => {
      const da = a.distance_miles ?? Infinity;
      const db = b.distance_miles ?? Infinity;
      return da - db;
    });
  } else if (by === 'brand') {
    copy.sort((a, b) => a.brand.localeCompare(b.brand));
  } else if (by === 'port') {
    copy.sort((a, b) => a.port - b.port);
  }
  return copy;
}

gridSort.addEventListener('change', () => {
  if (!_allCameras.length) return;
  applyAndRender();
});

imgFilter.addEventListener('change', () => {
  if (!_allCameras.length) return;
  applyAndRender();
});

// ---------------------------------------------------------------
// Search
// ---------------------------------------------------------------
async function runSearch() {
  const location = searchInput.value.trim();
  if (!location) {
    searchInput.focus();
    return;
  }

  const apiKey = apikeyInput.value.trim();
  if (!apiKey) {
    apikeyInput.focus();
    setStatus('error',
      '<span class="error-msg">Enter your Shodan API key first.</span>');
    return;
  }

  const radiusMiles = parseFloat(radiusSelect.value);
  const maxResults  = parseInt(limitSelect.value, 10);
  setLoading(true);

  try {
    const queries = getQueries();
    const payload = { location, radius_miles: radiusMiles, max_results: maxResults };
    if (queries) payload.queries = queries;

    const resp = await fetch(`${API_BASE}/api/search`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-Shodan-Key': apiKey,
      },
      body: JSON.stringify(payload),
    });

    if (!resp.ok) {
      let body = {};
      try { body = await resp.json(); } catch (_) {}
      const detail = body.error || `HTTP ${resp.status}`;
      if (body.upgrade_required) {
        setStatus('error',
          `<span class="error-msg">Shodan membership required</span>`
        );
        cameraGrid.innerHTML =
          `<div class="state-msg">
            <div class="state-title error-msg">
              Shodan geo filter requires a paid plan.
            </div>
            The radius-constrained search (<code>geo:</code> filter) is
            only available on Shodan Membership or higher.<br><br>
            Upgrade at
            <a href="https://account.shodan.io/billing"
               target="_blank" rel="noopener">shodan.io/billing</a>
            &mdash; plans start at $49/yr.
          </div>`;
        gridHeader.style.display = 'none';
        return;
      }
      throw new Error(detail);
    }

    const data = await resp.json();

    _allCameras = data.cameras;
    _lastCenter = data.center;
    _lastRadius = radiusMiles;

    applyAndRender();

    const n = data.total;
    const locLabel = escHtml(data.center.display_name.split(',')[0]);
    setStatus(
      n > 0 ? 'live' : 'idle',
      n > 0
        ? `<span class="count">${n} camera${n !== 1 ? 's' : ''}</span>` +
          ` found within ${radiusMiles} mi of ` +
          `<span class="location-label">${locLabel}</span>`
        : `No cameras found within ${radiusMiles} mi of ${locLabel}.`
    );

  } catch (err) {
    setStatus('error',
      `<span class="error-msg">Error: ${escHtml(err.message)}</span>`);
    cameraGrid.innerHTML =
      `<div class="state-msg">
        <div class="state-title error-msg">Scan failed.</div>
        ${escHtml(err.message)}
      </div>`;
    gridHeader.style.display = 'none';
  } finally {
    setLoading(false);
  }
}

searchBtn.addEventListener('click', runSearch);
searchInput.addEventListener('keydown', e => {
  if (e.key === 'Enter') runSearch();
});

// ---------------------------------------------------------------
// Utility
// ---------------------------------------------------------------
function escHtml(str) {
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}
