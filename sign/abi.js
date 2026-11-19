// Hex, integer and ABI helpers for the sign layer. Integer math only (BigInt): an amount is always {raw, decimals}, never a float.
import { keccak_256 } from './vendor/hashes/sha3.js';

export const lc = (v) => String(v == null ? '' : v).toLowerCase();
export const strip0x = (h) => (lc(h).startsWith('0x') ? lc(h).slice(2) : lc(h));
export const isHex = (h) => /^0x([0-9a-f]{2})*$/.test(lc(h));
export const isAddress = (a) => /^0x[0-9a-f]{40}$/.test(lc(a));
export const ZERO = '0x0000000000000000000000000000000000000000';

export function hexToBytes(h) {
  const s = strip0x(h);
  if (s.length % 2 || /[^0-9a-f]/.test(s)) throw new Error('bad hex');
  const out = new Uint8Array(s.length / 2);
  for (let i = 0; i < out.length; i++) out[i] = parseInt(s.slice(2 * i, 2 * i + 2), 16);
  return out;
}
export const bytesToHex = (b) => '0x' + Array.from(b, (x) => x.toString(16).padStart(2, '0')).join('');
export const keccak = (bytesOrHex) => bytesToHex(keccak_256(typeof bytesOrHex === 'string' ? hexToBytes(bytesOrHex) : bytesOrHex));
export const utf8 = (s) => new TextEncoder().encode(s);
export const selector = (signature) => keccak(utf8(signature)).slice(0, 10);

/** BigInt from a decimal string, a hex string ("0x…"), a number (safe integers only) or a bigint. */
export function big(v) {
  if (typeof v === 'bigint') return v;
  if (typeof v === 'number') { if (!Number.isSafeInteger(v)) throw new Error('unsafe number: pass a string'); return BigInt(v); }
  const s = String(v == null ? '0' : v).trim();
  if (s === '' || s === '0x') return 0n;
  if (!/^(0x[0-9a-fA-F]+|[0-9]+)$/.test(s)) throw new Error('not an integer: ' + s);
  return BigInt(s);
}
export const toQuantity = (v) => '0x' + big(v).toString(16);

/** 32-byte word (64 hex chars, no 0x) of an address or a non-negative integer. */
export function word(v) {
  if (typeof v === 'string' && isAddress(v)) return strip0x(v).padStart(64, '0');
  const n = big(v);
  if (n < 0n || n >= 1n << 256n) throw new Error('out of uint256 range');
  return n.toString(16).padStart(64, '0');
}

// ---- ERC-20 calldata
export const SEL = {
  approve: '0x095ea7b3',          // approve(address,uint256)
  allowance: '0xdd62ed3e',        // allowance(address,address)
  balanceOf: '0x70a08231',        // balanceOf(address)
  executeBatch: '0x34fcd5be',     // Simple7702Account.executeBatch((address target,uint256 value,bytes data)[])
};
export const approveData = (spender, amount) => SEL.approve + word(spender) + word(amount);
export const allowanceData = (owner, spender) => SEL.allowance + word(owner) + word(spender);
export const balanceOfData = (owner) => SEL.balanceOf + word(owner);

/** {spender, amount} of an approve(address,uint256) calldata, or null when it is anything else. */
export function decodeApprove(data) {
  const d = lc(data);
  if (!d.startsWith(SEL.approve) || d.length !== 10 + 128) return null;
  const w0 = d.slice(10, 74), w1 = d.slice(74, 138);
  if (!w0.startsWith('0'.repeat(24))) return null;
  return { spender: '0x' + w0.slice(24), amount: BigInt('0x' + w1) };
}

/**
 * Simple7702Account.executeBatch(Call[]) with Call = (address target, uint256 value, bytes data). Byte-for-byte the encoder of
 * the research probe and the engine encoder (tests check both): head (0x20, n), n offsets relative to the array start, then per call
 * target, value, 0x60 (offset of bytes inside the tuple — written THIRD, after target and value — written second it round-trips cleanly and reverts on chain), length, data
 * right-padded to 32 bytes.
 */
export function encodeExecuteBatch(calls) {
  const n = calls.length;
  let head = word(0x20) + word(n);
  const offs = [], tails = [];
  let cur = 32 * n;
  for (const c of calls) {
    const d = strip0x(c.data || '0x');
    if (d.length % 2) throw new Error('odd calldata');
    const padded = d.padEnd(Math.ceil(d.length / 64) * 64, '0');
    const body = word(c.target ?? c.to) + word(c.value ?? 0) + word(0x60) + word(d.length / 2) + padded;
    offs.push(word(cur));
    tails.push(body);
    cur += body.length / 2;
  }
  return SEL.executeBatch + head + offs.join('') + tails.join('');
}

