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
}

function paintHeat(hits, fresh) {
  vest.setHits(hits, fresh);
  $('grid-summary').textContent = vest.contacts.length + ' spatial hits / Peak estimated wire coverage: ' + vest.peak + ' hits';
  const legacy = hits.filter(hit => !hit.contact).length;
  const mismatch = hits.filter(hit => hit.contact && !vest.matches(hit.contact)).length;
  $('unmapped-regions').hidden = !legacy && !mismatch;
  $('unmapped-regions').textContent = (legacy ? legacy + ' legacy region-only hits have no wire coordinates. ' : '') + (mismatch ? mismatch + ' hits use a different grid size. ' : '') + 'These remain in totals and history but are not assigned invented positions on this heatmap.';
}

function displayDetail(detail) {
  if (String(detail.session.id) !== selectedId) return;
  const { session, hits } = detail;
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
    paintHeat(hits, newHits);
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
  }
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
$('saved-sessions').addEventListener('change', () => selectSession($('saved-sessions').value));
$('view-active').addEventListener('click', () => { if (active) selectSession(active.id); });
$('check-connection').addEventListener('click', () => restartPolling(true));
document.addEventListener('visibilitychange', () => restartPolling(true));
resetView();
restartPolling(true);
