"""Serveur : sert le site (dossier ou proxy), y ajoute la barre à la volée, et expose l'API /__lq/."""
import hmac, http.client, http.server, json, mimetypes, os, re, secrets, socket, socketserver, subprocess, sys, threading, time, traceback
import html, urllib.parse
from importlib import resources

from . import __version__, adapters, agent, scaffold
from .adapters.cli import AGENTS as CLI_AGENTS, locate as locate_cli
from .config import Config, redact
from .net import Transport
from .sandbox import DENY_DIRS, Conversation, HistoryStore, PathError, Project, denied

STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static')
MAX_BODY = 80 * 1024 * 1024
HOP = {'connection', 'keep-alive', 'proxy-authenticate', 'proxy-authorization', 'te', 'trailers', 'transfer-encoding', 'upgrade'}
BODY_END = re.compile(rb'</body\s*>', re.I)
HTML_END = re.compile(rb'</html\s*>', re.I)


def asset(name):
    """Fichier de livequill/static/, lu via importlib.resources pour fonctionner aussi depuis l'archive livequill.pyz."""
    return resources.files(__package__).joinpath('static', name).read_bytes()


LANGS_DIR = ('static', 'i18n')


def languages():
    """Langues de l'interface : l'anglais (textes du code), plus un fichier static/i18n/<code>.json par langue ajoutée."""
    out = [{'code': 'en', 'name': 'English'}]
    folder = resources.files(__package__).joinpath(*LANGS_DIR)
    if folder.is_dir():
        for f in sorted(folder.iterdir(), key=lambda f: f.name):
            if f.name.endswith('.json'):
                out.append({'code': f.name[:-5], 'name': json.loads(f.read_text('utf-8')).get('_name', f.name[:-5])})
    return out


def dictionary(lang):
    if lang == 'en' or lang not in {x['code'] for x in languages()}:
        return {}
    return json.loads(resources.files(__package__).joinpath(*LANGS_DIR, lang + '.json').read_text('utf-8'))


def localized(name, lang, token=None):
    """Page ou script de l'interface, avec la langue choisie, son dictionnaire et le jeton de session."""
    data = asset(name)
    data = (data.replace(b'/*__LQ_DICT__*/{}', json.dumps(dictionary(lang), ensure_ascii=False).encode())
                .replace(b'/*__LQ_LANGS__*/[]', json.dumps(languages(), ensure_ascii=False).encode())
                .replace(b'__LQ_LANG__', lang.encode()))
    return data.replace(b'__LQ_TOKEN__', token.encode()) if token else data


ASSETS = {'/__lq/overlay.js': ('overlay.js', 'text/javascript'), '/__lq/ui.js': ('ui.js', 'text/javascript'),
          '/__lq/sky.js': ('sky.js', 'text/javascript'), '/__lq/providers.js': ('providers.js', 'text/javascript'),
          '/__lq/settings.js': ('settings.js', 'text/javascript'),
          '/__lq/theme.css': ('theme.css', 'text/css')}
PAGE_HEADERS = {'X-Frame-Options': 'DENY', 'Content-Security-Policy': "frame-ancestors 'none'", 'Referrer-Policy': 'no-referrer'}


def inject(html, token):
    """Ajoute le script de la barre avant le dernier </body> (sinon </html>, sinon à la fin). Rien n'est écrit sur disque."""
    return inject_tag(html, f'<script src="/__lq/overlay.js" data-lq-token="{token}" defer></script>'.encode())


def inject_tag(html, tag):
    for rx in (BODY_END, HTML_END):
        hits = list(rx.finditer(html))
        if hits:
            i = hits[-1].start()
            return html[:i] + tag + html[i:]
    return html + tag


class Reloader:
    """Diffuse « reload » aux pages ouvertes. En mode dossier, surveille aussi les fichiers (modifs faites à la main)."""

    def __init__(self, project, watch=True):
        self.project, self.gen, self.cond = project, 0, threading.Condition()
        self.paused = False
        self.alive = True
        self.resync = False
        if watch:
            threading.Thread(target=self._watch, daemon=True).start()

    def bump(self, delay=0.0):
        # Les fichiers que l'appelant vient d'écrire sont annoncés par ce signal : la surveillance repart de leur nouvel
        # état au lieu de les annoncer une seconde fois (sinon la page recharge deux fois de suite)
        self.resync = True

        def go():
            time.sleep(delay)
            self.resync = True
            with self.cond:
                self.gen += 1
                self.cond.notify_all()
        threading.Thread(target=go, daemon=True).start()

    def _state(self):
        st = {}
        try:
            for p in self.project.walk():
                try:
                    s = os.stat(p)
                    st[p] = (s.st_mtime_ns, s.st_size)
                except OSError:
                    pass
        except Exception:
            pass
        return st

    def _watch(self):
        last = self._state()
        while self.alive:
            time.sleep(0.5)
            now = self._state()
            if self.resync:
                self.resync = False
            elif now != last and not self.paused:   # pendant une demande, c'est la fin de la demande qui recharge
                self.bump()
                self.resync = False                 # ce changement vient d'ici : rien à resynchroniser
            last = now

    def stop(self):
        self.alive = False
        self.bump()

    def wait(self, gen, timeout=15):
        with self.cond:
            if self.gen == gen:
                self.cond.wait(timeout)
            return self.gen


