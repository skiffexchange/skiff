// /app v2 (design/v2/SPEC.md §7): the swap window and one stage next to it — idle: the latest big buy of the token drawn as a live
// route scheme; typing: the source race; after Swap: the live scheme of your own swap. Below: big trades, watchlist, history.
import { t, th, has, esc, $, $$, api, tokens, tokI, nf, usd, usdShort, hhmm, blk, amt, ago, short, kfmt, chainLogo, vlogo, errLine, toast, plural, isStock, sheet, X_SVG, LOCAL, QS, RM } from './core.js';
import { tk, stamp } from './board.js';
import { mountStage, tradeRow } from './stage.js';
import { bindHeader, restore, onWallet, state as W, connect, saveHint } from './wallet.js';
import { chrome } from './chrome.js';
import { pick } from './picker.js';

let sw;
chrome((side, x) => (side === 'to' ? sw.setTo(x) : sw.setFrom(x)));
bindHeader(); restore();
sw = mountStage({});

/* ---------- big trades (polled; new rows light up) ---------- */
const TR = { stock: null, min: 1000, seen: new Set(), first: true, sort: 'size', big: null, bigAt: 0, bigKey: '', rows: null, empty: '', all: false };
/** «Largest first»: the 24 h feed of /api/trades read with raising size floors (fewest pages), sorted on the client */
async function biggest() {
  const key = (TR.stock || '') + ':' + TR.min;
  if (TR.big && TR.bigKey === key && Date.now() - TR.bigAt < 60000) return TR.big;
  const since = Date.now() / 1000 - 86400; let out = [], backfilled = true;
  for (const floor of [25000, 10000, 5000, 1000]) {
    out = []; let before = null;
    for (let page = 0; page < 10; page++) {
      const q = new URLSearchParams({ limit: 200, minUsd: Math.max(floor, TR.min) });
      if (TR.stock) q.set('stock', TR.stock); if (before) q.set('before', before);
      const d = await api('/api/trades?' + q); const ts = d.trades || [];
      if (d.backfilled === false) backfilled = false;
      out.push(...ts.filter((x) => x.ts >= since));
      if (ts.length < 200 || ts[ts.length - 1].ts < since) break;
      before = ts[ts.length - 1].ts;
    }
    if (out.length >= 12 || floor <= TR.min) break;
  }
  TR.big = { trades: out.sort((a, b) => b.usd - a.usd).slice(0, 12), backfilled }; TR.bigAt = Date.now(); TR.bigKey = key;
  return TR.big;
}
async function trades() {
  try {
    const q = new URLSearchParams({ limit: 12 }); if (TR.stock) q.set('stock', TR.stock); if (TR.min > 1000) q.set('minUsd', TR.min);
    const d = TR.sort === 'size' ? await biggest() : await api('/api/trades?' + q);
    const T = await tokens().catch(() => null);
    const rows = d.trades || [];
    const html = rows.map((x) => {
      const isNew = !TR.first && !TR.seen.has(x.tx + x.stock);
      const tok = T && T.find(4663, x.stock);
      return tradeRow(x, tok && { ...tok }, isNew);
    });
    rows.forEach((x) => TR.seen.add(x.tx + x.stock)); TR.first = false;
    TR.rows = html; TR.empty = `<p class="empty">${esc(d.backfilled === false ? t('empty.trades_loading') : TR.stock ? t('empty.trades_stock', { ticker: TR.stock }) : t('empty.trades'))}</p>`;
    paintTrades();
  } catch (e) {
    // P11: a busy server (429) or a dropped request on the first load — two quick retries before the error line (the 8 s refresh comes later)
    if (!TR.rows && (TR.retry = (TR.retry || 0) + 1) <= 2) { setTimeout(trades, 1200 * TR.retry); return; }
    if (!$('#trl').children.length) $('#trl').innerHTML = `<p class="empty">${esc(errLine(e))}</p>`;
  }
}
/**: SHOW rows, the rest behind one text button (no refetch on toggle) */
const SHOW = 6;
function paintTrades() {
  const rows = TR.rows || [], hid = rows.length - SHOW;
  $('#trl').innerHTML = rows.length ? (TR.all || hid <= 0 ? rows : rows.slice(0, SHOW)).join('') +
    (hid > 0 ? `<button type="button" class="trmore" aria-expanded="${TR.all ? 'true' : 'false'}">${esc(TR.all ? t('app.trades.less') : t('app.trades.more', { n: hid }))}</button>` : '') : TR.empty;
  const b = $('#trl .trmore');
  if (b) b.onclick = () => { TR.all = !TR.all; paintTrades(); if (!TR.all) $('#trades').scrollIntoView({ block: 'nearest' }); };
}
/* P8: the stock chip follows «You get» — a new stock there takes the chip at once (label and logo); when the chip is the active filter the
   list re-filters to it; a non-stock «You get» leaves the chip on the last stock. The chip is a filter only: it never changes the form
   (as on /stock/<ticker>, where the page's stock drives both and the table never drives the form). */
