// Chains the sign layer can switch a wallet to, with the EIP-3085 parameters to add them, and the only contracts a route may call.
//
// Robinhood Chain 4663 — official: https://docs.robinhood.com/chain/connecting (fetched 16.11.2026): network name «Robinhood Chain»,
// chain ID 4663, currency symbol ETH, explorer robinhoodchain.blockscout.com, public RPC https://rpc.mainnet.chain.robinhood.com
// («public, rate-limited, not for production»; Alchemy is the recommended production RPC — with our key it would leak into the bundle,
// so wallets get the public URL). viem 2.57.8 `chains/robinhood` carries the same id, RPC and explorer.
// Source chains — the same values as viem 2.57.8 chain definitions and each chain's own docs (Base docs «Connecting to Base»,
// docs.arbitrum.io «RPC endpoints», docs.optimism.io «Networks», docs.bnbchain.org «JSON-RPC endpoint», docs.polygon.technology).
// Ethereum (1) is never *added* — every wallet ships it, and MetaMask refuses wallet_addEthereumChain for it; only switched to.
// Polygon: polygon-rpc.com answers 401 without a key since 16.11 (measured) -> publicnode + drpc.
//
// Every other Pay-from network (48 in all on 16.11) comes from the engine at start: configureChains(GET /api/tokens chains[]). The engine's
// metadata (name, native coin, RPC, explorer) is validated; its route contract must equal the one PINNED here (PINNED_ROUTE, the route
// provider's own catalog, recorded 16.11.2026 and re-checked on chain: eth_chainId of each RPC = the id, eth_getCode of the contract is
// not empty). A network the engine names but this file does not pin, or pins differently, is NOT signable (chain_unverified).
import { lc } from './abi.js';

export const RH = 4663;
export const SOLANA = 1151111081099710;   // engine /api/tokens id for Solana — not an EVM chain, not signed by this layer

export const CHAINS = {
  4663: { id: 4663, name: 'Robinhood Chain', native: { name: 'Ether', symbol: 'ETH', decimals: 18 },
          rpc: ['https://rpc.mainnet.chain.robinhood.com'], explorer: 'https://robinhoodchain.blockscout.com', addable: true },
  8453: { id: 8453, name: 'Base', native: { name: 'Ether', symbol: 'ETH', decimals: 18 },
          rpc: ['https://mainnet.base.org'], explorer: 'https://basescan.org', addable: true, opStack: true },
  42161: { id: 42161, name: 'Arbitrum One', native: { name: 'Ether', symbol: 'ETH', decimals: 18 },
           rpc: ['https://arb1.arbitrum.io/rpc'], explorer: 'https://arbiscan.io', addable: true },
  1: { id: 1, name: 'Ethereum', native: { name: 'Ether', symbol: 'ETH', decimals: 18 },
       rpc: ['https://ethereum-rpc.publicnode.com'], explorer: 'https://etherscan.io', addable: false },
  10: { id: 10, name: 'OP Mainnet', native: { name: 'Ether', symbol: 'ETH', decimals: 18 },
        rpc: ['https://mainnet.optimism.io'], explorer: 'https://optimistic.etherscan.io', addable: true, opStack: true },
  56: { id: 56, name: 'BNB Smart Chain', native: { name: 'BNB', symbol: 'BNB', decimals: 18 },
        rpc: ['https://bsc-dataseed.bnbchain.org'], explorer: 'https://bscscan.com', addable: true },
  137: { id: 137, name: 'Polygon', native: { name: 'POL', symbol: 'POL', decimals: 18 },
         rpc: ['https://polygon-bor-rpc.publicnode.com', 'https://polygon.drpc.org'], explorer: 'https://polygonscan.com', addable: true },
};

const STATIC_IDS = Object.keys(CHAINS).map(Number);
const STATIC_META = JSON.parse(JSON.stringify(CHAINS));

export const hexChainId = (id) => '0x' + Number(id).toString(16);
export const chainOf = (id) => CHAINS[Number(id)] || null;
export const isEvmSource = (id) => !!CHAINS[Number(id)];

/** wallet_addEthereumChain parameters (EIP-3085), or null for a chain that is never added (Ethereum) or unknown. */
export function addChainParams(id) {
  const c = chainOf(id);
  if (!c || !c.addable) return null;
  return { chainId: hexChainId(c.id), chainName: c.name, nativeCurrency: { ...c.native }, rpcUrls: [...c.rpc], blockExplorerUrls: [c.explorer] };
}
export const txUrl = (chainId, hash) => { const c = chainOf(chainId); return c && hash ? `${c.explorer}/tx/${hash}` : null; };

