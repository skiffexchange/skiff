// /stock/{TICKER} v2: the token (logo, name, price, watch), the swap window + stage for this ticker, big trades in it, contract facts.
import { t, has, esc, $, api, tokens, tokI, nf, toast, errLine, hhmm } from './core.js';
import { mountStage, tradeRow } from './stage.js';
import { bindHeader, restore, state as W, connect, onWallet } from './wallet.js';
import { chrome } from './chrome.js';

chrome(); bindHeader(); restore();
const tick = (document.body.dataset.ticker || decodeURIComponent(location.pathname.split('/')[2] || '')).toUpperCase();
(async () => {
  const T = await tokens();
  const x = T.find(4663, tick);
  if (!x) {
    $('#main').innerHTML = `<div class="nf"><h1>${esc(t('stocks.detail.not_found', { ticker: tick || '—' }))}</h1><a class="cta" href="/stocks">${t('stocks.detail.not_found_cta')}</a></div>`;
    return;
  }
  $('#slogo').outerHTML = tokI(x, 'stk');
  $('#sname').textContent = t('stocks.detail.title', { name: x.name }).replace(/(\d)-(\d)/g, '$1\u2011$2');
  $('#sname').style.setProperty('--nc', $('#sname').textContent.length);
  $('#stk').textContent = t(x.kind === 'etf' ? 'stocks.detail.ticker_etf' : 'stocks.detail.ticker', { ticker: x.symbol });
  $('#spx').textContent = x.price != null ? t('stocks.detail.price', { price: nf(x.price, 2) }) : '—';
  $('#sca').textContent = x.address.slice(0, 10) + '…' + x.address.slice(-8);
  $('#sca').title = x.address;
  $('#sexp').href = 'https://robinhoodchain.blockscout.com/token/' + x.address;
  $('#trt').innerHTML = esc(t('stocks.detail.trades', { ticker: '\u0000' })).replace('\u0000', `<span class="pair">${tokI(x, 'stk')}${esc(x.symbol)}</span>`);   // P3: the ticker with its icon
  // P3: 24 h price from the deepest pool (5-min buckets) — an area chart in a fixed box; the change only when a real 24 h-old point exists
  api('/api/price-history?stock=' + encodeURIComponent(x.symbol), { cache: 60000 }).then((h) => {
    // P4: a STEP series on a fixed 24 h axis (a pool price moves only on a swap): x from `from` to `to`, horizontal then vertical;
    // the y range is at least ±0.5 % of the middle, so a flat day stays flat; the headline price is the same pool's `last`
    // P6: where the price comes from — the pool (venue mark, name, pair), from the same answer as the chart
    if (h && h.pool) api('/api/stats', { cache: 60000 }).then((st) => { const v = ((st && st.byDex) || []).find((d) => d.dex === h.pool.dex) || { name: h.pool.dex, logo: null };
      $('#spoolL').textContent = has('stocks.detail.pool') ? t('stocks.detail.pool') : t('stocks.cols.price');
      $('#spoolV').innerHTML = `${v.logo ? `<img src="${esc(v.logo)}" alt="" width="18" height="18">` : ''}${esc(v.name)} · ${esc(h.pool.pair || '')}`; balance();
    }).catch(() => {});
    const pts = (h && h.enough && h.points) || [];
    if (pts.length >= 2) {
      const x0 = h.from || pts[0][0], x1 = h.to || pts[pts.length - 1][0];
      const ys = pts.map((q) => q[1]); let y0 = Math.min(...ys), y1 = Math.max(...ys); const mid = (y0 + y1) / 2, half = Math.max((y1 - y0) / 2, mid * 0.005);
      y0 = mid - half; y1 = mid + half;
      const X = (v) => (((v - x0) / Math.max(1, x1 - x0)) * 600).toFixed(1), Y = (v) => (74 - ((v - y0) / Math.max(1e-12, y1 - y0)) * 68).toFixed(1);
      let d = `M${X(pts[0][0])} ${Y(pts[0][1])}`;
      for (let i = 1; i < pts.length; i++) d += `H${X(pts[i][0])}V${Y(pts[i][1])}`;
      d += `H600`;
      $('#spark .sl').setAttribute('d', d); $('#spark .sa').setAttribute('d', `${d}V80H${X(pts[0][0])}Z`);
      $('#spark').classList.add('on');
      if (has('stocks.detail.chart_aria')) $('#spark svg').setAttribute('aria-label', t('stocks.detail.chart_aria', { ticker: x.symbol }));
      // P7: crosshair — the step under the pointer (real points only: the last swap price at or before that moment) with its price and time
      const sp = $('#spark'), tip = sp.querySelector('.stip'), xh = sp.querySelector('.sxh'), xd = sp.querySelector('.sxd');
      const fmt = (v) => t('stocks.detail.price', { price: nf(v, v < 1 ? 4 : 2) });
      const show = (cx) => {
        const r = sp.querySelector('svg').getBoundingClientRect(); if (!r.width) return;
        const f = Math.min(1, Math.max(0, (cx - r.left) / r.width)), ts = x0 + f * (x1 - x0);
        let k = 0; for (let i = 0; i < pts.length; i++) { if (pts[i][0] <= ts) k = i; else break; }
        const q = pts[k], px = Math.max(0, Math.min(r.width, ((Math.max(q[0], ts) - x0) / Math.max(1, x1 - x0)) * r.width)), py = (Number(Y(q[1])) / 80) * r.height;
        xh.style.transform = `translateX(${px.toFixed(1)}px)`; xd.style.transform = `translate(${px.toFixed(1)}px,${py.toFixed(1)}px)`;
        tip.textContent = `${fmt(q[1])} · ${t('common.time_utc', { time: hhmm(q[0]) })}`;
        const tw = tip.offsetWidth; tip.style.transform = `translateX(${Math.max(0, Math.min(r.width - tw, px - tw / 2)).toFixed(1)}px)`;
        sp.classList.add('hov');
      };
      sp.addEventListener('pointermove', (e) => show(e.clientX));
      sp.addEventListener('pointerdown', (e) => show(e.clientX));
      sp.addEventListener('pointerleave', () => sp.classList.remove('hov'));
      sp.addEventListener('pointercancel', () => sp.classList.remove('hov'));
    } else if (has('stocks.detail.chart_empty')) { const e = $('#sempty'); e.textContent = t('stocks.detail.chart_empty'); e.hidden = false; }
    const head = h && h.last != null ? h.last : x.price;
    if (head != null) $('#spx').textContent = t('stocks.detail.price', { price: nf(head, head < 1 ? 4 : 2) });
    if (h && h.change24hPct != null && pts.length >= 2) { const v = h.change24hPct; $('#schgv').textContent = `${v > 0 ? '+' : v < 0 ? '−' : ''}${nf(Math.abs(v), 2)}%`; $('#schg').classList.add('on', v > 0 ? 'up' : v < 0 ? 'dn' : 'eq'); }
  }).catch(() => {});
  // P5 /: no big buy on the stage — the words depend on the table: «no big buys ($5k+)» only when the table is empty too; then the
  // contract card moves under the stage line, so the right column is never an empty field
  let TR_N = null;
  // P5/P6: no big buy — with an empty table the stage says «no big buys ($5k+)»; with rows in the table the stage steps aside (the table
  // is the content, and «no big buys» never sits above trades)
  const notr = () => {
    const st = $('#stage'); if (!st || TR_N == null) return;
    const no = st.classList.contains('notr');
    if (no && !TR_N && has('stocks.detail.no_trades')) $('#iline').textContent = t('stocks.detail.no_trades');
    $('.stgw').hidden = no && TR_N > 0;
    balance();
  };
  document.addEventListener('skiff-notr', notr);
  /* P6: the two columns end together — the swap (left) and the stage (right) stay on top; the trades table, the contract card and the
     notes go where the column bottoms come closest (or full width under both). Desktop only; one synchronous pass, no animation. */
  const balance = () => {
    if (innerWidth < 981 || TR_N == null) return;
    const L = $('.colL'), R = $('.colR'), F = $('#sfull'), swc = $('.swc'), stw = $('.stgw');
    const tbl = $('#trl').closest('.lowsec'), fk = $('.facts.fk'), fn = $('.facts.fn');
    const blocks = [tbl.hidden ? null : tbl, fk, fn].filter(Boolean);
    const G = 20, h = (e) => e.getBoundingClientRect().height;
    const size = {}; for (const e of blocks) { L.appendChild(e); const a = h(e); R.appendChild(e); size[blocks.indexOf(e)] = { L: a, R: h(e) }; }
    const base = { L: h(swc), R: stw.hidden ? 0 : h(stw) };
    let best = null;
    const walk = (i, asg) => {
      if (i === blocks.length) {
        let l = base.L, r = base.R, pen = 0;
        asg.forEach((c, k) => { if (c === 'L') { l += G + size[k].L; if (blocks[k] === tbl) pen += 400; } else if (c === 'R') { r += (r ? G : 0) + size[k].R; } else pen += 60; });
        const sc = Math.abs(l - r) + pen; if (!best || sc < best.sc) best = { sc, asg: asg.slice() }; return;
      }
      for (const c of ['R', 'L', 'F']) { asg.push(c); walk(i + 1, asg); asg.pop(); }
    };
    walk(0, []);
    best.asg.forEach((c, k) => (c === 'L' ? L : c === 'R' ? R : F).appendChild(blocks[k]));
  };
  document.addEventListener('skiff-stage', () => setTimeout(balance, 0));
  let rz = 0; addEventListener('resize', () => { clearTimeout(rz); rz = setTimeout(balance, 150); });
  mountStage({ to: x.symbol });
  let watched = false;
  const paintW = () => ($('#watch').textContent = t(watched ? 'stocks.detail.watching' : 'stocks.detail.watch'));
  onWallet(async (s) => { if (!s.account) { watched = false; paintW(); return; } try { const w = await api('/api/me/watchlist'); watched = (w.tickers || []).includes(x.symbol); paintW(); } catch { /* signed-in routes down */ } });
  $('#watch').onclick = async () => {
    if (!W.account) return connect();
    try { const w = await api('/api/me/watchlist'); const set = new Set(w.tickers || []); if (watched) set.delete(x.symbol); else set.add(x.symbol); await api('/api/me/watchlist', { method: 'PUT', body: { tickers: [...set] } }); watched = !watched; paintW(); toast(t('app.watch.saved')); } catch (e) { toast(errLine(e)); }
  };
  try {
    let d = null;   // P6: one retry after a busy answer — the page's composition waits for this count
    try { d = await api('/api/trades?limit=10&stock=' + encodeURIComponent(x.symbol)); } catch { await new Promise((r) => setTimeout(r, 1500)); d = await api('/api/trades?limit=10&stock=' + encodeURIComponent(x.symbol)); }
    const rows = (d.trades || []).map((y) => tradeRow(y, x)), SHOW = 6;     // same cap as /app (the block was too tall)
    let all = false;
    const paint = () => {
      const hid = rows.length - SHOW;
      $('#trl').innerHTML = rows.length ? (all || hid <= 0 ? rows : rows.slice(0, SHOW)).join('') +
        (hid > 0 ? `<button type="button" class="trmore" aria-expanded="${all}">${esc(all ? t('app.trades.less') : t('app.trades.more', { n: hid }))}</button>` : '')
        : '';
      $('#trl').closest('.lowsec').hidden = !rows.length;   // P5: the table section is hidden from the first frame and only appears with rows
      TR_N = rows.length; notr();
      const b = $('#trl .trmore'); if (b) b.onclick = () => { all = !all; paint(); };
    };
    paint();
  } catch (e) { $('#trl').innerHTML = `<p class="empty">${esc(errLine(e))}</p>`; TR_N = 0; notr(); }
})();
