# Security

## Reporting a vulnerability

If you find a vulnerability, please tell us privately first:

- open a [GitHub security advisory](../../security/advisories/new) on this repo, or
- send a DM to [@skiff_exchange](https://x.com/skiff_exchange) on X.

Please include what you found, steps to reproduce, and what an attacker could do with it. We'll reply as soon as we can and keep you posted until it's fixed.

## Scope

- `engine/`: the backend (quotes, route simulation, stock data, vault API)
- `web/` and `web-src/`: the site and its scripts
- the signing flow: what the site asks your wallet to sign, approvals and amounts

## What Skiff never does

- Skiff never holds your funds or your keys. Every swap is signed from your own wallet.
- Skiff never asks for your seed phrase or private key.
- Skiff never DMs first. Anyone who messages you first claiming to be Skiff is not us.

The only $SKIFF contract is `0x750bce9633a3f57faca59d4c3d103dc8ff37ad89` on Robinhood Chain. The only site is skiff.exchange and the only account is @skiff_exchange.

## Audit

A security audit is in progress. The report will be published in this repo as `AUDIT.md`.
