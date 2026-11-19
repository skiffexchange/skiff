// Page chrome shared by every page: mobile menu (opaque scrim, closes on ✕ / Esc / tap outside / link), ⌘K anywhere (palette on demand).
import { $, $$ } from './core.js';

const MENU = '<svg width="18" height="18" viewBox="0 0 18 18" fill="none" aria-hidden="true"><path d="M3 6h12M3 12h12" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg>';
const CLOSE = '<svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden="true"><path d="M3.5 3.5l9 9M12.5 3.5l-9 9" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg>';

// P2: a number and its unit never part at a line end («24 h», «100 ms») — the same characters, a no-break space between them
function keepUnits(root) {
  const w = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  for (let n = w.nextNode(); n; n = w.nextNode()) { const v = n.nodeValue; if (/\d (h|s|ms|min)\b/.test(v)) n.nodeValue = v.replace(/(\d) (h|s|ms|min)\b/g, '$1\u00a0$2'); }
}

export function chrome(onPick) {
  document.querySelectorAll('.bnote, .ld, .foot, .how2 p').forEach(keepUnits);
  const mb = $('[data-menu]'), mn = $('.mnav');
  if (mb && mn) {
    let sc = null;
    const set = (on) => {
      mn.classList.toggle('on', on); mb.setAttribute('aria-expanded', on); mb.innerHTML = on ? CLOSE : MENU;
      if (on && !sc) { sc = document.createElement('div'); sc.className = 'mscrim'; sc.addEventListener('click', () => set(false)); (mb.closest('.nav') || document.body).appendChild(sc); requestAnimationFrame(() => sc && sc.classList.add('on')); }
      if (!on && sc) { const s = sc; sc = null; s.classList.remove('on'); setTimeout(() => s.remove(), 200); }
      // Tab stays in the open menu: everything around the header is inert while it is open
      const hd = mb.closest('.nav'), out = hd ? [...document.body.children].filter((x) => !x.contains(hd)).concat([...hd.parentElement.children].filter((x) => x !== hd)) : [];
      out.forEach((x) => { x.inert = on; });
      if (on) { const a = mn.querySelector('a'); if (a) a.focus({ preventScroll: true }); }
    };
    mb.addEventListener('click', () => set(!mn.classList.contains('on')));
    mn.addEventListener('click', (e) => { if (e.target.closest('a')) set(false); });
    addEventListener('keydown', (e) => { if (e.key === 'Escape' && mn.classList.contains('on')) { set(false); mb.focus(); } });
    addEventListener('resize', () => { if (innerWidth > 980 && mn.classList.contains('on')) set(false); });
  }
  const open = async () => { const m = await import('./cmdk.js'); m.palette(onPick); };
  $$('[data-cmdk]').forEach((b) => b.addEventListener('click', open));
  addEventListener('keydown', (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); if (!document.querySelector('.scrim')) open(); }
  });
}
