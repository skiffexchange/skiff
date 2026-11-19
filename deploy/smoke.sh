#!/bin/sh
# Skiff (rhdex): checks after a deploy. Read-only: GET/HEAD only, nothing is signed, built or stored. Prints PASS / FAIL / SKIP / WARN.
#   sh deploy/smoke.sh https://skiff.exchange                 production check (Railway, 16.11)
#   SMOKE_SITE=https://skiff.exchange sh deploy/smoke.sh <BASE>   before DNS: the platform address, pages built for the domain
#   sh deploy/smoke.sh http://127.0.0.1:8850                  local stand (DEV + QUOTE_REPLAY): prod-only checks print SKIP
#   SMOKE_HEALTH_TOKEN=… sh deploy/smoke.sh https://<domain>  also reads the full /healthz (dev:false, quoteReplay:false, routerKey:true)
#   SMOKE_PROD=1 SMOKE_SITE=https://skiff.example sh deploy/smoke.sh http://127.0.0.1:18080   the prod image run locally:
#                                                     all prod checks except http→https/HSTS; og/siteUrl are expected to be SMOKE_SITE
#   SMOKE_OFFLINE=1 …   the engine runs with router and RPC unreachable: checks that need the chain or a reach
#                       measurement print SKIP
# Exit code 0 = no FAIL. Needs curl and python3 (python runs with -I, stdlib only).
set -u
BASE="${1:-}"; BASE="${BASE%/}"
case "$BASE" in http://*|https://*) ;; *) echo "usage: sh deploy/smoke.sh <base-url>, e.g. https://skiff.exchange or http://127.0.0.1:8850" >&2; exit 2 ;; esac
LOCAL=0; case "$BASE" in http://127.0.0.1*|http://localhost*|http://\[::1\]*) LOCAL=1 ;; esac
TLS=1; [ "$LOCAL" = 1 ] && TLS=0; [ "${SMOKE_PROD:-}" = 1 ] && LOCAL=0
SITE="${SMOKE_SITE:-$BASE}"; SITE="${SITE%/}"
HOST=$(echo "$BASE" | sed -E 's#^https?://##; s#[/:].*$##')
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
N=0; F=0; S=0
ok()   { N=$((N+1)); echo "PASS  $1"; }
bad()  { N=$((N+1)); F=$((F+1)); echo "FAIL  $1"; }
skip() { S=$((S+1)); echo "SKIP  $1"; }
warn() { echo "WARN  $1"; }
chk()  { if [ "$2" = 1 ]; then ok "$1"; else bad "$1${3:+  [$3]}"; fi; }
get()  { curl -s -o "$T/body" -D "$T/hdr" -w '%{http_code}' --max-time 40 "$@"; }
hdr()  { tr -d '\r' < "$T/hdr" | grep -i "^$1:" | tail -1 | cut -d: -f2- | sed 's/^ *//'; }
yes()  { if "$@" >/dev/null 2>&1; then echo 1; else echo 0; fi; }
py()   { python3 -I -c "$1" "$T/body"; }       # $1 = python code reading json from sys.argv[1]; prints "1" or a reason

echo "== Skiff smoke: $BASE  ($([ $LOCAL = 1 ] && echo 'local stand: prod-only checks are SKIP' || echo "production, site $SITE"))"

# ---- 1. health
c=$(get "$BASE/healthz"); chk "/healthz 200" $([ "$c" = 200 ] && echo 1 || echo 0) "code $c"
chk "/healthz ok:true" $(yes grep -q '"ok":true' "$T/body")
if [ $LOCAL = 0 ]; then
  r=$(py 'import json,sys;d=json.load(open(sys.argv[1]));print(1 if not ({"dev","routerBudget","rpc","threads"} & set(d)) else "full body is public: "+",".join(sorted(d)))')
  chk "/healthz public body is short (no dev/router/rpc internals)" $([ "$r" = 1 ] && echo 1 || echo 0) "$r"
  if [ -n "${SMOKE_HEALTH_TOKEN:-}" ]; then
    c=$(get -H "x-health-token: $SMOKE_HEALTH_TOKEN" "$BASE/healthz")
    r=$(py 'import json,sys;d=json.load(open(sys.argv[1]));b=[k for k,v in (("dev",False),("quoteReplay",False),("routerKey",True)) if d.get(k)!=v];print(1 if not b else "wrong: "+",".join(b))')
    chk "/healthz full: dev:false, quoteReplay:false, routerKey:true" $([ "$r" = 1 ] && echo 1 || echo 0) "$r"
  else skip "/healthz full body (set SMOKE_HEALTH_TOKEN)"; fi
else skip "/healthz public body is short (local stand serves the full body to 127.0.0.1 under DEV)"; fi

# ---- 2. https (prod only)
if [ $TLS = 1 ]; then
  c=$(curl -s -o /dev/null -w '%{http_code}' --max-time 30 "http://$HOST/"); chk "http:// redirects to https (301/308)" $([ "$c" = 301 ] || [ "$c" = 308 ] && echo 1 || echo 0) "code $c"
  get -I "$BASE/" >/dev/null; chk "HSTS on https" $([ -n "$(hdr strict-transport-security)" ] && echo 1 || echo 0)
else skip "http→https redirect and HSTS (prod only)"; fi

# ---- 2b. www → apex (prod, the real domain only): a 301/308 to the apex, or the same page with the apex canonical
SHOST=$(echo "$SITE" | sed -E 's#^https?://##; s#[/:].*$##')
if [ $TLS = 1 ] && [ "$HOST" = "$SHOST" ] && [ "${SHOST#www.}" = "$SHOST" ]; then
  c=$(curl -s -o "$T/www" -D "$T/wwwh" -w '%{http_code}' --max-time 30 "https://www.$SHOST/faq"); loc=$(tr -d '\r' < "$T/wwwh" | grep -i '^location:' | cut -d' ' -f2)
  if [ "$c" = 301 ] || [ "$c" = 308 ]; then chk "www.$SHOST → $SITE (code $c)" $([ "$loc" = "$SITE/faq" ] && echo 1 || echo 0) "location $loc"
  elif [ "$c" = 200 ]; then chk "www.$SHOST serves the page with the apex canonical (no redirect rule yet)" $(yes grep -q "rel=\"canonical\" href=\"$SITE/faq\"" "$T/www")
    warn "www.$SHOST answers 200 instead of a 301 to $SITE (Railway → Settings → Edge: redirect rule)"
  else bad "www.$SHOST/faq → code $c (DNS / certificate?)"; fi
