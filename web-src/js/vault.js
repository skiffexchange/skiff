// /token (Vault, TOKEN-UTILITY.md): the vault's USDG balance, the week log, the next-drop clock, «Check an address», the CA with copy.
// The server already rendered the state (body data-ca / data-vault / data-drop, CA and vault address); this file only fills live data
// into boxes that keep their size. No sign-in here: the header button loads the wallet layer only when pressed (no restore, no Privy).
import { t, esc, $, $$, api, amt, nf, errLine } from './core.js';
import { bindHeader } from './wallet.js';
import { chrome } from './chrome.js';

chrome(); bindHeader();
const B = document.body;

const ADDR = /^0x[0-9a-fA-F]{40}$/;
const MON = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
const day = (ts) => { const d = new Date(ts * 1000); return `${d.getUTCDate()} ${MON[d.getUTCMonth()]} ${d.getUTCFullYear()}`; };
const usd = (n) => nf(n, 2);
const logo = (x) => (x.logo ? `<span class="stk"><img src="${esc(x.logo)}" alt="" width="40" height="40"></span>` : `<span class="stk stk-t" data-t="${esc(x.symbol)}"></span>`);

// ---------- vault now (one line: the USDG balance) + week log + clock
function balance(d) {
  const h = d.holdings;
  if (!h || !h.usdg) return;   // no address yet, or the chain did not answer: the box keeps its height
  $('#vbal').textContent = t('token.vault.balance', { amount: h.usdg.amount });
  $('.vnow').classList.toggle('zero', h.usdg.raw === '0');
  if (h.dropUsdg && h.dropUsdg.raw !== '0') $('#vdrop').textContent = t('token.vault.drop_amount', { amount: h.dropUsdg.amount });   // DROP_SHARE of the vault, integer math on the server
}
function logRows(d) {
  const L = d.log || [];
  if (!L.length) return;
  $('#vlog').innerHTML = `<ol class="vll">${L.map((x) => {
    const links = `${x.tx ? `<a href="${esc(x.tx)}" target="_blank" rel="noopener">${esc(t('token.log.tx'))} ↗</a>` : ''}${x.race ? `<a href="${esc(x.race)}">${esc(t('token.log.race'))}</a>` : ''}`;
    if (x.kind === 'drop') return `<li class="vle drop"><span class="vnode">${DROP_SVG}</span><span class="vltx">${esc(t('token.log.drop', { date: day(x.ts), usd: usd(x.usd), holders: nf(x.holders, 0) }))}</span><span class="vlk">${links}</span></li>`;
    return `<li class="vle buy">${logo({ symbol: x.stock, logo: x.logo })}<span class="vltx">${esc(t('token.log.buy', { date: day(x.ts), usd: usd(x.usd), stock: x.stock }))}</span><span class="vlk">${links}</span></li>`;
  }).join('')}</ol>`;
}
const DROP_SVG = '<svg width="18" height="18" viewBox="0 0 18 18" fill="none" aria-hidden="true"><circle cx="9" cy="4.5" r="2" stroke="currentColor" stroke-width="1.5"/><circle cx="3.8" cy="13.5" r="2" stroke="currentColor" stroke-width="1.5"/><circle cx="14.2" cy="13.5" r="2" stroke="currentColor" stroke-width="1.5"/><path d="M8 6.4 5 11.6M10 6.4l3 5.2" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/></svg>';
let clock = null;
function timer(iso) {
  if (clock) { clearInterval(clock); clock = null; }
  const to = iso ? Date.parse(iso) : NaN;
  if (!isFinite(to)) return;   // no fake countdown: dashes stay until the owner sets the first drop date
  const cells = Object.fromEntries($$('#vclock [data-u]').map((e) => [e.dataset.u, e]));
  const tick = () => {
    let s = Math.max(0, Math.round((to - Date.now()) / 1000));
    const v = { d: Math.floor(s / 86400), h: Math.floor((s % 86400) / 3600), m: Math.floor((s % 3600) / 60), s: s % 60 };
    for (const k in v) cells[k].textContent = String(v[k]).padStart(2, '0');
    if (s === 0) { clearInterval(clock); clock = null; setTimeout(load, 5000); }
  };
  tick(); clock = setInterval(tick, 1000);
  $('#vclock').classList.add('on');
}
function load() {
  api('/api/vault').then((d) => { balance(d); logRows(d); timer(d.nextDrop); }).catch(() => {});
}
load();

// ---------- check an address (no wallet, no sign-in): validated here and again on the server
const res = $('#vres');
const say = (cls, html) => { res.className = 'vres ' + cls; res.innerHTML = html; };
$('#vchk').addEventListener('submit', async (e) => {
  e.preventDefault();
  const a = $('#vq').value.trim();
  if (!ADDR.test(a)) { say('bad', `<p>${esc(t('token.check.invalid'))}</p>`); return; }
  say('wait', '<p>&nbsp;</p>');
  try {
    const d = await api('/api/vault/check?address=' + encodeURIComponent(a));
    if (!d.drops) return say('note', `<p>${esc(t('token.check.prelaunch'))}</p>`);   // until there are drops
    const R = d.received || [];
    if (!R.length) return say('note', `<p>${esc(t('token.check.none'))}</p>`);
    say('got', `<ul class="vrl">${R.map((x) => `<li>${logo({ symbol: x.stock, logo: x.logo })}<span>${esc(t('token.check.row', { date: day(x.ts), amount: amt(x.raw, x.decimals, 2), stock: x.stock, usd: usd(x.usd) }))}</span></li>`).join('')}</ul>${d.totalUsd != null ? `<p class="vrt">${esc(t('token.check.total', { usd: usd(d.totalUsd) }))}</p>` : ''}`);
  } catch (err) {
    if (err && err.status === 400) return say('bad', `<p>${esc(t('token.check.invalid'))}</p>`);
    say('bad', `<p>${esc(errLine(err))}</p>`);
  }
});
$('#vq').addEventListener('input', () => { if (res.classList.contains('bad')) say('', ''); });

// ---------- CA copy (after launch)
const cp = $('#vcopy');
if (cp && B.dataset.ca === '1') cp.addEventListener('click', async () => {
  const ca = ($('#vcaa').textContent || '').trim();
  try { await navigator.clipboard.writeText(ca); } catch { const r = document.createRange(); r.selectNodeContents($('#vcaa')); const s = getSelection(); s.removeAllRanges(); s.addRange(r); return; }
  cp.classList.add('done'); setTimeout(() => cp.classList.remove('done'), 1600);
});
