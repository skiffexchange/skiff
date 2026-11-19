// Stable error codes of the sign layer. The UI keys its strings by `code` (wording belongs to the marketer); `reason` is a plain
// developer default, safe to show if a string is missing. `fix` tells the UI what the person can do.
import { lc, selector } from './abi.js';

export const CODES = {
  no_wallet:            { reason: 'No wallet found in this browser.', fix: 'install' },
  not_connected:        { reason: 'Connect a wallet first.', fix: 'connect' },
  account_mismatch:     { reason: 'This route was built for another wallet address.', fix: 'requote' },
  wrong_chain:          { reason: 'Your wallet is on another network.', fix: 'switch' },
  chain_add_rejected:   { reason: 'The network was not added to your wallet.', fix: 'switch' },
  chain_unsupported:    { reason: 'Your wallet cannot use this network.', fix: 'other_wallet' },
  chain_unverified:     { reason: 'This network could not be verified, so nothing was signed. Pay from another network.', fix: 'other_chain' },
  insufficient_balance: { reason: 'Not enough balance for this amount.', fix: 'lower_amount' },
  insufficient_gas:     { reason: 'Not enough for the network fee.', fix: 'add_gas' },
  quote_expired:        { reason: 'The price changed while you were deciding. Check the new one.', fix: 'review' },
  below_min:            { reason: 'The route now pays less than your minimum.', fix: 'requote' },
  user_rejected:        { reason: 'You cancelled in your wallet.', fix: 'retry' },
  batch_unsupported:    { reason: 'Your wallet signs these one by one.', fix: null },
  batch_declined:       { reason: 'One-signature mode was declined, signing step by step.', fix: null },
  reverted:             { reason: 'The transaction did not go through. Nothing was swapped.', fix: 'requote' },
  simulation_failed:    { reason: 'This route fails in a dry run right now.', fix: 'other_route' },
  unsafe_build:         { reason: 'The transaction does not match the trade on screen, so it was not signed.', fix: 'requote' },
  authorization_mismatch: { reason: 'The wallet signed a different delegation than requested, so nothing was sent.', fix: 'retry' },
  nonce_conflict:       { reason: 'Another transaction from this wallet got in first.', fix: 'retry' },
  pending_long:         { reason: 'Sent, still waiting for the network. It may still complete.', fix: 'wait' },
  bridge_failed:        { reason: 'The cross-chain transfer failed.', fix: 'support' },
  bridge_refunded:      { reason: 'The cross-chain transfer was refunded on the source network.', fix: null },
  bridge_partial:       { reason: 'Arrived on Robinhood Chain as a different token than requested.', fix: null },
  solana_unsupported:   { reason: 'Paying from Solana is not supported in this wallet flow yet.', fix: 'other_chain' },
  engine_busy:          { reason: 'Prices are busy right now. Try again in a moment.', fix: 'wait' },
  rate_limited:         { reason: 'Too many requests. Try again in a moment.', fix: 'wait' },
  venue_unavailable:    { reason: 'This venue has no route for this trade right now.', fix: 'other_route' },
  engine_error:         { reason: 'Could not reach the price service.', fix: 'retry' },
  rpc_error:            { reason: 'The network did not answer. Try again.', fix: 'retry' },
  unknown:              { reason: 'Something went wrong. Nothing was signed after the error.', fix: 'retry' },
};

export class SignError extends Error {
  constructor(code, extra = {}) {
    const c = CODES[code] ? code : 'unknown';
    super(extra.message || CODES[c].reason);
    this.name = 'SignError';
    this.code = c;
    this.reason = CODES[c].reason;
    this.fix = CODES[c].fix;
    Object.assign(this, extra);
  }
}
export const fail = (code, extra) => new SignError(code, extra);

// Simple7702Account BaseAccount: error ExecuteError(uint256 index, bytes error) — which call of the batch failed.
export const EXECUTE_ERROR = selector('ExecuteError(uint256,bytes)');

/** {index, inner} from revert data of executeBatch, or null. */
export function decodeExecuteError(data) {
  const d = lc(data);
  if (!d.startsWith(EXECUTE_ERROR)) return null;
  const b = d.slice(10);
  try {
    const index = Number(BigInt('0x' + b.slice(0, 64)));
    const off = Number(BigInt('0x' + b.slice(64, 128))) * 2, len = Number(BigInt('0x' + b.slice(off, off + 64))) * 2;
    return { index, inner: '0x' + b.slice(off + 64, off + 64 + len) };
  } catch { return null; }
}

