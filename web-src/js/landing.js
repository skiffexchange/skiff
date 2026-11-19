// Landing v2 (design/v2/SPEC.md §2): one promise, the swap window as the one main object, real counters from /api/reach,
// «Live on Robinhood Chain» — the route scheme playing real big buys from /api/trades. Nothing invented, nothing moves its neighbours.
import { t, has, esc, $, $$, api, tokens, netTokens, tokI, cbadge, nf, hhmm, blk, ago, RM, pauseOffscreen, onView, usdShort, RH_LOGO } from './core.js';
import { tk } from './board.js';
import { mountSwap } from './swap.js';
import { scheme, tradeNodes, trackNodes, lanesOf, laneLabel } from './scheme.js';
import { trackUsd, trackTx } from './stage.js';
import { bindHeader, restore } from './wallet.js';
import { chrome } from './chrome.js';

chrome();
bindHeader(); restore();

/* ---------- hero: a static promise, the reach line, a row of real stock logos ----------
   No rotation: the form never re-quotes on its own. The default pair is USDG → NVDA; a logo sets «You get» and the form quotes only when an
   amount is already there (setTo → requote, which stays idle at amount 0). Logos: popular first, then by 24 h volume; one accessible name each. */
const sw = mountSwap($('#swap'), { rows: true, compact: true, details: true, to: 'NVDA', onToken: (x) => markLogo(x && x.symbol) });
function markLogo(sym) { $$('#hlogos [data-sym]').forEach((b) => { const on = b.dataset.sym === sym; b.classList.toggle('on', on); b.setAttribute('aria-pressed', on ? 'true' : 'false'); }); }
(async () => {
  let T = null, st = null;
  try { [T, st] = await Promise.all([tokens(), api('/api/stats', { cache: 60000 }).catch(() => null)]); } catch { return; }
  const POP = ['NVDA', 'AAPL', 'TSLA', 'SPY', 'QQQ', 'MSTR', 'HOOD', 'AMZN', 'META', 'MSFT', 'GOOGL', 'PLTR'];
  const vol = Object.fromEntries(((st && st.byStock) || []).map((x) => [x.stock, x.usd]));
  const stocks = T.list.filter((x) => x.chain === 4663 && (x.kind === 'stock' || x.kind === 'etf') && x.logo);
  const pop = POP.map((s) => stocks.find((x) => x.symbol === s)).filter(Boolean);
  const rest = stocks.filter((x) => !pop.includes(x)).sort((a, b) => (vol[b.symbol] || 0) - (vol[a.symbol] || 0));
  const list = [...pop, ...rest].slice(0, 10);
  const row = $('#hlogos'); if (!row || !list.length) return;
  row.innerHTML = list.map((x) => `<button type="button" class="hlogo" data-sym="${esc(x.symbol)}" aria-pressed="false" aria-label="${esc(x.name || x.symbol)} (${esc(x.symbol)})" title="${esc(x.symbol)}">${tokI(x, 'stk')}</button>`).join('');
  row.querySelectorAll('[data-sym]').forEach((b) => b.addEventListener('click', () => { const x = T.find(4663, b.dataset.sym); if (x && sw) { sw.setTo(x); markLogo(x.symbol); } }));
  markLogo((sw && sw.state.to && sw.state.to.symbol) || 'NVDA');
})();

/* ---------- live block in the chain chip ---------- */
async function head() { let ok = false; try { const d = await api('/api/trades?limit=1'); ok = !!d.scannedTo && !d.stale; if (d.scannedTo) $$('[data-blk]').forEach((e) => { e.textContent = t('nav.chain_block', { block: blk(d.scannedTo) }); $$('[data-blkv]').forEach((v) => { v.textContent = blk(d.scannedTo); }); const pill = e.closest('.chain,.chip2') || e; pill.title = t('nav.chain_tip', { block: blk(d.scannedTo) }); /* P3: the live block, shown */  }); } catch { /* keeps last */ } $$('[data-blk]').forEach((e) => { const pill = e.closest('.chain,.chip2'); if (pill) pill.classList.toggle('down', !ok); }); }
head(); setInterval(() => { if (!document.hidden) head(); }, 15000);

