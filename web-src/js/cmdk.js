// ⌘K palette: buy a stock, pay with a token, pick a chain, or jump to an action. Instant, from the local token index.
import { t, esc, sheet, tokens, netTokens, tokI, chainLogo, nf, hhmm, isStock, SEARCH_SVG } from './core.js';
import { search } from './picker.js';

export async function palette(onPick) {
  const T = await tokens();
  let tab = 'stocks', q = '', sel = 0, rows = [], NT = null;
  netTokens().then((n) => { NT = n; if (tab === 'tokens' || q) draw(); }).catch(() => {});
  const price = (p) => (p != null ? '$' + nf(p, p < 1 ? 4 : 2) : '');
  const tokRow = (x) => ({ i: tokI(x, 'ic'), b: t('app.k.row.buy', { ticker: x.symbol }), s: x.custom ? t('swap.pick.custom_badge') : x.name, warn: !!x.custom, r: price(x.price), go: () => pickTo(x) });
  const acts = [
    { k: 'swap', label: t('app.k.act.swap'), go: () => go('/app') },
    { k: 'cross', label: t('app.k.act.cross'), go: () => go('/app?fromChain=8453&from=USDC') },
    { k: 'history', label: t('app.k.act.history'), go: () => go('/app#history') },
    // Vault: only where the page itself links it (the engine strips Vault links while TOKEN_PAGE is off)
    ...(document.querySelector('a[href="/token"][data-vlink]') ? [{ k: 'token', label: t('app.k.act.token'), go: () => go('/token') }] : []),
    { k: 'slip', label: t('app.k.act.slippage'), go: () => { const g = document.querySelector('.sw .gear'); g ? g.click() : go('/app'); } },
  ];
  function go(u) { done(); if (location.pathname === '/app' || location.pathname === '/swap') { const url = new URL(u, location.origin); if (url.pathname === location.pathname && !url.search) { location.hash = url.hash; if (url.hash) dispatchEvent(new HashChangeEvent('hashchange')); return; } } location.href = u; }
  const { el, close } = sheet(`<label class="srch" style="margin-top:18px">${SEARCH_SVG}<input type="text" autocomplete="off" spellcheck="false" placeholder="${esc(t('app.k.placeholder'))}" aria-label="${esc(t('app.k.placeholder'))}" role="combobox" aria-expanded="true" aria-autocomplete="list" aria-controls="k-list"></label>
    <div class="chips">${['stocks', 'tokens', 'chains', 'actions'].map((k) => `<button data-t="${k}" class="${k === tab ? 'on' : ''}">${t('app.k.tab.' + k)}</button>`).join('')}</div>
    <div class="list" role="listbox" id="k-list" aria-label="${esc(t('app.k.placeholder'))}"></div>
    <div class="khints"><span>${t('app.k.hint.move')}</span><span>${t('app.k.hint.open')}</span><span>${t('app.k.hint.close')}</span><span class="st">${esc(t('app.k.stamp', { time: T.at ? hhmm(T.at) : '' }))}</span></div>`, { wide: true, label: t('app.k.placeholder') });
  const done = close;
  const inp = el.querySelector('input'), list = el.querySelector('.list');
  function draw() {
    if (tab === 'stocks') {
      rows = search(T.list.filter((x) => x.chain === 4663 && isStock(x)), q).map((x) => ({ i: tokI(x, 'ic'), b: t('app.k.row.buy', { ticker: x.symbol }), s: x.name, r: x.price != null ? '$' + nf(x.price, 2) : '', go: () => pickTo(x) }));
      if (q && NT) rows = rows.concat(search(NT.all, q).map(tokRow));
    } else if (tab === 'tokens') rows = NT ? (q ? search(NT.all, q) : NT.all).map(tokRow) : [];
    else if (tab === 'chains') rows = T.chains.filter((c) => !q || c.name.toLowerCase().includes(q.toLowerCase())).map((c) => ({ i: chainLogo(c) ? `<img class="ic" src="${esc(chainLogo(c))}" alt="" style="border-radius:8px">` : '<span class="ic stk"></span>', b: c.name, s: c.native && typeof c.native === 'object' ? c.native.symbol : c.native, r: '', go: () => { const u = T.list.find((x) => x.chain === c.id && x.symbol === 'USDC') || T.list.find((x) => x.chain === c.id); if (u) pickFrom(u); } }));
    else rows = acts.filter((a) => !q || a.label.toLowerCase().includes(q.toLowerCase())).map((a) => ({ i: '<span class="ic stk" style="background:var(--ctrl2)"></span>', b: a.label, s: '', r: '', go: a.go }));
    sel = Math.min(sel, Math.max(0, rows.length - 1));
    inp.setAttribute('aria-activedescendant', rows.length ? 'k-o' + sel : '');
    list.innerHTML = rows.length ? rows.slice(0, 80).map((r, i) => `<button class="li ${i === sel ? 'sel' : ''}" data-i="${i}" role="option" id="k-o${i}" aria-selected="${i === sel}" tabindex="-1">${r.i}<span><b>${esc(r.b)}</b>${r.s ? `<small${r.warn ? ' class="cbadge"' : ''}>${esc(r.s)}</small>` : ''}</span><span class="r">${r.r ? esc(r.r) : '—'}</span></button>`).join('')
      : `<div class="empty"><b>${esc(t('app.k.empty', { q }))}</b>${esc(t('app.k.empty_body'))}</div>`;
  }
  function pickTo(x) { if (onPick) { done(); onPick('to', x); } else go('/app?to=' + encodeURIComponent(x.custom ? x.address : x.symbol)); }
  function pickFrom(x) { if (onPick) { done(); onPick('from', x); } else go(`/app?from=${encodeURIComponent(x.symbol)}&fromChain=${x.chain}`); }
  list.addEventListener('click', (e) => { const b = e.target.closest('[data-i]'); if (b) rows[+b.dataset.i].go(); });
  inp.addEventListener('input', () => { q = inp.value; sel = 0; if (/^0x/i.test(q) && tab !== 'stocks' && tab !== 'tokens') tab = 'tokens'; draw(); });
  inp.addEventListener('keydown', (e) => {
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') { e.preventDefault(); sel = Math.max(0, Math.min(rows.length - 1, sel + (e.key === 'ArrowDown' ? 1 : -1))); draw(); const s = list.querySelector('.sel'); s && s.scrollIntoView({ block: 'nearest' }); }
    if (e.key === 'Enter') { e.preventDefault(); e.stopPropagation(); if (rows[sel]) rows[sel].go(); }
    if (e.key === 'Tab' && e.altKey) { e.preventDefault(); const ks = ['stocks', 'tokens', 'chains', 'actions']; tab = ks[(ks.indexOf(tab) + (e.shiftKey ? 3 : 1)) % 4]; el.querySelectorAll('[data-t]').forEach((x) => x.classList.toggle('on', x.dataset.t === tab)); sel = 0; draw(); }
  });
  el.querySelectorAll('[data-t]').forEach((b) => b.addEventListener('click', () => { tab = b.dataset.t; el.querySelectorAll('[data-t]').forEach((x) => x.classList.toggle('on', x === b)); sel = 0; draw(); inp.focus(); }));
  draw(); if (!matchMedia('(hover: none), (pointer: coarse), (max-width: 760px)').matches) inp.focus();
}
