// Token picker: instant, from the local /api/tokens index (cached). Search by ticker, name or address. Icons everywhere a ticker is.
import { t, esc, sheet, tokens, netTokens, cbadge, cbadgeFor, tokI, chainLogo, nf, X_SVG, SEARCH_SVG, isStock, api, errLine, short } from './core.js';

const POPULAR = ['NVDA', 'TSLA', 'SPY', 'QQQ', 'AAPL', 'SPCX', 'GOOGL', 'MSFT', 'AMZN', 'META'];
export function search(list, q) {
  q = q.trim().toLowerCase();
  if (!q) return list;
  if (/^0x[0-9a-f]{6,}$/.test(q)) return list.filter((x) => x.address.startsWith(q));
  const sc = (x) => { const s = x.symbol.toLowerCase(), n = x.name.toLowerCase(); if (s === q) return 0; if (s.startsWith(q)) return 1; if (n.startsWith(q)) return 2; if (n.includes(' ' + q)) return 3; if (s.includes(q) || n.includes(q)) return 4; return 9; };
  return list.map((x) => [sc(x), x]).filter((a) => a[0] < 9).sort((a, b) => a[0] - b[0] || POPULAR.indexOf(b[1].symbol) - POPULAR.indexOf(a[1].symbol)).map((a) => a[1]);
}
const order = (a, b) => { const ia = POPULAR.indexOf(a.symbol), ib = POPULAR.indexOf(b.symbol); return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib); };

/** side: 'pay' | 'get'. Pay side (v2): the network row on top (every chain of /api/tokens, Robinhood Chain first; Copperx 48df424e,
 * Reown e671aeec), the list follows the network. chainsFirst: open with every network shown (from «Another chain»). */
