// Wallet discovery and connection for the «Connect wallet» button (sign-in = connecting your own wallet). Injected wallets via EIP-6963 (https://eips.ethereum.org/EIPS/eip-6963): MetaMask, Rabby, Coinbase, OKX, Phantom… each
// announces itself; window.ethereum is the fallback for an old wallet that does not announce. Privy (its wallet modal with
// WalletConnect QR, and an embedded wallet if the owner ever enables one) loads ONLY on demand: the ~1.5 MB bundle is fetched when
// someone picks «More wallets» or connected through Privy before on this browser.
import { big, lc } from './abi.js';
import { addChainParams, chainOf, hexChainId } from './chains.js';
import { fail, mapError } from './errors.js';

const KEY = 'skiff.wallet';   // {via: 'injected'|'privy', rdns}
const store = (s) => ({
  get() { try { return JSON.parse(s.getItem(KEY) || 'null'); } catch { return null; } },
  set(v) { try { s.setItem(KEY, JSON.stringify(v)); } catch { /* private window: nothing kept */ } },
  del() { try { s.removeItem(KEY); } catch { /* same */ } },
});
const memStorage = () => { const m = new Map(); return { getItem: (k) => m.get(k) ?? null, setItem: (k, v) => m.set(k, String(v)), removeItem: (k) => m.delete(k) }; };

/**
 * EIP-6963 discovery. Calls `onChange(list)` every time a wallet announces; returns {list(), stop()}.
 * list item: {id (rdns or uuid), uuid, name, icon (data URI from the wallet), rdns, provider}.
 */
export function watchInjected(onChange = () => {}, win = globalThis.window) {
  const found = new Map();
  if (!win || !win.addEventListener) return { list: () => [], stop() {} };
  const onAnnounce = (ev) => {
    const d = ev && ev.detail;
    if (!d || !d.info || !d.provider || typeof d.provider.request !== 'function') return;
    const id = d.info.rdns || d.info.uuid;
    found.set(id, { id, uuid: d.info.uuid, name: String(d.info.name || 'Wallet'), icon: d.info.icon || null, rdns: d.info.rdns || null, provider: d.provider });
    onChange(list());
  };
  const list = () => {
    const out = [...found.values()];
    if (!out.length && win.ethereum && typeof win.ethereum.request === 'function')   // a wallet that predates EIP-6963
      out.push({ id: 'injected', uuid: null, name: win.ethereum.isMetaMask ? 'MetaMask' : 'Browser wallet', icon: null, rdns: null, provider: win.ethereum, legacy: true });
    return out;
  };
  win.addEventListener('eip6963:announceProvider', onAnnounce);
  win.dispatchEvent(new Event('eip6963:requestProvider'));
  return { list, stop: () => win.removeEventListener('eip6963:announceProvider', onAnnounce) };
}

/** Load the Privy bundle once; resolves with window.SkiffPrivy when it is ready. */
function loadPrivy(win, src, config, timeoutMs = 20000) {
  if (win.__skiffPrivyLoading) return win.__skiffPrivyLoading;
  win.SKIFF_PRIVY = { ...(win.SKIFF_PRIVY || {}), ...config };
  win.__skiffPrivyLoading = new Promise((ok, no) => {
    const done = () => { if (win.SkiffPrivy && win.SkiffPrivy.ready) { win.removeEventListener('skiff-privy', done); ok(win.SkiffPrivy); } };
    win.addEventListener('skiff-privy', done);
    const s = win.document.createElement('script');
    s.src = src; s.async = true;
    s.onerror = () => { win.__skiffPrivyLoading = null; no(fail('engine_error', { message: 'wallet window did not load' })); };
    win.document.head.appendChild(s);
    setTimeout(() => { if (!(win.SkiffPrivy && win.SkiffPrivy.ready)) { win.__skiffPrivyLoading = null; no(fail('rpc_error', { message: 'wallet window timeout' })); } }, timeoutMs);
  });
  return win.__skiffPrivyLoading;
}
const until = (test, ms, step = 150) => new Promise((ok, no) => {
  const t0 = Date.now();
  (function tick() { if (test()) return ok(); if (Date.now() - t0 > ms) return no(fail('not_connected', { message: 'timeout' })); setTimeout(tick, step); })();
});

/**
 * The wallet manager the page uses.
 * opts: {privySrc: URL of the Privy bundle (site/privy/dist/privy.js, copied into web/assets/vendor/), privy: {appId, ...} (from /api/config.privyAppId),
 *        storage: localStorage by default, window}
 */
