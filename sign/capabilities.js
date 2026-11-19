// Which way a wallet signs a route: EIP-7702 batch (Privy embedded), EIP-5792 call batch (wallets that report atomic support on the
// chain), or plain sequential transactions. Spec: https://eips.ethereum.org/EIPS/eip-5792 (wallet_getCapabilities → per-chain
// `atomic.status` "supported" | "ready" | "unsupported"; the pre-2.0 drafts used `atomicBatch.supported: true`).
import { lc } from './abi.js';
import { hexChainId } from './chains.js';

function withTimeout(p, ms) {
  let t;
  const timer = new Promise((_, rej) => { t = setTimeout(() => rej(Object.assign(new Error('timeout'), { code: 'timeout' })), ms); });
  return Promise.race([Promise.resolve(p).finally(() => clearTimeout(t)), timer]);
}

/**
 * Normalize a wallet_getCapabilities answer for one chain → {atomic: 'supported'|'ready'|'unsupported', legacy: bool, raw}.
 * Accepts hex keys in any case, decimal keys, and the "0x0" all-chains key (EIP-5792: capabilities under 0x0 apply to every chain).
 */
export function parseCapabilities(result, chainId) {
  const none = { atomic: 'unsupported', legacy: false, raw: null };
  if (!result || typeof result !== 'object') return none;
  const want = Number(chainId);
  let entry = null, all = null;
  for (const [k, v] of Object.entries(result)) {
    const id = /^0x/i.test(k) ? parseInt(k, 16) : /^[0-9]+$/.test(k) ? Number(k) : NaN;
    if (id === want) entry = v;
    else if (id === 0) all = v;
  }
  const caps = { ...(all || {}), ...(entry || {}) };
  if (!entry && !all) return none;
  const a = caps.atomic;
  if (a && typeof a === 'object' && typeof a.status === 'string') {
    const s = lc(a.status);
    return { atomic: s === 'supported' || s === 'ready' ? s : 'unsupported', legacy: false, raw: caps };
  }
  if (caps.atomicBatch && caps.atomicBatch.supported === true) return { atomic: 'supported', legacy: true, raw: caps };
  return { atomic: 'unsupported', legacy: false, raw: caps };
}

/** wallet_getCapabilities(account, [chainId]) with a fallback to the one-argument form; null when the wallet has no such method. */
export async function getCapabilities(provider, account, chainId, { timeoutMs = 4000 } = {}) {
  const ask = (params) => withTimeout(provider.request({ method: 'wallet_getCapabilities', params }), timeoutMs);
  try { return await ask([account, [hexChainId(chainId)]]); }
  catch (e) {
    if (e && (e.code === 4001 || e.code === 4100)) return null;
    try { return await ask([account]); } catch { return null; }
  }
}

/**
 * The path for this build and wallet.
 *   '7702'     — Privy embedded wallet on 4663 and the engine simulated the batch OK (batch7702.available).
 *   '5792'     — the wallet reports atomic "supported" or "ready" on the build's chain and there is more than one call.
 *   'sequence' — everything else: [approve if allowance < amount, swap], one transaction each.
 * `mode` forces a path when it is possible ('auto' | '7702' | '5792' | 'sequence').
 */
export function choosePath({ walletKind, caps, build, mode = 'auto' }) {
  const seq = (build && build.sequence) || [];
  const can7702 = walletKind === 'privy-embedded' && build && Number(build.chainId) === 4663 && build.batch7702 && build.batch7702.available === true;
  const can5792 = !!caps && (caps.atomic === 'supported' || caps.atomic === 'ready') && seq.length > 1;
  if (mode === '7702') return can7702 ? '7702' : 'sequence';
  if (mode === '5792') return can5792 ? '5792' : 'sequence';
  if (mode === 'sequence') return 'sequence';
  if (can7702) return '7702';
  if (can5792) return '5792';
  return 'sequence';
}

/** EIP-5792 status → {state: 'pending'|'done'|'unconfirmed'|'failed'|'reverted', receipts}. v2 numeric codes, v1 strings. */
export function parseCallsStatus(st) {
  if (!st) return { state: 'pending', receipts: [] };
  const receipts = st.receipts || [];
  const allOk = receipts.length > 0 && receipts.every((r) => r && (r.status === '0x1' || r.status === 1 || r.status === 'success'));
  const s = st.status;
  if (typeof s === 'number' || /^[0-9]+$/.test(String(s))) {
    const n = Number(s);
    if (n >= 100 && n < 200) return { state: 'pending', receipts };
    if (n >= 200 && n < 300) return { state: receipts.length === 0 ? 'unconfirmed' : allOk ? 'done' : 'reverted', receipts };   // no receipts: cannot be confirmed here
    if (n >= 400 && n < 500) return { state: 'failed', receipts };   // not included, wallet will not retry
    return { state: 'reverted', receipts };                          // 500 full revert, 600 partial revert
  }
  const t = lc(s);
  if (t === 'confirmed') return { state: receipts.length === 0 ? 'unconfirmed' : allOk ? 'done' : 'reverted', receipts };
  if (t === 'pending' || t === '') return { state: 'pending', receipts };
  return { state: 'failed', receipts };
}
