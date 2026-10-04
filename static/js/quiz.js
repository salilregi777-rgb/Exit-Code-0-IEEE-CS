/* Quiz results and feedback are persisted by the server; answer keys stay private. */
(() => {
  const page = App.page;
  const {setInterval, setTimeout} = page;
  'use strict';
  const byId = id => document.getElementById(id);
  const show = (id, visible) => { byId(id).hidden = !visible; };
  const labels = { correct: '✓ Correct', incorrect: '✕ Incorrect', unanswered: '— Unanswered' };
  let snapshot = null, busy = false, polling = false, feedbackBusy = false, currentID = null, receivedAt = 0, reviewKey = '', feedbackKey = '', requestEpoch = 0;

  function renderReview(answers = []) {
    show('quiz-review', answers.length > 0);
    const key = JSON.stringify(answers);
    if (key === reviewKey) return;
    reviewKey = key;
    byId('quiz-review-count').textContent = `${answers.filter(a => a.status === 'correct').length} / ${answers.length} correct`;
    const last = answers[answers.length - 1];
    byId('quiz-latest-result').textContent = last ? `Question ${last.number}: ${labels[last.status] || 'Answer locked'}.` : '';
    byId('quiz-latest-result').className = `quiz-latest-result status-${last?.status || 'unanswered'}`;
    const fragment = document.createDocumentFragment();
    answers.forEach(answer => {
      const item = document.createElement('li'); item.className = `quiz-review-item answer-${answer.status}`;
      const detail = document.createElement('details');
      const summary = document.createElement('summary');
      const number = document.createElement('span'); number.className = 'quiz-review-number'; number.textContent = String(answer.number).padStart(2, '0');
      const title = document.createElement('span'); title.className = 'quiz-review-title'; title.textContent = answer.prompt;
      const result = document.createElement('span'); result.className = `quiz-review-verdict status-${answer.status}`; result.textContent = labels[answer.status] || 'Locked';
      summary.append(number, title, result);
      const selected = document.createElement('p'); selected.className = 'quiz-review-selected'; selected.textContent = answer.selected_answer == null ? 'No answer was submitted before this question closed.' : `Your answer: ${answer.selected_answer}`;
      detail.append(summary, selected); item.append(detail); fragment.append(item);
    });
    byId('quiz-answer-review').replaceChildren(fragment);
  }

  function renderFeedback() {
    const eligible = snapshot.started && (snapshot.completed || snapshot.status === 'CLOSED');
    const feedbackSubmitted = !!snapshot.feedback?.submitted;
    const canViewResults = feedbackSubmitted || (snapshot.status === 'CLOSED' && !snapshot.started);
    show('quiz-results-link', canViewResults);
    show('quiz-feedback-results', feedbackSubmitted);
    const finishAction = byId('quiz-finish-action');
    finishAction.href = canViewResults ? '/result' : '#quiz-feedback';
    finishAction.textContent = canViewResults ? 'View debugging results →' : 'Feedback form →';
    show('quiz-feedback', eligible);
    if (!eligible) return;
    const questions = snapshot.feedback_questions || [];
    const key = JSON.stringify(questions);
    if (key !== feedbackKey) {
      feedbackKey = key;
      const fragment = document.createDocumentFragment();
      questions.forEach((question, index) => {
        const fieldset = document.createElement('fieldset'); fieldset.className = 'feedback-rating';
        const legend = document.createElement('legend'); legend.textContent = `${index + 1}. ${question.prompt}`;
        const scale = document.createElement('div'); scale.className = 'feedback-scale';
        for (let rating = 1; rating <= 5; rating++) {
          const label = document.createElement('label');
          const radio = document.createElement('input'); radio.type = 'radio'; radio.name = question.id; radio.value = rating; radio.required = true; radio.setAttribute('aria-label', `${rating} out of 5${rating === 1 ? ', poor' : rating === 5 ? ', excellent' : ''}`);
          const number = document.createElement('span'); number.textContent = rating;
          label.append(radio, number); scale.append(label);
        }
        fieldset.append(legend, scale); fragment.append(fieldset);
      });
      byId('quiz-feedback-questions').replaceChildren(fragment);
    }
    const saved = snapshot.feedback;
    if (saved?.submitted) {
      Object.entries(saved.ratings || {}).forEach(([id, rating]) => {
        const input = Array.from(byId('quiz-feedback-form').elements).find(el => el.name === id && el.value === String(rating));
        if (input) input.checked = true;
      });
      byId('quiz-feedback-note').value = saved.note || '';
      byId('quiz-feedback-status').textContent = '✓ Feedback received. Thank you for taking part.';
      byId('quiz-feedback-status').className = 'status-correct';
      byId('quiz-feedback-submit').textContent = '✓ Feedback sent';
    }
    byId('quiz-feedback-length').textContent = byId('quiz-feedback-note').value.length;
    feedbackControls();
  }

  function render(data) {
    snapshot = data.quiz; receivedAt = performance.now();
    const q = snapshot.current_question;
    show('quiz-error', false);
    show('quiz-intro', !snapshot.started && snapshot.status !== 'CLOSED');
    show('quiz-play', !!q && !snapshot.completed);
    show('quiz-finish', snapshot.completed || snapshot.status === 'CLOSED');
    byId('quiz-start-status').textContent = snapshot.status === 'OPEN' ? 'The quiz is open. Start when your team is ready.' : 'Waiting for organizers to open the quiz.';
    byId('quiz-final-score').textContent = snapshot.quiz_score;
    byId('quiz-final-total').textContent = snapshot.quiz_total;
    if (snapshot.status === 'CLOSED' && !snapshot.started) {
      byId('quiz-finish-heading').textContent = 'The quiz has closed.';
      byId('quiz-finish-description').textContent = 'Your debugging result is unchanged.';
    }
    if (q) {
      byId('quiz-progress-label').textContent = `QUESTION ${String(q.number).padStart(2, '0')} / ${snapshot.quiz_total}`;
      byId('quiz-progress').value = snapshot.answered_count; byId('quiz-progress').max = snapshot.quiz_total;
      if (q.id !== currentID) {
        currentID = q.id; byId('quiz-question').textContent = q.prompt;
        const options = byId('quiz-options'); options.replaceChildren();
        q.options.forEach((text, i) => {
          const label = document.createElement('label'); label.className = 'quiz-option';
          const radio = document.createElement('input'); radio.type = 'radio'; radio.name = 'answer'; radio.value = i;
          const letter = document.createElement('span'); letter.className = 'quiz-option-letter'; letter.textContent = String.fromCharCode(65 + i);
          const content = document.createElement('span'); content.className = 'quiz-option-text'; content.textContent = text;
          label.append(radio, letter, content); options.append(label);
        });
        byId('quiz-answer-hint').textContent = 'Choose carefully. Your answer is final once locked.';
        byId('quiz-question').focus({ preventScroll: true });
      }
    }
    renderReview(snapshot.answers); renderFeedback(); controls(); tick();
  }

  function controls() {
    byId('quiz-lock').disabled = busy || !document.querySelector('#quiz-options input:checked') || !snapshot?.current_question;
    byId('quiz-start').disabled = busy || snapshot?.status !== 'OPEN';
    byId('quiz-lock').classList.toggle('loading', busy);
    byId('quiz-start').classList.toggle('loading', busy);
    byId('quiz-options').disabled = busy || !snapshot?.current_question;
  }
  function feedbackControls() {
    const disabled = feedbackBusy || !!snapshot?.feedback?.submitted;
    byId('quiz-feedback-form').querySelectorAll('input, textarea, button').forEach(el => { el.disabled = disabled; });
    byId('quiz-feedback-submit').classList.toggle('loading', feedbackBusy);
  }
  function tick() {
    if (!snapshot) return;
    const delta = Math.floor((performance.now() - receivedAt) / 1000);
    byId('quiz-question-clock').textContent = EventClock.format(Math.max(0, snapshot.question_remaining_seconds - delta));
    byId('quiz-session-clock').textContent = EventClock.format(Math.max(0, snapshot.remaining_seconds - delta));
    byId('quiz-question-clock').classList.toggle('timer-warning', snapshot.question_remaining_seconds - delta <= 10);
  }
  async function sync() {
    if (polling || busy || feedbackBusy) return;
    polling = true; const epoch = requestEpoch;
    try { const data = await page.request('/api/quiz/status'); if (epoch === requestEpoch) render(data); }
    catch (err) { byId('quiz-error').textContent = err.message; show('quiz-error', true); }
    finally { polling = false; }
  }
  async function action(url, body) {
    if (busy) return;
    busy = true; requestEpoch++; controls();
    try {
      render(await page.request(url, { method: 'POST', body }));
      const answer = snapshot.answers?.find(item => item.question_id === body.question_id);
      App.toast(answer ? `Answer locked. ${labels[answer.status] || ''}` : url.endsWith('answer') ? 'Answer locked.' : 'Quiz started.', answer?.status === 'incorrect' ? 'warning' : 'success');
    } catch (err) { byId('quiz-error').textContent = err.message; show('quiz-error', true); }
    finally { busy = false; controls(); }
  }
  async function sendFeedback(event) {
    event.preventDefault();
    if (feedbackBusy || snapshot?.feedback?.submitted || !byId('quiz-feedback-form').reportValidity()) return;
    const values = new FormData(byId('quiz-feedback-form'));
    const ratings = Object.fromEntries((snapshot.feedback_questions || []).map(q => [q.id, Number(values.get(q.id))]));
    feedbackBusy = true; requestEpoch++; feedbackControls();
    byId('quiz-feedback-status').textContent = 'Sending your feedback…';
    try {
      const data = await page.request('/api/quiz/feedback', { method: 'POST', body: { ratings, note: byId('quiz-feedback-note').value.trim() } });
      snapshot.feedback = data.feedback; renderFeedback();
      App.toast('Feedback received. Thank you!', 'success');
    } catch (err) { byId('quiz-feedback-status').textContent = err.message; byId('quiz-feedback-status').className = 'status-incorrect'; }
    finally { feedbackBusy = false; feedbackControls(); }
  }
  byId('quiz-start').addEventListener('click', () => action('/api/quiz/start', {}));
  byId('quiz-options').addEventListener('change', () => { byId('quiz-answer-hint').textContent = 'Ready to lock your final answer.'; controls(); });
  byId('quiz-answer-form').addEventListener('submit', event => {
    event.preventDefault();
    const radio = document.querySelector('#quiz-options input:checked');
    if (radio && currentID) action('/api/quiz/answer', { question_id: currentID, answer_index: Number(radio.value) });
  });
  byId('quiz-finish-action').addEventListener('click', event => {
    if (event.currentTarget.getAttribute('href') !== '#quiz-feedback') return;
    event.preventDefault();
    byId('quiz-feedback').scrollIntoView({behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth', block: 'start'});
    byId('quiz-feedback-heading').focus({preventScroll: true});
  });
  byId('quiz-feedback-form').addEventListener('submit', sendFeedback);
  byId('quiz-feedback-note').addEventListener('input', () => { byId('quiz-feedback-length').textContent = byId('quiz-feedback-note').value.length; });
  sync();
  setInterval(() => { if (!document.hidden) sync(); }, 3000);
  setInterval(tick, 250);
})();
