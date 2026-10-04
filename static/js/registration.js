(() => {
  'use strict';
  const tabs = document.querySelector('[data-react-tabs]')?.dataset.reactMounted === 'true' ? [] : [...document.querySelectorAll('.auth-tab')];
  function select(tab) {
    tabs.forEach(item => { const active = item === tab; item.classList.toggle('is-active', active); item.setAttribute('aria-selected', active); item.tabIndex = active ? 0 : -1; document.getElementById(item.getAttribute('aria-controls')).hidden = !active; });
    document.querySelector('.auth-tabs')?.dispatchEvent(new CustomEvent('segmentchange'));
  }
  tabs.forEach((tab, i) => { tab.addEventListener('click', () => select(tab)); tab.addEventListener('keydown', e => { if (!['ArrowLeft','ArrowRight','Home','End'].includes(e.key)) return; e.preventDefault(); const next = e.key === 'Home' ? 0 : e.key === 'End' ? tabs.length - 1 : (i + (e.key === 'ArrowRight' ? 1 : -1) + tabs.length) % tabs.length; select(tabs[next]); tabs[next].focus(); }); });
  const registrationForm = document.getElementById('team-register-form');
  let registrationOpen = registrationForm?.dataset.registrationOpen === 'true';
  function closeRegistration() {
    const wasOpen = registrationOpen;
    registrationOpen = false;
    if (registrationForm) {
      registrationForm.dataset.registrationOpen = 'false';
      registrationForm.hidden = true;
      registrationForm.inert = true;
      registrationForm.querySelectorAll('input, button').forEach(control => { control.disabled = true; });
    }
    document.getElementById('registration-closed-notice').hidden = false;
    document.getElementById('auth-heading').textContent = 'Welcome back.';
    document.getElementById('registration-intro').textContent = 'Registration is closed. Registered teams can still sign in.';
    if (wasOpen) document.getElementById('login-tab')?.click();
  }
  if (!registrationOpen) closeRegistration();
  let checkingRegistration = false;
  async function checkRegistrationWindow() {
    if (!registrationOpen || checkingRegistration || document.hidden) return;
    checkingRegistration = true;
    try {
      const state = await App.request('/api/event-status');
      if (state.event_status !== 'WAITING' || state.results_published) closeRegistration();
    } catch (_) { /* The POST transaction remains authoritative during a network interruption. */ }
    finally { checkingRegistration = false; }
  }
  const registrationPoll = window.setInterval(checkRegistrationWindow, 3000);
  document.addEventListener('visibilitychange', checkRegistrationWindow);
  window.addEventListener('pagehide', () => window.clearInterval(registrationPoll));
  checkRegistrationWindow();
  let debounce, version = 0;
  const name = document.getElementById('reg-name'), feedback = document.getElementById('team-name-feedback');
  name?.addEventListener('input', () => {
    clearTimeout(debounce); const own = ++version; feedback.className = 'field-hint'; feedback.textContent = name.value.trim() ? 'Checking availability…' : 'Give your team a name worth remembering.';
    if (!name.value.trim()) return;
    debounce = setTimeout(async () => { try { const data = await App.request(`/api/team-name-available?name=${encodeURIComponent(name.value.trim())}`); if (own !== version) return; feedback.textContent = data.available ? '✓ Team name available' : 'Team name already exists. Use Team login to continue.'; feedback.className = data.available ? 'field-hint text-success' : 'field-error'; } catch (_) { if (own === version) feedback.textContent = 'Availability will be checked when you register.'; } }, 350);
  });
  document.querySelectorAll('.team-access-form').forEach(form => {
    form.noValidate = true;
    form.addEventListener('submit', e => {
      if (form === registrationForm && !registrationOpen) {
        e.preventDefault();
        closeRegistration();
        document.getElementById('login-tab')?.click();
        return;
      }
      let first;
      form.querySelectorAll('input:not([type=hidden])').forEach(input => {
        const invalid = (input.required && !input.value.trim()) || !input.validity.valid;
        input.setAttribute('aria-invalid', invalid);
        const error = document.getElementById(input.getAttribute('aria-describedby'));
        if (error && invalid) error.textContent = input.type === 'email' ? 'Enter a valid email address.' : `${form.querySelector(`label[for="${input.id}"]`)?.textContent.replace('*','').trim() || 'This field'} is required.`;
        else if(error && input !== name) error.textContent = '';
        if (invalid && !first) first = input;
      });
      if (first) { e.preventDefault(); first.focus(); App.toast('Please check the highlighted fields.', 'warning'); return; }
      const button = form.querySelector('[type=submit]'); button.disabled = true; button.classList.add('loading'); button.textContent = 'Connecting your team…';
    });
  });
  window.addEventListener('pageshow', e => { if(e.persisted) location.reload(); });
})();
