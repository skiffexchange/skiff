// Source race (design/v2/SPEC.md §4): every source asked for this quote answers in turn. Rows = real answers only:
// SSE `source` events when the engine sends them; until then the real `route` / `baseline` / `done` events of the same stream.
// Layout never jumps: a fixed strip of source marks (one slot per source), a fixed number of row slots sorted by amount (FLIP, transform),
// one summary line. Best = the route the swap will use (MOST among simulated; the honest board rules stay).
import { t, has, esc, amt, nf, RM, venueName, vlogo, INTENT_SVG, api } from './core.js';

// the quiet bridge line «20+ liquidity sources across these routes» (marketer 16.11) — from /api/reach, only when measured
let SRC = null, REST = null;
const RESTW = new Set();
/** the empty-state line with the measured source count (the counters' rule: 20+ only when ≥ 20) */
export const emptyLine = () => (has('swap.empty.enter_amount') && SRC ? t('swap.empty.enter_amount', { sources: SRC >= 20 ? Math.floor(SRC / 10) * 10 : SRC }) : '\u00a0');
export const onSources = (f) => { if (SRC) f(); else RESTW.add(f); };   // races at rest waiting for the source list
// P2: at rest the card names three real sources that will answer (one multi-pool, one DEX, one market maker) — never grey bars
const restOf = (r) => { const pick = []; const add = (l, kind) => { const x = (l || []).find((y) => y && y.logo && !/other\.svg/.test(y.logo) && !pick.some((p) => p.logo === y.logo)); if (x) pick.push({ ...x, kind }); };
  add(r.aggregators && r.aggregators.list, 'aggregator'); add(r.dexes && r.dexes.list, 'dex'); add(r.makers && r.makers.list, 'maker'); add(r.aggregators && r.aggregators.list, 'aggregator'); return pick; };
// P6: the sources that will answer, drawn where the race will run (the card at rest) — real marks from /api/reach, one per logo, no numbers
const srcsOf = (r) => { const out = []; const add = (l, kind) => (l || []).forEach((y) => { if (y && y.logo && !/other\.svg/.test(y.logo) && !out.some((p) => p.logo === y.logo)) out.push({ name: y.name, logo: y.logo, kind }); });
  add(r.aggregators && r.aggregators.list, 'aggregator'); add(r.dexes && r.dexes.list, 'dex'); add(r.makers && r.makers.list, 'maker'); return out; };
let SRCS = null;
api('/api/reach', { cache: 600000 }).then((r) => { if (r && !r.stale && r.sources && r.sources.count) { SRC = r.sources.count; REST = restOf(r); SRCS = srcsOf(r); RESTW.forEach((f) => f()); RESTW.clear(); } }).catch(() => {});

const KIND = { aggregator: 'swap.race.kind.aggregator', dex: 'swap.race.kind.dex', maker: 'swap.race.kind.maker', pool: 'swap.race.kind.pool', bridge: 'swap.race.kind.bridge' };
const mark = (r) => (r.solver ? `<span class="intico">${INTENT_SVG}</span>` : vlogo({ logo: r.logo, name: r.name, kind: r.vkind, displayKey: r.key }, 'vlogo'));

