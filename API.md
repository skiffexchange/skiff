# Skiff public API

Skiff's price engine is open. Quotes raced across 20+ sources, all 64 stock tokens on Robinhood Chain, 24 h price history and the $SKIFF vault, as plain JSON.

- **Base URL:** `https://skiff.exchange`
- **No key, no sign-up.** Every endpoint below is a read-only `GET`.
- **Chain IDs:** Robinhood Chain is `4663`. The native token of any chain is `0x0000000000000000000000000000000000000000`.
- **Amounts** go in raw integer units (1,000 USDG with 6 decimals = `1000000000`). Every amount in a response comes as `{ raw, decimals, usd }`.

Transaction building and account endpoints are for the Skiff app and aren't part of the public API.

## Endpoints

| Endpoint | What you get |
|---|---|
| [`GET /api/quote`](#get-apiquote) | every route for a swap, raced and checked, plus the best one |
| [`GET /api/stocks-overview`](#get-apistocks-overview) | all 64 stock tokens: price, 24 h change, sparkline, liquidity, volume |
| [`GET /api/price-history`](#get-apiprice-history) | 24 h price line for one stock |
| [`GET /api/venues`](#get-apivenues) | the venues Skiff can route through |
| [`GET /api/reach`](#get-apireach) | chains, bridges, DEXs and sources Skiff is wired into |
| [`GET /api/vault`](#get-apivault) | the $SKIFF vault |

---

## GET /api/quote

Races a swap across 20+ sources and returns every route. Same-chain routes are simulated on the current block, so a route that would revert shows up as `reverted` instead of as a price. Cross-chain routes are quoted.

A quote takes about 1 to 5 seconds.

### Parameters

| Name | Required | Description |
|---|---|---|
| `fromChain` | yes | chain ID you pay from, e.g. `4663` (Robinhood Chain), `8453` (Base) |
| `fromToken` | yes | token address you pay with; `0x000…000` for the native token |
| `toChain` | yes | `4663` |
| `toToken` | yes | token address you get, e.g. NVDA `0xd0601ce157db5bdc3162bbac2a2c8af5320d9eec` |
| `amount` | yes | amount of `fromToken` in raw integer units |
| `fromAddress` | no | the sender; lets the quote account for that wallet |
| `toAddress` | no | the receiver, if different from the sender |

### Example 1: same chain, 1,000 USDG → NVDA

```sh
curl "https://skiff.exchange/api/quote?fromChain=4663&fromToken=0x5fc5360d0400a0fd4f2af552add042d716f1d168&toChain=4663&toToken=0xd0601ce157db5bdc3162bbac2a2c8af5320d9eec&amount=1000000000"
```

Excerpt (one route shown, trimmed):

```json
{
  "block": 118298032,
  "tookMs": 1490,
  "amountIn": { "raw": "1000000000", "decimals": 6, "usd": 999.91 },
  "routes": [
    {
      "id": "direct-de3b5ae122dfb1",
      "rank": 1,
      "venue": { "displayKey": "uniswap-v3", "name": "Uniswap V3", "kind": "dex" },
      "status": "ok",
      "simulated": true,
      "delivery": "onchain",
      "out": {
        "simulated": { "raw": "6356901964140826589", "decimals": 18, "usd": 998.95 },
        "quoted":    { "raw": "6356901964140826589", "decimals": 18, "usd": 998.95 },
        "min":       { "raw": "6325117454320122456", "decimals": 18, "usd": 993.96 }
      },
      "priceImpactPct": 0.0954,
      "fees": {
        "ours":  { "pct": 0, "usd": 0.0 },
        "route": { "pct": 0, "usd": 0.0, "items": [] },
        "bridge": [],
        "gas":   { "usd": 0.005, "limit": 176436, "estimated": false },
        "totalUsd": 0.0
      },
      "durationS": 0,
      "split": [
        { "dex": "uniswap-v3", "name": "Uniswap V3", "pool": "0xd4eb21209c4d6093f80b5b84f5c45cc093ea14a3",
          "pair": "NVDA/USDG", "shareBps": 10000, "sharePct": 100.0 }
      ],
      "splitByDex": [
        { "dex": "uniswap-v3", "name": "Uniswap V3", "shareBps": 10000, "sharePct": 100.0 }
      ]
    }
  ],
  "best": "direct-de3b5ae122dfb1",
  "bestBasis": "simulated",
  "baseline": {
    "kind": "single_pool",
    "name": "Uniswap V3",
    "pair": "NVDA/USDG",
    "liqUsd": 2033130,
    "out": { "raw": "6356901964140826589", "decimals": 18, "usd": 998.95 }
  },
  "vsSinglePoolUsd": null,
  "ourFeePct": 0
}
```

### Example 2: cross-chain, 1,000 USDC on Base → NVDA

```sh
curl "https://skiff.exchange/api/quote?fromChain=8453&fromToken=0x833589fcd6edb6e08f4c7c32d4f71b54bda02913&toChain=4663&toToken=0xd0601ce157db5bdc3162bbac2a2c8af5320d9eec&amount=1000000000"
```

Cross-chain routes are quoted, not simulated, so they come in `quotedOnly` with `"simulated": false` and `out.simulated: null`:

```json
{
  "block": null,
  "routes": [],
  "quotedOnly": [
    {
      "id": "12d5534aab8e20a987f8",
      "venue": { "displayKey": "intent-bridge", "name": "Solver bridge", "kind": "solver" },
      "status": "intent",
      "simulated": false,
      "delivery": "intent",
      "out": {
        "simulated": null,
        "quoted": { "raw": "6346779522661054862", "decimals": 18, "usd": 997.36 },
        "min":    { "raw": "6315038610343724497", "decimals": 18, "usd": 992.37 }
      },
      "durationS": 5,
      "fees": { "ours": { "pct": 0, "usd": 0.0 }, "route": { "pct": 0.25, "usd": 2.49 }, "totalUsd": 2.71 }
    }
  ],
  "ourFeePct": 0
}
```

### Fields

| Field | Meaning |
|---|---|
| `routes[]` | routes checked on the current block |
| `quotedOnly[]` | routes that are quoted but not simulated (cross-chain, solver fills) |
| `unavailable[]` | sources that didn't return a route for this swap, with a `code` |
| `status` | `ok` goes through; `reverted` failed in simulation and isn't offered |
| `simulated` | `true` if the route was run on `block` before quoting |
| `out.quoted` / `out.simulated` / `out.min` | what the source quoted, what the simulation delivered, the minimum after slippage |
| `fees.ours.pct` | Skiff's fee, always `0` |
| `fees.route`, `fees.bridge`, `fees.gas` | every other cost, line by line, in token units and $ |
| `priceImpactPct` | price impact of this route, in % |
| `split[]` / `splitByDex[]` | how the order is split, by pool and by DEX (`shareBps`, `sharePct`) |
| `durationS` | estimated time to complete, in seconds |
| `best` | `id` of the best route; `bestBasis` says whether it was picked on simulated or quoted output |
| `baseline` | the single deepest pool for this pair, simulated on the same block |
| `vsSinglePoolUsd` | how much more (in $) the best route delivers than `baseline`; `null` when the best route is that pool |

---

## GET /api/stocks-overview

All 64 stock and fund tokens on Robinhood Chain. Cached for 60 s.

```sh
curl "https://skiff.exchange/api/stocks-overview"
```

```json
{
  "stocks": [
    {
      "stock": "SPY",
      "price": 738.7538905768394,
      "change24hPct": 0.83,
      "spark": [[1795013248, 732.692424], [1795013700, 732.565676], "…"],
      "sparkEnough": true,
      "liqUsd": 7124850,
      "deepestLiqUsd": 2392649,
      "pools": 15,
      "venues": 7,
      "volume24hUsd": 15381511.32,
      "bigBuys24h": 20
    }
  ],
  "bigBuyMinUsd": 5000,
  "sparkBucketS": 1800,
  "from": 1795013248,
  "to": 1795099648,
  "at": 1795099648
}
```

- `spark` is the 24 h sparkline as `[unix ts, price]` points.
- `liqUsd` is liquidity across all pools for that stock; `deepestLiqUsd` is the deepest single pool.
- `volume24hUsd` and `bigBuys24h` are chain-wide; a big buy is `bigBuyMinUsd` or more.

---

## GET /api/price-history

The 24 h price line for one stock, from its deepest pool. Cached for 60 s.

```sh
curl "https://skiff.exchange/api/price-history?stock=NVDA"
```

```json
{
  "stock": "NVDA",
  "pool": { "id": "0xd4eb21209c4d6093f80b5b84f5c45cc093ea14a3", "dex": "uniswap-v3", "pair": "NVDA/USDG" },
  "step": true,
  "points": [[1795013295, 154.71337], [1795013400, 154.383779], "…"],
  "count": 287,
  "last": 157.193163,
  "lastSwapAt": 1795099500,
  "change24hPct": 1.6,
  "from": 1795013295,
  "to": 1795099695
}
```

`step: true` means the line is a step line: each point is the last swap in its bucket.

---

## GET /api/venues

Every venue Skiff can route through. Cached for 300 s.

```sh
curl "https://skiff.exchange/api/venues"
```

```json
{
  "tools": [ { "displayKey": "…", "name": "…", "logo": "…", "kind": "aggregator" }, "…" ],
  "dexes": [
    { "dex": "uniswap-v3", "name": "Uniswap V3", "logo": "/assets/logos/venues/uniswap.png", "kind": "dex" },
    { "dex": "uniswap-v4", "name": "Uniswap V4", "logo": "/assets/logos/venues/uniswap.png", "kind": "dex" },
    "…"
  ]
}
```

---

## GET /api/reach

What Skiff is wired into.

```sh
curl "https://skiff.exchange/api/reach"
```

```json
{
  "chains":      { "count": 23, "external": 22, "list": [ { "id": 4663, "name": "Robinhood Chain" }, { "id": 8453, "name": "Base" }, "…" ] },
  "bridges":     { "count": 6, "list": [ "…" ] },
  "dexes":       { "count": 8, "list": [ { "dex": "uniswap-v3", "name": "Uniswap V3", "volume24hUsd": 38672225.77 }, "…" ] },
  "makers":      { "count": 8, "list": [ "…" ] },
  "aggregators": { "count": 8, "list": [ "…" ] },
  "sources":     { "count": 24 },
  "ourFeePct": 0,
  "measuredAt": 1795046249,
  "stale": false
}
```

---

## GET /api/vault

The $SKIFF vault: trading fees from $SKIFF are swapped to USDG and kept in this public vault. Each week 50% of the vault buys stock tokens on Skiff, and they go out to holders by balance.

```sh
curl "https://skiff.exchange/api/vault"
```

```json
{
  "ticker": "$SKIFF",
  "ca": "0x750bce9633a3f57faca59d4c3d103dc8ff37ad89",
  "vault": "0x4afd2d2ad8b82ad6186c4f46929866277db2f1fb",
  "launched": true,
  "explorer": {
    "vault": "https://robinhoodchain.blockscout.com/address/0x4afd2d2ad8b82ad6186c4f46929866277db2f1fb",
    "ca": "https://robinhoodchain.blockscout.com/address/0x750bce9633a3f57faca59d4c3d103dc8ff37ad89"
  },
  "holdings": {
    "usdg": { "raw": "…", "decimals": 6, "amount": "…" },
    "usd": "…",
    "dropUsdg": { "raw": "…", "amount": "…" }
  },
  "log": [],
  "firstDrop": "<ISO 8601 time>",
  "nextDrop": "<ISO 8601 time>"
}
```

- `holdings.usdg` is the vault's USDG balance; `dropUsdg` is 50% of it, the amount for the next weekly buy.
- `log` lists past buys and drops.

---

## Rate limits

Per IP address:

| Endpoint | Limit |
|---|---|
| `/api/quote` | 40 requests per minute |
| all other public endpoints | 240 requests per minute |

Over the limit you get HTTP `429` with `retryInS` (seconds to wait).

Cache headers: `stocks-overview` and `price-history` are `max-age=60`, `venues` is `max-age=300`. Polling faster than that returns the same data.

## Errors

Errors are JSON: `{ "error": "<code>", "message": "<text>" }`.

| HTTP | `error` | When |
|---|---|---|
| 400 | `bad_request` | missing or invalid parameter |
| 429 | `busy` | rate limit hit; see `retryInS` |
| 502 | `upstream` | a price source or the route aggregator failed to answer |
| 503 | `routing_unavailable` | routing is down for a moment |
| 503 | `chain_unavailable` | the chain RPC is down for a moment |

## CORS

The API sends no CORS headers. Call it from your server, bot or backend, not from a browser page on another domain.

## Ideas

- **Bots:** quote any stock token on demand in Telegram or Discord, with the best route and its fee lines.
- **Dashboards:** 64 stocks with live prices, 24 h change, liquidity and chain-wide volume.
- **Wallets:** show stock token prices and a real quote before your users swap.
- **Research:** liquidity and price impact across pools on Robinhood Chain.

## Links

- [README](README.md)
- Site: [skiff.exchange](https://skiff.exchange), X: [@skiff_exchange](https://x.com/skiff_exchange)
- $SKIFF contract on Robinhood Chain: `0x750bce9633a3f57faca59d4c3d103dc8ff37ad89`
- 0% fee on every swap.