else skip "www → apex (prod on the real domain only)"; fi

# ---- 3. pages: 200, text/html, no-store, security headers, no placeholders
for p in / /app /stocks /stock/NVDA /faq /legal; do
  c=$(get "$BASE$p"); cp "$T/body" "$T/page_$(echo "$p" | tr '/' '_')"
  cc=$(hdr cache-control); ct=$(hdr content-type)
  chk "$p 200 text/html, cache-control no-store" $([ "$c" = 200 ] && echo "$ct" | grep -qi text/html && echo "$cc" | grep -qi no-store && echo 1 || echo 0) "code $c, $ct, $cc"
done
c=$(get "$BASE/nope-smoke-$$"); chk "unknown path → 404 text/html no-store" $([ "$c" = 404 ] && hdr content-type | grep -qi text/html && hdr cache-control | grep -qi no-store && echo 1 || echo 0) "code $c"
c=$(get "$BASE/api/nope-smoke"); chk "unknown /api path → 404 JSON" $([ "$c" = 404 ] && hdr content-type | grep -qi json && echo 1 || echo 0) "code $c"
c=$(get -I "$BASE/"); chk "HEAD / 200" $([ "$c" = 200 ] && echo 1 || echo 0) "code $c"
get "$BASE/app" >/dev/null
chk "/app x-frame-options DENY + CSP frame-ancestors 'none'" $(hdr x-frame-options | grep -qi deny && hdr content-security-policy | grep -q "frame-ancestors 'none'" && echo 1 || echo 0)
chk "/app x-content-type-options nosniff + referrer-policy" $(hdr x-content-type-options | grep -qi nosniff && [ -n "$(hdr referrer-policy)" ] && echo 1 || echo 0)
PH=$(grep -l '__SITE_URL__' "$T"/page_* 2>/dev/null | sed 's#.*/page_##' | tr '\n' ' ')
if [ $LOCAL = 0 ]; then chk "no __SITE_URL__ placeholder in pages" $([ -z "$PH" ] && echo 1 || echo 0) "$PH"
  LH=$(grep -li 'localhost\|127\.0\.0\.1' "$T"/page_* 2>/dev/null | sed 's#.*/page_##' | tr '\n' ' '); chk "no localhost in pages" $([ -z "$LH" ] && echo 1 || echo 0) "$LH"
  cb=""; for f in "$T"/page_*; do pth=$(basename "$f" | sed 's#^page_##; s#_#/#g'); [ "$pth" = / ] && want="$SITE/" || want="$SITE$pth"
    grep -q "rel=\"canonical\" href=\"$want\"" "$f" || cb="$cb $pth"; done
  chk "canonical = $SITE<path> on every page" $([ -z "$cb" ] && echo 1 || echo 0) "wrong on:$cb"
  c=$(get "$BASE/robots.txt"); chk "robots.txt 200, Sitemap: $SITE/sitemap.xml" $([ "$c" = 200 ] && grep -qx "Sitemap: $SITE/sitemap.xml" "$T/body" && echo 1 || echo 0) "code $c"
  c=$(get "$BASE/sitemap.xml"); r=$(python3 -I -c 'import re,sys;b=open(sys.argv[1]).read();l=re.findall(r"<loc>([^<]*)</loc>",b);print(1 if l and all(x.startswith(sys.argv[2]+"/") for x in l) else "locs: %d, off-site: %s"%(len(l),[x for x in l if not x.startswith(sys.argv[2]+"/")][:2]))' "$T/body" "$SITE")
  chk "sitemap.xml: every <loc> on $SITE" $([ "$c" = 200 ] && [ "$r" = 1 ] && echo 1 || echo 0) "code $c $r"
else skip "no __SITE_URL__ in pages, canonical / robots / sitemap on the domain (local web/ is built without a domain; the image build replaces it)"; fi

# ---- 4. og tags + og.png
P="$T/page__"
for m in 'property="og:title"' 'property="og:description"' 'property="og:image"' 'property="og:url"' 'name="twitter:card" content="summary_large_image"' 'name="twitter:image"'; do
  chk "/ has <meta $m>" $(yes grep -q "$m" "$P")
done
OGI=$(grep -o 'property="og:image" content="[^"]*"' "$P" | head -1 | sed 's/.*content="//; s/"$//')
if [ $LOCAL = 0 ]; then chk "og:image is absolute on this site ($OGI)" $(echo "$OGI" | grep -q "^$SITE/" && echo 1 || echo 0)
  OGU=$(grep -o 'property="og:url" content="[^"]*"' "$P" | sed 's/.*content="//; s/"$//'); chk "og:url = $SITE/" $([ "$OGU" = "$SITE/" ] && echo 1 || echo 0) "$OGU"
else skip "og:image / og:url absolute (placeholder locally)"; fi
OGP="/$(echo "$OGI" | sed -E 's#^(https?://[^/]+|__SITE_URL__)/##')"
c=$(get "$BASE$OGP"); chk "og image $OGP 200 image/png" $([ "$c" = 200 ] && hdr content-type | grep -qi image/png && echo 1 || echo 0) "code $c"
W=$(python3 -I -c 'import struct,sys;b=open(sys.argv[1],"rb").read(24);print("%dx%d"%struct.unpack(">II",b[16:24]) if b[:8]==b"\x89PNG\r\n\x1a\n" else "not png")' "$T/body")
chk "og image is 1200x630 ($W)" $([ "$W" = 1200x630 ] && echo 1 || echo 0)

