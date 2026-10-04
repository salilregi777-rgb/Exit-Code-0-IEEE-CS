/* Participant pages share one document so browser fullscreen survives navigation. */
(() => {
  'use strict';
  const routes = new Set(['/', '/arena', '/leaderboard', '/waiting', '/result', '/quiz']);
  const aliases = {'/debug-arena': '/arena', '/waiting-room': '/waiting', '/final-result': '/result'};
  const canonical = href => { const url = new URL(href, location.href); url.pathname = aliases[url.pathname] || url.pathname; return url; };
  let renderedPath = canonical(location.href).pathname, renderedSearch = location.search, serial = 0, loading = false, queued = null;
  const canNavigate = url => !!window.ParticipantGuard && url.origin === location.origin && routes.has(aliases[url.pathname] || url.pathname);
  const hardNavigate = (url, replace) => {
    window.ParticipantGuard?.leaveForNavigation();
    replace ? location.replace(url) : location.assign(url);
  };
  function scrollToTarget(url) {
    let target;
    try { target = url.hash && document.getElementById(decodeURIComponent(url.hash.slice(1))); } catch (_) {}
    if (target) target.scrollIntoView();
    else window.scrollTo(0, 0);
  }
  async function loadScript(source) {
    return new Promise((resolve, reject) => {
      const script = document.createElement('script');
      script.src = source.src; script.dataset.pageScript = '';
      script.onload = resolve; script.onerror = () => reject(new Error('Page controls could not load. Please retry.'));
      document.body.append(script);
    });
  }
  async function navigate(href, {replace = false, historyChange = false, refresh = false} = {}) {
    const url = canonical(href);
    if (!canNavigate(url)) { hardNavigate(url.href, replace); return false; }
    if (!refresh && !loading && url.pathname === renderedPath && url.search === renderedSearch) {
      if (!historyChange && url.href !== location.href) history[replace ? 'replaceState' : 'pushState']({}, '', url);
      scrollToTarget(url); return true;
    }
    if (loading) { queued = {href: url.href, options: {replace, historyChange, refresh}}; return false; }
    const ticket = ++serial;
    loading = true;
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 12000);
    document.getElementById('main-content').setAttribute('aria-busy', 'true');
    try {
      const response = await fetch(url, {signal: controller.signal, credentials: 'same-origin', cache: 'no-store', headers: {'X-Participant-Navigation': '1'}});
      if (!response.ok) throw new Error('This page could not load. Please try again.');
      const destination = canonical(response.url);
      if (!routes.has(destination.pathname)) { hardNavigate(destination.href, true); return false; }
      const next = new DOMParser().parseFromString(await response.text(), 'text/html');
      const incoming = next.getElementById('main-content');
      const incomingGate = next.getElementById('participant-gate');
      const currentGate = document.getElementById('participant-gate');
      if (!incoming || !incomingGate || incomingGate.dataset.team !== currentGate?.dataset.team || incomingGate.dataset.generation !== currentGate?.dataset.generation) {
        hardNavigate(destination.href, true); return false;
      }
      // Load styles before replacing the visible page; shared styles keep their order.
      const styles = [...next.querySelectorAll('link[rel="stylesheet"]')];
      await Promise.all(styles.filter(link => ![...document.querySelectorAll('link[rel="stylesheet"]')].some(current => current.href === link.href)).map(link => new Promise((resolve, reject) => {
        const copy = link.cloneNode(true); copy.onload = resolve; copy.onerror = () => { copy.remove(); reject(new Error('Page styles could not load. Please retry.')); };
        document.head.insertBefore(copy, document.querySelector('link[href*="css/interactions.css"]'));
      })));
      if (ticket !== serial || queued) return false;
      App.page.dispose();
      document.getElementById('confirm-dialog')?.close();
      App.unmountReactBits?.();
      document.querySelectorAll('[data-page-script]').forEach(script => script.remove());
      document.getElementById('main-content').replaceWith(incoming);
      for (const selector of ['.navbar', '.app-footer']) document.querySelector(selector)?.replaceWith(next.querySelector(selector));
      document.body.className = next.body.className;
      document.title = next.title;
      // Keep the guard, fullscreen element, animation field and shared clocks alive.
      const initialState = next.getElementById('initial-event-state');
      if (initialState) document.getElementById('initial-event-state').textContent = initialState.textContent;
      const finalURL = response.redirected ? destination : url;
      if (!historyChange || response.redirected) history[replace || historyChange ? 'replaceState' : 'pushState']({}, '', finalURL);
      renderedPath = finalURL.pathname; renderedSearch = finalURL.search;
      App.page = App.createPageScope();
      App.initPage();
      App.mountReactBits?.();
      if (initialState) EventClock.apply(JSON.parse(initialState.textContent));
      for (const script of next.querySelectorAll('[data-page-script]')) await loadScript(script);
      renderedPath = canonical(location.href).pathname;
      renderedSearch = location.search; // Arena selects its current question in history.
      document.dispatchEvent(new CustomEvent('participant:page-ready'));
      scrollToTarget(finalURL);
      return true;
    } catch (err) {
      if (historyChange && !queued) history.replaceState({}, '', renderedPath + renderedSearch);
      App.toast(err.message || 'Unable to open this page. Your current session is still active.', 'error');
      return false;
    } finally {
      clearTimeout(timeout);
      loading = false; document.getElementById('main-content')?.removeAttribute('aria-busy');
      if (queued) { const next = queued; queued = null; navigate(next.href, next.options); }
    }
  }
  document.addEventListener('click', event => {
    const link = event.target.closest?.('a[href]');
    if (!link || event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey || link.hasAttribute('download') || (link.target && link.target !== '_self')) return;
    const url = new URL(link.href, location.href);
    if (!canNavigate(url)) return;
    if (url.pathname === location.pathname && url.search === location.search && url.hash) return;
    event.preventDefault(); navigate(url);
  });
  window.addEventListener('popstate', () => {
    // Question changes are handled by the mounted arena without fetching a page.
    if (!loading && renderedPath === '/arena' && canonical(location.href).pathname === '/arena') { renderedSearch = location.search; return; }
    if (renderedPath === location.pathname && renderedSearch === location.search) { scrollToTarget(new URL(location.href)); return; }
    navigate(location.href, {historyChange: true});
  });
  App.navigation = {navigate, canNavigate, trackLocation: () => {renderedSearch = location.search;}, refresh: () => navigate(location.href, {replace: true, refresh: true}), get loading() {return loading;}};
})();
