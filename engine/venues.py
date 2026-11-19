"""Display map: every route tool key seen on 4663 -> public displayKey, label, local logo, kind; every pool dex -> displayKey + logo.

Public responses show venues by their own product names. The route API's own tools (its solver keys ending in "IntentsDex" /
"Intents", and smartDeposits) get neutral keys ("intent", "intent-bridge", "deposit-swap") and a neutral local icon; feeCollection is a fee,
never a venue. Labels marked `labelTbd` are working labels. Names that must never appear in a public answer come from the env var
ROUTE_HIDDEN_NAMES (a regex) plus the host of ROUTE_API_URL (config.py); see `FORBIDDEN` below."""
from __future__ import annotations

import re

import config as C

LOGO = "/assets/logos/venues/"
NEUTRAL_LOGO = LOGO + "intent.svg"      # the router's own tools only
OTHER_LOGO = LOGO + "other.svg"         # anything without its own mark yet

# key (as the router sends it) -> (displayKey, label, logo, kind, labelTbd)
TOOLS: dict[str, tuple[str, str, str, str, bool]] = {
    "kyberswap": ("kyberswap", "KyberSwap", LOGO + "kyberswap.svg", "aggregator", False),
    "bitget": ("bitget", "Bitget", LOGO + "bitget.svg", "aggregator", False),
    "fly": ("fly", "Fly", LOGO + "fly.svg", "aggregator", False),
    "nordstern": ("nordstern", "Nordstern Finance", LOGO + "nordstern.svg", "aggregator", False),
    "sushiswap": ("sushiswap", "SushiSwap", LOGO + "sushiswap.svg", "aggregator", False),
    "paraswap": ("velora", "Velora", LOGO + "velora.svg", "aggregator", False),
    "openocean": ("openocean", "OpenOcean", LOGO + "openocean.svg", "aggregator", False),
    "rialto": ("rialto", "Rialto", LOGO + "rialto.svg", "aggregator", False),
    # v1 (copy P2-14, swap-ux P1-4): the UI's own words; no echo of the provider's product names, kinds without "router"
    "smartDeposits": ("deposit-swap", "Swap on Robinhood Chain", LOGO + "swap.svg", "swap", True),
    "across": ("across", "Across", LOGO + "across.svg", "bridge", False),
    "paxos": ("paxos", "Paxos", LOGO + "paxos.svg", "bridge", False),
    "symbiosis": ("symbiosis", "Symbiosis", LOGO + "symbiosis.svg", "bridge", False),
    "layerswap": ("layerswap", "Layerswap", LOGO + "layerswap.svg", "bridge", False),
    "relaydepository": ("relay", "Relay", LOGO + "relay.svg", "bridge", False),
    "relay": ("relay", "Relay", LOGO + "relay.svg", "bridge", False),
    "glacis": ("glacis", "Glacis", LOGO + "glacis.svg", "bridge", False),
    "stargateV2": ("stargate", "Stargate", OTHER_LOGO, "bridge", False),
    "stargateV2Bus": ("stargate", "Stargate", OTHER_LOGO, "bridge", False),
    "mayan": ("mayan", "Mayan", OTHER_LOGO, "bridge", False),
    "mayanMCTP": ("mayan", "Mayan", OTHER_LOGO, "bridge", False),
    "cbridge": ("celer", "Celer", OTHER_LOGO, "bridge", False),
    "allbridge": ("allbridge", "Allbridge", OTHER_LOGO, "bridge", False),
    "eco": ("eco", "Eco", OTHER_LOGO, "bridge", False),
    "gasZipBridge": ("gaszip", "Gas.zip", OTHER_LOGO, "bridge", False),
    "uniswap": ("uniswap", "Uniswap", LOGO + "uniswap.png", "dex", False),
    "1inch": ("1inch", "1inch", OTHER_LOGO, "aggregator", False),
    "0x": ("0x", "0x", OTHER_LOGO, "aggregator", False),
    "okx": ("okx", "OKX", OTHER_LOGO, "aggregator", False),
    "jupiter": ("jupiter", "Jupiter", OTHER_LOGO, "aggregator", False),
    # audit v4 works M6: tools seen on the new Pay-from networks, by their own product names
    "kuru": ("kuru", "Kuru", OTHER_LOGO, "dex", False),
    "monorail": ("monorail", "Monorail", OTHER_LOGO, "aggregator", False),
    "eisen": ("eisen", "Eisen", OTHER_LOGO, "aggregator", False),
    "m0Dex": ("m0", "M0", OTHER_LOGO, "dex", False),
    "supersetDex": ("superset", "Superset", OTHER_LOGO, "dex", False),
    "gluex": ("gluex", "GlueX", OTHER_LOGO, "aggregator", False),
    "enso": ("enso", "Enso", OTHER_LOGO, "aggregator", False),
    "merkle": ("merkle", "Merkle", OTHER_LOGO, "aggregator", False),
    "odos": ("odos", "Odos", OTHER_LOGO, "aggregator", False),
    "dodo": ("dodo", "DODO", OTHER_LOGO, "aggregator", False),
    "lifuel": ("gas-refuel", "Gas refuel", NEUTRAL_LOGO, "swap", True),
}