/* ---------- lede + counters: only real numbers from /api/reach (marketer's rule: 20+ only when sources.count ≥ 20) ---------- */
const lede = $('#lede');
function fadeIn(el) { if (!RM && !document.hidden) el.animate([{ opacity: 0 }, { opacity: 1 }], { duration: 420, easing: 'ease-out' }); }
(async () => {
  let r = null;
  try { r = await api('/api/reach', { cache: 600000 }); } catch { r = null; }
  const n = r && r.sources && r.sources.count;
  if (r && n && has('landing.hero.title_static')) { const h = $('#h1'); const v = t('landing.hero.title_static', { sources: n >= 20 ? Math.floor(n / 10) * 10 : n }); if (h && h.textContent !== v) h.innerHTML = v.split(/(?<=\.)\s+/).map((x) => `<span class="hs">${esc(x)}</span>`).join(' '); }   // P8: «One swap. 20+ sources. The best price.»
  if (r && n && has('landing.hero.sub_reach')) lede.textContent = t('landing.hero.sub_reach', { sources: n >= 20 ? Math.floor(n / 10) * 10 : n, dexes: (r.dexes && r.dexes.count) || '', chains: (r.chains && r.chains.count) || '' });
  else lede.textContent = n >= 20 ? t('landing.hero.lede', { n: Math.floor(n / 10) * 10 }) : n > 0 ? t('landing.hero.lede_exact', { n }) : t('landing.hero.lede_plain');
  fadeIn(lede);
})();

/* ---------- P4: the counters are live chain numbers — Robinhood Chain block, stock-token volume 24 h, big buys 24 h, stock tokens.
   The grid, sizes and baselines of the cells are the same as before; the block number follows the header chip (every 15 s). ---------- */
(async () => {
  const st = $('#stats'); if (!st) return;
  let T = null, s = null, d = null;
  let n = null;
  try { [T, s, d, n] = await Promise.all([tokens(), api('/api/stats', { cache: 60000 }).catch(() => null), api('/api/trades?limit=1').catch(() => null), netTokens().catch(() => null)]); } catch { st.style.visibility = 'hidden'; return; }
  const lab = (k, fb) => (has(`landing.stats.${k}.label`) ? t(`landing.stats.${k}.label`) : fb);
  const sho = (k, fb) => (has(`landing.stats.${k}.label_short`) ? t(`landing.stats.${k}.label_short`) : fb);
  const imgs = (list, more) => `<span class="lg2">${list.map((x) => `<img src="${esc(x.logo)}" alt="" title="${esc(x.name || x.symbol || '')}" loading="lazy">`).join('')}${more > 0 ? `<em>+${more}</em>` : ''}</span>`;
  const cell = (k, val, label, short, marks, attr = '') => `<div class="st" role="listitem"><b${attr}>${esc(val)}</b><span class="cap"><span class="lf">${esc(label)}</span><span class="ls">${esc(short)}</span></span>${marks}</div>`;
  const stocks = T.list.filter((x) => x.chain === 4663 && (x.kind === 'stock' || x.kind === 'etf'));
  const vol = Object.fromEntries(((s && s.byStock) || []).map((x) => [x.stock, x.usd]));
  const topStocks = stocks.filter((x) => x.logo).sort((a, b) => (vol[b.symbol] || 0) - (vol[a.symbol] || 0));
  const dexMarks = []; for (const x of (s && s.byDex) || []) { if (x.logo && !dexMarks.some((y) => y.logo === x.logo)) dexMarks.push(x); }
  const blkN = d && d.scannedTo ? blk(d.scannedTo) : '—';
  const byIssuer = (list) => { const seen = new Set(); return list.filter((x) => { const k = (x.name || x.symbol).split(' ')[0].toLowerCase(); if (seen.has(k)) return false; seen.add(k); return true; }); };   // P5: one mark per issuer (SPY and GLD are both SPDR)
  const cells = [
    cell('block', blkN, lab('block', t('nav.chain')), sho('block', t('nav.chain')), `<span class="lg2"><img src="${RH_LOGO}" alt="" title="${esc(t('nav.chain'))}"><span class="ldot" aria-hidden="true"></span></span>`, ' data-blkv'),
    cell('volume', s && s.volume24hUsd != null ? usdShort(s.volume24hUsd) : '—', lab('volume', t('stocks.cols.volume')), sho('volume', t('stocks.cols.volume')), imgs(dexMarks.slice(0, 4), 0)),
    cell('bigbuys', s && s.bigBuys24h != null ? nf(s.bigBuys24h, 0) : '—', lab('bigbuys', t('app.trades.title')), sho('bigbuys', t('app.trades.title')), imgs(byIssuer(((s && s.bigBuysByStock) || []).map((b) => T.find(4663, b.stock)).filter((x) => x && x.logo)).slice(0, 4), 0)),
    cell('stocks', nf(stocks.length, 0), lab('stocks', t('nav.stocks')), sho('stocks', t('nav.stocks')), imgs(byIssuer(topStocks).slice(0, 3), stocks.length - 3)),
  ];
  // P11 («+100500 memecoins»): tokens with liquidity (listed + live network tokens, the picker's Tokens list), the top marks by
  // 24 h volume from that list (the engine already drops lookalikes; never a company mark)
  const tokN = n && n.all ? n.all.length : 0;   // = the picker's token list (ours + live network tokens)
  if (tokN && has('landing.stats.tokens.value')) {
    const top = (n.net || []).filter((x) => x.logo).sort((a, b) => (b.vol || 0) - (a.vol || 0));
    const marks = []; for (const x of top) { if (!marks.some((y) => y.logo === x.logo)) marks.push(x); if (marks.length === 4) break; }
    const tv = t('landing.stats.tokens.value', { count: nf(tokN, 0) });
    cells.push(cell('tokens', tv, lab('tokens', ''), sho('tokens', ''), imgs(marks, 0), /\d/.test(tv) ? '' : ' class="wd"'));   // words, not a number: their own size on the same line box
  }
  st.innerHTML = cells.join('');
  if (has('landing.stats.aria')) st.setAttribute('aria-label', t('landing.stats.aria', { block: blkN, volume: s && s.volume24hUsd != null ? usdShort(s.volume24hUsd) : '', count: s && s.bigBuys24h != null ? nf(s.bigBuys24h, 0) : '', stocks: stocks.length, tokens: tokN ? nf(tokN, 0) : '' }));
  // P6: no entrance fade — the live numbers are content from the first frame they exist
})();

