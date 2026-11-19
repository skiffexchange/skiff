// Live route scheme (design/v2/SPEC.md §5): source chain → bridge → Robinhood Chain → split by DEX → wallet.
// One component for the swap after signing (/api/track), the landing «Live on Robinhood Chain» (real big trades) and the DEV demo.
// A node lights only on a real confirmation (state "done" with time / explorer). Motion is transform + opacity only; the box never
// changes size (fixed height per direction); reduced motion → final state, no packets.
import { t, has, esc, nf, RM, RH_LOGO, short, tokI } from './core.js';

const WALLET_SVG = '<svg width="20" height="20" viewBox="0 0 20 20" fill="none" aria-hidden="true"><rect x="2.5" y="5" width="15" height="11" rx="3" stroke="currentColor" stroke-width="1.5"/><path d="M2.5 8.5h15" stroke="currentColor" stroke-width="1.5"/><circle cx="13.6" cy="12.2" r="1.1" fill="currentColor"/><path d="M5 5l7.5-2.5L14 5" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"/></svg>';
const CHECK = '<svg width="10" height="10" viewBox="0 0 10 10" fill="none" aria-hidden="true"><path d="m2 5.2 2 2 4-4.4" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>';
const MAXLANES = 5;
const hms = (ts) => { const d = new Date(ts * 1000); return [d.getUTCHours(), d.getUTCMinutes(), d.getUTCSeconds()].map((x) => String(x).padStart(2, '0')).join(':'); };

/** lanes from a split list [{name, logo, sharePct}] — same dex summed, top MAXLANES-1 + «other» */
export function lanesOf(split) {
  const by = new Map();
  for (const s of split || []) {
    const k = s.dex === 'other' ? 'other' : (s.name || s.dex || '').replace(/ V\d$/, '');
    const o = by.get(k) || { name: s.dex === 'other' ? null : k, logo: s.logo, pct: 0, other: s.dex === 'other' };
    o.pct += +s.sharePct || 0; by.set(k, o);
  }
  let ls = [...by.values()].filter((x) => x.pct > 0.05).sort((a, b) => (a.other - b.other) || b.pct - a.pct);
  if (ls.length > MAXLANES) { const keep = ls.slice(0, MAXLANES - 1); const rest = ls.slice(MAXLANES - 1).reduce((s, x) => s + x.pct, 0); ls = [...keep, { name: null, logo: null, pct: rest, other: true }]; }
  return ls;
}
const shareS = (p) => (p < 1 ? '<1' : nf(p, 0, 0));
export const laneLabel = (l) => (l.other ? t('swap.track.node.split_other', { share: shareS(l.pct) }) : t('swap.track.node.split_item', { share: shareS(l.pct), dex: l.name }));

/**
 * nodes: [{kind: source|bridge|chain|split|wallet, state: pending|active|done|failed, at, explorer, logo, title, sub, lanes}]
 * opts: {dir: 'h'|'v'|'auto', live: bool (packets keep flowing after done), label}
 */