# ---- 5. cache headers on assets (HTML no-store is checked above): versioned (?v=) → immutable for a year
ASSETS=$(grep -ohE '(src|href)="/assets/[^"]+\.(js|css)\?v=[^"]*"' "$P" | sed -E 's/^(src|href)="//; s/"$//' | sort -u | head -4)
chk "/ references versioned assets (?v=)" $([ -n "$ASSETS" ] && echo 1 || echo 0)
for a in $ASSETS; do
  get -I "$BASE$a" >/dev/null; cc=$(hdr cache-control); age=$(echo "$cc" | sed -nE 's/.*max-age=([0-9]+).*/\1/p')
  chk "$a immutable, max-age ≥ 1 year ($cc)" $([ "${age:-0}" -ge 31536000 ] && echo "$cc" | grep -qi immutable && echo 1 || echo 0)
done
a=$(echo "$ASSETS" | head -1 | sed 's/?.*//')
if [ -n "$a" ]; then get -I "$BASE$a" >/dev/null; cc=$(hdr cache-control)
  chk "$a without ?v= is not immutable ($cc)" $(echo "$cc" | grep -qi immutable && echo 0 || echo 1); fi

# ---- 6. API JSON shape
shape() { c=$(get "$BASE$1"); r=$(py "$2" 2>&1 | tail -1); chk "$1 200 JSON shape" $([ "$c" = 200 ] && [ "$r" = 1 ] && echo 1 || echo 0) "code $c ${r#1}"; }
shape /api/tokens 'import json,sys;d=json.load(open(sys.argv[1]));t=d["tokens"];assert isinstance(d["fields"],dict) and isinstance(d["chains"],list) and d["chains"];assert len(t)>=60 and all(k in t[0] for k in "casndk");assert isinstance(d["pricesAt"],int) and isinstance(d["stale"],bool);print(1)'
shape /api/venues 'import json,sys;d=json.load(open(sys.argv[1]));assert d["tools"] and d["dexes"];assert all(k in d["dexes"][0] for k in ("dex","name","logo","kind"));print(1)'
shape /api/board 'import json,sys;d=json.load(open(sys.argv[1]));r=d["rows"];assert isinstance(r,list) and isinstance(d["at"],int) and "lastError" in d;assert r and all("stock" in x and "sizeUsd" in x for x in r);assert all(k in x for x in r if not x.get("pending") for k in ("best","singlePool","vsSinglePool","postable","hero"));print(1)'
shape /api/trades 'import json,sys;d=json.load(open(sys.argv[1]));t=d["trades"];assert isinstance(t,list) and isinstance(d["minUsd"],int) and isinstance(d["backfilled"],bool);assert all(k in x for x in t[:5] for k in ("tx","stock","side","usd","ts","explorer"));print(1)'
shape /api/stats 'import json,sys;d=json.load(open(sys.argv[1]));assert all(k in d for k in ("volume24hUsd","swaps24h","bigTrades24h","byDex","byStock","venues","stale","backfilled","at"));print(1)'
shape "/api/price-history?stock=NVDA" 'import json,sys;d=json.load(open(sys.argv[1]));assert all(k in d for k in ("points","count","enough","last","change24hPct","price","pool","backfilled"));assert (not d["enough"]) or (d["points"] and all(len(p)==2 and p[1]>0 for p in d["points"]));print(1)'
shape "/api/stocks-overview" 'import json,sys;d=json.load(open(sys.argv[1]));r=d["stocks"];assert len(r)>=60;k=("stock","price","change24hPct","spark","liqUsd","pools","venues","volume24hUsd","bigBuys24h");assert all(all(x in s for x in k) for s in r);assert all(len(p)==2 and p[1]>0 for s in r for p in s["spark"]);print(1)'
shape /api/config 'import json,sys;d=json.load(open(sys.argv[1]));assert d["chainId"]==4663 and d["brand"] and d["ourFeePct"]==0;print(1)'
# v3 (new pitch 16.11): measured reach, Pay-from networks, trades sharePct, track validation
shape /api/reach 'import json,sys;d=json.load(open(sys.argv[1]));assert d["ourFeePct"]==0 and isinstance(d["stale"],bool);assert d.get("pending") or (d["measuredAt"] and d["sources"]["count"]==d["dexes"]["count"]+d["makers"]["count"]+d["aggregators"]["count"]);assert d["chains"]["list"][0]["id"]==4663;assert all(x["dex"]!="other" for x in d["dexes"]["list"]);print(1)'
c=$(get "$BASE/api/tokens"); r=$(py 'import json,sys;d=json.load(open(sys.argv[1]));cs=d["chains"];assert cs[0]["id"]==4663 and all(isinstance(c["native"],dict) and c["rpc"] and c["explorer"].startswith("https://") for c in cs);assert 1151111081099710 not in {c["id"] for c in cs};assert all((c["logo"] or "/").startswith("/") for c in cs) and all((t["l"] or "/").startswith("/") for t in d["tokens"]);print(1)')
chk "/api/tokens chains[]: Pay-from rows {native{}, rpc, explorer}, no Solana, logos only local or /api/img" $([ "$r" = 1 ] && echo 1 || echo 0) "$r"
r=$(py 'import json,re,sys;d=json.load(open(sys.argv[1]));b=[c["id"] for c in d["chains"] if not re.fullmatch(r"0x[0-9a-f]{40}",str(c.get("routeContract") or ""))];print(1 if not b else "no routeContract: "+",".join(map(str,b)))')
cp "$T/body" "$T/tokens.json"; get "$BASE/api/reach" >/dev/null; RPEND=$(py 'import json,sys;print(1 if json.load(open(sys.argv[1])).get("pending") else 0)' 2>/dev/null); cp "$T/tokens.json" "$T/body"
if [ "$r" != 1 ] && { [ "${SMOKE_OFFLINE:-}" = 1 ] || [ "$RPEND" = 1 ]; }; then
  skip "every Pay-from network names its route contract — reach not measured yet ($r); a fresh volume measures ~20 s after start, rerun in a few minutes"