/* ---------- Live on Robinhood Chain: real big buys of the last 24 h, the same scheme as your own swap ---------- */
const stage = $('#lstage'), pp = $('#lpp');
const LPH = matchMedia('(max-width: 760px)').matches;   // P3: phones get a compact card — three lanes at most, tight pitch, fixed slots
const SCH = scheme(stage, LPH ? { dir: 'auto', big: false, pitch: 56, lanePitch: 26, maxLanes: 3, minLanes: 3 } : { dir: 'auto', big: false, pitch: 84, minLanes: 5 });   // P1 (design/premium/SPEC.md §7.3): one compact card of fixed height; the scheme fits it
let trades = [], cur = 0, timer = null, stopped = false, vis = false;
const ROT = 7500;
async function loadTrades() {
  const since = Date.now() / 1000 - 86400;
  const d = await api('/api/trades?limit=200&minUsd=5000', { cache: 60000 });   // $5k floor (17.11): the latest big buys, not $1-2k ones (8 latest span ~0.1-5 h)
  // P2: the latest buys first (the scene is «live now»), up to 10 checked through /api/track
  const top = (d.trades || []).filter((x) => x.side === 'buy' && x.ts >= since && (x.pools || []).length).sort((a, b) => b.ts - a.ts).slice(0, 16);
  // real people's swaps only (the engine marks bots' arbitrage userSwap:false); the scheme comes from /api/track of that very transaction
  const tr = [];   // P5: one /api/track at a time, stop at six people's buys (no burst against the track limit)
  for (const x of top) { let r = null; try { r = await trackTx(x.tx); } catch { r = null; } tr.push(r); if (tr.filter((q) => q && q.userSwap === true && q.done).length >= 6) break; }
  const wal = (tq) => ((tq.nodes || []).find((n) => n.kind === 'wallet') || {}).amount;
  const lanes = (tq) => (((tq.nodes || []).find((n) => n.kind === 'split') || {}).split || []).length;
  const users = top.slice(0, tr.length).map((x, i) => ({ ...x, track: tr[i] }))
    .filter((x) => x.track && x.track.userSwap === true && x.track.done && wal(x.track) && (wal(x.track).usd || 0) >= x.usd * 0.5)   // the wallet really got the stock
    .sort((a, b) => b.ts - a.ts);
  return users.slice(0, 8);   // P7: people's buys only (contract receivers are userSwap:false) — never the unchecked list
}
function show(i) {
  cur = i; const x = trades[i]; if (!x) return;
  const T = window.__tok;
  const tok = (T && T.find(4663, x.stock)) || { symbol: x.stock, logo: x.logo, kind: 'stock' };
  $('#lline').innerHTML = esc(t('landing.live.line', { usd: nf(trackUsd(x), 0), ticker: '\u0000' })).replace('\u0000', `<span class="pair">${tk(tok)}${esc(x.stock)}</span>`);
  $('#lstamp').textContent = t('landing.live.stamp', { age: ago(Date.now() / 1000 - x.ts), time: hhmm(x.ts) });
  const lt = $('.ltag'); if (lt) lt.style.visibility = Date.now() / 1000 - x.ts < 3600 ? '' : 'hidden';
  $('#lstamp').title = t('common.stamp_tip', { block: blk(x.block), time: hhmm(x.ts), date: new Date(x.ts * 1000).toUTCString().slice(5, 16) });
  const a = $('#lcta'); a.href = '/app?to=' + encodeURIComponent(x.stock); a.innerHTML = esc(t('landing.live.cta', { ticker: '\u0000' })).replace('\u0000', `<span class="pair">${tk(tok)}${esc(x.stock)}</span>`) + ' ›';
  const nodes = x.track && (x.track.nodes || []).length ? trackNodes(x.track, { ticker: x.stock, showAddress: true, tokOf: (a) => (T && T.find(4663, a.symbol)) || null }) : tradeNodes(x, tok);
  const split = nodes.find((n) => n.kind === 'split');
  $('#laria').textContent = t('landing.live.aria', { usd: nf(trackUsd(x), 0), ticker: x.stock, age: ago(Date.now() / 1000 - x.ts), split: (split ? split.lanes : []).map(laneLabel).join(', ') });
  if (!RM) $('.lhead').animate([{ opacity: 0.25 }, { opacity: 1 }], { duration: 420, easing: 'ease-out' });
  if (RM || !vis) SCH.set(nodes, { finished: true, live: false }); else SCH.play(nodes, { step: 700 });
}
function sched() { clearTimeout(timer); if (!stopped && !RM && vis && !document.hidden && trades.length > 1) timer = setTimeout(() => { show((cur + 1) % trades.length); sched(); }, ROT); }
pp.addEventListener('click', () => { stopped = !stopped; pp.textContent = t(stopped ? 'landing.live.play' : 'landing.live.pause'); $('#laria').setAttribute('aria-live', stopped ? 'polite' : 'off'); if (!stopped) { show((cur + 1) % trades.length); } sched(); });
pauseOffscreen($('#live'), (on) => { const was = vis; vis = on; if (on && !was && trades.length && !stopped) show(cur); if (on) sched(); else { clearTimeout(timer); SCH.stop(); } });
(async () => {
  try { window.__tok = await tokens(); } catch { /* icons fall back to the trade's own logo */ }
  try { trades = await loadTrades(); } catch { trades = []; $('#lline').textContent = t('landing.live.down'); pp.hidden = true; return; }
  if (trades.length < 2) { $('#lline').textContent = t('landing.live.empty'); $('#lstamp').innerHTML = '&nbsp;'; pp.hidden = true; SCH.set([], { finished: true, live: false }); $('#live').classList.add('lempty'); return; }   // P7: fewer than two people's buys — the empty state
  if (RM) pp.hidden = true;
  show(0); sched();
})();

