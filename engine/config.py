"""Constants and environment for the rhdex engine. Everything measured is dated in research/README.md (15.11.2026).

Env (all optional locally):
  BRAND_NAME      working product name (default "Skiff"): User-Agent, Telegram texts, placeholder page, /api/config.
  RHDEX_SITE      public https address (default placeholder __SITE_URL__); setting it to https://… marks production (guard.py).
  ROUTE_API_URL   base URL of the route API (aggregator) the router client calls, e.g. https://<host>/v1/. NO default: empty = routing
                  is disabled (quotes, builds and the background board / reach refreshers answer «routing unavailable»; pools still load).
  ROUTE_API_KEY   route API key (server-side only; never in a response). Without it the public rate limits apply (~75 calls per 2 h per IP).
  ROUTE_API_KEY_HEADER  the HTTP header that carries ROUTE_API_KEY (the API's own header name); empty = the key is not sent.
  ROUTE_HIDDEN_NAMES  optional regex (case-insensitive) of names scrubbed from every public answer (venues.FORBIDDEN); the host of
                  ROUTE_API_URL is always added. Empty = only the host.
  LAUNCH_META_URL optional token-metadata endpoint with one {} for the token address (JSON with address + artwork/image); empty = off.
  PRIVY_APP_ID    Privy app id; enables Bearer sign-in (JWKS-verified ES256 access token).
  PRIVY_APP_SECRET  Privy server secret: wallet lookup for an access token (else only the identity token path works).
  TG_ENABLED      1 = Telegram on (OFF by default since 16.11; returns with the token).
  TG_BOT_TOKEN    Telegram bot token; empty = fake transport (messages land in the tg_outbox table only). Ignored unless TG_ENABLED=1.
  DEV=1           local dev sign-in via X-Dev-Wallet header, only from 127.0.0.1 (see auth.py). Refused in production (guard.py).
  BOARD=0         switch the /api/board refresher off (saves router budget while developing).
  STOCKSCAN=0     switch the factory scan off (new Robinhood stock tokens, stockscan.py).
  TRADES=0        switch the Swap-log scanner off.
  LIMITS_ENABLED  0 (limit orders not built; owner decision pending, CoW is the candidate).
  RHDEX_DB        SQLite path (default site/data/rhdex.db).
  TOKEN_PAGE      1 = serve /token (Vault), /api/vault*, and the «Vault» links in header, mobile menu, footer, dock and ⌘K. Default OFF:
                  /token and /api/vault* are a plain 404 and the engine strips every Vault link from the pages it serves.
  TOKEN_TICKER    the token's ticker without "$"; default EMPTY = no ticker anywhere (token.ticker.line renders only when set, as $TICKER).
  TOKEN_CA        the token contract on 4663; empty = before launch (no Buy, no CA, page noindex and out of the sitemap).
  VAULT_ADDRESS   the public vault wallet on 4663 (it holds USDG); empty = address not posted yet, no balance line.
  VAULT_FIRST_DROP  optional override, ISO UTC of the first drop (e.g. 2026-12-01T16:00:00Z). Empty: the first drop is one week after the
                  token's launch (its first mint on chain, else the moment the engine first saw TOKEN_CA; stored in meta, never shifts);
                  weekly after that. No TOKEN_CA and no override = dashes, no countdown.
"""
from __future__ import annotations

import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
SITE = os.path.abspath(os.path.join(HERE, ".."))
WEB = os.path.join(SITE, "web")
DATA = os.environ.get("RHDEX_DATA") or os.path.join(SITE, "data")      # prod: RHDEX_DATA=/data (the volume) — budget, pools, logs
RESEARCH = os.path.abspath(os.path.join(SITE, "..", "research"))

BRAND_NAME = (os.environ.get("BRAND_NAME") or "Skiff").strip()      # product name Skiff: the ONLY place it lives
SITE_URL = (os.environ.get("RHDEX_SITE") or "__SITE_URL__").strip()   # public https address; placeholder until the owner gives the domain
BRAND_SLUG = re.sub(r"[^a-z0-9]+", "-", BRAND_NAME.lower()).strip("-") or "app"
UA = f"{BRAND_SLUG}-engine/0.1"     # the official RPC refuses Python-urllib (Cloudflare 1010) — any other UA passes

