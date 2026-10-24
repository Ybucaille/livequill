#!/usr/bin/env node
// Records the README demo from a real run and takes the README screenshots.
// It starts LiveQuill with an empty config, then: setup → first provider → "My sites" → new site from the business
// template → open it → type a request → live answer → auto reload → versions preview → Undo → switch theme.
// Headless Chromium over the DevTools protocol, frames assembled with ffmpeg. No npm dependency.
//
//   node scripts/record-demo.mjs --home ~/demo-home --provider ollama --model qwen2.5:7b-instruct --out docs
//   node scripts/record-demo.mjs ... --shots-only      # screenshots only (no request to the model, no video)
//
// --home is used as $HOME for LiveQuill so the paths shown on screen stay short (~/LiveQuill/sites/…). Link your
// CLI credentials into it first if you record with a CLI agent (e.g. ln -s ~/.claude ~/.claude.json).
import { spawn, execFileSync } from 'node:child_process';
import { mkdtempSync, mkdirSync, writeFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const arg = (k, d) => { const i = process.argv.indexOf('--' + k); return i > 0 ? process.argv[i + 1] : d; };
const HOME = resolve(arg('home', tmpdir()));
const SITE = arg('site', 'Acme Bakery');                 // name typed in the new-site wizard
const PROVIDER = arg('provider', 'cli-claude');        // setup card to click
const MODEL = arg('model', 'haiku');                    // model to make default
const PROMPT = arg('prompt', 'make the hero title red');
const OUT = resolve(arg('out', 'docs'));
const SHOTS_ONLY = process.argv.includes('--shots-only');   // README screenshots only: no model call, no video
const PORT = +arg('port', 8790), CDP = 9333, W = 1280, H = 800;
const REPO = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const sleep = ms => new Promise(r => setTimeout(r, ms));

const work = mkdtempSync(join(tmpdir(), 'lq-rec-'));
const frames = join(work, 'frames');
mkdirSync(frames);
mkdirSync(join(OUT, 'images'), { recursive: true });

const lq = spawn('python3', [join(REPO, 'bin/livequill'), '--port', String(PORT), '--no-open', '--config', join(work, 'config.json')],
  { env: { ...process.env, HOME, LIVEQUILL_QUIET: '1' }, stdio: 'ignore' });
const chrome = spawn(arg('chromium', 'chromium'), ['--headless=new', `--remote-debugging-port=${CDP}`, `--user-data-dir=${join(work, 'profile')}`,
  `--window-size=${W},${H}`, '--hide-scrollbars', '--force-device-scale-factor=1', '--no-first-run', 'about:blank'], { stdio: 'ignore' });
const cleanup = () => { try { chrome.kill(); } catch {} try { lq.kill(); } catch {} };
process.on('exit', cleanup);

let targets;
for (let i = 0; i < 50 && !targets; i++) { try { targets = await (await fetch(`http://127.0.0.1:${CDP}/json/list`)).json(); } catch { await sleep(200); } }
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

// --- capture: every frame keeps its timestamp, ffmpeg replays real durations ---
const shots = [];
let casting = false;
handlers['Page.screencastFrame'] = p => {
  if (casting) {
    const f = join(frames, `${String(shots.length).padStart(5, '0')}.jpg`);
    writeFileSync(f, Buffer.from(p.data, 'base64'));
    shots.push({ f, t: p.metadata.timestamp });
  }
  cdp('Page.screencastFrameAck', { sessionId: p.sessionId });
};
let loaded = null;
handlers['Page.loadEventFired'] = () => { if (loaded) loaded(); };
const waitLoad = (ms = 15000) => new Promise(r => { loaded = r; setTimeout(r, ms); });
const cast = async on => { casting = on; if (on) await cdp('Page.startScreencast', { format: 'jpeg', quality: 88, maxWidth: W, maxHeight: H }).catch(() => {}); };
async function snap(name, clip) {
  const r = await cdp('Page.captureScreenshot', { format: name.endsWith('.png') ? 'png' : 'jpeg', quality: 90, ...(clip ? { clip: { ...clip, scale: 1 } } : {}) });
  writeFileSync(join(OUT, 'images', name), Buffer.from(r.result.data, 'base64'));
}

await cdp('Page.enable');
await cdp('Emulation.setDeviceMetricsOverride', { width: W, height: H, deviceScaleFactor: 1, mobile: false });
await cdp('Emulation.setEmulatedMedia', { features: [{ name: 'prefers-color-scheme', value: 'dark' }] });

// Visible pointer (headless draws none), re-injected after each navigation
const CURSOR = `(() => { if (document.getElementById('rec-cursor')) return;
  const c = document.createElement('div'); c.id = 'rec-cursor';
  c.innerHTML = '<svg width="22" height="22" viewBox="0 0 24 24"><path d="M4 2l16 9-7 2-3 7z" fill="#fff" stroke="#000" stroke-width="1.5" stroke-linejoin="round"/></svg>';
  Object.assign(c.style, { position: 'fixed', left: (window.__cx ?? ${W - 220}) + 'px', top: (window.__cy ?? ${H - 200}) + 'px', zIndex: 2147483647, pointerEvents: 'none',
    transition: 'left .55s cubic-bezier(.4,0,.2,1), top .55s cubic-bezier(.4,0,.2,1), transform .12s', filter: 'drop-shadow(0 2px 3px rgba(0,0,0,.5))' });
  document.documentElement.appendChild(c); })()`;
// Element lookup: `bar:` targets the floating bar's shadow DOM, `text:sel|word` the first sel containing word
const find = sel => {
  let q;
  if (sel.startsWith('bar:')) q = `document.getElementById('livequill').shadowRoot.querySelector(${JSON.stringify(sel.slice(4))})`;
  else if (sel.startsWith('text:')) {          // text:selector|word[|child]
    const [s, w, child] = sel.slice(5).split('|');
    q = `[...document.querySelectorAll(${JSON.stringify(s)})].find(e => e.textContent.includes(${JSON.stringify(w)}))` + (child ? `?.querySelector(${JSON.stringify(child)})` : '');
  }
  else q = `document.querySelector(${JSON.stringify(sel)})`;
  return js(`(() => { const e = ${q}; if (!e) return null; e.scrollIntoView({ block: 'nearest' }); const r = e.getBoundingClientRect();
    return { x: r.x + r.width / 2, y: r.y + r.height / 2 }; })()`);
};
async function waitFor(sel, ms = 30000) { for (let t = 0; t < ms; t += 250) { const b = await find(sel); if (b) return b; await sleep(250); } throw new Error('not found: ' + sel); }
async function point(x, y) { await js(CURSOR); await js(`window.__cx = ${x - 4}; window.__cy = ${y - 3}; Object.assign(document.getElementById('rec-cursor').style, { left: '${x - 4}px', top: '${y - 3}px' })`); }
async function click(sel) {
  let b = await waitFor(sel);
  await point(b.x, b.y); await sleep(650);
  b = await find(sel) || b;                       // re-measure: layout may have moved during the trip
  await point(b.x, b.y); await sleep(150);
  await js(`document.getElementById('rec-cursor').style.transform = 'scale(.82)'`);
  for (const type of ['mousePressed', 'mouseReleased']) await cdp('Input.dispatchMouseEvent', { type, x: b.x, y: b.y, button: 'left', clickCount: 1 });
  await sleep(140);
  await js(`document.getElementById('rec-cursor') && (document.getElementById('rec-cursor').style.transform = '')`);
}
async function type(text) { for (const ch of text) { await cdp('Input.insertText', { text: ch }); await sleep(45 + Math.random() * 45); } }
const key = async (k, code = k, vk = 0) => { for (const t of ['keyDown', 'keyUp']) await cdp('Input.dispatchKeyEvent', { type: t, key: k, code, windowsVirtualKeyCode: vk }); };
const base = `http://127.0.0.1:${PORT}`;
const tokenOf = async url => (await (await fetch(new URL('/', url))).text()).match(/lq-token" content="([^"]+)|data-lq-token="([^"]+)/).slice(1).find(Boolean);
const api = async (url, path, body) => (await fetch(new URL('/__lq/api/' + path, url), body === undefined
  ? { headers: { 'X-LQ-Token': await tokenOf(url) } }
  : { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-LQ-Token': await tokenOf(url) }, body: JSON.stringify(body) })).json();

for (let i = 0; i < 50; i++) { try { await fetch(base + '/__setup'); break; } catch { await sleep(200); } }

// 1. First launch: the dashboard sends you to the setup page
let l = waitLoad(); await cdp('Page.navigate', { url: base + '/' }); await l;
await sleep(2000); await js(CURSOR);
await snap('setup.jpg');
await cast(true);
await sleep(1500);
await click(`.pcard[data-kind="${PROVIDER}"]`);              // detected on this machine: the test runs by itself
const label = PROVIDER === 'cli-claude' ? MODEL[0].toUpperCase() + MODEL.slice(1) : MODEL;
await waitFor(`text:#pf-models .m|${label}`);
await sleep(900);
await click(`text:#pf-models .m|${label}|input[type=radio]`);
await sleep(500);
l = waitLoad();
await click('#pf-save');                                       // first provider saved → straight to "My sites"
await l; await sleep(1200); await js(CURSOR); await cast(true);

// 2. Create a site from the business template, then open it
await click('[data-wizard=create]');
await sleep(900);
await click('#wz-foot [data-next]');                           // static site
await waitFor('[data-choice=business] iframe');
await sleep(1200);
await click('[data-choice=business]');
await sleep(500);
await snap('wizard.jpg');
await click('#wz-foot [data-next]');
await click('#wz-name');
await type(SITE);
await sleep(1400);
await click('#wz-foot [data-next]');                           // create the site
await waitFor('[data-done=open]');
await sleep(1800);
await js(`window.open = () => null`);                          // open in the same tab for the recording
l = waitLoad();
await click('[data-done=open]');
await l; await sleep(1500); await js(CURSOR); await cast(true);
const site = (await api(base, 'sites')).sites[0].url;

if (!SHOTS_ONLY) {
  // 3. Ask for a change, watch the live panel, the page reloads by itself
  await click('bar:textarea');
  await type(PROMPT);
  await sleep(500);
  await key('Enter', 'Enter', 13);
  await sleep(1500);
  for (let i = 0; i < 600 && (await api(site, 'state')).running; i++) await sleep(500);
  l = waitLoad(8000); await l;
  await sleep(900); await js(CURSOR); await cast(true);
  await sleep(3200);
  await snap('hero.jpg');

  // 4. Versions: live preview of the site before the change, nothing restored
  await click('bar:[data-view=versions]');
  await click('bar:.ver[data-id=origin]');
  await sleep(2600);
  await click('bar:.histbtn');
  await sleep(600);

  // 5. Undo
  l = waitLoad(8000);
  await click('bar:.undo');
  await l; await sleep(900); await js(CURSOR); await cast(true);
  await sleep(2300);

  // 6. Another theme
  await click('bar:.dd[data-key=theme] .dd-btn');
  await sleep(500);
  await click('bar:.opt[data-v="skin:chatgpt"]');
  await sleep(500);
  await click('bar:.opt[data-v="mode:light"]');
  await sleep(1200);
  await key('Escape', 'Escape', 27);
  await point(W - 220, H / 2);
  await sleep(2600);
  await cast(false);
  await cdp('Page.stopScreencast');
}

// --- README screenshots (not part of the video) ---
const barClip = async () => js(`(() => { const r = document.getElementById('livequill').shadowRoot.querySelector('.box').getBoundingClientRect();
  return { x: Math.max(0, r.x - 40), y: Math.max(0, r.y - 30), width: Math.min(${W}, r.width + 80), height: r.height + 52 }; })()`);
await js(`document.getElementById('rec-cursor')?.remove()`);
const ollama = Object.entries((await api(site, 'kinds')).providers).find(([, p]) => p.type === 'ollama');   // update it rather than add a second one
await api(site, 'providers/save', { id: ollama ? ollama[0] : null, type: 'ollama', label: 'Ollama', make_default: true, default_model: 'qwen2.5:7b-instruct', models: [
  { id: 'qwen2.5:7b-instruct', label: 'qwen2.5:7b-instruct · 7.6B', meta: { capabilities: ['completion', 'tools'] }, shown: true },
  { id: 'llama3.2:3b', label: 'llama3.2:3b · 3.2B', meta: { capabilities: ['completion', 'tools'] }, shown: true }] });
await api(site, 'conversation/clear', {});
const themes = [];
for (const [skin, mode] of [['claude', 'dark'], ['chatgpt', 'light'], ['gemini', 'dark'], ['minimal', 'light']]) {
  await js(`localStorage.setItem('livequill-prefs', JSON.stringify({ ...JSON.parse(localStorage.getItem('livequill-prefs') || '{}'), skin: '${skin}', mode: '${mode}' }));
    sessionStorage.setItem('livequill-ui', '{"panel":false}')`);
  l = waitLoad(); await cdp('Page.reload'); await l; await sleep(1200);
  await js(`(() => { const t = document.getElementById('livequill').shadowRoot.querySelector('textarea'); t.value = ${JSON.stringify(PROMPT)}; t.dispatchEvent(new Event('input')); })()`);
  await sleep(400);
  const f = `theme-${skin}.png`; themes.push(join(OUT, 'images', f)); await snap(f, await barClip());
  if (skin === 'claude') {
    await js(`(() => { const t = document.getElementById('livequill').shadowRoot.querySelector('textarea'); t.value = ''; t.dispatchEvent(new Event('input')); })()`);
    await js(`document.getElementById('livequill').shadowRoot.querySelector('.dd[data-key=model] .dd-btn').click()`); await sleep(500);
    await snap('models.png', { x: W / 2 - 340, y: H - 470, width: 680, height: 470 });
    await js(`document.getElementById('livequill').shadowRoot.querySelector('.dd[data-key=theme] .dd-btn').click()`); await sleep(500);
    await snap('themes-menu.png', { x: W / 2 - 340, y: H - 380, width: 680, height: 380 });
    await key('Escape', 'Escape', 27);
  }
}
for (const [name, template] of [['Studio Portfolio', 'blank'], ['Spring Event', 'business']]) await api(base, 'sites/create', { name, template });
l = waitLoad(); await cdp('Page.navigate', { url: base + '/' }); await l; await sleep(2500);
await snap('hub.jpg');

ws.close(); cleanup();

// --- assemble: real durations between frames, then MP4 (H.264) and a light GIF ---
if (!SHOTS_ONLY) {
  const list = shots.map((s, i) => `file '${s.f}'\nduration ${Math.max(0.01, ((shots[i + 1]?.t ?? s.t + 1) - s.t)).toFixed(3)}`).join('\n') + `\nfile '${shots.at(-1).f}'\n`;
  writeFileSync(join(work, 'list.txt'), list);
  execFileSync('ffmpeg', ['-y', '-loglevel', 'error', '-f', 'concat', '-safe', '0', '-i', join(work, 'list.txt'), '-vf', 'fps=30,format=yuv420p',
    '-c:v', 'libx264', '-preset', 'slow', '-crf', '23', '-movflags', '+faststart', join(OUT, 'demo.mp4')]);
  execFileSync('ffmpeg', ['-y', '-loglevel', 'error', '-i', join(OUT, 'demo.mp4'), '-vf',
    'fps=8,scale=720:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=96:stats_mode=diff[p];[b][p]paletteuse=dither=bayer:bayer_scale=5:diff_mode=rectangle', join(OUT, 'demo.gif')]);
}
execFileSync('ffmpeg', ['-y', '-loglevel', 'error', ...themes.flatMap(t => ['-i', t]), '-filter_complex',
  themes.map((_, i) => `[${i}]scale=720:-1,pad=720:ih:(ow-iw)/2[t${i}]`).join(';') + ';' + themes.map((_, i) => `[t${i}]`).join('') + `vstack=inputs=${themes.length}`,
  join(OUT, 'images', 'themes.png')]);
for (const t of themes) rmSync(t);
rmSync(work, { recursive: true, force: true });
console.log(SHOTS_ONLY ? `screenshots → ${join(OUT, 'images')}` : `${shots.length} frames → ${join(OUT, 'demo.mp4')}, demo.gif, images/`);
