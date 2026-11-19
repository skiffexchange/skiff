// Skiff's Privy bundle: one IIFE file the page loads ONLY on demand —
// the sign layer (wallets.js) fetches it when someone presses Connect wallet, or connected through Privy before on this browser. Publishes window.SkiffPrivy and a 'skiff-privy' event.
// Sign-in is the wallet itself: loginMethods ['wallet'], no email. An embedded wallet exists only
// if the owner turns it on (window.SKIFF_PRIVY.embedded = true and a login method that creates one) — then the 7702 path
// (useSign7702Authorization) is used for it. The page sets window.SKIFF_PRIVY = {appId, …} first, from /api/config.privyAppId.
// Networks: NOT a fixed list — at init the bundle reads /api/tokens `chains[]` (the engine's Pay-from networks: Robinhood Chain first, then
// every network a swap can be paid from; id, name, native, rpc, explorer) and gives them all to Privy. The seven networks viem already
// knows keep viem's definitions. /api/tokens unreachable -> the seven built-in ones (the bundle never blocks sign-in on it).
import { useEffect } from 'react';
import { createRoot } from 'react-dom/client';
import { PrivyProvider, usePrivy, useWallets, useSign7702Authorization, useIdentityToken } from '@privy-io/react-auth';
import { defineChain } from 'viem';
import { robinhood, base, arbitrum, mainnet, optimism, bsc, polygon } from 'viem/chains';

