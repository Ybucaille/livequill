"""Serveur : sert le site (dossier ou proxy), y ajoute la barre à la volée, et expose l'API /__lq/."""
import hmac, http.client, http.server, json, os, re, secrets, socket, socketserver, threading, time, traceback
import urllib.parse

from . import adapters, agent
from .adapters.cli import AGENTS as CLI_AGENTS, locate as locate_cli
from .config import Config, redact
from .net import Transport
from .sandbox import DENY_DIRS, Conversation, HistoryStore, PathError, Project, denied

STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static')
MAX_BODY = 80 * 1024 * 1024
HOP = {'connection', 'keep-alive', 'proxy-authenticate', 'proxy-authorization', 'te', 'trailers', 'transfer-encoding', 'upgrade'}
BODY_END = re.compile(rb'</body\s*>', re.I)
HTML_END = re.compile(rb'</html\s*>', re.I)


def inject(html, token):
    """Ajoute le script de la barre avant le dernier </body> (sinon </html>, sinon à la fin). Rien n'est écrit sur disque."""
    tag = f'<script src="/__lq/overlay.js" data-lq-token="{token}" defer></script>'.encode()
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
        if watch:
            threading.Thread(target=self._watch, daemon=True).start()

    def bump(self, delay=0.0):
        def go():
            time.sleep(delay)
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
            if now != last and not self.paused:   # pendant une demande, c'est la fin de la demande qui recharge
                self.bump()
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
            turn.update(status='cancelled', answer='(annulé)', files=job.files)
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

    def undo(self):
        if self.job and not self.job.done:
            return {'ok': False, 'message': 'Une demande est en cours'}
        res = self.history.undo()
        if not res:
            return {'ok': False, 'message': 'Rien à annuler'}
        job_id, files = res
        self.conversation.mark(job_id, status='undone')
        self.reloader.bump(0.3)
        return {'ok': True, 'files': files, 'undo': self.history.count()}

    # --- fournisseurs --------------------------------------------------------
    def provider_conf(self, body):
        kind = body.get('type')
        if kind not in adapters.KINDS:
            raise ValueError('type de fournisseur inconnu')
        conf = {'type': kind}
        for k in ('label', 'base_url', 'api_key', 'api_key_env', 'model', 'extra_models', 'num_ctx', 'tool_mode', 'caps'):
            v = body.get(k)
            if v not in (None, ''):
                conf[k] = v
        if conf.get('base_url'):
            u = urllib.parse.urlsplit(conf['base_url'])
            if u.scheme not in ('http', 'https') or not u.netloc:
                raise ValueError('URL de base invalide')
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
            raise ValueError('aucun modèle : teste la connexion d’abord')
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
            out.setdefault(pid, {'ok': False, 'message': 'pas de réponse en 10 s'})
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
            raise ValueError('dossier introuvable')
        if root in ('/', os.path.realpath(os.path.expanduser('~'))):
            raise ValueError('choisis le dossier du projet, pas la racine ni ton dossier personnel')
        proxy = str(body.get('proxy') or '').strip()
        if proxy:
            if not proxy.startswith(('http://', 'https://')):
                proxy = 'http://' + proxy
            u = urllib.parse.urlsplit(proxy)
            if not u.hostname:
                raise ValueError('URL du serveur de dev invalide')
        name = str(body.get('name') or '').strip()[:80] or os.path.basename(root)
        for other in self.hub.config.sites():
            if other['id'] != sid and other['root'] == root:
                raise ValueError(f'ce dossier est déjà le site « {other["name"]} »')
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
                raise ValueError('site inconnu')
            if not os.path.isdir(site['root']):
                raise ValueError(f'dossier introuvable : {site["root"]}')
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
        home = os.path.realpath(os.path.expanduser('~'))
        out = []
        for s in self.hub.config.sites():
            app = self.running.get(s['id'], (None,))[0]
            root = s['root']
            out.append({'id': s['id'], 'name': s['name'], 'root': root, 'proxy': s.get('proxy') or '',
                        'display': '~' + root[len(home):] if root == home or root.startswith(home + os.sep) else root,
                        'exists': os.path.isdir(root), 'running': bool(app),
                        'url': f'http://127.0.0.1:{app.port}/' if app else None,
                        'busy': bool(app and app.job and not app.job.done)})
        return out


def browse(path):
    """Sous-dossiers d'un dossier, pour choisir un projet depuis le tableau de bord (noms seulement)."""
    path = os.path.realpath(os.path.expanduser(path or '~'))
    if not os.path.isdir(path):
        raise ValueError('dossier introuvable')
    dirs = []
    try:
        for e in sorted(os.scandir(path), key=lambda e: e.name.lower()):
            if e.is_dir() and not e.name.startswith('.') and e.name not in ('node_modules', '__pycache__'):
                dirs.append(e.name)
    except PermissionError:
        pass
    names = set(os.listdir(path)) if os.access(path, os.R_OK) else set()
    hints = [h for h, f in (('html', 'index.html'), ('node', 'package.json'), ('git', '.git')) if f in names]
    home = os.path.realpath(os.path.expanduser('~'))
    return {'path': path, 'parent': os.path.dirname(path) if path != '/' else None, 'dirs': dirs[:500], 'hints': hints,
            'display': '~' + path[len(home):] if path == home or path.startswith(home + os.sep) else path}