export function scheme(el, opts = {}) {
  el.classList.add('sch');
  let nodes = [], dir = 'h', W = 0, timers = [], ro = null;
  const isV = () => (opts.dir === 'v' || (opts.dir !== 'h' && el.clientWidth < 620));
  function nodeHtml(n, i) {
    const ic = n.kind === 'wallet' ? `<span class="wi">${WALLET_SVG}</span>` : n.logo ? `<img src="${esc(n.logo)}" alt="">` : '';
    const st = n.state === 'done' ? `<span class="ok">${n.at ? esc(t('swap.track.state.done', { time: hms(n.at) })) : ''}${n.explorer ? ` <a href="${esc(n.explorer)}" target="_blank" rel="noopener">${esc(t('swap.track.explorer'))} ↗</a>` : ''}</span>`
      : n.state === 'failed' ? `<span class="bad">${esc(t('swap.track.state.failed'))}</span>` : n.state === 'off' ? '' : `<span>${esc(t('swap.track.state.pending'))}</span>`;
    return `<div class="sn k-${n.kind} s-${n.state || 'pending'}" data-i="${i}"><div class="o"><i class="halo"></i><span class="c">${ic}</span><b class="tick">${CHECK}</b></div><div class="tx"><b${/^0x[0-9a-f]{4}…/i.test(n.title || '') ? ' class="adr"' : ''}>${esc(n.title || '')}</b>${n.sub ? `<small>${n.subTok ? tokI(n.subTok, 'sic') : ''}${esc(n.sub)}</small>` : ''}<em>${st}</em></div></div>`;
  }
  function build() {
    dir = isV() ? 'v' : 'h'; el.dataset.dir = dir; W = el.clientWidth;
    const split = nodes.find((n) => n.kind === 'split');
    const lanes = split ? split.lanes || [] : [];
    const flow = nodes.filter((n) => n.kind !== 'split');
    let html = '';
    if (dir === 'h') {
      // column centers: split gets ~2.4 units between the node before it and the one after it
      const units = []; nodes.forEach((n) => units.push(n.kind === 'split' ? 2.6 : 1));
      const tot = units.reduce((a, b) => a + b, 0), pad = Math.min(48, W * 0.05), span = W - pad * 2; let acc = pad;
      const cx = units.map((u) => { const c = acc + (span * u) / tot / 2; acc += (span * u) / tot; return c; });
      const H = el.clientHeight, big = !!opts.big, R = big ? 34 : 26, cy = Math.round(big ? H * 0.46 : Math.min(118, H * 0.42));
      el.classList.toggle('big', big);
      el.style.setProperty('--cy', cy + 'px');
      // trunk segments between consecutive flow nodes (split is drawn as lanes)
      let segs = '';
      for (let i = 0; i < nodes.length - 1; i++) {
        const a = nodes[i], b = nodes[i + 1];
        if (a.kind === 'split' || b.kind === 'split') continue;
        const x0 = cx[i] + R, x1 = cx[i + 1] - R;
        segs += `<div class="seg" data-to="${i + 1}" style="left:${x0}px;top:${cy}px;width:${Math.max(0, x1 - x0)}px;--w:${Math.max(0, x1 - x0)}px"><i class="fill"></i><i class="pk"></i></div>`;
      }
      let lanesHtml = '';
      if (split) {
        const si = nodes.indexOf(split), xa = cx[si - 1] + R, xb = cx[si + 1] - R;
        // P4: room for the «Split by DEX» title above the top lane — the title sits at one height (computed for the most lanes) and
        // keeps ≥ 8 px from the top pill for any lane count
        const TT = big ? 50 : 36, gapOf = (k) => Math.min(big ? 74 : 44, (H - (big ? 150 : 70) - TT) / Math.max(1, k));
        const gap = gapOf(lanes.length), top = cy - (gap * (lanes.length - 1)) / 2, topMax = cy - (gapOf(MAXLANES) * (MAXLANES - 1)) / 2;
        const l0 = xa + (xb - xa) * 0.22, l1 = xb - (xb - xa) * 0.22;
        let paths = '';
        lanes.forEach((l, k) => {
          const y = top + k * gap, sw = Math.max(1.25, Math.min(big ? 10 : 7, 1 + l.pct / (big ? 10 : 14)));
          const d1 = `M${xa} ${cy} C ${xa + (l0 - xa) * 0.55} ${cy}, ${l0 - (l0 - xa) * 0.45} ${y}, ${l0} ${y}`;
          const d2 = `M${l1} ${y} C ${l1 + (xb - l1) * 0.45} ${y}, ${xb - (xb - l1) * 0.55} ${cy}, ${xb} ${cy}`;
          paths += `<path class="b" d="${d1}" stroke-width="${sw}"/><path class="b" d="${d2}" stroke-width="${sw}"/><path class="l in" d="${d1}" stroke-width="${sw}"/><path class="l out" d="${d2}" stroke-width="${sw}"/>`;
          lanesHtml += `<div class="lane" style="left:${l0}px;top:${y}px;width:${l1 - l0}px;--sw:${sw}px;--lw:${l1 - l0}px"><i class="base"></i><i class="fill"></i><i class="pk" style="--d:${k * 0.23}s"></i><span class="lb">${l.logo ? `<img src="${esc(l.logo)}" alt="">` : '<span class="ot"></span>'}${esc(laneLabel(l))}</span></div>`;
        });
        lanesHtml = `<svg class="fan" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" aria-hidden="true">${paths}</svg>${lanesHtml}${lanes.length > 1 ? `<div class="stitle" style="left:${(l0 + l1) / 2}px;top:${Math.max(0, topMax - TT)}px">${esc(split.title || t('swap.track.node.split'))}</div>` : ''}`;
      }
      html = segs + lanesHtml + flow.map((n) => { const i = nodes.indexOf(n); return nodeHtml(n, i).replace('class="sn', `style="left:${cx[i]}px" class="sn`); }).join('');
    } else {
      // vertical: nodes stacked, the split is a list of share bars between the chain and the wallet
      let y = 0; const rows = []; const P = opts.pitch || 104, LP = opts.lanePitch || 34, M = opts.maxLanes || 99;
      nodes.forEach((n, i) => {
        if (n.kind === 'split') {
          // P3: optional lane cap (the rest joins «other»), lane pitch; no «Split by DEX» over a single venue; with minLanes the slot
          // keeps room for the most lanes and the lanes sit in its middle — the wallet node never moves 
          let ls = n.lanes || [];
          if (ls.length > M) { const rest = ls.slice(M - 1).reduce((acc, x) => acc + (x.pct || 0), 0); ls = [...ls.slice(0, M - 1), { name: null, logo: null, pct: rest, other: true }]; }
          const one = ls.length <= 1, slot = 34 + Math.max(ls.length, opts.minLanes || 0) * LP, used = (one ? 0 : 34) + ls.length * LP;
          const off = opts.minLanes ? Math.max(0, (slot - used) / 2) : 0;
          rows.push(`<div class="vsplit${one ? ' one' : ''}" data-i="${i}" style="top:${y + off}px">${one ? '' : `<b class="st2">${esc(n.title || t('swap.track.node.split'))}</b>`}${ls.map((l, k) => `<div class="vl"${LP !== 34 ? ` style="height:${LP}px"` : ''}><span class="lb">${l.logo ? `<img src="${esc(l.logo)}" alt="">` : '<span class="ot"></span>'}${esc(laneLabel(l))}</span><span class="bar"><i style="--k:${(l.pct / 100).toFixed(3)};--d:${k * 70}ms"></i></span></div>`).join('')}</div>`);
          y += (opts.minLanes ? slot : used) + 14;
        } else { rows.push(nodeHtml(n, i).replace('class="sn', `style="top:${y}px" class="sn`)); y += P; }
      });
      if (P < 104 && nodes.length && nodes[nodes.length - 1].kind !== 'split') y -= P - 64;   // the trunk ends at the last node (no tail below the wallet)
      el.style.setProperty('--vh', y + 'px');
      html = `<div class="vtrunk"><i class="fill"></i><i class="pk"></i></div>` + rows.join('');
    }
    el.innerHTML = html;
    el.setAttribute('role', 'list');
    paint(true);
  }
  /** state → classes; lines fill up to the furthest done node; the active node breathes; packets run on the live segment */
  function paint(instant) {
    const lastDone = nodes.reduce((m, n, i) => (n.state === 'done' ? i : m), -1);
    const split = nodes.find((n) => n.kind === 'split'), si = split ? nodes.indexOf(split) : -1;
    el.classList.toggle('static', !!(RM || instant && opts.static));
    el.querySelectorAll('.sn').forEach((s) => {
      const i = +s.dataset.i, n = nodes[i];
      s.className = `sn k-${n.kind} s-${n.state || 'pending'}` + (i === lastDone + 1 && n.state !== 'failed' && !opts.finished ? ' act' : '');
    });
    el.querySelectorAll('.seg').forEach((g) => { const to = +g.dataset.to; g.classList.toggle('on', to <= lastDone); g.classList.toggle('live', to === lastDone + 1 && !opts.finished); });
    const lit = si >= 0 && nodes[si].state === 'done';
    el.classList.toggle('lanes-on', lit);
    el.classList.toggle('lanes-live', lit && (opts.live || nodes[nodes.length - 1].state !== 'done'));
    if (dir === 'v') {
      const n = nodes.length; const tr = el.querySelector('.vtrunk');
      const doneY = (() => { const s = el.querySelector(`.sn[data-i="${lastDone}"]`) || el.querySelector(`.vsplit[data-i="${lastDone}"]`); return s ? s.offsetTop + 22 : 0; })();
      if (tr) { tr.style.setProperty('--k', n ? Math.min(1, Math.max(0, (doneY - tr.offsetTop) / Math.max(1, tr.clientHeight))).toFixed(3) : 0);   // P1: the trunk ends on the last node, so the fill is measured on the trunk itself
 tr.classList.toggle('live', lastDone < n - 1 && !opts.finished); }
      el.querySelectorAll('.vsplit').forEach((v) => v.classList.toggle('on', lit));
    }
    el.setAttribute('aria-label', nodes.filter((x) => x.kind !== 'split').map((x) => t('swap.track.aria', { node: x.title || '', state: x.state === 'done' ? t('swap.track.state.done', { time: x.at ? hms(x.at) : '' }) : t('swap.track.state.' + (x.state === 'failed' ? 'failed' : 'pending')) })).join('. '));
  }
  const S = {
    /** replace the whole scheme (new trade / new swap) */
    set(ns, o = {}) { Object.assign(opts, o); nodes = ns.map((x) => ({ ...x })); build(); return S; },
    /** update node states without rebuilding (same shape) */
    update(ns, o = {}) {
      Object.assign(opts, o);
      const same = ns.length === nodes.length && ns.every((n, i) => n.kind === nodes[i].kind && JSON.stringify(n.lanes || null) === JSON.stringify(nodes[i].lanes || null) && (n.title || '') === (nodes[i].title || '') && (n.sub || '') === (nodes[i].sub || ''));
      nodes = ns.map((x) => ({ ...x }));
      if (!same) { build(); return S; }
      // text of the state lines (time, explorer) changes in place
      el.querySelectorAll('.sn').forEach((s) => { const i = +s.dataset.i; const fresh = document.createElement('div'); fresh.innerHTML = nodeHtml(nodes[i], i); const em = fresh.querySelector('em'); const old = s.querySelector('em'); if (em && old && em.innerHTML !== old.innerHTML) old.innerHTML = em.innerHTML; });
      paint(false); return S;
    },
    /** play a finished trade: nodes light one after another (landing / demo); returns total ms */
    play(ns, { step = 650, onDone } = {}) {
      timers.forEach(clearTimeout); timers = [];
      const fin = ns.map((x) => ({ ...x }));
      if (RM) { S.set(fin, { finished: true, live: false }); onDone && onDone(); return 0; }
      S.set(fin.map((x) => ({ ...x, state: 'pending' })), { finished: false, live: true });
      let k = 0;
      fin.forEach((n, i) => { timers.push(setTimeout(() => { S.update(fin.map((x, j) => (j <= i ? x : { ...x, state: 'pending' })), { finished: i === fin.length - 1 }); if (i === fin.length - 1 && onDone) onDone(); }, 380 + (k++) * step + (n.kind === 'split' ? 120 : 0))); });
      return 380 + fin.length * step;
    },
    stop() { timers.forEach(clearTimeout); timers = []; },
    destroy() { S.stop(); ro && ro.disconnect(); el.innerHTML = ''; },
    get nodes() { return nodes; },
  };
  let lw = 0, lh = 0;
  ro = new ResizeObserver(() => { if (nodes.length && (Math.abs(el.clientWidth - lw) > 8 || (dir === 'h' && Math.abs(el.clientHeight - lh) > 8))) { lw = el.clientWidth; lh = el.clientHeight; build(); } });
  ro.observe(el);
  return S;
}

