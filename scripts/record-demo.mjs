#!/usr/bin/env node
// Enregistre la vidéo de démo du README : une vraie demande, la réponse en direct, le rechargement, « Annuler », un thème.
// Chromium headless piloté par le protocole DevTools, images assemblées par ffmpeg. Aucune dépendance npm.
//
//   ./bin/livequill demo --port 8792 --no-open        (un fournisseur déjà configuré)
//   node scripts/record-demo.mjs --url http://127.0.0.1:8792/ --model cli-claude::haiku --out docs/demo
//
import { spawn, execFileSync } from 'node:child_process';
import { mkdtempSync, mkdirSync, writeFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const arg = (k, d) => { const i = process.argv.indexOf('--' + k); return i > 0 ? process.argv[i + 1] : d; };
const URL_ = arg('url', 'http://127.0.0.1:8792/');
const MODEL = arg('model', 'cli-claude::haiku');
const PROMPT = arg('prompt', 'make the main title red');
const OUT = arg('out', 'docs/demo');
const W = 1280, H = 800, PORT = 9333;
const sleep = ms => new Promise(r => setTimeout(r, ms));

const work = mkdtempSync(join(tmpdir(), 'lq-rec-'));
const frames = join(work, 'frames');
mkdirSync(frames);
const chrome = spawn(arg('chromium', 'chromium'), ['--headless=new', `--remote-debugging-port=${PORT}`, `--user-data-dir=${join(work, 'profile')}`,
  `--window-size=${W},${H}`, '--hide-scrollbars', '--force-device-scale-factor=1', '--no-first-run', 'about:blank'], { stdio: 'ignore' });

let targets;
for (let i = 0; i < 50 && !targets; i++) { try { targets = await (await fetch(`http://127.0.0.1:${PORT}/json/list`)).json(); } catch { await sleep(200); } }
const ws = new WebSocket(targets.find(t => t.type === 'page').webSocketDebuggerUrl);
await new Promise(r => ws.addEventListener('open', r));
let id = 0; const pending = new Map(), handlers = {};
ws.addEventListener('message', e => {
  const m = JSON.parse(e.data);
  if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); }
  else if (m.method && handlers[m.method]) handlers[m.method](m.params);
});
const cdp = (method, params = {}) => new Promise(r => { const i = ++id; pending.set(i, r); ws.send(JSON.stringify({ id: i, method, params })); });
const js = async expr => (await cdp('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true })).result?.result?.value;

// --- capture : chaque image garde son horodatage, ffmpeg respecte les durées réelles ---
const shots = [];
handlers['Page.screencastFrame'] = p => {
  const f = join(frames, `${String(shots.length).padStart(5, '0')}.jpg`);
  writeFileSync(f, Buffer.from(p.data, 'base64'));
  shots.push({ f, t: p.metadata.timestamp });
  cdp('Page.screencastFrameAck', { sessionId: p.sessionId });
};
let loaded = null;
handlers['Page.loadEventFired'] = () => { if (loaded) loaded(); };
const waitLoad = () => new Promise(r => { loaded = r; });
const startCast = () => cdp('Page.startScreencast', { format: 'jpeg', quality: 88, maxWidth: W, maxHeight: H, everyNthFrame: 1 });

await cdp('Page.enable');
await cdp('Emulation.setDeviceMetricsOverride', { width: W, height: H, deviceScaleFactor: 1, mobile: false });
await cdp('Emulation.setEmulatedMedia', { features: [{ name: 'prefers-color-scheme', value: 'dark' }] });

// pointeur visible (le headless n'en dessine pas) : réinjecté après chaque rechargement
const CURSOR = `(() => { if (document.getElementById('rec-cursor')) return;
  const c = document.createElement('div'); c.id = 'rec-cursor';
  c.innerHTML = '<svg width="22" height="22" viewBox="0 0 24 24"><path d="M4 2l16 9-7 2-3 7z" fill="#fff" stroke="#000" stroke-width="1.5" stroke-linejoin="round"/></svg>';
  Object.assign(c.style, { position: 'fixed', left: '${W - 160}px', top: '${H - 160}px', zIndex: 2147483647, pointerEvents: 'none',
    transition: 'left .55s cubic-bezier(.4,0,.2,1), top .55s cubic-bezier(.4,0,.2,1), transform .12s', filter: 'drop-shadow(0 2px 3px rgba(0,0,0,.5))' });
  document.documentElement.appendChild(c); })()`;
const box = sel => js(`(() => { const r = document.getElementById('livequill').shadowRoot.querySelector(${JSON.stringify(sel)}).getBoundingClientRect();
  return { x: r.x + r.width / 2, y: r.y + r.height / 2 }; })()`);
async function click(sel) {
  let { x, y } = await box(sel);
  await js(`Object.assign(document.getElementById('rec-cursor').style, { left: '${x - 4}px', top: '${y - 3}px' })`);
  await sleep(700);
  ({ x, y } = await box(sel));   // remesure : le thème ou le menu a pu bouger pendant le trajet
  await js(`Object.assign(document.getElementById('rec-cursor').style, { left: '${x - 4}px', top: '${y - 3}px' })`);
  await sleep(150);
  await js(`document.getElementById('rec-cursor').style.transform = 'scale(.82)'`);
  for (const type of ['mousePressed', 'mouseReleased']) await cdp('Input.dispatchMouseEvent', { type, x, y, button: 'left', clickCount: 1 });
  await sleep(120);
  await js(`document.getElementById('rec-cursor') && (document.getElementById('rec-cursor').style.transform = '')`);
}
const token = (await (await fetch(new URL('/__setup', URL_))).text()).match(/lq-token" content="([^"]+)/)[1];
const state = async () => (await fetch(new URL('/__lq/api/state', URL_), { headers: { 'X-LQ-Token': token } })).json();

// préparation : modèle choisi, thème Claude sombre, panneau replié
let l = waitLoad(); await cdp('Page.navigate', { url: URL_ }); await l;
await js(`localStorage.setItem('livequill-prefs', JSON.stringify({ model: ${JSON.stringify(MODEL)}, effort: {}, fast: false, skin: 'claude', mode: 'dark' }));
  sessionStorage.setItem('livequill-ui', JSON.stringify({ panel: false }))`);
l = waitLoad(); await cdp('Page.reload'); await l;
await sleep(1500); await js(CURSOR);
await startCast();
await sleep(1800);

// 1. on tape la demande, comme un humain
await click('textarea');
for (const ch of PROMPT) { await cdp('Input.insertText', { text: ch }); await sleep(55 + Math.random() * 50); }
await sleep(600);
await cdp('Input.dispatchKeyEvent', { type: 'keyDown', key: 'Enter', code: 'Enter', windowsVirtualKeyCode: 13 });
await cdp('Input.dispatchKeyEvent', { type: 'keyUp', key: 'Enter', code: 'Enter', windowsVirtualKeyCode: 13 });

// 2. le modèle travaille (réponse en direct dans le panneau), puis la page se recharge toute seule
await sleep(1500);
for (let i = 0; i < 600 && (await state()).running; i++) await sleep(500);
l = waitLoad(); await Promise.race([l, sleep(8000)]);
await sleep(800); await js(CURSOR); await startCast().catch(() => {});
await sleep(3500);

// 3. « Annuler la dernière modif »
await js(CURSOR);
l = waitLoad();
await click('.undo');
await Promise.race([l, sleep(6000)]);
await sleep(800); await js(CURSOR); await startCast().catch(() => {});
await sleep(2500);

// 4. un autre thème : ChatGPT clair
await click('.dd[data-key=theme] .dd-btn');
await sleep(500);
await click('.opt[data-v="skin:chatgpt"]');
await sleep(500);
await click('.opt[data-v="mode:light"]');
await sleep(1200);
for (const type of ['keyDown', 'keyUp']) await cdp('Input.dispatchKeyEvent', { type, key: 'Escape', code: 'Escape', windowsVirtualKeyCode: 27 });
await js(`Object.assign(document.getElementById('rec-cursor').style, { left: '${W - 200}px', top: '${H / 2}px' })`);
await sleep(2800);

await cdp('Page.stopScreencast');
ws.close(); chrome.kill();

// --- assemblage : durées réelles entre images, puis MP4 (H.264) et GIF léger ---
const list = shots.map((s, i) => `file '${s.f}'\nduration ${Math.max(0.01, ((shots[i + 1]?.t ?? s.t + 1) - s.t)).toFixed(3)}`).join('\n') + `\nfile '${shots.at(-1).f}'\n`;
writeFileSync(join(work, 'list.txt'), list);
execFileSync('ffmpeg', ['-y', '-loglevel', 'error', '-f', 'concat', '-safe', '0', '-i', join(work, 'list.txt'), '-vf', 'fps=30,format=yuv420p',
  '-c:v', 'libx264', '-preset', 'slow', '-crf', '23', '-movflags', '+faststart', `${OUT}.mp4`]);
execFileSync('ffmpeg', ['-y', '-loglevel', 'error', '-i', `${OUT}.mp4`, '-vf',
  'fps=10,scale=720:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=96:stats_mode=diff[p];[b][p]paletteuse=dither=bayer:bayer_scale=5:diff_mode=rectangle', `${OUT}.gif`]);
rmSync(work, { recursive: true, force: true });
console.log(`${shots.length} images → ${OUT}.mp4, ${OUT}.gif`);
