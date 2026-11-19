// EIP-7702: authorization tuples and type-4 transactions, by hand (RLP, keccak, secp256k1 recovery). Used to CHECK what a wallet
// signed before anything is broadcast, and as the last-resort path for a Privy embedded wallet (secp256k1_sign over our own hash).
// Spec: https://eips.ethereum.org/EIPS/eip-7702 — MAGIC = 0x05, authority = ecrecover(keccak(MAGIC || rlp([chain_id, address, nonce])), y_parity, r, s);
// tx = 0x04 || rlp([chain_id, nonce, max_priority_fee_per_gas, max_fee_per_gas, gas_limit, destination, value, data, access_list,
// authorization_list, signature_y_parity, signature_r, signature_s]); authorization_list = [[chain_id, address, nonce, y_parity, r, s], …].
import { secp256k1 } from './vendor/curves/secp256k1.js';
import { keccak_256 } from './vendor/hashes/sha3.js';
import { big, bytesToHex, hexToBytes, isAddress, lc, strip0x } from './abi.js';

// ---------------------------------------------------------------- RLP
function intBytes(v) {
  let n = big(v);
  if (n < 0n) throw new Error('rlp: negative');
  const out = [];
  while (n > 0n) { out.unshift(Number(n & 0xffn)); n >>= 8n; }
  return Uint8Array.from(out);
}
const concat = (parts) => { const len = parts.reduce((a, p) => a + p.length, 0), o = new Uint8Array(len); let i = 0; for (const p of parts) { o.set(p, i); i += p.length; } return o; };
function lenPrefix(len, short) {
  if (len < 56) return Uint8Array.of(short + len);
  const lb = intBytes(len);
  return concat([Uint8Array.of(short + 55 + lb.length), lb]);
}
/** RLP of nested arrays of Uint8Array. */
export function rlpEncode(item) {
  if (Array.isArray(item)) { const body = concat(item.map(rlpEncode)); return concat([lenPrefix(body.length, 0xc0), body]); }
  if (!(item instanceof Uint8Array)) throw new Error('rlp: bytes or list');
  if (item.length === 1 && item[0] < 0x80) return item;
  return concat([lenPrefix(item.length, 0x80), item]);
}
/** RLP decode to nested arrays of Uint8Array; strict (whole input consumed, canonical lengths). */
export function rlpDecode(bytes) {
  const [v, end] = dec(bytes, 0);
  if (end !== bytes.length) throw new Error('rlp: trailing bytes');
  return v;
}
function dec(b, i) {
  const p = b[i];
  if (p === undefined) throw new Error('rlp: short');
  if (p < 0x80) return [b.subarray(i, i + 1), i + 1];
  const readLen = (n, at) => { let l = 0; for (let k = 0; k < n; k++) l = l * 256 + b[at + k]; if (n && b[at] === 0) throw new Error('rlp: non-canonical'); return l; };
  if (p < 0xb8) { const l = p - 0x80; if (i + 1 + l > b.length) throw new Error('rlp: short'); if (l === 1 && b[i + 1] < 0x80) throw new Error('rlp: non-canonical'); return [b.subarray(i + 1, i + 1 + l), i + 1 + l]; }
  if (p < 0xc0) { const n = p - 0xb7, l = readLen(n, i + 1); if (l < 56) throw new Error('rlp: non-canonical'); const s = i + 1 + n; if (s + l > b.length) throw new Error('rlp: short'); return [b.subarray(s, s + l), s + l]; }
  let s, l;
  if (p < 0xf8) { l = p - 0xc0; s = i + 1; } else { const n = p - 0xf7; l = readLen(n, i + 1); if (l < 56) throw new Error('rlp: non-canonical'); s = i + 1 + n; }
  if (s + l > b.length) throw new Error('rlp: short');
  const out = []; let j = s;
  while (j < s + l) { const [v, e] = dec(b, j); out.push(v); j = e; }
  if (j !== s + l) throw new Error('rlp: list length');
  return [out, s + l];
}
const asInt = (b) => (b.length ? BigInt(bytesToHex(b)) : 0n);
const asAddr = (b) => { if (b.length !== 20) throw new Error('rlp: address'); return bytesToHex(b); };
const addrBytes = (a) => { if (!isAddress(a)) throw new Error('bad address'); return hexToBytes(a); };
const dataBytes = (h) => hexToBytes(h || '0x');

// ---------------------------------------------------------------- authorization
export const MAGIC_AUTH = 0x05;
export const TX_TYPE_7702 = 0x04;

/** keccak256(0x05 || rlp([chainId, address, nonce])) — the digest a 7702 authorization signs. */
export function hashAuthorization({ chainId, address, contractAddress, nonce }) {
  const body = rlpEncode([intBytes(chainId), addrBytes(address ?? contractAddress), intBytes(nonce)]);
  return bytesToHex(keccak_256(concat([Uint8Array.of(MAGIC_AUTH), body])));
}

/** EOA address from a 65-byte uncompressed public key. */
const pubToAddress = (pub) => bytesToHex(keccak_256(pub.subarray(1)).subarray(12));

/** Address that produced (r, s, yParity) over the 32-byte `hash`. */
export function recoverAddress(hash, { r, s, yParity, v }) {
  let rec = yParity;
  if (rec === undefined || rec === null) { const vv = Number(big(v)); rec = vv >= 27 ? (vv - 27) % 2 : vv; }
  rec = Number(rec);
  if (rec !== 0 && rec !== 1) throw new Error('bad yParity');
  const sig = new secp256k1.Signature(big(r), big(s)).addRecoveryBit(rec);
  if (sig.hasHighS()) throw new Error('high s');   // EIP-2: s must be in the lower half (7702 requires it for authorizations too)
  return pubToAddress(sig.recoverPublicKey(strip0x(hash)).toRawBytes(false));
}
export const recoverAuthority = (auth) => recoverAddress(hashAuthorization(auth), auth);

