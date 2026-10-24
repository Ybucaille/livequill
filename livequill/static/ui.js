/* LiveQuill : briques communes des pages (traduction, API, icônes, fenêtres flottantes, menu de langue, choix du dossier).
   Le serveur remplace les marqueurs __LQ_* par la langue choisie et son dictionnaire. */
(() => {
  const DICT = /*__LQ_DICT__*/{};
  const LANGS = /*__LQ_LANGS__*/[];
  const LANG = '__LQ_LANG__';
  const TOKEN = (document.querySelector('meta[name=lq-token]') || {}).content || '';
  document.documentElement.lang = LANG;

  // Textes écrits en anglais dans le code ; une autre langue fournit leur traduction (clé = texte anglais)
  const t = (s, vars) => (DICT[s] || s).replace(/\{(\w+)\}/g, (_, k) => (vars && k in vars ? vars[k] : `{${k}}`));
  const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const $ = (s, root = document) => root.querySelector(s);

  async function api(path, body) {
    const res = await fetch('/__lq/api/' + path, body === undefined ? { headers: { 'X-LQ-Token': TOKEN } } :
      { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-LQ-Token': TOKEN }, body: JSON.stringify(body) });
    const data = await res.json().catch(() => ({}));
    if (!res.ok || data.ok === false) { const e = new Error(data.message || data.error || 'HTTP ' + res.status); e.data = data; throw e; }
    return data;
  }

  const P = d => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${d}</svg>`;
  const ICONS = {
    folder: P('<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>'),
    site: P('<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 9h18M7 6.5h.01M10 6.5h.01"/>'),
    home: P('<path d="M3 11 12 4l9 7"/><path d="M5 10v10h14V10"/>'),
    desktop: P('<rect x="3" y="4" width="18" height="12" rx="2"/><path d="M8 20h8M12 16v4"/>'),
    docs: P('<path d="M7 3h7l5 5v13H7z"/><path d="M14 3v5h5"/>'),
    sparkle: P('<path d="M12 3v4M12 17v4M3 12h4M17 12h4M6 6l2.5 2.5M15.5 15.5 18 18M6 18l2.5-2.5M15.5 8.5 18 6"/>'),
    plus: P('<path d="M12 5v14M5 12h14"/>'),
    gear: P('<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/>'),
    globe: P('<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18"/>'),
    back: P('<path d="M15 18l-6-6 6-6"/>'),
    next: P('<path d="M9 18l6-6-6-6"/>'),
    up: P('<path d="M12 19V5M5 12l7-7 7 7"/>'),
    ext: P('<path d="M14 4h6v6M20 4l-9 9"/><path d="M19 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1h5"/>'),
    check: P('<path d="M5 12.5 10 17l9-10"/>'),
    edit: P('<path d="M4 20h4L19 9l-4-4L4 16z"/>'),
    stop: P('<rect x="6" y="6" width="12" height="12" rx="2"/>'),
    server: P('<rect x="3" y="4" width="18" height="7" rx="2"/><rect x="3" y="13" width="18" height="7" rx="2"/><path d="M7 7.5h.01M7 16.5h.01"/>'),
    open: P('<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v1"/><path d="M3 7v10a2 2 0 0 0 2 2h12.5a2 2 0 0 0 1.9-1.4L21.5 12H7.5L5 19"/>'),
  };
  const icon = k => ICONS[k] || '';
  const logo = '<svg viewBox="0 0 64 64" aria-hidden="true"><rect width="64" height="64" rx="16" fill="#d97757"/><path d="M44 12c-10 2-20 12-24 26l-4 14 8-6c12-4 22-14 24-26-4 2-8 3-12 3 3-3 6-7 8-11z" fill="#fff"/></svg>';

  /* --- Fenêtres flottantes : focus rendu à l'ouverture et à la fermeture, Échap ferme la plus haute --- */
  const stack = [];
  function openModal(overlay, onClose) {
    stack.push({ overlay, onClose, focus: document.activeElement });
    overlay.classList.add('open');
    document.body.style.overflow = 'hidden';
    requestAnimationFrame(() => { const f = overlay.querySelector('[autofocus]:not([hidden]), input:not([type=hidden]):not([disabled]), .modal button:not(.x)'); if (f) f.focus(); });
  }
  function closeModal(overlay) {
    const i = stack.findIndex(m => m.overlay === overlay);
    if (i < 0) return;
    const [m] = stack.splice(i, 1);
    overlay.classList.remove('open');
    if (!stack.length) document.body.style.overflow = '';
    if (m.focus && document.contains(m.focus)) m.focus.focus();
    if (m.onClose) m.onClose();
  }
  document.addEventListener('keydown', e => { if (e.key === 'Escape' && stack.length) { e.preventDefault(); closeModal(stack[stack.length - 1].overlay); } });
  document.addEventListener('mousedown', e => { if (stack.length && e.target === stack[stack.length - 1].overlay) closeModal(e.target); });

  /* --- Menu de langue (une seule langue pour l'instant : la liste s'allonge avec static/i18n/<code>.json) --- */
  function langMenu(host) {
    const cur = LANGS.find(l => l.code === LANG) || { code: 'en', name: 'English' };
    host.classList.add('lang');
    host.innerHTML = `<button type="button" class="pill" aria-haspopup="menu" aria-expanded="false" title="${esc(t('Language'))}">${icon('globe')}<span>${esc(cur.name)}</span></button>
      <div class="lang-menu" role="menu" hidden>${LANGS.map(l => `<button type="button" role="menuitemradio" aria-checked="${l.code === LANG}" data-lang="${esc(l.code)}">${esc(l.name)}${l.code === LANG ? '<span class="ck">✓</span>' : ''}</button>`).join('')}
        <div class="soon">${esc(t('More languages are coming.'))}</div></div>`;
    const btn = host.querySelector('.pill'), menu = host.querySelector('.lang-menu');
    const show = on => { menu.hidden = !on; btn.setAttribute('aria-expanded', on); };
    btn.addEventListener('click', () => show(menu.hidden));
    document.addEventListener('click', e => { if (!host.contains(e.target)) show(false); });
    host.addEventListener('keydown', e => { if (e.key === 'Escape' && !menu.hidden) { e.stopPropagation(); show(false); btn.focus(); } });
    menu.addEventListener('click', async e => {
      const b = e.target.closest('[data-lang]'); if (!b) return;
      show(false);
      if (b.dataset.lang === LANG) return;
      await api('settings', { lang: b.dataset.lang });
      location.reload();
    });
  }

  /* --- Aperçu d'une page à l'échelle de son cadre --- */
  function fit(shot) {
    const f = shot.querySelector('iframe'); if (!f) return;
    const go = () => { f.style.transform = `scale(${shot.clientWidth / 1280})`; };
    go(); new ResizeObserver(go).observe(shot);
  }

  /* --- Choix d'un dossier : raccourcis, fil d'Ariane cliquable, dossiers de sites mis en avant --- */
  let picker = null;
  function pickFolder({ title, lead, start, choose, want = 'site' } = {}) {
    if (!picker) {
      picker = document.createElement('div');
      picker.className = 'overlay top';
      picker.innerHTML = `<div class="modal wide picker" role="dialog" aria-modal="true" aria-labelledby="pk-title">
        <button type="button" class="x" aria-label="${esc(t('Close'))}">×</button>
        <h3 id="pk-title"></h3><p class="lead"></p>
        <div class="places"></div><div class="crumbs"></div><div class="list" role="list"></div>
        <div class="here"><span class="sp"></span><button type="button" class="b pri" data-choose></button></div>
        <div class="msg bad" role="status"></div></div>`;
      document.body.appendChild(picker);
    }
    const m = picker.querySelector('.modal');
    $('#pk-title', m).textContent = title || t('Choose a folder');
    $('.lead', m).textContent = lead || '';
    $('.lead', m).hidden = !lead;
    $('[data-choose]', m).textContent = choose || t('Choose this folder');
    let cur = null, done = null;
    const describe = r => {
      if (want === 'parent') return t('New sites will be created in this folder.');
      if (r.here.html) return '✓ ' + t('A website was found here (index.html).');
      if (r.here.empty) return t('This folder is empty. You can create a site in it.');
      if (r.here.node) return t('A JavaScript project (package.json). It can be served behind its dev server.');
      return t('No home page (index.html) in this folder.');
    };
    async function go(path) {
      try {
        const r = await api('browse?path=' + encodeURIComponent(path));
        cur = r;
        $('.msg', m).textContent = '';
        $('.places', m).innerHTML = r.places.map(p => `<button type="button" class="pill" data-go="${esc(p.path)}">${icon(p.icon)}${esc(t(p.name))}</button>`).join('');
        const parts = r.crumbs.map((c, i) => `${i ? '<span>/</span>' : ''}<button type="button" data-go="${esc(c.path)}">${esc(c.label ? t(c.name) : c.name)}</button>`);
        $('.crumbs', m).innerHTML = `<button type="button" data-go="${esc(r.parent || r.path)}" title="${esc(t('Parent folder'))}" aria-label="${esc(t('Parent folder'))}" ${r.parent ? '' : 'disabled'}>↑</button>` + parts.join('');
        $('.list', m).innerHTML = r.dirs.length ? r.dirs.map(d => `<button type="button" role="listitem" class="${d.site ? 'site' : ''}" data-go="${esc(r.path + (r.path.endsWith('/') ? '' : '/') + d.name)}">
            ${icon(d.site ? 'site' : 'folder')}<span class="name">${esc(d.name)}</span>${d.site ? `<span class="badge">${esc(t('Website'))}</span>` : ''}</button>`).join('')
          : `<div class="empty">${esc(t('No folders here.'))}</div>`;
        $('.here .sp', m).innerHTML = `<b title="${esc(r.path)}">${icon('folder')} ${esc(r.display === '~' ? t('Home') : r.display)}</b><small>${esc(describe(r))}</small>`;
        $('.list', m).scrollTop = 0;
      } catch (e) { $('.msg', m).textContent = e.message; }
    }
    m.onclick = e => {
      const b = e.target.closest('button'); if (!b) return;
      if (b.classList.contains('x')) return closeModal(picker);
      if (b.dataset.go) return go(b.dataset.go);
      if ('choose' in b.dataset && cur) { done = cur; closeModal(picker); }
    };
    return new Promise(resolve => {
      openModal(picker, () => resolve(done));
      go(start || '~');
    });
  }

  window.LQ = { t, esc, $, api, icon, logo, openModal, closeModal, langMenu, fit, pickFolder, TOKEN, LANG };
})();