/* ---------- node builders ---------- */
/** a finished on-chain trade from /api/trades (landing «Live on Robinhood Chain», DEV demo without /api/track) */
export function tradeNodes(x, tok) {
  const tot = (x.pools || []).reduce((s, p) => s + (+p.usd || 0), 0) || 1;
  const VEN = Object.fromEntries((x.venues || []).map((v) => [v.dex, v]));
  const split = (x.pools || []).map((p) => ({ dex: p.dex, name: (VEN[p.dex] && VEN[p.dex].name) || p.dex, logo: VEN[p.dex] && VEN[p.dex].logo, sharePct: p.sharePct != null ? p.sharePct : (100 * (+p.usd || 0)) / tot }));
  const amtS = tok && x.amount ? fmtAmt(x.amount) : '';
  return [
    { kind: 'chain', state: 'done', at: x.ts, explorer: x.explorer, logo: RH_LOGO, title: t('swap.track.node.chain'), sub: t('swap.track.node.chain_sub', { ticker: x.stock }) },
    { kind: 'split', state: 'done', at: x.ts, title: t('swap.track.node.split'), lanes: lanesOf(split) },
    { kind: 'wallet', state: 'done', at: x.ts, subTok: x.wallet ? tok : null, title: x.wallet ? t('landing.live.node.wallet', { address: short(x.wallet) }) : t('swap.track.node.wallet_sub', { amount: amtS, ticker: x.stock }), sub: x.wallet ? t('swap.track.node.wallet_sub', { amount: amtS, ticker: x.stock }) : '' },
  ];
}
function fmtAmt(a) { const v = Number(BigInt(a.raw)) / 10 ** a.decimals; return nf(v, v >= 100 ? 2 : 4, 2); }

