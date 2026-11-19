// The swap window + its stage (design/v2/SPEC.md §5, §7) — shared by /swap and /stock/{T}. Idle: the biggest real buy of the token (a
// person's swap, /api/track userSwap) drawn as a live route scheme; typing: the source race; after Swap: the live scheme of your own swap.
// Phones: race and scheme live inside the card. Also the trade rows used below the stage on both pages.
import { t, has, esc, $, api, tokens, nf, hhmm, blk, ago, plural, LOCAL, QS, RM, RH_LOGO } from './core.js';
import { tk } from './board.js';
import { mountSwap } from './swap.js';
import { scheme, tradeNodes, trackNodes } from './scheme.js';

export const PHONE = matchMedia('(max-width: 979px)').matches;
/** the trade's dollar size as the scheme shows it: what the buyer paid (source node), else what landed (wallet), else the feed (audit v4) */
export const trackUsd = (x) => { const ns = (x.track && x.track.nodes) || []; const src = ns.find((n) => n.kind === 'source'), w = ns.find((n) => n.kind === 'wallet'); return (src && src.amount && src.amount.usd) || (w && w.amount && w.amount.usd) || x.usd; };
const okTrack = (x, tr) => { const w = (tr && (tr.nodes || []).find((n) => n.kind === 'wallet')) || {}; return tr && tr.done && tr.userSwap === true && w.amount && (w.amount.usd || 0) >= x.usd * 0.5; };
/** the biggest buy of a stock in 24 h that was a person's swap (bots' arbitrage skipped), with its /api/track answer */
/* P7: a finished swap's scheme never changes — /api/track answers for done transactions are kept in this browser for an hour, so moving
   between pages does not ask for the same trades again (the track limit stays for real visitors) */
export async function trackTx(tx) {
  const K = 'skiff.trk.' + tx;
  try { const c = JSON.parse(localStorage.getItem(K) || 'null'); if (c && Date.now() - c.at < 3600000) return c.r; } catch { /* none */ }
  const r = await api('/api/track?' + new URLSearchParams({ txHash: tx, fromChain: 4663, toChain: 4663 }), { cache: 3600000 });
  if (r && r.done) try { localStorage.setItem(K, JSON.stringify({ at: Date.now(), r })); } catch { /* full */ }
  return r;
}
export async function bigUserBuy(stock, onFirst) {
  const since = Date.now() / 1000 - 86400;
  const q = new URLSearchParams({ limit: 200, minUsd: 5000 }); if (stock) q.set('stock', stock);   // P5: one threshold for «big» — $5k, the freshest person's buy
  let d = null; try { d = await api('/api/trades?' + q, { cache: 60000 }); } catch { return null; }
  const buys = (d.trades || []).filter((y) => y.side === 'buy' && y.ts >= since && (y.pools || []).length).sort((a, b) => b.ts - a.ts).slice(0, stock ? 8 : 14);   // P7: any stock (app) looks a little further back for a person's buy · P2: the latest real buy, not the biggest of the day
  if (onFirst && buys.length) onFirst(buys[0]);   // P6: draw at once, confirm after
  for (const x of buys) {
    try { const tr = await trackTx(x.tx); if (okTrack(x, tr)) return { ...x, track: tr }; } catch { /* next */ }
  }
  return null;
}