class App:
    """Un site servi avec la barre (root donné), ou le tableau de bord des sites (root=None)."""

    def __init__(self, root, port, proxy=None, config=None, transport=None, hub_url=None):
        self.port = port
        self.config = config or Config()
        self.token = secrets.token_urlsafe(24)
        self.transport = transport or Transport()
        self.hub_url = hub_url
        self.job = None
        self.jobs = {}
        self.lock = threading.Lock()
        self.proxy = urllib.parse.urlsplit(proxy) if proxy else None
        if root is None:
            self.project = None
            self.mode = 'hub'
            self.sites = Sites(self)
            return
        self.project = Project(root)
        self.mode = 'proxy' if proxy else 'static'
        self.history = HistoryStore(self.project)
        self.conversation = Conversation(self.project)
        self.reloader = Reloader(self.project, watch=not proxy)

    @property
    def lang(self):
        lang = self.config.data.get('lang') or 'en'
        return lang if lang in {x['code'] for x in languages()} else 'en'

    def settings(self):
        out = {'lang': self.lang, 'langs': languages(), 'config_path': self.config.path, 'version': __version__}
        if self.project is None:
            d = scaffold.default_dir(self.config)
            out.update(sites_dir=d, sites_dir_display=tilde(d))
        return out

    def save_settings(self, body):
        if 'lang' in body:
            if body['lang'] not in {x['code'] for x in languages()}:
                raise ValueError('unknown language')
            self.config.set_option('lang', None if body['lang'] == 'en' else body['lang'])
        if 'sites_dir' in body:
            d = os.path.realpath(os.path.expanduser(str(body['sites_dir'] or '').strip() or scaffold.default_dir(self.config)))
            if not os.path.isdir(d):
                raise ValueError('folder not found')
            self.config.set_option('sites_dir', d)
        return dict(self.settings(), ok=True)

    @property
    def origin_hosts(self):
        return {f'127.0.0.1:{self.port}', f'localhost:{self.port}', f'[::1]:{self.port}'}

    # --- état exposé à la barre et au setup (jamais de clé) ---------------
    def state(self):
        provs = []
        for pid, p in self.config.providers().items():
            ad = adapters.make(p, self.transport)
            models = [{'id': m['id'], 'label': m.get('label') or m['id'], 'caps': ad.caps(m['id'], m.get('meta'))}
                      for m in p.get('models', []) if m.get('shown', True)]
            provs.append({'id': pid, 'type': p['type'], 'label': p.get('label') or adapters.KINDS[p['type']]['label'],
                          'models': models, 'default_model': p.get('default_model')})
        turns = self.conversation.load()[-30:]
        return {'project': os.path.basename(self.project.root), 'root': self.project.root, 'mode': self.mode,
                'providers': provs, 'default': self.config.data.get('default'),
                'running': self.job.id if self.job and not self.job.done else None,
                'conversation': turns, 'undo': self.history.count()}

    # --- demandes ----------------------------------------------------------
    def start_job(self, req):
        with self.lock:
            if self.job and not self.job.done:
                return None
            job = agent.Job(req)
            self.job = job
            self.jobs = {k: v for k, v in list(self.jobs.items())[-10:]}
            self.jobs[job.id] = job
        self.reloader.paused = True
        threading.Thread(target=self._run, args=(job,), daemon=True).start()
        return job

    def _run(self, job):
        req = job.req
        turn = {'job': job.id, 'time': time.strftime('%Y-%m-%d %H:%M:%S'), 'page': page_label(req.get('page')),
                'prompt': req['text'], 'provider': req['provider'], 'model': req['model'],
                'attachments': [f.get('name') for f in req.get('files') or []]}
        try:
            answer, files = agent.run(self, job)
            turn.update(status='ok', answer=answer, files=files)
            self.conversation.add(turn)
            job.emit({'type': 'done', 'answer': answer, 'files': files, 'undo': self.history.count()})
        except agent.Cancelled:
            turn.update(status='cancelled', answer='(stopped)', files=job.files)
            self.conversation.add(turn)
            job.emit({'type': 'done', 'cancelled': True, 'files': job.files, 'undo': self.history.count()})
        except Exception as e:
            msg = redact(str(e) or e.__class__.__name__, self.config.secrets())
            if os.environ.get('LIVEQUILL_DEBUG'):
                traceback.print_exc()
            turn.update(status='error', answer=msg, files=job.files)
            self.conversation.add(turn)
            job.emit({'type': 'error', 'message': msg})
            job.emit({'type': 'done', 'error': True, 'files': job.files, 'undo': self.history.count()})
        finally:
            job.finish()
            self.reloader.paused = False
            if job.files:
                self.reloader.bump(0.4)   # laisse le temps à « done » d'arriver avant le rechargement

    def versions(self):
        models = {t.get('job'): t.get('model') for t in self.conversation.load()}
        out = self.history.versions()
        for v in out:
            v['model'] = models.get(v['job'])
        return {'versions': out, 'mode': self.mode}

    def restore_version(self, version):
        if self.job and not self.job.done:
            return {'ok': False, 'message': 'A request is in progress'}
        try:
            done = self.history.restore_to(version)
        except PathError as e:
            return {'ok': False, 'message': str(e)}
        for job_id, _ in done:
            self.conversation.mark(job_id, status='undone')
        if done:
            self.reloader.bump(0.3)
        return {'ok': True, 'undone': len(done), 'undo': self.history.count()}

    def undo(self):
        if self.job and not self.job.done:
            return {'ok': False, 'message': 'A request is in progress'}
        res = self.history.undo()
        if not res:
            return {'ok': False, 'message': 'Nothing to undo'}
        job_id, files = res
        self.conversation.mark(job_id, status='undone')
        self.reloader.bump(0.3)
        return {'ok': True, 'files': files, 'undo': self.history.count()}

    # --- fournisseurs --------------------------------------------------------
    def provider_conf(self, body):
        kind = body.get('type')
        if kind not in adapters.KINDS:
            raise ValueError('unknown provider type')
        conf = {'type': kind}
        for k in ('label', 'base_url', 'api_key', 'api_key_env', 'model', 'extra_models', 'num_ctx', 'tool_mode', 'caps'):
            v = body.get(k)
            if v not in (None, ''):
                conf[k] = v
        if conf.get('base_url'):
            u = urllib.parse.urlsplit(conf['base_url'])
            if u.scheme not in ('http', 'https') or not u.netloc:
                raise ValueError('invalid base URL')
        old = self.config.get(body.get('id') or '') or {}
        if not conf.get('api_key') and old.get('api_key') and old.get('type') == kind:
            conf['api_key'] = old['api_key']
        return conf

    def test_provider(self, body):
        conf = self.provider_conf(body)
        sec = self.config.secrets() + ([conf['api_key']] if conf.get('api_key') else [])
        try:
            ad = adapters.make(conf, self.transport)
            msg, models = ad.test()
            out = [{'id': m['id'], 'label': m['label'], 'meta': m.get('meta') or {}, 'caps': ad.caps(m['id'], m.get('meta'))} for m in models]
            return {'ok': True, 'message': msg, 'models': out}
        except Exception as e:
            return {'ok': False, 'message': redact(describe_error(e), sec)}

    def save_provider(self, body):
        conf = self.provider_conf(body)
        pid = body.get('id') or ''
        if pid not in self.config.providers():
            base = re.sub(r'[^a-z0-9-]', '', conf['type']) or 'p'
            pid, n = base, 2
            while pid in self.config.providers():
                pid, n = f'{base}-{n}', n + 1
        models = []
        for m in body.get('models') or []:
            if isinstance(m, dict) and m.get('id'):
                models.append({'id': str(m['id']), 'label': str(m.get('label') or m['id']), 'meta': m.get('meta') or {},
                               'shown': bool(m.get('shown', True))})
        if not models:
            raise ValueError('no model: test the connection first')
        conf['models'] = models
        conf['default_model'] = body.get('default_model') or next((m['id'] for m in models if m['shown']), models[0]['id'])
        self.config.upsert(pid, conf)
        if body.get('make_default'):
            self.config.set_default(pid, conf['default_model'])
        return {'ok': True, 'id': pid}

    def status(self):
        """Connexion réelle à chaque fournisseur configuré, en parallèle (la page de setup affiche « Disponible »)."""
        out, sec = {}, self.config.secrets()

        def check(pid, conf):
            try:
                msg, models = adapters.make(conf, self.transport).test()
                out[pid] = {'ok': True, 'message': msg}
            except Exception as e:
                out[pid] = {'ok': False, 'message': redact(describe_error(e), sec)}
        threads = [threading.Thread(target=check, args=(pid, c), daemon=True) for pid, c in self.config.providers().items()]
        for t in threads:
            t.start()
        deadline = time.time() + 10
        for t in threads:
            t.join(max(0.1, deadline - time.time()))
        for pid in self.config.providers():
            out.setdefault(pid, {'ok': False, 'message': 'no answer within 10 s'})
        return out

    def detect(self):
        def up(url):
            try:
                return self.transport.json('GET', url, timeout=1.5)
            except Exception:
                return None
        tags = up('http://127.0.0.1:11434/api/tags')
        lms = up('http://127.0.0.1:1234/v1/models')
        lcpp = up('http://127.0.0.1:8080/v1/models')
        cli = [(k, locate_cli(k)) for k in CLI_AGENTS]
        envs = {k: bool(os.environ.get(d['env'])) for k, d in adapters.KINDS.items() if d.get('env')}
        return {'ollama': {'up': tags is not None, 'models': [m['name'] for m in (tags or {}).get('models', [])]},
                'lmstudio': {'up': lms is not None}, 'llamacpp': {'up': lcpp is not None},
                'cli': {k: bool(p) for k, (p, _) in cli}, 'cli_via': {k: app for k, (_, app) in cli if app}, 'env': envs}


