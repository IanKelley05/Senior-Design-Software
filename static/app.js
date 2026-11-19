// One polling loop, one selected-session ID. No active-session data is merged
// into the selected view; late responses are invalidated on every switch.
const $ = (id) => document.getElementById(id);
const POLL_MS = 250;
const LIST_MS = 2000;
const grid = JSON.parse($('vest-config').textContent);
const vest = new RhitVest($('vest'), grid);
let active = null;
let selectedId = '';
let renderedId = '';
let seenEvents = new Set();
let lastSignature = '';
let busy = false;
let ready = false;
let generation = 0;
let timer;
let pollController;
let nextListAt = 0;
let listSignature = '';
let latestDetail = null;
let heatMode = 'coverage';
let fullFields = RhitVest.fields([], grid);
const replay = new RhitReplay();
let replayFrame = null;
let paintedCount = -1;
const localTime = new Intl.DateTimeFormat(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit', fractionalSecondDigits: 3 });
const forceText = value => value === null ? '—' : new Intl.NumberFormat(undefined, { maximumFractionDigits: 2 }).format(value);

async function api(path, method = 'GET', data, signal) {
  const options = { method, cache: 'no-store', signal: signal || AbortSignal.timeout(8000) };
  if (data !== undefined) {
    options.headers = { 'Content-Type': 'application/json' };
    options.body = JSON.stringify(data);
  }
  const response = await fetch(path, options);
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || 'Request failed');
  return result;
}

function controls() {
  $('start-session').disabled = busy || !ready || Boolean(active);
  $('end-session').disabled = busy || !ready || !active;
  $('view-active').disabled = !active || String(active.id) === selectedId;
}

function recordingLabel() {
  $('active-session').textContent = active ? 'Recording: ' + active.name + ' (#' + active.id + ')' : 'No active session';
  controls();
}

function resetView() {
  cancelAnimationFrame(replayFrame);
  replayFrame = null;
  latestDetail = null;
  replay.reset(0);
  fullFields = RhitVest.fields([], grid);
  vest.preview = null;
  paintedCount = -1;
  renderedId = '';
  seenEvents = new Set();
  lastSignature = '';
  vest.clear();
  $('selected-title').textContent = selectedId ? 'Loading selected session…' : 'No session selected';
  $('session-times').textContent = '';
  $('view-mode').textContent = selectedId ? 'Loading' : 'No saved sessions';
  $('hit-count').textContent = selectedId ? '—' : '0';
  $('average-force').textContent = '—';
  $('maximum-force').textContent = '—';
  $('hit-rows').replaceChildren();
  $('empty-hits').hidden = false;
  $('empty-hits').textContent = selectedId ? 'Loading saved hits…' : 'No hits recorded. Start a session to begin.';
  $('history-count').textContent = '0 records';
  paintHeat([], []);
  replayControls();
}

function paintHeat(hits, fresh) {
  const maximum = heatMode === 'force' ? fullFields.peakForce : fullFields.peak;
  vest.setHits(hits, fresh, {mode: heatMode, scaleMaximum: maximum});
  $('mode-coverage').setAttribute('aria-pressed', String(heatMode === 'coverage'));
  $('mode-force').setAttribute('aria-pressed', String(heatMode === 'force'));
  $('heat-description').textContent = heatMode === 'force' ? 'Force: highest simulated estimated glove force recorded at each potential wire crossing (N). Not force per wire or pressure.' : 'Coverage: number of hits covering each potential wire crossing.';
  $('legend-zero').textContent = heatMode === 'force' ? '0 N / no force' : '0 hits';
  $('legend-low').textContent = heatMode === 'force' ? 'Lower force' : 'Lower coverage';
  $('legend-high').textContent = forceText(maximum) + (heatMode === 'force' ? ' N' : ' hits');
  $('grid-summary').textContent = vest.contacts.length + ' spatial hits shown / ' + (heatMode === 'force' ? 'Peak simulated estimated force: ' + forceText(vest.peakForce) + ' N' : 'Peak estimated wire coverage: ' + vest.peak + ' hits');
  const sessionHits = latestDetail?.hits || hits;
  const legacy = sessionHits.filter(hit => !hit.contact).length;
  const mismatch = sessionHits.filter(hit => hit.contact && !vest.matches(hit.contact)).length;
  $('unmapped-regions').hidden = !legacy && !mismatch;
  $('unmapped-regions').textContent = (legacy ? legacy + ' legacy region-only hits have no wire coordinates. ' : '') + (mismatch ? mismatch + ' hits use a different grid size. ' : '') + 'These remain in totals and history but are not assigned invented positions on this heatmap.';
}

