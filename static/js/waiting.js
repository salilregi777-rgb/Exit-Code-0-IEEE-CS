(() => {
  const page = App.page;
  const {setInterval, setTimeout} = page;
  const navigate = url => window.ParticipantGuard ? window.ParticipantGuard.navigate(url, {replace: true}) : location.replace(url);
  let entering = false;
  function update(state) {
    const count = document.getElementById('teams-registered-count'); if(count && state.connected_teams !== undefined) count.textContent = state.connected_teams;
    if(state.event_status === 'COMPLETED') { navigate('/result'); return; }
    if(state.event_status !== 'LIVE' || entering) return;
    entering = true;
    document.getElementById('lobby-status-heading').textContent = 'Event started.';
    document.getElementById('lobby-status-description').textContent = 'Preparing your debug arena. The server clock is running.';
    document.getElementById('lobby-status-text').textContent = 'Entering the arena';
    const counter = document.getElementById('lobby-countdown'); counter.hidden = false; let number = 3; counter.textContent = number;
    const interval = setInterval(() => { number--; if(number) counter.textContent = number; else { clearInterval(interval); navigate('/arena'); } }, 600);
  }
  page.listen(document, 'eventstate', e => update(e.detail)); if(App.eventState) update(App.eventState);
})();