class Sites:
    """Sites du tableau de bord : chacun a son propre serveur et son propre port, donc sa propre origine."""

    def __init__(self, hub):
        self.hub, self.running, self.lock = hub, {}, threading.Lock()

    @property
    def url(self):
        return f'http://127.0.0.1:{self.hub.port}/'

    def clean(self, body, sid=None):
        root = os.path.realpath(os.path.expanduser(str(body.get('root') or '').strip()))
        if not body.get('root') or not os.path.isdir(root):
            raise ValueError('folder not found')
        if root in ('/', os.path.realpath(os.path.expanduser('~'))):
            raise ValueError('choose the project folder, not the root or your home folder')
        proxy = str(body.get('proxy') or '').strip()
        if proxy:
            if not proxy.startswith(('http://', 'https://')):
                proxy = 'http://' + proxy
            u = urllib.parse.urlsplit(proxy)
            if not u.hostname:
                raise ValueError('invalid dev server address')
        name = str(body.get('name') or '').strip()[:80] or os.path.basename(root)
        for other in self.hub.config.sites():
            if other['id'] != sid and other['root'] == root:
                raise ValueError(f'this folder is already the site “{other["name"]}”')
        return {'name': name, 'root': root, 'proxy': proxy}

    def save(self, body):
        sid = str(body.get('id') or '')
        old = self.hub.config.get_site(sid) if sid else None
        site = self.clean(body, sid if old else None)
        if old:
            site = dict(old, **site)
            changed = (old['root'], old.get('proxy')) != (site['root'], site.get('proxy'))
        else:
            site['id'] = secrets.token_hex(4)
            changed = False
        self.hub.config.upsert_site(site)
        if changed and site['id'] in self.running:
            self.stop(site['id'])
            self.start(site['id'])
        return site

    def create(self, body):
        """Nouveau site : dossier créé à partir d'un modèle, puis ajouté à la liste."""
        name = str(body.get('name') or '').strip()[:80]
        if not name:
            raise ValueError('give your site a name')
        root = str(body.get('root') or '').strip()
        if not root:
            root = scaffold.suggest(scaffold.default_dir(self.hub.config), name)
        root = scaffold.create(root, name, str(body.get('template') or 'blank'))
        return self.save({'root': root, 'name': name})

    def reveal(self, sid):
        """Ouvre le dossier du site dans le gestionnaire de fichiers."""
        site = self.hub.config.get_site(sid)
        if not site or not os.path.isdir(site['root']):
            raise ValueError('folder not found')
        opener = 'open' if sys.platform == 'darwin' else 'xdg-open'
        try:
            subprocess.Popen([opener, site['root']], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, start_new_session=True)
        except OSError:
            raise ValueError(f'could not open the file manager ({opener} is missing)') from None

    def ensure(self, root, proxy=None):
        """livequill ./dossier : ajoute le site s'il n'existe pas encore."""
        root = os.path.realpath(root)
        for s in self.hub.config.sites():
            if s['root'] == root:
                if (proxy or '') != (s.get('proxy') or ''):
                    return self.save(dict(s, proxy=proxy or ''))['id']
                return s['id']
        return self.save({'root': root, 'proxy': proxy or ''})['id']

    def start(self, sid):
        with self.lock:
            if sid in self.running:
                return self.running[sid][0].port
            site = self.hub.config.get_site(sid)
            if not site:
                raise ValueError('unknown site')
            if not os.path.isdir(site['root']):
                raise ValueError(f'folder not found: {site["root"]}')
            app = App(site['root'], site.get('port') or self.hub.port + 1, proxy=site.get('proxy') or None,
                      config=self.hub.config, transport=self.hub.transport, hub_url=self.url)
            srv = make_server(app, port_tries=50)
            threading.Thread(target=srv.serve_forever, daemon=True).start()
            self.running[sid] = (app, srv)
        if site.get('port') != app.port:      # port stable : la barre garde ses préférences (localStorage par origine)
            self.hub.config.upsert_site(dict(site, port=app.port))
        return app.port

    def stop(self, sid):
        with self.lock:
            app, srv = self.running.pop(sid, (None, None))
        if srv:
            if app.job and not app.job.done:
                app.job.cancel()
            app.reloader.stop()
            srv.shutdown()
            srv.server_close()

    def remove(self, sid):
        self.stop(sid)
        self.hub.config.remove_site(sid)

    def stop_all(self):
        for sid in list(self.running):
            self.stop(sid)

    def public(self):
        out = []
        for s in self.hub.config.sites():
            app = self.running.get(s['id'], (None,))[0]
            root = s['root']
            home_page = not s.get('proxy') and any(os.path.isfile(os.path.join(root, f)) for f in ('index.html', 'index.htm'))
            out.append({'id': s['id'], 'name': s['name'], 'root': root, 'proxy': s.get('proxy') or '',
                        'display': tilde(root), 'exists': os.path.isdir(root), 'running': bool(app),
                        'thumb': f'/__lq/thumb/{s["id"]}/' if home_page else None,
                        'url': f'http://127.0.0.1:{app.port}/' if app else None,
                        'busy': bool(app and app.job and not app.job.done)})
        return out


