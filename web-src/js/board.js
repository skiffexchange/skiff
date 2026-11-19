// Board (who gives most, honest rules), route rail, rolling digits, split ribbons. One model for /api/quote answers and /api/board rows.
import { t, th, has, esc, amt, fl, nf, usd, kfmt, hhmm, blk, ago, plural, tokIcon, vlogo, venueName, INTENT_SVG, RM } from './core.js';

const USDG = { symbol: 'USDG', logo: '/assets/logos/tokens/usdg.png', kind: 'stable', decimals: 6 };
const tokOf = (x) => ({ symbol: x.symbol, logo: x.logo, kind: x.kind, decimals: x.decimals });

/** /api/quote (done) → model */
export function fromQuote(q) {
  const routes = q.routes || [], qo = q.quotedOnly || [];
  const cross = q.bestBasis === 'quoted' || (q.request && Number(q.request.fromChain) !== 4663);
  const sim = routes.filter((r) => r.status === 'ok').map((r) => route(r, r.out.simulated));
  const xq = cross ? routes.filter((r) => r.status === 'quoted').map((r) => route(r, r.out.quoted)) : [];
  const m = {
    kind: 'quote', cross, from: tokOf(q.fromToken), to: tokOf(q.toToken), amountIn: q.amountIn, block: q.block, at: q.at,
    sim: cross ? xq : sim,
    solver: qo.filter((r) => r.status === 'intent').map((r) => ({ id: r.id, venue: r.venue, min: r.out.min, quoted: r.out.quoted, raw: r })),
    quoted: qo.filter((r) => r.status === 'quoted').map((r) => ({ id: r.id, venue: r.venue, out: r.out.quoted, min: r.out.min, raw: r })),
    na: routes.filter((r) => r.status === 'reverted').map((r) => ({ venue: r.venue })),
    noRoute: [...new Map((q.unavailable || []).filter((u) => !/^(solver|swap|own-router-tool)$/.test(u.kind || '')).map((u) => [venueName(u), { venue: u }])).values()],
    base: q.baseline && q.baseline.available !== false ? q.baseline : null,
    vs: q.vsSinglePool || null, vsUsd: q.vsSinglePoolUsd, vsReason: q.vsSinglePoolReason || (q.vsSinglePool ? null : 'no_simulated_route'),
    bestId: q.best, defaultRoute: q.defaultRoute || null, routesUnavailable: q.routesUnavailable || null, stale: !!q.stale, staleAgeS: q.staleAgeS, notes: q.notes || [], insufficient: !!q.insufficientBalance, probe: !!q.probeSender,
    replay: !!q.replay, recordedAt: q.recordedAt,
  };
  m.sim.sort((a, b) => (BigInt(b.out.raw) > BigInt(a.out.raw) ? 1 : BigInt(b.out.raw) < BigInt(a.out.raw) ? -1 : 0));   // best first, also while streaming
  m.best = m.sim.find((r) => r.id === q.best) || null;
  return m;
}
/** an executable direct swap in one Uniswap V3/V4 pool (kind: "direct") — a venue like any other, its only «step» is the pool */
export function directOf(r) {
  if (!r || !(r.kind === 'direct' || (r.venue && (r.venue.kind === 'dex' || r.venue.kind === 'direct')))) return null;
  const v = r.venue || {}, p = r.pool && typeof r.pool === 'object' ? r.pool : {};
  const name = v.name || '', pair = p.pair || '', logo = v.logo, fee = p.feePct != null ? nf(p.feePct, 2, 0) : '';
  const label = has('direct.label') ? t('direct.label') : name;
  const sub = (has('direct.sub') ? t('direct.sub', { venue: name, pair, fee }) : [name, pair].filter(Boolean).join(' · ')).replace(/\s+%$/, '').replace(/(\s·\s*)+$/, '').trim();
  return { venue: { ...v, name: label, logo }, sub, poolId: String(p.id || p.address || '').toLowerCase(), pools: [{ dex: v.displayKey, name, logo, pair, pool: p.id || p.address, sharePct: 100, shareBps: 10000 }] };
}
function route(r, out) {
  const d = directOf(r);
  const pools = r.split && r.split.length ? r.split : d ? d.pools : [];
  return { id: r.id, venue: d ? d.venue : r.venue, out, direct: !!d, sub: d && d.sub, poolId: d && d.poolId, min: r.out && r.out.min, quotedOut: r.out && r.out.quoted, most: !!r.most, pools,
    byDex: r.splitByDex && r.splitByDex.length ? r.splitByDex : pools, via: r.via || [], path: r.path || [],
    fees: r.fees, gas: r.gas, steps: r.steps || [], durationS: r.durationS, impact: r.priceImpactPct, raw: r };
}
/** /api/board row → model */
export function fromBoardRow(row) {
  const ven = row.venues || [];
  const sim = ven.filter((v) => v.status === 'ok').map((v, i) => ({
    id: 'b' + i, venue: (directOf(v) || v).venue, out: v.out, min: v.min, most: !!v.most, direct: !!directOf(v), sub: directOf(v) && directOf(v).sub, poolId: directOf(v) && directOf(v).poolId,
    pools: v.most && row.best && (row.best.pools || []).length ? row.best.pools : (directOf(v) ? directOf(v).pools : []), byDex: v.most && row.best && (row.best.split || []).length ? row.best.split : (directOf(v) ? directOf(v).pools : []),
    fees: { route: row.best && v.most ? row.best.routeFee : { usd: v.routeFeeUsd }, gas: { usd: v.gasUsd } }, path: v.most && row.best ? row.best.path || [] : [],
  })).sort((a, b) => (BigInt(b.out.raw) > BigInt(a.out.raw) ? 1 : -1));
  const m = {
    kind: 'board', cross: false, from: USDG, to: { symbol: row.stock, logo: row.logo, kind: 'stock', decimals: 18 }, name: row.name, token: row.token,
    amountIn: row.amountIn, sizeUsd: row.sizeUsd, block: row.block, at: row.at, sim,
    solver: (row.quotedOnly || []).filter((r) => r.status === 'intent').map((r) => ({ venue: r.venue, min: r.minGuaranteed, quoted: r.quoted })),
    quoted: (row.quotedOnly || []).filter((r) => r.status === 'quoted').map((r) => ({ venue: r.venue, out: r.quoted, min: r.minGuaranteed })),
    na: ven.filter((v) => v.status === 'reverted').map((v) => ({ venue: v.venue })), noRoute: [],
    base: row.singlePool && row.singlePool.available !== false ? row.singlePool : null,
    vs: row.vsSinglePool, vsUsd: row.vsSinglePoolUsd, vsReason: row.vsSinglePoolReason, postable: row.postable, hero: row.hero,
    stale: !!row.stale, ageS: row.ageS, pending: !!row.pending, recordedAt: row.recordedAt,
  };
  m.best = m.sim.find((r) => r.most) || null;
  if (m.best && row.best) { m.best.impact = row.best.priceImpactPct; }
  return m;
}
const poolKey = (p) => (p.pool || '') + '|' + p.dex + '|' + (p.pair || '');
/** unique pools of a route in execution order: path[] (via hops included) when the engine gives it, else the share list */
export function routePools(r) {
  if (!r) return [];
  const src = r.path && r.path.length ? r.path : (r.pools || []);
  const seen = new Set(); return src.filter((p) => { const k = poolKey(p); if (seen.has(k)) return false; seen.add(k); return true; });
}
export const poolCount = (r) => routePools(r).length;
/** "+$ vs one pool" for any simulated row, in the engine's own price of the gap (vs.usd / vs.raw); null unless the engine gave a number */
export function rowVs(m, r) {
  if (!m.vs || m.vsUsd == null || !m.base || !r || !r.out || m.cross) return null;
  if (r === m.best) return m.vsUsd;
  const vraw = fl(m.vs.raw, m.vs.decimals); if (!vraw) return null;
  const px = m.vs.usd / vraw;
  const d = fl((BigInt(r.out.raw) - BigInt(m.base.out.raw)).toString(), r.out.decimals);
  return d * px;
}
export const isHero = (row) => (row.hero === true) || (row.hero === undefined && row.postable && (row.vsSinglePoolUsd || 0) >= 50 && (row.sizeUsd || 0) >= 50000 && !row.pending);
export const sizeLabel = (m) => (m.from.kind === 'stable' && m.amountIn.usd ? kfmt(Math.round(m.amountIn.usd / 1000) * 1000 >= 1000 ? Math.round(m.amountIn.usd / 1000) * 1000 : m.amountIn.usd) : amt(m.amountIn.raw, m.amountIn.decimals, 4));
export const tk = (o, cls = 'ic') => tokIcon(o.symbol, o.logo, o.kind, cls);
export const pairT = (o) => `<span class="pair">${tk(o)}${esc(o.symbol)}</span>`;
const poolsLabel = (n) => plural('common.pools', n);
const dexLogos = (r, n = 4) => {
  const seen = new Set(); const out = [];
  for (const p of (r.byDex && r.byDex.length ? r.byDex : r.pools)) { if (seen.has(p.dex)) continue; seen.add(p.dex); out.push(p); if (out.length >= n) break; }
  return out.map((p) => `<img src="${esc(p.logo || '/assets/logos/venues/other.svg')}" alt="${esc(p.name)}" loading="lazy">`).join('');
};
export function boardTitle(m) {
  return th('board.title', { amount: sizeLabel(m), from: pairT(m.from), to: pairT(m.to) }, ['from', 'to']);
}
/** D9: «Updated N min ago» on screen, block and UTC time in the tooltip; no block for cross-chain (none exists) */
export const stampTitle = (m) => { const ts = m.recordedAt || m.at, d = new Date(ts * 1000).toUTCString().slice(5, 16); return m.block ? t('common.stamp_tip', { block: blk(m.block), time: hhmm(ts), date: d }) : t('common.time_utc', { time: hhmm(ts) }); };
export const ageOf = (m) => Date.now() / 1000 - (m.recordedAt || m.at);
export const stamp = (m, id = 'board.stamp') => (ageOf(m) < 60 && has('common.updated_just') ? t('common.updated_just') : t(id, { age: ago(ageOf(m)) }));
/** the direct row and the «one pool» reference are the same pool → one row (D2) */
export const sameAsBase = (m, r) => !!(r && r.direct && m.base && m.base.out && ((r.poolId && m.base.pool && r.poolId === String(m.base.pool).toLowerCase()) || r.out.raw === m.base.out.raw));

