// Runs a function from a TypeScript module on JSON input, for tests that compare TS with Python.
// Usage: node tools/ts_eval.mjs src/engine/matcher.ts match < input.json   (input: list of argument lists)
import { createRequire } from 'node:module';
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
const require = createRequire(import.meta.url);
let ts;
try { ts = require('typescript'); } catch { ts = require(process.env.TS_PATH || '/home/claude/.npm-global/lib/node_modules/typescript'); }
const [file, fn] = process.argv.slice(2);
const cache = new Map();
function load(p) {
  if (cache.has(p)) return cache.get(p).exports;
  const mod = { exports: {} };
  cache.set(p, mod);
  const out = ts.transpileModule(fs.readFileSync(p, 'utf8'), { compilerOptions: { target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.CommonJS } }).outputText;
  const req = (s) => load(path.resolve(path.dirname(p), s.replace(/\.js$/, '') + '.ts'));
  vm.runInThisContext(`(function(exports, require, module){${out}\n})`)(mod.exports, req, mod);
  return mod.exports;
}
const f = load(path.resolve(file))[fn];
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
process.stdout.write(JSON.stringify(input.map((args) => f(...args))));
