// Minify our ES modules / CSS file by file (no bundling): comments gone, relative imports stamped with ?v=<version>.
// usage: node minify.mjs <version> <src>:<dst> [<src>:<dst> …]   (directories, walked recursively; .js and .css)
import { transform } from '../privy/node_modules/esbuild/lib/main.js';
import { readFileSync, writeFileSync, mkdirSync, readdirSync, statSync } from 'node:fs';
import { join, dirname } from 'node:path';

const [v, ...pairs] = process.argv.slice(2);
const IMP = /((?:from|import)\s*\(?\s*)(["'])(\.{1,2}\/[^"']+?\.js)(\?v=\w+)?\2/g;
let n = 0, bytes = 0;
async function walk(src, dst) {
  for (const f of readdirSync(src)) {
    const s = join(src, f), d = join(dst, f);
    if (statSync(s).isDirectory()) { await walk(s, d); continue; }
    if (!/\.(js|css)$/.test(f)) continue;
    const code = readFileSync(s, 'utf8');
    const out = await transform(code, { loader: f.endsWith('.css') ? 'css' : 'js', minify: true, format: f.endsWith('.js') ? 'esm' : undefined, target: f.endsWith('.js') ? 'es2020' : undefined, legalComments: 'none', charset: 'utf8' });
    let res = out.code.replace(/\/\/# sourceMappingURL=.*$/gm, '');
    if (f.endsWith('.js')) res = res.replace(IMP, (m, a, q, p) => `${a}${q}${p}?v=${v}${q}`);
    mkdirSync(dirname(d), { recursive: true });
    writeFileSync(d, res); n++; bytes += res.length;
  }
}
for (const p of pairs) { const [s, d] = p.split(':'); await walk(s, d); }
console.log(`minified ${n} files, ${Math.round(bytes / 1024)} KB`);