def tilde(path):
    home = os.path.realpath(os.path.expanduser('~'))
    return '~' + path[len(home):] if path == home or path.startswith(home + os.sep) else path


def user_dir(key, fallback):
    """Dossier XDG de l'utilisateur (Bureau, Documents…), avec son nom localisé s'il est déclaré dans user-dirs.dirs."""
    home = os.path.expanduser('~')
    try:
        with open(os.path.join(os.environ.get('XDG_CONFIG_HOME') or os.path.join(home, '.config'), 'user-dirs.dirs'), encoding='utf-8') as f:
            for line in f:
                if line.startswith(f'XDG_{key}_DIR='):
                    return os.path.expandvars(line.split('=', 1)[1].strip().strip('"').replace('$HOME', home))
    except OSError:
        pass
    return os.path.join(home, fallback)


def places(config):
    home = os.path.realpath(os.path.expanduser('~'))
    out = [{'name': 'Home', 'icon': 'home', 'path': home}]
    for key, fallback, name, ico in (('DESKTOP', 'Desktop', 'Desktop', 'desktop'), ('DOCUMENTS', 'Documents', 'Documents', 'docs')):
        d = os.path.realpath(user_dir(key, fallback))
        if d != home and os.path.isdir(d):
            out.append({'name': name, 'icon': ico, 'path': d})
    sites = os.path.realpath(scaffold.default_dir(config))
    if os.path.isdir(sites):
        out.append({'name': 'LiveQuill sites', 'icon': 'sparkle', 'path': sites})
    return out


def browse(path, config):
    """Sous-dossiers d'un dossier, pour choisir un site depuis le tableau de bord (noms seulement, sites repérés)."""
    path = os.path.realpath(os.path.expanduser(path or '~'))
    if not os.path.isdir(path):
        raise ValueError('folder not found')
    dirs = []
    try:
        for e in sorted(os.scandir(path), key=lambda e: e.name.lower()):
            if len(dirs) >= 500:
                break
            if e.is_dir() and not e.name.startswith('.') and e.name not in ('node_modules', '__pycache__'):
                dirs.append({'name': e.name, 'site': os.path.isfile(os.path.join(e.path, 'index.html'))})
    except PermissionError:
        pass
    try:
        names = set(os.listdir(path))
    except OSError:
        names = set()
    home = os.path.realpath(os.path.expanduser('~'))
    crumbs, cur = [], path
    while True:         # fil d'Ariane depuis le dossier personnel (« Home »), sinon depuis la racine (« Computer »)
        if cur == home:
            crumbs.insert(0, {'name': 'Home', 'path': cur, 'label': True})
            break
        parent = os.path.dirname(cur)
        if parent == cur:
            crumbs.insert(0, {'name': 'Computer', 'path': cur, 'label': True})
            break
        crumbs.insert(0, {'name': os.path.basename(cur), 'path': cur})
        cur = parent
    return {'path': path, 'parent': os.path.dirname(path) if path != '/' else None, 'dirs': dirs, 'display': tilde(path),
            'crumbs': crumbs, 'places': places(config),
            'here': {'html': 'index.html' in names, 'node': 'package.json' in names, 'empty': not (names - scaffold.IGNORED),
                     'writable': os.access(path, os.W_OK)}}


