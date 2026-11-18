// One polling loop, one selected-session ID. No active-session data is merged
// into the selected view; late responses are invalidated on every switch.
const $ = (id) => document.getElementById(id);
const POLL_MS = 250;
const LIST_MS = 2000;
const regionNodes = new Map([...document.querySelectorAll('.vest-region')].map(node => [node.dataset.region, node]));
const countNodes = new Map([...document.querySelectorAll('[data-count-region]')].map(node => [node.dataset.countRegion, node]));
const listCountNodes = new Map([...document.querySelectorAll('[data-list-count]')].map(node => [node.dataset.listCount, node]));
let active = null;
let selectedId = '';
let renderedId = '';
let seenEvents = new Set();
let lastSignature = '';
let lastDelivery = null;
let busy = false;
let ready = false;
let generation = 0;
let timer;
let pollController;
let nextListAt = 0;
let listSignature = '';
const flashTimers = new Map();
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
  $('send-hit').disabled = busy || !ready || !active;
  $('replay-hit').disabled = busy || !ready || !active || !lastDelivery || lastDelivery.sessionId !== active.id;
  $('view-active').disabled = !active || String(active.id) === selectedId;
}

function recordingLabel() {
  $('active-session').textContent = active ? 'Recording: ' + active.name + ' (#' + active.id + ')' : 'No active session';
  $('simulator-target').textContent = active ? 'Simulator target: ' + active.name + ' (#' + active.id + '). Viewing another session does not change this target.' : 'Start a session to accept simulated hits.';
  controls();
}

function resetView() {
  renderedId = '';
  seenEvents = new Set();
  lastSignature = '';
  for (const [id, node] of regionNodes) {
    clearTimeout(flashTimers.get(id));
    node.classList.remove('hit-flash');
  }
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
  paintRegions([], new Set());
}

function heatColor(count, maximum) {
  if (!count) return '#293441';
  const t = maximum <= 1 ? 1 : (count - 1) / (maximum - 1);
  return 'rgb(' + [99, 72, 32].map((base, i) => Math.round(base + ([243, 188, 87][i] - base) * t)).join(',') + ')';
}

function paintRegions(regions, newRegions) {
  const counts = new Map(regions.map(item => [item.region, item.hit_count]));
  const maximum = Math.max(1, ...counts.values());
  $('legend-max').textContent = maximum + (maximum === 1 ? ' hit' : ' hits');
  document.querySelector('.legend-gradient').style.background = maximum === 1 ? '#f3bc57' : '';
  for (const [id, node] of regionNodes) {
    const count = counts.get(id) || 0;
    node.querySelector('rect').setAttribute('fill', heatColor(count, maximum));
    node.querySelector('title').textContent = id + ': ' + count + ' hits';
    countNodes.get(id).textContent = count;
    listCountNodes.get(id).textContent = count;
    if (newRegions.has(id)) {
      clearTimeout(flashTimers.get(id));
      node.classList.remove('hit-flash');
      void node.getBoundingClientRect(); // Restart a brief pulse for another new hit.
      node.classList.add('hit-flash');
      flashTimers.set(id, setTimeout(() => node.classList.remove('hit-flash'), 800));
    }
  }
  const unmapped = regions.filter(item => !regionNodes.has(item.region));
  $('unmapped-regions').hidden = !unmapped.length;
  $('unmapped-regions').textContent = 'Historical regions outside the current layout: ' + unmapped.map(item => item.region + ' (' + item.hit_count + ' hits)').join(', ') + '. Included in totals and history.';
}

function displayDetail(detail) {
  if (String(detail.session.id) !== selectedId) return;
  const { session, hits } = detail;
  const sameSession = renderedId === selectedId;
  const newRegions = new Set(sameSession ? hits.filter(hit => !seenEvents.has(hit.event_id)).map(hit => hit.region) : []);
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
  paintRegions(detail.regions, newRegions);
  const signature = selectedId + ':' + hits.map(hit => hit.event_id).join(',');
  if (signature !== lastSignature) {
    lastSignature = signature;
    $('hit-rows').replaceChildren();
    for (const hit of [...hits].reverse()) {
      const row = document.createElement('tr');
      row.dataset.eventId = hit.event_id;
      for (const value of [localTime.format(new Date(hit.timestamp)), hit.region, forceText(hit.force_n)]) {
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
async function deliver() {
  if (!lastDelivery) return;
  const response = await api('/api/sessions/' + lastDelivery.sessionId + '/hits', 'POST', lastDelivery.hit);
  $('action-message').textContent = response.inserted ? 'Simulated hit saved to session #' + lastDelivery.sessionId + '.' : 'Duplicate recognized. Saved hit count is unchanged.';
}
$('hit-form').addEventListener('submit', event => {
  event.preventDefault();
  action(async () => {
    if (!active) return;
    const force = Number($('hit-force').value);
    if (!Number.isFinite(force) || force < 0) throw new Error('Force must be finite and nonnegative.');
    lastDelivery = { sessionId: active.id, hit: { event_id: crypto.randomUUID(), timestamp: new Date().toISOString(), region: $('hit-region').value, force_n: force } };
    $('last-event').textContent = 'Last simulated event: ' + lastDelivery.hit.event_id;
    await deliver();
  });
});
$('replay-hit').addEventListener('click', () => action(deliver));
$('saved-sessions').addEventListener('change', () => selectSession($('saved-sessions').value));
$('view-active').addEventListener('click', () => { if (active) selectSession(active.id); });
$('check-connection').addEventListener('click', () => restartPolling(true));
document.addEventListener('visibilitychange', () => restartPolling(true));
resetView();
restartPolling(true);
