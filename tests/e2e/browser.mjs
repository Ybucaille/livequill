#!/usr/bin/env node
// Parcours navigateur réel : un Chromium headless ouvre un site servi par LiveQuill, tape une demande dans la barre,
// attend le rechargement automatique, vérifie la page, clique sur « Annuler » et vérifie le retour à l'état d'origine,
// passe par les versions, puis crée un nouveau site depuis le tableau de bord.
// Le modèle est un petit serveur compatible OpenAI lancé ici même. Aucune dépendance npm.
//
//   node tests/e2e/browser.mjs [--chromium /chemin/vers/chrome]
import { spawn, execFileSync } from 'node:child_process';
import { mkdtempSync, writeFileSync, readFileSync, rmSync, existsSync } from 'node:fs';
import { createServer } from 'node:http';
import { tmpdir } from 'node:os';
import { join, dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const REPO = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
const arg = (k, d) => { const i = process.argv.indexOf('--' + k); return i > 0 ? process.argv[i + 1] : d; };
const sleep = ms => new Promise(r => setTimeout(r, ms));
const PAGE = '<!doctype html><html><body><h1 class="t">Hello</h1></body></html>';
const fail = msg => { console.error('✗ ' + msg); cleanup(); process.exit(1); };

function chromium() {
  const given = arg('chromium', process.env.CHROME || process.env.CHROME_BIN);
  if (given) return given;
  // Chrome d'abord : sur Ubuntu, « chromium » peut n'être qu'un raccourci vers un paquet snap qui ne démarre pas en headless
  for (const c of ['google-chrome', 'google-chrome-stable', 'chromium', 'chromium-browser']) {
    try { execFileSync('which', [c], { stdio: 'ignore' }); return c; } catch {}
  }
  const mac = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
  if (existsSync(mac)) return mac;
  fail('Chromium/Chrome introuvable (--chromium /chemin)');
}

// --- le « modèle » : 1er appel → edit_file, 2e appel (après le résultat de l'outil) → réponse finale ---
const calls = [];
const model = createServer((req, res) => {
  if (req.method === 'GET') { res.writeHead(200, { 'Content-Type': 'application/json' }); return res.end('{"data":[{"id":"fake-model"}]}'); }
  let body = '';
  req.on('data', c => (body += c));
  req.on('end', () => {
    const r = JSON.parse(body); calls.push(r);
    const after = r.messages.some(m => m.role === 'tool');
    const chunks = after
      ? [{ choices: [{ delta: { content: 'Titre changé.' }, finish_reason: 'stop' }] }]
      : [{ choices: [{ delta: { tool_calls: [{ index: 0, id: 'c1', function: { name: 'edit_file', arguments: JSON.stringify({ path: 'index.html', old_text: 'Hello', new_text: 'Salut' }) } }] }, finish_reason: 'tool_calls' }] }];
    res.writeHead(200, { 'Content-Type': 'text/event-stream' });
    for (const c of chunks) res.write('data: ' + JSON.stringify(c) + '\n\n');
    res.end('data: [DONE]\n\n');
  });
});
await new Promise(r => model.listen(0, '127.0.0.1', r));

// --- le projet et la config ---
const work = mkdtempSync(join(tmpdir(), 'lq-e2e-'));
const site = join(work, 'site');
execFileSync('mkdir', ['-p', site]);
writeFileSync(join(site, 'index.html'), PAGE);
writeFileSync(join(work, 'config.json'), JSON.stringify({
  providers: { custom: { type: 'custom', base_url: `http://127.0.0.1:${model.address().port}/v1`, models: [{ id: 'fake-model', label: 'fake' }], default_model: 'fake-model' } },
  default: { provider: 'custom', model: 'fake-model' }, sites: [], sites_dir: join(work, 'sites'),
}));
execFileSync('mkdir', ['-p', join(work, 'sites')]);

let lq, chrome;
function cleanup() {
  try { chrome?.kill(); } catch {} try { lq?.kill(); } catch {} model.close();
  // Chrome peut encore écrire dans son profil juste après kill() : on réessaie, et un échec de nettoyage n'est pas un échec du test
  try { rmSync(work, { recursive: true, force: true, maxRetries: 10, retryDelay: 300 }); } catch (e) { console.warn('nettoyage incomplet :', e.message); }
}
const stopChrome = () => new Promise(r => { if (!chrome || chrome.exitCode !== null) return r(); chrome.once('exit', r); chrome.kill(); setTimeout(r, 5000); });
const timer = setTimeout(() => fail('délai dépassé (90 s)'), 90_000);

lq = spawn('python3', [join(REPO, 'bin/livequill'), site, '--port', String(18000 + Math.floor(Math.random() * 1000)), '--no-open', '--config', join(work, 'config.json')],
  { env: { ...process.env, PYTHONUNBUFFERED: '1', LIVEQUILL_QUIET: '1' } });
let hubUrl;
const siteUrl = await new Promise((res, rej) => {
  let out = '';
  lq.stdout.on('data', d => {
    out += d;
    const h = out.match(/dashboard\s+: (http:\/\/127\.0\.0\.1:\d+\/)/); if (h) hubUrl = h[1];
    const m = out.match(/site\s+: (http:\/\/127\.0\.0\.1:\d+\/)/); if (m) res(m[1]);
  });
  lq.on('exit', c => rej(new Error('livequill s’est arrêté : ' + c + '\n' + out)));
}).catch(e => fail(e.message));

// --- le navigateur ---
const CDP = 19000 + Math.floor(Math.random() * 1000);
const browser = chromium();
console.log('navigateur :', browser);
chrome = spawn(browser, ['--headless=new', `--remote-debugging-port=${CDP}`, `--user-data-dir=${join(work, 'profile')}`, '--no-first-run',
  '--no-sandbox', '--window-size=1280,800', 'about:blank'], { stdio: 'ignore' });
let targets;
for (let i = 0; i < 160 && !targets; i++) { try { targets = await (await fetch(`http://127.0.0.1:${CDP}/json/list`)).json(); } catch { await sleep(250); } }
if (!targets) fail('Chromium ne répond pas');
const ws = new WebSocket(targets.find(t => t.type === 'page').webSocketDebuggerUrl);
await new Promise(r => ws.addEventListener('open', r));
let id = 0; const pending = new Map(); let loaded = 0;
ws.addEventListener('message', e => {
  const m = JSON.parse(e.data);
  if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); }
  if (m.method === 'Page.loadEventFired') loaded++;
});
const cdp = (method, params = {}) => new Promise(r => { const i = ++id; pending.set(i, r); ws.send(JSON.stringify({ id: i, method, params })); });
const js = async expr => (await cdp('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true })).result?.result?.value;
const until = async (what, cond, ms = 30_000) => { for (let t = 0; t < ms; t += 200) { if (await cond()) return; await sleep(200); } fail('attendu : ' + what); };
const bar = q => `document.getElementById('livequill')?.shadowRoot?.querySelector(${JSON.stringify(q)})`;
const h1 = () => js(`document.querySelector('h1')?.textContent`);

await cdp('Page.enable');
await cdp('Page.navigate', { url: siteUrl });
await until('la barre flottante', () => js(`!!${bar('textarea')}`));
await until('les modèles chargés', () => js(`!${bar('.send')}.disabled || ${bar('.dd-btn span')}.textContent !== 'Loading…'`));
console.log('✓ barre affichée sur', siteUrl);

// 1. demande → édition → rechargement automatique
await js(`(() => { const t = ${bar('textarea')}; t.focus(); t.value = 'change le titre'; t.dispatchEvent(new Event('input')); })()`);
const before = loaded;
for (const type of ['keyDown', 'keyUp']) await cdp('Input.dispatchKeyEvent', { type, key: 'Enter', code: 'Enter', windowsVirtualKeyCode: 13 });
await until('le rechargement après la modification', async () => loaded > before);
await until('le nouveau titre', async () => (await h1()) === 'Salut');
if (!readFileSync(join(site, 'index.html'), 'utf8').includes('Salut')) fail('le fichier n’a pas été modifié sur le disque');
if (calls.length !== 2) fail(`2 appels au modèle attendus, ${calls.length} reçus`);
console.log('✓ modification appliquée et page rechargée');

// 2. Annuler → rechargement → état d'origine
await until('le bouton Annuler actif', () => js(`${bar('.undo')} && !${bar('.undo')}.disabled`));
const beforeUndo = loaded;
await js(`${bar('.undo')}.click()`);
await until('le rechargement après l’annulation', async () => loaded > beforeUndo);
await until('le titre d’origine', async () => (await h1()) === 'Hello');
if (readFileSync(join(site, 'index.html'), 'utf8') !== PAGE) fail('le fichier n’est pas revenu à l’identique');
console.log('✓ annulation appliquée, fichier identique à l’original');

// 3. Nouvelle modification → Versions : aperçu vivant de l'état d'origine → « Revenir à cette version »
await until('la barre après rechargement', () => js(`!!${bar('textarea')} && ${bar('.dd-btn span')}.textContent !== 'Loading…'`));
await js(`(() => { const t = ${bar('textarea')}; t.focus(); t.value = 'change le titre'; t.dispatchEvent(new Event('input')); })()`);
const beforeSecond = loaded;
for (const type of ['keyDown', 'keyUp']) await cdp('Input.dispatchKeyEvent', { type, key: 'Enter', code: 'Enter', windowsVirtualKeyCode: 13 });
await until('le rechargement après la 2e modification', async () => loaded > beforeSecond);
await until('le titre modifié à nouveau', async () => (await h1()) === 'Salut');
await until('la barre prête', () => js(`!!${bar('.versbtn')}`));
await js(`${bar('.versbtn')}.click()`);
const versBtn = id => `[...document.getElementById('livequill').shadowRoot.querySelectorAll('.ver')].find(b => b.dataset.id === ${JSON.stringify(id)})`;
await until('la liste des versions', () => js(`!!${versBtn('origin')}`));
await js(`${versBtn('origin')}.click()`);
await until('l’aperçu vivant de la version d’origine', () =>
  js(`(() => { const f = ${bar('.pv iframe')}; return f && f.contentDocument && f.contentDocument.querySelector('h1')?.textContent === 'Hello'; })()`));
console.log('✓ aperçu de la version d’origine affiché sans rien restaurer');
if (!readFileSync(join(site, 'index.html'), 'utf8').includes('Salut')) fail('l’aperçu ne doit pas modifier le site');
await js('window.confirm = () => true');
const beforeRestore = loaded;
await js(`${bar('[data-restore]')}.click()`);
await until('le rechargement après le retour de version', async () => loaded > beforeRestore);
await until('le titre de la version d’origine', async () => (await h1()) === 'Hello');
if (readFileSync(join(site, 'index.html'), 'utf8') !== PAGE) fail('le retour de version n’a pas rétabli le fichier à l’identique');
console.log('✓ retour à la version d’origine');

// 4. Tableau de bord : l'assistant « New site » crée un site à partir du modèle d'entreprise
await cdp('Page.navigate', { url: hubUrl });
const q = s => `document.querySelector(${JSON.stringify(s)})`;
await until('le tableau de bord', () => js(`!!${q('#new')} && document.querySelectorAll('.scard').length === 1`));
await js(`${q('#new')}.click()`);
await until('le premier écran de l’assistant', () => js(`!!${q('[data-choice=create]')}`));
await js(`${q('[data-choice=create]')}.click()`);
await until('le choix du type de site', () => js(`!!${q('[data-choice=dynamic]')} && ${q('[data-choice=dynamic]')}.disabled`));
await js(`${q('#wz-foot [data-next]')}.click()`);
await until('les modèles de site', () => js(`!!${q('[data-choice=business]')}`));
await js(`${q('[data-choice=business]')}.click(); ${q('#wz-foot [data-next]')}.click()`);
await until('le champ du nom', () => js(`!!${q('#wz-name')}`));
await js(`(() => { const i = ${q('#wz-name')}; i.value = 'Acme Bakery'; i.dispatchEvent(new Event('input')); })()`);
await until('le dossier proposé', () => js(`(${q('#wz-where')}.textContent || '').endsWith('acme-bakery')`));
await js(`${q('#wz-foot [data-next]')}.click()`);
await until('l’écran « site prêt »', () => js(`!!${q('.done')}`));
const created = join(work, 'sites', 'acme-bakery', 'index.html');
if (!existsSync(created) || !readFileSync(created, 'utf8').includes('Acme Bakery')) fail('le site n’a pas été créé à partir du modèle');
await until('la carte du nouveau site', () => js(`document.querySelectorAll('.scard').length === 2`));
console.log('✓ nouveau site créé depuis le tableau de bord');

clearTimeout(timer);
ws.close();
await stopChrome();
cleanup();
console.log('✓ parcours navigateur réussi');