function replayAvailable() {
  return Boolean(latestDetail?.session.ended_at && latestDetail.hits.length);
}

function replayControls() {
  const available = replayAvailable();
  $('replay-play').disabled = !available;
  $('replay-play').textContent = replay.playing ? 'Pause' : (replay.elapsed >= replay.duration ? 'Play again' : 'Play');
  $('replay-back').disabled = !available || (replay.active && replay.count === 0);
  $('replay-next').disabled = !available || (replay.active && replay.count === replay.total);
  $('replay-restart').disabled = !available;
  $('replay-overview').disabled = !available || !replay.active;
  $('replay-position').disabled = !available;
  $('replay-position').max = replay.total;
  $('replay-position').value = replay.active ? replay.count : replay.total;
  $('replay-hint').textContent = !latestDetail?.session.ended_at ? 'End the session to replay its hits over 30 seconds.' : !replay.total ? 'This session has no hits to replay.' : '30 seconds total. Hits are evenly spaced, not played at their original timing. Stepping pauses playback.';
  $('replay-progress').textContent = replay.active ? 'Hit ' + replay.count + ' / ' + replay.total + ' | ' + (replay.elapsed / 1000).toFixed(1) + ' / 30.0 s | ' + (replay.playing ? 'Playing' : 'Paused') : 'Full session view';
  $('replay-progress').dataset.position = replay.active ? replay.count : replay.total;
  $('replay-progress').dataset.playing = replay.playing;
  $('replay-progress').dataset.elapsed = replay.elapsed;
  const current = replay.active && replay.count ? latestDetail?.hits[replay.count - 1] : null;
  $('replay-current').textContent = current ? localTime.format(new Date(current.timestamp)) + ' | ' + forceText(current.force_n) + ' N simulated estimated force' + (!vest.matches(current.contact) ? ' | No compatible grid coordinates for this hit.' : '') : 'Session totals above always describe the complete saved session.';
}

function renderScene(fresh = []) {
  const hits = latestDetail?.hits || [];
  const shown = replay.active ? hits.slice(0, replay.count) : hits;
  const current = replay.active && replay.count ? hits[replay.count - 1] : null;
  vest.preview = current && vest.matches(current.contact) ? current.contact : null;
  paintHeat(shown, replay.active ? [] : fresh);
  paintedCount = replay.count;
  const revealed = new Set(shown.map(hit => hit.event_id));
  for (const row of $('hit-rows').children) {
    row.classList.toggle('replay-current', Boolean(current && row.dataset.eventId === current.event_id));
    row.classList.toggle('replay-future', replay.active && !revealed.has(row.dataset.eventId));
  }
  replayControls();
}

function animateReplay(now) {
  replayFrame = null;
  replay.tick(now);
  if (paintedCount !== replay.count) renderScene();
  else replayControls();
  if (replay.playing) replayFrame = requestAnimationFrame(animateReplay);
}

function pauseReplay() {
  replay.pause(performance.now());
  cancelAnimationFrame(replayFrame);
  replayFrame = null;
  renderScene();
}

function seekReplay(count) {
  cancelAnimationFrame(replayFrame);
  replayFrame = null;
  replay.seek(count);
  vest.pulses = [];
  renderScene();
}