CHAIN = 4663
RPC = os.environ.get("RH_RPC", "https://rpc.mainnet.chain.robinhood.com")          # simulate + logs + calls (multicall only)
RPC_FALLBACK = os.environ.get("RH_RPC_FALLBACK", "https://robinhood-rpc.publicnode.com")
OTHER_RPC = {
    1: "https://ethereum-rpc.publicnode.com", 8453: "https://base-rpc.publicnode.com", 42161: "https://arbitrum-one-rpc.publicnode.com",
    10: "https://optimism-rpc.publicnode.com", 56: "https://bsc-rpc.publicnode.com", 137: "https://polygon-bor-rpc.publicnode.com",
}
SOLANA = 1151111081099710
# Pay-from networks the product offers (research/chains-17-11/summary.md keep list). Robinhood Chain + 13 core + 9
# secondary. Every other network is refused by /api/quote and /api/build and is not measured or listed, whatever the provider connects.
PAY_CHAIN_IDS = frozenset({
    CHAIN,
    1, 8453, 56, 999, 42161, 137, 43114, 143, 10, 196, 9745, 130, 57073,      # core: Ethereum, Base, BNB, HyperEVM, Arbitrum, Polygon,
                                                                              # Avalanche, Monad, Optimism, XLayer, Plasma, Unichain, Ink
    25, 5000, 100, 1672, 80094, 30, 2818, 747474, 480,                         # secondary: Cronos, Mantle, Gnosis, Pharos, Berachain,
})                                                                            # Rootstock, Morph, Katana, World Chain

MC3 = "0xca11bde05977b3631167028862be2a173976ca11"
QUOTER_V2 = "0x33e885ed0ec9bf04ecfb19341582aadcb4c8a9e7"
UNI_V3_FACTORY = "0x1f7d7550b1b028f7571e69a784071f0205fd2efa"
POOL_MANAGER = "0x8366a39cc670b4001a1121b8f6a443a643e40951"
# Uniswap on 4663 — official deployments (github Uniswap/sdks: sdk-core addresses.ts ROBINHOOD_ADDRESSES, universal-router-sdk constants
# [4663] V2_1_1), code checked with eth_getCode 16.11: SwapRouter02 24,497 B, UniversalRouter 24,546 B, Permit2 9,152 B, V4 PositionManager 23,877 B
SWAP_ROUTER02 = "0xcaf681a66d020601342297493863e78c959e5cb2"
UNIVERSAL_ROUTER = "0x8876789976decbfcbbbe364623c63652db8c0904"
PERMIT2 = "0x000000000022d473030f116ddee9f6b43ac78ba3"
V4_POSITION_MANAGER = "0x58daec3116aae6d93017baaea7749052e8a04fa7"
SIMPLE7702 = "0xe6cae83bde06e4c305530e199d7217f42808555b"     # Simple7702Account delegate (deployed on 4663)
ROUTER_DIAMOND = "0xb477751b76cf82d00a686a1232f5fcd772414af3"  # approvalAddress of every 4663 route (research 2.7)

NATIVE = "0x0000000000000000000000000000000000000000"
USDG = "0x5fc5360d0400a0fd4f2af552add042d716f1d168"
WETH = "0x0bd7d308f8e1639fab988df18a8011f41eacad73"
CBBTC = "0xcec185eb182c47d1ba1efc84e6959e18cd620be4"
PROBE = "0x1111111111111111111111111111111111111111"           # neutral sender for quotes without fromAddress (never signs)

TOPIC_TRANSFER = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
TOPIC_V3_SWAP = "0xc42079f94a6350d7e6235f29174924f928cc2ac818eb64fed8004e115fbcca67"
TOPIC_V4_SWAP = "0x40e9cecb9f5f1f1c5b9c97dec2917b7ee92e57ba5563708daca94dd84ad7112f"
TOPIC_V2_SWAP = "0xd78ad95fa46c994b6551d0da85fc275fe613ce37657fb8d5e3d130840159d822"

ROUTE_API_URL = (os.environ.get("ROUTE_API_URL") or "").strip()     # no default on purpose: empty = routing disabled (router.NotConfigured)
if ROUTE_API_URL and not ROUTE_API_URL.endswith("/"):
    ROUTE_API_URL += "/"
