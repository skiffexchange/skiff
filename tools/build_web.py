#!/usr/bin/env python3
"""Skiff front-end build. Sources: web-src/ (pages, js, css) + sign/*.js + engine/copy-strings.json -> web/ (served).
Served files carry no comments (JS/CSS minified per file with esbuild, HTML without build markers). Run after any change:

    (cd privy && npm ci)                          # once: esbuild (and the Privy bundle sources)
    python3 tools/build_web.py                    # from the repo root; a running engine (ENGINE=…) adds counts and stock pages

1. engine/copy-strings.json -> web/strings.json and site/web/assets/js/strings.js ({brand} filled from brand.name)
2. sign/*.js + vendor -> web/assets/js/sign/
3. every page: <!--@head …-->/<!--@header …-->/<!--@footer--> blocks re-rendered; data-s / data-sa strings filled
4. /swap = /app; /stock/{TICKER}.html per stock token (title / og per ticker), from stock/index.html
5. one version stamp (hash of every asset) on css/js URLs and on relative ES-module imports
6. optional guard: HIDDEN_NAMES (regex) must match nothing under web/
"""
import hashlib, html, json, os, re, shutil, sys, urllib.request

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
WEB = os.path.abspath(os.environ.get('WEB_OUT') or os.path.join(ROOT, 'web'))   # WEB_OUT: build into a scratch copy (a second engine serves it)
ENGINE = os.environ.get('ENGINE') or 'http://127.0.0.1:8850'
SRC = os.path.join(ROOT, 'web-src')
NODE = os.environ.get('NODE') or shutil.which('node') or 'node'
SIGN = os.path.join(ROOT, 'sign')
STRINGS = os.environ.get('COPY_STRINGS') or os.path.join(ROOT, 'engine', 'copy-strings.json')
STR = json.load(open(STRINGS))
BRAND = STR['brand.name']
HIDDEN = re.compile(os.environ['HIDDEN_NAMES'], re.I) if os.environ.get('HIDDEN_NAMES') else None


def s(id_, **kw):
    v = STR.get(id_)
    if v is None:
        print('MISSING STRING', id_, file=sys.stderr)
        return ''
    v = v.replace('{brand}', BRAND)
    for k, x in kw.items():
        v = v.replace('{' + k + '}', str(x))
    return v


E = lambda x: html.escape(x, quote=True)
CTX = {}
MODPRE = {'index': ['core', 'strings', 'board', 'swap', 'picker', 'wallet', 'chrome'], 'app': ['core', 'strings', 'board', 'swap', 'picker', 'wallet', 'chrome'],
          'swap': ['core', 'strings', 'board', 'swap', 'picker', 'wallet', 'chrome'], '_': ['core', 'strings', 'wallet', 'chrome']}

# ---------- partials ----------
def head(page, path, title=None, desc=None, ogt=None, ogd=None, alt=None, img=None):
    t = title or s(f'meta.{page}.title')
    d = desc if desc is not None else (s(f'meta.{page}.description') if f'meta.{page}.description' in STR else s('meta.home.description'))
    ogt = ogt or (s(f'meta.{page}.og_title') if f'meta.{page}.og_title' in STR else t)
    ogd = ogd or (s(f'meta.{page}.og_description') if f'meta.{page}.og_description' in STR else d)
    alt = alt or (s(f'meta.{page}.og_alt') if f'meta.{page}.og_alt' in STR else s('meta.home.og_alt'))
    if page == 'disclaimer' and 'meta.legal.title' in STR:
        t = s('meta.legal.title'); ogt = t
    site = '__SITE_URL__'
    img = img or '/assets/brand/og.png'   # /token: its own card (og-token.png); every other page the site card
    return f'''<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>{E(t)}</title>
<meta name="description" content="{E(d)}">
<meta name="theme-color" content="#001413">
<meta property="og:type" content="website">
<meta property="og:site_name" content="{E(BRAND)}">
<meta property="og:title" content="{E(ogt)}">
<meta property="og:description" content="{E(ogd)}">
<meta property="og:url" content="{site}{path}">
<meta property="og:image" content="{site}{img}">
<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630">
<meta name="twitter:image" content="{site}{img}">
<meta property="og:image:alt" content="{E(alt)}">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:site" content="{E(s("footer.x_handle"))}">
<meta name="twitter:title" content="{E(ogt)}">
<meta name="twitter:description" content="{E(ogd)}">
<link rel="canonical" href="{site}{path}">
<link rel="icon" href="/assets/brand/favicon.svg" type="image/svg+xml">
<link rel="apple-touch-icon" href="/assets/brand/apple-touch-icon.png">
<link rel="preload" href="/assets/fonts/dm-sans-latin-var.woff2" as="font" type="font/woff2" crossorigin><link rel="preload" href="/assets/fonts/geist-latin-var.woff2" as="font" type="font/woff2" crossorigin>
<link rel="stylesheet" href="/assets/css/skiff.css">'''


