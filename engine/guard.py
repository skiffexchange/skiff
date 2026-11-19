"""Production start guard. Imported by server.py first. Production = RHDEX_ENV=prod, RHDEX_SITE is https://…, or the process runs on a
platform: Railway (any of RAILWAY_ENVIRONMENT, RAILWAY_ENVIRONMENT_ID, RAILWAY_PROJECT_ID, RAILWAY_SERVICE_ID, RAILWAY_DEPLOYMENT_ID — Railway
injects them into every deployment, docs.railway.com/reference/variables) or Fly (FLY_APP_NAME; Fly is no longer used since 16.11).
In production the process refuses to start (exit 3) when DEV is set (any value: the X-Dev-Wallet sign-in must never exist on a server),
QUOTE_REPLAY is set (recorded quotes are dev-only), WEB_CONCURRENCY is set (several workers = several scanners), a required secret is
missing, or — on Railway — no volume is mounted at /data (the database would vanish with every deploy). Locally nothing is checked."""
from __future__ import annotations

import os
import sys


RAILWAY_MARKERS = ("RAILWAY_ENVIRONMENT", "RAILWAY_ENVIRONMENT_ID", "RAILWAY_PROJECT_ID", "RAILWAY_SERVICE_ID", "RAILWAY_DEPLOYMENT_ID")


def on_railway(env=None) -> bool:
    env = os.environ if env is None else env
    return any((env.get(k) or "").strip() for k in RAILWAY_MARKERS)


def is_prod(env=None) -> bool:
    env = os.environ if env is None else env
    return ((env.get("RHDEX_ENV") or "").strip().lower() in ("prod", "production") or (env.get("RHDEX_SITE") or "").startswith("https://")
            or bool((env.get("FLY_APP_NAME") or "").strip()) or on_railway(env))


def problems(env=None) -> list[str]:
    env = os.environ if env is None else env
    if not is_prod(env):
        return []
    out = []
    if "DEV" in env:
        out.append("DEV is set: the local X-Dev-Wallet sign-in must never exist on a server")
    if "QUOTE_REPLAY" in env:
        out.append("QUOTE_REPLAY is set: recorded dev quotes must never be served on a server")
    # audit v1 P0-3: what production cannot run without
    if not (env.get("PRIVY_APP_ID") or "").strip():
        out.append("PRIVY_APP_ID is not set: nobody can sign in")
    if not (env.get("PRIVY_APP_SECRET") or "").strip() and (env.get("RHDEX_PRIVY_IDTOKEN") or "") != "1":
        out.append("PRIVY_APP_SECRET is not set and RHDEX_PRIVY_IDTOKEN=1 is not declared (the page must then send privy-id-token)")
    if not (env.get("ROUTE_API_URL") or "").strip():
        out.append("ROUTE_API_URL is not set: no route API, no quotes")
    if not (env.get("ROUTE_API_KEY") or "").strip() or not (env.get("ROUTE_API_KEY_HEADER") or "").strip():
        out.append("ROUTE_API_KEY / ROUTE_API_KEY_HEADER are not set: without a router key the product serves ~15 quotes per 2 h")
    site = (env.get("RHDEX_SITE") or "").strip()
    if not site.startswith("https://") or "__" in site:
        out.append("RHDEX_SITE must be the public https:// address (robots, sitemap and links use it)")
    if not (env.get("RHDEX_DB") or "").startswith("/data/"):
        out.append("RHDEX_DB must point into the volume (/data/…)")
    if not (env.get("RHDEX_DATA") or "").startswith("/data"):
        out.append("RHDEX_DATA must point into the volume (/data)")
    if on_railway(env) and (env.get("RAILWAY_VOLUME_MOUNT_PATH") or "").rstrip("/") != "/data":
        out.append("no Railway volume mounted at /data (RAILWAY_VOLUME_MOUNT_PATH="
                   f"{env.get('RAILWAY_VOLUME_MOUNT_PATH') or 'unset'}): `railway volume add -m /data`")
    baked = os.path.join(os.path.dirname(os.path.abspath(__file__)), "site_url.txt")    # written by the Dockerfile from SITE_URL
    if os.path.isfile(baked):
        try:
            img = open(baked).read().strip()
        except OSError:
            img = ""
        if img and img.rstrip("/") != site.rstrip("/"):
            out.append(f"RHDEX_SITE ({site}) differs from the SITE_URL the pages were built with ({img}): og tags and bot links would disagree")
    if (env.get("RHDEX_TRUST_PROXY") or "") != "1":
        out.append("RHDEX_TRUST_PROXY=1 is required behind the platform proxy (per-IP limits)")
    if "WEB_CONCURRENCY" in env:
        out.append("WEB_CONCURRENCY is set: several workers would run several scanners")
    return out


def check() -> None:
    bad = problems()
    if bad:
        for b in bad:
            print("REFUSING TO START: " + b, file=sys.stderr, flush=True)
        raise SystemExit(3)


check()