def human(key: str) -> str:
    """an unknown tool key as words: camelCase split, a trailing Dex / Bridge / V2 dropped («fooBarDex» -> «Foo Bar»); empty -> DEX aggregator"""
    w = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", key or "").replace("_", " ").replace("-", " ").split()
    while w and w[-1].lower() in ("dex", "bridge", "v2", "v3", "aggregator"):
        w.pop()
    return " ".join(x[:1].upper() + x[1:] for x in w) or "DEX aggregator"
HIDDEN = {"feeCollection"}          # a fee step, never a venue

# pool dexes (DexScreener dexId + version) -> (displayKey, label, logo)
DEXES: dict[str, tuple[str, str, str]] = {
    "uniswap-v3": ("uniswap-v3", "Uniswap V3", LOGO + "uniswap.png"),
    "uniswap-v4": ("uniswap-v4", "Uniswap V4", LOGO + "uniswap.png"),
    "uniswap-v2": ("uniswap-v2", "Uniswap V2", LOGO + "uniswap.png"),
    "aero": ("aero", "Aero", LOGO + "aero.png"),
    "ramses": ("ramses", "Ramses", LOGO + "ramses.png"),
    "metric": ("metric", "Metric", LOGO + "metric.png"),
    "alandale": ("alandale", "Alandale", LOGO + "alandale.png"),
    "nami": ("nami", "Nami", LOGO + "nami.png"),
    "up": ("up", "Up", LOGO + "up.png"),
    "giga": ("giga", "Giga", LOGO + "giga.png"),
    "kittenswap": ("kittenswap", "KittenSwap", LOGO + "kittenswap.png"),
    "sushiswap": ("sushiswap", "SushiSwap", LOGO + "sushiswap.svg"),   # audit v4 design 11: one spelling everywhere (pool dex = aggregator)
    "other": ("other", "Other liquidity", LOGO + "other.svg"),
}
FACTORIES = {
    "0x1f7d7550b1b028f7571e69a784071f0205fd2efa": "uniswap-v3", "0xc63d7338c178e805c544b8f897066bd3bbcbe136": "aero",
    "0xe0c4ceb92d08ca985bb70fe0a22feb121a9854a8": "ramses", "0x1ac9db4a2608ba45d6127b1737949b51bb54b7f3": "up",
    "0x16494a80e08bcb9285d87b67149d7b01774d82f8": "alandale", "0xdedb80903df19f9db9b393eb66e07511cb9abda2": "nami",
    "0xece6ecd61177336ea6fb9b17937ac439d85ee20b": "giga", "0x8bceaa40b9acdfaedf85adf4ff01f5ad6517937f": "uniswap-v2",
}

# route API solver tools, recognised by the shape of their key (the API names them <brand>IntentsDex / <brand>Intents)
_INTENT_DEX = re.compile(r"^[A-Za-z0-9]*IntentsDex$")
_INTENT_BRIDGE = re.compile(r"^[A-Za-z0-9]*Intents$")
INTENT_VIEW = {"dex": ("intent", "Solver fill", NEUTRAL_LOGO, "solver", True),
               "bridge": ("intent-bridge", "Solver bridge", NEUTRAL_LOGO, "solver", True)}
