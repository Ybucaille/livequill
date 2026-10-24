/* LiveQuill : cartes des fournisseurs et carte flottante de configuration, communes au premier lancement et aux réglages.
   LQ.providers.mount({ grids: { local, cloud, cli }, onSaved(first) }) */
(() => {
  const { t, esc, $, api, openModal, closeModal } = window.LQ;
  const BASES = { ollama: 'http://127.0.0.1:11434', lmstudio: 'http://127.0.0.1:1234/v1', llamacpp: 'http://127.0.0.1:8080/v1', custom: 'http://127.0.0.1:8000/v1' };
  const GROUP = { local: 'On your computer', cloud: 'In the cloud', cli: 'Coding agent' };
  const COLOR = { local: 'local', cloud: 'cloudc', cli: 'cli' };
  let K = {}, P = {}, D = null, det = null, status = {}, edit = null, models = [], grids = {}, onSaved = null, overlay = null;
  const extLink = d => d.url ? `<a class="ext" href="${esc(d.url)}" target="_blank" rel="noopener noreferrer">${esc(t(d.url_label || 'Website'))} ↗</a>` : '';

  function build() {
    overlay = document.createElement('div');
    overlay.className = 'overlay top';
    overlay.innerHTML = `<form class="modal pform" autocomplete="off" role="dialog" aria-modal="true" aria-labelledby="pf-title">
      <button type="button" class="x" data-close aria-label="${esc(t('Close'))}">×</button>
      <span class="tag" id="pf-kind"></span>
      <h3 id="pf-title"></h3>
      <div class="note" id="pf-howto"></div>
      <label for="pf-label">${esc(t('Display name'))}</label>
      <input type="text" id="pf-label" placeholder="${esc(t('e.g. Ollama on my laptop'))}">
      <div id="pf-fields"></div>
      <details id="pf-adv"><summary>${esc(t('Advanced'))}</summary>
        <label for="pf-toolmode">${esc(t('Tool calls'))}</label>
        <select id="pf-toolmode"><option value="">${esc(t('Automatic (native tool calls when the model supports them)'))}</option><option value="text">${esc(t('Text mode (file and diff blocks, for models without tool calls)'))}</option></select>
        <div class="check"><input type="checkbox" id="pf-vision"><label for="pf-vision">${esc(t('Force vision (send images to the model)'))}</label></div>
      </details>
      <div class="row"><button type="button" class="b" id="pf-test">${esc(t('Test the connection'))}</button><span class="msg" id="pf-msg" role="status"></span></div>
      <input type="search" id="pf-filter" placeholder="${esc(t('Filter models…'))}" hidden style="margin-top:12px">
      <div class="models" id="pf-models" hidden></div>
      <div class="check"><input type="checkbox" id="pf-default"><label for="pf-default">${esc(t('Default provider in the bar'))}</label></div>
      <div class="row">
        <button type="submit" class="b pri" id="pf-save" disabled>${esc(t('Save'))}</button>
        <button type="button" class="b" data-close>${esc(t('Close'))}</button>
        <span class="sp"></span>
        <button type="button" class="b danger" id="pf-delete" hidden>${esc(t('Delete'))}</button>
      </div></form>`;
    document.body.appendChild(overlay);
    const form = $('form', overlay);
    form.addEventListener('click', e => { if (e.target.closest('[data-close]')) closeModal(overlay); });
    $('#pf-models').addEventListener('change', e => {
      const i = +e.target.dataset.i;
      if (e.target.type === 'checkbox') models[i].shown = e.target.checked;
      else { edit.default = models[i].id; models[i].shown = true; }
      renderModels();
    });
    $('#pf-filter').addEventListener('input', renderModels);
    $('#pf-test').addEventListener('click', test);
    form.addEventListener('submit', save);
    $('#pf-delete').addEventListener('click', async () => {
      if (!edit.id || !confirm(t('Delete this provider (and its key)?'))) return;
      await api('providers/delete', { id: edit.id });
      closeModal(overlay); refresh();
    });
  }

  async function refresh(recheck = true) {
    const r = await api('kinds');
    K = r.kinds; P = r.providers; D = r.default;
    if (recheck) status = {};
    render();
    if (recheck && Object.keys(P).length) { status = await api('status').catch(() => ({})); render(); }
    return r;
  }

  /* État d'une carte : vérifiée (disponible / erreur), détectée sur la machine, ou à configurer */
  function detected(kind) {
    if (!det) return null;
    if (kind === 'ollama') return det.ollama.up ? t('Ollama is running here with {n} model(s). Click to enable it.', { n: det.ollama.models.length }) : null;
    if (kind === 'lmstudio' || kind === 'llamacpp') return det[kind].up ? t('Server found on this computer. Click to enable it.') : null;
    if (kind.startsWith('cli-')) {
      const a = kind.slice(4), via = (det.cli_via || {})[a];
      if (!det.cli[a]) return null;
      return via ? t('Found in the {app} app, already signed in to your account. Click to enable it.', { app: via }) : t('Installed on this computer. Click to enable it.');
    }
    if (K[kind].env && det.env[kind]) return t('Key found in {env}. Click to enable it.', { env: K[kind].env });
    return null;
  }
  function idle(kind) {
    const d = K[kind];
    if (kind === 'custom') return t('Any OpenAI-compatible server: vLLM, LocalAI, Jan…');
    if (d.group === 'cloud') return t('Create a key on the provider’s website, then click here.');
    if (d.group === 'local') return t('Install it and start its server, then click here.');
    return kind === 'cli-claude' ? t('Install the CLI or the Claude Desktop app, then click here.')
      : kind === 'cli-codex' ? t('Install the CLI or the ChatGPT app, then click here.') : t('Install the CLI and sign in, then click here.');
  }

  function cardHtml(kind, pid) {
    const d = K[kind], p = pid ? P[pid] : null;
    let st, label, text;
    if (p) {
      const s = status[pid], n = (p.models || []).filter(m => m.shown !== false).length;
      if (!s) { st = 'check'; label = t('Checking…'); text = t('Connecting to the provider.'); }
      else if (s.ok) { st = 'ok'; label = t('Available'); text = t('{n} model(s) in the bar · default: {model}', { n, model: p.default_model }); }
      else { st = 'bad'; label = t('Cannot connect'); text = t(s.message); }
    } else {
      const found = detected(kind);
      st = found ? 'found' : 'idle';
      label = found ? t('Detected') : t({ cloud: 'Not set up', local: 'Not detected', cli: 'Not installed' }[d.group]);
      if (kind === 'custom') label = t('Add');
      text = found || idle(kind);
    }
    const title = p ? (p.label || d.label) : t(d.label);
    return `<div class="pcard st-${st} ${kind === 'custom' && !p ? 'add' : ''} ${edit && edit.type === kind && edit.id === (pid || null) ? 'sel' : ''}" role="button" tabindex="0"
        data-kind="${kind}" data-pid="${esc(pid || '')}" aria-label="${esc(title)}: ${esc(label)}">
      <div class="kh"><b>${esc(title)}</b><span class="dot" aria-hidden="true"></span></div>
      <div class="state">${esc(label)}</div>
      <small>${esc(text)}</small>
      ${!p && st === 'idle' ? extLink(d) : ''}
      ${p && D && D.provider === pid ? `<span class="pbadge">${esc(t('default'))}</span>` : ''}</div>`;
  }

  function render() {
    for (const [g, grid] of Object.entries(grids)) {
      if (!grid) continue;
      let html = '';
      for (const [k, d] of Object.entries(K)) {
        if (d.group !== g) continue;
        const pids = Object.keys(P).filter(id => P[id].type === k);
        html += pids.map(id => cardHtml(k, id)).join('');
        if (!pids.length || d.multi) html += cardHtml(k, null);   // « Autre » reste toujours proposé
      }
      grid.innerHTML = html;
    }
  }

  const field = (id, label, type, value, ph) =>
    `<label for="${id}">${esc(label)}</label><input type="${type}" id="${id}" value="${esc(value || '')}" placeholder="${esc(ph || '')}" spellcheck="false">`;

  function howto(type) {
    const d = K[type];
    if (d.group === 'cloud') return t('1. {link} and sign in.<br>2. Create a key, paste it below, then test the connection.',
      { link: d.url ? `<a href="${esc(d.url)}" target="_blank" rel="noopener noreferrer">${esc(t('Open the {name} keys page', { name: d.label }))} ↗</a>` : '' });
    if (d.group === 'cli') {
      const via = det && (det.cli_via || {})[type.slice(4)];
      if (via) return `${esc(t(d.help))}.<br>` + t('LiveQuill uses the agent built into the <b>{app}</b> app, with the app’s account. Not signed in yet? Open the {app} app and sign in.', { app: esc(via) });
      return `${esc(t(d.help))}.<br>` + t('Not signed in yet? Run {cmd} in a terminal.', { cmd: `<code>${esc(d.login)}</code>` }) + ' ' + extLink(d);
    }
    return `${esc(t(d.help))}. ${extLink(d)}`;
  }

  function openForm(type, id) {
    const d = K[type], p = id ? P[id] : {};
    edit = { type, id: id || null };
    models = id ? (p.models || []).map(m => ({ ...m, shown: m.shown !== false })) : [];
    edit.default = id ? p.default_model : null;
    $('#pf-kind').textContent = t(GROUP[d.group]);
    $('#pf-kind').style.color = `var(--${COLOR[d.group]})`;
    $('#pf-title').textContent = id ? t('Edit · {name}', { name: p.label || d.label }) : t(d.label) + (type === 'custom' ? ' · ' + t('OpenAI-compatible') : '');
    $('#pf-howto').innerHTML = howto(type);
    $('#pf-label').value = p.label || '';
    $('#pf-label').placeholder = t('e.g. {name} on my laptop', { name: t(d.label) });
    let f = '';
    if (d.fields.includes('base_url')) f += field('pf-base', t('Base URL'), 'url', p.base_url, BASES[type] || '');
    if (d.fields.includes('key')) {
      f += field('pf-key', type === 'custom' ? t('API key (optional)') : t('API key'), 'password', '', p.has_key ? t('•••••••• saved, leave empty to keep it') : t('paste your key here'));
      if (d.env && det && det.env[type]) f += `<div class="check"><input type="checkbox" id="pf-env" ${p.api_key_env || !id ? 'checked' : ''}><label for="pf-env">${t('Use the {env} environment variable', { env: `<code>${esc(d.env)}</code>` })}</label></div>`;
    }
    if (d.fields.includes('model')) f += field('pf-model', t('Model name (if the server does not list its models)'), 'text', p.model, t('e.g. llama-3.3-70b'));
    if (d.fields.includes('num_ctx')) f += field('pf-ctx', t('Context size (num_ctx)'), 'number', p.num_ctx || 16384, '16384');
    if (d.fields.includes('extra_models')) f += field('pf-extra', t('Extra models (comma separated, optional)'), 'text', p.extra_models, type === 'cli-codex' ? t('e.g. gpt-5.5, o4-mini') : t('e.g. claude-opus-5-5'));
    $('#pf-fields').innerHTML = f;
    $('#pf-adv').hidden = d.group === 'cli';
    $('#pf-adv').open = false;
    $('#pf-toolmode').value = p.tool_mode || '';
    $('#pf-vision').checked = !!(p.caps && p.caps.vision);
    $('#pf-default').checked = !Object.keys(P).length || (D && D.provider === id);
    $('#pf-delete').hidden = !id;
    $('#pf-msg').textContent = ''; $('#pf-msg').className = 'msg';
    $('#pf-filter').value = '';
    openModal(overlay, () => { edit = null; render(); });
    renderModels(); render();
    $('form', overlay).scrollTop = 0;
    // Détecté sur la machine (ou déjà configuré) : on teste tout de suite, un clic de moins
    if (id || detected(type)) test(); else ($('#pf-key') || $('#pf-base') || $('#pf-label')).focus();
  }

  function formBody() {
    const v = s => { const el = $(s); return el ? el.value.trim() : ''; };
    const b = { id: edit.id, type: edit.type, label: v('#pf-label'), base_url: v('#pf-base'), api_key: v('#pf-key'), model: v('#pf-model'),
      extra_models: v('#pf-extra'), num_ctx: v('#pf-ctx') ? +v('#pf-ctx') : '', tool_mode: $('#pf-toolmode').value };
    if ($('#pf-env') && $('#pf-env').checked) { b.api_key_env = K[edit.type].env; b.api_key = ''; }
    if ($('#pf-vision').checked) b.caps = { vision: true };
    return b;
  }

  function renderModels() {
    const box = $('#pf-models'), q = $('#pf-filter').value.toLowerCase();
    $('#pf-save').disabled = !models.length;
    box.hidden = !models.length; $('#pf-filter').hidden = models.length < 12;
    if (!edit) return;
    if (!edit.default || !models.some(m => m.id === edit.default)) edit.default = (models.find(m => m.shown) || models[0] || {}).id;
    const caps = m => [m.caps && m.caps.tools === false ? t('no tools → text mode') : '', m.caps && m.caps.vision ? t('vision') : '',
      m.caps && m.caps.effort ? t('reasoning') : '', m.caps && m.caps.fast ? t('fast') : ''].filter(Boolean).join(' · ');
    box.innerHTML = models.map((m, i) => (q && !(m.id + m.label).toLowerCase().includes(q)) ? '' :
      `<div class="m"><input type="checkbox" data-i="${i}" ${m.shown ? 'checked' : ''} aria-label="${esc(t('Show {name} in the bar', { name: m.label }))}">
        <span>${esc(m.label)} ${m.label.startsWith(m.id) ? '' : `<small>${esc(m.id)}</small>`}</span>
        <small>${esc(caps(m))}</small>
        <label class="def"><input type="radio" name="pf-def" data-i="${i}" ${m.id === edit.default ? 'checked' : ''}>${esc(t('default'))}</label></div>`).join('');
  }

  async function test() {
    const btn = $('#pf-test'), msg = $('#pf-msg');
    btn.disabled = true; msg.className = 'msg'; msg.textContent = t('Testing…');
    try {
      const r = await api('providers/test', formBody()).catch(e => e.data || { ok: false, message: e.message });
      msg.className = 'msg ' + (r.ok ? 'ok' : 'bad'); msg.textContent = (r.ok ? '✓ ' + t('Available') + ' · ' : '✗ ') + t(r.message);
      if (r.ok) {
        const prev = Object.fromEntries(models.map(m => [m.id, m.shown]));
        const few = r.models.length <= 12;
        models = r.models.map(m => ({ ...m, shown: m.id in prev ? prev[m.id] : few }));
        if (!models.some(m => m.shown) && models.length) {
          const pref = models.find(m => /qwen2\.5|qwen3|llama3\.[123]|opus-5-5|gpt-5|gemini-2\.5-flash|mistral-large|default/.test(m.id)) || models[0];
          pref.shown = true; edit.default = pref.id;
        }
        renderModels();
      }
    } catch (e) { msg.className = 'msg bad'; msg.textContent = '✗ ' + e.message; }
    btn.disabled = false;
  }

  async function save(e) {
    e.preventDefault();
    const msg = $('#pf-msg'), first = !Object.keys(P).length;
    const body = { ...formBody(), models: models.map(({ id, label, meta, shown }) => ({ id, label, meta, shown })), default_model: edit.default,
      make_default: $('#pf-default').checked };
    try {
      await api('providers/save', body);
      closeModal(overlay);
      await refresh();
      if (onSaved) onSaved(first);
    } catch (err) { msg.className = 'msg bad'; msg.textContent = '✗ ' + t(err.message); }
  }

  const pick = e => {
    if (e.target.closest('a')) return;             // le lien « Créer une clé ↗ » s'ouvre sans ouvrir le formulaire
    const c = e.target.closest('.pcard'); if (!c) return;
    if (e.type === 'keydown' && e.key !== 'Enter' && e.key !== ' ') return;
    e.preventDefault();
    openForm(c.dataset.kind, c.dataset.pid || null);
  };

  function mount(opts) {
    grids = opts.grids; onSaved = opts.onSaved;
    if (!overlay) build();
    for (const g of Object.values(grids)) if (g && !g.dataset.lqBound) { g.dataset.lqBound = 1; g.addEventListener('click', pick); g.addEventListener('keydown', pick); }
    if (!det) api('detect').then(d => { det = d; render(); }).catch(() => {});
    return refresh();
  }

  window.LQ.providers = { mount, refresh, get: () => ({ kinds: K, providers: P, default: D }) };
})();
