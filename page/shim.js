// Model Desk local runtime. A small stand-in for the hosted dashboard runtime the page was
// written against: it loads the datasets inlined in this file, runs the page's calculations,
// and calls its draw functions. No network access is needed or used.
(() => {
  const loaders = {}, calcs = {}, draws = {}, listeners = [], cache = {};
  let params = {}, started = false;
  try { params = JSON.parse(localStorage.getItem('model-desk-params') || '{}') || {}; } catch (e) { params = {}; }
  const EMPTY = { status: 'empty', data: [], columns: [], meta: [] };

  const inlined = (id) => {
    const el = document.getElementById('ds-' + id);
    if (!el) return null;
    try { return JSON.parse(el.textContent); } catch (e) { return undefined; }
  };

  const data = (id) => {
    if (cache[id]) return cache[id];
    let out = EMPTY;
    if (loaders[id]) {
      const answer = inlined(id);
      if (answer === undefined) out = { ...EMPTY, status: 'error' };
      else if (answer) {
        try { out = { status: 'ok', columns: [], meta: [], ...loaders[id].fn({ answer }) }; }
        catch (e) { console.error('loader', id, e); out = { ...EMPTY, status: 'error' }; }
      }
    } else if (calcs[id]) {
      const ins = calcs[id].inputs.map(data);
      if (ins.some((g) => g.status === 'error')) out = { ...EMPTY, status: 'error' };
      else if (ins.every((g) => g.status === 'ok')) {
        try { const rows = calcs[id].fn(...ins.map((g) => g.data)) || []; out = { status: 'ok', data: rows, columns: Object.keys(rows[0] || {}), meta: [] }; }
        catch (e) { console.error('calc', id, e); out = { ...EMPTY, status: 'error' }; }
      }
    }
    cache[id] = out;
    return out;
  };

  const drawOne = (el) => {
    const fn = draws[el.dataset.draw];
    if (!fn) return;
    try { fn(el); } catch (e) { console.error('draw', el.dataset.draw, e); }
  };

  const render = (name) => {
    if (!started) return;
    if (!name) {
      listeners.forEach((cb) => { try { cb(); } catch (e) { console.error('onData', e); } });
      document.querySelectorAll('[data-source]').forEach((el) => {
        const states = el.dataset.source.split(/\s+/).filter(Boolean).map((id) => data(id).status);
        el.dataset.dashSourceState = states.includes('error') ? 'error' : states.every((s) => s === 'ok') ? 'ok' : 'empty';
      });
    }
    document.querySelectorAll(name ? '[data-draw="' + name + '"]' : '[data-draw]').forEach(drawOne);
  };

  const dark = () => window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches;
  const palette = () => (dark()
    ? ['#7aa2f7', '#4fc3a1', '#c792ea', '#f0a35e', '#e06c75', '#56b6c2', '#d7ba7d', '#9aa0a6']
    : ['#2f5fd0', '#0e9f6e', '#8b5cf6', '#c2410c', '#be123c', '#0e7490', '#a16207', '#6b7280']);

  window.dash = {
    loader: (id, spec) => { loaders[id] = spec; },
    calc: (id, spec) => { calcs[id] = spec; },
    draw: (name, fn) => { if (fn) draws[name] = fn; else render(name); },
    data,
    onData: (cb) => { listeners.push(cb); },
    params: () => ({ ...params }),
    setParams: (p) => {
      params = { ...params, ...p };
      try { localStorage.setItem('model-desk-params', JSON.stringify(params)); } catch (e) { /* private window */ }
      render();
    },
    get colors() { return palette(); },
  };

  const start = () => { started = true; render(); };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start); else setTimeout(start, 0);
  let t = null;
  window.addEventListener('resize', () => { clearTimeout(t); t = setTimeout(() => render(), 150); });
  if (window.matchMedia) window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => render());
})();