function texts(e) {
  const parts = [];
  for (let x = e, i = 0; x && i < 4; x = x.cause || x.error || x.data?.originalError, i++) {
    parts.push(x.message, x.shortMessage, x.details, typeof x.data === 'string' ? x.data : x.data?.message);
  }
  return lc(parts.filter(Boolean).join(' | '));
}
function codesOf(e) {
  const out = [];
  for (let x = e, i = 0; x && i < 4; x = x.cause || x.error || x.data?.originalError, i++) if (x.code !== undefined) out.push(x.code);
  return out;
}

/**
 * Map anything a wallet, the RPC or the engine throws to a SignError. `ctx`: {stage: 'switch'|'batch'|'send'|'capabilities'|'engine'}.
 * EIP-1193 codes: 4001 rejected, 4100 unauthorized, 4200 unsupported method, 4900/4901 disconnected, 4902 unknown chain.
 * EIP-5792: 5700 unsupported capability, 5710 unsupported chain, 5740 bundle too large, 5750 upgrade rejected, 5760 atomicity unsupported.
 */
export function mapError(e, ctx = {}) {
  if (e instanceof SignError) return e;
  const codes = codesOf(e), m = texts(e);
  const has = (c) => codes.includes(c);
  const extra = { cause: e };
  if (has(4001) || has('ACTION_REJECTED') || /user (rejected|denied|cancel)|rejected the request|request rejected|cancelled by user|user closed/.test(m))
    return fail('user_rejected', extra);
  if (has(5750)) return fail('batch_declined', extra);
  if (has(5700) || has(5710) || has(5740) || has(5760) || (ctx.stage === 'batch' && (has(4200) || has(-32601) || /not supported|unsupported|method not found|does not exist/.test(m))))
    return fail('batch_unsupported', extra);
  if (ctx.stage === 'switch') {
    if (has(4902) || /unrecognized chain|unknown chain|chain .* not (been )?added|try adding the chain/.test(m)) return fail('chain_unsupported', { ...extra, needsAdd: true });
    return fail('wrong_chain', extra);
  }
  if (has(4100) || has(4900) || has(4901)) return fail('not_connected', extra);
  if (/insufficient funds|gas required exceeds|intrinsic gas too low|exceeds balance|not enough (eth|funds)/.test(m)) return fail('insufficient_gas', extra);
  if (/nonce too low|replacement transaction underpriced|already known|nonce has already been used/.test(m)) return fail('nonce_conflict', extra);
  if (/return amount is not enough|too little received|insufficient output|minout|min_out|slippage|amount out/.test(m)) return fail('below_min', extra);
  if (/execution reverted|reverted|executeerror|revert/.test(m)) {
    const dataHex = [e?.data, e?.data?.data, e?.error?.data, e?.cause?.data].find((x) => typeof x === 'string' && x.startsWith('0x'));
    return fail('reverted', { ...extra, executeError: dataHex ? decodeExecuteError(dataHex) : null });
  }
  if (/failed to fetch|network ?error|timeout|timed out|econn|503|502/.test(m)) return fail('rpc_error', extra);
  return fail('unknown', extra);
}

/** Engine HTTP refusal → SignError. */
export function engineError(status, body = {}) {
  const err = body.error || body.code || '';
  const extra = { httpStatus: status, engine: body };
  if (status === 429 || err === 'rate_limited') return fail('rate_limited', { ...extra, retryInS: body.retryInS });
  if (err === 'busy') return fail('engine_busy', { ...extra, retryInS: body.retryInS });
  if (err === 'below_min') return fail('below_min', { ...extra, simulatedOut: body.simulatedOut, guaranteedMin: body.guaranteedMin, minOut: body.minOut });
  if (err === 'simulation_reverted') return fail('simulation_failed', { ...extra, simulation: body.simulation });
  if (err === 'venue_unavailable' || err === 'no_transaction') return fail('venue_unavailable', extra);
  if (err === 'chain_unavailable' || err === 'simulation_unavailable') return fail('rpc_error', extra);
  if (err === 'route_expired') return fail('quote_expired', { ...extra, routeExpired: true });   // the quote is no longer held: quote again
  if (err === 'chain_unverified') return fail('chain_unverified', { ...extra, chainId: body.chainId });
  if (err === 'unverified_contract') return fail('unsafe_build', { ...extra, why: 'engine: ' + (body.why || 'contract'), chainId: body.chainId });
  return fail('engine_error', extra);
}