CHAIN_IMG = '/assets/brand/robinhood-chain.svg'
MENU_SVG = '<svg width="18" height="18" viewBox="0 0 18 18" fill="none" aria-hidden="true"><path d="M3 6h12M3 12h12" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg>'
SEARCH_SVG = '<svg width="18" height="18" viewBox="0 0 18 18" fill="none" aria-hidden="true"><circle cx="8" cy="8" r="5.5" stroke="currentColor" stroke-width="1.6"/><path d="m12.2 12.2 3.3 3.3" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg>'
LINKS = [('swap', '/app', 'nav.swap'), ('stocks', '/stocks', 'nav.stocks'), ('token', '/token', 'nav.token'), ('faq', '/faq', 'nav.faq')]
VL = lambda h: ' data-vlink' if h == '/token' else ''   # Vault links: the engine strips them when TOKEN_PAGE is off (server._no_vault)


def header(variant='site', active=''):
    brand = f'<a class="brand" href="/" aria-label="{E(BRAND)}"><img class="bm" src="/assets/brand/mark-skiff.svg" alt="" width="32" height="32"><img class="bw" src="/assets/brand/wordmark-skiff.svg" alt="{E(s("brand.wordmark_alt"))}" width="47" height="22"></a>'
    links = ''.join(f'<a href="{h}"{VL(h)}{" class=\"on\" aria-current=\"page\"" if k == active else ""}>{E(s(i))}</a>' for k, h, i in LINKS)
    connect = f'<button class="qbtn" data-connect>{E(s("nav.connect"))}</button>'
    menu = f'<button class="menu-b" aria-label="{E(s("nav.menu"))}" aria-expanded="false" data-menu>{MENU_SVG}</button>'
    mnav = f'<nav class="mnav" aria-label="{E(s("nav.menu"))}">{"".join(f"<a href=\"{h}\"{VL(h)}>{E(s(i))}</a>" for k, h, i in LINKS)}</nav>'
    skip = f'<a class="skip" href="#swap">{E(s("nav.skip"))}</a>'
    if variant == 'app':
        chip = f'<span class="chain"><img src="{CHAIN_IMG}" alt="" width="22" height="22">{E(s("nav.chain"))}<span class="dot"></span><span class="blk mono" data-blk></span></span>'
        cmd = f'<button class="cmdbar" data-cmdk>{SEARCH_SVG}<span>{E(s("app.cmdbar"))}</span><span class="kbd">{E(s("app.cmdbar_key"))}</span></button>'
        return f'{skip}<header class="nav">{brand}{cmd}<nav class="links alinks" aria-label="Main">{links}</nav><div class="rt">{chip}{connect}{menu}</div>{mnav}</header>'
    return f'{skip}<header class="nav">{brand}<nav class="links" aria-label="Main">{links}</nav><div class="rt">{connect}{menu}</div>{mnav}</header>'


X_SVG = '<svg width="16" height="16" viewBox="0 0 24 24" aria-hidden="true"><path fill="currentColor" d="M18.244 2.25h3.308l-7.227 8.26 8.502 11.24H16.17l-5.214-6.817L4.99 21.75H1.68l7.73-8.835L1.254 2.25H8.08l4.713 6.231zm-1.161 17.52h1.833L7.084 4.126H5.117z"/></svg>'


REACH = {}