function displayDetail(detail) {
  if (String(detail.session.id) !== selectedId) return;
  const { session, hits } = detail;
  latestDetail = detail;
  const sameSession = renderedId === selectedId;
  const newHits = sameSession ? hits.filter(hit => !seenEvents.has(hit.event_id)) : [];
  renderedId = selectedId;
  seenEvents = new Set(hits.map(hit => hit.event_id));
  $('selected-title').textContent = session.name + ' (#' + session.id + ')';
  $('session-times').textContent = 'Started ' + localTime.format(new Date(session.started_at)) + (session.ended_at ? ' · Ended ' + localTime.format(new Date(session.ended_at)) : ' · Recording in progress');
  $('view-mode').textContent = session.ended_at ? 'SAVED SESSION' : 'LIVE SESSION';
  $('hit-count').textContent = session.hit_count;
  $('average-force').textContent = forceText(session.average_force_n);
  $('maximum-force').textContent = forceText(session.max_force_n);
  // Active state is separate; never use the selected session as the write target.
  if (!session.ended_at) active = session;
  else if (active && active.id === session.id) active = null;
  recordingLabel();
  const signature = selectedId + ':' + hits.map(hit => hit.event_id).join(',');
  if (signature !== lastSignature) {
    lastSignature = signature;
    fullFields = RhitVest.fields(hits, grid);
    cancelAnimationFrame(replayFrame);
    replayFrame = null;
    replay.reset(hits.length);
    $('hit-rows').replaceChildren();
    for (const hit of [...hits].reverse()) {
      const row = document.createElement('tr');
      row.dataset.eventId = hit.event_id;
      for (const value of [localTime.format(new Date(hit.timestamp)), hit.contact ? 'R ' + hit.contact.rows.join(',') + ' / C ' + hit.contact.columns.join(',') : 'Legacy: ' + hit.region, forceText(hit.force_n)]) {
        const cell = document.createElement('td');
        cell.textContent = value;
        row.append(cell);
      }
      row.firstChild.title = hit.timestamp;
      $('hit-rows').append(row);
    }
    $('empty-hits').hidden = hits.length > 0;
    $('empty-hits').textContent = 'No hits recorded in this session.';
    $('history-count').textContent = hits.length + (hits.length === 1 ? ' record' : ' records');
    renderScene(newHits);
  }
  replayControls();
}

function updateSessions(sessions) {
  active = sessions.find(session => !session.ended_at) || null;
  const signature = JSON.stringify(sessions.map(s => [s.id, s.name, s.ended_at]));
  if (signature !== listSignature) {
    listSignature = signature;
    $('saved-sessions').replaceChildren();
    if (!sessions.length) $('saved-sessions').append(new Option('No saved sessions', ''));
    for (const session of sessions) $('saved-sessions').append(new Option('#' + session.id + ' · ' + session.name + (session.ended_at ? '' : ' (active)'), session.id));
  }
  if (!selectedId && sessions.length) {
    selectedId = String(active?.id || sessions[0].id);
    resetView();
  }
  $('saved-sessions').value = selectedId;
  recordingLabel();
}

