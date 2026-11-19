// Thin client for the engine routes the sign layer needs: /api/config, /api/tokens, /api/quote, POST /api/build, /api/status.
// The engine signs nothing and holds no key; it returns transactions for the person's own wallet to sign.
import { configureChains } from './chains.js';
import { engineError, fail } from './errors.js';

export function createEngine(base = '', { fetchImpl } = {}) {
  const f = fetchImpl || ((...a) => globalThis.fetch(...a));
  async function call(path, init) {
    let res, body;
    try { res = await f(base + path, init); } catch (e) { throw fail('engine_error', { cause: e }); }
    try { body = await res.json(); } catch { body = {}; }
    if (!res.ok) throw engineError(res.status, body);
    return body;
  }
  const qs = (o) => Object.entries(o).filter(([, v]) => v !== undefined && v !== null && v !== '').map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(String(v))}`).join('&');
  return {
    config: () => call('/api/config'),
    /** The picker index; its chains[] are the Pay-from networks (id, name, native, rpc, explorer, routeContract). */
    tokens: () => call('/api/tokens'),
    /** params: fromChain, fromToken, toChain, toToken, amount (raw string), fromDecimals?, toDecimals?, fromAddress?, toAddress? */
    quote: (params) => call('/api/quote?' + qs(params)),
    /** body: {routeId, fromAddress, minOut (raw), mode} or the quote fields + venue (displayKey). */
    build: (body) => call('/api/build', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) }),
    status: ({ txHash, fromChain, toChain = 4663, venue }) => call('/api/status?' + qs({ txHash, fromChain, toChain, venue })),
  };
}

/**
 * At start: register the engine's Pay-from networks with the sign layer (chains.js configureChains: metadata validated, route contract
 * must equal the pinned one). Returns {signable: [ids], refused: [{id, why}]}. Engine unreachable -> the seven built-in networks only.
 * Pass `tokens` when the page already fetched /api/tokens (one request, not two).
 */
export async function loadChains(engine, tokens) {
  let d = tokens;
  if (!d) { try { d = await engine.tokens(); } catch { return { signable: [], refused: [], offline: true }; } }
  return configureChains(d && d.chains);
}