else chk "/api/tokens chains[]: every Pay-from network names its route contract (the sign layer pins it)" $([ "$r" = 1 ] && echo 1 || echo 0) "$r"; fi
c=$(get "$BASE/api/reach"); cp "$T/body" "$T/reach.json"; get "$BASE/api/tokens" >/dev/null
r=$(python3 -I -c 'import json,sys;a=json.load(open(sys.argv[1]));b=json.load(open(sys.argv[2]));print(1 if [c["id"] for c in a["chains"]["list"]]==[c["id"] for c in b["chains"]] else "reach chains != tokens chains")' "$T/reach.json" "$T/body")
chk "/api/reach chains = /api/tokens chains (same Pay-from list)" $([ "$r" = 1 ] && echo 1 || echo 0) "$r"
c=$(get "$BASE/api/trades?limit=20"); r=$(py 'import json,sys;t=json.load(open(sys.argv[1]))["trades"];assert all("sharePct" in p for x in t for p in x["pools"]);assert all(abs(sum(p["shareBps"] for p in x["pools"])-10000)==0 for x in t if x["pools"] and x["usd"]>0);print(1)')
chk "/api/trades pools[] carry sharePct (bps sum 10,000)" $([ "$r" = 1 ] && echo 1 || echo 0) "$r"
c=$(get "$BASE/api/track?txHash=0x12"); chk "/api/track bad hash → 400" $([ "$c" = 400 ] && echo 1 || echo 0) "code $c"
c=$(get "$BASE/api/track?txHash=0x$(printf 'a%.0s' $(seq 64))&toChain=1"); chk "/api/track toChain ≠ 4663 → 400" $([ "$c" = 400 ] && echo 1 || echo 0) "code $c"
TXT=$(get "$BASE/api/trades?limit=1" >/dev/null; py 'import json,sys;t=json.load(open(sys.argv[1]))["trades"];print(t[0]["tx"] if t else "")')
if [ -n "$TXT" ]; then
  c=$(get "$BASE/api/track?txHash=$TXT"); r=$(py 'import json,sys;d=json.load(open(sys.argv[1]));assert d["status"] in ("DONE","FAILED","PENDING");assert [n["kind"] for n in d["nodes"]]==["source","split","wallet"];assert d["status"]!="DONE" or all(n["state"]=="done" and n["at"] and n["explorer"].startswith("https://robinhoodchain.blockscout.com/tx/") for n in d["nodes"]);print(1)')
  chk "/api/track on the latest feed trade: source → split → wallet, confirmed with block time" $([ "$c" = 200 ] && [ "$r" = 1 ] && echo 1 || echo 0) "code $c $r"
else skip "/api/track on a feed trade (feed empty)"; fi
get "$BASE/api/config" >/dev/null; cp "$T/body" "$T/config.json"
if [ $LOCAL = 0 ]; then
  r=$(python3 -I -c 'import json,sys;d=json.load(open(sys.argv[1]));b=[];d.get("privyAppId") or b.append("privyAppId null");d.get("quoteReplay") and b.append("quoteReplay on");d.get("siteUrl")==sys.argv[2] or b.append("siteUrl "+str(d.get("siteUrl")));print(1 if not b else ", ".join(b))' "$T/config.json" "$SITE")
  chk "/api/config: privyAppId set, quoteReplay false, siteUrl = $SITE" $([ "$r" = 1 ] && echo 1 || echo 0) "$r"
  get "$BASE/api/board" >/dev/null; r=$(py 'import json,sys;d=json.load(open(sys.argv[1]));print(1 if not d.get("replay") and not any(x.get("replay") for x in d["rows"]) else "replay rows served")')
  chk "/api/board: no replay rows" $([ "$r" = 1 ] && echo 1 || echo 0) "$r"
else skip "/api/config privyAppId + quoteReplay false, /api/board without replay rows (local stand runs QUOTE_REPLAY)"; fi

# ---- 7. dev sign-in impossible
DW='X-Dev-Wallet: 0x1111111111111111111111111111111111111111'
c=$(get -H "$DW" -H 'X-Forwarded-For: 203.0.113.7' "$BASE/api/me")
chk "X-Dev-Wallet + X-Forwarded-For → /api/me signedIn:false" $([ "$c" = 200 ] && grep -q '"signedIn":false' "$T/body" && echo 1 || echo 0) "code $c"
for H in 'X-Real-IP: 203.0.113.7' 'Fly-Client-IP: 203.0.113.7' 'Host: evil.example'; do   # audit v4 M18
  c=$(get -H "$DW" -H "$H" "$BASE/api/me")
  # a foreign Host may be refused by the edge before the app (Railway answers 404) — that is also "not signed in"
  ok=$( { [ "$c" = 200 ] && grep -q '"signedIn":false' "$T/body"; } || { [ "$H" = 'Host: evil.example' ] && [ "$c" = 404 ]; } && echo 1 || echo 0)
  chk "X-Dev-Wallet + $H → /api/me not signed in (200 signedIn:false, or edge 404 for a foreign Host)" $ok "code $c"