export async function pick({ side, cross = false, chain = null, chainsFirst = false }) {
  const T = await tokens();
  const chains = T.byChain;
  let tab = 'all', ch = chain || (cross ? 8453 : 4663), q = '', sel = 0, rows = [];
  // stocks first, all tokens second — «Tokens» is the last tab (ours ETH / WETH / USDG / cbBTC + live network tokens)
  const tabs = side === 'get' ? ['all', 'stocks', 'funds', 'tokens'] : ['all', 'stable', 'crypto', 'stocks', 'funds', 'tokens'];
  let NTL = null, ntErr = false;
  const chainChips = side === 'pay' ? [...T.chains].sort((a, b) => (a.id === 4663 ? -1 : b.id === 4663 ? 1 : 0)) : [];
  const SHOW = 5, more = Math.max(0, chainChips.length - SHOW);
  let expanded = chainsFirst || more === 0 || chainChips.slice(SHOW).some((c) => c.id === ch);
  const chipHtml = (c) => `<button data-ch="${c.id}" class="${c.id === ch ? 'on' : ''}" title="${esc(c.name)}">${chainLogo(c) ? `<img src="${esc(chainLogo(c))}" alt="">` : `<span class="lt" aria-hidden="true">${esc((c.name || '?')[0])}</span>`}${esc(c.name)}</button>`;
  return new Promise((resolve) => {
    let done = false;
    const { el, close } = sheet(`<div class="sh-h"><h2>${t(side === 'get' ? 'swap.pick.title_get' : 'swap.pick.title_pay')}</h2><button class="sh-x" data-close aria-label="${t('swap.pick.close')}">${X_SVG}</button></div>
      <label class="srch">${SEARCH_SVG}<input type="text" inputmode="search" autocomplete="off" spellcheck="false" placeholder="${esc(t('swap.pick.search'))}" aria-label="${esc(t('swap.pick.search'))}" role="combobox" aria-expanded="true" aria-autocomplete="list" aria-controls="pk-list"></label>
      ${chainChips.length ? `<p class="pkl">${esc(t('swap.pick.chain'))}</p><div class="chips cc ${expanded ? 'all' : ''}" role="group" aria-label="${esc(t('swap.pick.chain'))}">${(expanded ? chainChips : chainChips.slice(0, SHOW)).map(chipHtml).join('')}${!expanded && more ? `<button data-more>+${more}</button>` : ''}</div>` : ''}
      ${tabs.length ? `<div class="chips tt" ${side === 'pay' && ch !== 4663 ? 'hidden' : ''}>${tabs.map((k) => `<button data-t="${k}" class="${k === tab ? 'on' : ''}">${t('swap.pick.tab.' + k)}</button>`).join('')}</div>` : ''}
      ${side === 'get' ? `<p class="empty" style="padding:8px 18px 0;text-align:left">${t('swap.pick.only_rh')}</p>` : ''}
      <div class="list" role="listbox" id="pk-list" aria-label="${esc(t(side === 'get' ? 'swap.pick.title_get' : 'swap.pick.title_pay'))}"></div>`, { label: t(side === 'get' ? 'swap.pick.title_get' : 'swap.pick.title_pay'), onClose: () => { if (!done) resolve(null); } });
    const inp = el.querySelector('input'), list = el.querySelector('.list');
    netTokens().then((n) => { NTL = n; if (tab === 'tokens' || q) draw(); }).catch(() => { ntErr = true; if (tab === 'tokens') draw(); });
    // a row of a token that is not on our list: its own symbol and name, the «not on our list» badge, never a stock's look
    const netRow = (x, i) => `<button class="li nt ${i === sel ? 'sel' : ''}" role="option" id="pk-o${i}" aria-selected="${i === sel}" tabindex="-1" data-i="${i}">${tokI(x, 'ic')}<span><b>${esc(x.symbol)}${x.name && x.name.toLowerCase() !== x.symbol.toLowerCase() ? ` <span class="tn">${esc(x.name)}</span>` : ''}</b><small>${cbadgeFor(x)}</small></span><span class="r">${x.price != null ? esc(t('swap.pick.price', { price: nf(x.price, x.price < 1 ? 6 : x.price < 10 ? 4 : 2) })) : `<small>${t('swap.pick.no_price')}</small>`}</span></button>`;
    const kindOk = (x) => tab === 'all' || (tab === 'stocks' && x.kind === 'stock') || (tab === 'funds' && x.kind === 'etf') || (tab === 'crypto' && x.kind === 'crypto') || (tab === 'stable' && x.kind === 'stable');
    function draw() {
      let base = T.list.filter((x) => x.chain === ch && (side !== 'get' || isStock(x) || x.chain === 4663));
      if (ch !== 4663) tab = 'all';
      if (tab === 'tokens') {
        if (!NTL) { list.innerHTML = `<div class="empty">${ntErr ? esc(t('empty.tokens')) : `<span class="spin"></span> ${esc(t('empty.tokens_loading'))}`}</div>`; rows = []; return; }
        base = NTL.all;
      } else base = base.filter(kindOk);
      // a search on Robinhood Chain also finds the network tokens, after ours (an address of one of them needs no look-up)
      if (q && ch === 4663 && NTL && tab === 'all') base = [...base, ...NTL.net];
      const rank = (x) => (side === 'get' ? (isStock(x) ? 0 : 1) : ({ stable: 0, crypto: 1, etf: 3, stock: 2 }[x.kind] ?? 4));
      rows = q ? search(base, q) : tab === 'tokens' ? base : [...base].sort((a, b) => rank(a) - rank(b) || order(a, b));
      if (q && tab === 'all') rows = [...rows.filter((x) => !x.custom), ...rows.filter((x) => x.custom)];
      sel = Math.min(sel, Math.max(0, rows.length - 1));
      const full = q.trim().toLowerCase();
      if (!rows.length && /^0x[0-9a-f]{40}$/.test(full) && ch === 4663) { lookupRow(full); return; }
      // «0x…» that can't be an address (wrong length or not hex): say so instead of «check it on the explorer» (works, minor 3)
      if (!rows.length && /^0x/.test(full) && full.length > 2 && (!/^0x[0-9a-f]*$/.test(full) || full.length !== 42)) { list.innerHTML = `<div class="empty">${esc(t('swap.pick.not_address'))}</div>`; return; }
      if (!rows.length) {
        const other = side === 'pay' && ch === 4663 && T.list.some((x) => x.chain !== 4663 && search([x], q).length);
        list.innerHTML = `<div class="empty"><b>${esc(t('swap.pick.none_title', { q }))}</b>${esc(/^0x/.test(q) ? t('swap.pick.address_unknown') : t('swap.pick.none_body'))}${other ? `<br>${esc(t('swap.pick.none_samechain', { q }))}<br><button type="button" class="po3" style="color:var(--mint);margin-top:10px">${esc(t('swap.pay_other'))} ›</button>` : ''}</div>`;
        const b = list.querySelector('.po3'); if (b) b.onclick = () => { const x = T.list.find((y) => y.chain !== 4663 && search([y], q).length); if (x) { ch = x.chain; bindChips(); draw(); } };
        return;
      }
      inp.setAttribute('aria-activedescendant', rows.length ? 'pk-o' + sel : '');
      list.innerHTML = rows.slice(0, 160).map((x, i) => x.custom ? netRow(x, i) : `<button class="li ${i === sel ? 'sel' : ''}" role="option" id="pk-o${i}" aria-selected="${i === sel}" tabindex="-1" data-i="${i}">${tokI(x, 'ic', side === 'pay' && ch !== 4663, chains)}<span><b>${esc(x.symbol)}</b><small>${esc(x.symbol === 'USDG' && x.chain === 4663 ? t('swap.pick.usdg_sub') : x.name)}</small></span><span class="r">${x.price != null ? esc(t('swap.pick.price', { price: nf(x.price, x.price < 10 ? 4 : 2) })) : `<small>${t('swap.pick.no_price')}</small>`}</span></button>`).join('');
    }
    /* any token address on Robinhood Chain: read from the chain by the engine; never dressed as one of our listed tokens */
    let lkq = '';
    async function lookupRow(a) {
      if (lkq === a) return; lkq = a;
      list.innerHTML = `<div class="empty"><span class="spin"></span> ${esc(t('swap.pick.looking_up'))}</div>`;
      let d = null, e = null;
      try { d = await api('/api/token/' + a + '?soft=1', { cache: 300000 }); if (d && d.ok === false) { e = { code: d.code || 'not_a_token' }; d = null; } } catch (x) { e = x; }
      if (lkq !== a || q.trim().toLowerCase() !== a) return;
      lkq = '';
      if (e) { list.innerHTML = `<div class="empty"><b>${esc(t('swap.pick.none_title', { q: a.slice(0, 10) + '…' }))}</b>${esc(e.code === 'not_a_token' ? t('swap.pick.not_token') : e.code === 'rate_limited' ? errLine(e) : t('swap.pick.lookup_failed'))}</div>`; return; }
      const x = d.listed ? (T.list.find((y) => y.chain === 4663 && y.address === d.address) || null) : {
        chain: 4663, address: d.address, symbol: String(d.symbol || 'TOKEN').slice(0, 16), name: String(d.name || d.symbol || '').slice(0, 48),
        decimals: d.decimals, kind: 'custom', logo: /^(\/api\/img\/[0-9a-f]{24}|\/assets\/logos\/(net|chains|src)\/[0-9A-Za-z_.-]+\.(png|jpe?g|webp|gif|avif)(\?v=[0-9a-f]+)?)$/.test(d.logo || '') ? d.logo : null, price: null, custom: true, clash: !!d.clash, lookalike: !!d.lookalike, rh: !!d.robinhood, real: d.real || null };
      if (!x) { list.innerHTML = `<div class="empty">${esc(t('swap.pick.lookup_failed'))}</div>`; return; }
      rows = [x]; sel = 0;
      inp.setAttribute('aria-activedescendant', 'pk-o0');
      list.innerHTML = `<button class="li sel" role="option" id="pk-o0" aria-selected="true" tabindex="-1" data-i="0">${tokI(x, 'ic', true, chains)}<span><b>${esc(x.symbol)}</b><small>${x.custom ? cbadgeFor(x) : esc(t('swap.pick.row_sub', { name: x.name, chain: (chains[4663] || {}).name || '' }))}</small></span><span class="r"><small>${esc(short(x.address))}</small></span></button>${x.clash ? `<p class="note bad fake">${esc(t('swap.custom.clash', { symbol: x.symbol, address: x.real ? short(x.real.address) : '' }))}</p>` : ''}`;
    }
    const choose = (x) => { done = true; resolve(x); close(); };
    list.addEventListener('click', (e) => { const b = e.target.closest('[data-i]'); if (b) choose(rows[+b.dataset.i]); });
    inp.addEventListener('input', () => { q = inp.value; sel = 0; draw(); });
    inp.addEventListener('keydown', (e) => {
      if (e.key === 'ArrowDown' || e.key === 'ArrowUp') { e.preventDefault(); sel = Math.max(0, Math.min(rows.length - 1, sel + (e.key === 'ArrowDown' ? 1 : -1))); draw(); const s = list.querySelector('.sel'); s && s.scrollIntoView({ block: 'nearest' }); }
      if (e.key === 'Enter') { e.preventDefault(); e.stopPropagation(); if (rows[sel]) choose(rows[sel]); }
    });
    el.querySelectorAll('[data-t]').forEach((b) => b.addEventListener('click', () => { tab = b.dataset.t; el.querySelectorAll('[data-t]').forEach((x) => x.classList.toggle('on', x === b)); draw(); }));
    function bindChips() {
      const cc = el.querySelector('.chips.cc'); if (!cc) return;
      cc.classList.toggle('all', expanded);
      cc.innerHTML = (expanded ? chainChips : chainChips.slice(0, SHOW)).map(chipHtml).join('') + (!expanded && more ? `<button data-more>+${more}</button>` : '');
      cc.querySelectorAll('[data-ch]').forEach((b) => b.addEventListener('click', () => { ch = +b.dataset.ch; sel = 0; bindChips(); const tt = el.querySelector('.chips.tt'); if (tt) tt.hidden = side === 'pay' && ch !== 4663; draw(); }));
      const m = cc.querySelector('[data-more]'); if (m) m.addEventListener('click', () => { expanded = true; bindChips(); });
    }
    bindChips();
    draw();
    if (!matchMedia('(hover: none), (pointer: coarse), (max-width: 760px)').matches) inp.focus();   // P2: no keyboard popping over the list on touch screens
  });
}