document.addEventListener('skiff-to', (e) => {
  const x = e.detail; if (!x || !isStock(x)) return;
  const prev = TR.chip; if (prev && prev.symbol === x.symbol) return;
  TR.chip = x;
  if (TR.stock && prev && TR.stock === prev.symbol) { TR.stock = x.symbol; TR.first = true; TR.seen.clear(); TR.all = false; trFilters(); trades(); } else trFilters();
});
function trFilters() {
  if (!TR.chip && sw && sw.state.to && isStock(sw.state.to)) TR.chip = sw.state.to;
  const to = TR.chip || null;
  const f = $('#trf');
  f.innerHTML = `<button data-s="" class="${!TR.stock ? 'on' : ''}">${t('app.trades.filter_all')}</button>${to && isStock(to) ? `<button data-s="${esc(to.symbol)}" class="${TR.stock === to.symbol ? 'on' : ''}">${tokI(to, 'ic')}${esc(to.symbol)}</button>` : ''}<button data-m="10000" class="${TR.min === 10000 ? 'on' : ''}">${esc(t('app.trades.filter_min', { usd: '10,000' }))}</button><i class="brk" aria-hidden="true"></i><button data-o="size" class="${TR.sort === 'size' ? 'on' : ''}">${esc(t('app.trades.sort_size'))}</button><button data-o="time" class="${TR.sort === 'time' ? 'on' : ''}">${esc(t('app.trades.sort_time'))}</button>`;
  f.querySelectorAll('[data-o]').forEach((b) => b.addEventListener('click', () => { TR.sort = b.dataset.o; TR.first = true; TR.seen.clear(); trFilters(); trades(); }));
  f.querySelectorAll('[data-s]').forEach((b) => b.addEventListener('click', () => { TR.stock = b.dataset.s || null; TR.first = true; TR.seen.clear(); trFilters(); trades(); }));
  f.querySelectorAll('[data-m]').forEach((b) => b.addEventListener('click', () => { TR.min = TR.min === 10000 ? 1000 : 10000; TR.first = true; TR.seen.clear(); trFilters(); trades(); }));
}
setTimeout(trFilters, 300); setTimeout(() => { if (!TR.chip) trFilters(); }, 1500);   // P8: a slow swap mount still gets its stock chip
trades(); setInterval(() => { if (!document.hidden) trades(); }, 8000);

async function chainHead() { let ok = false; try { const d = await api('/api/trades?limit=1'); ok = !!d.scannedTo && !d.stale; if (d.scannedTo) $$('[data-blk]').forEach((e) => { e.textContent = t('nav.chain_block', { block: blk(d.scannedTo) }); const pill = e.closest('.chain,.chip2') || e; pill.title = t('nav.chain_tip', { block: blk(d.scannedTo) }); /* P3: the live block, shown */  }); } catch { /* keep */ } $$('[data-blk]').forEach((e) => { const pill = e.closest('.chain,.chip2'); if (pill) pill.classList.toggle('down', !ok); }); }
chainHead(); setInterval(() => { if (!document.hidden) chainHead(); }, 15000);