def empty_page(path, root):
    """Dossier sans index.html : explique quoi faire et liste les pages présentes."""
    try:
        pages = sorted(n for n in os.listdir(path) if n.endswith(('.html', '.htm')) and not n.startswith('.'))
    except OSError:
        pages = []
    where = os.path.relpath(path, root)
    links = ''.join(f'<li><a href="{urllib.parse.quote(n)}">{html.escape(n)}</a></li>' for n in pages[:50])
    title = 'No home page yet'
    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title><link rel="stylesheet" href="/__lq/theme.css">
<style>main{{max-width:620px;padding-top:12vh}}.island{{animation:none}}h1{{margin:14px 0 8px;font-size:30px;letter-spacing:-.02em}}
p{{color:var(--soft);margin:0 0 10px}}ul{{margin:12px 0 0;padding-left:20px}}</style></head>
<body><main><div class="isle"><div class="cushion" aria-hidden="true"><i></i><i></i><i></i></div><div class="island">
<span class="tag">{html.escape('/' if where == '.' else '/' + where + '/')}</span>
<h1>{title}</h1>
<p>This folder has no <code>index.html</code> yet. Ask for one in the bar below, for example
<b>“create a home page for my bakery, with our opening hours”</b>, and it will appear here.</p>
{f'<p>Pages in this folder:</p><ul>{links}</ul>' if links else ''}
</div></div></main><script src="/__lq/sky.js" defer></script></body></html>'''.encode()


def page_label(page):
    page = page or {}
    return f'{page.get("pathname", "/")}{page.get("hash", "")}'


def describe_error(e):
    s = str(e)
    if isinstance(e, OSError) and ('Connection refused' in s or 'urlopen error' in s):
        return f'Cannot connect ({s}). Is the server running?'
    if 'HTTP 401' in s or 'HTTP 403' in s:
        return f'The provider refused the key. {s[:300]}'
    return s[:600] or e.__class__.__name__


class Handler(http.server.SimpleHTTPRequestHandler):
    app: App = None
    server_version = 'livequill'

    def __init__(self, *a, **k):
        super().__init__(*a, directory=self.app.project.root if self.app.project else STATIC, **k)

    # --- garde-fous ------------------------------------------------------
    def host_ok(self):
        return (self.headers.get('Host') or '') in self.app.origin_hosts

    def token_ok(self, query=None):
        tok = self.headers.get('X-LQ-Token') or (query or {}).get('t', [''])[0]
        return hmac.compare_digest(tok.encode(), self.app.token.encode())

    def same_origin(self):
        site = self.headers.get('Sec-Fetch-Site')
        if site and site not in ('same-origin', 'none'):
            return False
        origin = self.headers.get('Origin')
        if origin and urllib.parse.urlsplit(origin).netloc not in self.app.origin_hosts:
            return False
        return True

    def end_headers(self):
        if not getattr(self, '_proxying', False):
            self.send_header('Cache-Control', 'no-store')
        super().end_headers()

    def send_json(self, obj, status=200):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_bytes(self, body, ctype, status=200, extra=None):
        self.send_response(status)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def deny(self, status=403, msg='forbidden'):
        self.send_json({'error': msg}, status)

    def log_message(self, fmt, *args):
        if os.environ.get('LIVEQUILL_QUIET'):
            return
        path = urllib.parse.urlsplit(self.path).path   # jamais la query (jeton) ni le corps
        if path.startswith(('/__lq/reload', '/__lq/api/jobs', '/__lq/overlay.js', '/__lq/api/state')):
            return
        code = args[1] if len(args) > 1 else ''
        print(f'  {self.command} {path} {code}', flush=True)

    # --- routage ---------------------------------------------------------
    def do_GET(self):
        self.route('GET')

    def do_HEAD(self):
        self.route('HEAD')

    def do_POST(self):
        self.route('POST')

    def do_DELETE(self):
        self.route('DELETE')

    def do_PUT(self):
        self.route('PUT')

    def do_PATCH(self):
        self.route('PATCH')

    def do_OPTIONS(self):
        self.route('OPTIONS')

    def route(self, method):
        if not self.host_ok():
            return self.deny(403, 'Host refused')
        u = urllib.parse.urlsplit(self.path)
        if u.path == '/__setup' or u.path.startswith('/__lq/'):
            return self.internal(method, u)
        if self.app.project is None:
            return self.hub_page(method, u)
        if not self.app.config.configured and method == 'GET' and self.wants_html(u.path):
            self.send_response(302)
            self.send_header('Location', '/__setup?next=' + urllib.parse.quote(self.path, safe=''))
            self.end_headers()
            return
        if self.app.proxy:
            return self.proxy(method)
        if method in ('GET', 'HEAD'):
            return self.static(method, u)
        self.send_error(405)

    def hub_page(self, method, u):
        """Tableau de bord : setup au premier lancement, puis la liste des sites."""
        if method != 'GET' or u.path not in ('/', '/index.html'):
            return self.send_error(404)
        if not self.app.config.configured:
            self.send_response(302)
            self.send_header('Location', '/__setup?next=%2F')
            self.end_headers()
            return
        html = localized('hub.html', self.app.lang, self.app.token)
        return self.send_bytes(html, 'text/html; charset=utf-8', extra=PAGE_HEADERS)

    def wants_html(self, path):
        accept = self.headers.get('Accept', '')
        return 'text/html' in accept or path.endswith(('/', '.html', '.htm'))

    def internal(self, method, u):
        app, q = self.app, urllib.parse.parse_qs(u.query)
        p = u.path
        if p in ASSETS and method == 'GET':
            name, ctype = ASSETS[p]
            return self.send_bytes(localized(name, app.lang), ctype + '; charset=utf-8')
        if p in ('/__setup', '/__lq/setup') and method == 'GET':
            # Le setup n'existe qu'au premier lancement ; ensuite, les réglages sont dans le tableau de bord
            hub_url = app.sites.url if app.project is None else app.hub_url
            if app.config.configured and hub_url:
                back = ''
                if app.project is not None:
                    nxt = (q.get('next') or ['/'])[0]
                    nxt = nxt if nxt.startswith('/') and not nxt.startswith('//') else '/'
                    back = '?back=' + urllib.parse.quote(f'http://127.0.0.1:{app.port}{nxt}', safe='')
                self.send_response(302)
                self.send_header('Location', hub_url + back + '#settings')
                self.end_headers()
                return
            return self.send_bytes(localized('setup.html', app.lang, app.token), 'text/html; charset=utf-8', extra=PAGE_HEADERS)
        if p == '/__lq/settings' and method == 'GET':
            # Réglages ouverts par la barre dans un cadre par-dessus le site : seule la page elle-même peut l'encadrer
            return self.send_bytes(localized('settings.html', app.lang, app.token), 'text/html; charset=utf-8',
                                   extra={'X-Frame-Options': 'SAMEORIGIN', 'Content-Security-Policy': "frame-ancestors 'self'",
                                          'Referrer-Policy': 'no-referrer'})
        if app.project is None and method == 'GET':
            m = re.fullmatch(r'/__lq/(template|thumb)/([\w-]+)(/.*)?', p)
            if m:
                return self.hub_file(m.group(1), m.group(2), urllib.parse.unquote(m.group(3) or '/'), q)
        m = re.fullmatch(r'/__lq/preview/([\w.-]+)(/.*)?', p)
        if m and method == 'GET' and app.project is not None:
            return self.preview(m.group(1), urllib.parse.unquote(m.group(2) or '/'))
        if not self.same_origin() or not self.token_ok(q):
            return self.deny(403, 'invalid token or origin')
        hub = app.project is None
        site_only = p in ('/__lq/reload', '/__lq/api/state', '/__lq/api/prompt', '/__lq/api/undo',
                          '/__lq/api/conversation/clear') or p.startswith(('/__lq/api/jobs/', '/__lq/api/versions'))
        hub_only = p.startswith(('/__lq/api/sites', '/__lq/api/browse', '/__lq/api/templates'))
        if (hub and site_only) or (not hub and hub_only):
            return self.deny(404, 'not found')
        if p == '/__lq/reload' and method == 'GET':
            return self.stream_reload()
        m = re.fullmatch(r'/__lq/api/jobs/(\w+)/events', p)
        if m and method == 'GET':
            return self.stream_job(m.group(1), int((q.get('from') or ['0'])[0] or 0))
        if method == 'GET':
            if p == '/__lq/api/state':
                return self.send_json(app.state())
            if p == '/__lq/api/versions':
                return self.send_json(app.versions())
            if p == '/__lq/api/detect':
                return self.send_json(app.detect())
            if p == '/__lq/api/status':
                return self.send_json(app.status())
            if p == '/__lq/api/kinds':
                return self.send_json({'kinds': adapters.public_kinds(), 'providers': app.config.public_providers(),
                                       'default': app.config.data.get('default'), 'mode': app.mode,
                                       'root': app.project.root if app.project else None,
                                       'hub_url': app.sites.url if hub else app.hub_url})
            if p == '/__lq/api/sites':
                return self.send_json({'sites': app.sites.public(), 'default': app.config.data.get('default'),
                                       'providers': {k: v.get('label') or adapters.KINDS[v['type']]['label']
                                                     for k, v in app.config.providers().items()}})
            if p == '/__lq/api/settings':
                return self.send_json(app.settings())
            if p == '/__lq/api/templates':
                return self.send_json({'templates': [dict(v, id=k) for k, v in scaffold.TEMPLATES.items()]})
            try:
                if p == '/__lq/api/browse':
                    return self.send_json(browse((q.get('path') or ['~'])[0], app.config))
                if p == '/__lq/api/sites/suggest':
                    parent = (q.get('parent') or [''])[0] or scaffold.default_dir(app.config)
                    root = scaffold.suggest(parent, (q.get('name') or [''])[0])
                    return self.send_json({'root': root, 'display': tilde(root), 'parent': os.path.dirname(root),
                                           'parent_display': tilde(os.path.dirname(root))})
            except ValueError as e:
                return self.send_json({'ok': False, 'message': str(e)}, 400)
            return self.deny(404, 'not found')
        if method not in ('POST', 'DELETE'):
            return self.deny(405, 'method not allowed')
        # Corps JSON obligatoire : un formulaire d'un autre site ne peut pas l'envoyer sans pré-vol CORS
        if not (self.headers.get('Content-Type') or '').startswith('application/json'):
            return self.deny(415, 'JSON expected')
        try:
            n = int(self.headers.get('Content-Length') or 0)
            if n > MAX_BODY:
                return self.deny(413, 'request too large')
            body = json.loads(self.rfile.read(n) or b'{}')
            if not isinstance(body, dict):
                raise ValueError
        except ValueError:
            return self.deny(400, 'invalid JSON')
        try:
            if p == '/__lq/api/prompt':
                return self.api_prompt(body)
            m = re.fullmatch(r'/__lq/api/jobs/(\w+)/cancel', p)
            if m:
                job = app.jobs.get(m.group(1))
                if job:
                    job.cancel()
                return self.send_json({'ok': bool(job)})
            if p == '/__lq/api/undo':
                return self.send_json(app.undo())
            if p == '/__lq/api/versions/restore':
                return self.send_json(app.restore_version(str(body.get('id') or '')))
            if p == '/__lq/api/conversation/clear':
                app.conversation.clear()
                return self.send_json({'ok': True})
            if p == '/__lq/api/providers/test':
                return self.send_json(app.test_provider(body))
            if p == '/__lq/api/providers/save':
                return self.send_json(app.save_provider(body))
            if p == '/__lq/api/providers/delete':
                app.config.remove(str(body.get('id')))
                return self.send_json({'ok': True})
            if p == '/__lq/api/sites/save':
                return self.send_json({'ok': True, 'site': app.sites.save(body)})
            if p == '/__lq/api/sites/create':
                return self.send_json({'ok': True, 'site': app.sites.create(body)})
            if p == '/__lq/api/sites/reveal':
                app.sites.reveal(str(body.get('id')))
                return self.send_json({'ok': True})
            if p == '/__lq/api/settings':
                return self.send_json(app.save_settings(body))
            if p == '/__lq/api/sites/delete':
                app.sites.remove(str(body.get('id')))
                return self.send_json({'ok': True})
            if p == '/__lq/api/sites/start':
                port = app.sites.start(str(body.get('id')))
                return self.send_json({'ok': True, 'url': f'http://127.0.0.1:{port}/'})
            if p == '/__lq/api/sites/stop':
                app.sites.stop(str(body.get('id')))
                return self.send_json({'ok': True})
            if p == '/__lq/api/default':
                app.config.set_default(str(body.get('provider')), str(body.get('model')))
                return self.send_json({'ok': True})
        except ValueError as e:
            return self.send_json({'ok': False, 'message': str(e)}, 400)
        return self.deny(404, 'not found')

    def api_prompt(self, body):
        app = self.app
        text = str(body.get('text') or '').strip()
        files = body.get('files') or []
        if not text and not files:
            return self.send_json({'ok': False, 'message': 'empty request'}, 400)
        pid = body.get('provider') or (app.config.data.get('default') or {}).get('provider')
        conf = app.config.get(pid or '')
        if not conf:
            return self.send_json({'ok': False, 'message': 'no provider set up'}, 400)
        model = body.get('model') or conf.get('default_model')
        page = body.get('page') if isinstance(body.get('page'), dict) else {}
        req = {'text': text, 'files': files if isinstance(files, list) else [], 'provider': pid, 'model': model,
               'effort': body.get('effort'), 'fast': bool(body.get('fast')),
               'page': {k: str(page.get(k, ''))[:500] for k in ('pathname', 'search', 'hash', 'title')},
               'selection': str(body.get('selection') or '')[:2000], 'outline': str(body.get('outline') or '')[:3000]}
        job = app.start_job(req)
        if not job:
            return self.send_json({'ok': False, 'message': 'a request is already in progress'}, 409)
        return self.send_json({'ok': True, 'job': job.id})

    # --- flux SSE --------------------------------------------------------
    def sse_start(self):
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.send_header('X-Accel-Buffering', 'no')
        self.end_headers()

    def stream_reload(self):
        self.sse_start()
        gen = self.app.reloader.gen
        try:
            while True:
                now = self.app.reloader.wait(gen)
                self.wfile.write(b'data: reload\n\n' if now != gen else b': ping\n\n')
                self.wfile.flush()
                gen = now
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass

    def stream_job(self, jid, start):
        job = self.app.jobs.get(jid)
        if not job:
            return self.deny(404, 'demande inconnue')
        self.sse_start()
        i = start
        try:
            while True:
                evs, done = job.wait_events(i)
                for ev in evs:
                    self.wfile.write(b'data: ' + json.dumps(ev, ensure_ascii=False).encode() + b'\n\n')
                i += len(evs)
                if not evs:
                    self.wfile.write(b': ping\n\n')
                self.wfile.flush()
                if done and i >= len(job.events):
                    break
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass

    # --- aperçu d'une version (les mêmes règles que les fichiers servis, sans la barre) -------------
    def hub_file(self, kind, ident, path, q):
        """Tableau de bord : fichiers d'un modèle (aperçu dans l'assistant) ou d'un site (vignette), en lecture seule, sans la barre."""
        parts = [x for x in path.split('/') if x]
        if any(x in ('.', '..') for x in parts):
            return self.send_error(404)
        if kind == 'template':
            if ident not in scaffold.TEMPLATES:
                return self.send_error(404)
            rel = '/'.join(parts) or 'index.html'
            found = dict(scaffold.files(ident)).get(rel)
            if found is None:
                return self.send_error(404)
            name = ((q.get('name') or [''])[0].strip() or 'Your site')[:80]
            body, ctype = scaffold.render(rel, found, name), mimetypes.guess_type(rel)[0] or 'application/octet-stream'
        else:
            site = self.app.config.get_site(ident)
            if not site or site.get('proxy') or not os.path.isdir(site['root']):
                return self.send_error(404)
            if denied(parts) or any(x in DENY_DIRS for x in parts):
                return self.send_error(404)
            try:
                project = Project(site['root'])
                full = project.resolve('/'.join(parts), allow_root=True)
                if os.path.isdir(full):
                    full = os.path.join(full, 'index.html')
                with project.open(full, 'rb') as f:
                    body = f.read(20_000_000)
            except (PathError, OSError):
                return self.send_error(404)
            ctype = mimetypes.guess_type(full)[0] or 'application/octet-stream'
        if ctype.startswith('text/') or ctype in ('application/javascript', 'image/svg+xml'):
            ctype += '; charset=utf-8'
        return self.send_bytes(body, ctype, extra={'X-Frame-Options': 'SAMEORIGIN', 'Cache-Control': 'no-store',
                                                  'Content-Security-Policy': "script-src 'none'; object-src 'none'; frame-ancestors 'self'"})   # aucun script : le jeton reste hors d'atteinte

    def preview(self, version, path):
        app = self.app
        if app.proxy:
            return self.send_error(404, 'no preview behind a dev server')
        parts = [x for x in path.split('/') if x]
        if denied(parts):
            return self.send_error(404)
        try:
            real = app.project.resolve('/'.join(parts), allow_root=True)
            if path.endswith('/') or not parts or os.path.isdir(real):
                parts.append('index.html')
                real = app.project.resolve('/'.join(parts))
            src = app.history.file_at(version, '/'.join(parts))
        except PathError:
            return self.send_error(404)
        if src is None or (src == 'current' and not os.path.isfile(real)):
            return self.send_error(404)
        with open(real if src == 'current' else src, 'rb') as f:
            data = f.read()
        ctype = mimetypes.guess_type(parts[-1])[0] or 'application/octet-stream'
        if ctype == 'text/html':
            label = 'before any change' if version == 'origin' else \
                time.strftime('version of %b %d, %H:%M', time.localtime(app.history.manifest(version)['time']))
            data = inject_tag(data, ('<div style="position:fixed;left:50%;bottom:14px;transform:translateX(-50%);z-index:2147483647;'
                                     'padding:7px 14px;border-radius:999px;background:#d97757;color:#fff;font:600 13px system-ui,sans-serif;'
                                     f'box-shadow:0 6px 20px rgba(0,0,0,.3)">Preview · {label}</div>').encode())
            ctype += '; charset=utf-8'
        return self.send_bytes(data, ctype)

    # --- mode dossier statique -------------------------------------------
    def static(self, method, u):
        rel = urllib.parse.unquote(u.path)
        parts = [x for x in rel.split('/') if x]
        if denied(parts) or any(x in DENY_DIRS for x in parts):
            return self.send_error(404)
        try:   # même vérification que les outils : un lien vers l'extérieur ou vers un fichier interdit n'est pas servi
            self.app.project.resolve('/'.join(parts), allow_root=True)
        except PathError:
            return self.send_error(404)
        path = self.translate_path(u.path)
        if os.path.isdir(path):
            if not u.path.endswith('/'):
                self.send_response(301)
                self.send_header('Location', u.path + '/' + (('?' + u.query) if u.query else ''))
                self.end_headers()
                return
            for idx in ('index.html', 'index.htm'):
                if os.path.isfile(os.path.join(path, idx)):
                    path = os.path.join(path, idx)
                    break
        if os.path.isdir(path):     # pas de page d'accueil : une page d'attente avec la barre, plutôt qu'une liste de fichiers sans elle
            return self.send_bytes(inject(empty_page(path, self.app.project.root), self.app.token), 'text/html; charset=utf-8')
        if path.endswith(('.html', '.htm')) and os.path.isfile(path):
            with open(path, 'rb') as f:
                body = inject(f.read(), self.app.token)
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            if method == 'GET':
                self.wfile.write(body)
            return
        return super().do_GET() if method == 'GET' else super().do_HEAD()

    # --- mode proxy --------------------------------------------------------
    def proxy(self, method):
        up = self.app.proxy
        if (self.headers.get('Upgrade') or '').lower() == 'websocket':
            return self.tunnel()
        n = int(self.headers.get('Content-Length') or 0)
        body = self.rfile.read(n) if n else None
        headers = {k: v for k, v in self.headers.items() if k.lower() not in HOP and k.lower() not in ('host', 'accept-encoding')}
        headers['Host'] = up.netloc
        headers['Accept-Encoding'] = 'identity'   # pour pouvoir modifier le HTML
        cls = http.client.HTTPSConnection if up.scheme == 'https' else http.client.HTTPConnection
        try:
            conn = cls(up.hostname, up.port or (443 if up.scheme == 'https' else 80), timeout=60)
            conn.request(method, self.path, body=body, headers=headers)
            r = conn.getresponse()
        except OSError as e:
            msg = f'<h1>livequill</h1><p>The dev server {up.geturl()} does not answer: {e}</p>'.encode()
            return self.send_bytes(inject(msg, self.app.token), 'text/html; charset=utf-8', 502)
        self._proxying = True
        ctype = r.getheader('Content-Type', '')
        is_html = ctype.startswith('text/html') and method == 'GET'
        enc = (r.getheader('Content-Encoding') or 'identity').lower()
        if is_html and enc == 'identity':
            data = inject(r.read(), self.app.token)
            self.send_response(r.status, r.reason)
            for k, v in r.getheaders():
                if k.lower() not in HOP and k.lower() not in ('content-length', 'content-security-policy'):
                    self.send_header(k, v)
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        else:
            self.send_response(r.status, r.reason)
            for k, v in r.getheaders():
                if k.lower() not in HOP:
                    self.send_header(k, v)
            self.end_headers()
            try:
                while True:
                    chunk = r.read1(65536) if hasattr(r, 'read1') else r.read(65536)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass
        conn.close()
        self.close_connection = True

    def tunnel(self):
        """WebSocket (HMR de Vite, Next…) : on relaie les octets dans les deux sens."""
        up = self.app.proxy
        try:
            s = socket.create_connection((up.hostname, up.port or 80), timeout=10)
        except OSError:
            return self.send_error(502)
        s.settimeout(None)
        lines = [f'{self.command} {self.path} HTTP/1.1']
        for k, v in self.headers.items():
            lines.append(f'Host: {up.netloc}' if k.lower() == 'host' else f'{k}: {v}')
        s.sendall(('\r\n'.join(lines) + '\r\n\r\n').encode())
        client = self.connection

        def pipe(a, b):
            try:
                while True:
                    d = a.recv(65536)
                    if not d:
                        break
                    b.sendall(d)
            except OSError:
                pass
            for x in (a, b):
                try:
                    x.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass

        t = threading.Thread(target=pipe, args=(s, client), daemon=True)
        t.start()
        pipe(client, s)
        t.join(2)
        s.close()
        self.close_connection = True


class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True    # relance immédiate malgré TIME_WAIT ; Linux refuse toujours deux serveurs sur un même port


def make_server(app, port_tries=20):
    handler = type('BoundHandler', (Handler,), {'app': app})
    last = None
    for port in range(app.port, app.port + port_tries) if app.port else [0]:
        try:
            srv = Server(('127.0.0.1', port), handler)   # jamais 0.0.0.0
            app.port = srv.server_address[1]
            return srv
        except OSError as e:
            last = e
    raise last
