// Plain pages (FAQ, legal, 404): header wallet button, mobile menu, ⌘K.
import { t, $, $$, api, amt, nf, ago, kfmt } from './core.js';
import { bindHeader, restore } from './wallet.js';
import { chrome } from './chrome.js';

chrome(); bindHeader(); restore();
$$('[data-upd]').forEach((e) => (e.textContent = t(e.dataset.upd, { date: '16 Nov 2026' })));

// FAQ: the «+$N vs one pool» example is live only (D4): the current $100k NVDA board row, when it is postable
const vl = $('#vslive');
if (vl) api('/api/board', { cache: 30000 }).then((d) => {
  const r = (d.rows || []).filter((x) => x.postable && x.best && x.singlePool && x.singlePool.out).sort((a, b) => b.vsSinglePoolUsd - a.vsSinglePoolUsd)[0];
  if (!r) return;
  vl.textContent = t('faq.a.vs_pool_live', { amount: kfmt(r.sizeUsd), from: 'USDG', ticker: r.stock, out: amt(r.best.out.raw, r.best.out.decimals, 2), dex: r.singlePool.name, pair: r.singlePool.pair || `${r.stock}/USDG`, poolOut: amt(r.singlePool.out.raw, r.singlePool.out.decimals, 2), usd: nf(r.vsSinglePoolUsd, 2), age: ago(Date.now() / 1000 - (r.recordedAt || r.at)) });
  vl.hidden = false;
}).catch(() => {});