done
c=$(get -H 'Host: evil.example' "$BASE/healthz"); r=$(py 'import json,sys;d=json.load(open(sys.argv[1]));print(1 if "routerBudget" not in d else "full body for a foreign Host")')
chk "/healthz with a foreign Host → short body (never the full one)" $([ "$r" = 1 ] && echo 1 || echo 0) "$r"
c=$(get -X POST -H 'content-type: application/json' -H 'X-Forwarded-For: 203.0.113.7' -d '{"chatId":1,"code":"x"}' "$BASE/api/dev/tg/start")
chk "/api/dev/tg/start from a proxied client → 404" $([ "$c" = 404 ] && echo 1 || echo 0) "code $c"
hx=$(grep -il 'x-dev-wallet' "$T/hdr" >/dev/null && echo 1 || echo 0); chk "no response header mentions X-Dev-Wallet" $([ "$hx" = 0 ] && echo 1 || echo 0)
if [ $LOCAL = 0 ]; then
  get -I "$BASE/" >/dev/null; sv=$(hdr server); chk "no uvicorn Server header (--no-server-header)" $(echo "$sv" | grep -qi uvicorn && echo 0 || echo 1) "server: $sv"
  c=$(get -H "$DW" "$BASE/api/me"); chk "X-Dev-Wallet → /api/me signedIn:false" $([ "$c" = 200 ] && grep -q '"signedIn":false' "$T/body" && echo 1 || echo 0) "code $c"
  c=$(get -H "$DW" "$BASE/api/me/watchlist"); chk "X-Dev-Wallet → /api/me/watchlist 401" $([ "$c" = 401 ] && echo 1 || echo 0) "code $c"
else skip "plain X-Dev-Wallet → signedIn:false (the local DEV stand accepts it from 127.0.0.1 by design)"; fi

# ---- 7b. Telegram is off until the token launch (code stays, TG_ENABLED=1 brings it back)
c=$(get "$BASE/api/me/telegram"); chk "/api/me/telegram → 404 (Telegram off)" $([ "$c" = 404 ] && echo 1 || echo 0) "code $c"
c=$(get "$BASE/api/config"); r=$(py 'import json,sys;d=json.load(open(sys.argv[1]));print(1 if d.get("telegramBot") is None and "telegram" not in d.get("features",{}) else "telegram still in /api/config")')
chk "/api/config: no telegramBot, no telegram feature" $([ "$r" = 1 ] && echo 1 || echo 0) "$r"
tgh=""; for u in / /app /stocks /stock/NVDA /faq /legal /strings.json /assets/js/strings.js; do
  get "$BASE$u" >/dev/null; grep -qi 'telegram' "$T/body" && tgh="$tgh $u"; done
chk "no «Telegram» in served pages and strings" $([ -z "$tgh" ] && echo 1 || echo 0) "found in:$tgh"
# alerts are off as well (ALERTS_ENABLED=1 brings them back)
c=$(get "$BASE/api/me/alerts"); chk "/api/me/alerts → 404 (alerts off)" $([ "$c" = 404 ] && echo 1 || echo 0) "code $c"
c=$(get "$BASE/api/me/alerts/feed"); chk "/api/me/alerts/feed → 404" $([ "$c" = 404 ] && echo 1 || echo 0) "code $c"
c=$(get "$BASE/api/config"); r=$(py 'import json,sys;d=json.load(open(sys.argv[1]));print(1 if "alerts" not in d.get("features",{}) else "alerts still in features")')
chk "/api/config: no alerts feature" $([ "$r" = 1 ] && echo 1 || echo 0) "$r"
alh=""; for u in /strings.json /assets/js/strings.js; do get "$BASE$u" >/dev/null; grep -qi 'alert' "$T/body" && alh="$alh $u"; done
for u in / /app /stocks /stock/NVDA /faq /legal; do get "$BASE$u" >/dev/null; grep -qiE '#alerts|>Alerts<|set an alert|big-trade alert' "$T/body" && alh="$alh $u"; done
chk "no alerts in served pages and strings" $([ -z "$alh" ] && echo 1 || echo 0) "found in:$alh"

# ---- 7c. any token address on 4663: lookup + picture proxy
c=$(get "$BASE/api/token/0xnothex"); chk "/api/token bad address → 400" $([ "$c" = 400 ] && echo 1 || echo 0) "code $c"
if [ "${SMOKE_OFFLINE:-}" = 1 ]; then skip "/api/token wallet address (no code) → 404 (needs the chain RPC)"; else
c=$(get "$BASE/api/token/0x1111111111111111111111111111111111111111"); chk "/api/token wallet address (no code) → 404 not_a_token" $([ "$c" = 404 ] && grep -q not_a_token "$T/body" && echo 1 || echo 0) "code $c"; fi
NVA=$(get "$BASE/api/tokens" >/dev/null; py 'import json,sys;print([t["a"] for t in json.load(open(sys.argv[1]))["tokens"] if t["s"]=="NVDA" and t["c"]==4663][0])')
c=$(get "$BASE/api/token/$NVA"); r=$(py 'import json,sys;d=json.load(open(sys.argv[1]));print(1 if d.get("listed") is True and d.get("symbol")=="NVDA" else d)')
chk "/api/token listed NVDA → our record" $([ "$c" = 200 ] && [ "$r" = 1 ] && echo 1 || echo 0) "code $c $r"
c=$(get "$BASE/api/img/../../etc/passwd"); chk "/api/img path trick → 404" $([ "$c" = 404 ] && echo 1 || echo 0) "code $c"
c=$(get "$BASE/api/img/000000000000000000000000"); chk "/api/img unknown id → 404" $([ "$c" = 404 ] && echo 1 || echo 0) "code $c"

# ---- 8. no hidden names (SMOKE_HIDDEN_NAMES) in served HTML, JS and public JSON
cat > "$T/scan.py" <<'PY'
import re, sys, os, urllib.request, urllib.parse
base, tmp = sys.argv[1], sys.argv[2]
HIDDEN = os.environ.get("SMOKE_HIDDEN_NAMES") or ""          # optional regex of names that must not appear in any public body
BADW = re.compile(HIDDEN, re.I) if HIDDEN else None
def fetch(u):
    req = urllib.request.Request(u, headers={"user-agent": "skiff-smoke/1", "accept-encoding": "identity"})
    with urllib.request.urlopen(req, timeout=40) as r:
        return r.read().decode("utf-8", "replace")
seen, queue, hits = set(), [], []
for f in sorted(os.listdir(tmp)):
    if f.startswith("page_"):
        queue.append(("page " + f[5:].replace("_", "/"), open(os.path.join(tmp, f), encoding="utf-8", errors="replace").read(), "/"))
