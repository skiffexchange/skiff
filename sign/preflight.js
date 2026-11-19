// Before the first signature: (1) vetBuild — the transactions are exactly the trade on screen (targets, approve amount, batch bytes);
// (2) preflight — chain, account, expiry, balance, gas, simulated output vs minimum. Plain reasons, stable codes (errors.js).
// Reads go through the person's own wallet provider (or an injected reader), never through our server.
import { allowanceData, balanceOfData, big, decodeApprove, decodeExactInputSingle02, decodeExecuteBatch, decodePermit2Approve, decodeUrV4Single, encodeExecuteBatch, isAddress, lc, ZERO } from './abi.js';
import { APPROVE_RESET_FIRST, DIRECT, L1_FEE_ORACLE, ROUTE_TARGETS, signable, SIMPLE7702, WETH_4663 } from './chains.js';
import { fail } from './errors.js';

/** JSON-RPC reads over an EIP-1193 provider. */
export function reader(provider) {
  const rq = (method, params) => provider.request({ method, params });
  return {
    chainId: async () => Number(big(await rq('eth_chainId', []))),
    call: (to, data, block = 'latest') => rq('eth_call', [{ to, data }, block]),
    balance: async (a) => big(await rq('eth_getBalance', [a, 'latest'])),
    code: (a) => rq('eth_getCode', [a, 'latest']),
    nonce: async (a) => Number(big(await rq('eth_getTransactionCount', [a, 'pending']))),
    gasPrice: async () => big(await rq('eth_gasPrice', [])),
    baseFee: async () => { const b = await rq('eth_getBlockByNumber', ['latest', false]); return b && b.baseFeePerGas ? big(b.baseFeePerGas) : null; },
    estimateGas: async (tx) => big(await rq('eth_estimateGas', [tx])),
    receipt: (h) => rq('eth_getTransactionReceipt', [h]),
  };
}
const word0 = (hex) => big('0x' + (lc(hex).slice(2, 66) || '0'));
/** No real amount comes near 2^128 raw (3.4e20 tokens at 18 decimals); an approve at or above it is treated as unlimited (max uint160 / uint256 too). */
const MAX_SANE = 1n << 128n;
const isCode = (c) => { const x = lc(c || '0x'); return x.length > 2 && !/^0xef0100[0-9a-f]{40}$/.test(x); };   // an EIP-7702 designator is an EOA

/** The executeBatch calls the sequence implies: [approve?, swap] — the only batch we ever sign. */
export function batchCallsOf(build) {
  return (build.sequence || []).map((s) => ({ target: lc(s.to), value: big(s.value || 0), data: lc(s.data) }));
}

/**
 * Throws SignError('unsafe_build', {why}) unless `build` is exactly the trade the person asked for.
 * asked: {fromChain, fromToken, amount (raw string), toAddress?}. account: the connected address.
 */
