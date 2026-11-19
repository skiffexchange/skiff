// The swap widget (every page): debounced quotes over SSE with stale requests aborted, best route first, the rest streaming into fixed
// slots; then review → sign in the person's own wallet → pending → success / failure. Nine states, never a layout jump inside the card.
import { t, th, has, esc, amt, nf, usd, toRaw, fmtTyped, blk, hhmm, plural, tokens, tokI, chainLogo, vlogo, venueName, isStock, api, ApiError, errLine,
  session, sheet, toast, X_SVG, LOCAL, QS, RM, short, pct, RH_LOGO, netTokens } from './core.js';
import { fromQuote, rowVs, stepsHtml, stamp, stampTitle, poolCount, pairT } from './board.js';
import { race as mkRace, emptyLine, onSources } from './race.js';
import { scheme, lanesOf, trackNodes } from './scheme.js';
import { pick } from './picker.js';
import { state as W, onWallet, connect, busyConnect, sign } from './wallet.js';

// public RPCs of the pay chains (same as the sign layer's chains.js); read-only balance, no key
const RPCS = { 4663: 'https://rpc.mainnet.chain.robinhood.com', 8453: 'https://mainnet.base.org', 42161: 'https://arb1.arbitrum.io/rpc', 1: 'https://ethereum-rpc.publicnode.com',
  10: 'https://mainnet.optimism.io', 56: 'https://bsc-dataseed.bnbchain.org', 137: 'https://polygon-bor-rpc.publicnode.com' };   // other networks: chains[].rpc[0] of /api/tokens (sign README)
const RH = 4663, REFRESH_S = 30, BUILD_TTL = 60, GAS_RESERVE = 500000000000000n;   // 0.0005 ETH kept for gas on Max
const SVG = {
  gear: '<svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden="true"><path d="M2 5h7M12 5h2M2 11h2M7 11h7" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/><circle cx="10.5" cy="5" r="1.6" stroke="currentColor" stroke-width="1.4"/><circle cx="5.5" cy="11" r="1.6" stroke="currentColor" stroke-width="1.4"/></svg>',
  down: '<svg width="14" height="14" viewBox="0 0 14 14" fill="none" aria-hidden="true"><path d="M7 2v10M3.5 8.5 7 12l3.5-3.5" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  check: '<svg width="14" height="14" viewBox="0 0 14 14" fill="none" aria-hidden="true"><path d="m3 7.4 2.6 2.6L11 4.6" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  checkL: '<svg width="24" height="24" viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="m5.5 12.5 4.2 4.2L18.5 8" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  xL: '<svg width="22" height="22" viewBox="0 0 22 22" fill="none" aria-hidden="true"><path d="M6 6l10 10M16 6 6 16" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>',
  back: '<svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden="true"><path d="M10 3 5 8l5 5" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>',
};

