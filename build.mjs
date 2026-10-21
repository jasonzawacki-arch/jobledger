// Dependency-free bundler: transpiles TypeScript with the compiler API, joins relative imports into one IIFE,
// and inlines the CSS into a single HTML file. Builds the same interface four ways:
//   dist/index.html        served by the Python server (data on your computer)
//   dist/app/index.html    the installable phone app (data saved on the phone; host as static files)
//   dist/demo/index.html   the demo preview's base (python preview/build_preview.py adds the sample data)
//   dist/play/index.html   the Google Play build (no service worker, no server/sync/API-key panels, Pro gate)
import { createRequire } from 'node:module';
import fs from 'node:fs';
import path from 'node:path';
const require = createRequire(import.meta.url);
let ts;
try { ts = require('typescript'); } catch { ts = require(process.env.TS_PATH || '/home/claude/.npm-global/lib/node_modules/typescript'); }

const root = path.dirname(new URL(import.meta.url).pathname);
function bundle(entryFile) {
  const entry = path.join(root, entryFile);
  const mods = new Map();
  const order = [];
  const resolve = (from, spec) => {
    const p = path.resolve(path.dirname(from), spec.replace(/\.js$/, '') + '.ts');
    if (!fs.existsSync(p)) throw new Error(`Cannot resolve ${spec} from ${from}`);
    return p;
  };
  const visit = (file) => {
    if (mods.has(file)) return mods.get(file);
    const m = { id: mods.size, deps: {}, code: '' };
    mods.set(file, m);
    const out = ts.transpileModule(fs.readFileSync(file, 'utf8'), {
      compilerOptions: { target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.CommonJS },
      fileName: file,
    }).outputText;
    const re = /require\("([^"]+)"\)/g;
    let x;
    while ((x = re.exec(out))) m.deps[x[1]] = visit(resolve(file, x[1])).id;
    m.code = out;
    order.push(m);
    return m;
  };
  visit(entry);
  const body = order.map((m) => `${m.id}:[function(exports,require){${m.code}\n},${JSON.stringify(m.deps)}]`).join(',\n');
  return `(function(){var D={${body}};var C={};function load(i){if(C[i])return C[i].exports;var m=C[i]={exports:{}};var d=D[i];d[0](m.exports,function(s){return load(d[1][s]);});return m.exports;}load(${mods.get(entry).id});})();`;
}
function page(entryFile) {
  const html = fs.readFileSync(path.join(root, 'src/index.template.html'), 'utf8');
  return html.replace('/*CSS*/', () => fs.readFileSync(path.join(root, 'src/style.css'), 'utf8'))
    .replace('/*JS*/', () => bundle(entryFile).replace(/<\/script/gi, '<\\/script'));
}
function installable(dir, html, swFile) {
  fs.mkdirSync(dir, { recursive: true });
  fs.writeFileSync(path.join(dir, 'index.html'), html);
  const ver = String(html.length) + '-' + Date.now().toString(36);
  fs.writeFileSync(path.join(dir, 'sw.js'), fs.readFileSync(path.join(root, 'src/pwa', swFile), 'utf8').replace('__V__', ver));
  for (const f of ['manifest.webmanifest', 'icon-192.png', 'icon-512.png', 'icon-maskable-512.png']) fs.copyFileSync(path.join(root, 'src/pwa', f), path.join(dir, f));
}
const dist = path.join(root, 'dist');
const server = page('src/app.ts');
installable(dist, server, 'sw.js');
const phone = page('src/boot-phone.ts');
installable(path.join(dist, 'app'), phone, 'sw-phone.js');
fs.writeFileSync(path.join(dist, 'app', '.nojekyll'), '');
fs.mkdirSync(path.join(dist, 'demo'), { recursive: true });
fs.writeFileSync(path.join(dist, 'demo', 'index.html'), page('src/boot-demo.ts'));
fs.mkdirSync(path.join(dist, 'play'), { recursive: true });
fs.writeFileSync(path.join(dist, 'play', 'index.html'), page('src/boot-play.ts'));
console.log('dist/index.html', (server.length / 1024).toFixed(0) + ' KB;', 'dist/app/index.html', (phone.length / 1024).toFixed(0) + ' KB;', 'dist/demo/index.html', 'dist/play/index.html', (fs.statSync(path.join(dist, 'play', 'index.html')).size / 1024).toFixed(0) + ' KB;');
