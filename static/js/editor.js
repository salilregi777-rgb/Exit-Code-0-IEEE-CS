/* Lightweight, escaped syntax tokens: source text never becomes executable HTML. */
(() => {
  const page = App.page;
  const {setInterval, setTimeout} = page;
  'use strict';
  const keywords = new Set('abstract assert boolean bool break byte case catch char class const continue def default del do double elif else enum except extends false False final finally float for from if import in int interface is lambda long native new None null package pass private protected public raise return short signed sizeof static string String struct super switch synchronized this throw throws true True try typedef unsigned using var virtual void volatile while with yield print printf include'.split(' '));
  const tokenPattern = /(\/\/.*$|#.*$|"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|\b\d+(?:\.\d+)?\b|\b[A-Za-z_]\w*\b|[+*=!<>%&|]+)/g;
  function highlight(element) {
    const text = element.textContent;
    const fragment = document.createDocumentFragment();
    let last = 0;
    for (const match of text.matchAll(tokenPattern)) {
      fragment.append(document.createTextNode(text.slice(last, match.index)));
      const token = match[0];
      let kind = '';
      if (token.startsWith('//') || token.startsWith('#')) kind = 'comment';
      else if (/^["']/.test(token)) kind = 'string';
      else if (/^\d/.test(token)) kind = 'number';
      else if (keywords.has(token)) kind = 'keyword';
      else if (/^[+*=!<>%&|]/.test(token)) kind = 'operator';
      else if (/^\s*\(/.test(text.slice(match.index + token.length))) kind = 'call';
      if (kind) { const span = document.createElement('span'); span.className = `syntax-${kind}`; span.textContent = token; fragment.append(span); }
      else fragment.append(document.createTextNode(token));
      last = match.index + token.length;
    }
    fragment.append(document.createTextNode(text.slice(last))); element.replaceChildren(fragment);
  }
  function selectLine(number, updateInput = true) {
    const viewer = document.getElementById('code-viewer-container');
    if (!viewer) return;
    viewer.querySelectorAll('.code-line').forEach(line => {
      const selected = Number(line.dataset.line) === Number(number);
      line.classList.toggle('selected-line', selected);
      line.querySelector('button').setAttribute('aria-pressed', String(selected));
    });
    document.getElementById('line-position').textContent = `Ln ${number || '—'}`;
    if (updateInput) { const input = document.getElementById('error_location'); input.value = number; input.dispatchEvent(new Event('input', { bubbles: true })); }
  }
  function render(code) {
    const viewer = document.getElementById('code-viewer-container');
    const fragment = document.createDocumentFragment();
    String(code).split('\n').forEach((text, index) => {
      const line = document.createElement('div'); line.className = 'code-line'; line.dataset.line = index + 1;
      const button = document.createElement('button'); button.type = 'button'; button.className = 'line-number'; button.textContent = String(index + 1).padStart(2, '0'); button.setAttribute('aria-label', `Select line ${index + 1}`);
      const content = document.createElement('code'); content.className = 'line-content'; content.textContent = text; highlight(content);
      line.append(button, content); fragment.append(line);
    });
    viewer.replaceChildren(fragment); viewer.scrollTop = 0;
    document.getElementById('error_location').max = String(code).split('\n').length;
  }
  window.CodeEditor = { render, selectLine };
  function init() {
    const viewer = document.getElementById('code-viewer-container');
    if (!viewer) return;
    viewer.querySelectorAll('.line-content').forEach(highlight);
    document.getElementById('error_location').max = viewer.querySelectorAll('.code-line').length;
    viewer.addEventListener('click', e => { const button = e.target.closest('.line-number'); if (button) selectLine(button.closest('.code-line').dataset.line); });
    ['contextmenu', 'copy', 'cut', 'dragstart', 'selectstart'].forEach(type => viewer.addEventListener(type, e => e.preventDefault()));
    viewer.addEventListener('keydown', e => { if ((e.ctrlKey || e.metaKey) && ['c', 'x', 'a', 's'].includes(e.key.toLowerCase())) { e.preventDefault(); App.toast('Source copying is restricted in competition mode.', 'warning'); } });
    document.getElementById('error_location').addEventListener('input', e => selectLine(e.target.value, false));
  }
  if (document.readyState === 'loading') page.listen(document, 'DOMContentLoaded', init); else init();
})();