/** cross-chain steps, each with its logo: bridge → swap on Robinhood Chain (the provider's own tools never named) */
export function stepsHtml(r, m) {
  return r.steps.map((s) => {
    const own = s.kind === 'own-router-tool' || s.kind === 'swap' || s.kind === 'solver' || /^(intent|deposit-swap|intent-bridge)$/.test(s.displayKey || '');
    const name = own && s.type !== 'cross' ? t('swap.route.cross_swap') : venueName(s);
    return `<span class="st">${vlogo(s, '')}${esc(name)}</span>`;
  }).join('<span class="ar">→</span>') + (r.durationS ? `<span class="du">· ${esc(t('swap.route.cross', { bridge: '', venue: '', s: r.durationS }).split('·').pop().trim())}</span>` : '');
}
/** render the comparison board; opts: {cmp, title, onPick(route), sel, static, delay, mergeNa, skel (slots), noHead} */
export function board(el, m, o = {}) {
  el.classList.toggle('cmp', !!o.cmp);
  if (!m) { el.innerHTML = ''; return; }
  const sims = m.sim;
  const vsList = sims.map((r) => rowVs(m, r));
  const maxG = Math.max(1, ...vsList.filter((x) => x > 0));
  const dp = 2;
  const outCell = (a) => `<div class="out">${amt(a.raw, a.decimals, dp)}<small>${tk(m.to)}${esc(m.to.symbol)}</small></div>`;
  const rows = [];
  sims.forEach((r, i) => {
    const g = vsList[i];
    const isBest = r.most || (m.cross && r.id === m.bestId);
    let cell = '', thin = false;
    if (g != null && g > 0) cell = `${t('board.vs.gain', { usd: nf(g, 2) })}<div class="bar"><i style="--k:${(g / maxG).toFixed(3)}"></i></div>`;
    else if (isBest && m.vsReason === 'pools_too_thin') { thin = true; cell = `<span title="${esc(t('board.vs.thin_tip_plain', { amount: sizeLabel(m) }))}">${t('board.vs.thin')}</span>`; }
    const ref = sameAsBase(m, r);
    const sub = r.direct ? `<span class="ps"><span class="lg">${dexLogos(r, 1)}</span><span class="pst">${esc(r.sub || '')}</span></span>`
      : r.pools.length ? `<span class="ps"><span class="lg">${dexLogos(r)}</span>${esc(t('board.row.pools', { pools: poolsLabel(poolCount(r)) }))}</span>`
      : m.cross && r.steps.length ? `<span class="ps">${stepsHtml(r, m)}</span>` : '';
    rows.push(`<div class="vr rowin ${isBest ? 'best' : ''} ${o.onPick ? 'pick' : ''} ${o.sel && o.sel === r.id ? 'sel' : ''}" data-id="${esc(r.id)}"><div class="who">${vlogo(r.venue)}<div><div class="nm"><span class="nt">${esc(venueName(r.venue))}</span>${isBest ? `<span class="most" ${has('common.most_tip') ? `title="${esc(t('common.most_tip', { ticker: m.to.symbol }))}"` : ''}>${t('common.most')}</span>` : ''}${ref ? `<span class="tag">${t(isBest && has('direct.tag') ? 'direct.tag' : 'board.pool.tag')}</span>` : ''}</div>${sub}</div></div>${outCell(r.out)}<div class="gain ${thin ? 'thin' : ''}" ${g > 0 && m.base ? `title="${esc(t('board.vs.tip', { dex: m.base.name, pair: m.base.pair || '', fee: m.base.feePct != null ? nf(m.base.feePct, 2, 0) : '', depth: m.base.liqUsd ? nf(m.base.liqUsd, 0) : '', block: blk(m.base.block || m.block) }))}"` : ''}>${cell}</div></div>`);
  });
  // one pool: a reference, never MOST, never signable
  if (m.base && m.base.out && !m.cross && !sims.some((r) => sameAsBase(m, r))) {
    const two = m.base.kind === 'single_venue';
    const clean = (x) => x.replace(/\s+%$/, '').replace(/(\s·\s*)+$/, '').replace(/^\s*·\s*/, '').trim();
    const sub0 = two ? t('board.pool.sub_path', { dex: m.base.name, mid: (m.base.pair || '').split('/')[1] || '' }) : t('board.pool.sub', { dex: m.base.name, pair: m.base.pair || '', fee: m.base.feePct != null ? nf(m.base.feePct, 2, 0) : '' });
    const sub = clean(sub0);
    const tip = two ? t('board.pool.tip_path') : t('board.pool.tip', { pair: m.base.pair });
    const at = sims.findIndex((r) => BigInt(r.out.raw) < BigInt(m.base.out.raw));
    rows.splice(at < 0 ? rows.length : at, 0, `<div class="vr rowin base" title="${esc(tip)}"><div class="who">${vlogo({ logo: m.base.logo, name: m.base.name })}<div><div class="nm"><span class="nt">${t(two ? 'board.pool.name_path' : 'board.pool.name')}</span><span class="tag">${t('board.pool.tag')}</span></div><span class="ps"><span class="pst">${esc(sub)}</span></span></div></div>${outCell(m.base.out)}<div class="gain"></div></div>`);
  }
  for (let k = 0; k < (o.skel || 0); k++) rows.push(`<div class="vr skel"><div class="who"><span class="sk" style="width:32px;height:32px;border-radius:9px"></span><div><span class="sk" style="width:120px"></span></div></div><div class="out"><span class="sk" style="width:70px;margin-left:auto"></span></div><div class="gain"><span class="sk" style="width:60px;margin-left:auto"></span></div></div>`);
  let lanes = '';
  if (m.solver.length) {
    lanes += `<div class="lane">${INTENT_SVG}${t('board.lane.solver')}</div>` + m.solver.map((r) => `<div class="vr rowin intent ${o.onPick ? 'pick' : ''} ${o.sel && o.sel === r.id ? 'sel' : ''}" data-id="${esc(r.id || '')}" title="${esc(t('board.solver.tip'))}"><div class="who"><span class="intico">${INTENT_SVG}</span><div><div class="nm"><span class="nt">${t('board.solver.name')}</span></div><span class="ps">${esc(t('board.solver.sub', { amount: amt(r.quoted.raw, r.quoted.decimals, 2) }))}</span></div></div><div class="out">${esc(t('board.solver.out', { amount: amt(r.min.raw, r.min.decimals, 2) }))}<small>${t('board.solver.min')}</small></div><div class="gain"></div></div>`).join('');
  }
  if (m.quoted.length && !m.cross && o.mergeQuoted) {
    lanes += `<div class="lane">${t('board.lane.quoted')}</div><div class="vr rowin quoted" title="${esc(t('board.quoted.tip'))}"><div class="who"><span class="lgs">${m.quoted.map((r) => vlogo(r.venue)).join('')}</span><div><div class="nm"><span class="nt">${esc(t('board.na.merged', { list: m.quoted.map((r) => venueName(r.venue)).join(', ') }))}</span></div></div></div><div class="out"></div><div class="gain"></div></div>`;
  } else if (m.quoted.length && !m.cross) {
    lanes += `<div class="lane">${t('board.lane.quoted')}</div>` + m.quoted.map((r) => `<div class="vr rowin quoted" title="${esc(t('board.quoted.tip'))}"><div class="who">${vlogo(r.venue)}<div><div class="nm"><span class="nt">${esc(venueName(r.venue))}</span></div>${r.min ? `<span class="ps">${esc(t('board.quoted.sub', { amount: amt(r.out.raw, r.out.decimals, 2) }))}</span>` : ''}</div></div><div class="out">${r.min ? esc(t('board.quoted.out', { amount: amt(r.min.raw, r.min.decimals, 2) })) : amt(r.out.raw, r.out.decimals, 2)}<small>${r.min ? t('board.quoted.min') : esc(m.to.symbol)}</small></div><div class="gain"></div></div>`).join('');
  }
  if (m.na.length) {
    const names = m.na.map((r) => venueName(r.venue)).join(', ');
    lanes += `<div class="lane">${t('board.lane.na')}</div><div class="vr rowin na"><div class="who"><span class="lgs">${m.na.map((r) => vlogo(r.venue)).join('')}</span><div><div class="nm"><span class="nt">${esc(t('board.na.merged', { list: names }))}</span></div><span class="ps">${t('board.na.sub')}</span></div></div><div class="out">${t('board.na.out')}</div><div class="gain">${t('board.na.col')}</div></div>`;
  }
  if (m.sim.length && (m.solver.length || (m.quoted.length && !m.cross)) && has('board.unverified')) lanes = `<p class="unv">${esc(t('board.unverified'))}</p>` + lanes;
  const head = o.noHead ? '' : `<div class="bh"><h2 class="bt">${o.title || boardTitle(m)}</h2><span class="meta" title="${esc(stampTitle(m))}">${esc(stamp(m))}</span></div>`;
  const cols = `<div class="bcols"><span>${t(m.cross ? 'board.cross.header' : 'board.col.venue')}</span><span>${t('board.col.get')}</span><span>${m.cross ? '' : t('board.col.vs')}</span></div>`;
  const nr = m.noRoute.length ? `<p class="bnote">${esc(t('board.no_route_venues', { list: m.noRoute.map((r) => venueName(r.venue)).join(', ') }))}</p>` : '';
  const xn = m.cross ? `<p class="bnote">${t('board.cross.note')}</p>` : '';
  const st = m.stale ? `<p class="bnote">${esc(t('board.stale', { age: m.staleAgeS != null ? Math.round(m.staleAgeS) + ' s' : (m.ageS != null ? Math.round(m.ageS / 60) + ' min' : '') }))}</p>` : '';
  el.innerHTML = head + cols + rows.join('') + lanes + nr + xn + st;
  if (o.onPick) el.querySelectorAll('.vr.pick').forEach((r) => r.addEventListener('click', () => o.onPick(r.dataset.id)));
  const ins = [...el.querySelectorAll('.rowin')];
  if (RM || o.static) { ins.forEach((r) => r.classList.add('on')); return; }
  ins.forEach((r) => r.querySelectorAll('.bar i').forEach((i) => { i.dataset.k = i.style.getPropertyValue('--k'); i.style.setProperty('--k', 0); }));
  ins.forEach((r, i) => setTimeout(() => { r.classList.add('on'); r.querySelectorAll('.bar i').forEach((x) => x.style.setProperty('--k', x.dataset.k)); }, (o.delay || 0) + i * 70));
}
/** variant B detail: the best row flashes when the money lands on the stock */
export function pingBest(el) { const b = el.querySelector('.vr.best'); if (!b || RM) return; b.classList.remove('ping'); void b.offsetWidth; b.classList.add('ping'); }