export function createWallet(opts = {}) {
  const win = opts.window || globalThis.window;
  const mem = store(opts.storage || (win && win.localStorage) || memStorage());
  const listeners = new Map();
  const state = { status: 'disconnected', account: null, chainId: null, kind: null, name: null, icon: null, rdns: null };
  let active = null;          // {via, provider, entry} | {via:'privy', privy}
  let unwire = () => {};
  let discovery = null;   // wallets may announce synchronously while watchInjected runs
  discovery = watchInjected((list) => emit('wallets', list), win);

  function emit(ev, payload) {
    for (const cb of listeners.get(ev) || []) { try { cb(payload); } catch (e) { console.error(e); } }
    if (ev !== 'change' && ev !== 'wallets') for (const cb of listeners.get('change') || []) { try { cb({ ...state }); } catch (e) { console.error(e); } }
  }
  function set(patch) { Object.assign(state, patch); }

  function wireInjected(provider) {
    const onAcc = (accs) => {
      const a = accs && accs[0] ? lc(accs[0]) : null;
      if (!a) { reset(); emit('disconnect', {}); return; }
      if (a !== state.account) { set({ account: a }); emit('accountsChanged', { account: a }); }
    };
    const onChain = (id) => { const n = Number(big(id)); if (n !== state.chainId) { set({ chainId: n }); emit('chainChanged', { chainId: n }); } };
    const onDisc = () => { reset(); emit('disconnect', {}); };
    if (provider.on) { provider.on('accountsChanged', onAcc); provider.on('chainChanged', onChain); provider.on('disconnect', onDisc); }
    return () => { if (provider.removeListener) { provider.removeListener('accountsChanged', onAcc); provider.removeListener('chainChanged', onChain); provider.removeListener('disconnect', onDisc); } };
  }
  function reset() { unwire(); unwire = () => {}; active = null; set({ status: 'disconnected', account: null, chainId: null, kind: null, name: null, icon: null, rdns: null }); }

  async function connectInjected(entry, silent) {
    const p = entry.provider;
    let accs;
    try { accs = await p.request({ method: silent ? 'eth_accounts' : 'eth_requestAccounts', params: [] }); }
    catch (e) { throw mapError(e); }
    if (!accs || !accs[0]) { if (silent) return false; throw fail('not_connected'); }
    const chainId = Number(big(await p.request({ method: 'eth_chainId', params: [] })));
    unwire(); active = { via: 'injected', provider: p, entry }; unwire = wireInjected(p);
    set({ status: 'connected', account: lc(accs[0]), chainId, kind: 'injected', name: entry.name, icon: entry.icon, rdns: entry.rdns });
    mem.set({ via: 'injected', rdns: entry.rdns || entry.id });
    emit('connect', { account: state.account, chainId });
    return true;
  }

  async function privy() {
    const cfg = opts.privy || {};
    if (!cfg.appId) throw fail('engine_error', { message: 'Privy app id missing (/api/config privyAppId)' });
    return loadPrivy(win, opts.privySrc || '/assets/vendor/privy.js', cfg);
  }
  function wirePrivy(P) {
    const on = (ev) => {
      const p = ev.detail;
      if (!p.ready) return;
      if (!p.authenticated || !p.address) { if (active && active.via === 'privy') { reset(); mem.del(); emit('disconnect', {}); } return; }
      const a = lc(p.address), kind = p.isEmbedded ? 'privy-embedded' : 'privy-external';
      const first = !(active && active.via === 'privy' && state.account);
      const accChanged = a !== state.account, chainChanged = p.chainId != null && p.chainId !== state.chainId;
      active = { via: 'privy', privy: p };
      set({ status: 'connected', account: a, chainId: p.chainId ?? state.chainId, kind, name: p.walletName || 'Wallet', icon: null, rdns: null });
      mem.set({ via: 'privy' });
      if (first) emit('connect', { account: a, chainId: state.chainId });
      else { if (accChanged) emit('accountsChanged', { account: a }); if (chainChanged) emit('chainChanged', { chainId: state.chainId }); }
    };
    win.addEventListener('skiff-privy', on);
    on({ detail: P });
    return () => win.removeEventListener('skiff-privy', on);
  }

  async function connectPrivy(silent) {
    set({ status: 'connecting' }); emit('change', { ...state });
    const P = await privy();
    unwire(); unwire = wirePrivy(P);
    if (!(P.authenticated && P.address)) {
      if (silent) { if (state.status !== 'connected') set({ status: 'disconnected' }); return false; }
      P.login();
      await until(() => win.SkiffPrivy.authenticated && win.SkiffPrivy.address, 10 * 60e3);
    }
    return true;
  }

  const api = {
    get state() { return { ...state }; },
    /** Detected injected wallets (EIP-6963) for the picker. Privy's own entry is added by the UI as «More wallets». */
    wallets: () => discovery.list(),
    on(ev, cb) { if (!listeners.has(ev)) listeners.set(ev, new Set()); listeners.get(ev).add(cb); return () => listeners.get(ev).delete(cb); },
    /** id: an injected wallet's id from wallets(), or 'privy'. */
    async connect(id) {
      if (id === 'privy') return connectPrivy(false);
      const entry = discovery.list().find((w) => w.id === id || w.rdns === id || w.uuid === id) || (id == null ? discovery.list()[0] : null);
      if (!entry) throw fail('no_wallet');
      set({ status: 'connecting' }); emit('change', { ...state });
      try { return await connectInjected(entry, false); } catch (e) { if (state.status === 'connecting') set({ status: 'disconnected' }); emit('change', { ...state }); throw e; }
    },
    /** Silent reconnect on page load: no prompt, no Privy bundle unless Privy was used before here. */
    async restore() {
      const last = mem.get();
      if (!last) return false;
      if (last.via === 'privy') { try { return await connectPrivy(true); } catch { return false; } }
      await new Promise((r) => setTimeout(r, 120));   // let wallets announce
      const entry = discovery.list().find((w) => w.rdns === last.rdns || w.id === last.rdns);
      if (!entry) return false;
      try { return await connectInjected(entry, true); } catch { return false; }
    },
    async disconnect() {
      const a = active;
      mem.del();
      if (a && a.via === 'privy') { try { await a.privy.logout(); } catch { /* signed out locally anyway */ } }
      else if (a && a.provider) { try { await a.provider.request({ method: 'wallet_revokePermissions', params: [{ eth_accounts: {} }] }); } catch { /* not every wallet can revoke: forgotten here */ } }
      reset(); emit('disconnect', {});
    },
    /** EIP-1193 provider of the connected wallet. */
    async provider() {
      if (!active) throw fail('not_connected');
      if (active.via === 'privy') return active.privy.provider();
      return active.provider;
    },
    /**
     * Switch the wallet to `chainId`, adding the network first when the wallet does not know it (EIP-3326 + EIP-3085).
     * Reads the chain back from the wallet: a wallet that "switches" without switching is caught here.
     */
    async switchChain(chainId) {
      if (!active) throw fail('not_connected');
      const id = Number(chainId);
      const viaProvider = async (p) => {   // EIP-3326 switch; unknown to the wallet (4902 / any non-rejection) -> EIP-3085 add, then switch again
        const hex = hexChainId(id);
        try { await p.request({ method: 'wallet_switchEthereumChain', params: [{ chainId: hex }] }); }
        catch (e) {
          const m = mapError(e, { stage: 'switch' });
          if (m.code === 'user_rejected') throw m;
          const params = addChainParams(id);   // the network's checked parameters (chains.js: pinned or engine-validated)
          if (!params) throw fail('chain_unsupported', { cause: e });
          try { await p.request({ method: 'wallet_addEthereumChain', params: [params] }); }
          catch (e2) { const m2 = mapError(e2, { stage: 'switch' }); throw m2.code === 'user_rejected' ? fail('chain_add_rejected', { cause: e2 }) : m2; }
          try { await p.request({ method: 'wallet_switchEthereumChain', params: [{ chainId: hex }] }); } catch (e3) { throw mapError(e3, { stage: 'switch' }); }
        }
      };
      if (active.via === 'privy') {
        // Privy switches (and adds) networks from its supportedChains (the bundle reads them from /api/tokens). A network its config does
        // not carry, or an external wallet behind Privy that does not know it -> the same add-then-switch on the wallet's own provider.
        try { await active.privy.switchChain(id); }
        catch (e) {
          const m = mapError(e, { stage: 'switch' });
          if (m.code === 'user_rejected') throw m;
          let p = null;
          try { p = await active.privy.provider(); } catch { throw m; }
          await viaProvider(p);
        }
      } else await viaProvider(active.provider);
      const p = await api.provider();
      const now = Number(big(await p.request({ method: 'eth_chainId', params: [] })));
      if (now !== id) throw fail('wrong_chain', { want: id, have: now });
      if (now !== state.chainId) { set({ chainId: now }); emit('chainChanged', { chainId: now }); }
      return now;
    },
    /** Privy embedded only: EIP-7702 authorization via useSign7702Authorization. */
    async signAuthorization(input) {
      if (!active || active.via !== 'privy' || state.kind !== 'privy-embedded') throw fail('batch_unsupported', { message: 'only an embedded wallet signs 7702 authorizations' });
      return active.privy.signAuthorization(input, { address: state.account });
    },
    chainName: (id) => (chainOf(id) || {}).name || null,
    /** Privy access token for the engine's signed-in routes (null for injected wallets: they have no Privy session). */
    async token() { return active && active.via === 'privy' ? active.privy.token() : null; },
  };
  return api;
}