// ---- contracts
/** Simple7702Account (eth-infinitism), deployed on 4663, byte-identical to Arbitrum One's. */
export const SIMPLE7702 = '0xe6cae83bde06e4c305530e199d7217f42808555b';
export const DESIGNATOR = '0xef0100' + SIMPLE7702.slice(2);   // eth_getCode of an EOA delegated to it
export const isDelegatedToUs = (code) => lc(code) === DESIGNATOR;

/**
 * The only contracts a route transaction may call (and be approved for), per chain. The route provider's diamond: on 4663
 * 0xb477…14af3 (engine config ROUTER_DIAMOND, research 2.7: approvalAddress of every 4663 route, incl. the intent tool), on the
 * source chains 0x1231…4eae (every recorded source-chain route).
 * A build naming anything else is refused before the first signature.
 */
export const ROUTE_TARGETS = {
  4663: ['0xb477751b76cf82d00a686a1232f5fcd772414af3'],
  1: ['0x1231deb6f5749ef6ce6943a275a1d3e7486f4eae'],
  8453: ['0x1231deb6f5749ef6ce6943a275a1d3e7486f4eae'],
  42161: ['0x1231deb6f5749ef6ce6943a275a1d3e7486f4eae'],
  10: ['0x1231deb6f5749ef6ce6943a275a1d3e7486f4eae'],
  56: ['0x1231deb6f5749ef6ce6943a275a1d3e7486f4eae'],
  137: ['0x1231deb6f5749ef6ce6943a275a1d3e7486f4eae'],
};

/**
 * Direct pool swaps, 4663 only (`kind: "direct"`) — developers.uniswap.org «Robinhood Chain Deployments» (16.11.2026):
 * Uniswap V3 SwapRouter02 and Uniswap V4 UniversalRouter (+ Permit2: ERC-20 approve → Permit2, then Permit2.approve → UniversalRouter).
 */
export const DIRECT = {
  swapRouter02: '0xcaf681a66d020601342297493863e78c959e5cb2',
  universalRouter: '0x8876789976decbfcbbbe364623c63652db8c0904',
  permit2: '0x000000000022d473030f116ddee9f6b43ac78ba3',
};
ROUTE_TARGETS[4663].push(DIRECT.swapRouter02, DIRECT.universalRouter);
const STATIC_TARGETS = JSON.parse(JSON.stringify(ROUTE_TARGETS));

/**
 * The route contract of every Pay-from network, pinned (16.11.2026): the provider's catalog entry per network, and on chain the contract
 * answers eth_getCode with code (5176 bytes on most, a 254-byte proxy on the newer networks, 10144 on zkSync, 13664 on Abstract).
 * The engine's chains[].routeContract must say the same, or the network is not signed.
 */