/* ---------- rolling digits (transform only) ---------- */
export function odo(el, str) {
  el.classList.add('odo'); el.innerHTML = '';
  [...str].forEach((ch) => {
    if (/\d/.test(ch)) { const d = document.createElement('span'); d.className = 'd'; d.setAttribute('aria-hidden', 'true'); d.innerHTML = `<span class="s">${'0123456789'.split('').map((x) => `<i>${x}</i>`).join('')}</span>`; el.appendChild(d); }
    else { const c = document.createElement('span'); c.className = 'c'; c.setAttribute('aria-hidden', 'true'); c.textContent = ch; el.appendChild(c); }
  });
}
export function odoSet(el, str, instant) {
  const ds = [...el.querySelectorAll('.d')], digits = [...str].filter((c) => /\d/.test(c));
  const shape = [...el.children].map((n) => (n.classList.contains('d') ? 'd' : 'c')).join('');
  const want = [...str].map((c) => (/\d/.test(c) ? 'd' : 'c')).join('');
  if (ds.length !== digits.length || shape !== want) { odo(el, str); instant = instant || false; }
  let di = 0;
  [...el.children].forEach((n, i) => {
    const ch = str[i];
    if (n.classList.contains('c')) { n.textContent = ch; return; }
    const s = n.firstChild;
    if (instant || RM) s.style.transition = 'none'; else { s.style.transition = ''; s.style.transitionDelay = di * 55 + 'ms'; }
    s.style.transform = `translateY(${-Number(ch)}em)`; di++;
  });
  el.setAttribute('aria-label', str);
}

