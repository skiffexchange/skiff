// Wallet seam (D16): «Connect wallet» opens the Privy window (wallet only: extensions + WalletConnect), so every connected wallet
// carries a Privy session for the signed-in routes. Direct EIP-6963 connection is the fallback when Privy cannot load (or has no app id
// yet): swaps work, saving watchlist/alerts asks to sign in. The sign layer loads only when someone connects or connected before.
import { t, esc, sheet, toast, short, session, devWallet, setDevWallet, LOCAL, api, X_SVG, $$, has, tokens } from './core.js';

let W = null, loading = null, cfg = null;
const subs = new Set();
export const state = { account: null, kind: null, name: null, chainId: null, dev: false, canSave: false };
export function onWallet(cb) { subs.add(cb); cb({ ...state }); return () => subs.delete(cb); }
function emit() {
  session.wallet = state.account;
  state.canSave = !!state.account && (state.dev || /^privy/.test(state.kind || ''));
  for (const cb of subs) { try { cb({ ...state }); } catch (e) { console.error(e); } }
}
async function config() { if (!cfg) cfg = api('/api/config', { cache: 600000 }).catch(() => ({})); return cfg; }
export async function sign() {
  if (!loading) loading = Promise.all([import('./sign/wallets.js'), import('./sign/execute.js'), import('./sign/engine.js'), config()]).then(([w, x, e, c]) => {
    window.SKIFF_PRIVY = { ...(window.SKIFF_PRIVY || {}), appId: c.privyAppId || null, logo: location.origin + '/assets/brand/lockup-skiff.svg', header: t('privy.header') };   // the Skiff lock-up in the wallet window
    W = w.createWallet({ privySrc: '/assets/vendor/privy.js', privy: c.privyAppId ? { appId: c.privyAppId } : {} });
    const sync = (s) => {
      if (state.dev) return;
      Object.assign(state, { account: s.status === 'connected' ? s.account : null, kind: s.kind, name: s.name, chainId: s.chainId });
      refreshToken().finally(emit);
    };
    W.on('change', sync); W.on('connect', () => sync(W.state)); W.on('disconnect', () => sync(W.state)); W.on('accountsChanged', () => sync(W.state)); W.on('chainChanged', () => sync(W.state));
    const engine = e.createEngine('');
    // the Pay-from networks are registered through the sign layer once (its own chains.js — the same module execute.js uses), never by hand
    const nets = tokens().then((T) => e.loadChains(engine, { chains: T.chains })).catch(() => e.loadChains(engine)).catch(() => ({ signable: [], refused: [] }));
    return { W, executeRoute: x.executeRoute, engine, nets };
  }).catch((err) => { loading = null; throw err; });
  return loading;
}
session.fresh = async () => { if (W && /^privy/.test(state.kind || '')) await refreshToken(); };
async function refreshToken() {
  try { session.token = W ? await W.token() : null; } catch { session.token = null; }
  try { session.idToken = window.SkiffPrivy && window.SkiffPrivy.idToken ? window.SkiffPrivy.idToken() : null; } catch { session.idToken = null; }
}
addEventListener('skiff-privy', () => { refreshToken(); });

/** on page load: silent reconnect only if this browser connected before (no window, no Privy bundle unless Privy was used here) */
export async function restore() {
  const d = devWallet();
  let last = null; try { last = localStorage.getItem('skiff.wallet'); } catch { /* none */ }
  if (d && LOCAL && sessionStorage.getItem('skiff.dev') === '1') { Object.assign(state, { account: d, kind: 'dev', name: 'Dev wallet', dev: true }); emit(); return; }
  if (!last) { emit(); return; }
  try { const s = await sign(); await s.W.restore(); } catch { emit(); }
}