/** mount the swap + stage. opts: {to} (ticker the swap buys) */
export function mountStage(opts = {}) {
  const P = { idle: $('#pIdle'), race: $('#pRace'), track: $('#pTrack') };
  const grid = document.querySelector('.app2');
  let mode = '', sw = null;
  function stageMode(m) {
    if (m === mode) return;
    const to = P[m]; mode = m; $('#stage').dataset.mode = m; grid.classList.toggle('trk', m === 'track');
    Object.entries(P).forEach(([k, el]) => { el.hidden = k !== m; });
    $('#stgT').textContent = m === 'track' ? t('swap.track.title') : m === 'idle' ? t('landing.live.title') : '';
    if (!RM && !document.hidden && to) to.animate([{ opacity: 0, transform: 'translateY(8px)' }, { opacity: 1, transform: 'none' }], { duration: 480, easing: 'cubic-bezier(.16,1,.3,1)' });
  }
  const idleSch = scheme($('#iSch'), { dir: 'auto', big: !PHONE });
  let idleFor = null, lastTx = null, drawnTx = null;   // drawnTx: a real trade is on the stage (not the skeleton)
  async function idle() {
    const cur = sw && sw.state.to;
    const to = cur ? (cur.custom ? cur.address : cur.symbol) : (opts.to || QS.get('to') || 'NVDA').toUpperCase();
    if (idleFor === to) { if (mode === 'idle' && idleSch.nodes.length) return; }
    idleFor = to;
    // prod-2 P1-2: never a bare heading while /api/trades + /api/track answer — the last trade seen here at once, else a quiet skeleton
    const KEY = 'skiff.big.' + (opts.to ? to : '*');
    if (mode === 'idle' && !idleSch.nodes.length) {
      let c0 = null; try { c0 = JSON.parse(localStorage.getItem(KEY) || 'null'); } catch { /* none */ }
      if (c0 && c0.track && Date.now() / 1000 - c0.ts < 86400 && !(cur && cur.custom)) await draw(c0);
      else { $('#iline').innerHTML = '<span class="sk" style="display:inline-block;width:62%;height:16px"></span>'; $('#istamp').innerHTML = '<span class="sk" style="display:inline-block;width:30%;height:12px"></span>'; $('#iSch').classList.add('ghost'); idleSch.set([{ kind: 'chain', state: 'off', logo: RH_LOGO, title: '' }, { kind: 'wallet', state: 'off', title: '' }], { finished: false, live: false }); }
    }
    // P7: no instant draw before /api/track answers — the engine marks trades into contracts userSwap:false, and only a confirmed
    // person's buy is drawn (a cached confirmed one is drawn at once above)
    let x = cur && cur.custom ? null : await bigUserBuy(to);   // a token not on our list has no stock trades — never the stock of the same symbol
    if (!x && !opts.to) x = await bigUserBuy(null);   // /swap only; a stock page never shows another ticker's trade (audit v4 S9)
    if (idleFor !== to) return;
    if (x && x.tx === lastTx && idleSch.nodes.length) return;   // P3: the same trade is not replayed on the refresh
    if (x) { lastTx = x.tx; try { localStorage.setItem(KEY, JSON.stringify(x)); } catch { /* full */ } }
    $('#stage').classList.toggle('notr', !x);   // P4/P5: a stock without a big buy — one compact line, no scheme (stock.js chooses the words)
    if (!x && opts.to) { $('#iline').textContent = t('landing.live.empty'); $('#istamp').textContent = ''; $('#iSch').classList.add('ghost'); idleSch.set([], { finished: true, live: false }); document.dispatchEvent(new CustomEvent('skiff-notr')); document.dispatchEvent(new CustomEvent('skiff-stage')); return; }
    if (!x) { $('#iline').textContent = t('landing.live.empty'); $('#istamp').textContent = ''; $('#iSch').classList.add('ghost'); idleSch.set([], { finished: true, live: false }); document.dispatchEvent(new CustomEvent('skiff-stage')); return; }   // P7: the empty state, no frozen «waiting» node
    await draw(x);
    document.dispatchEvent(new CustomEvent('skiff-stage'));
  }
  async function drawQuick(x) {
    drawnTx = x.tx;
    const T = await tokens().catch(() => null);
    const tok = (T && T.find(4663, x.stock)) || { symbol: x.stock, logo: x.logo, kind: 'stock' };
    $('#iSch').classList.remove('ghost');
    $('#iline').innerHTML = esc(t('landing.live.line', { usd: nf(x.usd, 0), ticker: '\u0000' })).replace('\u0000', `<span class="pair">${tk(tok)}${esc(x.stock)}</span>`);
    $('#istamp').textContent = t('landing.live.stamp', { age: ago(Date.now() / 1000 - x.ts), time: hhmm(x.ts) });
    if (mode === 'idle') idleSch.set(tradeNodes(x, tok), { finished: true, live: false });
    document.dispatchEvent(new CustomEvent('skiff-stage'));
  }
  async function draw(x) {
    drawnTx = x.tx;
    $('#iSch').classList.remove('ghost');
    const T = await tokens().catch(() => null);
    const tok = (T && T.find(4663, x.stock)) || { symbol: x.stock, logo: x.logo, kind: 'stock' };
    $('#iline').innerHTML = esc(t('landing.live.line', { usd: nf(trackUsd(x), 0), ticker: '\u0000' })).replace('\u0000', `<span class="pair">${tk(tok)}${esc(x.stock)}</span>`);
    $('#istamp').textContent = t('landing.live.stamp', { age: ago(Date.now() / 1000 - x.ts), time: hhmm(x.ts) });
    const nodes = trackNodes(x.track, { ticker: x.stock, showAddress: true, tokOf: (a) => (T && T.find(4663, a.symbol)) || null });
    if (mode === 'idle') { if (RM) idleSch.set(nodes, { finished: true, live: false }); else idleSch.play(nodes, { step: 650 }); }
  }
  function onUpdate(m, st, d, ev) {
    if (ev === 'meta' || ev === 'route' || ev === 'source' || ev === 'baseline') { if (mode !== 'track') stageMode('race'); return; }
    if (m && ev === 'done') { if (mode !== 'track') stageMode('race'); return; }
    if (!m && !st) { if (mode !== 'track') { stageMode('idle'); idle(); } }
  }
  sw = mountSwap($('#swap'), { raceEl: P.race, trackEl: PHONE ? null : P.track, to: opts.to, details: false, onUpdate,   // phones: the scheme lives in the window (design v5 S6)
    onTrack: (on, title) => { if (PHONE) { if (!on) stageMode(sw.state.m ? 'race' : 'idle'); return; } stageMode(on ? 'track' : (sw.state.m ? 'race' : 'idle')); if (on && title) $('#stgT').textContent = title; },
    onToken: (x) => { if (mode === 'idle') idle(); document.dispatchEvent(new CustomEvent('skiff-to', { detail: x })); } });   // P8: the page hears «You get» changes (Big trades chip)
  stageMode('idle');
  idle();
  setInterval(() => { if (mode === 'idle' && !document.hidden) { idleFor = null; idle(); } }, 120000);   // P5: every 2 min   // P3: a fresher buy replaces the scene within a minute

  /* DEV demo for video and audit (SPEC §10): ?demoTx=<hash>|top — local host + dev engine only; plays a real transaction via /api/track */
  if (LOCAL && QS.get('demoTx')) (async () => {
    let cfg = {}; try { cfg = await api('/healthz'); } catch { /* not dev */ }
    if (!cfg.dev) return;
    const want = QS.get('demoTx').toLowerCase();
    const x = want === 'top' ? await bigUserBuy(null) : await (async () => { const d = await api('/api/trades?limit=200&minUsd=1000'); const y = (d.trades || []).find((z) => z.tx.toLowerCase() === want); if (!y) return null; const tr = await api('/api/track?' + new URLSearchParams({ txHash: y.tx, fromChain: 4663, toChain: 4663 })).catch(() => null); return tr ? { ...y, track: tr } : null; })();
    if (!x) return;
    stageMode('track');
    const host = document.createElement('div'); host.className = 'schw'; P.track.replaceChildren(host);
    const S = scheme(host, { dir: 'auto', big: !PHONE });
    S.play(trackNodes(x.track, { ticker: x.stock, showAddress: true }), { step: 900 });
  })();
  return sw;
}

