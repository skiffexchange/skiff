# Skiff

Swap stock tokens, and any other token, on Robinhood Chain.
Pay from any of 23 chains in one signature. 0% fee.

- Site: [skiff.exchange](https://skiff.exchange)
- X: [@skiff_exchange](https://x.com/skiff_exchange)
- $SKIFF contract on Robinhood Chain: `0x750bce9633a3f57faca59d4c3d103dc8ff37ad89`

> Security audit in progress, report coming this week.

## What Skiff does

- **Any token on Robinhood Chain.** Stock and fund tokens like NVDA, SPY and TSLA, plus ETH, USDG, cbBTC and other tokens with a pool. You can also paste any token address.
- **Pay from where your money is.** USDC, USDT or ETH from any of 23 chains. Moving across chains and the swap are one route, and you sign once.
- **0% fee.** Skiff adds no fee of its own. The route fee, the bridge fee and gas each show as a separate line, in % and in $.
- **Your own wallet.** No deposits. Approvals are for the exact amount.

## How routing works

1. **Race.** Every swap goes to 20+ price sources, and each one quotes your exact amount.
2. **Simulate.** Every route is simulated on the current block before you sign. Routes that would revert are dropped, so a higher number that won't go through never reaches the screen.
3. **Split.** Big orders can be split across several pools and DEXs when that delivers more than a single pool.
4. **Review.** Before you sign, the review screen shows the split by pool and every fee line.

## The Vault (`/token`)

- Trading fees from $SKIFF are swapped to USDG and kept in a public vault. The vault address is posted on the Vault page.
- Each week 50% of the vault buys stock tokens on Skiff.
- Those stock tokens go out to holders by balance. Balances are counted at a random hour of the week.

Hold $SKIFF, get stock tokens.

## Repo layout

```
engine/    FastAPI backend: quotes, route simulation, stock data, vault API
web/       built static site, served by the engine
web-src/   page, JS and CSS sources
```

## Run locally

Requires Python 3.12+.

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env
```

In `.env`, set `ROUTE_API_URL` to your route aggregator endpoint (with `ROUTE_API_KEY` and `ROUTE_API_KEY_HEADER` if you have a key). The Robinhood Chain RPC has a default; override it with `RH_RPC` if you like. Without `ROUTE_API_URL` the engine still starts and reports routing as unavailable. Then:

```sh
./run.sh
```

Open http://127.0.0.1:8850 (set `PORT` to change it). `run.sh` loads `.env`, keeps data in `data/` and turns on `DEV=1` for local sign-in.

## Security

See [SECURITY.md](SECURITY.md). The audit report will be published here as `AUDIT.md`.

## License

MIT