def reach_band():
    # P2 (Auros 16b5f8b3 «connected matter»: light nodes on a fine-dot field, the product's own relation): every chain you can pay
    # from, joined by one line into Robinhood Chain. Real marks from the engine's /api/reach at build time; nothing clipped.
    chains = [c for c in (REACH.get('chains') or {}).get('list') or [] if c.get('logo') and 'robinhood' not in (c.get('name') or '').lower()]
    if not chains:
        return ''
    marks = ''.join(f'<img src="{E(c["logo"])}" alt="" title="{E(c.get("name") or "")}" width="28" height="28" loading="lazy">' for c in chains)
    cap = f'<p class="fcap">{E(s("footer.network.caption", chains=len(chains) + 1))}</p>' if 'footer.network.caption' in STR else ''   # P4: the band says what it is (all chains incl. Robinhood Chain)
    return f'<div class="fend"><div class="fendg">{cap}<div class="fnet" aria-hidden="true"><span class="fch">{marks}</span><i class="fl"></i><span class="fhub"><img src="{CHAIN_IMG}" alt="" width="40" height="40"></span></div></div></div>'


def footer():
    # P1 premium (design/premium/SPEC.md §8): brand | Product | Legal | Follow (X mark + handle), one fine row, a monumental wordmark
    col = lambda title, items: f'<div class="fc"><h4>{E(s(title))}</h4><ul>{"".join(f"<li><a href=\"{h}\"{VL(h)}>{E(s(i))}</a></li>" for h, i in items)}</ul></div>'
    xh = s("footer.x_handle")
    follow = '' if "__X_HANDLE__" in xh else f'<div class="fc fx"><h4>{E(s("footer.col.follow"))}</h4><a class="xbtn" href="https://x.com/{E(xh.lstrip("@"))}" rel="noopener" aria-label="{E(s("footer.x"))} {E(xh)}">{X_SVG}<span>{E(xh)}</span></a></div>'
    return f'''<footer class="foot"><div class="wrap"><div class="g">
<div class="fb"><a class="brand" href="/" aria-label="{E(BRAND)}"><img class="bm" src="/assets/brand/mark-skiff.svg" alt="" width="32" height="32"><img class="bw" src="/assets/brand/wordmark-skiff.svg" alt="{E(BRAND)}" width="47" height="22"></a><p class="tg">{E(s("footer.tagline"))}</p><p class="ch"><img src="{CHAIN_IMG}" alt="" width="20" height="20">{E(s("footer.chain"))}</p></div>
{col("footer.col.product", [("/app", "footer.swap"), ("/stocks", "footer.stocks"), ("/token", "footer.token"), ("/faq", "footer.faq")])}
{col("footer.col.legal", [("/legal#disclaimer", "footer.disclaimer"), ("/legal#terms", "footer.terms"), ("/legal#privacy", "footer.privacy")])}
{follow}
</div>{reach_band()}<div class="fine"><p>{E(s("footer.independent"))}</p><p>{E(s("footer.copy"))}</p></div></div></footer>'''


