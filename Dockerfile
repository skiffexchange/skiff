# Skiff (rhdex) — one container: the engine (FastAPI + uvicorn, one process with its threads: market, prices, trades scanner, board,
# Telegram off until TG_ENABLED=1) serves the API, SSE quotes and the built pages (web/). Build context = this folder (rhdex/site); .dockerignore is an
# allowlist: engine/, web/, requirements-lock.txt — never .env, .venv, data/, privy/node_modules, sign/, tools/, web-src/ (see .dockerignore).
# Deployed on Railway with a volume mounted at /data.
#
# Config and secrets (ROUTE_API_URL, ROUTE_API_KEY, ROUTE_API_KEY_HEADER, PRIVY_APP_ID, PRIVY_APP_SECRET, HEALTHZ_TOKEN) come from platform variables, never from a file in the image.
# The image is production by default (RHDEX_ENV=prod): engine/guard.py refuses to start (exit 3) with DEV or QUOTE_REPLAY set at all,
# with WEB_CONCURRENCY, without the required secrets, without an https RHDEX_SITE, with RHDEX_DB / RHDEX_DATA outside /data, or on Railway
# without a volume mounted at /data.
#
# SITE_URL is the ONE place of the public address: a Railway service variable (Railway hands service variables to the build as ARGs), it
# replaces the __SITE_URL__ placeholder in every page (canonical, og:url, og:image), is written to engine/site_url.txt, and becomes the
# default RHDEX_SITE (robots.txt, sitemap.xml, /api/config siteUrl). The build fails while it is not a real https:// address — a deploy can
# never ship placeholder og tags.
#
# Base pinned by digest: the multi-arch index of python:3.12.12-slim-bookworm (resolved 13.11.2026 with
# `docker buildx imagetools inspect python:3.12.12-slim-bookworm`). To move: inspect again, replace tag + digest, regenerate the lock.
FROM python:3.12.12-slim-bookworm@sha256:593bd06efe90efa80dc4eee3948be7c0fde4134606dd40d8dd8dbcade98e669c

ARG SITE_URL
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_NO_CACHE_DIR=1 \
    RHDEX_ENV=prod RHDEX_SITE=${SITE_URL} RHDEX_DB=/data/rhdex.db RHDEX_DATA=/data RHDEX_TRUST_PROXY=1 RHDEX_LOG=INFO PORT=8080 APP_UID=10001

WORKDIR /app
RUN useradd --uid 10001 --user-group --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin app
COPY requirements-lock.txt .
RUN pip install --require-hashes --no-deps -r requirements-lock.txt && rm requirements-lock.txt
COPY engine/ engine/
COPY web/ web/
RUN set -eu; \
    case "${SITE_URL:-}" in https://*) ;; *) echo "SITE_URL build arg must be the public https:// address (Railway service variable SITE_URL)" >&2; exit 1 ;; esac; \
    case "$SITE_URL" in *__*|*/) echo "SITE_URL is still a placeholder or ends with /: $SITE_URL" >&2; exit 1 ;; esac; \
    find web -name '*.html' -exec sed -i "s#__SITE_URL__#${SITE_URL}#g" {} +; \
    printf '%s' "$SITE_URL" > engine/site_url.txt; \
    if grep -rl '__SITE_URL__' web; then echo "placeholder left in web/" >&2; exit 1; fi; \
    rm -rf engine/__pycache__; python -m compileall -q engine; \
    mkdir -p /data && chown app:app /data
WORKDIR /app/engine
EXPOSE 8080
# PORT: Railway injects it (the domain targets it); 8080 is only the default for `docker run`.
# Started as root only to hand the volume to uid 10001 (a fresh Railway volume is owned by root), then setpriv drops to it and execs uvicorn:
# one process (--workers 1 explicitly: otherwise uvicorn reads WEB_CONCURRENCY, forks, and only the children hit guard.py while the
# parent idles — with it, the guard refuses in the main process, exit 3), SIGTERM reaches uvicorn directly. --log-level info prints
# «Uvicorn running on http://0.0.0.0:<PORT>» (the line to wait for in `railway logs`); --no-access-log keeps requests out of the log.
# No --proxy-headers: the engine reads the client IP from the platform header itself (RHDEX_TRUST_PROXY).
CMD ["sh", "-c", "set -e; if [ \"$(id -u)\" = 0 ]; then chown -R \"$APP_UID:$APP_UID\" /data; exec setpriv --reuid=\"$APP_UID\" --regid=\"$APP_UID\" --clear-groups --inh-caps=-all env HOME=/tmp uvicorn server:app --host 0.0.0.0 --port \"$PORT\" --workers 1 --no-proxy-headers --no-server-header --no-access-log --timeout-graceful-shutdown 10 --log-level info; fi; exec uvicorn server:app --host 0.0.0.0 --port \"$PORT\" --workers 1 --no-proxy-headers --no-server-header --no-access-log --timeout-graceful-shutdown 10 --log-level info"]