export function vetBuild(build, asked, account, { path, now: at } = {}) {
  const bad = (why) => { throw fail('unsafe_build', { why, build }); };
  if (!build || build.ok !== true) bad('not ok');
  if (!asked || !isAddress(account)) bad('no trade');
  const chain = Number(build.chainId);
  if (chain !== Number(asked.fromChain ?? 4663)) bad('chain');
  if (lc(build.from) !== lc(account)) throw fail('account_mismatch', { build });
  const sg = signable(chain);   // a network this layer cannot verify (not pinned, or the engine disagrees with the pin) is never signed
  if (!sg.ok || !ROUTE_TARGETS[chain]) throw fail('chain_unverified', { chainId: chain, why: sg.why || 'network not configured', build });
  const seq = build.sequence || [];
  if (seq.length < 1 || seq.length > 3) bad('steps');
  const swap = seq[seq.length - 1], approvals = seq.slice(0, -1);
  if (swap.kind !== 'swap') bad('step kinds');
  const amount = big(asked.amount), native = lc(asked.fromToken) === ZERO, toNative = lc(asked.toToken) === ZERO;
  if (amount <= 0n || amount >= MAX_SANE) bad('amount');
  for (const s of seq) if (!isAddress(s.to) || !/^0x([0-9a-f]{2})+$/.test(lc(s.data))) bad('format');
  for (const s of seq) if (s.chainId != null && Number(big(s.chainId)) !== chain) bad('step chain');   // a step for another network
  for (const s of approvals) if (big(s.value || 0) !== 0n) bad('value on approve');
  const target = lc(swap.to);
  if (!ROUTE_TARGETS[chain].includes(target)) bad('swap target');
  const value = big(swap.value || 0);
  // `value` on a token swap (or above the amount on a native one) can only be the route's own fee in the network's coin
  // (build.fees.nativeFee, e.g. a messaging fee of the bridge), and only up to what the page showed the person (asked.nativeFee, raw).
  const extra = native ? (value > amount ? value - amount : 0n) : value;
  if (extra > 0n) {
    const nf = build.fees && build.fees.nativeFee;
    if (!nf || Number(nf.chainId ?? chain) !== chain || extra > big(nf.raw || 0)) bad('value');
    if (asked.nativeFee == null || extra > big(asked.nativeFee)) bad('native fee not shown');
  }
  if (native && approvals.length) bad('approve on native');
  const me = lc(account), recv = asked.toAddress ? lc(asked.toAddress) : me;
  const minOut = big(asked.minOut || 0), now = BigInt(at ?? Math.floor(Date.now() / 1000));
  const erc20Approve = (s, spender) => {   // ERC-20 approve on the input token, to `spender`, exactly the amount (never unlimited)
    const a = decodeApprove(s.data);
    if (lc(s.to) !== lc(asked.fromToken) || !a) bad('approve');
    if (lc(a.spender) !== spender) bad('spender');
    if (a.amount !== amount || a.amount >= MAX_SANE) bad('approve amount');   // exactly the amount, never unlimited
  };
  if (chain === 4663 && target === DIRECT.universalRouter) {
    // Uniswap V4 direct: [ERC-20 approve → Permit2]? [Permit2.approve(token → UniversalRouter)]? then execute(V4 exact-in-single)
    let i = 0;
    if (approvals[i] && decodeApprove(approvals[i].data)) { erc20Approve(approvals[i], DIRECT.permit2); i++; }
    if (approvals[i]) {
      const p = decodePermit2Approve(approvals[i].data);
      if (lc(approvals[i].to) !== DIRECT.permit2 || !p) bad('permit2 approve');
      if (p.token !== lc(asked.fromToken) || p.spender !== DIRECT.universalRouter || p.amount !== amount) bad('permit2 approve fields');
      if (p.expiration <= now || p.expiration > now + 30n * 86400n) bad('permit2 expiration');
      i++;
    }
    if (i !== approvals.length) bad('step order');
    const v = decodeUrV4Single(swap.data);
    if (!v) bad('v4 shape');
    const cin = native ? ZERO : lc(asked.fromToken), cout = toNative ? ZERO : lc(asked.toToken);
    const [k0, k1] = v.zeroForOne ? [v.key.currency0, v.key.currency1] : [v.key.currency1, v.key.currency0];
    if (k0 !== cin || k1 !== cout || v.settle.currency !== cin || v.take.currency !== cout) bad('v4 tokens');
    if (v.amountIn !== amount || v.settle.amount !== amount) bad('v4 amount');
    if (v.take.min === 0n || v.take.min < minOut) bad('v4 minimum');   // TAKE_ALL is what must come out — never 0
    if (recv !== me) bad('recipient');                                   // TAKE_ALL pays msg.sender: the wallet itself
    if (v.deadline < now) bad('deadline');
  } else if (chain === 4663 && target === DIRECT.swapRouter02) {
    // Uniswap V3 direct: [ERC-20 approve → SwapRouter02]? then exactInputSingle (optionally inside multicall(deadline, [·]))
    if (approvals.length > 1) bad('steps');
    if (approvals[0]) erc20Approve(approvals[0], DIRECT.swapRouter02);
    const v = decodeExactInputSingle02(swap.data);
    if (!v) bad('v3 shape');
    if (v.tokenIn !== (native ? WETH_4663 : lc(asked.fromToken)) || v.tokenOut !== lc(asked.toToken) || toNative) bad('v3 tokens');
    if (v.amountIn !== amount || (native && value !== amount)) bad('v3 amount');
    if (v.recipient !== recv) bad('recipient');
    if (v.minOut === 0n || v.minOut < minOut) bad('v3 minimum');
    if (v.deadline !== null && v.deadline < now) bad('deadline');
  } else {
    // a route through the provider's contract: [approve(0) for a USDT-style token]? [ERC-20 approve → that contract]? then its transaction
    let ap = approvals;
    if (ap.length === 2) {   // the engine's reset step (kind approve-reset): approve(that same contract, 0) on the same token, only for listed tokens
      const z = decodeApprove(ap[0].data);
      if (ap[0].kind !== 'approve-reset' || !z || z.amount !== 0n || lc(ap[0].to) !== lc(asked.fromToken) || lc(z.spender) !== target ||
          !(APPROVE_RESET_FIRST[chain] || []).includes(lc(asked.fromToken))) bad('approve reset');
      ap = ap.slice(1);
    }
    if (ap.length > 1) bad('steps');
    if (ap[0]) erc20Approve(ap[0], target);
    if (!lc(swap.data).includes(recv.slice(2))) bad('recipient');   // heuristic: a route built for someone else is refused
  }
  if (path === '7702') {
    const b = build.batch7702;
    if (!b || b.available !== true || chain !== 4663) bad('no batch');
    if (lc(b.to) !== lc(account)) bad('batch to');
    const mine = encodeExecuteBatch(batchCallsOf(build));
    if (lc(b.data) !== mine || !decodeExecuteBatch(b.data)) bad('batch bytes');   // byte-equal to our own encoding of the vetted sequence
    if (big(b.value || 0) !== value) bad('batch value');
    const au = b.authorization || {};
    if (au.required && (lc(au.contractAddress) !== SIMPLE7702 || Number(au.chainId) !== 4663)) bad('delegate');
  }
  return true;
}

