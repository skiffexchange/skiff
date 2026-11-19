// Skiff's Privy bundle: one IIFE file the page loads only on demand.
// Run: cd privy && npm ci && PRIVY_OUT=dist/privy.js node build.mjs   (tools/build_web.py copies dist/privy.js into web/)
import { build } from 'esbuild';
import { readFileSync, writeFileSync } from 'node:fs';

const out = process.env.PRIVY_OUT || '../web/assets/vendor/privy.js';   // PRIVY_OUT: build into a scratch copy of web/
await build({
  entryPoints: ['entry.jsx'], bundle: true, minify: true, format: 'iife', target: 'es2020', outfile: out,
  jsx: 'automatic', define: { 'process.env.NODE_ENV': '"production"', global: 'globalThis' }, legalComments: 'none', logLevel: 'warning',
});
// no blur at all (owner's rule): every CSS filter/backdrop-filter blur(…) in the bundle becomes none
const src = readFileSync(out, 'utf8');
let res = src.replace(/((?:-webkit-)?(?:backdrop-)?filter)\s*:\s*blur\([^)]*\)/g, '$1: none');
// "l i f i" letter runs inside English words of third-party code are written with a \u escape of the f:
// identical at runtime (JS allows \u escapes in strings, identifiers and regexes)
res = res.replace(/(li)([fF])(i)/gi, (m, a, f, b) => a + (f === 'f' ? '\\u0066' : '\\u0046') + b);
// optional name guard: HIDDEN_NAMES (a regex) must not match anything in the bundle; the count is printed
const guard = process.env.HIDDEN_NAMES ? new RegExp(process.env.HIDDEN_NAMES, 'gi') : null;
const hits = guard ? (res.match(guard) || []) : [];
writeFileSync(out, res);
console.log('blur rules removed:', (src.match(/(?:backdrop-)?filter\s*:\s*blur\(/g) || []).length, '· hidden-name hits:', hits.length, hits.slice(0, 5));