export const PINNED_ROUTE = {
  4663: '0xb477751b76cf82d00a686a1232f5fcd772414af3',   // Robinhood Chain
  8453: '0x1231deb6f5749ef6ce6943a275a1d3e7486f4eae',   // Base
  42161: '0x1231deb6f5749ef6ce6943a275a1d3e7486f4eae',  // Arbitrum
  1: '0x1231deb6f5749ef6ce6943a275a1d3e7486f4eae',      // Ethereum
  10: '0x1231deb6f5749ef6ce6943a275a1d3e7486f4eae',     // OP Mainnet
  56: '0x1231deb6f5749ef6ce6943a275a1d3e7486f4eae',     // BNB Chain
  137: '0x1231deb6f5749ef6ce6943a275a1d3e7486f4eae',    // Polygon
  999: '0x0a0758d937d1059c356d4714e57f5df0239bce1a',    // HyperEVM
  143: '0x026f252016a7c47cdef1f05a3fc9e20c92a49c37',    // Monad
  5000: '0x1231deb6f5749ef6ce6943a275a1d3e7486f4eae',   // Mantle
  57073: '0x864b314d4c5a0399368609581d3e8933a63b9232',  // Ink
  100: '0x1231deb6f5749ef6ce6943a275a1d3e7486f4eae',    // Gnosis
  43114: '0x1231deb6f5749ef6ce6943a275a1d3e7486f4eae',  // Avalanche
  130: '0x864b314d4c5a0399368609581d3e8933a63b9232',    // Unichain
  146: '0x1231deb6f5749ef6ce6943a275a1d3e7486f4eae',    // Sonic
  80094: '0xf909c4ae16622898b885b89d7f839e0244851c66',  // Berachain
  9745: '0x026f252016a7c47cdef1f05a3fc9e20c92a49c37',   // Plasma
  59144: '0xde1e598b81620773454588b85d6b5d4eec32573e',  // Linea
  25: '0x1231deb6f5749ef6ce6943a275a1d3e7486f4eae',     // Cronos
  747474: '0xc59fe32c9549e3e8b5dccdabc45bd287bd5ba2bc', // Katana
  42220: '0x1231deb6f5749ef6ce6943a275a1d3e7486f4eae',  // Celo
  324: '0x341e94069f53234fe6dabef707ad424830525715',    // zkSync
  196: '0x1231deb6f5749ef6ce6943a275a1d3e7486f4eae',    // XLayer
  98866: '0x6f5c8bb0c5fe4eceac40ee1c238eab6bbb29761c',  // Plume
  534352: '0x1231deb6f5749ef6ce6943a275a1d3e7486f4eae', // Scroll
  252: '0x1231deb6f5749ef6ce6943a275a1d3e7486f4eae',    // Fraxtal
  1329: '0x1231deb6f5749ef6ce6943a275a1d3e7486f4eae',   // Sei
  34443: '0x1231deb6f5749ef6ce6943a275a1d3e7486f4eae',  // Mode
  1088: '0x24ca98fb6972f5ee05f0db00595c7f68d9fafd68',   // Metis
  2020: '0x452cf1b8597e6319cd21abd847312bf17e26d8d1',   // Ronin
  1868: '0x864b314d4c5a0399368609581d3e8933a63b9232',   // Soneium
  99901: '0x24e6da91aa4a8fee2447f542c92e006243bd6dc5',  // Elysium
  4326: '0x026f252016a7c47cdef1f05a3fc9e20c92a49c37',   // MegaETH
  42793: '0x977474593c982cfa8b197cae302e6d01f789435b',  // Etherlink
  30: '0x1231deb6f5749ef6ce6943a275a1d3e7486f4eae',     // Rootstock
  480: '0x1231deb6f5749ef6ce6943a275a1d3e7486f4eae',    // World Chain
  40: '0xa7cd44521903c65915648385e3fe881e49171582',     // Telos
  1672: '0xff70f4a1d11995621854f3692acf286d8acd04b2',   // Pharos
  2741: '0x4f8c9056bb8a3616693a76922fa35d53c056e5b3',   // Abstract
  16661: '0x213a83c67e1fc0334ef684571abcb820708a6536',  // 0G
  13371: '0x1231deb6f5749ef6ce6943a275a1d3e7486f4eae',  // Immutable zkEVM
  5031: '0x0bc413cae89c5b3ba3aa87fb98dca23425e1828f',   // Somnia
  2818: '0xf7ab42d00d2399f8a4fba2f15466be40709fe307',   // Morph
  747: '0x026f252016a7c47cdef1f05a3fc9e20c92a49c37',    // Flow
  8217: '0x1255d17c1bc2f764d087536410879f2d0d8772fd',   // Kaia
  33139: '0x2dea447e7dc6cd2f10b31bf10dcb30f87e838417',  // Apechain
  1776: '0xecec3970ca674278da8d9b1c484acaf6b20181f5',   // Injective
  42170: '0xff70f4a1d11995621854f3692acf286d8acd04b2',  // Arbitrum Nova
};

/**
 * Networks that bill an L1 data fee outside gas × price, and the oracle with getL1Fee(bytes) — measured 16.11.2026 by eth_call on each
 * network's public RPC (a non-zero answer for 600 bytes of calldata): OP-stack GasPriceOracle 0x42…0F; Scroll L1GasPriceOracle 0x53…02;
 * Morph 0x53…0F. Arbitrum-family networks put the L1 part into gasUsed (eth_estimateGas covers it); Linea, Metis, Celo answered nothing.
 */
const OP_ORACLE = '0x420000000000000000000000000000000000000f';
export const L1_FEE_ORACLE = {
  8453: OP_ORACLE, 10: OP_ORACLE, 5000: OP_ORACLE, 57073: OP_ORACLE, 130: OP_ORACLE, 747474: OP_ORACLE, 252: OP_ORACLE, 34443: OP_ORACLE,
  2020: OP_ORACLE, 1868: OP_ORACLE, 4326: OP_ORACLE, 480: OP_ORACLE,
  534352: '0x5300000000000000000000000000000000000002',
  2818: '0x530000000000000000000000000000000000000f',
};