/** Network fee upper bound for the transactions of `path`, in the chain's native coin (raw). */
export async function gasCost(build, path, rd) {
  const seq = build.sequence || [];
  let gas = 0n;
  if (path === '7702') gas = big(build.batch7702.gasLimit || 0) + (build.batch7702.authorization?.required ? 25_000n : 0n);   // PER_EMPTY_ACCOUNT_COST
  else for (const s of seq) gas += big(s.gasLimit || 300_000);
  let price;
  try { price = await rd.gasPrice(); } catch { price = null; }
  if (price === null) return null;
  let cost = gas * price * 12n / 10n;
  const oracle = L1_FEE_ORACLE[Number(build.chainId)];
  if (oracle) {   // L1 data fee billed outside gas × price (OP stack, Scroll, Morph): getL1Fee(bytes) on the network's oracle, doubled + 10%
    for (const s of (path === '7702' ? [build.batch7702] : seq)) {
      try {
        const d = (s.data || '0x').slice(2), len = d.length / 2;
        const arg = '0x49948e0e' + (32).toString(16).padStart(64, '0') + len.toString(16).padStart(64, '0') + d.padEnd(Math.ceil(d.length / 64) * 64, '0');
        cost += word0(await rd.call(oracle, arg)) * 22n / 10n;
      } catch { /* not fatal: the wallet's own estimate still applies */ }
    }
  }
  return cost;
}

/**
 * All checks before signing. Returns {ok, issues: SignError[], facts}. `ok` is false when any issue blocks signing;
 * wrong_chain alone is fixable (executeRoute switches the wallet first).
 * ctx: {account, walletChainId, provider | rd, path, now}
 */