/* ---------- route rail: you pay → pools in the engine's order → stock ---------- */
export function rail(el, m, o = {}) {
  const r = m.best; const pools = routePools(r);   // execution order, a pool hit twice is one node
  const MAX = o.max || 5;
  const shown = pools.length > MAX ? pools.slice(0, MAX - 1) : pools;
  const extra = pools.length - shown.length;
  el.className = 'rail';
  el.innerHTML = `<div class="track"></div><div class="fill"></div><div class="pk">${tk(m.from)}${esc(sizeLabel(m))}</div>`
    + `<div class="nd end" title="${esc(t('landing.stage.rail_from'))}"><div class="o">${tk(m.from)}</div><div class="t"><b>${amt(m.amountIn.raw, m.amountIn.decimals, 0)}</b><s>${esc(m.from.symbol)}</s></div></div>`
    + shown.map((p, i) => `<div class="nd"><span class="n">${String(i + 1).padStart(2, '0')}</span><div class="o"><img src="${esc(p.logo || '/assets/logos/venues/other.svg')}" alt=""></div><div class="t"><b>${esc(p.name)}</b><s>${esc(p.pair || '')}</s></div></div>`).join('')
    + (extra > 0 ? `<div class="nd"><span class="n">${String(shown.length + 1).padStart(2, '0')}</span><div class="o"><span class="nmore">+${extra}</span></div><div class="t"><b>${esc(poolsLabel(extra))}</b><s>&nbsp;</s></div></div>` : '')
    + `<div class="nd end stock" title="${esc(t('landing.stage.rail_to'))}"><div class="o">${tk(m.to)}</div><div class="t"><b>${r ? amt(r.out.raw, r.out.decimals, 2) : '—'}</b><s>${esc(m.to.symbol)}</s></div></div>`;
  el.setAttribute('aria-hidden', 'true');
  return el;
}
const ezB = (x1, y1, x2, y2) => (tt) => { let a = 0, b = 1; for (let k = 0; k < 24; k++) { const mm = (a + b) / 2; const bx = 3 * x1 * mm * (1 - mm) ** 2 + 3 * x2 * mm * mm * (1 - mm) + mm ** 3; bx < tt ? (a = mm) : (b = mm); } const mm = (a + b) / 2; return 3 * y1 * mm * (1 - mm) ** 2 + 3 * y2 * mm * mm * (1 - mm) + mm ** 3; };
const EZ = ezB(0.45, 0.05, 0.25, 1);
/** draw the line, ride the packet, light pools in order; calls onArrive when the money reaches the stock; returns duration ms */
export function railPlay(el, { onArrive, static: st } = {}) {
  const nds = [...el.querySelectorAll('.nd')], n = nds.length;
  const x = nds.map((d) => d.offsetLeft), W = x[n - 1] - x[0];
  const tr = el.querySelector('.track'), fi = el.querySelector('.fill'), pk = el.querySelector('.pk');
  [tr, fi].forEach((e) => { e.style.left = x[0] + 'px'; e.style.width = W + 'px'; });
  el.style.setProperty('--st', Math.floor(W / Math.max(1, n - 1)) + 'px'); el.style.setProperty('--rw', Math.floor(W) + 'px');
  (el._timers || []).forEach(clearTimeout); el._timers = [];
  if (RM || st) { el.classList.add('static'); pk.style.opacity = 0; onArrive && onArrive(); return 0; }
  el.classList.remove('static');
  nds.forEach((d) => d.classList.remove('on'));
  const D = Math.min(2600, 700 + n * 260);
  nds[0].classList.add('on');
  fi.animate([{ transform: 'scaleX(0)' }, { transform: 'scaleX(1)' }], { duration: D, easing: 'cubic-bezier(.45,.05,.25,1)', fill: 'forwards' });
  const pw = pk.offsetWidth;
  pk.animate([{ transform: `translateX(${x[0] - pw / 2}px)`, opacity: 0 }, { opacity: 1, offset: 0.08 }, { opacity: 1, offset: 0.9 }, { transform: `translateX(${x[n - 1] - pw / 2}px)`, opacity: 0 }], { duration: D, easing: 'cubic-bezier(.45,.05,.25,1)', fill: 'forwards' });
  nds.forEach((d, i) => {
    if (!i) return;
    const target = (x[i] - x[0]) / W; let lo = 0, hi = 1;
    for (let k = 0; k < 20; k++) { const mid = (lo + hi) / 2; EZ(mid) < target ? (lo = mid) : (hi = mid); }
    el._timers.push(setTimeout(() => { d.classList.add('on'); if (i === n - 1 && onArrive) onArrive(); }, lo * D));
  });
  return D;
}

