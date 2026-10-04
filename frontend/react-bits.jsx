import React, { useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { flushSync } from 'react-dom';
import PatternWaves from './components/PatternWaves';
import RubberSegment from './components/RubberSegment';
import ThoughtLine from './components/ThoughtLine';
import BorderGlow from './components/BorderGlow';
import TechText from './components/TechText';
import ElectricLogo from './components/ElectricLogo';
import DotGrid from './components/DotGrid';
import DemoMotion from './components/DemoMotion';

function HeroWaves() {
  const [paused, setPaused] = useState(document.documentElement.dataset.demoPaused === 'true');
  useEffect(() => {
    const observer = new MutationObserver(() => setPaused(document.documentElement.dataset.demoPaused === 'true'));
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ['data-demo-paused'] });
    return () => observer.disconnect();
  }, []);
  return <PatternWaves preset="silk" color="#f43b46" backgroundColor="transparent" spacing={12} markSize={0.9} depth={0.85} opacity={0.7} speed={0.22} cursorSize={65} cursorStrength={0.5} paused={paused} />;
}
function AccessTabs({ initial }) {
  const [value, setValue] = useState(initial);
  const select = next => {
    setValue(next);
    for (const name of ['register','login']) document.getElementById(`${name}-panel`).hidden = name !== next;
  };
  return <RubberSegment items={[
    {value:'register',label:'Register team',id:'register-tab',controls:'register-panel'},
    {value:'login',label:'Team login',id:'login-tab',controls:'login-panel'},
  ]} value={value} onChange={select} role="tablist" aria-label="Team access" trackColor="#100b10" thumbColor="#961e37" textColor="#ad939f" activeTextColor="#fff1f5" size="lg" radius={7} stretch={65} squash={2} glide={45} />;
}

const roots = new Map();
function mount(host, component) {
  const root = createRoot(host);
  roots.set(host, root);
  root.render(component);
  return root;
}
function mountPage() {
  window.App.demoEnhanced = false;
const waves = document.querySelector('[data-pattern-waves]');
if (waves) mount(waves, <HeroWaves />);
for (const host of document.querySelectorAll('[data-tech-text]')) {
  mount(host, <TechText text={host.dataset.techText} fontFamily="Arial, Helvetica, sans-serif" fontWeight={900} fontSize={150} accentColor="#ff394e" color="#f5f2f2" reach={160} speed={0.75} specks={8} />);
  host.closest('h1')?.setAttribute('data-tech-ready', '');
}
for (const host of document.querySelectorAll('[data-electric-logo]')) {
  mount(host, <ElectricLogo src="/static/exit-mark.svg" label="Exit code zero" color="#ffb2bd" glowColor="#ff203d" scale={0.66} intensity={1.25} glow={1.4} thickness={1.3} strands={3} speed={1.15} crackle={0.85} flicker={0.12} arcs={0.7} />);
}
// One viewport-sized field follows every page without growing with document height.
const siteDots = document.querySelector('[data-site-dot-grid]');
if (siteDots && !roots.has(siteDots)) {
  mount(siteDots, <DotGrid eventTarget={document} dotSize={2.6} gap={24} baseColor="#54232d" activeColor="#ff4055" proximity={135} speedTrigger={70} shockRadius={170} shockStrength={2.8} resistance={1000} returnDuration={1.1} maxDots={1600} maxDpr={1.5} />);
}
// Each surface uses the event palette; the editor keeps its original geometry.
const glowCards = new Set(document.querySelectorAll('[data-border-glow], .auth-panel, .lobby-status, .team-card, .lobby-facts, .standings-card, .result-receipt, .quiz-intro, .quiz-play, .quiz-finish, .admin-stat-card, .admin-page .card'));
for (const card of glowCards) {
  card.setAttribute('data-border-glow','');
  const host = document.createElement('div');
  host.dataset.reactBorderRoot = '';
  host.setAttribute('aria-hidden', 'true');
  card.append(host);
  mount(host, <BorderGlow eventTarget={card} strong glowColor="352 100 67" backgroundColor="transparent" borderRadius={6} glowIntensity={1.25} edgeSensitivity={65} coneSpread={70} colors={['#ff334d','#ff8594','#991a32']} fillOpacity={0.05} />);
}
const demo = document.querySelector('.editor-demo');
if (demo) {
  window.App.demoEnhanced = true;
  const host = document.createElement('div');
  host.dataset.demoMotion = '';
  host.setAttribute('aria-hidden','true');
  demo.append(host);
  mount(host, <DemoMotion target={demo} />);
}
const tabs = document.querySelector('[data-react-tabs]');
if (tabs) {
  const initial = tabs.querySelector('[aria-selected=true]')?.id === 'login-tab' ? 'login' : 'register';
  tabs.removeAttribute('role');
  tabs.removeAttribute('aria-label');
  tabs.dataset.reactMounted = 'true';
  flushSync(() => mount(tabs, <AccessTabs initial={initial} />));
}

}
window.App.mountReactBits = mountPage;
window.App.unmountReactBits = () => {
  for (const [host, root] of roots) {
    if (host.matches('[data-site-dot-grid]')) continue;
    root.unmount(); roots.delete(host);
  }
};
mountPage();

// Existing arena code drives the React trace using the actual request lifecycle.
const traces = new WeakMap();
window.App.activity = (host, label) => {
  if (!host) return {finish() {}};
  let root = traces.get(host);
  if (!root) { root = createRoot(host); traces.set(host, root); roots.set(host, root); }
  host.hidden = false;
  host.dataset.working = 'true';
  delete host.dataset.success;
  const start = performance.now();
  const steps = ['Required fields checked on this device.', 'Request sent. Waiting for the scoring server.'];
  flushSync(() => root.render(<ThoughtLine key={start} label={label} glyph="dot" glyphColor="#ff5d78" fontSize={11} steps={steps} working />));
  return { finish(success, message) {
    if (!roots.has(host)) return;
    host.dataset.working = 'false';
    host.dataset.success = String(success);
    root.render(<ThoughtLine key={start} label={label} doneLabel={message} glyph="dot" glyphColor={success ? '#71c7a3' : '#e5b96d'} fontSize={11} steps={[...steps, success ? 'Server confirmed the submission was stored.' : 'Request failed. Your input is preserved; retry when connected.']} working={false} elapsed={(performance.now()-start)/1000} collapseOnSettle={success} />);
  }};
};
document.documentElement.dataset.reactBits = 'ready';
