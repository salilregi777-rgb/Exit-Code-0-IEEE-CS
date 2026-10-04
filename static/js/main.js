/* Shared UI primitives. Server responses are the only source of official state. */
(() => {
  'use strict';
  let online = true;
  function toast(message, type = 'info') {
    const container = document.getElementById('toast-container');
    if (!container) return;
    const item = document.createElement('div');
    item.className = `toast ${['success', 'warning', 'error'].includes(type) ? type : 'info'}`;
    const text = document.createElement('span');
    text.textContent = message;
    const close = document.createElement('button');
    close.type = 'button'; close.className = 'toast-dismiss'; close.textContent = '×'; close.setAttribute('aria-label', 'Dismiss notification');
    let timeout;
    const dismiss = () => { clearTimeout(timeout); item.classList.add('leaving'); setTimeout(() => item.remove(), 200); };
    close.addEventListener('click', dismiss); item.append(text, close); container.append(item);
    while (container.children.length > 4) container.firstElementChild.remove();
    timeout = setTimeout(dismiss, type === 'error' ? 8000 : 4500);
  }
  function setConnection(connected) {
    document.querySelectorAll('[data-connection]').forEach(el => {
      el.classList.toggle('offline', !connected);
      const label = el.querySelector('[data-connection-label]');
      if (label) label.textContent = connected ? 'SERVER ONLINE' : 'RECONNECTING…';
    });
    if (connected && !online) toast('Connection restored. Your progress is safe.', 'success');
    online = connected;
  }
  async function request(url, options = {}) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 12000);
    const headers = { Accept: 'application/json', ...options.headers };
    let body = options.body;
    if (body && !(body instanceof FormData)) {
      headers['Content-Type'] = 'application/json';
      if (typeof body !== 'string') body = JSON.stringify(body);
    }
    const csrf = document.querySelector('meta[name=csrf-token]')?.content;
    if (csrf) headers['X-CSRF-Token'] = csrf;
    try {
      const res = await fetch(url, { credentials: 'same-origin', cache: 'no-store', ...options, body, headers, signal: options.signal || controller.signal });
      const isJSON = (res.headers.get('content-type') || '').includes('application/json');
      if (res.redirected || !isJSON) {
        if (res.status >= 500) throw new Error('The server could not complete this request. Please try again.');
        const error = new Error('Your session has ended. Sign in again to continue.'); error.status = 401; throw error;
      }
      const data = await res.json();
      setConnection(true);
      if (!res.ok || data.success === false) {
        const error = new Error(res.status >= 500 ? 'The server could not complete this request. Please try again.' : data.error || 'This request could not be completed.');
        error.status = res.status; error.data = data;
        if (data.blocked) document.dispatchEvent(new CustomEvent('participant:blocked', {detail: data}));
        if (data.fullscreen_required) document.dispatchEvent(new CustomEvent('participant:fullscreen-required'));
        throw error;
      }
      return data;
    } catch (err) {
      if (err.name === 'AbortError' || err instanceof TypeError) {
        setConnection(false);
        const offline = new Error('Unable to connect. Your draft is safe; please retry.'); offline.offline = true; throw offline;
      }
      throw err;
    } finally { clearTimeout(timeout); }
  }
  function confirmAction(message, options = {}) {
    return new Promise(resolve => {
      const dialog = document.getElementById('confirm-dialog');
      if (dialog.open) { resolve(false); return; }
      document.getElementById('confirm-title').textContent = options.title || 'Confirm action';
      document.getElementById('confirm-message').textContent = message;
      const accept = document.getElementById('confirm-accept');
      accept.textContent = options.confirmText || 'Confirm';
      accept.className = `btn ${options.danger ? 'btn-danger' : 'btn-primary'}`;
      dialog.returnValue = '';
      dialog.addEventListener('close', () => resolve(dialog.returnValue === 'confirm'), { once: true });
      dialog.showModal();
    });
  }
  const storage = {
    get(key, fallback = null) { try { return JSON.parse(localStorage.getItem(key)) ?? fallback; } catch (_) { return fallback; } },
    set(key, value) { try { localStorage.setItem(key, JSON.stringify(value)); return true; } catch (_) { return false; } },
    remove(key) { try { localStorage.removeItem(key); } catch (_) { /* Storage can be unavailable in private mode. */ } }
  };
  function createPageScope() {
    let active = true;
    const cleanups = [], timeouts = new Set(), intervals = new Set();
    // Stale responses must never write into another page with similar element IDs.
    const stopped = () => new Promise(() => {});
    return {
      get active() { return active; },
      listen(target, type, callback, options) {
        const listener = (...args) => { if (active) callback(...args); };
        target.addEventListener(type, listener, options);
        cleanups.push(() => target.removeEventListener(type, listener, options));
      },
      setTimeout(callback, delay) {
        const timer = window.setTimeout(() => { timeouts.delete(timer); if (active) callback(); }, delay);
        timeouts.add(timer); return timer;
      },
      setInterval(callback, delay) {
        const timer = window.setInterval(() => { if (active) callback(); }, delay);
        intervals.add(timer); return timer;
      },
      request(...args) {
        if (!active) return stopped();
        return request(...args).then(value => active ? value : stopped(), err => { if (!active) return stopped(); throw err; });
      },
      onCleanup(callback) { cleanups.push(callback); },
      dispose() {
        if (!active) return;
        // Draft savers run while their original fields are still attached.
        cleanups.forEach(cleanup => cleanup()); active = false;
        timeouts.forEach(window.clearTimeout); intervals.forEach(window.clearInterval);
        cleanups.length = 0; timeouts.clear(); intervals.clear();
      }
    };
  }

  window.App = { createPageScope, request, toast, confirm: confirmAction, setConnection, storage, eventState: null };
  App.page = createPageScope();
  window.showToast = toast;
  const init = () => {
    const page = App.page;
    const toggle = document.querySelector('.nav-toggle');
    toggle?.addEventListener('click', () => { const open = toggle.getAttribute('aria-expanded') !== 'true'; toggle.setAttribute('aria-expanded', String(open)); document.getElementById('site-navigation').classList.toggle('open', open); });
    if ('IntersectionObserver' in window && !matchMedia('(prefers-reduced-motion: reduce)').matches) {
      document.documentElement.classList.add('js-reveal');
      const observer = new IntersectionObserver(entries => entries.forEach(entry => { if (entry.isIntersecting) { entry.target.classList.add('visible'); observer.unobserve(entry.target); } }), { threshold: 0.06 });
      document.querySelectorAll('.reveal').forEach(el => observer.observe(el));
      page.onCleanup(() => observer.disconnect());
    }
    document.querySelectorAll('dialog').forEach(dialog => page.listen(dialog, 'click', event => { if (event.target === dialog && !['competition-dialog', 'participant-gate'].includes(dialog.id)) { const bounds = dialog.getBoundingClientRect(); if (event.clientX < bounds.left || event.clientX > bounds.right || event.clientY < bounds.top || event.clientY > bounds.bottom) dialog.close(); } }));
  };
  App.initPage = init;
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init); else init();
})();