/** opts: {rows: show streaming venue rows inside the card, details: link to the full board, onUpdate(model|null, stream), to, from, amount} */
// / P3: at rest the route slot says what to do — the same line as the landing card, the source count from /api/reach
const restHint = () => `<p class="rhint" data-empty>${esc(emptyLine())}</p>`;
onSources(() => document.querySelectorAll('.rhint[data-empty]').forEach((e) => { e.textContent = emptyLine(); }));
export function mountSwap(root, opts = {}) {
  const S = { tab: 'swap', from: null, to: null, amount: '', raw: null, slip: 0.005, receiver: null, phase: 'empty', m: null, st: null, sel: null,
    err: null, ctl: null, deb: null, refreshAt: 0, refreshT: null, bal: null, chains: {}, build: null, mode: null, fresh: false, forced: null };
  root.classList.add('sw', 'v2', 'boot');
  if (opts.compact) root.classList.add('cpt');
  // v2 (design/v2/SPEC.md §3): no Swap / Cross-chain tabs — the pay network is chosen with the pay token; one object, no boxes in boxes
  root.innerHTML = `<div class="sw-h"><button class="gear" aria-label="${esc(t('swap.settings'))}" title="${esc(t('swap.settings'))}">${SVG.gear}</button></div>
  <div class="body">
    <div class="fld fp"><div class="lb"><span>${t('swap.pay.label')}</span><em class="bal"></em></div><div class="ln"><input class="amt" inputmode="decimal" autocomplete="off" placeholder="0" aria-label="${esc(t('swap.amount_aria'))}"><button class="tok tp" aria-label="${esc(t('swap.pick.title_pay'))} USDG"><span class="tokwrap"><img class="ic" src="/assets/logos/tokens/usdg.png" alt=""></span>USDG<span class="chev" aria-hidden="true">▾</span></button></div><div class="sub"><span class="on"></span><button class="po">${t('swap.pay_other_short')}&nbsp;›</button></div></div>
    <div class="div"><button class="flip" aria-label="${esc(t('swap.flip'))}">${SVG.down}</button></div>
    <div class="fld fg"><div class="lb"><span>${t('swap.get.label')}</span><em class="gl"></em></div><div class="ln"><span class="amt get dim">0</span><button class="tok tg" aria-label="${esc(t('swap.pick.title_get'))} ${esc(/^0x/i.test(String(opts.to || QS.get('to') || '')) ? '' : String(opts.to || QS.get('to') || 'NVDA').toUpperCase())}"><span class="tokwrap">${(opts.to || QS.get('to')) ? `<span class="ic stk stk-t"></span>` : '<span class="ic stk"><img src="/assets/logos/stocks/NVDA.png" alt=""></span>'}</span>${esc(/^0x/i.test(String(opts.to || QS.get('to') || '')) ? '' : String(opts.to || QS.get('to') || 'NVDA').toUpperCase())}<span class="chev" aria-hidden="true">▾</span></button></div><div class="sub"><span class="usd"></span><span class="bo"></span></div></div>
    <button class="cta off" disabled>${t('swap.cta.enter')}</button>
    <div class="nslot"></div>
    <div class="det${!opts.raceEl && opts.rows ? ' rc' : ''}">${!opts.raceEl && opts.rows ? '' : `<div class="rslot">${restHint()}</div>${feesHtml(null, true)}`}</div>
  </div>
  <div class="pane" hidden></div><p class="sr" aria-live="polite" data-live></p>`;
  const $ = (s) => root.querySelector(s);
  // phones and the compact card: the window never shrinks back while a swap is in progress (audit v4 S5) — only on a fresh, empty start
  const NARROW = matchMedia('(max-width: 979px)');
  function hold() {
    if (!(NARROW.matches || opts.compact)) return;
    if (S.phase === 'empty') { S.maxH = 0; root.style.minHeight = ''; return; }
    // narrow screens draw the scheme in the window: from the signature on, the window holds the height of its tallest end state
    // (result / failure ≤ 625 px measured 17.11 after the compact layout) so the stage below never jumps when the swap ends (design v5 S6)
    const endH = NARROW.matches && !opts.trackEl && /^(pending|success|fail)$/.test(S.phase) ? 626 : 0;
    S.maxH = Math.max(S.maxH || 0, root.offsetHeight, endH); root.style.minHeight = S.maxH + 'px';
  }
  const inp = $('.fp .amt'), getEl = $('.fg .amt'), cta = $('.cta'), det = $('.det'), nslot = $('.nslot'), prog = { set innerHTML(h) { const g = $('.fg .gl'); if (g) { g.innerHTML = h; g.classList.toggle('pg', !!h); } } }, pane = $('.pane'), body = $('.body');
  // the race: on a stage next to the card (/app, stock pages) or compact inside the card (landing)
  let raceEl = opts.raceEl || null, inlineRace = false;
  if (!raceEl && opts.rows) { raceEl = document.createElement('div'); inlineRace = true; }
  const RC = raceEl ? mkRace(raceEl, { rows: inlineRace ? 3 : 6, onPick: inlineRace ? null : (id) => { S.sel = id; paint(); RC.select(id); opts.onPick && opts.onPick(id); } }) : null;
  if (inlineRace) { det.appendChild(raceEl); const tl = document.createElement('div'); tl.className = 'rtail'; det.appendChild(tl); RC.idle(); }   // the race holds its place from the first frame
  let srcSeen = false;

  /* ---------- tokens ---------- */
  const tokBtn = (el, tk) => { el.innerHTML = `${tokI(tk, 'ic', true, S.chains)}${esc(tk.symbol)}<span class="chev" aria-hidden="true">▾</span>`; el.setAttribute('aria-label', `${t(el.classList.contains('tp') ? 'swap.pick.title_pay' : 'swap.pick.title_get')} ${tk.symbol}`); };
  /** /app?to=0x… (the Buy of /tokens, a shared link): a network token from the engine's list, else read by address — always as «not on our list» */
  async function addrToken(a) {
    try { const n = await netTokens(); const x = n.all.find((y) => y.address === a); if (x) return x; } catch { /* list down: read the address */ }
    try {
      const d = await api('/api/token/' + a + '?soft=1', { cache: 300000 });
      if (!d || d.ok === false || d.listed) return null;
      return { chain: RH, address: d.address, symbol: String(d.symbol || 'TOKEN').slice(0, 16), name: String(d.name || d.symbol || '').slice(0, 48), decimals: d.decimals,
        kind: 'custom', logo: /^(\/api\/img\/[0-9a-f]{24}|\/assets\/logos\/(net|chains|src)\/[0-9A-Za-z_.-]+\.(png|jpe?g|webp|gif|avif)(\?v=[0-9a-f]+)?)$/.test(d.logo || '') ? d.logo : null, price: null, custom: true, clash: !!d.clash, lookalike: !!d.lookalike };
    } catch { return null; }
  }
  async function init() {
    const T = await tokens();
    S.chains = T.byChain;
    const qTo = opts.to || QS.get('to'), qFrom = QS.get('from'), qCh = +(QS.get('fromChain') || 0);
    // a ticker set before the index loaded (landing hero) wins; a token not on our list keeps its own record (never matched to a stock by symbol)
    S.to = (S.to && (S.to.custom ? S.to : T.find(RH, S.to.symbol))) || (qTo && T.find(RH, qTo)) || (qTo && /^0x[0-9a-fA-F]{40}$/.test(qTo) && await addrToken(qTo.toLowerCase())) || T.find(RH, 'NVDA');
    if (qCh && qCh !== RH && qFrom) { S.tab = 'cross'; S.from = T.find(qCh, qFrom) || T.find(8453, 'USDC'); }
    else S.from = (qFrom && T.find(RH, qFrom)) || T.find(RH, 'USDG');
    const a = opts.amount || QS.get('amount');
    if (a && /^[\d.,]+$/.test(a)) { inp.value = fmtTyped(a); S.amount = inp.value; }
    paintTokens(); setTab(S.tab, true); paint();
    const unboot = () => root.classList.remove('boot');   // P6: the insides show with their first real layout and the real font (no shift on load); no rAF (background tabs)
    if (document.fonts && document.fonts.status !== 'loaded') { document.fonts.ready.then(unboot); setTimeout(unboot, 1500); } else unboot();
    if (LOCAL && QS.get('state')) return forceState(QS.get('state'));
    if (S.amount) requote(0);
  }
  function paintTokens() {
    tokBtn($('.tp'), S.from); tokBtn($('.tg'), S.to);
    const c = S.chains[S.from.chain];
    const cl = c ? chainLogo(c) : null;
    $('.fp .on').innerHTML = `${cl ? `<img src="${esc(cl)}" alt="">` : ''}${esc(t('swap.on_chain', { chain: c ? c.name : '' }))}`;
    root.classList.toggle('xtab', S.from.chain !== RH); S.tab = S.from.chain !== RH ? 'cross' : 'swap'; $('.flip').disabled = S.tab === 'cross';
    $('.fg .gl').textContent = gl();
    loadBalance();
  }
  const gl = () => (S.to.kind === 'stock' ? t('swap.get.kind.stock') : S.to.kind === 'etf' ? t('swap.get.kind.etf') : t('swap.get.kind.crypto'));
  /** the pay network decides same-chain vs cross-chain; no tabs (v2) */
  function setTab(tab) {
    S.tab = tab; root.classList.toggle('xtab', tab === 'cross');
    $('.flip').disabled = tab === 'cross';
  }
  const pickPay = async (chainsFirst) => {
    const x = await pick({ side: 'pay', chain: S.from.chain, chainsFirst });
    if (x && !x.cross) { if (x.address === S.to.address && x.chain === RH) S.to = S.from; S.from = x; setTab(x.chain === RH ? 'swap' : 'cross'); paintTokens(); requote(0); }
  };
  $('.po').addEventListener('click', () => pickPay(true));
  $('.tp').addEventListener('click', () => pickPay(false));
  $('.tg').addEventListener('click', async () => { const x = await pick({ side: 'get' }); if (x) { if (x.address === S.from.address && x.chain === S.from.chain) S.from = S.to; S.to = x; paintTokens(); requote(0); opts.onToken && opts.onToken(x); } });
  $('.flip').addEventListener('click', () => { if (S.tab === 'cross' || !isStock(S.from) && !isStock(S.to) && S.to.chain !== RH) return; [S.from, S.to] = [S.to, S.from]; paintTokens(); requote(0); opts.onToken && opts.onToken(S.to); });
  $('.gear').addEventListener('click', settings);
  inp.addEventListener('input', () => {
    const pos = inp.selectionStart, before = inp.value.length;
    let v = fmtTyped(inp.value);
    const dot = v.indexOf('.'); if (dot >= 0 && S.from && v.length - dot - 1 > S.from.decimals) v = v.slice(0, dot + 1 + S.from.decimals);   // never more decimals than the token has
    inp.value = v; try { const p = Math.max(0, pos + (v.length - before)); inp.setSelectionRange(p, p); } catch { /* type */ }
    S.amount = v; touch();
    requote(300);
  });

  function touch() { S.touched = true; S.pv = null; opts.onInput && opts.onInput(); }
  /* ---------- balance (public node of Robinhood Chain; other chains: none shown) ---------- */
  async function loadBalance() {
    S.bal = null; paintBal();
    const meta = S.chains[S.from.chain], rpc = (meta && meta.rpc && [].concat(meta.rpc)[0]) || RPCS[S.from.chain];
    if (!W.account || !rpc) return;   // Solana and unknown chains: no balance line
    try {
      const isNative = /^0x0{40}$/.test(S.from.address);
      const body = isNative ? { jsonrpc: '2.0', id: 1, method: 'eth_getBalance', params: [W.account, 'latest'] }
        : { jsonrpc: '2.0', id: 1, method: 'eth_call', params: [{ to: S.from.address, data: '0x70a08231' + W.account.slice(2).padStart(64, '0') }, 'latest'] };
      const ch = S.from.chain, tokAddr = S.from.address;
      const r = await fetch(rpc, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) }).then((x) => x.json());
      if (S.from.chain !== ch || S.from.address !== tokAddr) return;   // the token changed while we were reading
      S.bal = BigInt(r.result || '0x0').toString(); paintBal();
    } catch { S.bal = 'na'; paintBal(); }   // the network's RPC didn't answer: say so instead of hiding the balance (audit v4)
  }
  function paintBal() {
    const el = $('.fp .bal');
    if (S.bal == null) { el.innerHTML = ''; return; }
    if (S.bal === 'na') { el.className = 'bal'; el.innerHTML = `<span class="bt" title="${esc(t('swap.balance_na_tip', { chain: (S.chains[S.from.chain] || {}).name || '', sym: S.from.symbol }))}">${esc(t('swap.balance_na'))}</span>`; return; }   // no Max / Half
    const low = S.raw && BigInt(S.raw) > BigInt(S.bal);
    el.className = 'bal' + (low ? ' warn' : '');
    el.innerHTML = `<span class="bt">${tokI(S.from, 'ic xs')}${esc(t(low ? 'swap.balance_low' : 'swap.balance', { amount: amt(S.bal, S.from.decimals, 2), sym: S.from.symbol }))}</span><button type="button" class="mxb">${t('swap.max')}</button>`;
    el.querySelector('.mxb').onclick = () => {
      let v = BigInt(S.bal);
      if (/^0x0{40}$/.test(S.from.address)) { const c0 = chosen(); const nfee = c0 && nativeFeeOf(c0) ? BigInt(nativeFeeOf(c0).raw) : 0n; const keep = GAS_RESERVE + nfee; v = v > keep ? v - keep : 0n; }   // leave the network coin for gas and the route's own fee
      if (v <= 0n) return;
      const d = S.from.decimals, sc = 10n ** BigInt(d), ip = v / sc, fp = (v % sc).toString().padStart(d, '0').replace(/0+$/, '');
      inp.value = fmtTyped(ip.toString() + (fp ? '.' + fp : '')); S.amount = inp.value; touch(); requote(0);
    };
  }
  onWallet(() => { if (S.from) { loadBalance(); if (S.raw && S.phase !== 'pending') requote(0); else paint(); } });

  /* ---------- quotes: debounce 300 ms, abort stale, SSE stream ---------- */
  function requote(delay = 300, quiet = false) {
    clearTimeout(S.deb); clearTimeout(S.refreshT); S.quotedAt = 0;
    if (S.ctl) { S.ctl.abort(); S.ctl = null; }
    if (S.phase === 'review' || S.phase === 'pending') return;
    S.raw = S.from ? toRaw(S.amount, S.from.decimals) : null;
    if (!quiet) { S.retried = false; if (S.phase !== 'error') S.autoRetried = false; }
    paintBal();
    if (!S.raw) { S.phase = 'empty'; S.m = null; S.st = null; S.err = null; if (RC) RC.idle(); paint(); opts.onUpdate && opts.onUpdate(null); return; }
    if (!quiet) { S.phase = 'quoting'; S.m = null; S.sel = null; S.st = { total: 0, routes: [], base: null, block: null, ready: false }; S.err = null; paint(); }
    S.deb = setTimeout(() => { run(quiet).catch(() => {}); }, delay);   // an aborted stream never surfaces as a page error
  }
  async function run(quiet) {
    const ctl = new AbortController(); S.ctl = ctl;
    const p = new URLSearchParams({ fromChain: S.from.chain, fromToken: S.from.address, toChain: RH, toToken: S.to.address, amount: S.raw, fromDecimals: S.from.decimals, toDecimals: S.to.decimals });
    if (W.account && !W.dev && S.from.chain === RH) p.set('fromAddress', W.account);
    if (W.account && W.dev && S.from.chain === RH) p.set('fromAddress', W.account);
    if (S.receiver) p.set('toAddress', S.receiver);
    const stq = quiet ? { total: 0, routes: [], base: null, block: null } : S.st;
    if (RC && !quiet) { srcSeen = false; RC.reset({ n: 0, ticker: S.to.symbol, cross: S.from.chain !== RH }); }
    try {
      const res = await fetch('/api/quote/stream?' + p, { signal: ctl.signal, headers: { accept: 'text/event-stream' } });
      if (!res.ok) { let j = {}; try { j = await res.json(); } catch { /* none */ } throw new ApiError(j.error || 'generic', res.status, j); }
      const rd = res.body.getReader(), dec = new TextDecoder(); let buf = '';
      for (;;) {
        const { value, done } = await rd.read(); if (done) break;
        buf += dec.decode(value, { stream: true }).replace(/\r/g, '');
        let i;
        while ((i = buf.indexOf('\n\n')) >= 0) {
          const chunk = buf.slice(0, i); buf = buf.slice(i + 2);
          let ev = 'message', data = '';
          for (const line of chunk.split('\n')) { if (line.startsWith('event:')) ev = line.slice(6).trim(); else if (line.startsWith('data:')) data += line.slice(5).trim(); }
          if (!data || ctl.signal.aborted) continue;
          const d = JSON.parse(data);
          if (ev === 'meta') { if (RC && !quiet && d.cached) RC.cached(); stq.total = d.routesFound || 0; stq.block = d.block; stq.insufficient = d.insufficientBalance; if (RC && !quiet && d.routesFound) RC.hint(d.routesFound); }
          else if (ev === 'routes_found') { stq.total = (d.routesFound || 0) + stq.routes.length; stq.insufficient = d.insufficientBalance; stq.unavail = d.routesUnavailable; if (RC && !quiet && !srcSeen && d.routesFound) RC.hint(d.routesFound); }
          else if (ev === 'source') { if (RC && !quiet) { srcSeen = true; RC.source(d); } }
          else if (ev === 'baseline') { stq.base = d.baseline; if (RC && !quiet && !srcSeen) RC.baseline(d.baseline); }
          else if (ev === 'route') { stq.routes.push({ ...d.route, group: d.group }); if (RC && !quiet && !srcSeen) RC.route(d.route, d.group); }
          else if (ev === 'error') throw new ApiError(d.error || 'generic', 0, d);
          else if (ev === 'done') {
            S.m = fromQuote(d); S.st = stq; S.phase = 'ready'; S.err = null; S.sel = null; S.fresh = quiet; S.m.routerUnav = d.routerUnavailable || null;
            if (!quiet) announce();
            if (RC && !quiet) { if (!srcSeen) { (d.routes || []).forEach((r) => RC.route(r, 'routes')); (d.quotedOnly || []).forEach((r) => RC.route(r, 'quotedOnly')); if (d.baseline) RC.baseline(d.baseline); } const c = chosen(); RC.done(S.m, c && c.r.id); }
          }
          if (!quiet || ev === 'done') { paint(); opts.onUpdate && opts.onUpdate(S.m && ev === 'done' ? S.m : null, stq, d, ev); }
        }
      }
      if (!quiet && S.phase === 'quoting' && !ctl.signal.aborted) throw new ApiError('stream_dropped', 0, {});   // the stream closed without «done»
      if (S.phase === 'ready') scheduleRefresh();
      if (S.phase === 'ready' && S.reopen) { S.reopen = false; const c = chosen(); if (c && W.account) (customTk() ? confirmCustom : review)(); }
    } catch (e) {
      if (e.name === 'AbortError' || ctl.signal.aborted) return;
      if (quiet && S.m) { scheduleRefresh(); return; }
      // a dropped connection gets one quiet retry before anything is said (audit v4)
      const dropped = e.code === 'stream_dropped' || e.code === 'network' || e instanceof TypeError;
      if (dropped && !quiet && !S.retried) { S.retried = true; if (S.ctl === ctl) S.ctl = null; prog.innerHTML = `<span class="spin"></span><span class="pt">${esc(t('errors.stream_dropped'))}</span>`; return run(false); }
      if (dropped) e = new ApiError('stream_dropped_final', 0, {});
      if (e.code === 'unknown_token') e = Object.assign(new ApiError('unknown_token', 400, {}), { line: t('swap.custom.no_route') });
      S.phase = 'error'; S.err = e; S.errAt = Date.now(); if (RC) RC.error(); paint(); opts.onUpdate && opts.onUpdate(null, null, null, 'error', e);
    } finally { if (S.ctl === ctl) S.ctl = null; }
  }
  function announce() {
    const c = chosen(), el = root.querySelector('[data-live]'); if (!el) return;
    if (!c) { el.textContent = t('swap.cta.no_route'); return; }
    const o = c.solver ? c.r.min : c.r.out;
    const picked = S.sel && S.m.best && S.sel !== S.m.best.id;
    el.textContent = `${t('swap.get.label')} ${c.solver ? '≥ ' : ''}${amt(o.raw, o.decimals, 4)} ${S.to.symbol} · ${c.solver ? t('board.solver.min') : picked ? t('swap.get.picked') : t('swap.get.pick')}`;
  }
  function scheduleRefresh() {
    clearTimeout(S.refreshT);
    // P4: the landing card (compact) never re-quotes on its own — «Quoted N s ago · Refresh»; /app and /stock keep the 30 s refresh
    if (opts.compact) {
      S.refreshAt = 0; S.quotedAt = S.quotedAt || Date.now();
      const up = () => { if (S.phase !== 'ready' || !S.quotedAt) return; paintHead(); S.refreshT = setTimeout(up, 1000); };
      paintHead(); S.refreshT = setTimeout(up, 1000); return;
    }
    S.refreshAt = Date.now() + REFRESH_S * 1000;
    const tick = () => {
      if (S.phase !== 'ready') return;
      const left = Math.ceil((S.refreshAt - Date.now()) / 1000);
      if (left <= 0) { if (document.hidden || !onScreen) { S.refreshT = setTimeout(tick, 1000); return; } requote(0, true); return; }
      paintHead(); S.refreshT = setTimeout(tick, 1000);
    };
    S.refreshT = setTimeout(tick, 1000);
  }
  root.addEventListener('click', (e) => { if (e.target.closest('.rfb')) { e.preventDefault(); requote(0); } });
  let onScreen = true;
  new IntersectionObserver((es) => { onScreen = es[0].isIntersecting; }).observe(root);

  /* ---------- selected route (best unless the person picked a row on the board) ---------- */
  const chosen = () => {
    const m = S.m; if (!m) return null;
    if (S.sel) { const r = m.sim.find((x) => x.id === S.sel); if (r) return { r, solver: false }; const s = m.solver.find((x) => x.id === S.sel); if (s) return { r: s, solver: true }; }
    if (m.defaultRoute) { const r = m.sim.find((x) => x.id === m.defaultRoute); if (r) return { r, solver: false }; const so = m.solver.find((x) => x.id === m.defaultRoute); if (so) return { r: so, solver: true }; }
    if (m.best) return { r: m.best, solver: false };
    if (m.solver.length) return { r: m.solver[0], solver: true };
    return null;
  };
  const poolsOf = (r) => plural('common.pools', poolCount(r));

  /* ---------- paint ---------- */
  function paintHead() {
    const g = $('.fg .gl');
    if (g) {
      if (S.phase === 'quoting' && S.st) { g.innerHTML = `<span class="spin"></span><span class="pt">${esc(t('swap.state.quoting.cta'))}</span>`; g.title = ''; }
      else if (opts.compact && S.phase === 'ready' && S.quotedAt && chosen()) { const ago = Math.max(0, Math.round((Date.now() - S.quotedAt) / 1000)); g.innerHTML = `${esc(has('swap.refresh.ago') ? t('swap.refresh.ago', { s: ago }) : t('common.updated_ago', { age: ago + ' s' }))} · <button type="button" class="rfb">${esc(has('swap.refresh.btn') ? t('swap.refresh.btn') : t('swap.cta.fresh'))}</button>`; g.title = ''; }
      else if (S.phase === 'ready' && S.refreshAt && chosen()) { const left = Math.max(0, Math.ceil((S.refreshAt - Date.now()) / 1000)); g.textContent = t('swap.state.refresh.ring', { s: left }); g.title = ''; }
      else { g.textContent = gl(); g.title = has('swap.get.tip') ? t('swap.get.tip', { brand: '' }) : ''; }
      g.classList.toggle('pg', S.phase === 'quoting' || (S.phase === 'ready' && !!S.refreshAt));
    }
    const gr = root.querySelector('.gear'); if (gr) gr.style.visibility = /^(review|pending|success|fail)$/.test(S.phase) ? 'hidden' : '';
  }
  function setGet(text, cls = '', flash = false) {
    getEl.className = 'amt get ' + cls;
    if (getEl.textContent !== text) { getEl.textContent = text; if (flash && !RM) { getEl.classList.remove('flash'); void getEl.offsetWidth; getEl.classList.add('flash'); } }
  }
  function paint() {
    paintHead();
    pane.hidden = true; body.hidden = false;
    const m = S.m, c = chosen();
    // You get
    const sub = $('.fg .sub'), usdEl = $('.fg .usd'), bo = $('.fg .bo'), glEl = $('.fg .gl');
    usdEl.textContent = ''; bo.textContent = ''; void glEl;
    if (S.phase === 'empty' && S.pv && S.pv.best) {   // the landing example (a live board quote) until the person types
      const pv = S.pv, o = pv.best.out;
      inp.placeholder = amt(pv.amountIn.raw, pv.amountIn.decimals, 0);
      setGet(amt(o.raw, o.decimals, 4), 'dim'); if (o.usd) usdEl.textContent = t('swap.usd', { usd: nf(o.usd, 0) });
      bo.innerHTML = th('board.example', { size: kfmtUsd(pv), from: pairT(pv.from), ticker: pairT(pv.to) }, ['from', 'ticker']);
    } else if (S.phase === 'empty') { inp.placeholder = '0'; setGet('0', 'dim'); }
    else if (S.phase === 'quoting') {
      const ok = (S.st.routes || []).filter((r) => r.status === 'ok' && r.out && r.out.simulated).sort((a, b) => (BigInt(b.out.simulated.raw) > BigInt(a.out.simulated.raw) ? 1 : -1))[0]
        || (S.tab === 'cross' ? (S.st.routes || []).filter((r) => r.out && r.out.quoted && r.status === 'quoted')[0] : null);
      if (ok) { const o = ok.out.simulated || ok.out.quoted; setGet(amt(o.raw, o.decimals, 4)); if (o.usd) usdEl.textContent = t('swap.usd', { usd: nf(o.usd, 0) }); }
      else setGet(getEl.textContent && getEl.textContent !== '0' && getEl.textContent !== '—' ? getEl.textContent : '0', 'dim');
    } else if (S.phase === 'ready' && c) {
      const o = c.solver ? c.r.min : c.r.out;
      setGet((c.solver ? '≥ ' : '') + amt(o.raw, o.decimals, 4), c.solver ? 'min' : '', S.fresh);
      if (o.usd) usdEl.textContent = t('swap.usd', { usd: nf(o.usd, 0) });
      bo.textContent = c.solver ? t('board.solver.min') : (S.sel && m.best && S.sel !== m.best.id) ? t('swap.get.picked') : t('swap.get.pick');   // the number of routes lives once — in the race
    } else if (S.phase === 'ready') { setGet(t('swap.state.unavailable.out'), 'dim'); usdEl.textContent = S.tab === 'cross' ? '' : t('swap.state.unavailable.sub'); }
    else if (S.phase === 'error') setGet('—', 'dim');
    S.fresh = false;
    void sub;
    // CTA + note
    let lbl = t('swap.cta.enter'), cls = 'off', act = null, note = '';
    if (S.phase === 'quoting') lbl = t('swap.cta.quoting');
    else if (S.phase === 'error') {
      const wait = S.err && S.err.body && S.err.body.retryInS ? Math.max(0, Math.ceil(S.errAt + S.err.body.retryInS * 1000 - Date.now()) / 1000 | 0) : 0;
      lbl = S.err && S.err.code === 'stream_dropped_final' ? t('common.retry') : t('swap.cta.fresh'); cls = wait ? 'off' : ''; act = wait ? null : () => requote(0);
      note = `<div class="note bad">${esc(S.err && S.err.line ? S.err.line : wait ? errLine({ code: S.err.code, body: { retryInS: wait } }) : errLine(S.err))}</div>`;
      if (wait) { clearTimeout(S.waitT); S.waitT = setTimeout(() => S.phase === 'error' && paint(), 1000); }
      else if (S.err && S.err.body && S.err.body.retryInS && !S.autoRetried) { S.autoRetried = true; clearTimeout(S.waitT); S.waitT = setTimeout(() => { if (S.phase === 'error') requote(0); }, 300); }
    }
    else if (S.phase === 'ready') {
      const insufficient = m.insufficient || (S.bal != null && S.bal !== 'na' && S.raw && BigInt(S.raw) > BigInt(S.bal));
      if (!c && customTk()) { lbl = t('swap.cta.no_route'); note = `<div class="note bad">${esc(t('swap.custom.no_route'))}</div>`; }
      else if (!c) { lbl = t('swap.cta.no_route'); const ven = [...m.na, ...m.noRoute]; note = m.cross ? `<div class="note bad"><b>${esc(t('swap.state.unavailable.cross_title'))}</b> ${esc(t('swap.state.unavailable.cross_body'))}` : `<div class="note bad"><b>${t('swap.state.unavailable.title')}</b> ${t('swap.state.unavailable.body')}${ven.length ? `<div class="lg">${ven.map((r) => vlogo(r.venue, '')).join('')}</div>` : ''}</div>`; }
      else if (!W.account) { lbl = t('swap.cta.connect'); cls = ''; act = () => busyConnect([cta]); }
      else if (insufficient) { lbl = t('swap.cta.insufficient', { sym: S.from.symbol }); cls = 'warn'; note = `<div class="note warn">${th('swap.state.insufficient.note', { sym: S.from.symbol, ticker: S.to.symbol })}</div>`; }
      else if (m.stale) { lbl = t('swap.cta.fresh'); cls = ''; act = () => requote(0); note = `<div class="note info">${esc(t('swap.stale.sign_block'))}</div>`; }
      else { lbl = t('swap.cta.review'); cls = ''; act = customTk() ? confirmCustom : review; }
      if (!note && customTk()) note = `<div class="note warn">${esc(customTk().symbol)} · ${esc(t('swap.pick.custom_badge'))}</div>`;
    }
    cta.className = 'cta ' + cls; cta.textContent = lbl; cta.disabled = !act; cta.onclick = act;
    S.note = note; nslot.innerHTML = '';
    paintDet(c);
    const lk = det.querySelector('.note.warn'); if (lk) lk.innerHTML = lk.innerHTML.replace(esc(t('swap.state.insufficient.note_link')), `<button type="button" class="po2">${esc(t('swap.state.insufficient.note_link'))}</button>`);
    const po2 = det.querySelector('.po2'); if (po2) po2.onclick = () => pickPay(true);
    paintBal();
    hold();
  }
  const kfmtUsd = (pv) => '$' + nf(Math.round((pv.amountIn.usd || 0) / 1000), 0, 0) + 'k';
  function feesHtml(r, full) {
    const f = r && r.fees;
    const rows = [`<div class="z"><span title="${esc(t('swap.fees.ours.tip'))}">${t('swap.fees.ours.label')}</span><b>${t('swap.fees.ours.value')}</b></div>`];
    if (S.phase === 'empty' && !full) return `<div class="fees">${rows[0]}</div>`;
    const sk = '<span class="sk" style="width:84px;height:12px"></span>';
    rows.push(`<div><span title="${esc(t('swap.fees.route.tip'))}">${t('swap.fees.route.label')}${has('swap.fees.route.note') ? ` <em class="fnote">${esc(t('swap.fees.route.note'))}</em>` : ''}</span><b>${f && f.route && f.route.pct != null ? esc(t('swap.fees.route.value', { pct: pct(f.route.pct), usd: nf(f.route.usd, 2) })) : f && f.route && f.route.usd != null ? usd(f.route.usd) : S.phase === 'quoting' ? sk : '—'}</b></div>`);
    if (S.tab === 'cross') {
      const bi = f && f.bridge ? f.bridge.filter((x) => x.included !== false) : []; const b = bi.length ? bi.reduce((s, x) => s + (x.usd || 0), 0) : null;
      const br = r && r.steps && r.steps.find((s) => s.type === 'cross');
      rows.push(`<div><span title="${esc(t('swap.fees.bridge.tip', { bridge: br ? venueName(br) : '' }))}">${t('swap.fees.bridge.label')}</span><b>${b != null ? esc(t('swap.fees.bridge.value', { usd: nf(b, 2) })) : S.phase === 'quoting' ? sk : '—'}</b></div>`);
    }
    let g = f && f.gas && f.gas.usd != null ? f.gas.usd : r && r.gas && r.gas.usd;
    let est = !!((f && f.gas && f.gas.estimated) || (r && r.gas && r.gas.estimated));
    if (g == null && r && S.m) {
      const lim = (r.gas && (r.gas.used || r.gas.limit)) || (f && f.gas && f.gas.limit);
      const ref = S.m.sim.find((x) => x !== r && x.gas && x.gas.usd != null && (x.gas.used || x.gas.limit));
      if (lim && ref) { g = ref.gas.usd / (ref.gas.used || ref.gas.limit) * lim; est = true; }
    }
    rows.push(`<div><span title="${esc(t('swap.fees.gas.tip'))}">${t('swap.fees.gas.label')}</span><b>${g != null ? (g < 0.01 ? esc(t('swap.fees.gas.tiny')) : (est ? '≈ ' : '') + esc(t('swap.fees.gas.value', { usd: nf(g, 2) }))) : S.phase === 'quoting' ? sk : '—'}</b></div>`);
    return `<div class="fees">${rows.join('')}</div>`;
  }
  function routeLine(c, mm) {
    const m = mm || S.m;
    if (!m || !c) return `<div class="routeln"><span class="sk" style="width:60%;height:12px"></span></div><div class="routeln r2"></div>`;
    const r = c.r;
    if (c.solver) return `<div class="routeln"><span class="t">${m.cross && r.raw && r.raw.steps ? stepsHtml({ steps: r.raw.steps, durationS: r.raw.durationS }, m) : esc(t('board.solver.name'))}</span></div><div class="routeln r2"><span>${esc(t('board.solver.min'))}</span></div>`;
    const logos = `<span class="lg">${vlogo(r.venue, '')}${(r.byDex.length ? r.byDex : r.pools).slice(0, 3).map((p) => `<img src="${esc(p.logo || '/assets/logos/venues/other.svg')}" alt="">`).join('')}</span>`;
    if (m.cross) {
      const br = r.steps.find((s) => s.type === 'cross'), sw = [...r.steps].reverse().find((s) => s.type === 'swap');
      void br; void sw;
      return `<div class="routeln"><span class="t">${stepsHtml(r, m)}</span></div><div class="routeln r2"></div>`;
    }
    const g = rowVs(m, r);
    // works v5 m9: a huge amount warns on the card too (same threshold as Review), in place of the gain
    const gain = r.impact > 1 ? `<span class="iw">${esc(t('swap.route.impact_high', { pct: pct(r.impact) }))}</span>`
      : g != null && g > 0 ? `<span class="gn">${esc(t('swap.route.gain', { usd: nf(g, 2) }))}</span>` : (r === m.best && m.vsReason === 'pools_too_thin' ? `<span>${esc(t('swap.route.thin'))}</span>` : '');
    const picked = m === S.m && S.sel && m.best && S.sel !== m.best.id;   // works v5: «Your pick: …», never «Best: …» on a row the person chose
    const line = r.direct ? esc(t(picked ? 'swap.route.picked_direct' : 'swap.route.best_direct', { venue: (r.pools[0] && r.pools[0].name) || '' })) : th(picked ? 'swap.route.picked' : 'swap.route.best', { venue: `<b>${esc(venueName(r.venue))}</b>`, pools: poolsOf(r) }, ['venue']);
    // works v5 m6: the route provider did not answer, the card shows what did answer — say so quietly
    const partial = m === S.m && (m.routesUnavailable || m.routerUnav) && !gain ? `<span>${esc(t('swap.route.partial'))}</span>` : '';
    const why = r.direct && r === m.best && m.vsReason === 'best_is_pool' && has('direct.why_short') ? `<span class="gn">${esc(t('direct.why_short'))}</span>` : '';
    return `<div class="routeln">${logos}<span class="t">${line}</span></div><div class="routeln r2">${gain || why || partial}<span class="r" title="${esc(stampTitle(m))}">${esc(stamp(m, 'swap.route.block'))}</span></div>`;
  }
  function paintDet(c) {
    det.classList.toggle('full', S.phase !== 'empty'); det.classList.toggle('x', S.tab === 'cross');
    if (inlineRace) {
      // landing: the race lives in the card (3 slots), then one route line; full fees come in Review (v2: air)
      // (no empty bands, nothing moves): at rest the race already holds its place — the sources line and three quiet slots
      if (!det.contains(raceEl)) { det.innerHTML = ''; det.appendChild(raceEl); const tail = document.createElement('div'); tail.className = 'rtail'; det.appendChild(tail); }
      det.classList.add('rc');
      const tail = det.querySelector('.rtail');
      if (S.phase === 'empty' && RC) RC.idle();
      tail.innerHTML = S.note || (S.phase === 'ready' && c ? routeLine(c) : '');
      S.detH = Math.max(S.detH || 0, det.offsetHeight); det.style.minHeight = S.detH + 'px';
      return;
    }
    if (S.phase === 'empty' && S.pv && S.pv.best) { det.classList.add('full'); det.innerHTML = routeLine({ r: S.pv.best, solver: false }, S.pv) + feesHtml(S.pv.best, true); S.detH = 0; det.style.minHeight = ''; return; }
    // the window keeps one height from the first frame to the answer: route line and three fee lines are always there (audit v4 B1/S5)
    if (S.phase === 'empty') { det.innerHTML = `<div class="rslot">${restHint()}</div>` + feesHtml(null, true); S.detH = 0; det.style.minHeight = ''; return; }
    const r = c && !c.solver ? c.r : null;
    det.innerHTML = `<div class="rslot">${S.note || routeLine(S.phase === 'ready' ? c : null)}</div>` + feesHtml(r || (c && c.solver ? c.r.raw : null)) + (opts.details && S.phase === 'ready' && c ? `<div class="fees dlr"><div><span></span><a class="dl" href="${detailsUrl()}">${t('swap.route.details')} ›</a></div></div>` : '');
    S.detH = Math.max(S.detH || 0, det.offsetHeight); det.style.minHeight = S.detH + 'px';   // the card never shrinks back while quotes refresh
  }
  const detailsUrl = () => `/app?${new URLSearchParams({ from: S.from.symbol, fromChain: S.from.chain, to: S.to.custom ? S.to.address : S.to.symbol, amount: S.amount.replace(/,/g, '') })}`;

  /* ---------- settings ---------- */
  function settings() {
    const opt = [0.001, 0.005, 0.01];
    const { el, close } = sheet(`<div class="sh-h"><h2>${t('swap.slip.title')}</h2><button class="sh-x" data-close aria-label="${t('nav.close')}">${X_SVG}</button></div>
      <div class="pop"><p>${esc(t('swap.slip.body', { sym: S.from.symbol }))}</p>
      <div class="opts">${opt.map((v, i) => `<button data-v="${v}" class="${S.slip === v ? 'on' : ''}">${t('swap.slip.opt' + (i + 1))}</button>`).join('')}<label class="cus"><input inputmode="decimal" placeholder="${esc(t('swap.slip.custom'))}" aria-label="${esc(t('swap.slip.custom'))}" value="${opt.includes(S.slip) ? '' : nf(S.slip * 100, 2, 0)}"></label></div>
      <p class="sm"></p>
      <div class="kv"><span>${t('swap.slip.approve_title')}</span><b>${t('swap.slip.approve_value')}</b></div>
      <div><div class="kv" style="margin-bottom:8px"><span>${t('swap.slip.receiver_title')}</span></div><input class="inp rcv" placeholder="${esc(t('swap.slip.receiver_ph'))}" value="${esc(S.receiver || '')}" spellcheck="false" autocomplete="off"><p class="rm" style="margin-top:6px;color:var(--bad)"></p></div>
      <button class="cta" data-close>${t('common.done')}</button></div>`, { label: t('swap.slip.title') });
    const sm = el.querySelector('.sm');
    const hint = () => { sm.textContent = S.slip < 0.001 ? t('swap.slip.low') : S.slip > 0.01 ? t('swap.slip.high') : ''; };
    el.querySelectorAll('[data-v]').forEach((b) => b.addEventListener('click', () => { S.slip = +b.dataset.v; el.querySelectorAll('[data-v]').forEach((x) => x.classList.toggle('on', x === b)); el.querySelector('.cus input').value = ''; hint(); }));
    el.querySelector('.cus input').addEventListener('input', (e) => { const v = parseFloat(e.target.value); if (v > 0) { if (v > 5) { sm.textContent = t('swap.slip.max'); return; } S.slip = v / 100; el.querySelectorAll('[data-v]').forEach((x) => x.classList.remove('on')); hint(); } });
    const rcv = el.querySelector('.rcv'), rm = el.querySelector('.rm');
    rcv.addEventListener('input', () => { const v = rcv.value.trim(); if (!v) { S.receiver = null; rm.textContent = ''; return; } if (/^0x[0-9a-fA-F]{40}$/.test(v)) { S.receiver = v.toLowerCase(); rm.textContent = ''; } else rm.textContent = t('swap.slip.receiver_bad'); });
    hint(); void close;
  }

  /* ---------- a pasted token that is not on our list: an explicit warning the person confirms before Review ---------- */
  const customTk = () => (S.to && S.to.custom ? S.to : S.from && S.from.custom ? S.from : null);
  const okCustom = new Set();
  function confirmCustom() {
    const x = customTk(); if (!x) return review();
    if (okCustom.has(x.address)) return review();
    const ex = 'https://robinhoodchain.blockscout.com/token/' + x.address;
    const { el, close } = sheet(`<div class="sh-h"><h2>${esc(t('swap.custom.warn_title'))}</h2><button class="sh-x" data-close aria-label="${esc(t('swap.pick.close'))}">${X_SVG}</button></div>
      <div style="padding:4px 18px 18px"><div class="li" style="pointer-events:none">${tokI(x, 'ic')}<span><b>${esc(x.symbol)}</b><small>${esc(x.address)}</small></span></div>
      <p class="note warn">${esc(t('swap.custom.warn_body', { symbol: x.symbol, address: short(x.address) }))}</p>
      ${x.clash ? `<p class="note bad">${esc(t('swap.custom.clash', { symbol: x.symbol }))}</p>` : ''}
      ${x.lookalike ? `<p class="note bad" title="${esc(t('swap.custom.lookalike_tip'))}">${esc(t('swap.custom.lookalike', { name: x.name || x.symbol, symbol: x.symbol }))} <span class="lkt">${esc(t('swap.custom.lookalike_tip'))}</span></p>` : ''}
      <p style="margin:12px 0"><a href="${esc(ex)}" target="_blank" rel="noopener noreferrer" style="color:var(--mint)">${esc(t('swap.custom.explorer'))} ↗</a></p>
      <label class="row2" style="font:400 14px/1.4 var(--f);color:var(--silver);gap:10px;align-items:flex-start"><input type="checkbox" data-ok style="margin-top:3px"> <span>${esc(t('swap.custom.confirm'))}</span></label>
      <button class="cta" data-go disabled style="margin-top:14px;width:100%">${esc(t('swap.cta.review'))}</button></div>`, { label: t('swap.custom.warn_title') });
    const box = el.querySelector('[data-ok]'), go = el.querySelector('[data-go]');
    box.addEventListener('change', () => { go.disabled = !box.checked; });
    go.addEventListener('click', () => { if (!box.checked) return; okCustom.add(x.address); close(); review(); });
  }
  /* ---------- review (state 06) ---------- */
  /** the route's own fee in the source network's coin, on top of the amount (e.g. a bridge messaging fee: 78.67 MON); null when none */
  const nativeFeeOf = (c) => { const f = (c.solver ? c.r.raw && c.r.raw.fees : c.r.fees) || (S.build && S.build.fees) || {}; const n = f.nativeFee || (S.build && S.build.fees && S.build.fees.nativeFee); return n && n.raw && n.raw !== '0' ? n : null; };
  const chainName = () => (S.chains[S.from.chain] || {}).name || '';
  /** Review row «Fee in MON»: amount up to 4 decimals without trailing zeros, $ when priced, «on top, from your wallet on {chain}» */
  function nativeFeeRow(n) {
    const a = amt(n.raw, n.decimals, 4).replace(/(\.\d*?)0+$/, '$1').replace(/\.$/, '');
    const v = n.usd != null ? t('swap.review.native_fee_value', { amount: a, symbol: n.symbol || '', usd: nf(n.usd, 2) }) : t('swap.review.native_fee_value_plain', { amount: a, symbol: n.symbol || '' });
    return `<div class="fees nfee"><div><span title="${esc(t('swap.review.native_fee_tip', { symbol: n.symbol || '', chain: chainName() }))}">${esc(t('swap.review.native_fee', { symbol: n.symbol || '' }))}</span><b>${esc(v)}</b></div><p class="nfn">${esc(t('swap.review.native_fee_note', { chain: chainName() }))}</p></div>`;
  }
  const minOutOf = (c) => { if (c.solver) return c.r.min.raw; if (S.m && S.m.cross && c.r.min && c.r.min.raw) return c.r.min.raw; const o = BigInt(c.r.out.raw); return (o - (o * BigInt(Math.round(S.slip * 1e6))) / 1000000n).toString(); };
  async function review() {
    const c = chosen(); if (!c) return;
    clearTimeout(S.refreshT); S.phase = 'review'; S.build = null; S.mode = null; S.buildErr = null;
    renderReview(c);
    // what the wallet can do: one signature (7702 / 5792) or two transactions
    try {
      if (W.dev) S.mode = 'two';
      else {
        const s = await sign(); const prov = await s.W.provider();
        if (W.kind === 'privy-embedded') S.mode = 'one';
        else { const cap = await import('./sign/capabilities.js'); const raw = await cap.getCapabilities(prov, W.account, S.from.chain); S.mode = cap.parseCapabilities(raw, S.from.chain).atomic !== 'unsupported' ? 'one' : 'two'; }
      }
    } catch { S.mode = 'two'; }
    if (S.phase !== 'review') return;
    renderReview(c);
    try {
      S.build = await api('/api/build', { method: 'POST', auth: true, body: { routeId: c.r.id, fromAddress: W.account, minOut: minOutOf(c), mode: 'auto', slippage: Math.min(0.05, S.slip) } });   // engine v6: the quote's minimum and the person's slippage (cross-chain and Solver fill re-pull to it)
      S.builtAt = Date.now();
    } catch (e) {
      if (e.code === 'route_expired' || e.status === 410) { delete pane.dataset.rv; S.phase = 'ready'; requote(0); return; }
      if (e.code === 'below_min' && S.m && S.m.cross) e.cross = true;
      S.buildErr = e;
    }
    if (S.phase === 'review') renderReview(c);
  }
  function renderReview(c) {
    paintHead();
    const m = S.m, r = c.r, out = c.solver ? r.min : r.out;
    const sym = S.from.symbol;
    const native = /^0x0{40}$/.test(S.from.address);
    const approve = S.build ? S.build.sequence && S.build.sequence.some((x) => x.kind === 'approve') : !native;   // a native coin needs no approval: one transaction
    const one = S.mode === 'one';
    const mode = S.mode ? (approve ? t(one ? 'swap.review.mode_one' : 'swap.review.mode_two') : native ? t('swap.review.mode_native', { sym }) : t('swap.state.no_approve')) : '';
    const left = S.build ? Math.max(0, (S.build.expiresInS || BUILD_TTL) - Math.round((Date.now() - S.builtAt) / 1000)) : null;
    const nextId = S.buildErr && S.buildErr.code === 'simulation_reverted' && S.buildErr.body && S.buildErr.body.nextRouteId;
    const nextR = nextId && S.m && S.m.sim.find((x) => x.id === nextId);
    const vc = S.buildErr && (S.buildErr.code === 'chain_unverified' || S.buildErr.code === 'unverified_contract' || S.buildErr.code === 'below_min' || nextR);
    const err = S.buildErr ? `<div class="note bad">${esc(buildErr(S.buildErr))}${vc ? `<div class="nbtn">${nextR ? `<button type="button" data-next>${esc(t('swap.cta.next_route', { venue: venueName(nextR.venue) }))}</button>` : S.buildErr.code === 'below_min' ? `<button type="button" data-fresh>${esc(t(S.buildErr.cross && freshOut(S.buildErr) ? 'swap.cta.use_new' : 'swap.cta.fresh'))}</button>` : `${S.buildErr.code === 'unverified_contract' ? `<button type="button" data-fresh>${esc(t('swap.cta.fresh'))}</button>` : ''}<button type="button" data-other>${esc(t('swap.pay_other'))}</button>`}</div>` : ''}</div>` : (S.build && S.build.insufficientBalance ? `<div class="note warn">${esc(t('errors.insufficient_balance'))}</div>` : '');
    const ctaLbl = !S.mode ? t('swap.cta.quoting') : approve ? (one ? t('swap.state.one.cta') : t('swap.cta.approve', { sym })) : t('swap.cta.swap');
    const can = S.build && !S.buildErr && !S.build.insufficientBalance;
    const fine = [r.impact != null && !(r.impact > 1) ? `${t('swap.review.impact')} ${pct(Math.max(0, r.impact))}%` : '', `${t('swap.review.slippage')} ${pct(S.slip * 100)}%`, m.block ? t('swap.review.quote_block', { block: blk(m.block) }) : '', left != null ? t('swap.review.expires', { s: left }) : ''].filter(Boolean).map((x) => `<span class="nw">${esc(x)}</span>`).join(' · ');
    // design/v2/SPEC.md §8: «You get» is the one big line; everything else is quiet (Family 72e4aeb5, Reown 6508ecf7)
    pane.innerHTML = `<div class="rv2">
      <div class="rtop"><h3>${esc(t('swap.review.title'))}</h3><button class="bk">${SVG.back}${esc(t('common.back'))}</button></div>
      <div class="rget"><span class="lb">${esc(t('swap.review.get'))}</span><div class="big">${tokI(S.to, 'ic')}<b>${c.solver ? '≥ ' : ''}${amt(out.raw, out.decimals, 4)}</b><span>${esc(S.to.symbol)}</span></div>${out.usd ? `<small>${esc(t('swap.usd', { usd: nf(out.usd, 0) }))}</small>` : ''}</div>
      <div class="rpay"><span class="lb">${esc(t('swap.review.pay'))}</span><b>${tokI(S.from, 'ic', true, S.chains)}${amt(S.raw, S.from.decimals, 2)} ${esc(sym)}</b></div>
      <div class="fees">
        <div><span title="${esc(t('swap.review.min_tip', { sym }))}">${t('swap.review.min')}</span><b class="rt">${tokI(S.to, 'ic xs')}${amt(minOutOf(c), out.decimals, 4)} ${esc(S.to.symbol)}</b></div>
        <div><span>${t('swap.review.route')}</span><b class="rt">${vlogo(r.venue, 'ic')}${esc(venueName(r.venue))}${c.solver || poolCount(r) <= 1 ? '' : ' · ' + esc(poolsOf(r))}</b></div>
        ${S.receiver ? `<div><span></span><b>${esc(t('swap.review.receiver', { address: short(S.receiver) }))}</b></div>` : ''}
      </div>
      ${feesHtml(c.solver ? r.raw : r, true)}
      ${nativeFeeOf(c) ? nativeFeeRow(nativeFeeOf(c)) : ''}
      <p class="fine">${fine}</p>
      ${r.impact > 1 ? `<div class="note warn">${esc(t('swap.review.impact_high', { pct: pct(r.impact) }))}</div>` : ''}
    </div>
    <p class="mode">${mode ? `<span class="dot"></span>${esc(mode)}` : '&nbsp;'}</p>
    <button class="cta ${can ? '' : 'off'}${!S.build && !S.buildErr ? ' prep' : ''}" ${can ? '' : 'disabled'}${!S.build && !S.buildErr ? ' aria-busy="true"' : ''}><span>${esc(ctaLbl)}</span></button>${err}`;
    pane.hidden = false; body.hidden = true; hold();
    if (!RM && !document.hidden && !pane.dataset.rv) pane.animate([{ opacity: 0, transform: 'translateY(6px)' }, { opacity: 1, transform: 'none' }], { duration: 360, easing: 'cubic-bezier(.16,1,.3,1)' });
    pane.dataset.rv = '1';
    pane.querySelector('.bk').onclick = () => { delete pane.dataset.rv; S.phase = 'ready'; paint(); scheduleRefresh(); };
    const b = pane.querySelector('.cta'); if (can) b.onclick = () => { delete pane.dataset.rv; execute(c); };
    const fr = pane.querySelector('[data-fresh]');
    const fx = S.buildErr && S.buildErr.cross && S.buildErr.body && S.buildErr.body.fresh;
    if (fr && fx && fx.routeId && fx.min) fr.onclick = () => { delete pane.dataset.rv; Object.assign(c.r, { id: fx.routeId, min: { ...(c.r.min || {}), ...fx.min }, out: fx.quoted ? { ...(c.r.out || {}), ...fx.quoted } : c.r.out }); S.phase = 'review'; review(); };   // «Use new quote»: the fresh route, its own minimum
    else if (fr) fr.onclick = () => { delete pane.dataset.rv; S.phase = 'ready'; S.reopen = S.buildErr && S.buildErr.code === 'below_min'; requote(0); };
    const nx = pane.querySelector('[data-next]'); if (nx) nx.onclick = () => { delete pane.dataset.rv; S.sel = nextId; if (RC) RC.select(nextId); S.phase = 'review'; review(); };
    const ot = pane.querySelector('[data-other]'); if (ot) ot.onclick = () => { delete pane.dataset.rv; S.phase = 'ready'; paint(); pickPay(true); };
    clearTimeout(S.expT);
    if (left != null) S.expT = setTimeout(() => { if (S.phase !== 'review') return; if (left <= 1) { delete pane.dataset.rv; toast(t('swap.review.expired')); S.phase = 'ready'; requote(0); } else renderReview(c); }, 1000);
  }
  /** the fresh amount a 409 below_min carries (engine v5: {quoted, min, routeId}) */
  function freshOut(e) { const b = (e && e.body) || {}; const x = b.quoted || b.min || (b.fresh && (b.fresh.quoted || b.fresh.min)); return x && x.raw ? x : null; }
  function buildErr(e) {
    const c = e && e.code;
    if (c === 'below_min' && e.cross) { const f = freshOut(e); return f ? t('errors.below_min_cross', { amount: amt(f.raw, f.decimals != null ? f.decimals : S.to.decimals, 4), ticker: S.to.symbol }) : t('errors.below_min_cross_plain'); }
    if (c === 'below_min') return t('errors.below_min');
    if (c === 'simulation_reverted') return t('errors.simulation_reverted');
    if (c === 'venue_unavailable') return t('errors.venue_unavailable', { venue: venueName(chosen().r.venue) });
    if (c === 'chain_unavailable') return t('errors.chain_unavailable');
    if (c === 'simulation_unavailable') return t('errors.simulation_unavailable');
    if (c === 'sign_in_required') return t('errors.sign_in_required');
    if (c === 'chain_unverified') return t('errors.chain_unverified', { chain: chainName() });
    if (c === 'unverified_contract' || c === 'unsafe_build') return t('errors.unverified_contract');
    return errLine(e);
  }

  /* ---------- sign and send (states 07–09) + the live scheme (design/v2/SPEC.md §5) ---------- */
  async function execute(c) {
    S.phase = 'pending'; S.steps = { at: 'signing', txHash: null, explorerUrl: null }; S.track = null; S.tSigned = null; S.tDone = null;
    renderPending(c);
    try {
      const s = await sign();
      await s.nets;   // networks registered with the sign layer before the first signature
      const asked = { fromChain: S.from.chain, fromToken: S.from.address, toChain: RH, toToken: S.to.address, amount: S.raw, minOut: minOutOf(c), toAddress: S.receiver || undefined, venue: c.r.venue && c.r.venue.displayKey, nativeFee: nativeFeeOf(c) ? nativeFeeOf(c).raw : undefined };
      const res = await s.executeRoute(S.build, { wallet: s.W, asked, engine: s.engine, mode: 'auto', onStep: (ev) => {
        S.steps = { ...S.steps, ...ev, at: ev.state };
        if (!S.tSigned && /pending/.test(ev.state)) S.tSigned = Date.now();
        if (ev.txHash && !S.trackOn) startTrack(c);
        if (ev.state !== 'done' && ev.state !== 'failed') renderPending(c);
      } });
      if (res.status === 'pending') { S.steps.at = 'swap-pending'; S.steps.long = true; renderPending(c); return; }
      S.tDone = Date.now(); S.steps = { ...S.steps, at: 'done', destExplorerUrl: res.destExplorerUrl, explorerUrl: res.explorerUrl || S.steps.explorerUrl };
      await success(c, res);
    } catch (e) { fail(c, e); }
  }
  /** /api/track: real confirmations; 404 (engine not there yet) → the scheme follows the wallet's own steps and receipt */
  function startTrack(c) {
    S.trackOn = true;
    const cross = S.m.cross, every = cross ? 4500 : 1500;
    const q = () => new URLSearchParams({ txHash: S.steps.txHash, fromChain: S.from.chain, toChain: RH, ...(c.r.id ? { routeId: c.r.id } : {}) });
    const tick = async () => {
      if (!S.trackOn || !S.steps.txHash) return;
      try { S.track = await api('/api/track?' + q()); if (S.phase === 'pending' || S.phase === 'success') paintTrack(c); if (S.track.done || S.track.status === 'DONE' || S.track.status === 'FAILED') { S.trackOn = false; return; } }
      catch (e) { if (e.status === 404) { S.trackOn = false; return; } }
      S.trackT = setTimeout(tick, every);
    };
    tick();
  }
  /** nodes from the wallet's steps (fallback until /api/track answers) */
  function localNodes(c) {
    const r = c.r, st = S.steps || {}, at = st.at || 'signing', done = at === 'done', failed = at === 'failed';
    const out = c.solver ? r.min : r.out, outS = S.got || amt(out.raw, out.decimals, 4);
    const src = S.chains[S.from.chain] || {}, ns = [];
    if (S.m.cross) {
      const br = (r.steps || []).find((x) => x.type === 'cross') || (r.raw && r.raw.steps || []).find((x) => x.type === 'cross') || r.venue;
      const sent = /bridge-pending|done/.test(at);
      ns.push({ kind: 'source', state: sent ? 'done' : failed && !st.txHash ? 'failed' : 'pending', explorer: sent ? st.explorerUrl : null, logo: chainLogo(src), title: t('swap.track.node.source', { chain: src.name || '' }), sub: t('swap.track.node.source_sub', { amount: amt(S.raw, S.from.decimals, 2), sym: S.from.symbol }) });
      ns.push({ kind: 'bridge', state: done ? 'done' : failed && st.txHash ? 'failed' : 'pending', logo: br && br.logo, title: t('swap.track.node.bridge', { bridge: venueName(br) }), sub: t('swap.track.node.bridge_sub') });
      ns.push({ kind: 'chain', state: done ? 'done' : 'pending', explorer: done ? st.destExplorerUrl : null, logo: RH_LOGO, title: t('swap.track.node.chain'), sub: t('swap.track.node.chain_sub', { ticker: S.to.symbol }) });
    } else {
      ns.push({ kind: 'chain', state: done ? 'done' : failed && st.txHash ? 'failed' : 'pending', explorer: done ? st.explorerUrl : null, logo: RH_LOGO, title: t('swap.track.node.chain'), sub: t('swap.track.node.chain_sub', { ticker: S.to.symbol }) });
    }
    const lanes = lanesOf((r.byDex && r.byDex.length ? r.byDex : r.pools) || []);
    if (lanes.length) ns.push({ kind: 'split', state: done ? 'done' : 'pending', title: t('swap.track.node.split'), lanes });
    ns.push({ kind: 'wallet', state: done ? 'done' : 'pending', title: t('swap.track.node.wallet'), sub: failed ? '' : t('swap.track.node.wallet_sub', { amount: (c.solver ? '≥ ' : '') + outS, ticker: S.to.symbol }) });
    if (failed) ns.forEach((x) => { if (x.state === 'pending') x.state = 'off'; });   // after a failure nothing is «waiting» (works)
    if (S.demoLit != null) ns.forEach((x, i) => { x.state = i < S.demoLit ? 'done' : 'pending'; x.at = x.state === 'done' && S.demoAt ? S.demoAt[i] : null; x.explorer = null; });   // DEV demo only: nodes light one by one
    return ns;
  }
  let SCH = null, schEl = null;
  function paintTrack(c) {
    const host = opts.trackEl || pane.querySelector('.trk');
    if (!host) return;
    if (!SCH || schEl !== host) { schEl = host; host.innerHTML = '<div class="schw"></div>'; SCH = scheme(host.firstChild, { dir: opts.trackEl ? 'auto' : 'v', big: !!opts.trackEl, pitch: opts.trackEl ? 104 : 80 }); }
    const r = c.r, out = c.solver ? r.min : r.out;
    const nodes = S.track && (S.track.nodes || []).length ? trackNodes(S.track, { ticker: S.to.symbol, fromSym: S.from.symbol, tokOf: (a) => (a.symbol === S.to.symbol ? S.to : a.symbol === S.from.symbol ? S.from : null), split: r.byDex && r.byDex.length ? r.byDex : r.pools, expected: t('swap.track.node.wallet_sub', { amount: amt(out.raw, out.decimals, 4), ticker: S.to.symbol }) }) : localNodes(c);
    if (!SCH.nodes.length) SCH.set(nodes, { live: true }); else SCH.update(nodes, { finished: nodes.every((n) => n.state === 'done') });
    const outS = S.got || amt(out.raw, out.decimals, 4);
    void outS; opts.onTrack && opts.onTrack(true, t('swap.track.title'));
  }
  /** the quiet receipt of the swap in progress / done: minimum (pending only), route, fees — the same lines as Review */
  function receipt(c, withMin) {
    if (!opts.trackEl) return '';
    const r = c.r, out = c.solver ? r.min : r.out;
    return `<div class="rcpt"><div class="fees">${withMin ? `<div><span>${t('swap.review.min')}</span><b class="rt">${tokI(S.to, 'ic xs')}${amt(minOutOf(c), out.decimals, 4)} ${esc(S.to.symbol)}</b></div>` : ''}<div><span>${t('swap.review.route')}</span><b class="rt">${vlogo(r.venue, 'ic')}${esc(venueName(r.venue))}${c.solver || poolCount(r) <= 1 ? '' : ' · ' + esc(poolsOf(r))}</b></div></div>${feesHtml(c.solver ? r.raw : r, true)}${nativeFeeOf(c) ? nativeFeeRow(nativeFeeOf(c)) : ''}</div>`;
  }
  function renderPending(c) {
    paintHead();
    const r = c.r, st = S.steps || {}, out = c.solver ? r.min : r.out;
    pane.innerHTML = `<div class="pend"><span class="lb">${esc(t('swap.review.get'))}</span><div class="big">${tokI(S.to, 'ic')}<b>${c.solver ? '≥ ' : ''}${amt(out.raw, out.decimals, 4)}</b><span>${esc(S.to.symbol)}</span></div>
      ${c.solver ? `<p class="sub2">${esc(t('swap.state.pending.solver_note', { amount: amt(out.raw, out.decimals, 4), ticker: S.to.symbol }))}</p>` : ''}</div>
      ${opts.trackEl ? receipt(c, true) : '<div class="trk"></div>'}
      ${st.long ? `<div class="note info">${esc(t('swap.state.pending.slow'))}${st.explorerUrl ? ` <a href="${esc(st.explorerUrl)}" target="_blank" rel="noopener">${t('swap.track.explorer')} ↗</a>` : ''}</div>` : ''}<button class="cta off" disabled>${t(/signing/.test(st.at || '') ? 'swap.cta.sign' : 'swap.state.pending.cta')}</button>`;
    pane.hidden = false; body.hidden = true;
    if (!opts.trackEl) { SCH = null; }
    paintTrack(c);
    hold();
  }
  async function success(c, res) {
    S.phase = 'success'; paintHead();
    let got = null;
    try { const h = await api('/api/me/history', { method: 'POST', body: { tx: res.txHash, chainId: S.from.chain } }); const mv = (h.moves || []).find((x) => x.direction === 'in' && x.token && x.token.toLowerCase() === S.to.address); if (mv) got = amt(mv.amount, mv.decimals, 4); } catch { /* the receipt read below the fold */ }
    S.got = got;
    renderSuccess(c, got, res.destExplorerUrl || res.explorerUrl || (res.txHash ? 'https://robinhoodchain.blockscout.com/tx/' + res.txHash : null));
  }
  function renderSuccess(c, got, link) {
    const r = c.r, out = c.solver ? r.min : r.out;
    const amtS = got || amt(out.raw, out.decimals, 4);
    const secs = S.tSigned && S.tDone ? Math.max(1, Math.round((S.tDone - S.tSigned) / 1000)) : null;
    const tm = secs == null ? '' : secs < 60 ? t('swap.track.time', { s: secs }) : t('swap.track.time_min', { min: Math.floor(secs / 60), s: secs % 60 });
    pane.innerHTML = `<div class="res2"><div class="ok">${SVG.checkL}</div><div class="big">${tokI(S.to, 'ic')}<b>${esc(amtS)}</b><span>${esc(S.to.symbol)}</span></div><p>${esc(t('swap.state.success.body', { ticker: S.to.symbol }))}</p>${tm ? `<p class="tm">${esc(tm)}</p>` : ''}${link ? `<a class="xl" href="${esc(link)}" target="_blank" rel="noopener">${esc(t('swap.track.explorer'))} ↗</a>` : ''}</div>
      ${opts.trackEl ? receipt(c, false) : '<div class="trk"></div>'}
      <button class="cta">${t('swap.cta.again')}</button>
      ${isStock(S.to) ? `<button class="aw">${tokI(S.to, 'ic xs')}${esc(t('swap.state.success.add_watch', { ticker: S.to.symbol }))}</button>` : ''}`;
    pane.hidden = false; body.hidden = true;
    if (!opts.trackEl) SCH = null;
    paintTrack(c); hold();
    pane.querySelector('.cta').onclick = () => { S.trackOn = false; clearTimeout(S.trackT); S.phase = 'empty'; inp.value = ''; S.amount = ''; S.m = null; S.got = null; SCH = null; opts.onTrack && opts.onTrack(false); requote(0); loadBalance(); };
    const aw = pane.querySelector('.aw');
    if (aw) aw.onclick = async () => { try { const w = await api('/api/me/watchlist'); await api('/api/me/watchlist', { method: 'PUT', body: { tickers: [...new Set([...(w.tickers || []), S.to.symbol])] } }); toast(t('app.watch.saved')); } catch (e) { toast(errLine(e)); } };
  }
  function fail(c, e) {
    S.phase = 'fail'; paintHead();
    const code = e && e.code;
    if (code === 'user_rejected') { toast(t('swap.wallet.rejected')); S.phase = 'review'; opts.onTrack && opts.onTrack(false); renderReview(c); return; }
    if (code === 'quote_expired') { toast(t('errors.quote_expired')); S.phase = 'ready'; opts.onTrack && opts.onTrack(false); requote(0); return; }
    const src = S.chains[S.from.chain] || {};
    const why = code === 'chain_unverified' ? t('errors.chain_unverified', { chain: chainName() }) : code === 'unsafe_build' || code === 'unverified_contract' ? t('errors.unverified_contract') : S.m && S.m.cross && code === 'bridge_failed' ? t('swap.track.fail.bridge', { bridge: venueName(((c.r.steps || []).find((x) => x.type === 'cross')) || c.r.venue), chain: src.name || '' })
      : code === 'below_min' || code === 'reverted' && /slippage|min/i.test(e.why || '') ? t('swap.state.fail.slippage') : code === 'bridge_failed' ? t('swap.state.fail.bridge') : code === 'reverted' ? t('swap.state.fail.reverted') : (e && e.code && t('errors.' + (code === 'insufficient_gas' ? 'wallet.gas' : code === 'wrong_chain' ? 'wallet.wrong_chain' : code === 'nonce_conflict' ? 'wallet.nonce' : 'generic'), { chain: 'Robinhood Chain' })) || t('errors.generic');
    const link = (e && e.txHash) ? 'https://robinhoodchain.blockscout.com/tx/' + e.txHash : (S.steps && S.steps.explorerUrl);
    S.steps = { ...(S.steps || {}), at: 'failed' };
    S.failCode = code;
    renderFail(why, link, c);
  }
  function renderFail(why, link, c) {
    pane.innerHTML = `<div class="res2"><div class="no">${SVG.xL}</div><h3>${esc(t(S.m && S.m.cross ? 'swap.track.fail.title' : 'swap.state.fail.title'))}</h3><p>${esc(why)} ${S.m && S.m.cross ? '' : esc(t('swap.state.fail.funds', { sym: S.from.symbol }))}</p>${link ? `<a class="xl" href="${esc(link)}" target="_blank" rel="noopener">${esc(t('swap.track.explorer'))} ↗</a>` : ''}</div>${opts.trackEl || !c ? '' : '<div class="trk"></div>'}<button class="cta">${t('swap.state.fail.cta')}</button>${/^(chain_unverified|unsafe_build|unverified_contract)$/.test(S.failCode || '') ? `<button class="aw" data-other>${esc(t('swap.pay_other'))}</button>` : ''}`;
    pane.hidden = false; body.hidden = true;
    const ot = pane.querySelector('[data-other]'); if (ot) ot.onclick = () => { S.trackOn = false; S.phase = 'empty'; SCH = null; S.failCode = null; opts.onTrack && opts.onTrack(false); requote(0); pickPay(true); };
    if (c) { if (!opts.trackEl) SCH = null; paintTrack(c); }
    hold();
    pane.querySelector('.cta').onclick = () => { S.trackOn = false; S.phase = 'empty'; SCH = null; opts.onTrack && opts.onTrack(false); requote(0); };
  }

  /* ---------- dev-only audit switch: ?state=01…09 (local host only, never in production) ---------- */
  async function forceState(k) {
    const n = String(k).replace(/\D/g, '').padStart(2, '0');
    const T = await tokens();
    if (n === '01') { paint(); return; }
    if (n === '10') { paint(); return demoSwap(); }
    if (n === '05') S.to = T.find(RH, 'TSLA');
    paintTokens();
    inp.value = n === '05' ? '50,000' : '100,000'; S.amount = inp.value; S.raw = toRaw(S.amount, S.from.decimals);
    S.phase = 'quoting'; S.st = { total: 0, routes: [], base: null, block: null }; paint();
    if (n === '02') { // freeze mid-stream: stop after the first route
      const orig = opts.onUpdate; let seen = 0;
      opts.onUpdate = (mm, stq, d, ev) => { if (ev === 'route' && ++seen >= 2 && S.ctl) { S.ctl.abort(); S.phase = 'quoting'; paint(); } orig && orig(mm, stq, d, ev); };
    }
    await run(false);
    if (n === '02') return;
    if (n === '03') { S.fresh = true; paint(); return; }
    if (n === '04') { S.m.insufficient = true; Object.assign(W, { account: W.account || '0x1111111111111111111111111111111111111111' }); S.bal = '0'; paint(); return; }
    if (n === '05') { S.m.sim = []; S.m.best = null; S.m.solver = []; S.m.base = null; paint(); opts.onUpdate && opts.onUpdate(S.m, S.st, null, 'done'); return; } // audit view of «no simulated fill»
    const c = chosen(); if (!c) return;
    S.mode = 'one'; S.build = { sequence: [{ kind: 'approve' }, { kind: 'swap' }], expiresInS: 60 }; S.builtAt = Date.now();
    if (n === '06') { S.phase = 'review'; renderReview(c); clearTimeout(S.expT); return; }
    if (n === '07') { S.phase = 'pending'; S.steps = { at: 'swap-pending' }; renderPending(c); return; }
    if (n === '08') { S.phase = 'success'; S.steps = { at: 'done' }; paintHead(); renderSuccess(c, null, null); return; }
    if (n === '09') { S.phase = 'fail'; S.steps = { at: 'failed', txHash: '0x' }; paintHead(); renderFail(t('swap.state.fail.slippage'), null, c); }
  }

  /** DEV demo (local host only, never in production): the whole moment for the direction video — typing → the race on a recorded
   *  quote → Review → a staged signature → the scheme lighting node by node → the result. No wallet, no transaction: staged, labelled so. */
  async function demoSwap() {
    const sl = (ms) => new Promise((r) => setTimeout(r, ms));
    const amtTxt = QS.get('demoAmount') || '50000';
    await sl(+(QS.get('demoDelay') || 900));
    inp.focus();
    for (const ch of amtTxt) { inp.value = fmtTyped(inp.value.replace(/,/g, '') + ch); S.amount = inp.value; touch(); requote(260); await sl(120); }
    for (let i = 0; i < 200 && S.phase !== 'ready'; i++) await sl(100);
    const c = chosen(); if (!c) return;
    await sl(1900);
    S.mode = 'one'; S.build = { sequence: [{ kind: 'approve' }, { kind: 'swap' }], expiresInS: 60 }; S.builtAt = Date.now();
    clearTimeout(S.refreshT); S.phase = 'review'; renderReview(c); clearTimeout(S.expT);
    await sl(2300);
    delete pane.dataset.rv;
    S.phase = 'pending'; S.steps = { at: 'swap-signing' }; S.demoLit = 0; renderPending(c);
    await sl(1300);
    S.steps = { at: 'swap-pending' }; S.tSigned = Date.now(); renderPending(c);
    const n = localNodes(c).length;
    S.demoAt = [];
    for (let k = 1; k <= n; k++) { await sl(k === 1 ? 1100 : 750); S.demoAt[k - 1] = Math.floor(Date.now() / 1000); S.demoLit = k; if (k === n) S.steps = { at: 'done' }; paintTrack(c); }
    await sl(500);
    S.tDone = Date.now(); S.phase = 'success'; paintHead(); renderSuccess(c, null, null);
  }

  init().catch((e) => { root.classList.remove('boot'); S.phase = 'error'; S.err = e; paint(); });
  return {
    get state() { return S; },
    select(id) { if (!S.m) return; S.sel = id; if (S.phase === 'ready') paint(); },
    setTo(tk, quiet) { S.to = tk; if (!quiet) S.touched = true; if (!S.from) { tokBtn($('.tg'), tk); return; } paintTokens(); requote(0); },
    preview(m) { if (S.touched || S.raw) return; S.pv = m; if (S.phase === 'empty' && S.from) paint(); },
    get touched() { return !!S.touched; },
    setFrom(tk) { S.from = tk; setTab(tk.chain === RH ? 'swap' : 'cross'); paintTokens(); requote(0); },
    get race() { return RC; },
    setAmount(a) { inp.value = fmtTyped(a); S.amount = inp.value; requote(0); },
    focus() { inp.focus(); },
  };
}
