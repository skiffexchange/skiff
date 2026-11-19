// executeRoute(build): sign and send a route from POST /api/build in the person's own wallet, choosing the path:
//   '7702'     Privy embedded wallet: one type-4 transaction to itself, executeBatch([approve?, swap]) on Simple7702Account, carrying
//              the EIP-7702 authorization (nonce = current + 1) the first time; later trades are plain self-calls.
//   '5792'     a wallet that reports atomic batching on the chain: wallet_sendCalls([approve, swap]) + wallet_getCallsStatus.
//   'sequence' everyone else: approve (exact amount, only if allowance < amount) → receipt → swap → receipt.
// Steps are reported via onStep({state, …}) with state one of:
//   'switching' · 'approve-signing' · 'approve-pending' · 'swap-signing' · 'swap-pending' · 'bridge-pending' · 'done' · 'failed'
//   (+ 'fallback': information only — a 5792 wallet refused the batch, the same route continues step by step)
// Cross-chain: the source-chain transaction, then /api/status until the destination receipt on 4663 — never 'done' before it.
// Nothing here holds a key; every signature is the wallet's, after the person's click.
import { approveData, big, decodeApprove, lc, toQuantity } from './abi.js';
import { DIRECT, isDelegatedToUs, RH, SIMPLE7702, txUrl } from './chains.js';
import { choosePath, getCapabilities, parseCallsStatus, parseCapabilities } from './capabilities.js';
import { fail, mapError, SignError } from './errors.js';
import { preflight, reader } from './preflight.js';
import { authorizationNonce, normalizeAuthorization, parseSignedTx, recoverAuthority, serialize7702, signingHash7702, splitSignature } from './tx7702.js';

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function waitReceipt(rd, hash, { timeoutMs = 180000, pollMs = 700 } = {}) {
  const t0 = Date.now();
  for (let i = 0; Date.now() - t0 < timeoutMs; i++) {
    try { const r = await rd.receipt(hash); if (r && r.blockNumber) return r; } catch { /* the node may not know the hash for a moment */ }
    await sleep(i < 20 ? pollMs : pollMs * 3);
  }
  return null;   // sent, not confirmed: the caller reports pending, never failed
}
const okReceipt = (r) => r && (r.status === '0x1' || r.status === 1 || r.status === 'success');
/** Right before every signature: the wallet is still on the build's network (a person can switch networks between two steps). */
async function ensureChain(rd, chain) {
  let now;
  try { now = await rd.chainId(); } catch (e) { throw fail('rpc_error', { cause: e, stage: 'chain' }); }
  if (now !== Number(chain)) throw fail('wrong_chain', { want: Number(chain), have: now, stage: 'send' });
}

/**
 * opts:
 *   wallet   — from createWallet() (or any {state:{account,kind}, provider(), switchChain(id), signAuthorization(input)})
 *   asked    — what the person chose on the page: {fromChain, fromToken, toChain, toToken, amount (raw), minOut (raw), toAddress?,
 *              venue?, buildBody? (the POST /api/build body, used to rebuild on expiry)}
 *   engine   — createEngine() (status polling, rebuild on expiry)
 *   onStep   — callback
 *   mode     — 'auto' | '7702' | '5792' | 'sequence'
 * Resolves {status: 'done'|'pending', path, txHash, explorerUrl, outcome?, bridge?}. Throws SignError (quote_expired carries `fresh`).
 */