/** /api/track answer → nodes (contract in NEW-PITCH.md) */
export function trackNodes(tr, ctx = {}) {
  return (tr.nodes || []).map((n) => {
    const chainName = (n.chain && n.chain.name) || ctx.chainName || '';
    const amt = n.amount ? `${fmtAmt(n.amount)}` : '';
    const base = { kind: n.kind, state: n.state === 'done' ? 'done' : n.state === 'failed' ? 'failed' : 'pending', at: n.at, explorer: n.explorer };
    const subTok = n.amount ? ((ctx.tokOf && ctx.tokOf(n.amount)) || { symbol: n.amount.symbol, kind: 'token' }) : null;   // P3: every ticker in a node with its icon
    if (n.kind === 'source') return { ...base, logo: n.chain && n.chain.id === 4663 ? RH_LOGO : n.chain && n.chain.logo, subTok, title: t('swap.track.node.source', { chain: chainName }), sub: amt ? t('swap.track.node.source_sub', { amount: amt, sym: (n.amount && n.amount.symbol) || ctx.fromSym || '' }) : '' };
    if (n.kind === 'bridge') return { ...base, logo: n.bridge && n.bridge.logo, title: t('swap.track.node.bridge', { bridge: (n.bridge && n.bridge.name) || '' }), sub: t('swap.track.node.bridge_sub') };
    if (n.kind === 'chain') return { ...base, logo: RH_LOGO, title: t('swap.track.node.chain'), sub: t('swap.track.node.chain_sub', { ticker: ctx.ticker || (n.amount && n.amount.symbol) || '' }) };
    if (n.kind === 'split') return { ...base, title: t('swap.track.node.split'), lanes: lanesOf(n.split || ctx.split || []) };
    return { ...base, subTok: amt ? subTok : null, title: ctx.walletTitle || (ctx.showAddress && n.address ? t('landing.live.node.wallet', { address: short(n.address) }) : n.contract && !n.address ? (has('scheme.wallet.contract') ? t('scheme.wallet.contract') : t('swap.track.node.wallet')) : t('swap.track.node.wallet')), sub: amt ? t('swap.track.node.wallet_sub', { amount: amt, ticker: (n.amount && n.amount.symbol) || ctx.ticker || '' }) : (ctx.expected || '') };
  });
}
