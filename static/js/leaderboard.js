(() => {
  const page = App.page;
  const {setInterval, setTimeout} = page;
 'use strict';
 const byId=id=>document.getElementById(id);let pending=false,previous=new Map(),signature='',movement=new Map();
 function el(tag,cls,text){const n=document.createElement(tag);n.className=cls||'';if(text!==undefined)n.textContent=text;return n;}
 function render(data){
  const mode=data.event_status==='WAITING'?'READY':data.leaderboard_state;byId('lb-mode-text').textContent=mode;byId('lb-mode').dataset.mode=mode;
  byId('lb-team-count').textContent=`${data.leaderboard.length} registered teams`;byId('lb-updated').textContent='Updated '+new Date().toLocaleTimeString([],{hour:'2-digit',minute:'2-digit',second:'2-digit'});
  const nextSignature=JSON.stringify([data.leaderboard,data.current_team_id,mode]);if(signature===nextSignature)return;signature=nextSignature;
  const podium=byId('leaderboard-podium'),body=byId('leaderboard-table-body');podium.replaceChildren();body.replaceChildren();podium.setAttribute('aria-busy','false');
  if(!data.leaderboard.length){podium.hidden=true;const row=el('tr');const cell=el('td');cell.colSpan=5;const empty=el('div','table-empty');empty.append(el('h3','','The starting grid is open.'),el('p','','No teams registered yet. Standings will appear here as teams join.'));cell.append(empty);row.append(cell);body.append(row);return;}
  podium.hidden=false;
  data.leaderboard.forEach((team,i)=>{
   const rank=i+1,own=team.id===data.current_team_id,old=previous.get(team.id);
   if(old&&old!==rank)movement.set(team.id,{delta:old-rank,until:performance.now()+8000});
   if(i<3){const card=el('article',`podium-card podium-${rank}${own?' own-team':''}`);const top=el('div','podium-top');top.append(el('span','eyebrow',rank===1?(mode==='FINAL'?'CHAMPION':'LEADING'):`POSITION ${rank}`),el('span','podium-rank',String(rank).padStart(2,'0')));const score=el('div','podium-score',Number(team.score).toLocaleString());score.append(el('span','','DEBUGGING POINTS'));card.append(top,el('div','podium-avatar',team.name.slice(0,2).toUpperCase()),el('h3','',team.name),el('span','mono muted',team.id),score);if(own)card.append(el('span','podium-own','YOUR TEAM'));podium.append(card);}
   const row=el('tr',own?'own-team':'');row.dataset.teamId=team.id;if(old!==undefined&&old!==rank)row.classList.add('rank-changed');const rankCell=el('td','rank-cell',String(rank).padStart(2,'0'));const move=movement.get(team.id);if(move&&move.until>performance.now())rankCell.append(el('span',move.delta>0?'rank-movement rank-up':'rank-movement rank-down',`${move.delta>0?'↑':'↓'} ${Math.abs(move.delta)}`));const teamCell=el('td','team-cell');const identity=el('div','');identity.append(el('strong','',team.name),el('small','mono muted',team.id));teamCell.append(identity);if(own)teamCell.append(el('span','badge badge-primary','YOU'));row.append(rankCell,teamCell,el('td','mono',team.completed_count),el('td','score-cell mono',Number(team.score).toLocaleString()));const status=el('td');status.append(el('span','badge',mode==='FINAL'?'FINAL':data.event_status==='COMPLETED'?'LOCKED':data.event_status==='PAUSED'?'PAUSED':team.completed_count?'COMPETING':'READY'));row.append(status);body.append(row);
  });previous=new Map(data.leaderboard.map((t,i)=>[t.id,i+1]));
 }
 async function refresh(){if(pending)return;pending=true;const button=byId('refresh-leaderboard');button.disabled=true;button.classList.add('loading');try{render(await page.request('/api/leaderboard-data'));}catch(err){byId('lb-updated').textContent='Reconnecting…';if(!previous.size){byId('leaderboard-podium').hidden=true;byId('leaderboard-table-body').replaceChildren();const row=el('tr');const cell=el('td','table-empty',err.message+' Use Refresh to retry.');cell.colSpan=5;row.append(cell);byId('leaderboard-table-body').append(row);}}finally{pending=false;button.disabled=false;button.classList.remove('loading');}}
 byId('refresh-leaderboard').addEventListener('click',refresh);refresh();setInterval(()=>{if(!document.hidden)refresh();document.querySelectorAll('.rank-movement').forEach(n=>{const id=n.closest('tr').dataset.teamId;if((movement.get(id)?.until||0)<performance.now())n.remove();});},5000);
})();
