// /stocks: every stock and fund token on Robinhood Chain — instant search; per row the one price, a 24 h step chart, 24 h change, pool
// volume, liquidity, pools and venues, big buys ($5k+, 24 h) — all from one /api/stocks-overview call; «—» for anything the engine has not got.
import { t, has, esc, $, $$, api, tokens, tokI, nf, hhmm } from './core.js';
import { bindHeader, restore } from './wallet.js';
import { chrome } from './chrome.js';
import { search } from './picker.js';
import { dash, money, pct, spark as spk } from './mkt.js';

chrome(); bindHeader(); restore();
let f = 'all', q = '', list = [];
const OV = {};
// P9: sort by a header (aria-sort); nulls always last; default — pool volume, high to low (rows with volume, then priced, then the rest)
let sortK = 'vol', sortDir = -1;
const val = {
  name: (x) => x.symbol, price: (x) => x.price, chg: (x) => (OV[x.symbol] || {}).change24hPct, vol: (x) => (OV[x.symbol] || {}).volume24hUsd,
  liq: (x) => (OV[x.symbol] || {}).liqUsd, pools: (x) => (OV[x.symbol] || {}).pools || null, venues: (x) => (OV[x.symbol] || {}).venues || null, bb: (x) => (OV[x.symbol] || {}).bigBuys24h,
};
const cmp = (a, b) => {
  const va = val[sortK](a), vb = val[sortK](b);
  if (va == null && vb == null) return (b.price != null) - (a.price != null) || a.symbol.localeCompare(b.symbol);
  if (va == null) return 1; if (vb == null) return -1;
  if (sortK === 'name') return sortDir * String(va).localeCompare(String(vb));
  return sortDir * (va - vb) || a.symbol.localeCompare(b.symbol);
};
const poolsV = (o) => (!o.pools ? dash : o.venues && has('stocks.cols.pools_value') ? esc(t('stocks.cols.pools_value', { pools: nf(o.pools, 0), venues: nf(o.venues, 0) })) : nf(o.pools, 0));
function draw() {
  let rows = list.filter((x) => f === 'all' || x.kind === f);
  rows = q ? search(rows, q) : rows.sort(cmp);
  $('#rows').style.minHeight = '';
  $('#rows').innerHTML = rows.length ? rows.map((x) => { const o = OV[x.symbol] || {};
    return `<a class="r" href="/stock/${encodeURIComponent(x.symbol)}"><span class="nmc">${tokI(x, 'stk')}<span><b>${esc(x.symbol)}</b><small>${esc(x.name)}</small></span></span>${spk(o, OV.__from, OV.__to)}<span class="num">${x.price != null ? '$' + nf(x.price, 2) : dash}</span><span class="num chg">${pct(o.change24hPct)}</span><span class="num c-vol">${money(o.volume24hUsd)}</span><span class="num c-liq">${money(o.liqUsd)}</span><span class="num c-pl">${poolsV(o)}</span><span class="num c-bb">${o.bigBuys24h != null ? nf(o.bigBuys24h, 0) : dash}</span><span class="buy c-buy" data-buy="${esc(x.symbol)}">${t('stocks.row.buy')}</span></a>`; }).join('')
    : `<div class="empty"><b>${esc(t('empty.search', { q }))}</b>${esc(t('swap.pick.none_body'))}</div>`;
}
// column labels: the marketer's keys, else an existing label (never a made-up word)
const lab = { chg: ['stocks.cols.change', 'stocks.cols.chg'], vol: ['stocks.cols.volume'], liq: ['stocks.cols.liquidity', 'tokens.cols.liquidity'], pools: ['stocks.cols.pools'], bb: ['stocks.cols.bigbuys', 'landing.stats.bigbuys.label_short'] };
for (const [k, ids] of Object.entries(lab)) { const id = ids.find((i) => has(i)); const b = $(`#sxh [data-k="${k}"] button`); if (b && id && !b.textContent.trim()) b.textContent = t(id); }
const paintSort = () => $$('#sxh [data-k]').forEach((h) => h.setAttribute('aria-sort', h.dataset.k === sortK ? (sortDir > 0 ? 'ascending' : 'descending') : 'none'));
$$('#sxh [data-k] button').forEach((b) => b.addEventListener('click', () => { const k = b.parentElement.dataset.k; if (sortK === k) sortDir = -sortDir; else { sortK = k; sortDir = k === 'name' ? 1 : -1; } paintSort(); draw(); }));
$('#rows').innerHTML = Array.from({ length: +$('#rows').dataset.count || 75 }, () => '<div class="r sk-r"><span class="nmc"><span class="sk" style="width:36px;height:36px;border-radius:50%"></span><span class="sk" style="width:140px;height:14px"></span></span></div>').join('');
(async () => {
  let T, s;
  try { [T, s] = await Promise.all([tokens(), api('/api/stocks-overview', { cache: 60000 }).catch(() => null)]); }
  catch { $('#rows').innerHTML = `<div class="empty"><b>${esc(t('errors.quotes_down'))}</b><br><button type="button" class="btn2" id="sretry" style="margin-top:12px">${esc(t('errors.quotes_down_cta'))}</button></div>`; $('#sretry').addEventListener('click', () => location.reload()); return; }
  list = T.list.filter((x) => x.chain === 4663 && (x.kind === 'stock' || x.kind === 'etf'));
  if (s && s.stocks) { OV.__from = s.from; OV.__to = s.to; s.stocks.forEach((x) => (OV[x.stock] = x)); }
  // the one price of the site: the overview carries the same price as /api/tokens; it fills a token whose list price is missing
  list.forEach((x) => { const o = OV[x.symbol]; if (x.price == null && o && o.price != null) x.price = o.price; });
  $('#lede').textContent = t('stocks.lede', { count: list.length });
  $('#stamp').textContent = t('stocks.stamp', { time: T.at ? hhmm(T.at) : '' });
  paintSort(); draw();
})();
$('#rows').addEventListener('click', (e) => { const b = e.target.closest('[data-buy]'); if (b) { e.preventDefault(); location.href = '/app?to=' + encodeURIComponent(b.dataset.buy); } });
$('#q').addEventListener('input', (e) => { q = e.target.value; draw(); });
$$('#flt [data-f]').forEach((b) => b.addEventListener('click', () => { f = b.dataset.f; $$('#flt [data-f]').forEach((x) => x.classList.toggle('on', x === b)); draw(); }));