export async function executeRoute(build, opts) {
  const { wallet, asked, engine, onStep = () => {}, mode = 'auto' } = opts;
  const timing = { receiptTimeoutMs: opts.receiptTimeoutMs ?? 180000, pollMs: opts.pollMs ?? 700, bridgePollMs: opts.bridgePollMs ?? 6000,
                   bridgeTimeoutMs: opts.bridgeTimeoutMs ?? 30 * 60e3 };
  let path = null, lastHash = null;
  const emit = (state, extra = {}) => { try { onStep({ state, path, chainId: Number(build && build.chainId), ...extra }); } catch (e) { console.error(e); } };
  try {
    const account = lc(wallet.state.account);
    if (!account) throw fail('not_connected');
    let provider = await wallet.provider();
    let rd = opts.rd || reader(provider);
    const walletKind = wallet.state.kind;
    const chain = Number(build.chainId);
    // capabilities first (they decide the path, and the path decides gas and what is vetted)
    let caps = null;
    if (walletKind !== 'privy-embedded' && (build.sequence || []).length > 1 && mode !== 'sequence') caps = parseCapabilities(await getCapabilities(provider, account, chain), chain);
    path = choosePath({ walletKind, caps, build, mode });
    let walletChainId = await rd.chainId();
    let pf = await preflight(build, asked, { account, walletChainId, rd, path, now: opts.now });
    if (pf.issues.some((x) => x.code === 'wrong_chain')) {
      emit('switching', { want: chain });
      await wallet.switchChain(chain);
      provider = await wallet.provider(); rd = opts.rd || reader(provider);
      walletChainId = await rd.chainId();
      pf = await preflight(build, asked, { account, walletChainId, rd, path, now: opts.now });
    }
    const expired = pf.issues.find((x) => x.code === 'quote_expired');
    if (expired) throw await expiredError(engine, asked, {});
    const blocking = pf.issues.find((x) => x.code !== 'wrong_chain');
    if (blocking) throw blocking;
    if (pf.issues.length) throw pf.issues[0];

    let sent;
    if (path === '7702') sent = await run7702(build, { account, provider, rd, wallet, emit, timing, rand: opts.rand });
    else if (path === '5792') {
      try { sent = await run5792(build, { account, provider, rd, emit, timing }); }
      catch (e) {
        const m = mapError(e, { stage: 'batch' });
        if (m.code !== 'batch_unsupported' && m.code !== 'batch_declined') throw m;
        path = 'sequence';
        emit('fallback', { from: '5792', to: 'sequence', note: m.code });
        sent = await runSequence(build, { account, provider, rd, emit, timing, facts: pf.facts, engine, asked, now: opts.now });
      }
    } else sent = await runSequence(build, { account, provider, rd, emit, timing, facts: pf.facts, engine, asked, now: opts.now });
    lastHash = sent.txHash;
    const explorerUrl = txUrl(chain, sent.txHash);
    if (sent.pending) { emit('swap-pending', { txHash: sent.txHash, explorerUrl, long: true }); return { status: 'pending', path, txHash: sent.txHash, explorerUrl }; }

    if (chain === RH) {   // same chain: the receipt on 4663 is the destination receipt
      emit('done', { txHash: sent.txHash, explorerUrl, receipt: sent.receipt });
      return { status: 'done', path, txHash: sent.txHash, explorerUrl, outcome: 'complete', receipt: sent.receipt };
    }
    return await trackBridge({ engine, build, asked, txHash: sent.txHash, explorerUrl, emit, timing, path });
  } catch (e0) {
    const e = e0 instanceof SignError ? e0 : mapError(e0, { stage: 'send' });
    if (e.code !== 'quote_expired') emit('failed', { error: { code: e.code, reason: e.reason, fix: e.fix, why: e.why }, txHash: e.txHash || lastHash, explorerUrl: txUrl(Number(build && build.chainId), e.txHash || lastHash) });
    throw e;
  }
}

/** quote_expired carrying a fresh build (never signed automatically: the page shows the new price, the person confirms again). */
async function expiredError(engine, asked, extra) {
  let fresh = null;
  if (engine && asked && asked.buildBody) { try { fresh = await engine.build(asked.buildBody); } catch (e) { return fail('quote_expired', { ...extra, rebuildError: e.code || String(e) }); } }
  return fail('quote_expired', { ...extra, fresh });
}