/** Normalize a wallet's authorization answer (Privy returns {address, chainId, nonce, r, s, yParity, v}). */
export function normalizeAuthorization(a) {
  if (!a) throw new Error('no authorization');
  const address = lc(a.address ?? a.contractAddress);
  let yParity = a.yParity;
  if (yParity === undefined || yParity === null) { const vv = Number(big(a.v)); yParity = vv >= 27 ? (vv - 27) % 2 : vv; }
  return { chainId: Number(big(a.chainId)), address, nonce: Number(big(a.nonce)), yParity: Number(yParity),
           r: '0x' + big(a.r).toString(16).padStart(64, '0'), s: '0x' + big(a.s).toString(16).padStart(64, '0') };
}

/**
 * The nonce the authorization must carry. When the same account sends the type-4 transaction ("executor: self"), the transaction
 * consumes `txNonce` first and the authorization is checked after it, so it must carry txNonce + 1 — signed with txNonce the
 * transaction succeeds and delegates nowhere.
 */
export const authorizationNonce = (txNonce, selfExecuting = true) => Number(big(txNonce)) + (selfExecuting ? 1 : 0);

// ---------------------------------------------------------------- type-4 transaction
const authList = (list) => (list || []).map((a) => {
  const n = normalizeAuthorization(a);
  return [intBytes(n.chainId), addrBytes(n.address), intBytes(n.nonce), intBytes(n.yParity), intBytes(n.r), intBytes(n.s)];
});
const accessList = (list) => (list || []).map((e) => [addrBytes(e.address), (e.storageKeys || []).map((k) => hexToBytes(k))]);
function fields7702(tx) {
  if (!tx.authorizationList || !tx.authorizationList.length) throw new Error('type-4 needs an authorization');
  return [intBytes(tx.chainId), intBytes(tx.nonce), intBytes(tx.maxPriorityFeePerGas ?? 0), intBytes(tx.maxFeePerGas), intBytes(tx.gas ?? tx.gasLimit),
          addrBytes(tx.to), intBytes(tx.value ?? 0), dataBytes(tx.data), accessList(tx.accessList), authList(tx.authorizationList)];
}
/** keccak256(0x04 || rlp(fields without signature)) — what the sender's key signs. */
export const signingHash7702 = (tx) => bytesToHex(keccak_256(concat([Uint8Array.of(TX_TYPE_7702), rlpEncode(fields7702(tx))])));
/** 0x04 || rlp(fields ++ [yParity, r, s]). */
export function serialize7702(tx, sig) {
  const f = fields7702(tx);
  if (sig) f.push(intBytes(sig.yParity), intBytes(sig.r), intBytes(sig.s));
  return bytesToHex(concat([Uint8Array.of(TX_TYPE_7702), rlpEncode(f)]));
}

/**
 * Parse a signed raw transaction (type 2 or 4) into plain fields and recover its sender. Used to check what a wallet signed
 * before it is broadcast — a wallet that silently drops `authorizationList` returns a type-2 here.
 */
export function parseSignedTx(raw) {
  const b = hexToBytes(raw);
  const type = b[0];
  if (type !== 0x02 && type !== 0x04) throw new Error('unsupported tx type 0x' + (type ?? 0).toString(16));
  const f = rlpDecode(b.subarray(1));
  const n = type === 0x04 ? 13 : 12;
  if (!Array.isArray(f) || f.length !== n) throw new Error('bad tx field count');
  const tx = { type, chainId: asInt(f[0]), nonce: asInt(f[1]), maxPriorityFeePerGas: asInt(f[2]), maxFeePerGas: asInt(f[3]), gas: asInt(f[4]),
               to: f[5].length ? asAddr(f[5]) : null, value: asInt(f[6]), data: bytesToHex(f[7]),
               accessList: f[8].map((e) => ({ address: asAddr(e[0]), storageKeys: e[1].map(bytesToHex) })) };
  let sigAt = 9;
  if (type === 0x04) {
    tx.authorizationList = f[9].map((a) => ({ chainId: Number(asInt(a[0])), address: asAddr(a[1]), nonce: Number(asInt(a[2])), yParity: Number(asInt(a[3])),
                                              r: '0x' + asInt(a[4]).toString(16).padStart(64, '0'), s: '0x' + asInt(a[5]).toString(16).padStart(64, '0') }));
    sigAt = 10;
  }
  const sig = { yParity: Number(asInt(f[sigAt])), r: asInt(f[sigAt + 1]), s: asInt(f[sigAt + 2]) };
  const unsigned = rlpEncode(f.slice(0, sigAt));
  const hash = bytesToHex(keccak_256(concat([Uint8Array.of(type), unsigned])));
  tx.from = recoverAddress(hash, sig);
  tx.hash = bytesToHex(keccak_256(b));
  return tx;
}

/** Signature {r, s, yParity} from a 65-byte hex signature (r ‖ s ‖ v), as returned by secp256k1_sign. */
export function splitSignature(sigHex) {
  const s = strip0x(sigHex);
  if (s.length !== 130) throw new Error('signature must be 65 bytes');
  const v = parseInt(s.slice(128), 16);
  return { r: '0x' + s.slice(0, 64), s: '0x' + s.slice(64, 128), yParity: v >= 27 ? (v - 27) % 2 : v };
}
