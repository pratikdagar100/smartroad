/* SmartRoad map layer (Leaflet).  Works offline: hazards, vehicle and route are
   drawn locally; background tiles are optional and fail gracefully. */
const SRMap = (() => {
  const NCR_BOUNDS = [[27.75, 76.75], [29.15, 78.05]];     // Delhi NCR overview
  let map, vehicle, vehEl, routeLine, tiles, follow = true, routePts = [], lastGps = null;
  const markers = new Map();          // id -> {marker, data}
  let hazards = [], warnId = null, lastZoomAdj = 0, tileErr = 0, tileOk = 0, sessionHasFix = false;
  let cfg = { zoom: 17, zoomBySpeed: true, tileUrl: '', offline: false, center: [28.6139, 77.2090] };

  const vehIcon = () => L.divIcon({
    className: '', iconSize: [38, 38], iconAnchor: [19, 19],
    html: '<div class="veh"><svg viewBox="0 0 40 40"><circle cx="20" cy="20" r="17" fill="#3aa0ff33" stroke="#3aa0ff" stroke-width="2"/>' +
          '<path d="M20 6 L30 31 L20 25 L10 31 Z" fill="#3aa0ff" stroke="#fff" stroke-width="2" stroke-linejoin="round"/></svg></div>'
  });

  function hazardClass(h) {
    if (h.status === 'FIXED') return 'fixed';
    if (h.severity === 'HIGH') return 'severe';
    if (h.is_new) return 'new';
    return 'known';
  }
  function hazardIcon(h) {
    const c = hazardClass(h), s = c === 'severe' ? 22 : 18;
    return L.divIcon({ className: '', iconSize: [s, s], iconAnchor: [s / 2, s / 2],
                       html: `<div class="hz ${c}${h.id === warnId ? ' warn' : ''}"></div>` });
  }
  function popupHtml(h) {
    const when = (h.last_detected || '').replace('T', ' ').slice(0, 16);
    return `<b>${h.hazard_type.toUpperCase().replace('_', ' ')} #${h.id}</b> &middot; ${h.status}<br>` +
      `Estimated severity: <b>${h.severity}</b><br>Confidence ${Math.round(h.confidence * 100)}% &middot; seen ${h.detection_count}&times;` +
      `${h.verified ? ' &middot; IMU verified' : ''}<br><small>Last: ${when} UTC &middot; ${h.source}</small>` +
      (h.image_url ? `<img src="${h.image_url}" alt="hazard">` : '') +
      `<br><button onclick="SRMap.setStatus(${h.id},'FIXED')">Mark fixed</button>` +
      `<button onclick="SRMap.setStatus(${h.id},'ACTIVE')">Active</button>`;
  }

  function init(c) {
    cfg = Object.assign(cfg, c);
    map = L.map('map', { zoomControl: false, attributionControl: true, preferCanvas: false, maxZoom: 19, minZoom: 6 })
      .setView(cfg.center, 11);
    // Offline pack (if present) is the base layer; online tiles draw over it when reachable.
    if (cfg.offline) L.tileLayer('/tiles/{z}/{x}/{y}.png', { maxZoom: 19, maxNativeZoom: cfg.offlineMaxZoom || 11 }).addTo(map);
    if (cfg.tileUrl) {
      tiles = L.tileLayer(cfg.tileUrl, { maxZoom: 19, attribution: '&copy; OpenStreetMap contributors', crossOrigin: false });
      tiles.on('tileerror', () => { tileErr++; checkTiles(); });
      tiles.on('tileload', () => { tileOk++; checkTiles(); });
      tiles.addTo(map);
    } else checkTiles(true);
    routeLine = L.polyline([], { color: '#3aa0ff', weight: 4, opacity: .85 }).addTo(map);
    vehicle = L.marker(cfg.center, { icon: vehIcon(), zIndexOffset: 1000, interactive: false });
    map.on('dragstart', () => setFollow(false));
    // fall back to the NCR overview until the first GPS fix arrives
    map.fitBounds(NCR_BOUNDS);
  }
  function checkTiles(force) {
    const note = document.getElementById('map-note');
    note.hidden = !(force || (tileErr > 4 && tileOk === 0));
  }

  function setFollow(on) {
    follow = on;
    document.getElementById('btn-live').classList.toggle('on', on);
    if (on && lastGps) { map.setView([lastGps.latitude, lastGps.longitude], map.getZoom(), { animate: true }); }
  }

  function targetZoom(kmh) {
    if (!cfg.zoomBySpeed) return cfg.zoom;
    if (kmh < 15) return Math.min(19, cfg.zoom + 1);
    if (kmh < 40) return cfg.zoom;
    if (kmh < 70) return cfg.zoom - 1;
    return cfg.zoom - 2;
  }

  function updateGps(g) {
    if (!g || !g.fix || g.latitude == null) return;
    const ll = [g.latitude, g.longitude];
    if (!sessionHasFix) {            // first fix: leave the NCR overview, jump to the vehicle
      sessionHasFix = true; vehicle.addTo(map); vehEl = vehicle.getElement();
      map.setView(ll, cfg.zoom);
    }
    lastGps = g;
    vehicle.setLatLng(ll);
    vehEl = vehEl || vehicle.getElement();
    const inner = vehEl && vehEl.querySelector('.veh');
    if (inner && g.heading != null) inner.style.transform = `rotate(${g.heading}deg)`;
    const last = routePts[routePts.length - 1];
    if (!last || map.distance(last, ll) > 3) { routePts.push(ll); routeLine.setLatLngs(routePts); }
    if (follow) {
      const now = Date.now();
      if (now - lastZoomAdj > 4000) {
        lastZoomAdj = now;
        const z = targetZoom(g.speed_kmh || 0);
        if (z !== map.getZoom()) map.setView(ll, z, { animate: true }); else map.panTo(ll, { animate: true, duration: .25 });
      } else map.panTo(ll, { animate: true, duration: .25, noMoveStart: true });
    }
  }

  function setHazards(list) {
    hazards = list;
    const seen = new Set();
    for (const h of list) {
      seen.add(h.id);
      const cur = markers.get(h.id);
      if (cur) {
        cur.marker.setLatLng([h.latitude, h.longitude]).setIcon(hazardIcon(h));
        cur.marker.setPopupContent(popupHtml(h)); cur.data = h;
      } else {
        const m = L.marker([h.latitude, h.longitude], { icon: hazardIcon(h), riseOnHover: true })
          .bindPopup(popupHtml(h)).addTo(map);
        markers.set(h.id, { marker: m, data: h });
      }
    }
    for (const [id, v] of markers) if (!seen.has(id)) { map.removeLayer(v.marker); markers.delete(id); }
  }

  function setWarning(id) {
    if (id === warnId) return;
    const prev = warnId; warnId = id;
    for (const k of [prev, id]) { const v = markers.get(k); if (v) v.marker.setIcon(hazardIcon(v.data)); }
  }

  async function setStatus(id, status) {
    try {
      await fetch(`/api/hazards/${id}/status`, { method: 'POST', headers: { 'Content-Type': 'application/json' },
                                                body: JSON.stringify({ status }) });
    } catch (e) { /* offline UI keeps working */ }
  }

  function showAll() {
    setFollow(false);
    const pts = hazards.map(h => [h.latitude, h.longitude]);
    if (lastGps) pts.push([lastGps.latitude, lastGps.longitude]);
    if (pts.length > 1) map.fitBounds(pts, { padding: [40, 40], maxZoom: 18 });
    else if (pts.length === 1) map.setView(pts[0], 17);
  }
  function showNcr() { setFollow(false); map.fitBounds(NCR_BOUNDS); }
  function clearRoute() { routePts = []; routeLine.setLatLngs([]); }
  function loadRoute(points) { routePts = points.slice(); routeLine.setLatLngs(routePts); }

  return { init, updateGps, setHazards, setWarning, setFollow, setStatus, showAll, showNcr, clearRoute, loadRoute,
           zoomIn: () => map.zoomIn(), zoomOut: () => map.zoomOut(), center: () => setFollow(true),
           invalidate: () => map && map.invalidateSize(), isFollowing: () => follow };
})();