/* ---------- split: ribbons fan out from what you pay, converge into the stock ---------- */
export function splitParts(r, m) {
  const src = r && (r.byDex && r.byDex.length ? r.byDex : r.pools) || [];
  const parts = src.slice(0, 6).map((p) => ({ dex: p.dex, name: p.name, logo: p.logo, pct: p.sharePct, usd: (m.amountIn.usd || 0) * p.sharePct / 100,
    kind: p.dex === 'other' ? '' : p.dex === 'metric' ? t('landing.split.kind.prop') : t('landing.split.kind.pool') }));
  return parts;
}
export function splitSummary(parts) { return parts.map((p) => t('landing.split.summary', { share: nf(p.pct, 0, 0), dex: p.name })).join(' · '); }
export function split(el, parts, o = {}) {
  if (!parts.length) { el.innerHTML = `<p class="empty">${t('empty.split')}</p>`; el.style.height = ''; return; }
  const W = el.clientWidth || 600, rh = o.rh || 48, H0 = parts.length * rh, pad = Math.min(o.pad || 96, W * 0.16), cx0 = pad, cx1 = W - pad;
  el.style.height = H0 + 'px'; el.style.minHeight = '';
  const tot = parts.reduce((s, p) => s + p.pct, 0) || 100, band = Math.min(H0 * 0.3, 72), sc = band / tot;
  let y0 = (H0 - band) / 2, y1 = y0;
  const cols = ['#cbfffc', '#02b9b0', '#fad1ff', '#7ee0d9', '#e6b9f3', '#00827c'];
  const ribs = parts.map((p, i) => {
    const h = Math.max(p.pct * sc, 2), ry = i * rh + rh / 2, a = y0, b2 = y1; y0 += h; y1 += h;
    const L = `M0 ${a} C ${cx0 * 0.6} ${a}, ${cx0 * 0.5} ${ry - h / 2}, ${cx0} ${ry - h / 2} L ${cx0} ${ry + h / 2} C ${cx0 * 0.5} ${ry + h / 2}, ${cx0 * 0.6} ${a + h}, 0 ${a + h} Z`;
    const R = `M${W} ${b2} C ${W - (W - cx1) * 0.6} ${b2}, ${W - (W - cx1) * 0.5} ${ry - h / 2}, ${cx1} ${ry - h / 2} L ${cx1} ${ry + h / 2} C ${W - (W - cx1) * 0.5} ${ry + h / 2}, ${W - (W - cx1) * 0.6} ${b2 + h}, ${W} ${b2 + h} Z`;
    return `<path class="rib" style="transition-delay:${i * 90}ms" d="${L}" fill="${cols[i]}" fill-opacity=".42"/><path class="rib" style="transition-delay:${600 + i * 90}ms" d="${R}" fill="${cols[i]}" fill-opacity=".22"/>`;
  }).join('');
  el.innerHTML = `<svg viewBox="0 0 ${W} ${H0}" preserveAspectRatio="none" aria-hidden="true" style="height:${H0}px;bottom:auto">${ribs}<rect x="-6" y="${(H0 - band) / 2}" width="6" height="${band}" rx="3" fill="#cbfffc"/><rect x="${W}" y="${(H0 - band) / 2}" width="6" height="${band}" rx="3" fill="#fad1ff"/></svg>`
    + parts.map((p, i) => `<div class="srow" style="--x0:${cx0}px;--x1:${W - cx1}px;top:${i * rh + (rh - 40) / 2}px"><img class="vlogo" src="${esc(p.logo || '/assets/logos/venues/other.svg')}" alt=""><span><b>${esc(p.name)}</b><em>${esc(p.kind)}</em></span><span class="u">${p.usd ? usd(p.usd, 0) : ''}</span><span class="p">${nf(p.pct, 1, 1)}%</span></div>`).join('');
  el.classList.remove('on');
  if (RM || o.static) el.classList.add('on'); else requestAnimationFrame(() => requestAnimationFrame(() => el.classList.add('on')));
}
