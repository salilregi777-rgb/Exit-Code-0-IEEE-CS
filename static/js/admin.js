/* EXIT CODE 0 — organizer controls. Server state is always authoritative. */
(() => {
  'use strict';
  const workspace = document.getElementById('admin-workspace');
  if (!workspace) return;
  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
  const panels = {
    overview: ['Overview', 'Competition overview', 'Your event, at a glance. Every signal in one place.'],
    controls: ['Event control', 'Run the competition', 'Start, pause, resume and close the debugging round.'],
    teams: ['Teams', 'The starting grid', 'Registered teams, participation status and debugging progress.'],
    questions: ['Questions', 'The challenge library', 'Review the question bank and manage availability.'],
    submissions: ['Submissions', 'Every fix, accounted for', 'Inspect answers, review scoring and record judging decisions.'],
    rankings: ['Leaderboard', 'The official standings', 'Review debugging scores and publish the final results.'],
    quiz: ['Quiz', 'Keep the room thinking', 'A separate rapid-fire round while judges verify the results.'],
    feedback: ['Feedback', 'Hear from participants', 'Six quiz ratings and optional notes, saved per team.'],
    security: ['Security', 'A clearer view of activity', 'Browser activity signals for informed organizer review.'],
    settings: ['Settings', 'Event settings', 'Competition configuration, exports and data management.']
  };
  let state = { event_status: workspace.dataset.eventStatus };
  let dashboard = null;
  let requestInFlight = false;
  let actionInFlight = false;
  let activitySignature = '';
  let leaderboardSignature = '';
  let currentPanel = 'overview';
  let countdownAnchor = performance.now();
  let countdownSeconds = 0;
  let initialStats = null;
  let pollHandle;
  const formatNumber = value => Number(value || 0).toLocaleString(undefined, { maximumFractionDigits: 1 });
  const request = (url, options = {}) => window.App.request(url, options);
  const post = (url, body) => request(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  const toast = (message, type = 'success') => window.App.toast(message, type);
  const parseDate = value => {
    if (!value) return null;
    const normalized = value.replace(' ', 'T');
    const date = new Date(/(?:Z|[+-]\d\d:\d\d)$/.test(normalized) ? normalized : `${normalized}Z`);
    return Number.isNaN(date.valueOf()) ? null : date;
  };
  const timeText = value => parseDate(value)?.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit', second: '2-digit' }) || '—';
  const relativeTime = value => {
    const date = parseDate(value);
    if (!date) return 'No activity yet';
    const seconds = Math.max(0, Math.floor((Date.now() - date.valueOf()) / 1000));
    if (seconds < 60) return `${seconds}s ago`;
    if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
    return date.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
  };
  function element(tag, className, text) {
    const item = document.createElement(tag);
    if (className) item.className = className;
    if (text !== undefined) item.textContent = String(text);
    return item;
  }
  function setText(selector, value) { const item = $(selector); if (item) item.textContent = value; }
  function setConnected(connected) {
    $('.admin-sync').classList.toggle('is-offline', !connected);
    setText('#admin-sync-label', connected ? 'Synced with server' : 'Reconnecting…');
    window.App.setConnection(connected);
  }
  function showPanel() {
    const name = location.hash.slice(1);
    currentPanel = panels[name] ? name : 'overview';
    const [label, title, description] = panels[currentPanel];
    $$('.admin-panel').forEach(panel => { panel.hidden = panel.id !== currentPanel; });
    $$('.admin-nav a').forEach(link => {
      const active = link.dataset.panel === currentPanel;
      link.classList.toggle('active', active);
      if (active) link.setAttribute('aria-current', 'page'); else link.removeAttribute('aria-current');
    });
    setText('#admin-current-panel', label);
    $('#admin-page-title').replaceChildren(document.createTextNode(title), element('span', 'admin-title-dot', '.'));
    setText('#admin-page-description', description);
    if (currentPanel === 'rankings') refreshLeaderboard();
    if (currentPanel === 'feedback') refreshFeedback();
  }
  function setBusy(button, busy, label) {
    if (!button) return;
    if (busy) {
      button.dataset.originalLabel = button.textContent;
      button.textContent = label || 'Saving…';
      button.disabled = true;
      button.classList.add('loading');
      button.setAttribute('aria-busy', 'true');
    } else {
      button.textContent = button.dataset.originalLabel || button.textContent;
      button.disabled = false;
      button.classList.remove('loading');
      button.removeAttribute('aria-busy');
    }
  }
  function updateTimer() {
    const elapsed = state.event_status === 'LIVE' ? Math.floor((performance.now() - countdownAnchor) / 1000) : 0;
    const seconds = Math.max(0, countdownSeconds - elapsed);
    setText('#admin-telemetry-timer', `${String(Math.floor(seconds / 60)).padStart(2, '0')}:${String(seconds % 60).padStart(2, '0')}`);
  }
  function updateState(next) {
    if (!next?.event_status) return;
    state = next;
    workspace.dataset.eventStatus = state.event_status;
    $$('[data-event-status-text]').forEach(item => { item.textContent = state.event_status; });
    if (Number.isFinite(Number(next.remaining_seconds))) {
      countdownSeconds = Number(next.remaining_seconds);
      countdownAnchor = performance.now();
      updateTimer();
    }
    const allowed = { start: ['WAITING'], pause: ['LIVE'], resume: ['PAUSED'], end: ['LIVE', 'PAUSED'] };
    $$('[data-event-action]').forEach(button => { button.disabled = actionInFlight || !allowed[button.dataset.eventAction].includes(state.event_status); });
    const notes = {
      WAITING: 'Teams can register without fullscreen restrictions. Starting closes registration and opens the arena for eligible teams.',
      LIVE: 'The debugging round is live. Teams can submit until the server timer expires.',
      PAUSED: 'The clock is paused. Existing progress is preserved; submissions are temporarily locked.',
      COMPLETED: 'Debugging is complete. Submissions are locked and results are ready for verification.'
    };
    setText('#admin-control-note', notes[state.event_status] || 'Reading event state…');
    setText('#admin-state-description', ({ WAITING: 'Ready for the starting signal', LIVE: 'Debugging round in progress', PAUSED: 'Clock and submissions paused', COMPLETED: 'Debugging submissions locked' })[state.event_status] || 'Server-authoritative state');
    updateRoundControls();
  }
  function updateRoundControls() {
    if (!dashboard) return;
    const quiz = dashboard.quiz || {};
    const completed = state.event_status === 'COMPLETED';
    setText('#admin-quiz-status', quiz.status || 'WAITING');
    setText('#admin-quiz-participants', formatNumber(quiz.participants));
    setText('#admin-quiz-completed', formatNumber(quiz.completed));
    $$('[data-quiz-action]').forEach(button => {
      const open = button.dataset.quizAction === 'open';
      button.disabled = actionInFlight || !completed || (open ? quiz.status !== 'WAITING' : quiz.status !== 'OPEN');
    });
    setText('#admin-quiz-note', quiz.status === 'OPEN'
      ? `The quiz is open. ${quiz.question_count || 10} questions · ${quiz.duration_minutes || 7} minutes per team. Debugging scores stay unchanged.`
      : quiz.status === 'CLOSED' ? 'The quiz is closed. Completed quiz scores remain separate from the debugging rankings.'
      : completed ? 'Debugging has ended. You can now open the engagement quiz.' : 'The quiz becomes available after debugging ends.');
    const publish = $('#admin-publish-results');
    publish.disabled = actionInFlight || !completed || dashboard.results_published;
    if (!publish.hasAttribute('aria-busy')) publish.textContent = dashboard.results_published ? 'Results published ✓' : 'Publish final results';
    setText('#admin-publish-description', dashboard.results_published
      ? 'Final debugging results are visible to every team. Quiz scores are excluded from these rankings.'
      : completed ? 'Review scoring adjustments, then publish the final debugging rankings to all teams.'
      : 'End the debugging round, verify scores, then publish the final rankings.');
  }
  function updateStats(stats) {
    if (!initialStats) initialStats = { registered_teams: stats.registered_teams, total_submissions: stats.total_submissions };
    $$('[data-stat]').forEach(item => {
      const key = item.dataset.stat;
      if (stats[key] === undefined) return;
      const value = key === 'average_score' ? Number(stats[key]).toFixed(1) : formatNumber(stats[key]);
      if (item.textContent !== value) {
        item.textContent = value;
        item.classList.remove('admin-stat-updated');
        void item.offsetWidth;
        item.classList.add('admin-stat-updated');
      }
    });
    const changed = stats.registered_teams !== initialStats.registered_teams || stats.total_submissions !== initialStats.total_submissions;
    const notice = $('#admin-records-notice');
    if (notice) notice.hidden = !changed;
  }
  function updateActivity(events) {
    const signature = JSON.stringify(events);
    if (signature === activitySignature) return;
    activitySignature = signature;
    const feed = $('#admin-activity-feed');
    if (!events.length) {
      const empty = element('div', 'admin-empty');
      empty.append(element('span', 'admin-empty-symbol', '↳'), element('h3', '', 'Ready for the first signal.'), element('p', '', 'Submissions and participant activity will appear here during the competition.'));
      feed.replaceChildren(empty);
      return;
    }
    const labels = { session_violation: 'received a fullscreen/focus violation', submission: 'submitted', fullscreen_exit: 'exited fullscreen', fullscreen_enter: 'entered fullscreen', tab_hidden: 'switched away from the arena', window_blur: 'moved focus away', window_focus: 'returned to the arena', window_visible: 'returned to the arena', tab_visible: 'returned to the arena' };
    const rows = events.slice(0, 20).map(event => {
      const row = element('div', 'admin-activity-item');
      row.dataset.eventType = event.event_type;
      const description = element('p');
      description.append(element('strong', '', event.team_name || 'Team'), document.createTextNode(` ${labels[event.event_type] || event.event_type.replaceAll('_', ' ')}${event.question_id ? ` ${event.question_id}` : ''}`));
      const time = element('time', '', timeText(event.created_at));
      time.title = parseDate(event.created_at)?.toLocaleString() || '';
      row.append(element('span', 'admin-activity-dot'), description, time);
      return row;
    });
    feed.replaceChildren(...rows);
  }
  function teamAction(team) {
    const button = element('button', `btn ${team.blocked ? 'btn-primary' : 'btn-secondary'} admin-small-btn`, team.blocked ? 'Unban team' : team.is_active ? 'Disable' : 'Enable');
    button.type = 'button';
    button.dataset.teamName = team.team_name;
    if (team.blocked) button.dataset.teamUnban = team.team_id;
    else {
      button.dataset.teamToggle = team.team_id;
      button.dataset.active = String(Number(!!team.is_active));
      button.disabled = !!dashboard?.results_published;
      if (button.disabled) button.title = 'Results are final. Team eligibility is locked.';
    }
    return button;
  }
  function updateTeamAccess(teams) {
    const byId = new Map(teams.map(team => [team.team_id, team]));
    $$('.team-row[data-team-id]').forEach(row => {
      const team = byId.get(row.dataset.teamId);
      if (!team) return;
      const signature = `${team.is_active}:${team.blocked}:${!!dashboard?.results_published}`;
      if (row.dataset.accessSignature === signature) return;
      row.dataset.accessSignature = signature;
      $('[data-team-status]', row).replaceChildren(element('span', `badge ${team.blocked ? 'admin-badge-warning' : team.is_active ? 'admin-badge-success' : 'admin-badge-muted'}`, team.blocked ? 'Blocked · 2 / 2' : team.is_active ? 'Active' : 'Disabled'));
      $('[data-team-action]', row).replaceChildren(teamAction(team));
    });
  }
  function updateSecurity(teams) {
    const target = $('#admin-security-body');
    if (!teams.length) {
      const row = element('tr'); row.append(element('td', 'admin-table-empty', 'Activity signals will appear here after teams register.')); row.firstChild.colSpan = 7;
      target.replaceChildren(row); return;
    }
    const rows = teams.map(team => {
      const row = element('tr');
      const identity = element('td'); identity.append(element('strong', '', team.team_name), element('small', '', team.team_id));
      const focus = Number(team.focus_losses || 0), tabs = Number(team.tab_switches || 0), fullscreen = Number(team.fullscreen_exits || 0);
      const activity = element('td', '', relativeTime(team.last_seen));
      activity.title = parseDate(team.last_seen)?.toLocaleString() || 'No activity reported';
      const status = element('td');
      const hasSignals = focus + tabs + fullscreen > 0;
      status.append(element('span', `badge ${team.blocked || team.violations ? 'admin-badge-warning' : 'admin-badge-muted'}`, team.blocked ? 'Blocked · 2 / 2' : team.violations ? `Warning · ${team.violations} / 2` : hasSignals ? 'Review signals' : 'No flags'));
      const action = element('td');
      if (team.blocked) action.append(teamAction(team));
      else action.textContent = '—';
      row.append(identity, element('td', 'mono', focus), element('td', 'mono', tabs), element('td', 'mono', fullscreen), activity, status, action);
      return row;
    });
    target.replaceChildren(...rows);
  }
  async function refreshFeedback() {
    try {
      const data = await request('/api/admin/feedback');
      const rows = data.responses || [];
      setText('#admin-feedback-count', `${rows.length} ${rows.length === 1 ? 'response' : 'responses'}`);
      if (!rows.length) return;
      $('#admin-feedback-body').replaceChildren(...rows.map(response => {
        const row = element('tr');
        row.append(element('td', '', response.team_name));
        ['clarity', 'difficulty', 'interface', 'pacing', 'enjoyment', 'overall'].forEach(key => row.append(element('td', 'mono', `${response.ratings[key]} / 5`)));
        const note = element('td', '', response.note || '—'); note.style.whiteSpace = 'pre-wrap'; note.style.minWidth = '220px'; row.append(note);
        return row;
      }));
    } catch (error) { setConnected(false); }
  }
  async function refreshLeaderboard() {
    try {
      const data = await request('/api/leaderboard-data');
      const teams = data.leaderboard || [];
      const signature = JSON.stringify(teams);
      if (signature === leaderboardSignature) return;
      leaderboardSignature = signature;
      const target = $('#admin-leaderboard-body');
      if (!teams.length) {
        const row = element('tr'); row.append(element('td', 'admin-table-empty', 'No eligible teams have registered yet.')); row.firstChild.colSpan = 4; target.replaceChildren(row); return;
      }
      target.replaceChildren(...teams.map((team, index) => {
        const row = element('tr');
        const name = element('td'); name.append(element('strong', '', team.name), element('small', '', team.id));
        row.append(element('td', 'mono', `#${team.rank || index + 1}`), name, element('td', 'mono', team.completed_count), element('td', 'admin-score', formatNumber(team.debug_score ?? team.score)));
        return row;
      }));
    } catch (error) { setConnected(false); }
  }
  async function refreshDashboard() {
    if (requestInFlight || document.hidden) return;
    requestInFlight = true;
    try {
      dashboard = await request('/api/admin/dashboard-data');
      updateStats(dashboard.stats || {});
      updateState(dashboard.event_state);
      updateActivity(dashboard.activity || []);
      updateSecurity(dashboard.security || []);
      updateTeamAccess(dashboard.security || []);
      updateRoundControls();
      setConnected(true);
      if (currentPanel === 'rankings') await refreshLeaderboard();
      if (currentPanel === 'feedback') await refreshFeedback();
    } catch (error) {
      setConnected(false);
      if (!dashboard) {
        setText('#admin-control-note', 'Unable to sync. Controls will be enabled after a successful server connection.');
        $$('[data-event-action]').forEach(button => { button.disabled = true; });
      }
    } finally { requestInFlight = false; }
  }
  async function performAction(button, url, body, loadingLabel, shouldReload = false) {
    if (actionInFlight) return;
    actionInFlight = true;
    setBusy(button, true, loadingLabel);
    updateState(state);
    try {
      const result = await post(url, body);
      if (result.success === false) throw new Error(result.error || 'The action could not be completed.');
      toast(result.message || 'Changes saved.');
      if (shouldReload) { location.reload(); return; }
      await refreshDashboard();
    } catch (error) { toast(error.message || 'Unable to save. Please try again.', 'error'); }
    finally { actionInFlight = false; setBusy(button, false); updateState(state); }
  }
  function bindFilters(inputId, rowSelector, emptyId, countId) {
    $(inputId)?.addEventListener('input', event => {
      const query = event.target.value.trim().toLowerCase();
      const rows = $$(rowSelector);
      let count = 0;
      rows.forEach(row => { row.hidden = !row.dataset.search.includes(query); if (!row.hidden) count++; });
      $(emptyId).hidden = count !== 0 || !rows.length;
      if (countId) setText(countId, `${count} ${count === 1 ? 'team' : 'teams'}`);
    });
  }
  function openDetail(templateId) {
    const template = document.getElementById(templateId);
    if (!template) return;
    $('#admin-detail-content').replaceChildren(template.content.cloneNode(true));
    $('#admin-detail-dialog').showModal();
  }
  $$('[data-event-action]').forEach(button => {
    button.addEventListener('click', async () => {
      const action = button.dataset.eventAction;
      const confirmations = {
        start: ['Start the debugging round?', `New team registration will close. The ${state.duration_minutes || 40}-minute timer and fullscreen rules will begin for registered teams.`, 'Start event'],
        pause: ['Pause the competition?', 'The clock will stop and submissions will be temporarily locked. Remaining time is preserved.', 'Pause event'],
        resume: ['Resume the competition?', 'The timer will continue from its remaining time and teams can submit again.', 'Resume event'],
        end: ['End the debugging round?', 'This closes all debugging submissions immediately. The round cannot be resumed after ending.', 'End event']
      };
      const [title, message, confirmText] = confirmations[action];
      if (!await window.App.confirm(message, { title, confirmText, danger: action === 'end' })) return;
      await performAction(button, '/api/admin/event-action', { action }, ({ start: 'Starting…', pause: 'Pausing…', resume: 'Resuming…', end: 'Ending…' })[action]);
    });
  });
  workspace.addEventListener('click', async event => {
    const unban = event.target.closest('[data-team-unban]');
    if (unban) {
      if (actionInFlight) return;
      const publishedNote = dashboard?.results_published ? ' This also restores their eligibility in the published rankings.' : '';
      if (!await window.App.confirm(`Restore ${unban.dataset.teamName}? Their violations will reset to 0/2, and their saved answers and scores will be kept. They must enter fullscreen again to continue.${publishedNote}`, { title: 'Unban team', confirmText: 'Unban team' })) return;
      await performAction(unban, '/api/admin/unban-team', { team_id: unban.dataset.teamUnban }, 'Restoring…');
      return;
    }
    const button = event.target.closest('[data-team-toggle]');
    if (!button || actionInFlight || button.disabled) return;
    const disable = button.dataset.active === '1';
    const verb = disable ? 'Disable' : 'Enable';
    if (!await window.App.confirm(`${verb} ${button.dataset.teamName}? ${disable ? 'Their competition access will be blocked and they will be excluded from the public rankings. Existing submissions are retained.' : 'Their competition access and public ranking eligibility will be restored.'}`, { title: `${verb} team`, confirmText: `${verb} team`, danger: disable })) return;
    await performAction(button, '/api/admin/toggle-team', { team_id: button.dataset.teamToggle }, 'Updating…');
  });
  $$('[data-question-toggle]').forEach(button => button.addEventListener('click', async () => {
    const disable = button.dataset.active === '1';
    if (!await window.App.confirm(`${disable ? 'Disable' : 'Enable'} question ${button.dataset.questionToggle}? ${disable ? 'Disabled questions cannot receive new submissions. Review current assignments before proceeding.' : 'This question will become available in the active bank.'}`, { title: 'Update question availability', confirmText: disable ? 'Disable question' : 'Enable question', danger: disable })) return;
    await performAction(button, '/api/admin/toggle-question', { question_id: button.dataset.questionToggle }, 'Updating…', true);
  }));
  $$('[data-question-view]').forEach(button => button.addEventListener('click', () => openDetail(`question-reference-${button.dataset.questionView}`)));
  $$('[data-submission-view]').forEach(button => button.addEventListener('click', () => openDetail(`submission-review-${button.dataset.submissionView}`)));
  $('#admin-detail-content').addEventListener('submit', async event => {
    const form = event.target.closest('.admin-override-form');
    if (!form) return;
    event.preventDefault();
    const score = Number(form.elements.new_score.value);
    const maximum = Number(form.elements.new_score.max);
    const reason = form.elements.reason.value.trim();
    const error = $('.admin-form-error', form);
    if (!Number.isFinite(score) || score < 0 || score > maximum || !reason) {
      error.textContent = `Enter a score between 0 and ${maximum} and a reason for the change.`; return;
    }
    error.textContent = '';
    await performAction($('button[type="submit"]', form), '/api/admin/override-score', { submission_id: Number(form.dataset.submissionId), new_score: score, reason }, 'Saving score…', true);
  });
  $$('[data-quiz-action]').forEach(button => button.addEventListener('click', async () => {
    const open = button.dataset.quizAction === 'open';
    if (!await window.App.confirm(open ? 'Teams can start the engagement quiz. Quiz points will stay separate from debugging scores.' : 'Teams will no longer be able to submit quiz answers. This does not affect debugging scores.', { title: open ? 'Open rapid fire?' : 'Close the quiz?', confirmText: open ? 'Open quiz' : 'Close quiz', danger: !open })) return;
    await performAction(button, '/api/admin/quiz-action', { action: button.dataset.quizAction }, open ? 'Opening…' : 'Closing…');
  }));
  $('#admin-publish-results').addEventListener('click', async event => {
    const button = event.currentTarget;
    if (!await window.App.confirm('The final debugging standings will be visible to every participant. Confirm that judging and score adjustments are complete. Quiz scores will not be included.', { title: 'Publish final results?', confirmText: 'Publish results' })) return;
    await performAction(button, '/api/admin/publish-results', {}, 'Publishing…');
  });
  $('#admin-reset-open').addEventListener('click', () => {
    $('#admin-reset-form').reset(); $('#admin-reset-confirm').disabled = true;
    setText('#admin-reset-form .admin-form-error', ''); $('#admin-reset-dialog').showModal();
    $('#reset-confirmation-input').focus();
  });
  $('#reset-confirmation-input').addEventListener('input', event => { $('#admin-reset-confirm').disabled = event.target.value !== 'RESET EVENT'; });
  $('#admin-reset-form').addEventListener('submit', async event => {
    event.preventDefault();
    const confirmation = $('#reset-confirmation-input').value;
    if (confirmation !== 'RESET EVENT') { setText('#admin-reset-form .admin-form-error', 'Type RESET EVENT exactly to proceed.'); return; }
    await performAction($('#admin-reset-confirm'), '/api/admin/reset-event', { confirmation }, 'Resetting…', true);
  });
  $$('[data-close-dialog]').forEach(button => button.addEventListener('click', () => button.closest('dialog').close()));
  $$('.admin-dialog').forEach(dialog => dialog.addEventListener('click', event => { if (event.target === dialog) { const bounds = dialog.getBoundingClientRect(); if (event.clientX < bounds.left || event.clientX > bounds.right || event.clientY < bounds.top || event.clientY > bounds.bottom) dialog.close(); } }));
  $('#admin-refresh').addEventListener('click', event => { event.currentTarget.setAttribute('aria-busy', 'true'); location.reload(); });
  $('#admin-records-refresh')?.addEventListener('click', () => location.reload());
  bindFilters('#team-search-input', '.team-row', '#team-search-empty', '#team-filter-count');
  bindFilters('#question-search-input', '.question-row', '#question-search-empty');
  $$('[data-local-time]').forEach(item => { const date = parseDate(item.dataset.localTime); if (date) { item.textContent = date.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }); item.dateTime = date.toISOString(); } });
  window.addEventListener('hashchange', showPanel);
  window.addEventListener('eventstate', event => updateState(event.detail));
  document.addEventListener('visibilitychange', () => { if (!document.hidden) refreshDashboard(); });
  document.body.classList.add('admin-ready');
  showPanel();
  $$('[data-event-action]').forEach(button => { button.disabled = true; });
  refreshDashboard();
  pollHandle = setInterval(refreshDashboard, 5000);
  const timerHandle = setInterval(updateTimer, 1000);
  window.addEventListener('pagehide', () => { clearInterval(pollHandle); clearInterval(timerHandle); });
})();