for p in ("/api/tokens", "/api/network-tokens", "/api/venues", "/api/board", "/api/trades", "/api/stats", "/api/config", "/api/reach", "/healthz", "/404-smoke"):
    try: queue.append((p, fetch(base + p), "/"))
    except Exception as e:  # 404 page body is read by urllib as an error
        body = e.read().decode("utf-8", "replace") if hasattr(e, "read") else ""
        queue.append((p, body, "/"))
n = 0
while queue:
    name, text, where = queue.pop()
    n += 1
    for m in (BADW.finditer(text) if BADW else ()):
        s = text[max(0, m.start() - 30): m.end() + 30].replace("\n", " ")
        hits.append(f"{name}: …{s}…")
        break
    for ref in re.findall(r"""["'(]((?:/|\./|\.\./)[^"'()\s]*?\.(?:js|mjs|css)(?:\?[^"'()\s]*)?)["')]""", text):
        u = urllib.parse.urljoin(base + where, ref) if not ref.startswith("/") else base + ref
        key = u.split("?")[0]
        if key in seen or not key.startswith(base): continue
        seen.add(key)
        try: queue.append((key[len(base):], fetch(u), key[len(base):]))
        except Exception as e:
            if "/assets/vendor/" not in name: hits.append(f"{key[len(base):]} (from {name}): fetch failed {e}")   # vendor bundles carry dead internal paths
print(f"scanned {n} bodies ({len(seen)} js/css)")
print("1" if not hits else "HITS " + " | ".join(hits[:8]))
PY
r=$(python3 -I "$T/scan.py" "$BASE" "$T" 2>&1)
info=$(echo "$r" | head -1); res=$(echo "$r" | tail -1)
chk "no SMOKE_HIDDEN_NAMES match in pages, JS, CSS, public JSON — $info" $([ "$res" = 1 ] && echo 1 || echo 0) "$res"
# audit v3 N2: no open redirect — protocol-relative and backslash paths are 404, never a Location off the site
for u in "//evil.com/" "///evil.com/faq/" "/%2F%2Fevil.com%2Fa.html" "/%5Cevil.com/a.html" "/%2F%5Cevil.com"; do
  c=$(curl -s -o /dev/null -D "$T/rh" -w '%{http_code}' --max-time 20 --path-as-is "$BASE$u"); loc=$(grep -i '^location:' "$T/rh" | tr -d '\r' | cut -d' ' -f2)
  case "$loc" in ''|/[!/\\]*) okl=1 ;; *) okl=0 ;; esac
  chk "no open redirect: $u" $([ "$okl" = 1 ] && [ "$c" != 200 ] && echo 1 || echo 0) "code $c location '${loc}'"
