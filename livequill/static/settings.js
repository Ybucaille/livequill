/* LiveQuill : le panneau de réglages (modèles, modèle par défaut, langue, dossier des nouveaux sites), commun au tableau
   de bord et aux sites, où la barre l'ouvre par-dessus la page dans un cadre de la même origine.
   LQ.settings.open({ tab, onChange, onClose }) */
(() => {
  const { t, esc, $, api, openModal, closeModal, pickFolder } = window.LQ;
  let el = null, opts = {}, mounted = false, settings = null, changed = false;

  function build() {
    el = document.createElement('div');
    el.className = 'overlay';
    el.innerHTML = `<div class="modal wide" role="dialog" aria-modal="true" aria-labelledby="st-title">
      <button type="button" class="x" data-st-close aria-label="${esc(t('Close'))}">×</button>
      <h3 id="st-title">${esc(t('Settings'))}</h3>
      <div class="tabs" role="tablist">
        <button type="button" role="tab" data-tab="providers">${esc(t('Models'))}</button>
        <button type="button" role="tab" data-tab="general">${esc(t('General'))}</button>
      </div>
      <div class="sect" data-pane="providers">
        <h4>${esc(t('On your computer'))}</h4><div class="pgrid three" data-group="local"></div>
        <h4>${esc(t('In the cloud'))}</h4><div class="pgrid three" data-group="cloud"></div>
        <h4>${esc(t('Coding agents'))}</h4><div class="pgrid three" data-group="cli"></div>
      </div>
      <div class="sect" data-pane="general" hidden>
        <label for="st-default">${esc(t('Default model in the bar'))}</label>
        <select id="st-default"></select>
        <label for="st-lang">${esc(t('Language'))}</label>
        <select id="st-lang"></select>
        <div id="st-dir-row"><label>${esc(t('Folder for new sites'))}</label>
          <div class="where"><span id="st-dir"></span><button type="button" class="b sm" id="st-dir-change">${esc(t('Change…'))}</button></div></div>
        <h4>${esc(t('About'))}</h4>
        <div class="kv"><span>${esc(t('Version'))}</span><span id="st-version"></span><span>${esc(t('Settings file'))}</span><code id="st-config"></code></div>
        <span class="msg" id="st-msg" role="status"></span>
      </div></div>`;
    document.body.appendChild(el);
    el.addEventListener('click', e => {
      if (e.target.closest('[data-st-close]')) return closeModal(el);
      const b = e.target.closest('[data-tab]'); if (b) show(b.dataset.tab);
    });
    $('#st-default').addEventListener('change', async e => {
      const [provider, ...rest] = e.target.value.split('::');
      await api('default', { provider, model: rest.join('::') });
      saved(t('Default model saved'));
    });
    $('#st-lang').addEventListener('change', async e => {
      await api('settings', { lang: e.target.value });
      (opts.onLang || (() => location.reload()))();
    });
    $('#st-dir-change').addEventListener('click', async () => {
      const r = await pickFolder({ title: t('Folder for new sites'), lead: t('New sites are created in this folder, each in its own subfolder.'),
        start: settings.sites_dir, choose: t('Use this folder'), want: 'parent' });
      if (!r) return;
      try { await api('settings', { sites_dir: r.path }); await fill(); saved(t('Folder saved')); }
      catch (err) { $('#st-msg').className = 'msg bad'; $('#st-msg').textContent = t(err.message); }
    });
  }

  function saved(m) {
    changed = true;
    if (opts.onChange) opts.onChange();
    $('#st-msg').className = 'msg ok'; $('#st-msg').textContent = '✓ ' + m;
    setTimeout(() => { $('#st-msg').textContent = ''; }, 2500);
  }

  function show(tab) {
    el.querySelectorAll('[data-tab]').forEach(b => b.setAttribute('aria-selected', b.dataset.tab === tab));
    el.querySelectorAll('[data-pane]').forEach(p => { p.hidden = p.dataset.pane !== tab; });
  }

  async function fill() {
    settings = await api('settings');
    const k = await api('kinds');
    const items = Object.entries(k.providers).flatMap(([pid, p]) => (p.models || []).filter(m => m.shown !== false)
      .map(m => `<option value="${esc(pid)}::${esc(m.id)}" ${k.default && k.default.provider === pid && k.default.model === m.id ? 'selected' : ''}>${esc(m.label || m.id)} · ${esc(p.label || t(k.kinds[p.type].label))}</option>`));
    $('#st-default').innerHTML = items.join('') || `<option>${esc(t('Add a model first'))}</option>`;
    $('#st-lang').innerHTML = settings.langs.map(l => `<option value="${esc(l.code)}" ${l.code === settings.lang ? 'selected' : ''}>${esc(l.name)}</option>`).join('');
    $('#st-dir-row').hidden = !settings.sites_dir;                // dossier des nouveaux sites : réglage du tableau de bord
    if (settings.sites_dir) { $('#st-dir').textContent = settings.sites_dir_display; $('#st-dir').title = settings.sites_dir; }
    $('#st-version').textContent = settings.version;
    $('#st-config').textContent = settings.config_path;
  }

  async function open(o = {}) {
    opts = o; changed = false;
    if (!el) build();
    show(o.tab || 'providers');
    openModal(el, () => { if (opts.onClose) opts.onClose(changed); });
    if (!mounted) {
      mounted = true;
      const grids = Object.fromEntries([...el.querySelectorAll('.pgrid')].map(g => [g.dataset.group, g]));
      await LQ.providers.mount({ grids, onSaved: () => { fill(); saved(t('Model saved')); } });
    } else LQ.providers.refresh();
    fill();
  }

  window.LQ.settings = { open };
})();