def page_label(page):
    page = page or {}
    return f'{page.get("pathname", "/")}{page.get("hash", "")}'


def describe_error(e):
    s = str(e)
    if isinstance(e, OSError) and ('Connection refused' in s or 'urlopen error' in s):
        return f'Connexion impossible ({s}). Le serveur est-il lancé ?'
    if 'HTTP 401' in s or 'HTTP 403' in s:
        return f'Clé refusée par le fournisseur. {s[:300]}'
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

    def deny(self, status=403, msg='interdit'):
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
            return self.deny(403, 'Host refusé')
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
        with open(os.path.join(STATIC, 'hub.html'), 'rb') as f:
            html = f.read().replace(b'__LQ_TOKEN__', self.app.token.encode())
        return self.send_bytes(html, 'text/html; charset=utf-8',
                               extra={'X-Frame-Options': 'DENY', 'Content-Security-Policy': "frame-ancestors 'none'",
                                      'Referrer-Policy': 'no-referrer'})

    def wants_html(self, path):
        accept = self.headers.get('Accept', '')
        return 'text/html' in accept or path.endswith(('/', '.html', '.htm'))

    def internal(self, method, u):
        app, q = self.app, urllib.parse.parse_qs(u.query)
        p = u.path
        if p == '/__lq/overlay.js' and method == 'GET':
            with open(os.path.join(STATIC, 'overlay.js'), 'rb') as f:
                return self.send_bytes(f.read(), 'text/javascript; charset=utf-8')
        if p in ('/__setup', '/__lq/setup') and method == 'GET':
            with open(os.path.join(STATIC, 'setup.html'), 'rb') as f:
                html = f.read().replace(b'__LQ_TOKEN__', app.token.encode())
            return self.send_bytes(html, 'text/html; charset=utf-8',
                                   extra={'X-Frame-Options': 'DENY', 'Content-Security-Policy': "frame-ancestors 'none'",
                                          'Referrer-Policy': 'no-referrer'})
        if not self.same_origin() or not self.token_ok(q):
            return self.deny(403, 'jeton ou origine invalide')
        hub = app.project is None
        site_only = p in ('/__lq/reload', '/__lq/api/state', '/__lq/api/prompt', '/__lq/api/undo',
                          '/__lq/api/conversation/clear') or p.startswith('/__lq/api/jobs/')
        hub_only = p.startswith('/__lq/api/sites') or p == '/__lq/api/browse'
        if (hub and site_only) or (not hub and hub_only):
            return self.deny(404, 'introuvable')
        if p == '/__lq/reload' and method == 'GET':
            return self.stream_reload()
        m = re.fullmatch(r'/__lq/api/jobs/(\w+)/events', p)
        if m and method == 'GET':
            return self.stream_job(m.group(1), int((q.get('from') or ['0'])[0] or 0))
        if method == 'GET':
            if p == '/__lq/api/state':
                return self.send_json(app.state())
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
            if p == '/__lq/api/browse':
                try:
                    return self.send_json(browse((q.get('path') or ['~'])[0]))
                except ValueError as e:
                    return self.send_json({'ok': False, 'message': str(e)}, 400)
            return self.deny(404, 'introuvable')
        if method not in ('POST', 'DELETE'):
            return self.deny(405, 'méthode refusée')
        # Corps JSON obligatoire : un formulaire d'un autre site ne peut pas l'envoyer sans pré-vol CORS
        if not (self.headers.get('Content-Type') or '').startswith('application/json'):
            return self.deny(415, 'JSON attendu')
        try:
            n = int(self.headers.get('Content-Length') or 0)
            if n > MAX_BODY:
                return self.deny(413, 'requête trop grosse')
            body = json.loads(self.rfile.read(n) or b'{}')
            if not isinstance(body, dict):
                raise ValueError
        except ValueError:
            return self.deny(400, 'JSON invalide')
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
        return self.deny(404, 'introuvable')

    def api_prompt(self, body):
        app = self.app
        text = str(body.get('text') or '').strip()
        files = body.get('files') or []
        if not text and not files:
            return self.send_json({'ok': False, 'message': 'demande vide'}, 400)
        pid = body.get('provider') or (app.config.data.get('default') or {}).get('provider')
        conf = app.config.get(pid or '')
        if not conf:
            return self.send_json({'ok': False, 'message': 'aucun fournisseur configuré'}, 400)
        model = body.get('model') or conf.get('default_model')
        page = body.get('page') if isinstance(body.get('page'), dict) else {}
        req = {'text': text, 'files': files if isinstance(files, list) else [], 'provider': pid, 'model': model,
               'effort': body.get('effort'), 'fast': bool(body.get('fast')),
               'page': {k: str(page.get(k, ''))[:500] for k in ('pathname', 'search', 'hash', 'title')},
               'selection': str(body.get('selection') or '')[:2000], 'outline': str(body.get('outline') or '')[:3000]}
        job = app.start_job(req)
        if not job:
            return self.send_json({'ok': False, 'message': 'une demande est déjà en cours'}, 409)
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
            msg = f'<h1>livequill</h1><p>Le serveur de dev {up.geturl()} ne répond pas : {e}</p>'.encode()
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
