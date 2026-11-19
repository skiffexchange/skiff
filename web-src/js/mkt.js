// Market-table cells shared by /stocks and /tokens (P9): «—» for nulls, signed 24 h %, short dollars, the 24 h step sparkline.
import { nf, usdShort } from './core.js';
export const dash = '<span class="nil">—</span>';
export const money = (n) => (n == null ? dash : usdShort(n));
export const pct = (v) => (v == null ? dash : `<span class="${v > 0 ? 'up' : v < 0 ? 'dn' : 'eq'}">${v > 0 ? '+' : v < 0 ? '−' : ''}${nf(Math.abs(v), 2)}%</span>`);
/** 24 h step line on a fixed axis (from → to), the same drawing as the stock page chart; an empty slot when there is no real series */
export function spark(o, x0, x1) {
  const pts = (o && o.sparkEnough && o.spark) || [];
  if (pts.length < 2 || !x0 || !x1) return '<span class="spk"></span>';
  const W = 112, H = 32;
  const ys = pts.map((p) => p[1]); let y0 = Math.min(...ys), y1 = Math.max(...ys); const mid = (y0 + y1) / 2, half = Math.max((y1 - y0) / 2, mid * 0.005); y0 = mid - half; y1 = mid + half;
  const X = (v) => Math.max(0, Math.min(W, ((v - x0) / Math.max(1, x1 - x0)) * W)).toFixed(1), Y = (v) => (H - 3 - ((v - y0) / Math.max(1e-12, y1 - y0)) * (H - 6)).toFixed(1);
  let d = `M${X(pts[0][0])} ${Y(pts[0][1])}`; for (let i = 1; i < pts.length; i++) d += `H${X(pts[i][0])}V${Y(pts[i][1])}`; d += `H${W}`;
  const c = o.change24hPct; const cls = c == null ? 'nt' : c > 0 ? 'up' : c < 0 ? 'dn' : 'nt';
  return `<span class="spk ${cls}"><svg viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" aria-hidden="true" preserveAspectRatio="none"><path d="${d}"/></svg></span>`;
}