_SEEN_INTENT: dict[str, str] = {}      # displayKey -> the API's own key, learned from the routes it sends (server-side only)


def _intent_kind(key: str | None) -> str | None:
    k = key or ""
    return "dex" if _INTENT_DEX.match(k) else "bridge" if _INTENT_BRIDGE.match(k) else None


def is_intent(key: str | None) -> bool:
    """the route API's own solver tools (same-chain fill or bridge)"""
    return _intent_kind(key) is not None


def _forbidden() -> re.Pattern:
    parts = []
    if C.ROUTE_HIDDEN_NAMES:
        try:
            re.compile(C.ROUTE_HIDDEN_NAMES)
            parts.append(C.ROUTE_HIDDEN_NAMES)
        except re.error:
            pass
    host = C.ROUTE_API_HOST
    if host:
        parts.append(re.escape(host))
    return re.compile("|".join(f"(?:{p})" for p in parts) if parts else r"(?!x)x", re.I)


# names that never appear in a public answer (matches nothing when ROUTE_HIDDEN_NAMES and ROUTE_API_URL are empty)
FORBIDDEN = _forbidden()


def tool(key: str | None) -> dict:
    """Public view of a router tool key. Unknown keys pass through only if they do not name the router; else neutral."""
    k = key or ""
    ik = _intent_kind(k)
    if k in TOOLS:
        dk, label, logo, kind, tbd = TOOLS[k]
    elif ik:
        dk, label, logo, kind, tbd = INTENT_VIEW[ik]
        _SEEN_INTENT.setdefault(dk, k)
    elif FORBIDDEN.search(k):
        dk, label, logo, kind, tbd = "intent", "Solver fill", NEUTRAL_LOGO, "solver", True
    else:
        dk, label, logo, kind, tbd = re.sub(r"[^a-z0-9-]", "", k.lower()) or "other", human(k), OTHER_LOGO, "aggregator", True
    return {"displayKey": dk, "name": label, "logo": logo, "kind": kind, **({"labelTbd": True} if tbd else {})}


def tool_key_of(display_key: str) -> str | None:
    """displayKey -> the router's own key (server-side only)."""
    for k, v in TOOLS.items():
        if v[0] == display_key:
            return k
    if display_key in _SEEN_INTENT:
        return _SEEN_INTENT[display_key]
    return display_key if not FORBIDDEN.search(display_key or "") else None


def dex(dex_key: str) -> dict:
    dk, label, logo = DEXES.get(dex_key, (dex_key, dex_key.replace("-", " ").title(), OTHER_LOGO))
    return {"dex": dk, "name": label, "logo": logo}


def dex_key(dex_id: str, labels: list | None) -> str:
    d = (dex_id or "").lower()
    ls = [str(x).lower() for x in labels or []]
    if d == "uniswap":
        return "uniswap-v4" if "v4" in ls else "uniswap-v2" if "v2" in ls else "uniswap-v3"
    if d.startswith("ramses"):
        return "ramses"
    return d or "other"


def table() -> list[dict]:
    rows = [{"key": "(hidden)" if FORBIDDEN.search(k) else k, **tool(k)} for k in TOOLS]
    rows += [{"key": "(hidden)", **{"displayKey": v[0], "name": v[1], "logo": v[2], "kind": v[3], "labelTbd": True}} for v in INTENT_VIEW.values()]
    # the public table never shows the router's own keys — the UI only needs displayKey
    return [{kk: vv for kk, vv in r.items() if kk != "key"} for r in rows]


def sanitize(o):
    """Last guard on every public JSON: drop keys naming the router, neutralise strings that do; the API's own solver keys become their
    displayKey. Keep ROUTE_HIDDEN_NAMES free of hex letters only, so hex data stays untouched."""
    if isinstance(o, dict):
        return {k: sanitize(v) for k, v in o.items() if not FORBIDDEN.search(str(k))}
    if isinstance(o, list):
        return [sanitize(v) for v in o]
    if isinstance(o, str) and is_intent(o):
        return INTENT_VIEW[_intent_kind(o)][0]
    if isinstance(o, str) and FORBIDDEN.search(o):
        if o in TOOLS:
            return TOOLS[o][0]
        return FORBIDDEN.sub("route", o)
    return o