async function poll(version) {
  const started = performance.now();
  const controller = new AbortController();
  pollController = controller;
  const timeout = setTimeout(() => controller.abort(), 8000);
  let delay = POLL_MS;
  try {
    if (Date.now() >= nextListAt) {
      // Receiver metrics do not access PostgreSQL and remain useful in an outage.
      const udp = await api('/api/udp', 'GET', undefined, controller.signal);
      if (version !== generation) return;
      $('udp-state').textContent = udp.state === 'not_started' ? 'Not started · launch with python app.py' : udp.state + ' · ' + udp.host + ':' + udp.port;
      for (const key of ['received', 'accepted', 'duplicate', 'invalid', 'inactive_session', 'database_failures']) $('udp-' + key).textContent = udp.counters[key];
      $('udp-note').textContent = 'UDP counters since receiver start; excludes browser-generated hits and undelivered packets. In flight: ' + udp.in_flight + '. Unexpected processing failures: ' + udp.counters.processing_failures + '.';
      const sessions = await api('/api/sessions', 'GET', undefined, controller.signal);
      if (version !== generation) return;
      updateSessions(sessions);
      nextListAt = Date.now() + LIST_MS;
    }
    const requestedId = selectedId;
    if (requestedId) {
      const detail = await api('/api/sessions/' + requestedId + '/hits', 'GET', undefined, controller.signal);
      if (version !== generation || requestedId !== selectedId) return;
      displayDetail(detail);
    }
    if (version !== generation) return;
    ready = true;
    $('database-message').textContent = 'PostgreSQL connected · Displaying saved records';
    document.querySelector('.connection').classList.remove('error');
    $('poll-status').textContent = 'Live refresh · 250 ms';
    if ($('action-message').textContent === 'Loading saved sessions…') $('action-message').textContent = 'Ready. Select a session to review its saved hits.';
  } catch (error) {
    if (version !== generation) return;
    ready = false;
    nextListAt = 0;
    delay = 2000;
    $('database-message').textContent = error.name === 'AbortError' ? 'Connection timed out. Retrying…' : error.message;
    document.querySelector('.connection').classList.add('error');
    $('poll-status').textContent = 'Connection lost · displayed data may be stale';
  } finally {
    clearTimeout(timeout);
    if (version === generation) {
      controls();
      pollController = null;
      if (!document.hidden) timer = setTimeout(() => poll(version), Math.max(0, delay - (performance.now() - started)));
    }
  }
}

function restartPolling(refreshList = false) {
  clearTimeout(timer);
  generation += 1;
  pollController?.abort();
  if (refreshList) nextListAt = 0;
  if (!document.hidden) poll(generation);
}

function selectSession(id) {
  selectedId = String(id);
  $('saved-sessions').value = selectedId;
  resetView();
  controls();
  restartPolling();
}

async function action(work) {
  if (busy) return;
  busy = true;
  controls();
  try { await work(); }
  catch (error) { $('action-message').textContent = error.message; }
  finally { busy = false; controls(); restartPolling(true); }
}

$('start-form').addEventListener('submit', event => {
  event.preventDefault();
  action(async () => {
    const session = await api('/api/sessions', 'POST', { name: $('session-name').value });
    active = session;
    selectSession(session.id);
    $('action-message').textContent = 'Session started. Viewing the new session.';
  });
});
$('end-session').addEventListener('click', () => action(async () => {
  if (!active) return;
  await api('/api/sessions/' + active.id + '/end', 'POST');
  active = null;
  $('action-message').textContent = 'Recording ended. All hits remain saved.';
}));
for (const mode of ['coverage', 'force']) $("mode-" + mode).addEventListener('click', () => {
  // Toggling is purely visual: do not reset the replay position or clock.
  heatMode = mode;
  renderScene();
});
$('replay-play').addEventListener('click', () => {
  if (!replayAvailable()) return;
  if (replay.playing) { pauseReplay(); return; }
  vest.pulses = [];
  replay.play(performance.now());
  renderScene();
  replayFrame = requestAnimationFrame(animateReplay);
});
$('replay-back').addEventListener('click', () => {
  if (replayAvailable()) seekReplay((replay.active ? replay.count : replay.total) - 1);
});
$('replay-next').addEventListener('click', () => {
  if (replayAvailable()) seekReplay((replay.active ? replay.count : 0) + 1);
});
$('replay-restart').addEventListener('click', () => { if (replayAvailable()) seekReplay(0); });
$('replay-position').addEventListener('input', () => { if (replayAvailable()) seekReplay(Number($('replay-position').value)); });
$('replay-overview').addEventListener('click', () => {
  cancelAnimationFrame(replayFrame);
  replayFrame = null;
  replay.overview();
  vest.pulses = [];
  renderScene();
});
$('saved-sessions').addEventListener('change', () => selectSession($('saved-sessions').value));
$('view-active').addEventListener('click', () => { if (active) selectSession(active.id); });
$('check-connection').addEventListener('click', () => restartPolling(true));
document.addEventListener('visibilitychange', () => {
  if (document.hidden && replay.playing) pauseReplay();
  restartPolling(true);
});
resetView();
restartPolling(true);