// ---------------------------------------------------------------- sequence
async function sendTx(provider, tx) {
  return provider.request({ method: 'eth_sendTransaction', params: [tx] });
}
async function runSequence(build, { account, provider, rd, emit, timing, facts, engine, asked, now }) {
  const chain = Number(build.chainId), seq = build.sequence;
  const approvals = seq.slice(0, -1), swap = seq[seq.length - 1];   // every step before the swap is a permission (1 for a route or V3, up to 2 for V4)
  const base = { from: account, chainId: toQuantity(chain) };
  const txs = [];
  for (const s of approvals) {
    const a = decodeApprove(s.data);
    if (a && facts && facts.resetFirst && !txs.length) txs.push({ ...base, to: s.to, data: approveData(a.spender, 0), value: '0x0' });   // USDT-style: 0 first
    txs.push({ ...base, to: s.to, data: s.data, value: '0x0', ...(s.gasLimit ? { gas: s.gasLimit } : {}) });
  }
  for (let i = 0; i < txs.length; i++) {
    const step = { step: i + 1, of: txs.length + 1, permit2: lc(txs[i].to) === DIRECT.permit2 };
    emit('approve-signing', step);
    await ensureChain(rd, chain);
    const h = await sendTx(provider, txs[i]).catch((e) => { throw mapError(e); });
    emit('approve-pending', { ...step, txHash: h, explorerUrl: txUrl(chain, h) });
    const r = await waitReceipt(rd, h, timing);
    if (!r) throw fail('pending_long', { txHash: h, stage: 'approve' });
    if (!okReceipt(r)) throw fail('reverted', { txHash: h, stage: 'approve' });
  }
  if (txs.length) {   // the approval may have taken long: an expired route is rebuilt and shown again, the approval stays
    const t = now ?? Math.floor(Date.now() / 1000);
    if (t > Number(build.builtAt || 0) + Number(build.expiresInS || 0) - 5) throw await expiredError(engine, asked, { approved: true });
  }
  const step = { step: txs.length + 1, of: txs.length + 1 };
  emit('swap-signing', step);
  await ensureChain(rd, chain);
  const h = await sendTx(provider, { ...base, to: swap.to, data: swap.data, value: toQuantity(swap.value || 0), ...(swap.gasLimit ? { gas: swap.gasLimit } : {}) })
    .catch((e) => { throw mapError(e); });
  emit('swap-pending', { ...step, txHash: h, explorerUrl: txUrl(chain, h) });
  const r = await waitReceipt(rd, h, timing);
  if (!r) return { txHash: h, pending: true };
  if (!okReceipt(r)) throw fail('reverted', { txHash: h, stage: 'swap' });
  return { txHash: h, receipt: r };
}

// ---------------------------------------------------------------- EIP-5792
async function run5792(build, { account, provider, rd, emit, timing }) {
  const chain = Number(build.chainId);
  const calls = build.sequence.map((s) => ({ to: s.to, data: s.data, value: toQuantity(s.value || 0) }));
  emit('swap-signing', { batch: true, calls: calls.length });
  if (rd) await ensureChain(rd, chain);
  let res;
  try {
    res = await provider.request({ method: 'wallet_sendCalls', params: [{ version: '2.0.0', chainId: toQuantity(chain), from: account, atomicRequired: true, calls }] });
  } catch (e) {
    const m = String(e && e.message || '').toLowerCase();
    if (!(e && e.code === -32602) && !/version/.test(m)) throw e;
    res = await provider.request({ method: 'wallet_sendCalls', params: [{ version: '1.0', chainId: toQuantity(chain), from: account, calls }] });   // pre-2.0 wallets
  }
  const id = typeof res === 'string' ? res : res && res.id;
  if (!id) throw fail('unknown', { message: 'wallet_sendCalls returned no id' });
  emit('swap-pending', { callsId: id });
  const t0 = Date.now();
  for (let i = 0; Date.now() - t0 < timing.receiptTimeoutMs; i++) {
    let st = null;
    try { st = parseCallsStatus(await provider.request({ method: 'wallet_getCallsStatus', params: [id] })); } catch { /* not known yet */ }
    if (st && st.state === 'unconfirmed') return { txHash: null, callsId: id, pending: true };   // the wallet says done but shows no receipt
    if (st && st.state !== 'pending') {
      const last = st.receipts[st.receipts.length - 1];
      const h = last && last.transactionHash;
      if (st.state === 'done') { emit('swap-pending', { txHash: h, explorerUrl: txUrl(chain, h), callsId: id }); return { txHash: h, receipt: last, callsId: id }; }
      throw fail('reverted', { txHash: h, callsId: id, batchState: st.state });
    }
    await sleep(i < 20 ? timing.pollMs : timing.pollMs * 3);
  }
  return { txHash: null, callsId: id, pending: true };
}

// ---------------------------------------------------------------- EIP-7702 (Privy embedded)
/**
 * One type-4 transaction from the wallet to itself. The authorization is checked (delegate, chain, nonce = tx nonce + 1, signer =
 * this account) and the signed transaction is parsed and checked (type 4, our calldata, our authorization, sender) BEFORE it is
 * broadcast — a wallet that silently drops `authorizationList` would otherwise send a self-call that does nothing.
 */