const KNOWN = { 4663: robinhood, 8453: base, 42161: arbitrum, 1: mainnet, 10: optimism, 56: bsc, 137: polygon };
const FALLBACK = [robinhood, base, arbitrum, mainnet, optimism, bsc, polygon];
const httpsOnly = (u) => typeof u === 'string' && /^https:\/\/[a-z0-9.-]+(\/[^\s"'<>]*)?$/i.test(u);

/** Pay-from networks from the engine (/api/tokens chains[]) as viem chains; Robinhood Chain always first. */
function chainsFrom(list) {
  const out = [];
  for (const c of list || []) {
    const id = Number(c && c.id);
    if (!Number.isSafeInteger(id) || id <= 0 || out.some((x) => x.id === id)) continue;
    if (KNOWN[id]) { out.push(KNOWN[id]); continue; }
    const rpc = (c.rpc || []).filter(httpsOnly);
    const n = c.native || {};
    if (!rpc.length || !n.symbol || Number(n.decimals) !== 18) continue;
    out.push(defineChain({ id, name: String(c.name || 'Chain ' + id).slice(0, 48),
      nativeCurrency: { name: String(n.name || n.symbol).slice(0, 48), symbol: String(n.symbol).slice(0, 16), decimals: 18 },
      rpcUrls: { default: { http: rpc } },
      blockExplorers: httpsOnly(c.explorer) ? { default: { name: 'Explorer', url: c.explorer } } : undefined }));
  }
  if (!out.some((x) => x.id === 4663)) out.unshift(robinhood);
  else out.sort((a, b) => (a.id === 4663 ? -1 : b.id === 4663 ? 1 : 0));
  return out;
}

async function loadChains() {
  if (Array.isArray(CFG.chains) && CFG.chains.length) return chainsFrom(CFG.chains);
  try {
    const r = await fetch('/api/tokens', { credentials: 'same-origin', headers: { accept: 'application/json' } });
    if (!r.ok) return FALLBACK;
    const d = await r.json();
    const cs = chainsFrom(d && d.chains);
    return cs.length > 1 ? cs : FALLBACK;
  } catch (e) {
    return FALLBACK;
  }
}

const CFG = window.SKIFF_PRIVY || {};
const eq = (a, b) => !!a && !!b && String(a).toLowerCase() === String(b).toLowerCase();

function Bridge() {
  const { ready, authenticated, user, login, logout, getAccessToken, connectWallet } = usePrivy();
  const { wallets } = useWallets();
  const { signAuthorization } = useSign7702Authorization();
  const { identityToken } = useIdentityToken();   // the engine finds the wallet from it when no app secret is set (privy-id-token header)
  const addr = user?.wallet?.address || null;   // the wallet the person signed in with
  const wallet = wallets.find((w) => eq(w.address, addr)) || wallets[0] || null;
  const chainId = wallet && wallet.chainId ? Number(String(wallet.chainId).split(':').pop()) : null;
  useEffect(() => {
    window.SkiffPrivy = {
      ready, authenticated, login, logout, connectWallet,
      address: authenticated ? (addr || wallet?.address || null) : null,
      walletClientType: wallet?.walletClientType || null,
      isEmbedded: wallet?.walletClientType === 'privy',
      walletName: wallet?.meta?.name || null,
      chainId,
      provider: () => (wallet ? wallet.getEthereumProvider() : Promise.reject(new Error('no wallet'))),
      switchChain: (id) => (wallet ? wallet.switchChain(Number(id)) : Promise.reject(new Error('no wallet'))),
      // EIP-7702 authorization (embedded wallets only — Privy refuses external ones): {contractAddress, chainId, nonce?, executor?}
      signAuthorization: (input, opts) => signAuthorization(input, opts),
      token: () => getAccessToken(),
      idToken: () => identityToken || null,
    };
    window.dispatchEvent(new CustomEvent('skiff-privy', { detail: window.SkiffPrivy }));
  }, [ready, authenticated, addr, wallet, chainId, getAccessToken, signAuthorization, identityToken]);
  return null;
}

// No blur anywhere (an opaque scrim; build.mjs strips every CSS blur()). Skiff colours: canvas #001413, panel #012624, mint #cbfffc, lilac #fad1ff.
const brand = document.createElement('style');
brand.textContent = `#privy-dialog,#privy-modal-content{--privy-border-radius-sm:8px !important;--privy-border-radius-md:12px !important;--privy-border-radius-mdlg:14px !important;--privy-border-radius-lg:20px !important;--privy-border-radius-full:9999px !important;--privy-color-background:#021c1a !important;--privy-color-background-2:#012624 !important;--privy-color-background-3:#0b3330 !important;--privy-color-foreground:#f2fffe !important;--privy-color-foreground-2:#edfffe !important;--privy-color-foreground-3:#b5c4c2 !important;--privy-color-foreground-4:#7c8d8b !important;--privy-color-foreground-accent:#012624 !important;--privy-color-accent:#cbfffc !important;--privy-color-accent-light:#edfffe !important;--privy-color-accent-lightest:#062a27 !important;--privy-color-accent-dark:#02b9b0 !important;--privy-color-accent-darkest:#01534f !important;--privy-color-success:#cbfffc !important;--privy-color-error:#ff9aa8 !important;--privy-color-error-light:#2c1a1f !important}
#privy-dialog,#privy-modal-content{font-family:"DM Sans",ui-sans-serif,system-ui,sans-serif}
#privy-dialog-backdrop{backdrop-filter:none !important;-webkit-backdrop-filter:none !important;background:rgba(0,10,9,.78) !important}
w3m-modal,appkit-modal{backdrop-filter:none !important;-webkit-backdrop-filter:none !important}`;
document.head.appendChild(brand);

// the Skiff lock-up (mark tile + wordmark), byte-for-byte web-src/assets/brand/lockup-skiff.svg (16.11), as the fallback window logo
const LOGO = 'data:image/svg+xml,' + encodeURIComponent('<svg xmlns="http://www.w3.org/2000/svg" width="92" height="32" viewBox="0 0 92 32" fill="none"><g transform="scale(.5)"><defs><radialGradient id="kl" cx="32" cy="-5.12" r="60.8" gradientUnits="userSpaceOnUse"><stop stop-color="#03504B"/><stop offset=".55" stop-color="#012A28"/><stop offset="1" stop-color="#001413"/></radialGradient><linearGradient id="kr" x1="0" y1="0" x2="0" y2="64" gradientUnits="userSpaceOnUse"><stop stop-color="#CBFFFC" stop-opacity=".42"/><stop offset=".5" stop-color="#CBFFFC" stop-opacity=".08"/></linearGradient></defs><rect width="64" height="64" rx="18" fill="url(#kl)"/><rect x=".5" y=".5" width="63" height="63" rx="17.5" fill="none" stroke="url(#kr)"/><path d="M44.74 13Q20.46 25.67 20.46 51L27.32 51Q27.32 25.67 44.74 13ZM44.74 13Q29.43 25.67 29.43 51L35.77 51Q35.77 25.67 44.74 13ZM44.74 13Q37.88 25.67 37.88 51L44.74 51Q44.74 25.67 44.74 13Z" fill="#CBFFFC"/></g><path d="M47.23 23.89Q45.54 23.89 44.43 23.37Q43.32 22.86 42.74 21.94Q42.17 21.02 42.05 19.8L44.11 19.8Q44.2 20.45 44.55 20.98Q44.89 21.52 45.56 21.86Q46.23 22.19 47.26 22.19Q48.07 22.19 48.64 21.94Q49.22 21.69 49.52 21.24Q49.81 20.78 49.81 20.18Q49.81 19.4 49.47 18.95Q49.12 18.51 48.47 18.28Q47.81 18.06 46.83 17.94Q45.78 17.8 44.97 17.53Q44.15 17.27 43.59 16.84Q43.03 16.41 42.74 15.76Q42.46 15.12 42.46 14.24Q42.46 13.21 43.01 12.42Q43.56 11.63 44.57 11.19Q45.59 10.75 46.97 10.75Q49 10.75 50.19 11.68Q51.37 12.61 51.58 14.33L49.6 14.33Q49.48 13.45 48.79 12.95Q48.09 12.44 46.95 12.44Q45.73 12.44 45.11 12.9Q44.49 13.35 44.49 14.09Q44.49 14.64 44.76 15.08Q45.04 15.53 45.71 15.82Q46.38 16.12 47.5 16.27Q48.91 16.43 49.89 16.8Q50.87 17.17 51.38 17.94Q51.89 18.7 51.87 20.02Q51.87 21.26 51.28 22.12Q50.7 22.98 49.66 23.43Q48.62 23.89 47.23 23.89ZM60.99 23.6L55.29 16.82L60.66 11.03L63.12 11.03L56.98 17.53L57.01 16.1L63.55 23.6ZM53.61 23.6L53.61 6.4L55.62 6.4L55.62 23.6ZM64.63 23.6L64.63 11.03L66.63 11.03L66.63 23.6ZM65.63 8.65Q65.03 8.65 64.64 8.25Q64.24 7.86 64.24 7.26Q64.24 6.66 64.64 6.29Q65.03 5.92 65.63 5.92Q66.2 5.92 66.61 6.29Q67.02 6.66 67.02 7.26Q67.02 7.86 66.61 8.25Q66.2 8.65 65.63 8.65ZM69.74 23.6L69.74 9.7Q69.74 8.55 70.12 7.82Q70.5 7.09 71.26 6.75Q72.01 6.4 73.11 6.4L74.95 6.4L74.95 8.12L73.44 8.12Q72.53 8.12 72.14 8.49Q71.75 8.86 71.75 9.77L71.75 23.6ZM67.78 12.73L67.78 11.03L75.09 11.03L75.09 12.73ZM77.12 23.6L77.12 9.7Q77.12 8.55 77.5 7.82Q77.88 7.09 78.64 6.75Q79.39 6.4 80.49 6.4L82.33 6.4L82.33 8.12L80.82 8.12Q79.91 8.12 79.52 8.49Q79.13 8.86 79.13 9.77L79.13 23.6ZM75.16 12.73L75.16 11.03L82.47 11.03L82.47 12.73Z" fill="#F2FFFE" stroke="#F2FFFE" stroke-width="0.41" stroke-linejoin="round"/></svg>');

const el = document.createElement('div');
el.id = 'privy-root';
document.body.appendChild(el);
loadChains().then((supportedChains) => createRoot(el).render(
  <PrivyProvider appId={CFG.appId} config={{
    loginMethods: CFG.loginMethods || ['wallet'],
    supportedChains,
    defaultChain: robinhood,
    appearance: { theme: '#021c1a', accentColor: '#cbfffc', logo: CFG.logo || LOGO, landingHeader: CFG.header, loginMessage: CFG.message,
      walletChainType: 'ethereum-only', showWalletLoginFirst: true,
      walletList: ['detected_ethereum_wallets', 'metamask', 'rabby_wallet', 'coinbase_wallet', 'okx_wallet', 'wallet_connect_qr'] },
    embeddedWallets: { ethereum: { createOnLogin: CFG.embedded ? 'users-without-wallets' : 'off' }, showWalletUIs: !!CFG.embedded },
  }}><Bridge /></PrivyProvider>
));