/** one big-trade row on fixed columns (audit v4 B3): icon | ticker | side | one venue logo + «N pools · time» | amount | age ↗ */
/** P5: «14:30» today, «16 Nov 14:30» for an older trade (UTC, as every time on the site) */
const whenS = (ts) => { const d = new Date(ts * 1000), n = new Date(); return d.toISOString().slice(0, 10) === n.toISOString().slice(0, 10) ? hhmm(ts) : `${d.getUTCDate()} ${d.toUTCString().slice(8, 11)} ${hhmm(ts)}`; };
export function tradeRow(x, tok, isNew) {
  const v = (x.venues || [])[0];
  return `<div class="tr2 ${isNew ? 'new' : ''}">${tk(tok || { symbol: x.stock, logo: x.logo, kind: 'stock' }, 'stk')}<b class="tk3">${esc(x.stock)}</b><span class="sd ${x.side}">${t('common.' + x.side)}</span><span class="m">${v ? `<img src="${esc(v.logo || '/assets/logos/venues/other.svg')}" alt="" title="${esc((x.venues || []).map((y) => y.name).join(', '))}">` : '<i></i>'}<span>${esc(t('app.trades.row_sub', { legs: plural('common.legs', x.legs || 1), time: whenS(x.ts) }))}</span></span><b class="u">${esc(t('app.trades.usd', { usd: nf(x.usd, 0) }))}</b><a class="w" href="${esc(x.explorer)}" target="_blank" rel="noopener" title="${esc(t('app.trades.block', { block: blk(x.block) }))}" aria-label="${esc(t('common.explorer'))}">↗</a></div>`;   // P3: the time is shown once (in «pools · time»); this is the explorer link
}
