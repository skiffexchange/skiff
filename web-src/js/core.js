// Skiff core: strings, number formats (integer math for token amounts), engine API, icons, small UI helpers.
import STR from './strings.js';

export const RM = matchMedia('(prefers-reduced-motion: reduce)').matches;
export const QS = new URLSearchParams(location.search);
// dev-only switches (?state=, dev wallet) work only on a local host; never in production
export const LOCAL = /^(127\.0\.0\.1|localhost|\[::1\])$/.test(location.hostname);
export const $ = (s, r = document) => r.querySelector(s);
export const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const MISSING = new Set();

/** string by id with {slots}; {brand} is filled from brand.name. Values are escaped unless the slot name ends with "!" in vars. */
export function t(id, vars = {}, html = false) {
  let s = STR[id];
  if (s == null) { if (!MISSING.has(id)) { MISSING.add(id); console.warn('string missing:', id); } return ''; }
  return s.replace(/\{(\w+)\}/g, (m, k) => {
    if (k === 'brand') return STR['brand.name'];
    if (!(k in vars)) return m;
    const v = vars[k];
    return html && !(vars.__raw && vars.__raw.includes(k)) ? esc(v) : String(v);
  });
}
/** html-safe string with some raw (already-built html) slots */
export const has = (id) => id in STR;
export const th = (id, vars = {}, raw = []) => t(id, { ...vars, __raw: raw }, true);
export const esc = (v) => String(v ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

/* ---------- numbers ---------- */
const nfCache = {};
export function nf(n, dp = 2, min = dp) {
  const k = dp + ':' + min;
  const f = nfCache[k] || (nfCache[k] = new Intl.NumberFormat('en-US', { minimumFractionDigits: min, maximumFractionDigits: dp }));
  return f.format(n);
}
export const usd = (n, dp = 2) => '$' + nf(Math.abs(n), dp);
export const usd0 = (n) => '$' + nf(n, 0);
export function usdShort(n) {
  if (n >= 1e9) return '$' + nf(n / 1e9, 2) + 'B';
  if (n >= 1e6) return '$' + nf(n / 1e6, 2) + 'M';
  if (n >= 1e3) return '$' + nf(n / 1e3, 1, 0) + 'k';
  return '$' + nf(n, 0);
}
export const kfmt = (n) => (n >= 1000 ? '$' + nf(n / 1000, 1, 0) + 'k' : '$' + nf(n, 0));
export const pct = (p) => nf(p, 2, 0);
const TEN = (d) => 10n ** BigInt(d);
/** raw integer string → human string with dp decimals, rounded half-up, thousands commas (BigInt only) */
export function amt(raw, dec, dp = 2) {
  if (raw == null) return '—';
  let v = BigInt(raw); const neg = v < 0n; if (neg) v = -v;
  const sc = TEN(dec), q = dec > dp ? TEN(dec - dp) : 1n;
  let units = dec > dp ? (v + q / 2n) / q : v * TEN(dp - dec);   // value × 10^dp
  const ip = units / TEN(dp), fp = units % TEN(dp);
  const is = ip.toString().replace(/\B(?=(\d{3})+(?!\d))/g, ',');
  void sc;
  return (neg ? '-' : '') + is + (dp ? '.' + fp.toString().padStart(dp, '0') : '');
}
/** raw → float, for proportions only (bars), never for display of amounts */
export const fl = (raw, dec) => Number(BigInt(raw)) / 10 ** dec;
/** user's typed amount → raw integer string, or null; no floats */
export function toRaw(s, dec) {
  s = String(s || '').replace(/[,\s_]/g, '');
  if (!/^\d*\.?\d*$/.test(s) || s === '' || s === '.') return null;
  const [i, f = ''] = s.split('.');
  const raw = BigInt((i || '0') + (f + '0'.repeat(dec)).slice(0, dec));
  return raw > 0n ? raw.toString() : null;
}
/** pretty-print what the user typed (thousands commas), keep their decimals */
export function fmtTyped(s) {
  s = String(s).replace(/[^\d.]/g, '');
  const dot = s.indexOf('.');
  if (dot >= 0) s = s.slice(0, dot + 1) + s.slice(dot + 1).replace(/\./g, '');
  const [i, f] = s.split('.');
  const ii = (i || '').replace(/^0+(?=\d)/, '').replace(/\B(?=(\d{3})+(?!\d))/g, ',');
  return f !== undefined ? (ii || '0') + '.' + f : ii;
}
export const hhmm = (ts) => { const d = new Date(ts * 1000); return String(d.getUTCHours()).padStart(2, '0') + ':' + String(d.getUTCMinutes()).padStart(2, '0'); };
export const blk = (b) => nf(b, 0);
export function ago(s) { s = Math.max(0, Math.round(s)); if (s < 60) return s + ' s'; if (s < 3600) return Math.round(s / 60) + ' min'; return Math.round(s / 3600) + ' h'; }
export const short = (a) => (a ? a.slice(0, 6) + '…' + a.slice(-4) : '');
export const plural = (base, n) => t(base + (n === 1 ? '_one' : '_many'), { n });

/* ---------- engine API ---------- */
const DEVKEY = 'skiff.devWallet';
export function devWallet() { if (!LOCAL) return null; try { return localStorage.getItem(DEVKEY); } catch { return null; } }
export function setDevWallet(a) { try { a ? localStorage.setItem(DEVKEY, a) : localStorage.removeItem(DEVKEY); } catch { /* private window */ } }
if (LOCAL && /^0x[0-9a-fA-F]{40}$/.test(QS.get('devwallet') || '')) setDevWallet(QS.get('devwallet').toLowerCase());
export const session = { wallet: null, token: null, idToken: null };
function authHeaders() {
  const h = {};
  if (session.token) h.authorization = 'Bearer ' + session.token;
  if (session.idToken) h['privy-id-token'] = session.idToken;
  const d = devWallet(); if (d && session.wallet === d) h['x-dev-wallet'] = d;
  return h;
}
export class ApiError extends Error { constructor(code, status, body) { super(code); this.code = code; this.status = status; this.body = body || {}; } }
const memo = new Map();
export async function api(path, { method = 'GET', body, signal, cache = 0, auth = false, retried = false } = {}) {
  const me = auth || path.startsWith('/api/me');
  if (me && session.fresh) { try { await session.fresh(); } catch { /* keeps the last token */ } }   // Privy refreshes its access token itself
  const key = method + path;
  if (cache && memo.has(key)) { const m = memo.get(key); if (Date.now() - m.t < cache) return m.p; }
  const p = (async () => {
    let res;
    try {
      res = await fetch(path, { method, signal, headers: { ...(body ? { 'content-type': 'application/json' } : {}), ...(auth || path.startsWith('/api/me') ? authHeaders() : {}) }, body: body ? JSON.stringify(body) : undefined });
    } catch (e) { if (e.name === 'AbortError') throw e; throw new ApiError('network', 0); }
    let j = {}; try { j = await res.json(); } catch { /* empty */ }
    if (res.status === 401 && me && !retried && session.fresh) { await session.fresh(true); return api(path, { method, body, signal, auth, retried: true }); }
    if (!res.ok) throw new ApiError(j.error || 'generic', res.status, j);
    return j;
  })();
  if (cache) { memo.set(key, { t: Date.now(), p }); p.catch(() => memo.delete(key)); }
  return p;
}
export function errLine(e) {
  const c = e && e.code;
  const s = (e && e.body && e.body.retryInS) || 30;
  if (c === 'rate_limited') return t('errors.rate_limited', { s });
  if (c === 'busy') return t('errors.busy', { s });
  const id = 'errors.' + c;
  return STR[id] ? t(id, { s }) : t('errors.generic');
}

/* ---------- token index (instant picker, cached) ---------- */
let TOK = null;
export function tokens() {
  if (!TOK) {
    // P7: the token list is what every page stands on — a busy server (429) or a dropped request gets two more tries before the page gives up
    const get = (n) => api('/api/tokens', { cache: 60000 }).catch((e) => { if (n > 0 && (!e.status || e.status === 429 || e.status >= 500)) return new Promise((r) => setTimeout(r, 1500 * (3 - n))).then(() => get(n - 1)); throw e; });
    TOK = get(2).then((d) => {
      const list = d.tokens.map((x) => ({ chain: x.c, address: x.a, symbol: x.s, name: x.n, decimals: x.d, kind: x.k, logo: x.l, price: x.p }));
      const chains = d.chains; const byChain = Object.fromEntries(chains.map((c) => [c.id, c]));
      try { sessionStorage.setItem('skiff.tokens', JSON.stringify(d)); } catch { /* full */ }
      return { list, chains, byChain, at: d.pricesAt, find: (chain, q) => list.find((x) => x.chain === chain && (x.symbol.toLowerCase() === String(q).toLowerCase() || x.address === String(q).toLowerCase())) };
    }).catch((e) => { TOK = null; throw e; });
  }
  return TOK;
}
/** the «not on our list» badge; on narrow screens only its first part shows (prod-1 m3), the whole line stays in the title */
export function cbadge() { const full = t('swap.pick.custom_badge'), i = full.indexOf(' · '); return `<span class="cbadge" title="${esc(full)}">${i > 0 ? `${esc(full.slice(0, i))}<span class="cb2">${esc(full.slice(i))}</span>` : esc(full)}</span>`; }
export const isStock = (tk) => tk && (tk.kind === 'stock' || tk.kind === 'etf');
// a network token's picture that does not load: its letter mark takes the same place (no broken image, no inline handler — CSP)
addEventListener('error', (e) => { const i = e.target; if (i && i.tagName === 'IMG' && i.dataset.tkl) { const m = document.createElement('span'); m.className = i.className + ' tkl'; m.setAttribute('aria-hidden', 'true'); m.textContent = i.dataset.tkl; i.replaceWith(m); } }, true);

/* ---------- network tokens (stocks first, all tokens second) ----------
 * /api/network-tokens: listed[] (ETH, WETH, USDG, cbBTC — ours, the same records as /api/tokens) + tokens[] (live tokens of the chain
 * with liquidity, not on our list). A network token is never dressed as one of ours: its own name and symbol, its own picture
 * (/api/img/… only) or a letter mark — never a stock's logo, even when its symbol is a stock's (clash). */
let NT = null;
export function netTokens() {
  if (!NT) {
    NT = Promise.all([tokens(), api('/api/network-tokens', { cache: 300000 })]).then(([T, d]) => {
      const ours = (d.listed || []).map((x) => Object.assign(T.list.find((y) => y.chain === 4663 && y.address === x.address) || { chain: 4663, address: x.address, symbol: x.symbol, name: x.name, decimals: x.decimals, kind: x.kind, logo: x.logo, price: x.priceUsd }, { liq: x.liqUsd, vol: x.vol24hUsd, netPrice: x.priceUsd }));
      const net = (d.tokens || []).filter((x) => /^0x[0-9a-f]{40}$/.test(x.address || '')).map((x) => ({
        chain: 4663, address: x.address, symbol: String(x.symbol || '').slice(0, 16) || '?', name: String(x.name || x.symbol || '').slice(0, 48), decimals: x.decimals,
        kind: 'token', logo: /^(\/api\/img\/[0-9a-f]{24}|\/assets\/logos\/(net|chains|src)\/[0-9A-Za-z_.-]+\.(png|jpe?g|webp|gif|avif)(\?v=[0-9a-f]+)?)$/.test(x.logo || '') ? x.logo : null, price: x.priceUsd, custom: true, clash: !!x.clash,
        liq: x.liqUsd, vol: x.vol24hUsd }));
      return { ours, net, all: [...ours, ...net], at: d.at, stale: !!d.stale, snapshot: !!d.snapshot };
    }).catch((e) => { NT = null; throw e; });
  }
  return NT;
}

/* ---------- icons: wherever a ticker is, its icon is ---------- */
export const RH_LOGO = '/assets/brand/robinhood-chain.svg';
export function chainLogo(c) { return c && (c.id === 4663 || c.key === 'robinhood') ? RH_LOGO : (c && c.logo) || null; }
const LIGHT = new Set(['USO', 'AMC']);   // marks drawn dark on transparent: they sit on a light circle (prod-2)
export function tokIcon(sym, logo, kind, cls = 'ic') {
  if (kind === 'stock' || kind === 'etf') return logo ? `<span class="${cls} stk${LIGHT.has(sym) ? ' lt' : ''}"><img src="${esc(logo)}" alt="" loading="lazy" decoding="async"></span>` : `<span class="${cls} stk stk-t" aria-hidden="true" data-t="${esc(sym)}"></span>`;
  const L = (kind === 'token' || kind === 'custom') ? esc(String(sym || '?').replace(/^[^a-z0-9]+/i, '').slice(0, 1).toUpperCase() || '?') : '';
  if (logo) return `<img class="${cls}" src="${esc(logo)}" alt="" loading="lazy" decoding="async"${L ? ` data-tkl="${L}"` : ''}>`;
  if (L) return `<span class="${cls} tkl" aria-hidden="true">${L}</span>`;
  return `<span class="${cls} stk stk-t" aria-hidden="true" data-t="${esc(sym)}"></span>`;
}
/** token icon from a token object of /api/tokens (with chain badge for non-RH chains when badge) */
export function tokI(tk, cls = 'ic', badge = false, chains = null) {
  if (!tk) return `<span class="${cls} stk"></span>`;
  const i = tokIcon(tk.symbol, tk.logo, tk.kind, cls);
  if (!badge) return i;
  const c = chains && chains[tk.chain];
  const cl = c ? chainLogo(c) : null;
  return `<span class="tokwrap">${i}${cl ? `<img class="cb" src="${esc(cl)}" alt="">` : ''}</span>`;
}
export function vlogo(v, cls = 'vlogo') {
  if (!v) return '';
  if (v.kind === 'solver' || v.kind === 'own-router-tool' || ((v.displayKey === 'intent' || v.displayKey === 'intent-bridge') && v.kind !== 'swap') || (v.displayKey === 'deposit-swap' && !v.logo)) return `<span class="intico ${cls === 'vlogo' ? '' : cls}">${INTENT_SVG}</span>`;
  return v.logo ? `<img class="${cls}" src="${esc(v.logo)}" alt="${esc(v.name || '')}" loading="lazy" decoding="async">` : `<span class="txtmark ${cls}">${esc((v.name || '?')[0])}</span>`;
}
export const INTENT_SVG = `<svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden="true"><path d="M2 8h9M8 4.5 11.5 8 8 11.5" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/><circle cx="13.5" cy="8" r="1.3" fill="currentColor"/></svg>`;
export const venueName = (v) => (v && v.kind === 'swap') ? t('swap.route.cross_swap') : (v && (v.kind === 'solver' || v.kind === 'own-router-tool' || v.displayKey === 'intent' || v.displayKey === 'intent-bridge')) ? t('board.solver.name') : (v && v.name) || '';

/* ---------- UI helpers ---------- */
let toastT;
export function toast(msg) {
  let el = $('#toast'); if (!el) { el = document.createElement('div'); el.id = 'toast'; el.className = 'toast'; el.setAttribute('role', 'status'); document.body.appendChild(el); }
  el.textContent = msg; el.classList.add('on'); clearTimeout(toastT); toastT = setTimeout(() => el.classList.remove('on'), 3200);
}
/** opaque scrim + sheet; returns {el, close} */
export function sheet(html, { wide = false, label = '', onClose } = {}) {
  const sc = document.createElement('div');
  sc.className = 'scrim';
  sc.innerHTML = `<div class="sheet ${wide ? 'wide' : ''}" tabindex="-1" role="dialog" aria-modal="true" aria-label="${esc(label)}">${html}</div>`;
  document.body.appendChild(sc);
  const prev = document.activeElement;
  const bg = [...document.body.children].filter((x) => x !== sc && x.tagName !== 'SCRIPT');
  bg.forEach((x) => { x.inert = true; });   // the page behind the sheet is out of reach for Tab and screen readers
  const focusables = () => [...sc.querySelectorAll('button,[href],input,select,textarea,[tabindex]:not([tabindex="-1"])')].filter((x) => !x.disabled && x.offsetParent !== null);
  const close = () => {
    sc.classList.remove('on'); document.removeEventListener('keydown', key); bg.forEach((x) => { x.inert = false; });
    setTimeout(() => sc.remove(), 200);
    requestAnimationFrame(() => setTimeout(() => { if (prev && prev.focus && prev !== document.body) prev.focus(); }, 0));   // after the key that closed it is released
    onClose && onClose();
  };
  const key = (e) => {
    if (e.key === 'Escape') { e.preventDefault(); close(); return; }
    if (e.key === 'Tab') { const f = focusables(); if (!f.length) return; const i = f.indexOf(document.activeElement); if (e.shiftKey && i <= 0) { e.preventDefault(); f[f.length - 1].focus(); } else if (!e.shiftKey && i === f.length - 1) { e.preventDefault(); f[0].focus(); } }
  };
  sc.addEventListener('mousedown', (e) => { if (e.target === sc) close(); });
  document.addEventListener('keydown', key);
  sc.querySelectorAll('[data-close]').forEach((b) => b.addEventListener('click', close));
  requestAnimationFrame(() => sc.classList.add('on'));
  setTimeout(() => { if (!sc.contains(document.activeElement)) { const f = focusables(); const touch = matchMedia('(hover: none), (pointer: coarse), (max-width: 760px)').matches;   /* P3: no keyboard popping up on phones */ ((touch ? f.find((x) => x.tagName !== 'INPUT') : f.find((x) => x.tagName === 'INPUT')) || f[0] || sc.firstElementChild).focus({ preventScroll: true, focusVisible: !touch }); } }, 0);
  return { el: sc.firstElementChild, close };
}
export const X_SVG = '<svg width="14" height="14" viewBox="0 0 14 14" fill="none" aria-hidden="true"><path d="M3 3l8 8M11 3l-8 8" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg>';
export const SEARCH_SVG = '<svg width="18" height="18" viewBox="0 0 18 18" fill="none" aria-hidden="true"><circle cx="8" cy="8" r="5.5" stroke="currentColor" stroke-width="1.6"/><path d="m12.2 12.2 3.3 3.3" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg>';

/** pause CSS + WAAPI animations of a block while it is off-screen or the tab is hidden */
export function pauseOffscreen(el, onVis) {
  let vis = true;
  const apply = () => {
    const on = vis && !document.hidden;
    el.classList.toggle('paused', !on);
    el.getAnimations({ subtree: true }).forEach((a) => (on ? a.play() : a.pause()));
    onVis && onVis(on);
  };
  new IntersectionObserver((es) => { vis = es[0].isIntersecting; apply(); }).observe(el);
  document.addEventListener('visibilitychange', apply);
}
/** run fn once when el first scrolls into view */
export function onView(el, fn, margin = '200px') {
  if (!el) return;
  const io = new IntersectionObserver((es) => { if (es[0].isIntersecting) { io.disconnect(); fn(); } }, { rootMargin: margin });
  io.observe(el);
}