def dock(active='swap', inapp=False):
    ic = {
        'swap': '<path d="M3 6h11l-3-3M15 12H4l3 3" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/>',
        'stocks': '<path d="m2 13 4.5-4.5 3 3L16 5" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/>',
        'trades': '<path d="M3 5h12M3 9h12M3 13h8" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/>',
        'tokens': '<circle cx="9" cy="9" r="6.5" stroke="currentColor" stroke-width="1.6"/><circle cx="9" cy="9" r="2.6" stroke="currentColor" stroke-width="1.6"/>',
        'token': '<rect x="2.5" y="4" width="13" height="10.5" rx="2.5" stroke="currentColor" stroke-width="1.6"/><circle cx="9" cy="9.25" r="2.2" stroke="currentColor" stroke-width="1.6"/><path d="M5 14.5v1.2M13 14.5v1.2" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/>',
        'you': '<circle cx="9" cy="9" r="6.5" stroke="currentColor" stroke-width="1.6"/><path d="M9 5.5V9l2.5 1.5" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/>',
    }
    items = [('swap', '#swap' if inapp else '/app', 'app.dock.swap'), ('stocks', '/stocks', 'app.dock.stocks'), ('token', '/token', 'app.dock.token'), ('you', '#history' if inapp else '/app#history', 'app.dock.history')]
    a = ''.join(f'<a href="{h}"{VL(h)}{f" data-v={chr(34)}{k}{chr(34)}" if inapp and k not in ("stocks", "tokens", "token") else ""}{" class=" + chr(34) + "on" + chr(34) + " aria-current=" + chr(34) + "page" + chr(34) if k == active else ""}><svg width="18" height="18" viewBox="0 0 18 18" fill="none" aria-hidden="true">{ic[k]}</svg><span>{E(s(i))}</span></a>' for k, h, i in items)
    srch = f'<span class="sp"></span><button data-cmdk><span class="kbd">{E(s("app.cmdbar_key"))}</span><svg class="smI" width="18" height="18" viewBox="0 0 18 18" fill="none" aria-hidden="true"><circle cx="8" cy="8" r="5.5" stroke="currentColor" stroke-width="1.6"/><path d="m12.2 12.2 3.3 3.3" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg><span>{E(s("app.dock.search"))}</span></button>'
    return f'<nav class="dock" aria-label="App">{a}{srch}</nav>'


BLOCK = re.compile(r'<!--@(header|head|footer|dock)([^>]*)-->.*?<!--/@-->', re.S)


def attrs(a):
    return dict(re.findall(r'(\w+)="([^"]*)"', a))


def render_blocks(src, ctx):
    def rep(m):
        kind, a = m.group(1), attrs(m.group(2))
        if kind == 'head':
            inner = head(a.get('page', 'home'), a.get('path', '/'), ctx.get('title'), ctx.get('desc'), ctx.get('ogt', ctx.get('title')), ctx.get('ogd', ctx.get('desc')), ctx.get('alt'), ctx.get('img'))
        elif kind == 'header':
            inner = header(a.get('variant', 'site'), a.get('active', ''))
        elif kind == 'dock':
            inner = dock(a.get('active', 'swap'), a.get('app') == '1')
        else:
            inner = footer()
        return f'<!--@{kind}{m.group(2)}-->\n{inner}\n<!--/@-->'
    return BLOCK.sub(rep, src)


DS = re.compile(r'(<(\w+)\b[^>]*?\sdata-s="([^"]+)"[^>]*>)(.*?)(</\2>)', re.S)