async function run7702(build, { account, provider, rd, wallet, emit, timing, rand = Math.random }) {
  const b = build.batch7702;
  const code = lc(await rd.code(account));
  const delegated = isDelegatedToUs(code);
  if (!delegated && code !== '0x' && code !== '') throw fail('unsafe_build', { why: 'account has other code' });
  const nonce = await rd.nonce(account);
  let auth = null;
  if (!delegated) {
    emit('swap-signing', { sub: 'authorization' });
    const want = authorizationNonce(nonce, true);
    const sign = async (input) => normalizeAuthorization(await wallet.signAuthorization(input));
    try {
      auth = await sign({ contractAddress: SIMPLE7702, chainId: RH, executor: 'self' });
      if (auth.nonce !== want) auth = await sign({ contractAddress: SIMPLE7702, chainId: RH, nonce: want });   // an SDK without `executor`
    } catch (e) { throw mapError(e); }
    if (auth.address !== SIMPLE7702 || auth.chainId !== RH || auth.nonce !== want) throw fail('authorization_mismatch', { auth });
    if (lc(recoverAuthority(auth)) !== account) throw fail('authorization_mismatch', { auth, why: 'signer' });
  }
  // fresh fees, not a constant: base fee × 1.5–2.5; Nitro ignores the tip
  let baseFee = null;
  try { baseFee = await rd.baseFee(); } catch { /* fall back below */ }
  if (baseFee === null) baseFee = await rd.gasPrice();
  const maxFeePerGas = baseFee * BigInt(150 + Math.floor(rand() * 100)) / 100n + 1n;
  let gas = big(b.gasLimit || 0) + (auth ? 25_000n : 0n);
  const value = big(b.value || 0);
  const est = { from: account, to: account, data: b.data, value: toQuantity(value) };
  if (auth) Object.assign(est, { type: '0x4', authorizationList: [{ chainId: toQuantity(auth.chainId), address: auth.address, nonce: toQuantity(auth.nonce), yParity: toQuantity(auth.yParity), r: auth.r, s: auth.s }] });
  try { const g = await rd.estimateGas(est); if (g * 12n / 10n > gas) gas = g * 12n / 10n; } catch { /* the engine's simulated limit stays */ }
  emit('swap-signing', { sub: 'transaction' });
  await ensureChain(rd, RH);
  let hash;
  if (!auth) {   // already delegated to Simple7702Account: a plain self-call
    hash = await provider.request({ method: 'eth_sendTransaction', params: [{ from: account, to: account, data: b.data, value: toQuantity(value), gas: toQuantity(gas), chainId: toQuantity(RH) }] })
      .catch((e) => { throw mapError(e); });
  } else {
    const tx = { chainId: RH, nonce, maxPriorityFeePerGas: 0n, maxFeePerGas, gas, to: account, value, data: b.data, accessList: [], authorizationList: [auth] };
    const raw = await signType4(provider, account, tx);
    hash = await provider.request({ method: 'eth_sendRawTransaction', params: [raw.raw] }).catch((e) => { throw mapError(e); });
    if (lc(hash) !== lc(raw.hash)) throw fail('unknown', { message: 'node returned another hash', hash, expected: raw.hash });
  }
  emit('swap-pending', { txHash: hash, explorerUrl: txUrl(RH, hash) });
  const r = await waitReceipt(rd, hash, timing);
  if (!r) return { txHash: hash, pending: true };
  if (!okReceipt(r)) throw fail('reverted', { txHash: hash, stage: 'batch' });
  if (auth && !isDelegatedToUs(await rd.code(account))) throw fail('reverted', { txHash: hash, why: 'delegation missing after the transaction' });
  return { txHash: hash, receipt: r, delegatedNow: !!auth };
}

