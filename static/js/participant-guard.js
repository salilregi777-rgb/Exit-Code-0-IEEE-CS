/* The browser reports departures; persistent account limits are enforced by the server. */
(() => {
  'use strict';
  const gate = document.getElementById('participant-gate');
  if (!gate) return;
  const button = document.getElementById('participant-enter');
  const title = document.getElementById('participant-gate-title');
  const message = document.getElementById('participant-gate-message');
  const count = document.getElementById('participant-violations');
  const error = document.getElementById('participant-gate-error');
  const pendingKey = `ex0-fullscreen-pending:${gate.dataset.generation}:${gate.dataset.team}`;
  const id = () => crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(36).slice(2)}`;
  const documentID = id();
  const documentStartedAt = performance.timeOrigin || Date.now();
  const fullscreen = () => !!document.fullscreenElement;
  const focused = () => !document.hidden && document.hasFocus();
  let active = false, armed = false, blocked = false, navigating = false, busy = false, activationID = null, poll;
  let required = gate.dataset.required !== 'false';
  const entryMessage = message.textContent;
  let resolveReady;
  const ready = new Promise(resolve => { resolveReady = resolve; });
  let pending = [];
  try {
    const saved = JSON.parse(sessionStorage.getItem(pendingKey) || '[]');
    if (Array.isArray(saved)) pending = saved.filter(item => typeof item === 'string' || item?.event_id).map(item => typeof item === 'string' ? {event_type: 'fullscreen_exit', event_id: item} : item);
  } catch (_) {}
  const persist = () => { try { sessionStorage.setItem(pendingKey, JSON.stringify(pending)); } catch (_) {} };
  function syncButton() {
    const toggle = document.getElementById('fullscreen-toggle');
    if (!toggle) return;
    toggle.disabled = active;
    toggle.setAttribute('aria-label', active ? 'Fullscreen active' : 'Enter fullscreen');
    const label = toggle.querySelector('[data-fullscreen-label]');
    if (label) label.textContent = active ? 'Fullscreen active' : 'Enter fullscreen';
  }
  function show() {
    active = false;
    if (!required || navigating) return;
    if (!gate.open) gate.showModal();
    syncButton();
  }
  function setRequired(value) {
    const changed = required !== value;
    required = value;
    if (!required) {
      active = false; armed = false; blocked = false; activationID = null;
      pending = []; persist();
      if (gate.open) gate.close();
      syncButton(); resolveReady();
    } else if (changed) show();
  }
  function apply(state) {
    if (typeof state.required === 'boolean') setRequired(state.required);
    count.textContent = `${state.violations || 0} / ${state.limit || 2} violations`;
    if (!required) return;
    if (state.blocked) {
      blocked = true; armed = false; show(); button.hidden = true;
      title.textContent = 'Account blocked';
      message.textContent = 'Two fullscreen or focus violations were recorded. Your answers are saved, but this account cannot continue. Contact an organizer.';
      error.textContent = '';
    } else {
      if (blocked) {
        blocked = false; armed = false; activationID = null;
        pending = []; persist(); button.hidden = false; button.disabled = false;
        error.textContent = ''; show();
      }
      title.textContent = state.violations ? 'Competition warning' : 'Enter fullscreen';
      message.textContent = state.violations ? 'One departure has been recorded. Another app switch, tab switch, or fullscreen exit will block this account. Keep this window focused and return to fullscreen.' : entryMessage;
    }
  }
  const report = body => App.request('/api/activity', {method: 'POST', body, keepalive: true});
  const signal = (event_type, event_id = id(), activation_id = activationID) => report({event_type, event_id, activation_id, document_id: documentID, document_started_at: documentStartedAt});
  function release() {
    if (!activationID) return Promise.resolve();
    return signal('fullscreen_leave', `release-${activationID}`);
  }
  let flushTask;
  function flush() {
    if (flushTask) return flushTask;
    flushTask = (async () => {
      while (pending.length) {
        const state = await report(pending[0]);
        pending.shift(); persist(); apply(state);
        if (blocked) break;
      }
    })().finally(() => { flushTask = null; });
    return flushTask;
  }
  async function enter() {
    if (!required || busy || blocked) return;
    busy = true; button.disabled = true; error.textContent = '';
    try {
      if (!document.documentElement.requestFullscreen || document.fullscreenEnabled === false) throw new Error('This browser cannot enter fullscreen. Please use a supported desktop browser to participate.');
      // Keep requestFullscreen directly inside the click's user activation.
      if (!fullscreen()) await document.documentElement.requestFullscreen();
      await flush();
      if (blocked) return;
      if (!fullscreen() || !focused()) { show(); error.textContent = 'Keep this window focused and click to continue.'; return; }
      const enteringID = id();
      activationID = enteringID;
      const state = await signal('fullscreen_enter', enteringID, null);
      if (navigating) { await release(); return; }
      apply(state);
      if (!required) return;
      if (!blocked && state.activation_id === enteringID && state.is_fullscreen && fullscreen() && focused() && !pending.length) {
        armed = true; active = true; gate.close(); syncButton(); resolveReady();
        document.dispatchEvent(new CustomEvent('participant:fullscreen-ready'));
      } else if (!blocked) {
        // A switch during entry cannot leave server access open in the background.
        await release();
        show(); error.textContent = 'Entry was interrupted. Keep this window focused and try again.';
      }
    } catch (err) {
      if (activationID && !pending.length && !blocked) {
        const cleanup = {event_type: 'fullscreen_leave', event_id: `release-${activationID}`, activation_id: activationID, document_id: documentID, document_started_at: documentStartedAt};
        pending.push(cleanup); persist(); flush().catch(() => {});
      }
      if (navigating) return;
      if (err.data?.blocked) apply(err.data);
      show(); error.textContent = err.name === 'NotAllowedError' ? 'Fullscreen was not allowed. Click the button to try again.' : err.message;
    } finally { busy = false; button.disabled = blocked; }
  }
  function departure(event_type) {
    if (!required || !armed || navigating || blocked) return;
    // One physical switch often emits blur + hidden + fullscreenexit. Latch once.
    armed = false;
    pending.push({event_type, event_id: id(), activation_id: activationID, document_id: documentID, document_started_at: documentStartedAt});
    persist(); show();
    title.textContent = 'Competition window left';
    message.textContent = 'Participation is locked while this departure is recorded. Stay in fullscreen and keep this window focused.';
    flush().catch(err => {
      if (err.data?.blocked) apply(err.data);
      else error.textContent = 'Unable to record this departure yet. Reconnect to continue; the warning is saved on this device.';
    });
  }
  function beginNavigation() { navigating = true; armed = false; active = false; }
  function navigate(url, {replace = false} = {}) {
    if (App.navigation?.canNavigate(new URL(url, location.href))) return App.navigation.navigate(url, {replace});
    beginNavigation();
    replace ? location.replace(url) : location.assign(url);
    return true;
  }
  gate.addEventListener('cancel', e => e.preventDefault());
  gate.addEventListener('close', () => { if (required && !active && !navigating) show(); });
  button.addEventListener('click', enter);
  document.addEventListener('click', event => { if (event.target.closest?.('#fullscreen-toggle')) enter(); });
  document.addEventListener('participant:page-ready', syncButton);
  document.addEventListener('fullscreenchange', () => { if (!fullscreen()) departure('fullscreen_exit'); });
  window.addEventListener('blur', () => departure('window_blur'));
  document.addEventListener('visibilitychange', () => { if (document.hidden) departure('tab_hidden'); else check(); });
  // Internal redirects and page teardown release access without counting a departure.
  window.addEventListener('beforeunload', beginNavigation);
  window.addEventListener('pagehide', () => {
    beginNavigation(); clearInterval(poll);
    if (activationID && !blocked && !pending.length) release().catch(() => {});
  });
  window.addEventListener('pageshow', event => {
    if (event.persisted) { navigating = false; armed = false; show(); poll = setInterval(check, 6000); }
  });
  document.addEventListener('click', event => {
    const link = event.target.closest?.('a[href]');
    if (!link || event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey || link.hasAttribute('download') || (link.target && link.target !== '_self')) return;
    const url = new URL(link.href, location.href);
    if (url.origin !== location.origin || (url.pathname === location.pathname && url.search === location.search)) return;
    // Run after local click handlers so cancelled navigation cannot suppress enforcement.
    if (!event.defaultPrevented) beginNavigation();
  });
  document.addEventListener('participant:blocked', event => apply(event.detail));
  document.addEventListener('participant:fullscreen-required', () => { setRequired(true); armed = false; show(); });
  document.addEventListener('eventstate', event => {
    setRequired(event.detail.event_status !== 'WAITING');
  });
  window.addEventListener('online', () => { flush().catch(() => {}); });
  async function check() {
    if (navigating || document.hidden) return;
    if (active && (!focused() || !fullscreen())) { departure(fullscreen() ? 'window_blur' : 'fullscreen_exit'); return; }
    const checkedActivation = active && !busy ? activationID : null;
    try {
      const state = await App.request('/api/fullscreen/status');
      apply(state);
      if (required && !active && !busy) show();
      if (checkedActivation && !busy && active && checkedActivation === activationID && (!state.is_fullscreen || state.activation_id !== activationID)) { armed = false; show(); }
    } catch (err) { if (err.data?.blocked) apply(err.data); else if (err.status === 401) navigate('/register'); }
  }
  window.addEventListener('focus', check);
  poll = setInterval(check, 6000);
  window.ParticipantGuard = {ready, enter, navigate, leaveForNavigation: beginNavigation, get active() {return active;}, get required() {return required;}};
  if (gate.open) gate.close();
  setRequired(required);
  show();
  flush().then(check).catch(err => { if (err.data?.blocked) apply(err.data); else error.textContent = err.message; });
})();