/* ---------- how it works: the stock count is live ---------- */
tokens().then((T) => { const n = T.list.filter((x) => x.chain === 4663 && (x.kind === 'stock' || x.kind === 'etf')).length; if (n) $('#how1').textContent = t('landing.how.1.body', { count: Math.max(0, n - 3) }); }).catch(() => {});
$$('.how2 li').forEach((li, i) => onView(li, () => { if (!RM && !document.hidden) li.animate([{ opacity: 0, transform: 'translateY(10px)' }, { opacity: 1, transform: 'none' }], { duration: 700, delay: i * 80, easing: 'cubic-bezier(.16,1,.3,1)', fill: 'backwards' }); }, '-12% 0px'));
void lanesOf;

/* ---------- the Stocks and Tokens zones (zones; stocks first, all tokens second): six rows each, real data ---------- */
const zprice = (p) => (p == null ? (has('tokens.no_price') ? `<small>${esc(t('tokens.no_price'))}</small>` : '—') : '$' + nf(p, p < 0.01 ? 6 : p < 1 ? 4 : 2));
const zrow = (x, href, sub, buy) => `<a class="zr${x.custom ? ' nt' : ''}" href="${esc(href)}">${tokI(x, 'zi')}<span class="zn"><b>${esc(x.symbol)}</b><small>${sub}</small></span><span class="zp">${zprice(x.price)}</span><span class="zb">${esc(buy)}</span></a>`;
(async () => {
  let T, st; try { [T, st] = await Promise.all([tokens(), api('/api/stats', { cache: 60000 }).catch(() => null)]); } catch { return; }
  const vol = Object.fromEntries(((st && st.byStock) || []).map((x) => [x.stock, x.usd]));
  const list = T.list.filter((x) => x.chain === 4663 && (x.kind === 'stock' || x.kind === 'etf')).sort((a, b) => (vol[b.symbol] || 0) - (vol[a.symbol] || 0)).slice(0, 4);
  const el = $('#zst'); if (el && list.length) el.innerHTML = list.map((x) => zrow(x, '/stock/' + encodeURIComponent(x.symbol), esc(x.name), t('stocks.row.buy'))).join('');
})();
(async () => {
  let n; try { n = await netTokens(); } catch { return; }
  { const ld = $('#ltokens .mkh .ld'); const cnt = ((n.listed || []).length + (n.tokens || []).length) || n.all.length; if (ld && cnt && has('landing.markets.tokens_lede')) ld.textContent = t('landing.markets.tokens_lede', { count: cnt }); }   // P5 
  const list = n.all.filter((x) => !x.custom).sort((a, b) => (b.vol || 0) - (a.vol || 0)).slice(0, 4);   // P2: tokens on our list only
  const el = $('#ztk'); if (el && list.length) el.innerHTML = list.map((x) => zrow(x, '/app?to=' + encodeURIComponent(x.custom ? x.address : x.symbol), x.custom ? cbadge() : esc(x.name), t('stocks.row.buy'))).join('');
})();

