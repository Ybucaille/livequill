/* livequill : barre flottante ajoutée à la volée par le serveur, jamais écrite dans les fichiers du projet.
   Aucune clé API ici : le navigateur ne connaît que le jeton de session (data-lq-token). */
(() => {
  if (window.__livequill) return;
  window.__livequill = true;
  const TOKEN = document.currentScript && document.currentScript.dataset.lqToken;
  const MAX_FILE = 15 * 1024 * 1024;
  const PREFS_KEY = 'livequill-prefs', UI_KEY = 'livequill-ui';
  const load = (s, k, d) => { try { return JSON.parse(s.getItem(k)) || d; } catch (_) { return d; } };
  const save = (s, k, v) => { try { s.setItem(k, JSON.stringify(v)); } catch (_) {} };
  const prefs = Object.assign({ model: '', effort: {}, fast: false, skin: 'claude', mode: 'auto' }, load(localStorage, PREFS_KEY, {}));
  if (typeof prefs.effort !== 'object') prefs.effort = {};
  const ui = Object.assign({ panel: false }, load(sessionStorage, UI_KEY, {}));
  let state = null, files = [], job = null, es = null;

  const api = async (path, body) => {
    const res = await fetch('/__lq/api/' + path, body === undefined ? { headers: { 'X-LQ-Token': TOKEN } } : {
      method: 'POST', headers: { 'Content-Type': 'application/json', 'X-LQ-Token': TOKEN }, body: JSON.stringify(body)
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.message || data.error || 'HTTP ' + res.status);
    return data;
  };
  const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const icon = d => `<svg viewBox="0 0 24 24"><path d="${d}"/></svg>`;
  const I = {
    plus: 'M12 5v14M5 12h14', send: 'M12 19V5M5 12l7-7 7 7', stop: 'M7 7h10v10H7z', chev: 'm6 9 6 6 6-6', check: 'M20 6 9 17l-5-5',
    undo: 'M9 14 4 9l5-5M4 9h10.5a5.5 5.5 0 0 1 0 11H11', gear: 'M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z',
    log: 'M4 6h16M4 12h16M4 18h10', palette: 'M12 22a10 10 0 1 1 10-10c0 2.8-2.2 4-4 4h-1.7a1.8 1.8 0 0 0-1.3 3.1A1.6 1.6 0 0 1 12 22zM7.5 11.5h.01M10.5 7.5h.01M15 8h.01', fast: 'M13 2 4 14h7l-1 8 9-12h-7z', trash: 'M3 6h18M8 6V4h8v2M6 6l1 14h10l1-14', x: 'M18 6 6 18M6 6l12 12'
  };


  /* Thèmes de la barre : forme commune + couleurs claires/sombres. Ajouter un thème = ajouter une entrée ici. */
  const SYS = 'system-ui,-apple-system,"Segoe UI",Roboto,sans-serif';
  const THEMES = {
    claude: { label: 'Claude', swatch: '#d97757',
      shape: { font: SYS, size: '15px', radius: '16px', pad: '10px 10px 8px 14px', width: '660px', btn: '9px', 'send-size': '32px', 'send-radius': '9px', 'menu-radius': '12px', 'bubble-radius': '12px' },
      dark: { bg: '#262624', line: '#3a3a3a', ink: '#eceae4', ink2: '#dcd9d0', mute: '#8f8d86', soft: '#b9b6ad', hover: '#34332f', bubble: '#34332f', field: '#30302e',
        acc: '#d97757', 'acc-bg': '#d97757', 'acc-ink': '#fff', acc2: '#f0a37f', 'acc-soft': '#3a2c25', off: '#4a4945', bad: '#ff8a7a', thumb: '#4a4945', drag: '#2e2a27',
        shadow: '0 12px 40px rgba(0,0,0,.45)' },
      light: { bg: '#faf9f5', line: '#e3e0d6', ink: '#1f1e1d', ink2: '#3d3929', mute: '#8a877d', soft: '#5e5d59', hover: '#f0eee6', bubble: '#f0eee6', field: '#f3f1ea',
        acc: '#c6613f', 'acc-bg': '#d97757', 'acc-ink': '#fff', acc2: '#b5532f', 'acc-soft': '#f6e4dc', off: '#e6e3d9', bad: '#c4382a', thumb: '#d6d2c6', drag: '#f6ece6',
        shadow: '0 10px 32px rgba(40,30,20,.14)' } },
    chatgpt: { label: 'ChatGPT', swatch: '#0d0d0d',
      shape: { font: 'ui-sans-serif,-apple-system,"Segoe UI",Helvetica,Arial,sans-serif', size: '16px', radius: '28px', pad: '12px 12px 10px 18px', width: '700px', btn: '999px', 'send-size': '36px', 'send-radius': '50%', 'menu-radius': '16px', 'bubble-radius': '18px' },
      dark: { bg: '#303030', line: '#3d3d3d', ink: '#ececec', ink2: '#e3e3e3', mute: '#9b9b9b', soft: '#c4c4c4', hover: '#424242', bubble: '#424242', field: '#3a3a3a',
        acc: '#ececec', 'acc-bg': '#ffffff', 'acc-ink': '#000', acc2: '#ffffff', 'acc-soft': '#454545', off: '#4f4f4f', bad: '#ff8f84', thumb: '#555', drag: '#383838',
        shadow: '0 8px 30px rgba(0,0,0,.4)' },
      light: { bg: '#ffffff', line: '#e5e5e5', ink: '#0d0d0d', ink2: '#2f2f2f', mute: '#8f8f8f', soft: '#5d5d5d', hover: '#f3f3f3', bubble: '#f3f3f3', field: '#f4f4f4',
        acc: '#0d0d0d', 'acc-bg': '#000000', 'acc-ink': '#fff', acc2: '#0d0d0d', 'acc-soft': '#ececec', off: '#d7d7d7', bad: '#d1372a', thumb: '#d4d4d4', drag: '#f7f7f7',
        shadow: '0 4px 24px rgba(0,0,0,.10)' } },
    gemini: { label: 'Gemini', swatch: 'linear-gradient(135deg,#4285f4,#9b72cb,#d96570)',
      shape: { font: '"Google Sans",Roboto,' + SYS, size: '15.5px', radius: '32px', pad: '12px 14px 10px 20px', width: '700px', btn: '999px', 'send-size': '38px', 'send-radius': '50%', 'menu-radius': '18px', 'bubble-radius': '18px 4px 18px 18px' },
      dark: { bg: '#1e1f20', line: '#37393b', ink: '#e3e3e3', ink2: '#d5d7da', mute: '#8e918f', soft: '#c4c7c5', hover: '#2d2f31', bubble: '#2d2f31', field: '#282a2c',
        acc: '#a8c7fa', 'acc-bg': 'linear-gradient(135deg,#4285f4,#9b72cb 60%,#d96570)', 'acc-ink': '#fff', acc2: '#a8c7fa', 'acc-soft': '#1f3760', off: '#333537', bad: '#f2b8b5', thumb: '#444746', drag: '#242628',
        shadow: '0 10px 34px rgba(0,0,0,.5)' },
      light: { bg: '#f0f4f9', line: '#dde3ea', ink: '#1f1f1f', ink2: '#2f3133', mute: '#747775', soft: '#444746', hover: '#e1e7ef', bubble: '#e1e7ef', field: '#e9eef6',
        acc: '#0b57d0', 'acc-bg': 'linear-gradient(135deg,#4285f4,#9b72cb 60%,#d96570)', 'acc-ink': '#fff', acc2: '#0b57d0', 'acc-soft': '#d3e3fd', off: '#dde3ea', bad: '#b3261e', thumb: '#c4c7c5', drag: '#e6edf7',
        shadow: '0 6px 26px rgba(30,50,90,.14)' } },
    minimal: { label: 'Minimal', swatch: 'linear-gradient(135deg,#fff 50%,#111 50%)',
      shape: { font: 'Inter,' + SYS, size: '14.5px', radius: '10px', pad: '9px 9px 7px 12px', width: '620px', btn: '6px', 'send-size': '30px', 'send-radius': '6px', 'menu-radius': '8px', 'bubble-radius': '6px' },
      dark: { bg: '#111111', line: '#2a2a2a', ink: '#f5f5f5', ink2: '#d4d4d4', mute: '#7a7a7a', soft: '#a8a8a8', hover: '#1f1f1f', bubble: '#1f1f1f', field: '#1a1a1a',
        acc: '#f5f5f5', 'acc-bg': '#f5f5f5', 'acc-ink': '#111', acc2: '#f5f5f5', 'acc-soft': '#262626', off: '#2a2a2a', bad: '#f87171', thumb: '#333', drag: '#181818',
        shadow: '0 6px 24px rgba(0,0,0,.5)' },
      light: { bg: '#ffffff', line: '#dcdcdc', ink: '#111111', ink2: '#2b2b2b', mute: '#8a8a8a', soft: '#555555', hover: '#f2f2f2', bubble: '#f2f2f2', field: '#f6f6f6',
        acc: '#111111', 'acc-bg': '#111111', 'acc-ink': '#fff', acc2: '#111111', 'acc-soft': '#ededed', off: '#e2e2e2', bad: '#dc2626', thumb: '#d0d0d0', drag: '#f7f7f7',
        shadow: '0 4px 18px rgba(0,0,0,.10)' } },
  };
  const MODES = [['auto', 'Auto (système)'], ['light', 'Clair'], ['dark', 'Sombre']];
  const vars = o => Object.entries(o).map(([k, v]) => `--${k}:${v}`).join(';');
  const themeCss = () => Object.entries(THEMES).map(([id, t]) =>
    `:host([data-skin="${id}"]){${vars(t.shape)}}` +
    `:host([data-skin="${id}"][data-mode="dark"]){${vars(t.dark)};--status:${t.dark.soft};--status-shadow:0 1px 4px #000}` +
    `:host([data-skin="${id}"][data-mode="light"]){${vars(t.light)};--status:${t.light.soft};--status-shadow:0 0 6px #fff,0 0 2px #fff}`).join('\n');
  const darkMq = window.matchMedia ? matchMedia('(prefers-color-scheme: dark)') : null;

  const host = document.createElement('div');
  host.id = 'livequill';
  const root = host.attachShadow({ mode: 'open' });
  root.innerHTML = `<style>
    :host{all:initial}
    ${themeCss()}
    *{box-sizing:border-box}
    .box{position:fixed;left:50%;bottom:22px;z-index:2147483647;transform:translateX(-50%);width:min(var(--width),calc(100vw - 32px));
      display:flex;flex-direction:column;gap:6px;padding:var(--pad);border:1px solid var(--line);border-radius:var(--radius);
      background:var(--bg);box-shadow:var(--shadow);font:var(--size)/1.45 var(--font);color:var(--ink);transition:background .2s,border-color .2s}
    .box.drag{border-color:var(--acc);background:var(--drag)}
    textarea{width:100%;min-height:26px;max-height:160px;padding:4px 0;border:0;outline:0;resize:none;background:none;color:inherit;font:inherit}
    textarea::placeholder{color:var(--mute)}
    .files{display:flex;flex-wrap:wrap;gap:6px}.files:empty{display:none}
    .file{display:inline-flex;align-items:center;gap:6px;max-width:220px;padding:3px 4px;border:1px solid var(--line);border-radius:8px;background:var(--field);font-size:12px}
    .file img{width:26px;height:26px;object-fit:cover;border-radius:5px}
    .file span{overflow:hidden;white-space:nowrap;text-overflow:ellipsis}
    .file button{width:18px;height:18px;background:none;color:var(--mute)}
    .bar{display:flex;flex-wrap:wrap;align-items:center;gap:4px}
    .grow{flex:1}
    button{flex:none;display:grid;place-items:center;border:0;border-radius:var(--btn);background:none;color:var(--soft);cursor:pointer;font:inherit}
    button:hover{color:var(--ink);background:var(--hover)}
    button:disabled{opacity:.35;cursor:default;background:none}
    .icon{width:32px;height:32px}
    .icon.on{color:var(--acc2);background:var(--acc-soft)}
    .send{width:var(--send-size);height:var(--send-size);border-radius:var(--send-radius);background:var(--acc-bg);color:var(--acc-ink)}
    .send:hover{background:var(--acc-bg);color:var(--acc-ink);filter:brightness(1.12)}
    .send:disabled{opacity:1;background:var(--off);color:var(--mute);filter:none}
    .send.stop{background:var(--ink);color:var(--bg)}
    .dd{position:relative}.dd[hidden]{display:none}
    .dd-btn{height:30px;max-width:220px;padding:0 6px 0 9px;gap:4px;display:inline-flex;align-items:center;font-size:13px;white-space:nowrap}
    .dd-btn.icon{padding:0;max-width:none;width:32px;height:32px;display:grid}
    .dd-btn span{overflow:hidden;text-overflow:ellipsis}
    .dd-btn svg{width:14px;height:14px;transition:transform .15s}.dd-btn[aria-expanded="true"]{background:var(--hover);color:var(--ink)}.dd-btn:not(.icon)[aria-expanded="true"] svg{transform:rotate(180deg)}
    .dd-btn.icon svg{width:17px;height:17px}
    .menu{position:absolute;right:0;bottom:calc(100% + 8px);min-width:250px;max-width:min(380px,calc(100vw - 40px));max-height:min(420px,60vh);overflow-y:auto;overflow-x:hidden;overscroll-behavior:contain;padding:5px;border:1px solid var(--line);border-radius:var(--menu-radius);background:var(--bg);box-shadow:var(--shadow)}
    .menu.left{right:auto;left:-6px}
    .menu[hidden]{display:none}
    .menu,.log{scrollbar-width:thin;scrollbar-color:var(--thumb) transparent}
    .menu::-webkit-scrollbar,.log::-webkit-scrollbar{width:10px}
    .menu::-webkit-scrollbar-track,.log::-webkit-scrollbar-track{background:transparent}
    .menu::-webkit-scrollbar-thumb,.log::-webkit-scrollbar-thumb{background:var(--thumb);border-radius:10px;border:3px solid var(--bg)}
    .menu::-webkit-scrollbar-thumb:hover,.log::-webkit-scrollbar-thumb:hover{background:var(--mute)}
    .menu::-webkit-scrollbar-button,.log::-webkit-scrollbar-button{display:none}
    .menu h6{margin:8px 8px 4px;color:var(--mute);font:500 11px var(--font);letter-spacing:.04em;text-transform:uppercase}
    .menu h6:first-child{margin-top:4px}
    .opt{width:100%;display:flex;justify-content:space-between;align-items:center;gap:14px;padding:7px 9px;border-radius:8px;color:var(--ink);font-size:13px;text-align:left}
    .opt:hover,.opt:focus-visible{background:var(--hover);outline:0}
    .opt svg{width:14px;height:14px;color:var(--acc);visibility:hidden}.opt[aria-selected="true"] svg{visibility:visible}
    .opt>span{min-width:0;overflow:hidden;white-space:nowrap;text-overflow:ellipsis;display:flex;align-items:center;gap:9px}
    .opt small{color:var(--mute);font-size:11px;margin-left:-3px}
    .sw{flex:none;width:18px;height:18px;border-radius:50%;border:1px solid rgba(128,128,128,.35)}
    .menu .empty{padding:8px 9px;color:var(--mute);font-size:13px}
    .fast{height:30px;padding:0 9px;font-size:13px;gap:5px;display:inline-flex;align-items:center}
    .fast[hidden]{display:none}
    .fast[aria-pressed="true"]{color:var(--acc2);background:var(--acc-soft)}
    svg{width:17px;height:17px;fill:none;stroke:currentColor;stroke-width:2;stroke-linecap:round;stroke-linejoin:round}
    .fast svg{width:14px;height:14px}
    .status{position:absolute;left:16px;top:-24px;max-width:calc(100% - 32px);overflow:hidden;white-space:nowrap;text-overflow:ellipsis;font-size:12px;color:var(--status);text-shadow:var(--status-shadow)}
    .box.open .status{display:none}
    .status.err{color:var(--bad)}
    input[type=file]{display:none}
    .panel{display:none;flex-direction:column;margin:-2px -2px 4px -6px;border-bottom:1px solid var(--line);padding-bottom:6px}
    .box.open .panel{display:flex}
    .phead{display:flex;align-items:center;gap:4px;font-size:12px;color:var(--mute);padding:0 0 4px 4px}
    .phead b{flex:1;font-weight:500;letter-spacing:.04em;text-transform:uppercase;font-size:11px}
    .phead button{height:26px;padding:0 8px;font-size:12px;gap:5px;display:inline-flex;align-items:center}
    .phead button svg{width:14px;height:14px}
    .log{max-height:min(42vh,380px);overflow:auto;display:flex;flex-direction:column;gap:10px;padding:2px 6px 2px 4px;font-size:13.5px}
    .log:empty::before{content:"Aucune demande pour l’instant. Les réponses du modèle et les fichiers modifiés s’affichent ici.";color:var(--mute);font-size:13px}
    .turn{display:flex;flex-direction:column;gap:5px}
    .you{align-self:flex-end;max-width:85%;padding:6px 10px;border-radius:var(--bubble-radius);background:var(--bubble);white-space:pre-wrap;word-break:break-word}
    .you small{display:block;color:var(--mute);font-size:11px;margin-top:2px}
    .ans{white-space:pre-wrap;word-break:break-word;color:var(--ink2)}
    .ans.err{color:var(--bad)}
    .meta{font-size:11.5px;color:var(--mute)}
    .undone .ans,.undone .chips{text-decoration:line-through;opacity:.6}
    .steps{display:flex;flex-direction:column;gap:2px;font:12px/1.4 ui-monospace,SFMono-Regular,Menlo,monospace;color:var(--mute)}
    .steps .bad{color:var(--bad)}
    .chips{display:flex;flex-wrap:wrap;gap:5px}
    .chip{padding:1px 7px;border-radius:6px;background:var(--acc-soft);color:var(--acc2);font:12px ui-monospace,SFMono-Regular,Menlo,monospace}
    .think{color:var(--mute);font-size:12px;font-style:italic;white-space:pre-wrap;max-height:4.5em;overflow:hidden}
    .dots::after{content:"…";animation:b 1s steps(3) infinite}
    @keyframes b{0%{opacity:.2}50%{opacity:1}100%{opacity:.2}}
  </style>
  <div class="box">
    <span class="status" role="status"></span>
    <div class="panel">
      <div class="phead"><b>Conversation</b>
        <button type="button" class="clear" title="Oublier l’historique de ce projet">${icon(I.trash)}Nouvelle</button>
        <button type="button" class="close" aria-label="Replier">${icon(I.x)}</button></div>
      <div class="log" aria-live="polite"></div>
    </div>
    <div class="files"></div>
    <textarea rows="1" placeholder="Demande une modif…" aria-label="Demande de modification"></textarea>
    <div class="bar">
      <button class="icon attach" type="button" aria-label="Ajouter des pièces jointes" title="Pièces jointes (ou colle / glisse un fichier)">${icon(I.plus)}</button>
      <input type="file" multiple>
      <button class="icon panelbtn" type="button" aria-label="Afficher la conversation" title="Conversation et réponse en direct">${icon(I.log)}</button>
      <button class="icon undo" type="button" aria-label="Annuler la dernière modif" title="Annuler la dernière modif" disabled>${icon(I.undo)}</button>
      <button class="icon gear" type="button" aria-label="Réglages des fournisseurs" title="Fournisseurs et modèles">${icon(I.gear)}</button>
      <div class="dd" data-key="theme"><button class="dd-btn icon" type="button" aria-haspopup="listbox" aria-expanded="false" aria-label="Thème de la barre" title="Thème de la barre">${icon(I.palette)}</button><div class="menu left" role="listbox" hidden></div></div>
      <span class="grow"></span>
      <div class="dd" data-key="model"><button class="dd-btn" type="button" aria-haspopup="listbox" aria-expanded="false" aria-label="Modèle"><span>Chargement…</span>${icon(I.chev)}</button><div class="menu" role="listbox" hidden></div></div>
      <div class="dd" data-key="effort" hidden><button class="dd-btn" type="button" aria-haspopup="listbox" aria-expanded="false" aria-label="Réflexion"><span></span>${icon(I.chev)}</button><div class="menu" role="listbox" hidden></div></div>
      <button class="fast" type="button" aria-pressed="false" title="Mode fast (si le modèle le gère)" hidden>${icon(I.fast)}Fast</button>
      <button class="send" type="button" aria-label="Envoyer" disabled>${icon(I.send)}</button>
    </div>
  </div>`;

  const $ = s => root.querySelector(s);
  const box = $('.box'), ta = $('textarea'), send = $('.send'), status = $('.status'), list = $('.files'), picker = $('input[type=file]');
  const fast = $('.fast'), logEl = $('.log'), undoBtn = $('.undo');
  const dds = [...root.querySelectorAll('.dd')];
  const ddTheme = root.querySelector('.dd[data-key=theme]'), ddModel = root.querySelector('.dd[data-key=model]'), ddEffort = root.querySelector('.dd[data-key=effort]');

  const setStatus = (t, err) => { status.textContent = t || ''; status.classList.toggle('err', !!err); };
  const running = () => !!(job && !job.done);
  const refresh = () => {
    send.disabled = !running() && ((!ta.value.trim() && !files.length) || !current());
    send.classList.toggle('stop', running());
    send.innerHTML = icon(running() ? I.stop : I.send);
    send.setAttribute('aria-label', running() ? 'Arrêter' : 'Envoyer');
    undoBtn.disabled = running() || !(state && state.undo);
  };
  const grow = () => { ta.style.height = 'auto'; ta.style.height = ta.scrollHeight + 'px'; refresh(); };
  const setPanel = open => { ui.panel = open; box.classList.toggle('open', open); $('.panelbtn').classList.toggle('on', open); save(sessionStorage, UI_KEY, ui); if (open) logEl.scrollTop = logEl.scrollHeight; };

  /* --- modèles, réflexion, fast : selon les capacités du modèle choisi --- */
  const allModels = () => (state ? state.providers.flatMap(p => p.models.map(m => ({ ...m, pid: p.id, plabel: p.label, key: p.id + '::' + m.id }))) : []);
  const current = () => allModels().find(m => m.key === prefs.model);
  const pickDefault = () => {
    const ms = allModels();
    if (ms.some(m => m.key === prefs.model)) return;
    const d = state.default;
    prefs.model = (d && ms.some(m => m.key === d.provider + '::' + d.model)) ? d.provider + '::' + d.model : (ms[0] ? ms[0].key : '');
  };
  const optHtml = (v, l, sel, sub) => `<button class="opt" type="button" role="option" data-v="${esc(v)}" aria-selected="${sel}" title="${esc(l)}"><span>${esc(l)}${sub ? `<small>${esc(sub)}</small>` : ''}</span>${icon(I.check)}</button>`;
  const paint = () => {
    const m = current();
    ddModel.querySelector('.dd-btn span').textContent = m ? m.label : (state ? 'Aucun modèle' : 'Chargement…');
    const groups = state ? state.providers.filter(p => p.models.length) : [];
    ddModel.querySelector('.menu').innerHTML = groups.length
      ? groups.map(p => `<h6>${esc(p.label)}</h6>` + p.models.map(x => optHtml(p.id + '::' + x.id, x.label, p.id + '::' + x.id === prefs.model,
          [x.caps.vision ? 'vision' : '', x.caps.tools ? '' : 'mode texte'].filter(Boolean).join(' · '))).join('')).join('')
      : '<div class="empty">Aucun fournisseur : ouvre les réglages.</div>';
    const eff = m && m.caps.effort;
    ddEffort.hidden = !eff;
    if (eff) {
      let e = prefs.effort[m.key];
      if (!eff.some(([v]) => v === e)) e = (eff.find(([v]) => v === 'medium') || eff[Math.floor((eff.length - 1) / 2)])[0];
      prefs.effort[m.key] = e;
      ddEffort.querySelector('.dd-btn span').textContent = 'Réflexion : ' + eff.find(([v]) => v === e)[1];
      ddEffort.querySelector('.menu').innerHTML = '<h6>Réflexion</h6>' + eff.map(([v, l]) => optHtml(v, l, v === e)).join('');
    }
    fast.hidden = !(m && m.caps.fast);
    fast.setAttribute('aria-pressed', String(!!(m && m.caps.fast && prefs.fast)));
    ta.placeholder = m ? `Demande une modif à ${m.label}…` : 'Configure un fournisseur dans les réglages ⚙';
    refresh();
  };
  const applyTheme = () => {
    if (!THEMES[prefs.skin]) prefs.skin = 'claude';
    host.dataset.skin = prefs.skin;
    host.dataset.mode = prefs.mode === 'auto' ? (darkMq && !darkMq.matches ? 'light' : 'dark') : prefs.mode;
    ddTheme.querySelector('.menu').innerHTML = '<h6>Thème</h6>' + Object.entries(THEMES).map(([id, t]) =>
      `<button class="opt" type="button" role="option" data-v="skin:${id}" aria-selected="${id === prefs.skin}"><span><i class="sw" style="background:${t.swatch}"></i>${esc(t.label)}</span>${icon(I.check)}</button>`).join('') +
      '<h6>Apparence</h6>' + MODES.map(([v, l]) => optHtml('mode:' + v, l, v === prefs.mode)).join('');
  };
  if (darkMq) darkMq.addEventListener('change', () => { if (prefs.mode === 'auto') applyTheme(); });
  const closeMenus = except => dds.forEach(dd => { if (dd !== except) { dd.querySelector('.menu').hidden = true; dd.querySelector('.dd-btn').setAttribute('aria-expanded', 'false'); } });
  dds.forEach(dd => {
    const btn = dd.querySelector('.dd-btn'), menu = dd.querySelector('.menu');
    btn.addEventListener('click', () => {
      closeMenus(dd);
      menu.hidden = !menu.hidden; btn.setAttribute('aria-expanded', String(!menu.hidden));
      const opts = [...menu.querySelectorAll('.opt')];
      if (!menu.hidden && opts.length) (opts.find(o => o.getAttribute('aria-selected') === 'true') || opts[0]).focus();
    });
    menu.addEventListener('click', e => {
      const o = e.target.closest('.opt'); if (!o) return;
      if (dd === ddTheme) {
        const [k, v] = o.dataset.v.split(':');
        prefs[k] = v; save(localStorage, PREFS_KEY, prefs); applyTheme();
        dd.querySelector(`.opt[data-v="${o.dataset.v}"]`).focus();   // le menu reste ouvert pour comparer les thèmes
        return;
      }
      if (dd === ddModel) prefs.model = o.dataset.v; else prefs.effort[prefs.model] = o.dataset.v;
      save(localStorage, PREFS_KEY, prefs); paint(); closeMenus(); btn.focus();
    });
    menu.addEventListener('keydown', e => {
      const opts = [...menu.querySelectorAll('.opt')], i = opts.indexOf(root.activeElement);
      if (e.key === 'ArrowDown' || e.key === 'ArrowUp') { e.preventDefault(); opts[(i + (e.key === 'ArrowDown' ? 1 : -1) + opts.length) % opts.length].focus(); }
      else if (e.key === 'Escape') { closeMenus(); btn.focus(); }
    });
  });
  document.addEventListener('pointerdown', e => { if (!e.composedPath().some(n => dds.includes(n))) closeMenus(); });
  fast.addEventListener('click', () => { prefs.fast = !prefs.fast; save(localStorage, PREFS_KEY, prefs); paint(); });

  /* --- pièces jointes : bouton +, coller, glisser-déposer --- */
  const renderFiles = () => {
    list.innerHTML = files.map((f, i) => `<span class="file">${f.type.startsWith('image/') ? `<img src="${f.data}" alt="">` : ''}<span title="${esc(f.name)}">${esc(f.name)}</span><button type="button" data-i="${i}" aria-label="Retirer ${esc(f.name)}">×</button></span>`).join('');
    refresh();
  };
  const addFiles = fl => {
    for (const f of fl) {
      if (f.size > MAX_FILE) { setStatus(`${f.name} dépasse 15 Mo`, true); continue; }
      const r = new FileReader();
      r.onload = () => { files.push({ name: f.name || 'collage.png', type: f.type || 'application/octet-stream', size: f.size, data: r.result }); renderFiles(); };
      r.readAsDataURL(f);
    }
  };
  $('.attach').addEventListener('click', () => picker.click());
  picker.addEventListener('change', () => { addFiles(picker.files); picker.value = ''; });
  list.addEventListener('click', e => { const b = e.target.closest('button[data-i]'); if (b) { files.splice(+b.dataset.i, 1); renderFiles(); } });
  ta.addEventListener('paste', e => { const fl = [...e.clipboardData.files]; if (fl.length) { e.preventDefault(); addFiles(fl); } });
  box.addEventListener('dragover', e => { e.preventDefault(); box.classList.add('drag'); });
  box.addEventListener('dragleave', () => box.classList.remove('drag'));
  box.addEventListener('drop', e => { e.preventDefault(); box.classList.remove('drag'); addFiles(e.dataTransfer.files); });

  /* --- conversation et réponse en direct --- */
  const turnHtml = t => {
    const err = t.status === 'error';
    const meta = [t.model, t.status === 'undone' ? 'annulée' : t.status === 'cancelled' ? 'arrêtée' : ''].filter(Boolean).join(' · ');
    return `<div class="turn ${t.status === 'undone' ? 'undone' : ''}"><div class="you">${esc(t.prompt || '(pièces jointes)')}${t.attachments && t.attachments.length ? `<small>📎 ${esc(t.attachments.join(', '))}</small>` : ''}</div>
      <div class="ans ${err ? 'err' : ''}">${esc(t.answer || '')}</div>
      ${t.files && t.files.length ? `<div class="chips">${t.files.map(f => `<span class="chip">✎ ${esc(f)}</span>`).join('')}</div>` : ''}
      <div class="meta">${esc(meta)}</div></div>`;
  };
  const renderLog = () => {
    const turns = (state && state.conversation) || [];
    logEl.innerHTML = turns.filter(t => !job || t.job !== job.id).map(turnHtml).join('');
    if (job) logEl.appendChild(job.el);
    logEl.scrollTop = logEl.scrollHeight;
  };

  function follow(id, prompt) {
    const el = document.createElement('div');
    el.className = 'turn';
    el.innerHTML = `<div class="you">${esc(prompt || '…')}</div><div class="think" hidden></div><div class="steps"></div><div class="ans"></div><div class="chips"></div><div class="meta dots">En cours</div>`;
    job = { id, el, done: false, files: new Set(), answer: '' };
    const q = s => el.querySelector(s);
    renderLog(); refresh();
    const scroll = () => { logEl.scrollTop = logEl.scrollHeight; };
    if (es) es.close();
    es = new EventSource(`/__lq/api/jobs/${id}/events?t=${encodeURIComponent(TOKEN)}&from=0`);
    es.onmessage = m => {
      const ev = JSON.parse(m.data);
      if (ev.type === 'start') { if (!prompt && ev.prompt) q('.you').textContent = ev.prompt; q('.meta').textContent = `${ev.label} · ${ev.model}`; q('.meta').classList.add('dots'); setStatus(`${ev.model} travaille…`); }
      else if (ev.type === 'text') { job.answer += ev.text; q('.ans').textContent = job.answer.trim(); }
      else if (ev.type === 'thinking') { const t = q('.think'); t.hidden = false; t.textContent = (t.textContent + ev.text).slice(-400); }
      else if (ev.type === 'tool') { const d = document.createElement('div'); d.textContent = '› ' + ev.summary; q('.steps').appendChild(d); setStatus(ev.summary); }
      else if (ev.type === 'tool_result' && !ev.ok) { const d = document.createElement('div'); d.className = 'bad'; d.textContent = '  ✗ ' + ev.summary; q('.steps').appendChild(d); }
      else if (ev.type === 'info') { const d = document.createElement('div'); d.textContent = 'ℹ ' + ev.text; q('.steps').appendChild(d); }
      else if (ev.type === 'file') { job.files.add(ev.path); q('.chips').innerHTML = [...job.files].map(f => `<span class="chip">✎ ${esc(f)}</span>`).join(''); }
      else if (ev.type === 'error') { q('.ans').classList.add('err'); q('.ans').textContent = ev.message; setStatus(ev.message, true); }
      else if (ev.type === 'done') {
        es.close(); es = null; job.done = true; q('.meta').classList.remove('dots'); q('.think').hidden = true;
        if (ev.answer) q('.ans').textContent = ev.answer;
        if (state) state.undo = ev.undo;
        const n = (ev.files || []).length;
        q('.meta').textContent = ev.cancelled ? 'Arrêtée' : ev.error ? 'Échec' : n ? `${n} fichier(s) modifié(s) — rechargement…` : 'Aucun fichier modifié';
        if (!ev.error) setStatus(ev.cancelled ? 'Demande arrêtée' : n ? 'Modifié — rechargement…' : 'Terminé, rien de modifié');
        refresh();
      }
      scroll();
    };
    es.onerror = () => { if (job && job.done) { es.close(); es = null; } };
  }

  /* Aperçu de la page pour le modèle : titres visibles et texte sélectionné (le site n'est jamais lu ailleurs) */
  let lastSel = '';
  document.addEventListener('selectionchange', () => {
    const s = document.getSelection();
    if (s && s.rangeCount && !host.contains(s.anchorNode) && s.toString().trim()) lastSel = s.toString().trim().slice(0, 500);
  });
  const outline = () => [...document.querySelectorAll('h1,h2,h3')].filter(h => !host.contains(h) && h.getClientRects().length).slice(0, 20)
    .map(h => `${h.tagName.toLowerCase()}${h.id ? '#' + h.id : ''}${[...h.classList].slice(0, 3).map(c => '.' + c).join('')} "${h.textContent.trim().replace(/\s+/g, ' ').slice(0, 80)}"`).join('\n');

  async function submit() {
    if (running()) { try { await api(`jobs/${job.id}/cancel`, {}); } catch (_) {} return; }
    const text = ta.value.trim(), m = current();
    if ((!text && !files.length) || !m) return;
    send.disabled = true;
    try {
      const page = { pathname: location.pathname, search: location.search, hash: location.hash, title: document.title };
      const res = await api('prompt', { text, files, page, outline: outline(), selection: lastSel, provider: m.pid, model: m.id, effort: prefs.effort[m.key], fast: !!(m.caps.fast && prefs.fast) });
      ta.value = ''; files = []; lastSel = ''; renderFiles(); grow();
      setPanel(true);
      follow(res.job, text);
    } catch (e) { setStatus('Échec de l’envoi : ' + e.message, true); refresh(); }
  }

  undoBtn.addEventListener('click', async () => {
    try { const r = await api('undo', {}); setStatus(r.ok ? `Annulé : ${r.files.join(', ')} — rechargement…` : r.message, !r.ok); if (state) state.undo = r.undo || 0; refresh(); }
    catch (e) { setStatus('Annulation impossible : ' + e.message, true); }
  });
  $('.gear').addEventListener('click', () => { location.href = '/__setup?next=' + encodeURIComponent(location.pathname + location.search + location.hash); });
  $('.panelbtn').addEventListener('click', () => setPanel(!ui.panel));
  $('.close').addEventListener('click', () => setPanel(false));
  $('.clear').addEventListener('click', async () => {
    if (running()) return;
    try { await api('conversation/clear', {}); state.conversation = []; renderLog(); setStatus('Nouvelle conversation'); } catch (e) { setStatus(e.message, true); }
  });

  ta.addEventListener('input', grow);
  for (const t of ['keydown', 'keyup', 'keypress']) root.addEventListener(t, e => e.stopPropagation()); // pas de raccourcis du site pendant la saisie
  ta.addEventListener('keydown', e => { if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); if (!running()) submit(); } });
  send.addEventListener('click', submit);
  applyTheme();
  document.body.appendChild(host);
  setPanel(ui.panel);

  /* --- état initial, rechargement auto --- */
  (async () => {
    try {
      state = await api('state');
      pickDefault(); paint(); renderLog();
      if (state.running) { setPanel(true); const t = (state.conversation || []).find(x => x.job === state.running); follow(state.running, t ? t.prompt : ''); }
      else {
        const last = (state.conversation || []).slice(-1)[0];
        if (last && last.status === 'ok' && last.files && last.files.length) setStatus(`✓ ${last.answer || 'Modifié'}`.slice(0, 140));
      }
    } catch (e) { setStatus('livequill : ' + e.message, true); }
  })();
  const reload = new EventSource('/__lq/reload?t=' + encodeURIComponent(TOKEN));
  reload.onmessage = () => location.reload();
})();