/** Inverse of encodeExecuteBatch (strict: re-encoding the result must give the same bytes, else null). */
export function decodeExecuteBatch(data) {
  const d = lc(data);
  if (!d.startsWith(SEL.executeBatch)) return null;
  const body = d.slice(10);
  const w = (byteOff) => BigInt('0x' + (body.slice(byteOff * 2, byteOff * 2 + 64) || '0'));
  try {
    const arr = Number(w(0));
    const n = Number(w(arr));
    if (n > 64) return null;
    const calls = [];
    for (let i = 0; i < n; i++) {
      const t = arr + 32 + Number(w(arr + 32 + 32 * i));
      const target = '0x' + body.slice(t * 2 + 24, t * 2 + 64);
      const value = w(t + 32);
      const bo = t + Number(w(t + 64));
      const len = Number(w(bo));
      const dataHex = '0x' + body.slice((bo + 32) * 2, (bo + 32 + len) * 2);
      if (dataHex.length !== 2 + len * 2) return null;
      calls.push({ target, value, data: dataHex });
    }
    return encodeExecuteBatch(calls) === d ? calls : null;
  } catch { return null; }
}

// ---- amounts (integer only)
/** "123.456" from raw + decimals, trimmed to `maxFrac` fractional digits (rounded down, never up). */
export function formatUnits(raw, decimals, maxFrac = decimals) {
  const neg = big(raw) < 0n, s = (neg ? -big(raw) : big(raw)).toString().padStart(decimals + 1, '0');
  const whole = s.slice(0, s.length - decimals), frac = s.slice(s.length - decimals).slice(0, maxFrac).replace(/0+$/, '');
  return (neg ? '-' : '') + (frac ? `${whole}.${frac}` : whole);
}
/** raw BigInt from a human string "1.5" and decimals; extra fractional digits are an error, not rounded. */
export function parseUnits(text, decimals) {
  const s = String(text).trim();
  if (!/^[0-9]*(\.[0-9]*)?$/.test(s) || s === '' || s === '.') throw new Error('not an amount');
  const [w, f = ''] = s.split('.');
  if (f.length > decimals) throw new Error('too many decimals');
  return BigInt(w || '0') * 10n ** BigInt(decimals) + BigInt((f + '0'.repeat(decimals)).slice(0, decimals) || '0');
}

// ---- direct pool swaps on 4663 (kind "direct"): Uniswap V3 SwapRouter02, Uniswap V4 UniversalRouter + Permit2
// Addresses: developers.uniswap.org «Robinhood Chain Deployments» (fetched 16.11.2026) — SwapRouter02 0xcaf6…5cb2, Permit2 0x0000…8BA3,
// UniversalRouter 0x8876…0904 ; factory and QuoterV2 there equal the engine's config.
SEL.permit2Approve = '0x87517c45';     // Permit2.approve(address token, address spender, uint160 amount, uint48 expiration)
SEL.urExecute = '0x3593564c';          // UniversalRouter.execute(bytes commands, bytes[] inputs, uint256 deadline)
SEL.exactInputSingle02 = '0x04e45aaf'; // SwapRouter02.exactInputSingle((tokenIn, tokenOut, fee, recipient, amountIn, amountOutMinimum, sqrtPriceLimitX96))
SEL.multicall02 = '0x5ae401dc';        // SwapRouter02.multicall(uint256 deadline, bytes[] data)

// ABI reading over a body (hex without 0x and selector); offsets in bytes
const rdWord = (b, at) => { const s = b.slice(at * 2, at * 2 + 64); if (s.length !== 64) throw new Error('short'); return BigInt('0x' + s); };
const rdAddr = (b, at) => { const s = b.slice(at * 2, at * 2 + 64); if (s.length !== 64 || !s.startsWith('0'.repeat(24))) throw new Error('addr'); return '0x' + s.slice(24); };
const rdBytes = (b, at) => { const n = Number(rdWord(b, at)); const s = b.slice((at + 32) * 2, (at + 32 + n) * 2); if (s.length !== n * 2) throw new Error('short'); return s; };
const rdBytesArr = (b, at) => { const n = Number(rdWord(b, at)); if (n > 32) throw new Error('long'); const out = []; for (let i = 0; i < n; i++) out.push(rdBytes(b, at + 32 + Number(rdWord(b, at + 32 + 32 * i)))); return out; };

/** {token, spender, amount, expiration} of Permit2.approve calldata, or null. */
export function decodePermit2Approve(data) {
  const d = lc(data);
  if (!d.startsWith(SEL.permit2Approve) || d.length !== 10 + 4 * 64) return null;
  try { const b = d.slice(10); const amount = rdWord(b, 64), expiration = rdWord(b, 96);
    if (amount >= 1n << 160n || expiration >= 1n << 48n) return null;
    return { token: rdAddr(b, 0), spender: rdAddr(b, 32), amount, expiration }; } catch { return null; }
}
export const permit2ApproveData = (token, spender, amount, expiration) => SEL.permit2Approve + word(token) + word(spender) + word(amount) + word(expiration);