export async function connect() {
  const c = await config();
  if (c.privyAppId) {
    try { const s = await sign(); await s.W.connect('privy'); return; }
    catch (e) { if (e && e.code === 'user_rejected') { toast(t('errors.wallet.rejected')); return; } /* Privy did not load: fall back to the extensions */ }
  }
  let ws = [];
  try { const s = await sign(); await new Promise((r) => setTimeout(r, 150)); ws = s.W.wallets(); } catch { /* sign layer missing */ }
  const rows = ws.map((w) => `<button data-w="${esc(w.id)}">${w.icon ? `<img src="${esc(w.icon)}" alt="">` : '<span class="ph"></span>'}${esc(w.name)}</button>`).join('')
    + (LOCAL ? `<button data-w="dev"><span class="ph"></span>Dev wallet ${esc(short(devWallet() || ''))}</button>` : '');
  const none = !ws.length && !LOCAL;
  const { el, close } = sheet(`<div class="sh-h"><h2>${t('nav.connect')}</h2><button class="sh-x" data-close aria-label="${t('nav.close')}">${X_SVG}</button></div>
    <div class="pop">${none ? `<p>${t('errors.wallet.not_found')}</p>` : `<div class="wl">${rows}</div>`}<p>${t('swap.wallet.connect_to_swap')}</p></div>`, { label: t('nav.connect') });
  el.querySelectorAll('[data-w]').forEach((b) => b.addEventListener('click', async () => {
    const id = b.dataset.w;
    if (id === 'dev') {
      let a = devWallet();
      if (!a) { const r = crypto.getRandomValues(new Uint8Array(20)); a = '0x' + [...r].map((x) => x.toString(16).padStart(2, '0')).join(''); setDevWallet(a); }
      sessionStorage.setItem('skiff.dev', '1');
      Object.assign(state, { account: a, kind: 'dev', name: 'Dev wallet', dev: true, chainId: 4663 }); emit(); close(); return;
    }
    try { const s = await sign(); close(); await s.W.connect(id); }
    catch (e) { toast(e && e.code === 'user_rejected' ? t('errors.wallet.rejected') : t('errors.wallet.not_found')); }
  }));
}
export async function disconnect() {
  if (state.dev) { sessionStorage.removeItem('skiff.dev'); Object.assign(state, { account: null, kind: null, name: null, dev: false }); emit(); return; }
  try { const s = await sign(); await s.W.disconnect(); } catch { /* gone */ }
}
export const wallet = () => W;
/** line for the watchlist/alerts when the wallet is connected without a Privy session (fallback path) */
export const saveHint = () => (has('app.signin.save') ? t('app.signin.save') : t('app.signin.title'));

/** header button: «Connect wallet» or the address with a small menu */
export function bindHeader() {
  const btns = $$('[data-connect]');
  btns.forEach((b) => b.addEventListener('click', () => (state.account ? accountMenu(b) : busyConnect(btns))));
  onWallet((s) => btns.forEach((b) => { b.innerHTML = s.account ? `<span class="av"></span>${esc(t('nav.connected', { address: short(s.account) }))}` : esc(t('nav.connect')); }));
}
/** «Connect wallet» with a working state: a spinner from the click until a wallet window (Privy or ours) is on screen, 20 s at most */
export function busyConnect(btns) {
  const list = (btns && btns.length ? btns : $$('[data-connect]')).filter((b) => !b.classList.contains('busy'));
  list.forEach((b) => { b.classList.add('busy'); b.setAttribute('aria-busy', 'true'); });
  let mo = null, tm = null;
  const end = () => { if (mo) mo.disconnect(); clearTimeout(tm); list.forEach((b) => { b.classList.remove('busy'); b.removeAttribute('aria-busy'); }); };
  mo = new MutationObserver(() => { if (document.querySelector('#privy-modal-content, [id^="privy"], .scrim')) end(); });
  mo.observe(document.body, { childList: true, subtree: true });
  tm = setTimeout(end, 20000);
  return connect().finally(end);
}
function accountMenu() {
  const { el, close } = sheet(`<div class="sh-h"><h2>${esc(short(state.account))}</h2><button class="sh-x" data-close aria-label="${t('nav.close')}">${X_SVG}</button></div>
    <div class="pop"><div class="wl"><button data-a="copy">${t('nav.copy_address')}</button><button data-a="off">${t('nav.disconnect')}</button></div></div>`, { label: t('nav.connected', { address: short(state.account) }) });
  el.querySelector('[data-a=copy]').onclick = async () => { try { await navigator.clipboard.writeText(state.account); toast(t('nav.copied')); } catch { /* denied */ } close(); };
  el.querySelector('[data-a=off]').onclick = () => { close(); disconnect(); };
}
