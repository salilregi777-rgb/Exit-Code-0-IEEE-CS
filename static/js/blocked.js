/* A blocked team can recover in place after an organizer restores access. */
(() => {
  'use strict';
  const button = document.getElementById('blocked-recheck');
  const status = document.getElementById('blocked-status');
  if (!button || !status) return;
  let checking = false;
  async function check() {
    if (checking || document.hidden) return;
    checking = true;
    button.disabled = true;
    try {
      const response = await fetch('/api/fullscreen/status', { credentials: 'same-origin', cache: 'no-store' });
      if (response.status === 401) { location.replace('/register'); return; }
      if (!response.ok) throw new Error('Unable to check access. Reconnect and try again.');
      const state = await response.json();
      if (!state.blocked && state.team_active) {
        status.textContent = 'Access restored. Returning to the arena…';
        location.replace('/arena');
        return;
      }
      status.textContent = 'Your account is still blocked. An organizer can restore access; we will keep checking.';
    } catch (error) { status.textContent = error.message; }
    finally { checking = false; button.disabled = false; }
  }
  button.addEventListener('click', check);
  window.addEventListener('focus', check);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) check(); });
  const poll = setInterval(check, 6000);
  window.addEventListener('pagehide', () => clearInterval(poll));
  check();
})();