done
c=$(curl -s -o /dev/null -D "$T/rh" -w '%{http_code}' --max-time 20 "$BASE/no-such-page"); chk "404 carries X-Robots-Tag noindex" $([ "$c" = 404 ] && grep -qi '^x-robots-tag: noindex' "$T/rh" && echo 1 || echo 0) "code $c"
c=$(get "$BASE/assets/js/core.js.map"); chk "no source maps served" $([ "$c" = 404 ] && echo 1 || echo 0) "code $c"
# «Tokens» next to Stocks
c=$(get "$BASE/api/network-tokens"); r=$(py 'import json,re,sys;d=json.load(open(sys.argv[1]));t=d["tokens"];ru=d["rule"];assert [x["symbol"] for x in d["listed"]][:3]==["ETH","WETH","USDG"];assert d.get("pending") or (d["count"]==len(t)>0);assert all(x["liqUsd"]>=ru["minLiqUsd"] and x["vol24hUsd"]>=ru["minVol24hUsd"] and x["kind"]=="token" and x["listed"] is False and isinstance(x["decimals"],int) for x in t);assert all(x["logo"] is None or re.fullmatch(r"/api/img/[0-9a-f]{24}",x["logo"]) or re.fullmatch(r"/assets/logos/net/0x[0-9a-f]{40}\.(png|jpg|webp|gif)\?v=[0-9a-f]{10}",x["logo"]) for x in t);print(1)')
chk "/api/network-tokens: listed ETH/WETH/USDG first, every token passes the rule, decimals, logos only baked files or /api/img" $([ "$c" = 200 ] && [ "$r" = 1 ] && echo 1 || echo 0) "code $c $r"
get "$BASE/api/network-tokens" >/dev/null
r=$(py 'import json,sys;d=json.load(open(sys.argv[1]));b=sorted({x["symbol"] for x in d["tokens"]} & {"COIN","CRWV","ORCL","NBIS","LITE","RKLB","wtHOOD","NET"});print(1 if not b and not any(x["clash"] for x in d["tokens"]) else "listed: "+",".join(b))')
chk "/api/network-tokens: company / stock lookalikes hidden (COIN, CRWV, ORCL, NBIS, LITE, RKLB, wtHOOD, NET)" $([ "$r" = 1 ] && echo 1 || echo 0) "$r"
c=$(get "$BASE/api/token/0x6330d8c3178a418788df01a47479c0ce7ccf450b")
if [ "$c" = 503 ]; then skip "/api/token COIN lookalike (needs the chain RPC)"; else
r=$(py 'import json,sys;d=json.load(open(sys.argv[1]));print(1 if d.get("lookalike") is True and d.get("listed") is False else d)')
chk "/api/token COIN (Coinbase lookalike) → lookalike:true, still not listed" $([ "$c" = 200 ] && [ "$r" = 1 ] && echo 1 || echo 0) "code $c $r"; fi
get "$BASE/api/tokens" >/dev/null
L=$(py 'import json,sys;d=json.load(open(sys.argv[1]));print(" ".join(t["l"] for t in d["tokens"] if t["c"]!=4663 and t["l"]))'); n=0; badl=""
for u in $L; do n=$((n+1)); case "$u" in /api/img/*) badl="$badl $u:img";; *) c=$(curl -s -o /dev/null -w '%{http_code}' --max-time 20 "$BASE$u"); [ "$c" = 200 ] || badl="$badl $u:$c";; esac; done
chk "/api/tokens: all $n logos of other networks' tokens are local files that load (no /api/img)" $([ -z "$badl" ] && echo 1 || echo 0) "$badl"
get "$BASE/api/network-tokens" >/dev/null
L=$(py 'import json,sys;d=json.load(open(sys.argv[1]));print(" ".join(x["logo"] for x in d["tokens"] if x["logo"])[:4000])'); n=0; badl=""
for u in $L; do n=$((n+1)); c=$(curl -s -o /dev/null -w '%{http_code}' --max-time 20 "$BASE$u"); [ "$c" = 200 ] || badl="$badl $u:$c"; [ $n -ge 25 ] && break; done
chk "/api/network-tokens: the first $n logos load (200) — no letter fallback by 404" $([ -z "$badl" ] && echo 1 || echo 0) "$badl"
get "$BASE/api/network-tokens" >/dev/null
v=$(hdr cache-control); chk "/api/network-tokens cached 5 min" $(echo "$v" | grep -q 'max-age=300' && echo 1 || echo 0) "$v"
# audit v4 (fresh C5, M9–M11, M13; works, C-11, M4)
c=$(curl -s -o /dev/null -D "$T/fh" -w '%{http_code}' --max-time 20 "$BASE/favicon.ico")
chk "/favicon.ico → 301 /assets/brand/favicon.svg" $([ "$c" = 301 ] && tr -d '\r' < "$T/fh" | grep -qi '^location: /assets/brand/favicon.svg' && echo 1 || echo 0) "code $c"
c=$(curl -s -o /dev/null -D "$T/th" -w '%{http_code}' --max-time 20 "$BASE/tokens")
chk "/tokens → 301 /app (page removed 17.11)" $([ "$c" = 301 ] && tr -d '\r' < "$T/th" | grep -qi '^location: /app' && echo 1 || echo 0) "code $c"
get "$BASE/sitemap.xml" >/dev/null; chk "sitemap.xml without /tokens" $(grep -q '/tokens<' "$T/body" && echo 0 || echo 1)
c=$(get "$BASE/no-such-page"); chk "404 page has no canonical and no og:url" $([ "$c" = 404 ] && ! grep -qi 'rel="canonical"' "$T/body" && ! grep -qi 'og:url' "$T/body" && echo 1 || echo 0) "code $c"
c=$(get "$BASE/assets/brand/favicon.svg"); v=$(hdr cache-control)
chk "/assets/brand/* without ?v= is revalidated (no-cache)" $([ "$c" = 200 ] && echo "$v" | grep -q 'no-cache' && echo 1 || echo 0) "$c $v"
get "$BASE/app" >/dev/null; v=$(hdr content-security-policy)
chk "CSP connect-src allows Coinbase Wallet (wss/https www.walletlink.org)" $(echo "$v" | grep -q 'wss://www.walletlink.org' && echo "$v" | grep -q 'https://www.walletlink.org' && echo 1 || echo 0)
c=$(get "$BASE/api/quote?fromToken=0x5fc5360d0400a0fd4f2af552add042d716f1d168&toToken=0x1111111111111111111111111111111111111111&amount=1000000")
chk "/api/quote with an unknown toToken → 400 unknown_token (no provider call)" $([ "$c" = 400 ] && grep -q '"unknown_token"' "$T/body" && echo 1 || echo 0) "code $c"
c=$(get -X POST -H 'content-type: application/json' -d '{"routeId":"0123456789abcdef0123","fromAddress":"0x1111111111111111111111111111111111111111","minOut":"0"}' "$BASE/api/build")
chk "/api/build with an unknown routeId → 410 route_expired" $([ "$c" = 410 ] && grep -q '"route_expired"' "$T/body" && echo 1 || echo 0) "code $c"
c=$(get "$BASE/api/token/0x1111111111111111111111111111111111111111?soft=1")
if [ "$c" = 503 ]; then skip "/api/token ?soft=1 not a token (needs the chain RPC)"; else
chk "/api/token not a token with ?soft=1 → 200 {ok:false, code:not_a_token}" $([ "$c" = 200 ] && grep -q '"not_a_token"' "$T/body" && echo 1 || echo 0) "code $c"; fi

# ---- Vault (/token, 18.11): TOKEN_PAGE unset → /token, /api/vault, /api/vault/check are a plain 404 AND the engine strips every Vault link
#      (header, mobile menu, footer, dock); TOKEN_PAGE=1 → shapes, links present. Probed, so both states PASS.
vlinks() { python3 -I -c 'import re,sys;t=open(sys.argv[1]).read();print(len(re.findall(r"<a href=\"/token\"",t)))' "$T/body"; }
c=$(get "$BASE/api/vault")
if [ "$c" = 404 ]; then
  echo "      (Vault is off on this engine: TOKEN_PAGE unset)"
  chk "Vault off: /api/vault → 404 not_found" $(grep -q '"not_found"' "$T/body" && echo 1 || echo 0)
  c2=$(curl -s -o /dev/null -w '%{http_code}' --max-time 20 "$BASE/token"); chk "Vault off: /token → 404" $([ "$c2" = 404 ] && echo 1 || echo 0) "code $c2"
  c3=$(curl -s -o /dev/null -w '%{http_code}' --max-time 20 "$BASE/api/vault/check?address=0x1111111111111111111111111111111111111111"); chk "Vault off: /api/vault/check → 404" $([ "$c3" = 404 ] && echo 1 || echo 0) "code $c3"
  for pg in / /app /faq; do get "$BASE$pg" >/dev/null; n=$(vlinks); chk "Vault off: no /token link on $pg (menu, footer, dock stripped)" $([ "$n" = 0 ] && echo 1 || echo 0) "$n links"; done
  get "$BASE/nope-vault-404" >/dev/null; n=$(vlinks); chk "Vault off: no /token link on the 404 page" $([ "$n" = 0 ] && echo 1 || echo 0) "$n links"
  get "$BASE/sitemap.xml" >/dev/null; chk "Vault off: sitemap.xml without /token" $(grep -q '/token<' "$T/body" && echo 0 || echo 1)
else
  r=$(py 'import json,sys;d=json.load(open(sys.argv[1]));k={"ticker","ca","vault","launched","holdings","log","drops","nextDrop"};m=k-set(d);h=d.get("holdings");ok=not m and d["launched"]==bool(d["ca"]) and isinstance(d["log"],list) and (d["ticker"] is None or d["ticker"].startswith("$")) and (h is None or (isinstance(h.get("usdg"),dict) and {"raw","decimals","amount"}<=set(h["usdg"]))) and (d["vault"] is not None or h is None);print(1 if ok else "bad shape, missing "+",".join(sorted(m)))')
  chk "Vault on: /api/vault 200 {ticker|null, ca, vault, launched=ca set, holdings {usdg{raw,decimals,amount}, usd}|null, log[], drops, nextDrop}" $([ "$c" = 200 ] && [ "$r" = 1 ] && echo 1 || echo 0) "code $c $r"
  chk "Vault on: /api/vault has no placeholder ({ticker}, {brand}, __TICKER__, \$__)" $(grep -q '{ticker}\|{brand}\|__TICKER__\|\$__' "$T/body" && echo 0 || echo 1)
  L=$(py 'import json,sys;print(1 if json.load(open(sys.argv[1]))["launched"] else 0)')
  r=$(py 'import json,sys,datetime as D
d=json.load(open(sys.argv[1]));P=lambda x:D.datetime.fromisoformat(x.replace("Z","+00:00")).timestamp() if x else None
src,la,fd,nd=d.get("source"),P(d.get("launchAt")),P(d.get("firstDrop")),P(d.get("nextDrop"))
if not d["launched"] and src!="override": ok=la is None and fd is None and nd is None; why="no CA: launchAt/firstDrop/nextDrop must be null"
elif src=="override": ok=fd is not None and nd is not None and nd>=fd and (nd-fd)%604800==0; why="override: nextDrop = firstDrop + k weeks"
else: ok=la is not None and fd==la+604800 and nd is not None and (nd-fd)%604800==0 and d.get("launchSource") in ("chain","first_seen") and src==d["launchSource"]; why="CA: firstDrop = launchAt + 7 d, weekly"
print(1 if ok else why+" "+json.dumps({k:d.get(k) for k in ("launched","launchAt","launchSource","firstDrop","nextDrop","source")}))')
  chk "Vault on: drop clock (no CA → null; CA → launchAt + 7 d weekly; VAULT_FIRST_DROP override wins)" $([ "$r" = 1 ] && echo 1 || echo 0) "$r"
  r=$(py 'import json,sys;h=json.load(open(sys.argv[1]))["holdings"];print(1 if h is None or (int(h["dropUsdg"]["raw"])==int(h["usdg"]["raw"])//2) else h)')
  chk "Vault on: holdings.dropUsdg = 50% of the USDG balance (integer raw)" $([ "$r" = 1 ] && echo 1 || echo 0) "$r"
  c=$(get "$BASE/token"); rb=$(hdr x-robots-tag)
  chk "Vault on: /token 200, no placeholder anywhere in the page ({ticker}, {brand}, __TICKER__, \$__)" $([ "$c" = 200 ] && grep -q 'data-s="token.hero.title"' "$T/body" && ! grep -q '{ticker}\|{brand}\|__TICKER__\|\$__' "$T/body" && echo 1 || echo 0) "code $c"
  chk "Vault on: /token has its own og card (og-token.png?v=) on og:image and twitter:image" $([ "$(grep -c 'og-token.png?v=' "$T/body")" -ge 2 ] && echo 1 || echo 0)
  if [ "$L" = 1 ]; then chk "Vault launched (TOKEN_CA set): /token indexable" $([ -z "$rb" ] && ! grep -q 'name="robots" content="noindex"' "$T/body" && echo 1 || echo 0) "x-robots-tag $rb"
  else chk "Vault before launch (no TOKEN_CA): /token noindex (header + meta)" $(echo "$rb" | grep -q noindex && grep -q 'name="robots" content="noindex"' "$T/body" && echo 1 || echo 0) "x-robots-tag $rb"; fi
  get "$BASE/sitemap.xml" >/dev/null
  if [ "$L" = 1 ]; then chk "Vault launched: sitemap.xml lists /token" $(grep -q '/token<' "$T/body" && echo 1 || echo 0)
  else chk "Vault before launch: sitemap.xml without /token (noindex)" $(grep -q '/token<' "$T/body" && echo 0 || echo 1); fi
  c=$(get "$BASE/api/vault/check?address=0x12"); chk "Vault on: /api/vault/check bad address → 400 bad_address" $([ "$c" = 400 ] && grep -q '"bad_address"' "$T/body" && echo 1 || echo 0) "code $c"
  c=$(get "$BASE/api/vault/check?address=0x1111111111111111111111111111111111111111")
  r=$(py 'import json,sys;d=json.load(open(sys.argv[1]));print(1 if d.get("address")=="0x1111111111111111111111111111111111111111" and isinstance(d.get("received"),list) and isinstance(d.get("drops"),bool) else d)')
  chk "Vault on: /api/vault/check good address → 200 {address, drops, received[]}" $([ "$c" = 200 ] && [ "$r" = 1 ] && echo 1 || echo 0) "code $c $r"
  get "$BASE/" >/dev/null; n=$(vlinks); chk "Vault on: / links /token in header, mobile menu and footer (3)" $([ "$n" -ge 3 ] && echo 1 || echo 0) "$n links"
  get "$BASE/app" >/dev/null; n=$(vlinks); chk "Vault on: /app links /token in header, mobile menu, footer and dock (4)" $([ "$n" -ge 4 ] && echo 1 || echo 0) "$n links"
fi

echo "== result: $((N-F)) of $N PASS, FAIL: $F, SKIP: $S"
[ "$F" = 0 ]