const https = (u) => typeof u === 'string' && u.length < 200 && /^https:\/\/[a-z0-9.-]+(:[0-9]{2,5})?(\/[^\s"'<>@]*)?$/i.test(u);
const unverified = {};   // id -> why (a network the engine listed that cannot be signed)

/** Check one engine chains[] entry. Returns {ok, why, meta}. */
export function vetChainMeta(c) {
  const id = Number(c && c.id);
  if (!Number.isSafeInteger(id) || id <= 0) return { ok: false, why: 'id' };
  const pin = PINNED_ROUTE[id];
  if (!pin) return { ok: false, why: 'route contract not pinned for this network' };
  const rc = c.routeContract == null ? null : lc(c.routeContract);
  const known = STATIC_IDS.includes(id);
  if (rc !== pin && !(known && rc === null)) return { ok: false, why: rc ? 'route contract differs from the pinned one' : 'no route contract' };
  if (known) return { ok: true, meta: STATIC_META[id] };   // the seven known networks keep their checked parameters (engine silent = pinned)
  const n = c.native || {};
  if (Number(n.decimals) !== 18) return { ok: false, why: 'native decimals must be 18' };
  if (typeof n.symbol !== 'string' || !/^[A-Za-z0-9.]{1,8}$/.test(n.symbol)) return { ok: false, why: 'native symbol' };
  const name = typeof c.name === 'string' ? c.name.trim() : '';
  if (!name || name.length > 40 || /[<>"'`\\]/.test(name)) return { ok: false, why: 'name' };
  const rpc = (Array.isArray(c.rpc) ? c.rpc : []).filter(https).slice(0, 4);
  if (!rpc.length) return { ok: false, why: 'no https rpc' };
  if (!https(c.explorer)) return { ok: false, why: 'explorer' };
  const nm = typeof n.name === 'string' && n.name.trim() && n.name.length <= 40 && !/[<>"'`\\]/.test(n.name) ? n.name.trim() : n.symbol;
  return { ok: true, meta: { id, name, native: { name: nm, symbol: n.symbol, decimals: 18 }, rpc, explorer: c.explorer.replace(/\/+$/, ''), addable: id !== 1 } };
}

/**
 * Register the engine's Pay-from networks (GET /api/tokens → chains[]). Returns {signable: [ids], refused: [{id, why}]}.
 * Fails closed: a network that does not pass vetChainMeta has no route target, so vetBuild refuses it (chain_unverified).
 */
export function configureChains(list) {
  const signable = [], refused = [];
  for (const c of Array.isArray(list) ? list : []) {
    const id = Number(c && c.id);
    const v = vetChainMeta(c);
    if (!v.ok) {
      refused.push({ id, why: v.why });
      if (Number.isSafeInteger(id)) { unverified[id] = v.why; delete ROUTE_TARGETS[id]; if (!STATIC_IDS.includes(id)) delete CHAINS[id]; }
      continue;
    }
    delete unverified[id];
    CHAINS[id] = { ...v.meta };
    ROUTE_TARGETS[id] = STATIC_TARGETS[id] ? [...STATIC_TARGETS[id]] : [PINNED_ROUTE[id]];
    signable.push(id);
  }
  return { signable, refused };
}

/** {ok, why}: can this layer sign a transaction on `id`? */
export function signable(id) {
  const n = Number(id);
  if (unverified[n]) return { ok: false, why: unverified[n] };
  if (!ROUTE_TARGETS[n] || !CHAINS[n]) return { ok: false, why: 'network not configured' };
  return { ok: true };
}

/** Back to the seven built-in networks (tests). */
export function resetChains() {
  for (const k of Object.keys(CHAINS)) delete CHAINS[k];
  for (const k of Object.keys(ROUTE_TARGETS)) delete ROUTE_TARGETS[k];
  for (const k of Object.keys(unverified)) delete unverified[k];
  Object.assign(CHAINS, JSON.parse(JSON.stringify(STATIC_META)));
  Object.assign(ROUTE_TARGETS, JSON.parse(JSON.stringify(STATIC_TARGETS)));
}

export const WETH_4663 = '0x0bd7d308f8e1639fab988df18a8011f41eacad73';

/** Tokens that revert approve(x) while the current allowance is non-zero (must approve(0) first): USDT on Ethereum. */
export const APPROVE_RESET_FIRST = { 1: ['0xdac17f958d2ee523a2206206994597c13d831ec7'] };

/** OP-stack GasPriceOracle predeploy: getL1Fee(bytes) — the data fee that gas × maxFeePerGas does not cover. */
export const OP_GAS_ORACLE = OP_ORACLE;