export async function preflight(build, asked, ctx) {
  const issues = [], facts = {};
  const rd = ctx.rd || reader(ctx.provider);
  const add = (code, extra) => issues.push(fail(code, extra));
  if (build && (Number(build.chainId) === 1151111081099710 || build.solana)) { add('solana_unsupported'); return { ok: false, issues, facts }; }
  try { vetBuild(build, asked, ctx.account, { path: ctx.path, now: ctx.now }); } catch (e) { issues.push(e); return { ok: false, issues, facts }; }
  if (ctx.walletChainId != null && Number(ctx.walletChainId) !== Number(build.chainId)) add('wrong_chain', { want: Number(build.chainId), have: Number(ctx.walletChainId) });
  const now = ctx.now ?? Math.floor(Date.now() / 1000);
  facts.expiresAt = Number(build.builtAt || 0) + Number(build.expiresInS || 0);
  if (now > facts.expiresAt - 5) add('quote_expired', { expiresAt: facts.expiresAt });
  if (build.simulation && build.simulation.status === 'reverted') add('simulation_failed', { simulation: build.simulation });
  const minOut = big(asked.minOut || 0);
  if (minOut > 0n) {
    const same = Number(build.chainId) === 4663 && build.delivery !== 'intent';
    const got = same ? big(build.simulation?.out?.raw ?? 0) : big(build.minOutRouter?.raw ?? 0);
    if (got < minOut) add('below_min', { got: got.toString(), minOut: minOut.toString() });
    if (ctx.path === '7702' && same && big(build.batch7702?.simulatedOut?.raw ?? 0) < minOut) add('below_min', { batch: true });
  }
  // balance and gas — read fresh, integers only, on the chain the wallet signs on (only meaningful when the wallet is on it)
  const onChain = ctx.walletChainId == null || Number(ctx.walletChainId) === Number(build.chainId);
  const amount = big(asked.amount), native = lc(asked.fromToken) === ZERO;
  const seq = build.sequence || [], swapValue = big((seq[seq.length - 1] || {}).value || 0);
  const feeOnTop = native ? (swapValue > amount ? swapValue - amount : 0n) : swapValue;   // the route's own fee in the network's coin (vetted)
  if (onChain) {
    // every contract the transactions call must have code on this network, read through the person's own wallet: the route contract
    // (and the token being approved). Could not read -> not verified -> not signed.
    try {
      facts.code = {};
      for (const t of [...new Set(seq.map((s) => lc(s.to)))]) {
        const c = await rd.code(t);
        facts.code[t] = isCode(c);
        if (!facts.code[t]) add('unsafe_build', { why: 'no contract at ' + t });
      }
    } catch (e) { add('chain_unverified', { why: 'contract check failed', cause: e }); }
    try {
      facts.native = await rd.balance(ctx.account);
      facts.balance = native ? facts.native : word0(await rd.call(asked.fromToken, balanceOfData(ctx.account)));
      if (facts.balance < amount) add('insufficient_balance', { have: facts.balance.toString(), need: amount.toString() });
      facts.gasCost = await gasCost(build, ctx.path || 'sequence', rd);
      if (facts.gasCost !== null) {
        const needNative = facts.gasCost + (native ? amount : 0n) + feeOnTop;
        if (facts.native < needNative && !(native && facts.balance < amount)) add('insufficient_gas', { have: facts.native.toString(), need: needNative.toString() });
      }
      const approve = (build.sequence || []).slice(0, -1).find((s) => s.kind !== 'approve-reset' && decodeApprove(s.data));
      if (approve) {
        facts.allowance = word0(await rd.call(asked.fromToken, allowanceData(ctx.account, decodeApprove(approve.data).spender)));
        facts.resetFirst = facts.allowance > 0n && (APPROVE_RESET_FIRST[Number(build.chainId)] || []).includes(lc(asked.fromToken)) &&
          !(build.sequence || []).some((s) => s.kind === 'approve-reset');   // the engine already put approve(0) in the sequence
      }
    } catch (e) {
      facts.readError = String(e && e.message || e);
      if (build.insufficientBalance === true) add('insufficient_balance', { source: 'engine' });
    }
  } else if (build.insufficientBalance === true) add('insufficient_balance', { source: 'engine' });
  const blocking = issues.filter((x) => x.code !== 'wrong_chain');
  return { ok: blocking.length === 0, issues, facts };
}