/* P2: a two-sentence closing title sets each sentence on its own line (no hanging first word of the next sentence) — same words */
{ const h = $('#cta h2'); if (h && !h.children.length) { const parts = h.textContent.split(/(?<=[.!?])\s+/).filter(Boolean); if (parts.length > 1) { h.textContent = ''; parts.forEach((p) => { const s = document.createElement('span'); s.className = 'sent'; s.textContent = p; h.appendChild(s); h.appendChild(document.createTextNode(' ')); }); } } }

/* P3: How — each step shows a small piece of the real product, filled from live data (Column 556092c0 / Stripe ac5a3e30: product UI
   inside the feature card). Fixed boxes from the first frame; nothing here is invented: tickers, networks and sources come from the API. */
(async () => {
  let T = null, st = null, r = null;
  try { [T, st, r] = await Promise.all([tokens(), api('/api/stats', { cache: 60000 }).catch(() => null), api('/api/reach', { cache: 600000 }).catch(() => null)]); } catch { return; }
  const vol = Object.fromEntries(((st && st.byStock) || []).map((x) => [x.stock, x.usd]));
  const top = T.list.filter((x) => x.chain === 4663 && (x.kind === 'stock' || x.kind === 'etf') && x.logo).sort((a, b) => (vol[b.symbol] || 0) - (vol[a.symbol] || 0)).slice(0, 3);
  const chip = (x) => `<span class="hchip">${tokI(x, 'ic')}${esc(x.symbol)}</span>`;
  const h1 = $('#hv1'); if (h1 && top.length) h1.innerHTML = `<div class="hrow">${top.map(chip).join('')}</div>`;
  const pay = ['USDC', 'USDT', 'ETH'].map((s) => T.list.find((x) => x.symbol === s && x.chain !== 4663 && x.logo) || T.list.find((x) => x.symbol === s && x.logo)).filter(Boolean);
  const nets = ((r && r.chains && r.chains.list) || []).filter((c) => c.logo && !/robinhood/i.test(c.name || '')).slice(0, 3);
  const h2 = $('#hv2'); if (h2 && (pay.length || nets.length)) h2.innerHTML = `<div class="hrow">${pay.slice(0, 2).map(chip).join('')}</div><div class="hrow">${nets.slice(0, 2).map((c) => `<span class="hchip net"><img class="ic" src="${esc(c.logo)}" alt="">${esc(c.name)}</span>`).join('')}</div>`;
  const pickSrc = (l, kind) => { const x = (l || []).find((y) => y && y.logo && !/other\.svg/.test(y.logo)); return x ? { ...x, kind } : null; };
  const srcs = r ? [pickSrc(r.aggregators && r.aggregators.list, 'aggregator'), pickSrc(r.dexes && r.dexes.list, 'dex'), pickSrc(r.makers && r.makers.list, 'maker')].filter(Boolean) : [];
  const KIND = { aggregator: 'swap.race.kind.aggregator', dex: 'swap.race.kind.dex', maker: 'swap.race.kind.maker' };
  const h3 = $('#hv3'); if (h3 && srcs.length) h3.innerHTML = srcs.map((x) => `<div class="hsrc"><img class="ic" src="${esc(x.logo)}" alt=""><b>${esc(x.name)}</b><small>${esc(t(KIND[x.kind]))}</small></div>`).join('');
  const h4 = $('#hv4'); if (h4) h4.innerHTML = `<p class="hbadge"><span class="dot"></span>${esc(t('swap.state.one.badge'))}</p><span class="hsign">${esc(t('swap.state.one.cta'))}</span>`;
})();