/** Have the embedded wallet sign `tx` as type 4; verify every field of what came back; fall back to secp256k1_sign over our own hash. */
async function signType4(provider, account, tx) {
  const check = (raw) => {
    const p = parseSignedTx(raw);
    const a = p.authorizationList && p.authorizationList[0], want = tx.authorizationList[0];
    const ok = p.type === 4 && lc(p.from) === account && lc(p.to) === account && lc(p.data) === lc(tx.data) && p.nonce === big(tx.nonce) &&
      p.chainId === BigInt(RH) && p.value === tx.value && p.authorizationList.length === 1 && a && lc(a.address) === want.address &&
      a.nonce === want.nonce && a.chainId === want.chainId && lc(a.r) === lc(want.r) && lc(a.s) === lc(want.s);
    return ok ? { raw, hash: p.hash } : null;
  };
  const rpcTx = { from: account, to: account, data: tx.data, value: toQuantity(tx.value), gas: toQuantity(tx.gas), nonce: toQuantity(tx.nonce),
                  maxFeePerGas: toQuantity(tx.maxFeePerGas), maxPriorityFeePerGas: '0x0', chainId: toQuantity(RH), type: 4,   // type 4 explicitly: without it Privy serializes 1559 and drops the authorization
                  authorizationList: tx.authorizationList.map((a) => ({ address: a.address, chainId: a.chainId, nonce: a.nonce, yParity: a.yParity, r: a.r, s: a.s })) };
  try {
    const raw = await provider.request({ method: 'eth_signTransaction', params: [rpcTx] });
    const ok = typeof raw === 'string' ? check(raw) : null;
    if (ok) return ok;
  } catch (e) {
    const m = mapError(e);
    if (m.code === 'user_rejected') throw m;
  }
  // escape hatch (knowledge doc §1): the wallet signs the raw hash, we assemble the type-4 ourselves
  let sigHex;
  try { sigHex = await provider.request({ method: 'secp256k1_sign', params: [signingHash7702(tx)] }); } catch (e) { throw mapError(e); }
  const raw = serialize7702(tx, splitSignature(sigHex));
  const ok = check(raw);
  if (!ok) throw fail('authorization_mismatch', { why: 'signed transaction does not match' });
  return ok;
}

// ---------------------------------------------------------------- cross-chain
async function trackBridge({ engine, build, asked, txHash, explorerUrl, emit, timing, path }) {
  if (!engine) return { status: 'pending', path, txHash, explorerUrl };
  const venue = asked && asked.venue || (build.venue && build.venue.displayKey);
  const fromChain = Number(build.chainId);
  const t0 = Date.now();
  emit('bridge-pending', { txHash, explorerUrl, status: 'PENDING' });
  while (Date.now() - t0 < timing.bridgeTimeoutMs) {
    await sleep(timing.bridgePollMs);
    let st = null;
    try { st = await engine.status({ txHash, fromChain, toChain: RH, venue }); } catch { continue; }
    const s = String(st && st.status || 'UNKNOWN').toUpperCase(), sub = String(st && st.substatus || '').toUpperCase();
    if (s === 'FAILED') throw fail('bridge_failed', { txHash, bridge: st });
    if (s === 'DONE' && sub === 'REFUNDED') throw fail('bridge_refunded', { txHash, bridge: st });
    if (s === 'DONE') {
      const dest = st.receiving && st.receiving.txHash;
      if (!dest) { emit('bridge-pending', { txHash, explorerUrl, status: s, substatus: sub, waiting: 'destination' }); continue; }
      let d = null;   // the destination receipt on 4663 itself, through the engine's same-chain status
      try { d = await engine.status({ txHash: dest, fromChain: RH, toChain: RH }); } catch { continue; }
      if (!d || d.status === 'PENDING') { emit('bridge-pending', { txHash, explorerUrl, status: s, substatus: sub, destTxHash: dest }); continue; }
      if (d.status !== 'DONE') throw fail('bridge_failed', { txHash, destTxHash: dest, bridge: st });
      const partial = sub === 'PARTIAL';
      const destUrl = txUrl(RH, dest);
      emit('done', { txHash, explorerUrl, destTxHash: dest, destExplorerUrl: destUrl, outcome: partial ? 'partial' : 'complete', ...(partial ? { code: 'bridge_partial', received: st.receiving } : {}) });
      return { status: 'done', path, txHash, explorerUrl, destTxHash: dest, destExplorerUrl: destUrl, outcome: partial ? 'partial' : 'complete', bridge: st };
    }
    emit('bridge-pending', { txHash, explorerUrl, status: s, substatus: sub, message: st && st.substatusMessage, stale: !!(st && st.stale) });
  }
  emit('swap-pending', { txHash, explorerUrl, long: true });
  return { status: 'pending', path, txHash, explorerUrl };
}
