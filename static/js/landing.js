(() => {
  const page = App.page;
  const {setInterval, setTimeout} = page;
  // Static-JavaScript fallback; the React demo owns these animations when available.
  if (!window.App.demoEnhanced) {
  const stages = [ ['Scanning source…','RUNNING',false], ['Division by zero · line 08','DIAGNOSTIC',false], ['Guard the empty input.','APPLYING FIX',true], ['Running the sample tests…','TESTING',true], ['All checks passed. Exit code: 0','PASSED',true] ];
  let stage=0, paused=matchMedia('(prefers-reduced-motion: reduce)').matches;
  const button=document.getElementById('demo-toggle');
  function paint(){ const [text,badge,fixed]=stages[stage];document.getElementById('demo-status').textContent=text;document.getElementById('demo-status-badge').textContent=badge;document.getElementById('demo-status-badge').classList.toggle('is-success',stage===4);document.getElementById('demo-return-value').textContent=fixed?'0':'total / count';document.getElementById('demo-bug-line').classList.toggle('is-fixed',fixed);document.getElementById('demo-line-indicator').textContent=fixed?'✓':'!';document.getElementById('demo-problem-count').textContent=fixed?'0':'1'; }
  function toggle(){ button.textContent=paused?'Play demo':'Pause demo';button.setAttribute('aria-pressed',paused);document.documentElement.dataset.demoPaused=String(paused); }
  if(paused)stage=4;paint();toggle();button.addEventListener('click',()=>{paused=!paused;toggle();});
  setInterval(()=>{if(!paused&&!document.hidden){stage=(stage+1)%stages.length;paint();}},3000);
  }
  function event(state){document.getElementById('home-event-status').textContent=state.event_status==='WAITING'?'READY':state.event_status; if(state.connected_teams!==undefined)document.getElementById('home-team-count').textContent=state.connected_teams;}
  page.listen(document, 'eventstate',e=>event(e.detail));if(App.eventState)event(App.eventState);
  let pending=false;
  async function standings(){if(pending||document.hidden)return;pending=true;try{const data=await page.request('/api/leaderboard-data');document.getElementById('preview-state').textContent=data.leaderboard_state;const host=document.getElementById('home-standings');host.replaceChildren();if(!data.leaderboard.length){const p=document.createElement('p');p.className='preview-empty';p.textContent='No teams registered yet. The starting grid is yours.';host.append(p);}else data.leaderboard.slice(0,3).forEach((team,i)=>{const row=document.createElement('div');row.className='preview-row';const rank=document.createElement('span');rank.className='preview-rank';rank.textContent=String(i+1).padStart(2,'0');const name=document.createElement('strong');name.textContent=team.name;const score=document.createElement('span');score.className='preview-score';score.textContent=team.score;row.append(rank,name,score);host.append(row);});}catch(_){document.getElementById('home-standings').textContent='Standings unavailable. Reconnecting to the event server…';}finally{pending=false;}}
  standings();setInterval(standings,12000);
})();