def snip(key):
    kind, val = key.split(':', 1)
    if kind == 'n':
        if val == 'tcount':
            return str(CTX.get('tcount', ''))
        if val == 'sources':   # P8: the hero H1 «{sources}+ sources» — floor to ten, as the page does (23 → 20)
            n = ((REACH.get('sources') or {}).get('count') or 20)
            return str(n // 10 * 10 if n >= 20 else n)
        return str(max(0, CTX.get('count', 64) - 3)) if val == 'stockcount' else str(CTX.get('count', 64))
    if kind == 'tk' and val == 'skel':
        return '<span class="tk"><span class="stk tksk"></span><span class="tkt">&#8203;</span></span>'
    if kind == 'tk':
        return f'<span class="tk"><span class="stk"><img src="/assets/logos/stocks/{val}.png" alt="" width="34" height="34"></span>{val}</span>'
    return ''


def fill_one(m):
    open_tag, id_ = m.group(1), m.group(3)
    sv = re.search(r'\sdata-sv="([^"]+)"', open_tag)
    if not sv:
        return open_tag + E(s(id_)) + m.group(5)
    txt = E(s(id_))
    for pair in sv.group(1).split(';'):
        k, key = pair.split('=', 1)
        txt = txt.replace('{' + k + '}', snip(key))
    if id_ == 'landing.hero.title_static':   # P8: each sentence keeps together; the H1 breaks only between sentences
        txt = ' '.join(f'<span class="hs">{x}</span>' for x in re.split(r'(?<=\.)\s+', txt))
    return open_tag + txt + m.group(5)
DSA = re.compile(r'(<[^>]*?\sdata-sa="([^"]+)"[^>]*>)', re.S)


def fill(src):
    src = DS.sub(fill_one, src)

    def fa(m):
        tag = m.group(1)
        for pair in m.group(2).split(';'):
            at, id_ = pair.split(':', 1)
            val = E(s(id_))
            if re.search(rf'\s{at}="[^"]*"', tag):
                tag = re.sub(rf'(\s{at}=")[^"]*(")', lambda x: x.group(1) + val + x.group(2), tag, count=1)
            else:
                tag = tag[:-1] + f' {at}="{val}">'
        return tag
    return DSA.sub(fa, src)


def stamp(src, v):
    src = re.sub(r'(/assets/(?:css|js)/[\w./-]+\.(?:css|js))(\?v=\w+)?', rf'\1?v={v}', src)
    return src


def main():
    import subprocess, tempfile
    # strings
    shutil.copy(STRINGS, os.path.join(WEB, 'strings.json'))
    filled = {k: (v.replace('{brand}', BRAND) if k != 'brand.name' else v) for k, v in STR.items()}
    tmp = tempfile.mkdtemp(prefix='skiffbuild-')
    os.makedirs(os.path.join(tmp, 'js'))
    open(os.path.join(tmp, 'js', 'strings.js'), 'w').write('export default ' + json.dumps(filled, ensure_ascii=False, separators=(',', ':')) + ';\n')
    # version = hash of every source that ends up in the page graph
    h = hashlib.sha1()
    for base in (SRC, SIGN):
        for d, _, fs in sorted(os.walk(base)):
            if '/test' in d or 'node_modules' in d:
                continue
            for f in sorted(fs):
                if f.endswith(('.js', '.css', '.html')):
                    h.update(open(os.path.join(d, f), 'rb').read())
    h.update(json.dumps(filled, sort_keys=True).encode())
    v = h.hexdigest()[:10]
    # js + css: minified per file (comments, internal notes and source-map links gone)
    sign_tmp = os.path.join(tmp, 'sign')
    os.makedirs(sign_tmp)
    for f in os.listdir(SIGN):
        if f.endswith('.js'):
            shutil.copy(os.path.join(SIGN, f), sign_tmp)
    if os.path.isdir(os.path.join(SIGN, 'vendor')):
        shutil.copytree(os.path.join(SIGN, 'vendor'), os.path.join(sign_tmp, 'vendor'), ignore=shutil.ignore_patterns('*.md', 'package.json', '*.map'))
    jsdst = os.path.join(WEB, 'assets', 'js')
    shutil.rmtree(jsdst, ignore_errors=True)
    os.makedirs(jsdst)
    subprocess.run([NODE, os.path.join(os.path.dirname(__file__), 'minify.mjs'), v, f"{os.path.join(SRC, 'js')}:{jsdst}", f"{os.path.join(tmp, 'js')}:{jsdst}",
                    f"{sign_tmp}:{os.path.join(jsdst, 'sign')}", f"{os.path.join(SRC, 'css')}:{os.path.join(WEB, 'assets', 'css')}"], check=True)
    shutil.rmtree(tmp, ignore_errors=True)
    # stock list (engine) for counts and per-ticker pages
    toks = []
    try:
        toks = json.load(urllib.request.urlopen(ENGINE + '/api/tokens', timeout=5))['tokens']
    except Exception as e:
        print('engine not reachable for /api/tokens:', e, file=sys.stderr)
    try:
        board_stocks = {r['stock'] for r in json.load(urllib.request.urlopen(ENGINE + '/api/board', timeout=5))['rows']}
    except Exception:
        board_stocks = set()
    stocks = [x for x in toks if x.get('c') == 4663 and x.get('k') in ('stock', 'etf')]
    count = len(stocks)
    # /tokens: rows the page will show (ours first + live network tokens) — the engine's list, else the shipped snapshot
    try:
        nt = json.load(urllib.request.urlopen(ENGINE + '/api/network-tokens', timeout=5))
    except Exception:
        try:
            nt = json.load(open(os.path.join(ROOT, 'engine', 'network-tokens-snapshot.json')))
        except Exception:
            nt = {'listed': [], 'tokens': []}
    tcount = len(nt.get('listed') or []) + len(nt.get('tokens') or [])
    try:
        REACH.update(json.load(urllib.request.urlopen(ENGINE + '/api/reach', timeout=5)))
    except Exception as e:
        print('engine not reachable for /api/reach (footer band skipped):', e, file=sys.stderr)
    CTX['tcount'] = tcount
    CTX['count'] = count

    def emit(src_html, ctx, out):
        page = stamp(fill(render_blocks(src_html, ctx)), v)
        page = re.sub(r'<!--@(?:header|head|footer|dock)[^>]*-->\n?|<!--/@-->\n?', '', page)
        page = re.sub(r'<!--design:.*?-->\n?', '', page, flags=re.S)   # the designer's Refero ledger at the top of a page source never ships
        if ctx.get('faq_usdg'):   # prod-1 m5: «What is USDG?» — the ticker with its icon
            q0 = E(s('faq.q.usdg'))
            page = page.replace('data-s="faq.q.usdg">' + q0, 'data-s="faq.q.usdg">' + q0.replace('USDG', '<span class="tk"><img src="/assets/logos/tokens/usdg.png" alt="" width="22" height="22">USDG</span>', 1), 1)
        # landing stocks / tokens zones: six fixed row slots from the first frame (filled by landing.js; nothing moves)
        page = page.replace('{lskel}', ''.join('<span class="hlogo sk0"><span class="sk"></span></span>' for _ in range(10)))   # hero logo row: ten fixed slots
        page = page.replace('{zskel}', ''.join('<div class="zr sk0"><span class="sk zi"></span><span class="sk" style="width:46%;height:14px"></span><span class="sk" style="width:64px;height:14px;margin-left:auto"></span></div>' for _ in range(4)))   # P2: four rows in each market panel (the landing lists tokens on our list only)
        page = page.replace('{count}', str(count)).replace('{stockcount}', str(max(0, count - 3))).replace('{tcount}', str(tcount))
        css = open(os.path.join(WEB, 'assets', 'css', 'skiff.css')).read()
        page = re.sub(r'<link rel="stylesheet" href="/assets/css/skiff\.css\?v=\w+">', lambda m_: '<style>' + css + '</style>', page)
        pre = MODPRE.get(os.path.basename(out).replace('.html', ''), MODPRE['_'])
        if os.environ.get('MODPRELOAD'):
            page = page.replace('</head>', ''.join(f'<link rel="modulepreload" href="/assets/js/{x}.js?v={v}">' for x in pre) + '</head>', 1)
        os.makedirs(os.path.dirname(out), exist_ok=True)
        open(out, 'w').write(page)
    pages = [f for f in os.listdir(os.path.join(SRC, 'pages')) if f.endswith('.html')]
    for f in pages:
        if f == 'tokens.html':   # P12: the page is gone; /tokens is a 301 to /app on the engine
            continue
        src = open(os.path.join(SRC, 'pages', f)).read()
        ctx = {}
        if f == 'stocks.html':
            ctx = {'title': s('meta.stocks.title'), 'desc': s('meta.stocks.description', count=count)}
        if f == 'faq.html':
            ctx = {'faq_usdg': True}
        if f == 'token.html':   # Vault: its own preview card (design/og-token/og-token.html → assets/brand/og-token.png)
            ctx = {'img': '/assets/brand/og-token.png'}
        if f == 'tokens.html':
            ctx = {'title': s('meta.tokens.title'), 'desc': s('meta.tokens.description', count=tcount), 'ogt': s('meta.tokens.og_title'), 'ogd': s('meta.tokens.og_description'), 'alt': s('meta.tokens.og_alt')}
        emit(src, ctx, os.path.join(WEB, f))
    app = open(os.path.join(SRC, 'pages', 'app.html')).read()
    emit(app.replace('<!--@head page="app" path="/app"-->', '<!--@head page="swap" path="/swap"-->'), {}, os.path.join(WEB, 'swap.html'))
    tpl = open(os.path.join(SRC, 'pages', 'stock', 'index.html')).read()
    shutil.rmtree(os.path.join(WEB, 'stock'), ignore_errors=True)
    emit(tpl, {'title': s('meta.stocks.title'), 'desc': s('meta.stocks.description', count=count)}, os.path.join(WEB, 'stock', 'index.html'))
    for x in stocks:
        tk, nm = x['s'], x.get('n') or x['s']
        page = tpl.replace('<!--@head page="stock" path="/stock"-->', f'<!--@head page="stock" path="/stock/{tk}"-->')
        page = page.replace('data-ticker=""', f'data-ticker="{E(tk)}"' + (' data-hasboard="1"' if tk in board_stocks else ''))
        _t = s("stocks.detail.title", name=nm)   # P8: the length sets the H1 size (two balanced lines at most, no lone word)
        page = page.replace('<h1 id="sname">&nbsp;</h1>', f'<h1 id="sname" style="--nc:{len(_t)}">{E(_t)}</h1>')
        page = page.replace('<p class="tk2" id="stk">&nbsp;</p>', f'<p class="tk2" id="stk">{E(s("stocks.detail.ticker_etf" if x.get("k") == "etf" else "stocks.detail.ticker", ticker=tk))}</p>')
        if x.get('l'):
            page = page.replace('<span class="stk" id="slogo"></span>', f'<span class="stk" id="slogo"><img src="{E(x["l"])}" alt="" width="48" height="48"></span>')
        emit(page, {'title': s('meta.stock.title', ticker=tk), 'desc': s('meta.stock.description', ticker=tk, name=nm), 'alt': s('meta.stock.og_alt', ticker=tk)}, os.path.join(WEB, 'stock', f'{tk}.html'))
    # the Privy bundle (built in privy/dist; loaded on demand by the wallet layer)
    pv = os.path.join(ROOT, 'privy', 'dist', 'privy.js')
    if os.path.isfile(pv):
        os.makedirs(os.path.join(WEB, 'assets', 'vendor'), exist_ok=True)
        shutil.copy(pv, os.path.join(WEB, 'assets', 'vendor', 'privy.js'))
    else:
        print('no privy/dist/privy.js — the wallet window keeps the old bundle', file=sys.stderr)
    # brand + other static assets kept with the sources (v2: Skiff mark, wordmark, favicon, og)
    if os.path.isdir(os.path.join(SRC, 'assets')):
        shutil.copytree(os.path.join(SRC, 'assets'), os.path.join(WEB, 'assets'), dirs_exist_ok=True)
    # long-cache: brand files and the Privy bundle get ?v=<hash of their bytes> wherever pages or scripts name them (engine v4: no ?v= → no-cache)
    hb = hashlib.sha1()
    for d0 in (os.path.join(WEB, 'assets', 'brand'), os.path.join(WEB, 'assets', 'vendor')):
        for f0 in sorted(os.listdir(d0)) if os.path.isdir(d0) else []:
            hb.update(f0.encode()); hb.update(open(os.path.join(d0, f0), 'rb').read())
    bv = hb.hexdigest()[:10]
    ASSET = re.compile(r'(/assets/(?:brand/[\w.-]+\.(?:svg|png)|vendor/privy\.js))(\?v=\w+)?')
    for d0, _, fs0 in os.walk(WEB):
        for f0 in fs0:
            if not f0.endswith(('.html', '.js')) or '/vendor' in d0:
                continue
            p0 = os.path.join(d0, f0); txt0 = open(p0, encoding='utf-8', errors='ignore').read()
            new0 = ASSET.sub(lambda m0: m0.group(1) + '?v=' + bv, txt0)
            if new0 != txt0:
                open(p0, 'w', encoding='utf-8').write(new0)
    # guard: no hidden name (HIDDEN_NAMES), no build markers in anything served
    bad = []
    for d, _, fs in os.walk(WEB):
        for f in fs:
            p = os.path.join(d, f)
            if not f.endswith(('.html', '.js', '.css', '.json', '.svg')):
                continue
            txt = open(p, errors='ignore').read()
            if (HIDDEN and (HIDDEN.search(txt) or HIDDEN.search(f))) or '<!--@' in txt:
                bad.append(os.path.relpath(p, WEB))
    print(f'built v={v}: {len(pages)} pages + swap + {count} stock pages; guard hits: {bad or "none"}')
    if bad:
        sys.exit(2)


if __name__ == '__main__':
    main()
