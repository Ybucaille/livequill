#!/usr/bin/env node
// Syntax check for the browser code: every script in livequill/static and the inline <script> of every HTML page. Used by CI.
import { readFileSync, readdirSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import vm from 'node:vm';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const files = [];
for (const dir of ['livequill/static', 'site']) {
  try { for (const f of readdirSync(join(root, dir)).sort()) if (/\.(html|js)$/.test(f)) files.push(join(root, dir, f)); } catch {}
}
let failed = 0;
for (const f of files) {
  const src = readFileSync(f, 'utf8');
  const scripts = f.endsWith('.html') ? [...src.matchAll(/<script(?![^>]*\bsrc=)[^>]*>([\s\S]*?)<\/script>/g)].map(m => m[1]) : [src];
  scripts.forEach((code, i) => {
    try { new vm.Script(code, { filename: `${f}#${i}` }); }
    catch (e) { failed++; console.error(`✗ ${f} (script ${i}): ${e.message}`); }
  });
  console.log(`✓ ${f.slice(root.length + 1)} (${scripts.length} script${scripts.length > 1 ? 's' : ''})`);
}
process.exit(failed ? 1 : 0);