ROUTE_API_HOST = re.sub(r"^[a-z]+://", "", ROUTE_API_URL.lower()).split("/")[0].split(":")[0]
ROUTE_API_KEY = (os.environ.get("ROUTE_API_KEY") or "").strip()
ROUTE_API_KEY_HEADER = (os.environ.get("ROUTE_API_KEY_HEADER") or "").strip()
ROUTE_HIDDEN_NAMES = (os.environ.get("ROUTE_HIDDEN_NAMES") or "").strip()
LAUNCH_META_URL = (os.environ.get("LAUNCH_META_URL") or "").strip()
ROUTE_KEYED = bool(ROUTE_API_KEY and ROUTE_API_KEY_HEADER)          # the key is actually sent: keyed rate limits apply


def routing_enabled() -> bool:
    return bool(ROUTE_API_URL)
SLIPPAGE = 0.005

QUOTE_TTL_S = 12          # same pair + same amount within this window = cached answer (and coalesced while in flight)
BOARD_PERIOD_S = 240      # with a key: full board every 4 min; without: one row per BOARD_ROW_NOKEY_S
BOARD_ROW_NOKEY_S = 300
BOARD_STOCKS = ["NVDA", "TSLA", "SPY", "QQQ", "AAPL", "SPCX"]
BOARD_SIZES_USD = [50_000, 100_000]
TRADE_MIN_USD = 1_000
BIG_BUY_USD = 5_000                  # «big buys»: one threshold for the landing counter, Live and the /app, /stock stage (premium-4)
TRADE_POLL_S = 5
TRADE_KEEP_S = 86_400
POOL_MIN_LIQ_USD = 20_000

HERO_MIN_USD = float(os.environ.get("HERO_MIN_USD") or 50)   # board hero: postable, gain >= this, size >= $50k (no small profits)
VS_POOL_MAX_GAP_PCT = float(os.environ.get("VS_POOL_MAX_GAP_PCT") or 3)   # base below best route by >= this % -> pools_too_thin

LIMITS_ENABLED = (os.environ.get("LIMITS_ENABLED") or "0").strip() == "1"   # stub: nothing behind it yet
# Big-trade alerts: OFF since 16.11. Code kept; ALERTS_ENABLED=1 brings back /api/me/alerts* and
# the trade listener. Telegram (tg.enabled) needs alerts on as well.
ALERTS_ENABLED = (os.environ.get("ALERTS_ENABLED") or "0").strip() == "1"


def flag(name: str, default: str = "1") -> bool:
    return (os.environ.get(name) or default).strip() not in ("0", "", "false", "off")
ETH_PSEUDO = "0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"   # eth_simulateV1 traceTransfers: native moves appear from here

# tokens that revert approve(x) while the current allowance is non-zero: the build adds approve(0) first (audit v4 M20)
APPROVE_RESET_FIRST = {(1, "0xdac17f958d2ee523a2206206994597c13d831ec7")}   # USDT on Ethereum


# ---- the token page /token (Vault), TOKEN-UTILITY.md. One place; empty = the pre-launch state.
_HEX40 = re.compile(r"^0x[0-9a-fA-F]{40}$")


def _addr_env(name: str) -> str:
    v = (os.environ.get(name) or "").strip()
    return v.lower() if _HEX40.match(v) else ""


TOKEN_PAGE = (os.environ.get("TOKEN_PAGE") or "0").strip() == "1"
# /portfolio + /api/me/portfolio + its links (data-plink) in the header, menu, dock, footer and ⌘K. Default OFF until the owner's.
PORTFOLIO = (os.environ.get("PORTFOLIO") or "0").strip() == "1"
TOKEN_TICKER = re.sub(r"[^A-Za-z0-9]", "", (os.environ.get("TOKEN_TICKER") or "").strip().lstrip("$"))[:16]   # "" = no ticker yet
TOKEN_CA = _addr_env("TOKEN_CA")
VAULT_ADDRESS = _addr_env("VAULT_ADDRESS")
VAULT_FIRST_DROP = (os.environ.get("VAULT_FIRST_DROP") or "").strip()
DROP_SHARE = 0.5   # all token fees land in the vault; this share of the vault's USDG goes out in each weekly drop