/* ---------- you: watchlist, alerts, history (Telegram: off until the token launch, — TG stays false; the engine 404s it too) ---------- */
const TG = false;
const ALERTS = false;   // big-trade alerts off since 16.11; the engine 404s /api/me/alerts*
let ytab = 'watchlist';
const ybox = $('#ybox');
$$('[data-y]').forEach((b) => b.addEventListener('click', () => { ytab = b.dataset.y; you(); }));
onWallet(() => you());
async function you() {
  $$('[data-y]').forEach((b) => { b.classList.toggle('on', b.dataset.y === ytab); b.setAttribute('aria-selected', b.dataset.y === ytab); b.tabIndex = b.dataset.y === ytab ? 0 : -1; });
  if (W.account && !W.canSave) {
    ybox.innerHTML = `<p class="l">${esc(t(ytab === 'alerts' ? 'app.save.alerts' : ytab === 'history' ? 'app.save.history' : ytab === 'telegram' ? 'app.save.alerts' : 'app.save.watch'))}</p><button class="btn2 pr" data-c>${t('app.save.cta')}</button><p class="l" style="margin-top:10px">${esc(t('app.save.note'))}</p>`;
    ybox.querySelector('[data-c]').onclick = connect; return;
  }
  if (!W.account) {
    ybox.innerHTML = `<h3 style="font:500 16px/1.3 var(--f)">${t('app.signin.title')}</h3><p class="l" style="margin-top:8px">${t('app.signin.body')}</p><button class="btn2 pr" data-c>${t('app.signin.cta')}</button>`;
    ybox.querySelector('[data-c]').onclick = connect; return;
  }
  ybox.innerHTML = `<span class="sk" style="height:14px;width:60%"></span>`;
  try { await ({ watchlist, alerts, history, telegram })[ytab](); } catch (e) { ybox.innerHTML = `<p class="l">${esc(errLine(e))}</p>`; }
}
async function watchlist() {
  const [w, T] = await Promise.all([api('/api/me/watchlist'), tokens()]);
  const list = (w.tickers || []).map((s) => T.find(4663, s)).filter(Boolean);
  ybox.innerHTML = `<div class="row2" style="justify-content:space-between;margin-bottom:6px"><p class="l" style="margin:0">${t('app.watch.limit')}</p><button class="btn2" data-add>${t('app.watch.add')}</button></div>`
    + (list.length ? list.map((x) => `<div class="lrow">${tokI(x, 'stk')}<span><b>${esc(x.symbol)}</b><small>${esc(x.name)}</small></span><span>${x.price != null ? '$' + nf(x.price, 2) : ''}</span><span class="row2"><button class="btn2" data-buy="${esc(x.symbol)}">${t('app.watch.buy')}</button><button class="x" data-rm="${esc(x.symbol)}">${t('app.watch.remove')}</button></span></div>`).join('') : `<p class="empty">${t('empty.watch')}</p>`);
  const save = async (tickers) => { await api('/api/me/watchlist', { method: 'PUT', body: { tickers } }); toast(t('app.watch.saved')); watchlist(); };
  ybox.querySelector('[data-add]').onclick = async () => { const x = await pick({ side: 'get' }); if (x && isStock(x)) save([...new Set([...(w.tickers || []), x.symbol])]).catch((e) => toast(errLine(e))); };
  ybox.querySelectorAll('[data-rm]').forEach((b) => (b.onclick = () => save((w.tickers || []).filter((s) => s !== b.dataset.rm)).catch((e) => toast(errLine(e)))));
  ybox.querySelectorAll('[data-buy]').forEach((b) => (b.onclick = () => { const x = T.find(4663, b.dataset.buy); sw.setTo(x); scrollTo({ top: 0, behavior: 'smooth' }); setView('swap'); }));
}
async function alerts() {
  const [a, f, tg, T] = await Promise.all([api('/api/me/alerts'), api('/api/me/alerts/feed').catch(() => ({ hits: [] })), TG ? api('/api/me/telegram').catch(() => ({ linked: false })) : { linked: false }, tokens()]);
  let tick = (sw.state.to && isStock(sw.state.to)) ? sw.state.to : T.find(4663, 'NVDA');
  ybox.innerHTML = `<p class="l">${t('app.alerts.lede')}</p>
    <div class="row2" style="margin-bottom:10px"><button class="tok" data-tk style="height:40px"></button><input class="inp" data-min inputmode="numeric" style="width:160px" placeholder="${esc(t('app.alerts.min'))}" aria-label="${esc(t('app.alerts.min'))}" value="10,000">
    ${TG ? `<label class="row2" style="font:400 14px/1 var(--f);color:var(--silver)"><input type="checkbox" data-tg ${tg.linked ? 'checked' : 'disabled'}> ${t(tg.linked ? 'app.alerts.tg' : 'app.alerts.tg_off')}</label>` : ''}<button class="btn2 pr" data-save>${t('app.alerts.new')}</button></div><p class="l" data-msg style="min-height:20px;margin:0 0 6px"></p>
    ${(a.alerts || []).length ? a.alerts.map((x) => { const tt = T.find(4663, x.ticker); return `<div class="lrow">${tokI(tt || { symbol: x.ticker, kind: 'stock' }, 'stk')}<span><b>${esc(t('app.alerts.row', { ticker: x.ticker, min: nf(x.minUsd, 0) }))}</b>${TG && x.telegram ? `<small>${t('app.alerts.row_tg')}</small>` : ''}</span><span></span><button class="x" data-del="${x.id}">${t('common.delete')}</button></div>`; }).join('') : `<p class="empty">${t('empty.alerts')}</p>`}
    <h3 style="font:500 15px/1 var(--f);margin:20px 0 6px">${t('app.alerts.feed')}</h3>
    ${(f.hits || []).length ? f.hits.slice(0, 20).map((h) => `<div class="lrow">${tokI(T.find(4663, h.ticker) || { symbol: h.ticker, kind: 'stock' }, 'stk')}<span><b>${esc(t('app.alerts.hit', { ticker: h.ticker, side: t('common.' + h.side), usd: nf(h.usd, 0), time: hhmm(h.ts) }))}</b>${TG && h.telegramSent ? `<small>${t('app.alerts.hit_tg')}</small>` : ''}</span><span></span><span></span></div>`).join('') : `<p class="empty">${t('empty.alerts_feed')}</p>`}
    <p class="l" style="margin-top:12px">${t('app.alerts.limit')}</p>`;
  const tb = ybox.querySelector('[data-tk]'); const paintTk = () => (tb.innerHTML = `${tokI(tick, 'ic')}${esc(tick.symbol)}<span class="chev">▾</span>`); paintTk();
  tb.onclick = async () => { const x = await pick({ side: 'get' }); if (x && isStock(x)) { tick = x; paintTk(); } };
  const msg = ybox.querySelector('[data-msg]');
  ybox.querySelector('[data-save]').onclick = async () => {
    const v = +String(ybox.querySelector('[data-min]').value).replace(/[^\d.]/g, '');
    if (!(v >= 1000)) { msg.textContent = t('app.alerts.min_floor'); return; }
    if (v > 10000000) { msg.textContent = t('app.alerts.min_cap'); return; }
    try { await api('/api/me/alerts', { method: 'POST', body: { ticker: tick.symbol, minUsd: v, telegram: !!(TG && ybox.querySelector('[data-tg]')?.checked) } }); toast(t('app.alerts.saved')); alerts(); } catch (e) { msg.textContent = errLine(e); }
  };
  ybox.querySelectorAll('[data-del]').forEach((b) => (b.onclick = async () => { try { await api('/api/me/alerts/' + b.dataset.del, { method: 'DELETE' }); toast(t('app.alerts.deleted')); alerts(); } catch (e) { toast(errLine(e)); } }));
}
async function history() {
  const [h, T] = await Promise.all([api('/api/me/history'), tokens()]);
  const sym = (m) => { const x = T.list.find((y) => y.address === (m.token || '').toLowerCase()); return x || { symbol: m.symbol, kind: 'crypto' }; };
  ybox.innerHTML = `<p class="l">${t('app.history.lede')}</p>
    ${(h.swaps || []).length ? h.swaps.map((s) => { const o = (s.moves || []).find((m) => m.direction === 'out'), i = (s.moves || []).find((m) => m.direction === 'in'); const st = s.status === 'DONE' || s.status === 'done' ? 'done' : s.status === 'FAILED' || s.status === 'failed' ? 'failed' : 'pending';
      return `<div class="lrow">${i ? tokI(sym(i), 'stk') : '<span class="stk" style="width:32px;height:32px"></span>'}<span><b>${esc(t('app.history.row', { paid: o ? amt(o.amount, o.decimals, 2) + ' ' + o.symbol : '—', got: i ? amt(i.amount, i.decimals, 4) + ' ' + i.symbol : '—' }))}</b><small>${esc(hhmm(s.ts) + ' UTC · ' + t('common.block_short', { block: blk(s.block || 0) }))}</small></span><span class="st-${st[0]}">${t('app.history.status.' + st)}</span><a class="x" href="https://robinhoodchain.blockscout.com/tx/${esc(s.tx)}" target="_blank" rel="noopener">↗</a></div>`; }).join('')
      : `<p class="empty">${t('empty.history')}<br><a href="#swap" style="color:var(--mint)" data-first>${t('empty.history_cta')}</a></p>`}
    <div class="row2" style="margin-top:14px"><input class="inp" data-h placeholder="${esc(t('app.history.add_ph'))}" style="flex:1;min-width:200px" spellcheck="false"><button class="btn2" data-add>${t('app.history.add')}</button></div><p class="l" data-msg style="margin-top:6px"></p>`;
  const f = ybox.querySelector('[data-first]'); if (f) f.onclick = () => setView('swap');
  ybox.querySelector('[data-add]').onclick = async () => {
    const v = ybox.querySelector('[data-h]').value.trim();
    try { await api('/api/me/history', { method: 'POST', body: { tx: v, chainId: 4663 } }); history(); } catch (e) { ybox.querySelector('[data-msg]').textContent = e.status === 400 ? t('app.history.add_bad') : errLine(e); }
  };
}
let tgPoll = null;
async function telegram() {
  clearInterval(tgPoll);
  const s = await api('/api/me/telegram');
  if (s.linked) {
    ybox.innerHTML = `<p class="l">${t('app.tg.lede')}</p><div class="lrow" style="grid-template-columns:1fr auto"><span><b>${esc(t('app.tg.linked', { date: s.linkedAt ? new Date(s.linkedAt * 1000).toUTCString().slice(5, 16) : '' }))}</b><small>@${esc(s.bot || '')}</small></span><button class="x" data-un>${t('app.tg.unlink')}</button></div>`;
    ybox.querySelector('[data-un]').onclick = async () => { await api('/api/me/telegram', { method: 'DELETE' }); toast(t('app.tg.unlinked')); telegram(); };
    return;
  }
  ybox.innerHTML = `<p class="l">${t('app.tg.lede')}</p><button class="btn2 pr" data-link>${t('app.tg.link')}</button><div data-w style="margin-top:12px"></div>`;
  ybox.querySelector('[data-link]').onclick = async () => {
    try {
      const l = await api('/api/me/telegram/link', { method: 'POST' });
      ybox.querySelector('[data-w]').innerHTML = `<a class="btn2" href="${esc(l.url)}" target="_blank" rel="noopener">${esc(t('app.tg.open', { bot: l.bot }))} ↗</a><p class="l" style="margin-top:10px">${t('app.tg.wait')}</p>`;
      const t0 = Date.now();
      tgPoll = setInterval(async () => { if (Date.now() - t0 > 600000) return clearInterval(tgPoll); try { const x = await api('/api/me/telegram'); if (x.linked) { clearInterval(tgPoll); telegram(); } } catch { /* keep polling */ } }, 4000);
    } catch (e) { toast(errLine(e)); }
  };
}

