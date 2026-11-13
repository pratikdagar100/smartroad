/* SmartRoad UI controller */
(() => {
  const $ = id => document.getElementById(id);
  let wasDown = false, switching = false, srcInfo = null, modelInfo = null, snap = null, lastRev = -1, ws, wsDelay = 500, health = null, settings = null, view = 'drive';

  // ---------- helpers ----------
  const esc = s => String(s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const j = async (url, opt) => { const r = await fetch(url, opt); if (!r.ok) throw new Error(r.status); return r.json(); };
  const pretty = t => String(t || '').toUpperCase().replace(/_/g, ' ');

  // ---------- views ----------
  document.querySelectorAll('#tabs button').forEach(b => b.onclick = () => showView(b.dataset.view));
  function showView(v) {
    view = v;
    document.querySelectorAll('#tabs button').forEach(b => b.classList.toggle('active', b.dataset.view === v));
    for (const n of ['drive', 'health', 'settings']) $('view-' + n).hidden = n !== v;
    if (v === 'drive') setTimeout(SRMap.invalidate, 50);
    if (v === 'health') pollHealth();
    if (v === 'settings') loadSettings();
  }

  // ---------- map buttons ----------
  $('btn-zoom-in').onclick = () => SRMap.zoomIn();
  $('btn-zoom-out').onclick = () => SRMap.zoomOut();
  $('btn-center').onclick = () => SRMap.center();
  $('btn-live').onclick = () => SRMap.setFollow(!SRMap.isFollowing());
  $('btn-all').onclick = () => SRMap.showAll();
  $('btn-ncr').onclick = () => SRMap.showNcr();
  $('btn-clear').onclick = () => SRMap.clearRoute();
  window.addEventListener('resize', () => SRMap.invalidate());

  // ---------- live updates ----------
  function connect() {
    ws = new WebSocket((location.protocol === 'https:' ? 'wss://' : 'ws://') + location.host + '/ws/live');
    ws.onopen = () => { wsDelay = 500; setConn(true); if (wasDown && switching) location.reload(); };
    ws.onmessage = e => { try { snap = JSON.parse(e.data); render(snap); } catch (err) { console.error(err); } };
    ws.onclose = () => { wasDown = true; setConn(false); setTimeout(connect, wsDelay); wsDelay = Math.min(5000, wsDelay * 1.6); };
    ws.onerror = () => ws.close();
  }
  function setConn(on) {
    const c = $('b-conn'); c.textContent = on ? 'LIVE' : 'BACKEND DISCONNECTED';
    c.className = 'conn ' + (on ? 'on' : 'off');
  }

  function chip(id, sub) {
    const s = (snap.subsystems || {})[sub];
    const el = $('chip-' + id); if (!el) return;
    el.className = 'chip ' + (s ? s.status.toLowerCase() : '');
    el.title = s ? s.message : '';
  }

  function banner(el, cls, html) {
    if (!html) { el.hidden = true; return; }
    el.hidden = false; el.className = 'banner ' + cls; el.innerHTML = html;
  }

  function render(s) {
    $('speed').textContent = Math.round(s.speed_kmh || 0);
    const ms = $('mode-select');
    if (srcInfo && ms.value !== srcInfo.current && !switching) { ms.value = srcInfo.current; }
    ms.className = srcInfo ? srcInfo.current : '';
    chip('gps', 'gps'); chip('camera', 'camera'); chip('ai', 'ai'); chip('database', 'database');

    // known-hazard warning
    const w = s.proximity && s.proximity.warning;
    banner($('banner-known'), 'known' + (w && w.level === 'CRITICAL' ? ' critical' : ''),
      w ? `<span>&#9888; ${esc(w.text)}<small>${w.level === 'CRITICAL' ? 'SLOW DOWN' : 'Stored hazard #' + w.id + ' &middot; est. severity ' + esc(w.severity)}</small></span><b class="big">${Math.round(w.distance_m)} m</b>` : '');
    SRMap.setWarning(w ? w.id : null);

    // live detection banner
    const a = s.alert;
    banner($('banner-detect'), 'detect',
      a ? `<span>&#9888; ${esc(a.text)}<small>${a.new ? 'New hazard saved #' + a.hazard_id : 'Matched known hazard #' + a.hazard_id} &middot; est. severity ${esc(a.severity)}</small></span><b class="big">${Math.round(a.confidence * 100)}%</b>` : '');

    // system messages over the video
    const msgs = [];
    const sub = s.subsystems || {};
    if (sub.camera && sub.camera.status === 'ERROR') msgs.push(['CAMERA ERROR', '']);
    if (sub.ai && sub.ai.status === 'ERROR') {
      const missing = /NOT INSTALLED/.test(sub.ai.message || '');
      msgs.push([esc(sub.ai.message || 'AI ERROR') + (missing ? ' <button id="btn-install-ai">INSTALL</button>' : ''), 'warn', true]);
    }
    const g = s.gps || {};
    if (!g.fix) msgs.push([g.status === 'UNAVAILABLE' ? 'GPS UNAVAILABLE' : 'GPS SIGNAL LOST', 'warn']);
    $('overlay-msgs').innerHTML = msgs.map(m => `<div class="msg ${m[1]}">${m[2] ? m[0] : esc(m[0])}</div>`).join('');
    const ib = $('btn-install-ai'); if (ib) ib.onclick = () => { showView('settings'); installModel(); };

    // detection text
    $('det-list').innerHTML = (s.detections || []).slice(0, 3).map(d =>
      `${pretty(d.class)} ${Math.round(d.confidence * 100)}% [${d.bbox.join(',')}]`).join('<br>');

    // bottom bar
    $('b-fps').textContent = s.fps.detect;
    $('b-acc').textContent = g.accuracy != null ? g.accuracy : '--';
    $('b-known').textContent = s.known_hazards;
    const r = s.route || {};
    $('b-route').textContent = r.id ? `#${r.id} ${(r.distance_m / 1000).toFixed(2)} km` + (r.repeat ? ' (REPEAT)' : '') : '--';

    SRMap.updateGps(g);
    if (s.hazard_rev !== lastRev) { lastRev = s.hazard_rev; loadHazards(); }
  }

  async function loadHazards() {
    try { SRMap.setHazards(await j('/api/hazards')); } catch (e) { /* keep last */ }
  }

  // ---------- health page ----------
  const card = (title, val, sub, state, pct) =>
    `<div class="card ${state || ''}"><h3>${title}</h3><div class="val">${val}</div><div class="sub">${esc(sub || '')}</div>` +
    (pct != null ? `<div class="bar"><i style="width:${Math.min(100, pct)}%"></i></div>` : '') + '</div>';
  async function pollHealth() {
    if (view !== 'health') return;
    try {
      const h = await j('/api/health'), sy = h.system || {}, sb = h.subsystems || {};
      const st = k => (sb[k] ? sb[k].status.toLowerCase() : 'warn');
      const sm = k => (sb[k] ? sb[k].message : '');
      $('health-grid').innerHTML =
        card('CPU', (sy.cpu_percent ?? '--') + '%', '', '', sy.cpu_percent) +
        card('RAM', (sy.ram_percent ?? '--') + '%', `${sy.ram_used_mb ?? '?'} / ${sy.ram_total_mb ?? '?'} MB`, '', sy.ram_percent) +
        card('TEMPERATURE', sy.temperature_c != null ? sy.temperature_c + ' &deg;C' : 'n/a', 'CPU', sy.temperature_c > 80 ? 'warn' : '') +
        card('DISK', (sy.disk_percent ?? '--') + '%', `${sy.disk_free_gb ?? '?'} GB free`, '', sy.disk_percent) +
        card('CAMERA', sb.camera ? sb.camera.status : '--', sm('camera'), st('camera')) +
        card('GPS', sb.gps ? sb.gps.status : '--', sm('gps'), st('gps')) +
        card('AI MODEL', sb.ai ? sb.ai.status : '--', sm('ai') || h.detector.message, st('ai')) +
        card('DATABASE', h.db.ok ? 'OK' : 'ERROR', h.db.path, h.db.ok ? 'ok' : 'error') +
        card('NETWORK', sy.network ? 'ONLINE' : 'OFFLINE', 'optional - SmartRoad works offline', sy.network ? 'ok' : 'warn') +
        card('IMU', sb.imu ? sb.imu.status : '--', sm('imu'), sb.imu && sb.imu.status === 'OFF' ? '' : st('imu'));
      $('health-events').textContent = 'Session: ' + JSON.stringify(h.counters) + '\nRejected candidates: ' + JSON.stringify(h.rejected_events) +
        '\n\nRecent events:\n' + ((snap && snap.events) || []).map(e => new Date(e.t * 1000).toLocaleTimeString() + '  ' + e.text).join('\n');
    } catch (e) { /* ignore */ }
    setTimeout(pollHealth, 2000);
  }

  // ---------- settings page ----------
  const LABELS = {
    'proximity.warning_distance_m': 'Warning distance (m)', 'proximity.critical_distance_m': 'Critical distance (m)',
    'model.confidence': 'AI confidence (0-1)', 'events.min_confidence': 'Event min. confidence (0-1)',
    'hazards.merge_radius_m': 'Hazard merge radius (m)', 'map.default_zoom': 'Map zoom', 'map.zoom_by_speed': 'Zoom by speed (0/1)',
    'severity.medium_threshold': 'Severity: MEDIUM threshold', 'severity.high_threshold': 'Severity: HIGH threshold',
    'geolocation.distance_estimate_m': 'Pothole distance estimate (m)', 'display.brightness': 'Display brightness (0-100)'
  };
  const getPath = (o, p) => p.split('.').reduce((a, k) => (a == null ? a : a[k]), o);
  async function loadSettings() {
    loadSource(); loadModels();
    try {
      settings = await j('/api/settings');
      const f = $('settings-form');
      f.innerHTML = settings.editable.map(k => {
        let v = getPath(settings.config, k); if (typeof v === 'boolean') v = v ? 1 : 0;
        return `<div class="card"><label for="s-${k}">${LABELS[k] || k}</label><input id="s-${k}" data-k="${k}" type="number" step="any" value="${v ?? ''}"></div>`;
      }).join('') + '<div class="card"><button type="submit">SAVE SETTINGS</button><div class="sub" id="s-msg"></div></div>';
      const c = settings.config;
      $('settings-info').textContent =
        `Camera: ${c.camera.backend} (device ${c.camera.device}, ${c.camera.width}x${c.camera.height})\n` +
        `Model: ${c.model.path}  classes: ${c.model.classes.join(', ')}\nGPS: ${c.gps.mode} ${c.gps.device} @ ${c.gps.baudrate}\n` +
        `Database: ${c.database.path}\nDemo mode: ${c.demo.enabled}\nTile source: ${c.map.offline_tiles ? 'offline /tiles' : c.map.tile_url}\n` +
        'Hardware settings are edited in config.yaml (restart required).';
    } catch (e) { $('settings-info').textContent = 'Could not load settings'; }
  }
  $('settings-form').onsubmit = async ev => {
    ev.preventDefault();
    const body = {};
    document.querySelectorAll('#settings-form input').forEach(i => { if (i.value !== '') body[i.dataset.k] = Number(i.value); });
    try {
      await j('/api/settings', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
      $('s-msg').textContent = 'Saved.';
    } catch (e) { $('s-msg').textContent = 'Rejected (check values, e.g. critical <= warning).'; }
  };

  // ---------- input source ----------
  async function loadSource() {
    try {
      srcInfo = await j('/api/source');
      const ms = $('mode-select');
      ms.innerHTML = Object.entries(srcInfo.options).map(([k, v]) => `<option value="${k}">${v}</option>`).join('');
      ms.value = srcInfo.current;
      renderSettingsTop();
    } catch (e) { /* ignore */ }
  }
  const srcMsg = (m, cls) => { const el = $('s-src-msg'); if (el) { el.textContent = m; el.className = 'sub ' + (cls || ''); } };
  async function applySource(mode, extra) {
    const body = Object.assign({ mode }, extra || {});
    try {
      await j('/api/source', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
      switching = true; $('switching').hidden = false;
      setTimeout(() => location.reload(), 15000);      // fallback if the socket never drops
    } catch (e) {
      srcMsg(mode === 'video' ? 'Enter an existing video/image file path first.' : 'Request rejected.', 'err');
      if (srcInfo) $('mode-select').value = srcInfo.current;
    }
  }
  $('mode-select').onchange = e => {
    const m = e.target.value;
    if (m === 'video') { showView('settings'); setTimeout(() => srcMsg('Enter the video file path, then press APPLY.', 'err'), 300); e.target.value = srcInfo.current; return; }
    applySource(m);
  };

  // ---------- AI model ----------
  async function loadModels() {
    try { modelInfo = await j('/api/models'); renderSettingsTop(); } catch (e) { /* ignore */ }
  }
  async function installModel(id) {
    try {
      id = id || (modelInfo && modelInfo.catalog[0] && modelInfo.catalog[0].id) || 'yolov8n-pothole-seg';
      await j('/api/models/install', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ id }) });
      pollModelJob();
    } catch (e) { const m = $('s-model-msg'); if (m) { m.textContent = 'Install request failed'; m.className = 'sub err'; } }
  }
  async function pollModelJob() {
    await loadModels();
    if (modelInfo && modelInfo.job.status === 'running') setTimeout(pollModelJob, 800);
  }
  async function selectModel(file) {
    try { await j('/api/models/select', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ file }) }); loadModels(); }
    catch (e) { const m = $('s-model-msg'); if (m) { m.textContent = 'Could not load that model'; m.className = 'sub err'; } }
  }
  function applySourceForm() {
    applySource($('src-mode').value, { camera_device: $('src-cam').value, gps_device: $('src-gps').value, video_path: $('src-video').value });
  }
  window.SRUI = { installModel, selectModel, applySourceForm };

  function renderSettingsTop() {
    const box = $('settings-top'); if (!box || view !== 'settings') return;
    if (box.contains(document.activeElement) && document.activeElement.tagName === 'INPUT') return;   // don't clobber typing
    let h = '';
    if (srcInfo) {
      const sv = srcInfo.saved || {};
      h += `<div class="card wide"><h3>INPUT SOURCE</h3>
        <select id="src-mode">${Object.entries(srcInfo.options).map(([k, v]) => `<option value="${k}"${k === srcInfo.current ? ' selected' : ''}>${v}</option>`).join('')}</select>
        <label>Camera device (index like 0, or /dev/video0)</label><input id="src-cam" value="${esc(sv.camera_device ?? srcInfo.camera_device)}">
        <label>GPS device (COM3, /dev/ttyUSB0, /dev/serial0)</label><input id="src-gps" value="${esc(sv.gps_device ?? srcInfo.gps_device)}">
        <label>Video / image file (for VIDEO mode)</label><input id="src-video" value="${esc(sv.video_path ?? srcInfo.video_path)}" placeholder="C:\\videos\\road.mp4 or /home/pi/road.mp4">
        <div class="row"><span class="sub" id="s-src-msg">Current: ${esc(srcInfo.options[srcInfo.current])}. Applying restarts camera and GPS (a few seconds).</span>
        <button class="primary" type="button" onclick="SRUI.applySourceForm()">APPLY</button></div></div>`;
    }
    if (modelInfo) {
      const c = modelInfo.current, job = modelInfo.job;
      const ok = c.status === 'OK' || c.status === 'DEMO';
      h += `<div class="card wide ${ok ? 'ok' : 'error'}"><h3>AI MODEL</h3>
        <div class="val">${c.status === 'DEMO' ? 'SIMULATED DETECTOR' : ok ? esc(c.file) : 'NOT INSTALLED'}</div>
        <div class="sub ${ok ? 'okk' : 'err'}">${c.status === 'DEMO' ? 'Demo mode uses a scripted detector for the synthetic camera. Switch to TEST or LIVE to use ' + esc(c.file) + (c.exists ? '.' : ' (not installed yet).') : ok ? esc(c.status + ' - ' + c.backend + ' - classes: ' + c.classes.join(', ')) : esc(c.message || 'No model loaded')}</div>
        ${job.status === 'running' ? `<div class="bar"><i style="width:${Math.round(job.progress * 100)}%"></i></div><div class="sub">${esc(job.message)} ${Math.round(job.progress * 100)}%</div>` : ''}
        ${job.status === 'error' ? `<div class="sub err">${esc(job.message)}</div>` : ''}
        <div class="sub" id="s-model-msg"></div>
        ${modelInfo.catalog.map(m => `<div class="row"><span class="sub"><b>${esc(m.name)}</b><br>${esc(m.notes || '')}</span>
          <button class="primary" type="button" ${job.status === 'running' ? 'disabled' : ''} onclick="SRUI.installModel('${m.id}')">${m.installed ? 'REINSTALL' : 'INSTALL'}</button></div>`).join('')}
        ${modelInfo.installed.length ? '<label style="margin-top:.6rem">Installed model files</label>' + modelInfo.installed.map(m =>
          `<div class="row"><span class="sub">${esc(m.file)} (${m.size_mb} MB)${m.file === c.file ? ' - in use' : ''}</span>
           <button type="button" ${m.file === c.file ? 'disabled' : ''} onclick="SRUI.selectModel('${esc(m.file)}')">USE</button></div>`).join('') : ''}
      </div>`;
    }
    box.innerHTML = h;
  }

  // ---------- boot ----------
  (async () => {
    let c = { map: {} };
    try { c = (await j('/api/settings')).config; } catch (e) { /* defaults */ }
    SRMap.init({ zoom: c.map.default_zoom || 17, zoomBySpeed: c.map.zoom_by_speed !== false,
                 tileUrl: c.map.tile_url || '', offline: !!c.map.offline_tiles, center: c.map.default_center || [28.6139, 77.2090] });
    try { const r = await j('/api/route'); if (r.points && r.points.length) SRMap.loadRoute(r.points); } catch (e) { /* none yet */ }
    loadHazards();
    loadSource();
    connect();
    const disk = async () => { try { const h = await j('/api/health'); const sy = h.system || {};
      $('b-disk').textContent = sy.disk_free_gb != null ? sy.disk_free_gb + ' GB free' : '--'; } catch (e) { /* ignore */ } };
    disk(); setInterval(disk, 15000);
  })();
})();
