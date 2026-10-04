/* Participant state stays server-controlled; only unsent drafts and review flags are local. */
(() => {
  const page = App.page;
  const {setInterval, setTimeout} = page;
  'use strict';
  const fields = ['error_location', 'error_type', 'expected_output', 'correction'];
  let root, form, qid, progress, generation, draftKey, dirty = false, busy = false, switching = false, submitted = false, requestID = null, reviews = {}, saveTimeout, syncPending = null, progressEpoch = 0, loadSerial = 0;
  const byId = id => document.getElementById(id);
  const navigate = url => window.ParticipantGuard ? window.ParticipantGuard.navigate(url, { replace: true }) : location.replace(url);
  const canSubmit = () => App.eventState?.event_status === 'LIVE' && Number(App.eventState.remaining_seconds) > 0;
  const current = () => progress?.questions.find(q => q.id === qid);
  const isAnswered = () => submitted || !!(current()?.is_answered ?? current()?.is_completed);
  const statusLabel = status => ({ correct: '✓ Correct', partial: '◐ Partially correct', incorrect: '✕ Incorrect' })[status] || 'Answer locked';
  const value = () => Object.fromEntries(fields.map(name => [name, byId(name).value]));
  const draftNamespace = () => `ex0:draft:${generation}:${root.dataset.teamId}:`;
  function setDraftKey() { draftKey = `${draftNamespace()}${qid}`; }
  function saveDraft() {
    if (!dirty || !draftKey || submitted) return;
    const saved = App.storage.set(draftKey, { ...value(), request_id: requestID, saved_at: Date.now() });
    byId('draft-status').textContent = saved ? 'Saved locally ✓' : 'Local storage unavailable';
  }
  function restoreDraft() {
    setDraftKey();
    const draft = isAnswered() ? null : App.storage.get(draftKey);
    fields.forEach(name => { byId(name).value = typeof draft?.[name] === 'string' ? draft[name] : ''; byId(name).removeAttribute('aria-invalid'); document.querySelector(`[data-error-for="${name}"]`).textContent = ''; });
    requestID = draft?.request_id || null; dirty = !!draft; submitted = !!(current()?.is_answered ?? current()?.is_completed);
    byId('draft-status').textContent = draft ? 'Draft restored from this device' : 'Drafts save on this device';
    CodeEditor.selectLine(byId('error_location').value, false);
    byId('form-feedback-alert').hidden = true; byId('next-question').hidden = true; byId('submission-trace').hidden = true;
    updateReview(); renderAnswerStatus(); updateControls();
  }
  function hydrateSubmission(question) {
    if (question?.submission) {
      fields.forEach(name => { byId(name).value = question.submission[name] ?? ''; });
      CodeEditor.selectLine(byId('error_location').value, false);
    }
  }
  function renderAnswerStatus(result = current()) {
    const status = byId('answer-status');
    status.hidden = !isAnswered();
    renderFieldResults(isAnswered() ? result?.field_results : null);
    if (!isAnswered()) return;
    const verdict = result?.answer_status || current()?.answer_status;
    const earned = result?.awarded_score ?? result?.total_score ?? 0;
    const maximum = result?.max_score ?? result?.base_points ?? current()?.points ?? 0;
    status.className = `answer-status status-${verdict || 'locked'}`;
    const summary = document.createElement('strong');
    summary.textContent = `${statusLabel(verdict)} · ${earned} / ${maximum} pts`;
    const description = document.createElement('span');
    const modifiers = [];
    for (const [key, label, used] of [['hint', 'Hint', result?.hint_used], ['swap', 'Swap', result?.swap_used], ['double_commit', 'Double Commit', result?.is_double_commit]]) {
      if (!used) continue;
      const adjustment = Number(result?.powerup_adjustments?.[key] || 0);
      if (adjustment) modifiers.push(`${label}: ${adjustment > 0 ? '+' : '−'}${Math.abs(adjustment)} already applied to team total`);
      else if (key !== 'double_commit' && result?.penalties?.[key]) modifiers.push(`${label}: −${result.penalties[key]} under the original scoring rules`);
      else modifiers.push(`${label} used under the original scoring rules`);
    }
    description.textContent = `This answer is final.${modifiers.length ? ' ' + modifiers.join(' · ') + '.' : ''}`;
    status.replaceChildren(summary, description);
    if (Array.isArray(result?.field_results)) {
      const heading = document.createElement('strong');
      heading.className = 'answer-breakdown-heading'; heading.textContent = 'Your answer, part by part';
      const breakdown = document.createElement('ul'); breakdown.className = 'answer-breakdown';
      for (const field of result.field_results) {
        if (!fields.includes(field.key) || !['correct', 'partial', 'incorrect'].includes(field.status)) continue;
        const item = document.createElement('li'); item.dataset.field = field.key;
        const label = document.createElement('span'); label.textContent = field.label;
        const outcome = document.createElement('strong'); outcome.className = `status-${field.status}`;
        outcome.textContent = `${statusLabel(field.status)} · ${field.score} / ${field.max_score}`;
        item.append(label, outcome); breakdown.append(item);
      }
      const note = document.createElement('span'); note.className = 'answer-breakdown-note';
      note.textContent = 'These are question points. Fixed power-up changes are applied separately to your team total, once per tool. Organizer overrides change the question total only.';
      if (result.score_overridden) note.textContent += ' Your total includes an organizer adjustment.';
      status.append(heading, breakdown, note);
    }
    byId('draft-status').textContent = 'Answer locked on the server';
    const next = progress?.questions.find(q => q.is_unlocked && !(q.is_answered ?? q.is_completed) && q.id !== qid);
    byId('next-question').hidden = !next; byId('next-question').dataset.next = next?.id || '';
  }
  function renderFieldResults(results) {
    for (const name of fields) {
      const input = byId(name); const container = input.closest('.field');
      const id = `field-result-${name}`;
      byId(id)?.remove(); delete container.dataset.answerStatus;
      const descriptions = (input.getAttribute('aria-describedby') || '').split(/\s+/).filter(value => value && value !== id);
      const result = Array.isArray(results) ? results.find(item => item.key === name && ['correct', 'partial', 'incorrect'].includes(item.status)) : null;
      if (result) {
        const badge = document.createElement('small'); badge.id = id; badge.className = `field-result status-${result.status}`;
        badge.textContent = `${statusLabel(result.status)} · ${result.score} / ${result.max_score} pts`;
        input.insertAdjacentElement('afterend', badge); container.dataset.answerStatus = result.status;
        descriptions.push(id);
      }
      if (descriptions.length) input.setAttribute('aria-describedby', descriptions.join(' '));
      else input.removeAttribute('aria-describedby');
    }
  }
  function updateReview() {
    const reviewed = !!reviews[qid];
    byId('review-toggle').setAttribute('aria-pressed', String(reviewed));
    byId('review-toggle').textContent = reviewed ? '⚑ Marked' : '⚑ Review';
    document.querySelectorAll('[data-question]').forEach(button => {
      const reviewedQuestion = !!reviews[button.dataset.question];
      button.classList.toggle('review', reviewedQuestion);
      if (reviewedQuestion && button.dataset.completed !== '1') button.querySelector('.question-symbol').textContent = '⚑';
    });
  }
  function updateControls() {
    const locked = isAnswered();
    const disabled = !canSubmit() || busy || switching || !qid || !progress || locked;
    byId('commit-fix-btn').disabled = disabled;
    byId('commit-fix-btn').classList.toggle('loading', busy);
    byId('commit-fix-btn').textContent = busy ? 'VALIDATING…' : !canSubmit() ? (App.eventState?.event_status === 'PAUSED' ? 'EVENT PAUSED' : 'SUBMISSIONS CLOSED') : locked ? 'ANSWER LOCKED' : '⑂  LOCK ANSWER  →';
    document.querySelectorAll('[data-powerup]').forEach(button => {
      const key = button.dataset.powerup.toUpperCase().replaceAll('-', '_');
      const pu = progress?.powerups?.[key];
      button.disabled = disabled || !!pu?.is_used || !!pu?.is_armed;
      const label = button.querySelector('[data-powerup-status]');
      const descriptions = { 'rubber-duck': 'Get a hint · −5 points now', 'git-revert': 'Replace challenge · −7 points now', 'double-commit': 'Add +15 points now' };
      const applied = Number(pu?.score_adjustment || 0);
      label.textContent = pu?.is_used ? `Used${applied ? ` · ${applied > 0 ? '+' : '−'}${Math.abs(applied)} points applied` : ''}` : pu?.is_armed ? 'Armed under original rules' : descriptions[button.dataset.powerup];
      if (button.dataset.powerup === 'git-revert' && current()?.is_completed) button.disabled = true;
    });
    fields.forEach(name => { byId(name).disabled = busy || switching || locked || !progress; });
    byId('review-toggle').disabled = locked;
    document.querySelectorAll('#code-viewer-container .line-number').forEach(button => { button.disabled = busy || switching || locked || !progress; });
  }
  function renderProgress(data) {
    progress = data;
    const score = byId('arena-team-score');
    const newScore = String(data.score);
    if (score.textContent.trim() !== newScore) { score.textContent = newScore; score.classList.remove('score-changed'); void score.offsetWidth; score.classList.add('score-changed'); }
    byId('completed-count').textContent = data.completed_count; byId('question-total').textContent = data.total_questions; byId('sidebar-question-total').textContent = data.total_questions;
    byId('question-progress').value = data.completed_count; byId('question-progress').max = data.total_questions || 1;
    const list = byId('question-list'); const fragment = document.createDocumentFragment();
    for (const question of data.questions) {
      const button = document.createElement('button'); button.type = 'button'; button.className = 'question-item'; button.dataset.question = question.id; button.dataset.order = question.question_order; button.dataset.completed = question.is_completed ? '1' : '0';
      if (question.answer_status) button.classList.add(`answer-${question.answer_status}`);
      button.disabled = !question.is_unlocked; button.classList.toggle('current', question.id === qid); button.classList.toggle('completed', !!question.is_completed);
      if (question.id === qid) button.setAttribute('aria-current', 'true');
      button.title = `Question ${question.question_order} · ${question.difficulty} · ${question.points} points`;
      const number = document.createElement('span'); number.className = 'question-number'; number.textContent = String(question.question_order).padStart(2, '0');
      const label = document.createElement('span'); label.className = 'question-label'; const title = document.createElement('strong'); title.textContent = `Question ${question.question_order}`; const info = document.createElement('small'); info.textContent = question.is_answered ? `${question.awarded_score} / ${question.max_score ?? question.points} pts · ${statusLabel(question.answer_status).replace(/^[^ ]+ /, '')}` : `${question.points} pts · ${question.difficulty}`; label.append(title, info);
      const symbol = document.createElement('span'); symbol.className = 'question-symbol'; symbol.textContent = question.answer_status === 'incorrect' ? '✕' : question.answer_status === 'partial' ? '◐' : question.is_completed ? '✓' : question.id === qid ? '●' : question.is_unlocked ? '○' : '−'; symbol.setAttribute('aria-label', question.answer_status ? statusLabel(question.answer_status) : question.is_completed ? 'Submitted' : question.is_unlocked ? 'Available' : 'Locked');
      button.append(number, label, symbol); fragment.append(button);
    }
    const scroll = list.scrollTop; list.replaceChildren(fragment); list.scrollTop = scroll; updateReview(); renderAnswerStatus(); updateControls();
  }
  async function syncProgress() {
    if (syncPending) return syncPending;
    syncPending = (async () => {
      try {
        while (page.active) {
          const epoch = progressEpoch;
          const data = await page.request('/api/team-progress');
          // A poll started before a tool/submission must not overwrite the
          // newly confirmed total. Fetch the current state before rendering.
          if (epoch !== progressEpoch) continue;
          if (generation && data.generation !== generation) { navigate('/register'); return; }
          generation = data.generation; renderProgress(data); return data;
        }
      } catch (err) { if (err.status === 401 || err.status === 403) { byId('arena-state-notice').hidden = false; byId('arena-state-notice').textContent = err.message; progress = null; updateControls(); } }
      finally { syncPending = null; }
    })();
    return syncPending;
  }
  async function selectQuestion(id, push = true) {
    if (!id || busy || switching || id === qid) return;
    saveDraft(); switching = true; updateControls();
    const serial = ++loadSerial;
    document.querySelector('.arena-workspace').classList.add('is-loading');
    try {
      const data = await page.request(`/api/question/${encodeURIComponent(id)}`);
      if (serial !== loadSerial) return;
      const q = data.question; submitted = false; qid = q.id;
      const known = current(); if (known) Object.assign(known, q); byId('form-question-id').value = qid;
      byId('breadcrumb-question').textContent = qid; byId('question-title').textContent = q.title; byId('question-task').textContent = q.task || ''; byId('question-difficulty').textContent = q.difficulty; byId('question-points').textContent = `${q.points} PTS`; byId('source-language').textContent = q.language;
      byId('source-filename').textContent = ({ Python: 'main.py', C: 'main.c' })[q.language] || 'source';
      byId('challenge-number').textContent = `CHALLENGE ${String(current()?.question_order || qid).padStart(2, '0')}`;
      CodeEditor.render(q.code); restoreDraft(); hydrateSubmission(q); byId('rubber-duck-hint-box').hidden = true;
      if (progress) renderProgress(progress);
      if (push) history.pushState({ question: qid }, '', `/arena?q=${encodeURIComponent(qid)}`);
      App.navigation?.trackLocation();
      document.title = `${qid} · Debug Arena — EXIT CODE 0`;
    } catch (err) { App.toast(err.message, 'error'); }
    finally { switching = false; document.querySelector('.arena-workspace').classList.remove('is-loading'); updateControls(); }
  }
  function validate() {
    let firstInvalid;
    for (const name of fields) {
      const input = byId(name); const invalid = !input.value.trim() || !input.validity.valid;
      input.setAttribute('aria-invalid', String(invalid));
      document.querySelector(`[data-error-for="${name}"]`).textContent = invalid ? name === 'error_location' ? 'Choose a line in the source.' : 'This field is required.' : '';
      if (invalid && !firstInvalid) firstInvalid = input;
    }
    firstInvalid?.focus(); return !firstInvalid;
  }
  async function submit(event) {
    event.preventDefault(); if (busy || isAnswered() || !canSubmit() || !progress || !validate()) return;
    busy = true; updateControls();
    const confirmed = await App.confirm('Your answer will be scored and locked. You cannot edit or submit this question again.', { title: 'Lock this answer?', confirmText: 'Lock answer' });
    if (!page.active) return;
    if (!confirmed) { busy = false; updateControls(); return; }
    if (isAnswered() || !canSubmit()) { busy = false; updateControls(); return; }
    requestID ||= (crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(36).slice(2)}`);
    dirty = true; saveDraft(); updateControls();
    const activity = App.activity(byId('submission-trace'), 'Waiting for server validation…');
    try {
      const data = await page.request('/api/submit-bug-fix', { method: 'POST', body: { question_id: qid, request_id: requestID, ...value() } });
      progressEpoch++;
      activity.finish(true, 'Submission recorded');
      submitted = true; dirty = false; App.storage.remove(draftKey); requestID = null;
      const result = { ...data.result, is_answered: true, is_completed: true, awarded_score: data.result.total_score };
      if (current()) Object.assign(current(), result);
      renderAnswerStatus(result);
      const alert = byId('form-feedback-alert'); alert.hidden = true;
      byId('draft-status').textContent = 'Submission saved to server ✓';
      byId('arena-team-score').textContent = data.new_score;
      byId('arena-team-score').classList.add('score-changed');
      App.toast(`${statusLabel(data.result.answer_status)}. Answer locked.`, data.result.answer_status === 'incorrect' ? 'warning' : 'success');
      await syncProgress();
      const next = progress?.questions.find(q => q.is_unlocked && !q.is_completed && q.id !== qid);
      byId('next-question').hidden = !next; byId('next-question').dataset.next = next?.id || '';
      byId('answer-status').scrollIntoView({ behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth', block: 'nearest' });
    } catch (err) {
      activity.finish(false, 'Submission not confirmed');
      const alert = byId('form-feedback-alert'); alert.hidden = false; alert.className = 'alert alert-warning'; alert.textContent = err.message;
      if (!err.offline && err.status < 500) requestID = null;
      App.toast(err.message, 'error'); EventClock.sync(); await syncProgress();
    } finally { busy = false; updateControls(); }
  }
  async function usePowerup(button) {
    if (busy || isAnswered() || !canSubmit()) return;
    const type = button.dataset.powerup;
    const descriptions = { 'rubber-duck': ['Use Rubber Duck?', 'Reveal one hint and deduct 5 points from your team total immediately. One use per team.'], 'git-revert': ['Replace this challenge?', 'Replace this challenge permanently and deduct 7 points from your team total immediately. One use per team.'], 'double-commit': ['Use Double Commit?', 'Add 15 points to your team total immediately. Your answer will earn its normal question points. One use per team.'] };
    if (!await App.confirm(descriptions[type][1], { title: descriptions[type][0], confirmText: 'Activate' })) return;
    if (!page.active) return;
    busy = true; updateControls(); button.classList.add('loading');
    try {
      const data = await page.request(`/api/powerup/${type}`, { method: 'POST', body: { question_id: qid } });
      progressEpoch++;
      if (Number.isFinite(data.new_score)) {
        byId('arena-team-score').textContent = data.new_score;
        byId('arena-team-score').classList.remove('score-changed');
        void byId('arena-team-score').offsetWidth;
        byId('arena-team-score').classList.add('score-changed');
      }
      App.toast(data.message, 'success');
      if (data.hint) { byId('rubber-duck-hint-box').textContent = data.hint; byId('rubber-duck-hint-box').hidden = false; }
      await syncProgress();
      if (data.new_question_id) { busy = false; dirty = false; App.storage.remove(draftKey); await selectQuestion(data.new_question_id); }
    } catch (err) { App.toast(err.message, 'error'); }
    finally { busy = false; button.classList.remove('loading'); updateControls(); }
  }
  function onState(state) {
    if (!root) return;
    const notice = byId('arena-state-notice');
    if (state.event_status === 'PAUSED') { notice.hidden = false; notice.textContent = 'Competition paused by the organizers. Your draft is saved; submissions will resume with the event.'; }
    else if (state.event_status === 'COMPLETED') { saveDraft(); notice.hidden = false; notice.textContent = 'Debugging complete. Submissions are locked. Opening your results…'; setTimeout(() => navigate('/result'), 1400); }
    else if (state.event_status === 'WAITING') { saveDraft(); navigate('/waiting'); }
    else notice.hidden = true;
    updateControls();
  }
  async function init() {
    root = byId('arena'); if (!root) return;
    form = byId('bug-fix-form'); qid = byId('form-question-id').value;
    if (qid) history.replaceState({ question: qid }, '', `/arena?q=${encodeURIComponent(qid)}`);
    form.addEventListener('submit', submit);
    form.addEventListener('input', () => { if (isAnswered()) return; dirty = true; requestID = null; byId('draft-status').textContent = 'Saving draft…'; clearTimeout(saveTimeout); saveTimeout = setTimeout(saveDraft, 350); updateControls(); });
    form.addEventListener('paste', event => { event.preventDefault(); App.toast('Pasting answers is restricted in competition mode.', 'warning'); });
    byId('question-list').addEventListener('click', event => { const button = event.target.closest('[data-question]'); if (button && !button.disabled) selectQuestion(button.dataset.question); });
    byId('next-question').addEventListener('click', () => selectQuestion(byId('next-question').dataset.next));
    byId('review-toggle').addEventListener('click', () => { reviews[qid] = !reviews[qid]; App.storage.set(`${draftNamespace()}reviews`, reviews); if (progress) renderProgress(progress); else updateReview(); });
    document.querySelectorAll('[data-powerup]').forEach(button => button.addEventListener('click', () => usePowerup(button)));
    page.listen(document, 'eventstate', event => onState(event.detail));
    page.listen(window, 'pagehide', saveDraft);
    page.onCleanup(saveDraft);
    page.listen(window, 'popstate', () => { if (location.pathname !== '/arena') return; const target = new URLSearchParams(location.search).get('q') || progress?.questions.find(q => q.is_unlocked && !q.is_completed)?.id || progress?.questions[0]?.id; selectQuestion(target, false); });
    page.listen(document, 'visibilitychange', () => { if (document.hidden) saveDraft(); else syncProgress(); });
    updateControls();
    const data = await syncProgress();
    if (data) { reviews = App.storage.get(`${draftNamespace()}reviews`, {}); restoreDraft();
      if (isAnswered()) { const initialID = qid; try { const answer = await page.request(`/api/question/${encodeURIComponent(initialID)}`); if (qid === initialID) hydrateSubmission(answer.question); } catch (_) { /* Score and answer lock remain visible during a network failure. */ } } }
    else { byId('draft-status').textContent = 'Reconnect to enable saved drafts'; }
    if (App.eventState) onState(App.eventState);
    setInterval(async () => { if (!document.hidden) { const first = !generation; const data = await syncProgress(); if (first && data) { reviews = App.storage.get(`${draftNamespace()}reviews`, {}); if (dirty) { setDraftKey(); saveDraft(); } else restoreDraft(); } } }, 10000);
  }
  if (document.readyState === 'loading') page.listen(document, 'DOMContentLoaded', init); else init();
})();