/* ---------- views (phone dock) and deep links ---------- */
function setView(v) {
  document.body.dataset.view = v;
  // the header marks Swap on /app (Big trades left the menu 18.11, owner)
  document.querySelectorAll('.nav .links a').forEach((a) => { const h = a.getAttribute('href'); const on = h === '/app'; a.classList.toggle('on', on); if (on) a.setAttribute('aria-current', 'page'); else a.removeAttribute('aria-current'); });
  $$('.dock [data-v]').forEach((a) => a.classList.toggle('on', a.dataset.v === v));
}
function route() {
  const h = location.hash.slice(1);
  if (h === 'trades') { setView('trades'); if (innerWidth > 760) $('#trades').scrollIntoView({ behavior: 'smooth' }); }
  else if (['history', 'watchlist', ...(ALERTS ? ['alerts'] : []), ...(TG ? ['telegram'] : [])].includes(h)) { ytab = h; setView('you'); you(); $('#you').scrollIntoView({ behavior: 'smooth' }); }
  else setView('swap');
}
$$('.dock [data-v]').forEach((a) => a.addEventListener('click', (e) => { if (a.dataset.v === 'swap') { e.preventDefault(); history_replace(''); setView('swap'); scrollTo({ top: 0, behavior: 'smooth' }); } }));
function history_replace(h) { try { window.history.replaceState(null, '', location.pathname + location.search + (h ? '#' + h : '')); } catch { /* sandbox */ } }
addEventListener('hashchange', route); route();
void th; void usd; void ago; void short; void kfmt; void vlogo; void sheet; void X_SVG; void stamp;
