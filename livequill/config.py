"""Config utilisateur : fournisseurs et clés API, stockés côté serveur uniquement (chmod 600)."""
import copy, json, os, threading

from . import APP


def config_dir():
    if os.environ.get('LIVEQUILL_CONFIG_DIR'):
        return os.environ['LIVEQUILL_CONFIG_DIR']
    base = os.environ.get('XDG_CONFIG_HOME') or os.path.expanduser('~/.config')
    return os.path.join(base, APP)


class Config:
    """{"providers": {id: {...}}, "default": {"provider": id, "model": str} | None}"""

    def __init__(self, path=None):
        self.path = path or os.path.join(config_dir(), 'config.json')
        self.lock = threading.RLock()
        self.data = self._load()

    def _load(self):
        try:
            with open(self.path, encoding='utf-8') as f:
                data = json.load(f)
        except (FileNotFoundError, ValueError):
            data = {}
        data.setdefault('providers', {})
        data.setdefault('default', None)
        data.setdefault('sites', [])
        return data

    def save(self):
        with self.lock:
            d = os.path.dirname(self.path)
            os.makedirs(d, mode=0o700, exist_ok=True)
            os.chmod(d, 0o700)
            tmp = self.path + '.tmp'
            # Créé directement en 600 : la clé n'est jamais lisible par les autres, même un instant
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                json.dump(self.data, f, indent=2, ensure_ascii=False)
            os.chmod(tmp, 0o600)
            os.replace(tmp, self.path)

    @property
    def configured(self):
        return bool(self.data['providers'])

    def providers(self):
        return self.data['providers']

    def get(self, pid):
        return self.data['providers'].get(pid)

    def upsert(self, pid, conf):
        with self.lock:
            old = self.data['providers'].get(pid, {})
            conf = dict(conf)
            # Clé vide envoyée par la page de setup = garder la clé déjà enregistrée
            if not conf.get('api_key') and old.get('api_key'):
                conf['api_key'] = old['api_key']
            self.data['providers'][pid] = conf
            if not self.data['default'] or self.data['default'].get('provider') not in self.data['providers']:
                self.data['default'] = {'provider': pid, 'model': conf.get('default_model')}
            self.save()

    def remove(self, pid):
        with self.lock:
            self.data['providers'].pop(pid, None)
            if self.data['default'] and self.data['default'].get('provider') == pid:
                first = next(iter(self.data['providers'].items()), None)
                self.data['default'] = {'provider': first[0], 'model': first[1].get('default_model')} if first else None
            self.save()

    def set_default(self, pid, model):
        with self.lock:
            if pid in self.data['providers']:
                self.data['default'] = {'provider': pid, 'model': model}
                self.save()

    # --- sites gérés depuis le tableau de bord ---------------------------
    def sites(self):
        return self.data['sites']

    def get_site(self, sid):
        return next((x for x in self.data['sites'] if x['id'] == sid), None)

    def upsert_site(self, site):
        with self.lock:
            sites = [x for x in self.data['sites'] if x['id'] != site['id']]
            old = self.get_site(site['id'])
            if old:
                sites.insert(self.data['sites'].index(old), site)
            else:
                sites.append(site)
            self.data['sites'] = sites
            self.save()

    def remove_site(self, sid):
        with self.lock:
            self.data['sites'] = [x for x in self.data['sites'] if x['id'] != sid]
            self.save()

    def secrets(self):
        out = []
        for p in self.data['providers'].values():
            for k in (p.get('api_key'), os.environ.get(p.get('api_key_env') or '') if p.get('api_key_env') else None):
                if k and len(k) >= 6:
                    out.append(k)
        return out

    def public_providers(self):
        """Vue sans secret, seule forme qui sort vers le navigateur."""
        out = {}
        for pid, p in self.data['providers'].items():
            q = copy.deepcopy(p)
            q['has_key'] = bool(q.pop('api_key', None)) or bool(p.get('api_key_env') and os.environ.get(p['api_key_env']))
            out[pid] = q
        return out


def api_key(conf):
    return conf.get('api_key') or (os.environ.get(conf['api_key_env']) if conf.get('api_key_env') else None) or ''


def redact(text, secrets):
    text = str(text)
    for s in secrets:
        if s:
            text = text.replace(s, '••••')
    return text