/** opts: {rows: number of row slots, onPick(routeId)} */
export function race(el, opts = {}) {
  const K = opts.rows || 5, KMAX = opts.rows && opts.rows <= 3 ? opts.rows : 7;   // the stage panel has one fixed height 
  el.classList.add('race');
  let R = null;   // {t0, n, ticker, dec, src: Map(key → row), order:[], done, best, sel}
  const head = document.createElement('div'); head.className = 'rh';
  const strip = document.createElement('div'); strip.className = 'rs'; strip.setAttribute('aria-hidden', 'true');
  const bar = document.createElement('div'); bar.className = 'rbar'; bar.innerHTML = '<i></i>';
  const list = document.createElement('div'); list.className = 'rl'; list.setAttribute('role', 'list');
  const foot = document.createElement('div'); foot.className = 'rf';
  const live = document.createElement('p'); live.className = 'sr'; live.setAttribute('aria-live', 'polite');
  el.replaceChildren(head, bar, strip, list, foot, live);
  list.style.setProperty('--rows', K);

  function reset({ n, ticker, cross }) {
    R = { t0: performance.now(), n: n || 0, ticker, cross: !!cross, src: new Map(), done: false, best: null, sel: null, err: null };
    el.classList.remove('done', 'err', 'idle'); el.classList.add('on');
    paint();
  }
  /** add or update one source row */
  function put(r) {
    const old = R.src.get(r.key);
    if (!old) r.ms = r.ms != null ? r.ms : Math.round(performance.now() - R.t0);
    R.src.set(r.key, { ...(old || {}), ...r });
    if (R.src.size > R.n) R.n = R.src.size;
    paint();
  }
  /** SSE `source` event (engine contract) */
  function source(ev) {
    const solver = ev.venueKind === 'solver' || ev.key === 'intent';
    if (ev.state === 'unavailable') ev = { ...ev, state: ev.reason === 'timeout' ? 'late' : 'busy' };
    if (ev.state === 'no_route' && /429|rate|busy/i.test(ev.code || '')) ev = { ...ev, state: 'busy' };
    else if (ev.state === 'no_route' && /timeout|late/i.test(ev.code || '')) ev = { ...ev, state: 'late' };
    const base = ev.key === 'baseline', direct = ev.key === 'direct';
    const sub = base ? [ev.name !== 'One pool' ? ev.name : null, ev.pair].filter(Boolean).join(' · ') : direct ? [ev.pair, t('swap.race.kind.direct')].filter(Boolean).join(' · ') : null;
    // one row per route: key = routeId when there is one (audit v4 works); a late «no route» without a routeId never wipes a priced row
    const key = ev.routeId || ev.key;
    if (!ev.routeId && ev.state === 'no_route' && [...R.src.values()].some((r) => r.vkey === ev.key && r.out)) return;
    if (ev.routeId && R.src.has(ev.key) && !R.src.get(ev.key).id) R.src.delete(ev.key);
    put({ key, vkey: ev.key, id: ev.routeId || null, name: base ? t('board.pool.name') : solver ? t('board.solver.name') : ev.name, sub: sub || undefined, logo: ev.logo, vkind: solver ? 'solver' : ev.venueKind || ev.kind, kind: base ? 'pool' : ev.kind, state: ev.state, out: ev.out || (R.src.get(ev.key) || {}).out || null, min: ev.min || (R.src.get(key) || {}).min || null, ms: ev.ms, solver, ref: base, cached: !!ev.cached });
  }
  /** fallback: a `route` event of the current stream */
  function route(rt, group) {
    const v = rt.venue || {}, solver = rt.status === 'intent' || v.kind === 'solver' || v.kind === 'own-router-tool';
    const out = rt.status === 'ok' ? rt.out.simulated : rt.status === 'intent' ? rt.out && rt.out.min : rt.out && rt.out.quoted;
    const state = rt.status === 'ok' ? 'simulated' : rt.status === 'reverted' ? 'reverted' : 'quoted';
    put({ key: 'r:' + (rt.id || v.displayKey), vkey: v.displayKey, id: rt.id, name: venueName(v), logo: v.logo, vkind: v.kind, kind: v.kind === 'dex' || rt.kind === 'direct' ? 'pool' : v.kind === 'aggregator' ? 'aggregator' : null, state, out: rt.status === 'intent' ? (rt.out && rt.out.quoted) || out : out, min: rt.out && rt.out.min, solver, group });
  }
  function baseline(b) {
    if (!b || b.available === false || !b.out) return;
    put({ key: 'base', name: t('board.pool.name'), sub: [b.name, b.pair].filter(Boolean).join(' · '), logo: b.logo, vkind: 'dex', kind: 'pool', state: 'simulated', out: b.out, ref: true });
  }
  /** final answer: best route, no-route list, total time */
  function done(m, chosenId) {
    if (!R) return;
    const seen = (v) => [...R.src.values()].some((r) => r.name === venueName(v) || r.vkey === v.displayKey || r.key === v.displayKey || r.key === 'r:' + v.displayKey);
    (m.noRoute || []).forEach((u) => { const v = u.venue; const k = 'r:' + v.displayKey; if (!seen(v)) put({ key: k, vkey: v.displayKey, name: venueName(v), logo: v.logo, vkind: v.kind, state: 'no_route', out: null }); });
    (m.na || []).forEach((u) => { const v = u.venue; if (![...R.src.values()].some((r) => r.name === venueName(v))) put({ key: 'na:' + v.displayKey, name: venueName(v), logo: v.logo, vkind: v.kind, state: 'reverted', out: null }); });
    const mins = new Map([...(m.solver || []), ...(m.quoted || [])].filter((x) => x.id && x.min).map((x) => [x.id, x.min]));
    for (const r of R.src.values()) if (r.id && mins.has(r.id) && (r.solver || r.state === 'quoted')) r.min = mins.get(r.id);
    R.done = true; R.sec = (performance.now() - R.t0) / 1000; R.best = chosenId || null; R.n = Math.max(R.n, R.src.size);
    el.classList.add('done');
    paint();
    const b = bestRow();
    const pk0 = pickedRow();
    if (pk0 && b) live.textContent = t('swap.race.aria_picked', { venue: pk0.name, amount: pk0.out ? amt(pk0.out.raw, pk0.out.decimals, 4) : '', ticker: R.ticker || '', best: b.name });
    else if (b && !simN()) live.textContent = `${nWord('swap.race.done_none', withAmt())}: ${b.name}, ${unchecked(b) && b.min ? '≥ ' + amt(b.min.raw, b.min.decimals, 4) : b.out ? amt(b.out.raw, b.out.decimals, 4) : ''} ${R.ticker || ''}`;
    else if (b) live.textContent = has('swap.race.aria_answered') ? t('swap.race.aria_answered', { n: answered().length, total: Math.max(R.n, answered().length), venue: b.name, amount: b.out ? amt(b.out.raw, b.out.decimals, 4) : '', ticker: R.ticker || '' }) : t(nAns() === 1 ? 'swap.race.aria_done_one' : 'swap.race.aria_done', { n: nAns(), venue: b.name, amount: b.out ? amt(b.out.raw, b.out.decimals, 4) : '', ticker: R.ticker || '' });
  }
  function hint(n) { if (R && n > R.n) { R.n = n; paint(); } }
  /** the card says what went wrong; the race only stops (no skeletons, no second message) */
  function error() { if (!R) return; R.err = true; el.classList.add('err'); paint(); }
  function idle() {
    R = null; el.classList.remove('on', 'done', 'err'); el.classList.add('idle'); strip.innerHTML = ''; foot.innerHTML = ''; bar.firstChild.style.transform = 'scaleX(0)';
    // in the card (landing): the race keeps its place at rest — the sources line and its row slots, quiet (no shimmer)
    if (K <= 3) {
      // at amount 0 the card shows one calm line («Enter an amount to compare N+ sources»), no rows, no skeletons; the place stays
      head.innerHTML = `<p class="rempty">${esc(emptyLine())}</p>`;
      list.innerHTML = SRCS && SRCS.length ? `<div class="rsrcs" role="list">${SRCS.slice(0, 10).map((x) => `<span class="rsrc" role="listitem"><img src="${esc(x.logo)}" alt="" loading="lazy"><b>${esc(x.name)}</b><small>${esc(t(KIND[x.kind]))}</small></span>`).join('')}</div>` : '';
      if (!SRC) RESTW.add(() => { if (!R && el.classList.contains('idle')) idle(); });
    } else { head.innerHTML = ''; list.innerHTML = ''; }
  }
  function cached() { if (R) { R.cached = true; paint(); } }
  function select(id) { if (!R) return; R.sel = id; paint(); }

  const bestRow = () => { if (!R || !R.best) return null; return [...R.src.values()].find((r) => r.id === R.best) || null; };
  const pickedRow = () => { if (!R || !R.sel || R.sel === R.best) return null; return [...R.src.values()].find((r) => r.id === R.sel) || null; };
  const val = (r) => (r.out ? Number(BigInt(r.out.raw)) / 10 ** r.out.decimals : -1);
  // «One pool» is the reference for «+$ vs one pool», not a route: never in the ranking (audit v4)
  const routes = () => [...R.src.values()].filter((r) => !r.ref);
  const answered = () => routes().filter((r) => r.state !== 'asked');
  /** best first, always; then executable by amount; quoted-only; «would fail» last and without an amount */
  function ranked() {
    const b = R.done ? bestRow() : null;
    const rows = routes().filter((r) => !/^(no_route|asked|busy|late)$/.test(r.state));
    const rank = (r) => (r === b ? -1 : r.state === 'reverted' ? 3 : r.solver ? 2 : unchecked(r) ? 1 : 0);
    return rows.sort((x, y) => rank(x) - rank(y) || val(y) - val(x));
  }
  // a row nobody simulated on this block: a solver, or a venue that only quoted (its number is the venue's word, not our check)
  const unchecked = (r) => r.solver || r.state === 'quoted';
  // the number a row shows: its guaranteed minimum when it has one (unchecked rows), else its amount
  const shown = (r) => { const x = unchecked(r) && r.min && r.min.raw ? r.min : r.out; return x ? Number(BigInt(x.raw)) / 10 ** x.decimals : -1; };
  // N of «Best of N»: the checked routes; else the quoted ones; else the solvers — the same number on screen and for screen readers
  const simN = () => answered().filter((r) => r.out && r.state === 'simulated' && !r.solver).length;
  const withAmt = () => answered().filter((r) => r.out && r.state !== 'reverted').length;
  const nAns = () => answered().filter((r) => r.out && r.state === 'simulated' && !r.solver).length || answered().filter((r) => r.out && unchecked(r) && !r.solver).length || answered().filter((r) => r.out && r.solver).length;
  const nWord = (id, n, vars) => t(n === 1 && has(id + '_one') ? id + '_one' : id, { n, ...vars });
  /** paint: header, rows (fixed slots while asking, FLIP re-order), footer */
  function paint() {
    if (!R) return;
    // «Best of N»: N = routes that came back with an amount; «One pool», refusals and «would fail» are not routes to pick (works)
    const ans = nAns(), all = answered().length, n = Math.max(R.n, all);
    // P4: one count from the first answer to the last — «{n} of {total} sources answered»; «on one block» only for a same-chain quote
    // with two or more simulated routes, otherwise the «ranked by quote» note
    const took = R.done && R.sec >= 0.05 && !R.cached ? t('swap.race.done_sub_one', { s: nf(R.sec, 1, 1) }) : '';
    // P7: the note follows the facts — the best route simulated on our block (same chain, not a solver): «sources» when two or more
    // were simulated, «Simulated on our block.» when it is the only one; «ranked by quote» only when the best route was not simulated
    const bb = R.done && !R.err ? bestRow() : null;
    const bSim = !!bb && !R.cross && !bb.solver && bb.state === 'simulated';
    const oneBlock = bSim && simN() >= 2;
    const srcl = !R.done || R.err || !bb ? '' : oneBlock ? (SRC ? (SRC >= 20 ? t('swap.race.sources', { n: Math.floor(SRC / 10) * 10 }) : SRC === 1 ? t('swap.race.sources_exact_one') : t('swap.race.sources_exact', { n: SRC })) : t('swap.race.sources'))
      : bSim ? (has('swap.race.simulated_one') ? t('swap.race.simulated_one') : '')
      : (has('swap.race.ranked_quote') ? t('swap.race.ranked_quote') : '');
    const cnt = has('swap.race.answered') ? t('swap.race.answered', { n: all, total: n }) : t('swap.race.progress', { done: all, n });
    head.innerHTML = R.err ? '<b>&nbsp;</b><span></span>'
      : `<b>${n ? esc(cnt) : '&nbsp;'}</b><span>${esc(took)}</span>${srcl ? `<small class="srcl">${esc(srcl)}</small>` : ''}`;
    void ans;
    bar.firstChild.style.transform = `scaleX(${R.done || R.err ? (R.err ? 0 : 1) : n ? Math.min(1, all / n) : 0.06})`;
    strip.innerHTML = '';   // the rows are the sources; a strip of the same marks repeated them (audit v4 S17)
    const live = !R.done && !R.err;
    const rows = ranked().slice(0, live ? K : Math.max(K, Math.min(KMAX, ranked().length))), b = bestRow(), pk = pickedRow();
    const firstPos = new Map([...list.children].map((c) => [c.dataset.k, c.getBoundingClientRect().top]));
    const html = rows.map((r) => {
      const isB = b === r && R.done, isP = pk === r;
      const time = r.ms > 0 && !r.cached && r.state !== 'reverted' ? ' · ' + esc(t('swap.race.row.time', { ms: nf(r.ms, 0) })) : '';
      const st = isB ? `<em class="best">${esc(t('swap.race.state.best'))}</em>` : isP ? `<em class="best pick">${esc(t('swap.race.state.chosen'))}</em>` : `<em>${esc(t('swap.race.state.' + r.state))}${time}</em>`;
      const kw = r.kind && KIND[r.kind] && r.kind !== 'aggregator' ? t(KIND[r.kind]) : '';
      const kind = r.sub ? esc(r.sub) : kw && !r.name.toLowerCase().includes(kw.toLowerCase()) ? esc(kw) : '';   // never «Solver bridge · bridge»
      const dg = (x) => amt(x.raw, x.decimals, x.decimals > 6 ? 4 : 2);
      // «≥» only before a guaranteed minimum (works v5); the venue's own quote then goes small under the name; a bare quote is «≈»
      const useMin = unchecked(r) && r.min && r.min.raw && r.state !== 'reverted';
      const o = r.state === 'reverted' ? '—' : useMin ? `<span title="${esc(t('board.solver.out_tip'))}">≥ ${dg(r.min)}</span>` : r.out ? `${unchecked(r) ? '≈ ' : ''}${dg(r.out)}` : '—';
      const qsub = useMin && r.out ? esc(t('board.solver.sub', { amount: dg(r.out) })) : '';
      return `<div class="rr ${isB ? 'b' : ''} ${isP ? 'p' : ''} ${r.state === 'reverted' ? 'na' : ''} ${r.id && !r.solver && r.state === 'simulated' && opts.onPick ? 'pick' : ''} ${R.sel && R.sel === r.id ? 'sel' : ''}" role="listitem" data-k="${esc(r.key)}" data-id="${esc(r.id || '')}" aria-label="${esc(t('swap.race.aria_row', { venue: r.name, state: t('swap.race.state.' + (isB ? 'best' : r.state)), amount: r.out && r.state !== 'reverted' ? amt(r.out.raw, r.out.decimals, 4) : '', ticker: R.ticker || '' }))}">${mark(r)}<span class="nm"><b>${esc(r.name)}</b>${kind || qsub ? `<small>${[kind, qsub].filter(Boolean).join(' · ')}</small>` : '<small aria-hidden="true">\u00a0</small>'}</span>${st}<span class="v">${o}</span></div>`;
    }).join('') + (live ? Array.from({ length: Math.max(0, K - rows.length) }, () => '<div class="rr sk0"><span class="sk" style="width:28px;height:28px;border-radius:50%"></span><span class="sk" style="width:38%;height:12px"></span><span></span><span class="sk" style="width:64px;height:12px;margin-left:auto"></span></div>').join('') : '');
    list.innerHTML = html;
    if (R.done && !R.err) {
      const rowEls = [...list.children];
      const first = rows.findIndex((r) => unchecked(r) && !r.solver && r !== b);
      const firstS = rows.findIndex((r) => r.solver && r !== b && r.state !== 'reverted');
      const sep = (i, label, tip) => { if (i > 0 && rowEls[i]) { const g = document.createElement('div'); g.className = 'rgrp'; g.dataset.k = '__q'; g.title = tip; g.textContent = label; rowEls[i].before(g); } };
      if (first > 0 && rows.slice(0, first).some((r) => !unchecked(r))) sep(first, t('board.lane.quoted'), t('board.quoted.tip'));
      sep(firstS, t('board.lane.solver'), t('board.solver.tip'));
      list.querySelectorAll('.rr').forEach((c) => { const r = R.src.get(c.dataset.k); if (r && unchecked(r) && r !== b) c.classList.add('uq'); });
    }
    if (!RM) [...list.children].forEach((c) => {
      const k = c.dataset.k; if (!k || k === '__q') return;
      const was = firstPos.get(k), now = c.getBoundingClientRect().top;
      if (was == null && !document.hidden) c.animate([{ opacity: 0, transform: 'translateY(6px)' }, { opacity: 1, transform: 'none' }], { duration: 420, easing: 'cubic-bezier(.16,1,.3,1)' });
      else if (Math.abs(was - now) > 1) c.animate([{ transform: `translateY(${was - now}px)` }, { transform: 'none' }], { duration: 420, easing: 'cubic-bezier(.16,1,.3,1)' });
    });
    if (opts.onPick) list.querySelectorAll('.rr.pick').forEach((x) => x.addEventListener('click', () => opts.onPick(x.dataset.id)));
    // footer: best line, then the routes that had no route (names only — no logo cluster)
    const nr = answered().filter((r) => r.state === 'no_route');
    let f = '';
    if (R.err) f = '';
    else if (R.done && b && pk) f = `<p class="bl">${esc(t('swap.race.picked', { venue: pk.name, best: b.name, ticker: R.ticker || '' }))}</p>`;
    else if (R.done && b && !rows.some((r) => r !== b && r.state !== 'reverted' && shown(r) > shown(b))) f = `<p class="bl">${esc(t('swap.race.best', { venue: b.name, ticker: R.ticker || '' }))}</p>`;
    else if (R.done && !b) f = `<p class="e">${esc(t('swap.race.none'))}</p>`;
    if (nr.length && R.done && !R.err) f += `<p class="nr">${esc(t('swap.race.no_route_group', { list: nr.map((r) => r.name).join(', ') }))}</p>`;
    for (const [st, id] of [['busy', 'swap.race.busy_group'], ['late', 'swap.race.late_group']]) { const g = answered().filter((r) => r.state === st); if (g.length && R.done && !R.err) f += `<p class="nr">${esc(t(id, { list: g.map((r) => r.name).join(', ') }))}</p>`; }
    foot.innerHTML = f;
  }
  return { reset, hint, source, route, baseline, done, error, idle, select, cached, get active() { return !!R; } };
}
