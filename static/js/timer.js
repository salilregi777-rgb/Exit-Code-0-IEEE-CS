/* One shared poller. Local interpolation is display-only; Flask enforces expiry. */
(() => {
  'use strict';
  let state = null, receivedAt = 0, pending = false, pollTimeout;
  const warnings = new Set();
  const format = seconds => `${String(Math.floor(seconds / 60)).padStart(2, '0')}:${String(seconds % 60).padStart(2, '0')}`;
  function render() {
    if (!state) return;
    const elapsed = state.event_status === 'LIVE' ? Math.floor((performance.now() - receivedAt) / 1000) : 0;
    const seconds = Math.max(0, (Number(state.remaining_seconds) || 0) - elapsed);
    document.querySelectorAll('[data-event-timer], #event-timer-display').forEach(el => {
      el.textContent = format(seconds);
      el.classList.toggle('timer-warning', state.event_status === 'LIVE' && seconds <= 600 && seconds > 300);
      el.classList.toggle('timer-urgent', state.event_status === 'LIVE' && seconds <= 300 && seconds > 60);
      el.classList.toggle('timer-critical', state.event_status === 'LIVE' && seconds <= 60);
      el.setAttribute('aria-label', `${Math.floor(seconds / 60)} minutes ${seconds % 60} seconds remaining${state.event_status === 'PAUSED' ? ', paused' : ''}`);
    });
    if (state.event_status === 'LIVE' && document.body.classList.contains('arena-page')) {
      const threshold = seconds <= 60 ? 60 : seconds <= 300 ? 300 : seconds <= 600 ? 600 : 0;
      if (threshold && !warnings.has(threshold)) { warnings.add(threshold); App.toast(`${threshold / 60} ${threshold === 60 ? 'minute' : 'minutes'} remaining.`, 'warning'); }
    }
  }
  function apply(data) {
    state = data; receivedAt = performance.now(); App.eventState = data;
    document.querySelectorAll('[data-event-status=""], #global-event-status').forEach(el => { el.textContent = data.event_status; el.dataset.status = data.event_status; });
    document.querySelectorAll('[data-team-count], #connected-teams-count').forEach(el => { if (data.connected_teams !== undefined) el.textContent = data.connected_teams; });
    render();
    document.dispatchEvent(new CustomEvent('eventstate', { detail: data, bubbles: true }));
  }
  async function sync() {
    if (pending) return;
    const organizerPage = location.pathname === '/admin' || location.pathname.startsWith('/admin/');
    // The sign-in form needs no authenticated polling. Organizer dashboard
    // requests use its own session even if a participant tab is blocked.
    if (organizerPage && !document.getElementById('admin-workspace')) return;
    pending = true; clearTimeout(pollTimeout);
    try { apply(await App.request(organizerPage ? '/api/admin/event-status' : '/api/event-status')); } catch (_) { App.setConnection(false); }
    finally { pending = false; pollTimeout = setTimeout(sync, document.hidden ? 12000 : 5000); }
  }
  window.EventClock = { sync, apply, format, get state() { return state; } };
  // Backward compatible display adapter, without adding extra requests or timers.
  window.EventTimer = class {
    constructor(id, onExpire) { this.el = document.getElementById(id); this.listener = e => { if (this.el) this.el.textContent = e.detail.formatted_time; if (e.detail.event_status === 'COMPLETED' && onExpire) { onExpire(); onExpire = null; } }; document.addEventListener('eventstate', this.listener); }
    destroy() { document.removeEventListener('eventstate', this.listener); }
    syncWithServer() { return sync(); }
  };
  const init = () => { try { apply(JSON.parse(document.getElementById('initial-event-state').textContent)); } catch (_) { /* Wait for authoritative poll. */ } sync(); setInterval(render, 1000); };
  document.addEventListener('visibilitychange', () => { if (!document.hidden) sync(); });
  window.addEventListener('online', sync);
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init); else init();
})();