/**
 * UniversalRouter execute(commands = 0x10 V4_SWAP, [ (actions 0x06 SWAP_EXACT_IN_SINGLE · 0x0c SETTLE_ALL · 0x0f TAKE_ALL, params) ], deadline)
 * — the only V4 shape signed. ExactInputSingleParams
 * (PoolKey{currency0, currency1, fee, tickSpacing, hooks}, zeroForOne, amountIn, amountOutMinimum, hopPrice, hookData);
 * SETTLE_ALL(currency, maxAmount); TAKE_ALL(currency, minAmount) — TAKE_ALL pays msg.sender.
 */
export function decodeUrV4Single(data) {
  const d = lc(data);
  if (!d.startsWith(SEL.urExecute)) return null;
  try {
    const b = d.slice(10);
    const commands = rdBytes(b, Number(rdWord(b, 0))), inputs = rdBytesArr(b, Number(rdWord(b, 32))), deadline = rdWord(b, 64);
    if (commands !== '10' || inputs.length !== 1) return null;
    const p = inputs[0];
    const actions = rdBytes(p, Number(rdWord(p, 0))), params = rdBytesArr(p, Number(rdWord(p, 32)));
    if (actions !== '060c0f' || params.length !== 3) return null;
    const s = params[0], t = Number(rdWord(s, 0));   // the struct is behind one offset (it has a dynamic member)
    const key = { currency0: rdAddr(s, t), currency1: rdAddr(s, t + 32), fee: rdWord(s, t + 64), tickSpacing: rdWord(s, t + 96), hooks: rdAddr(s, t + 128) };
    const swap = { zeroForOne: rdWord(s, t + 160) === 1n, amountIn: rdWord(s, t + 192), minOut: rdWord(s, t + 224), hookData: rdBytes(s, t + Number(rdWord(s, t + 288))) };
    const settle = { currency: rdAddr(params[1], 0), amount: rdWord(params[1], 32) }, take = { currency: rdAddr(params[2], 0), min: rdWord(params[2], 32) };
    return { deadline, key, ...swap, settle, take };
  } catch { return null; }
}
const encBytes = (h) => { const s = strip0x(h); return word(s.length / 2) + s.padEnd(Math.ceil(s.length / 64) * 64, '0'); };
const encBytesArr = (arr) => { let off = arr.length * 32, head = ''; const enc = arr.map(encBytes); for (const e of enc) { head += word(off); off += e.length / 2; } return word(arr.length) + head + enc.join(''); };
/** The same encoding, built independently — used by tests and the live check. */
export function encodeUrV4Single(key, zeroForOne, amountIn, minOut, deadline) {
  const kw = word(key.currency0) + word(key.currency1) + word(key.fee) + word(key.tickSpacing) + word(key.hooks);
  const swap = word(32) + kw + word(zeroForOne ? 1 : 0) + word(amountIn) + word(minOut) + word(0) + word(320) + word(0);
  const [cin, cout] = zeroForOne ? [key.currency0, key.currency1] : [key.currency1, key.currency0];
  const plan = word(64) + word(64 + encBytes('060c0f').length / 2) + encBytes('060c0f') + encBytesArr([swap, word(cin) + word(amountIn), word(cout) + word(minOut)]);
  return SEL.urExecute + word(96) + word(96 + encBytes('10').length / 2) + word(deadline) + encBytes('10') + encBytesArr([plan]);
}

/** SwapRouter02 exactInputSingle (optionally inside multicall(deadline, [one call])) → params, or null for any other shape. */
export function decodeExactInputSingle02(data) {
  let d = lc(data), deadline = null;
  try {
    if (d.startsWith(SEL.multicall02)) {
      const b = d.slice(10); deadline = rdWord(b, 0);
      const inner = rdBytesArr(b, Number(rdWord(b, 32)));
      if (inner.length !== 1) return null;
      d = '0x' + inner[0];
    }
    if (!d.startsWith(SEL.exactInputSingle02) || d.length !== 10 + 7 * 64) return null;
    const b = d.slice(10);
    return { tokenIn: rdAddr(b, 0), tokenOut: rdAddr(b, 32), fee: rdWord(b, 64), recipient: rdAddr(b, 96), amountIn: rdWord(b, 128),
             minOut: rdWord(b, 160), sqrtPriceLimitX96: rdWord(b, 192), deadline };
  } catch { return null; }
}
