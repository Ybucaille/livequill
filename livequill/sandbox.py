"""Accès aux fichiers du projet ciblé : chemins verrouillés au dossier, sauvegardes avant écriture, annulation."""
import fnmatch, json, os, re, shutil, threading, time, uuid

WORKDIR = '.livequill'                      # dossier de travail de l'outil dans le projet ciblé
DENY_DIRS = {'.git', 'node_modules', WORKDIR}
MAX_READ_LINES = 400
MAX_LINE = 400                                 # les lignes géantes (polices en base64…) sont tronquées
MAX_SEARCH = 80
TAG = re.compile(r'<[^>]*>')


class PathError(ValueError):
    pass


def denied(parts):
    return any(p in DENY_DIRS or p.startswith('.env') for p in parts)


class Project:
    def __init__(self, root):
        self.root = os.path.realpath(root)
        self.work = os.path.join(self.root, WORKDIR)

    # --- chemins ---------------------------------------------------------
    def resolve(self, rel, allow_root=False):
        """Chemin relatif du modèle -> chemin absolu sûr, sinon PathError."""
        if not isinstance(rel, str) or '\0' in rel:
            raise PathError('chemin invalide')
        rel = rel.strip().replace('\\', '/')
        if rel.startswith(('/', '~')) or re.match(r'^[A-Za-z]:', rel):
            raise PathError(f'chemin absolu refusé : {rel}')
        parts = [p for p in rel.split('/') if p not in ('', '.')]
        if '..' in parts:
            raise PathError(f'« .. » refusé : {rel}')
        if not parts and not allow_root:
            raise PathError('chemin vide')
        if denied(parts):
            raise PathError(f'accès interdit : {rel}')
        real = os.path.realpath(os.path.join(self.root, *parts))
        if real != self.root and not real.startswith(self.root + os.sep):
            raise PathError(f'sort du projet (lien symbolique ?) : {rel}')
        if real != self.root and denied(os.path.relpath(real, self.root).split(os.sep)):
            raise PathError(f'accès interdit : {rel}')
        return real

    def rel(self, path):
        return os.path.relpath(path, self.root).replace(os.sep, '/')

    def walk(self, sub='.'):
        base = self.resolve(sub, allow_root=True)
        for d, dirs, files in os.walk(base):
            dirs[:] = sorted(x for x in dirs if not denied([x]) and not os.path.islink(os.path.join(d, x)))
            for f in sorted(files):
                if denied([f]):
                    continue
                p = os.path.join(d, f)
                if os.path.islink(p):
                    try:
                        self.resolve(self.rel(p))
                    except PathError:
                        continue
                yield p

    # --- outils donnés au modèle -----------------------------------------
    def list_files(self, path='.', pattern=None, limit=300):
        out = []
        for p in self.walk(path or '.'):
            r = self.rel(p)
            if pattern and not fnmatch.fnmatch(r, pattern) and not fnmatch.fnmatch(os.path.basename(r), pattern):
                continue
            out.append(f'{r} ({os.path.getsize(p)} o)')
            if len(out) >= limit:
                out.append(f'… (liste tronquée à {limit})')
                break
        return '\n'.join(out) or '(aucun fichier)'

    def read_file(self, path, offset=1, limit=MAX_READ_LINES):
        full = self.resolve(path)
        if not os.path.isfile(full):
            raise PathError(f'fichier introuvable : {path}')
        with open(full, encoding='utf-8', errors='replace') as f:
            lines = f.read().split('\n')
        offset = max(1, int(offset or 1))
        limit = max(1, min(int(limit or MAX_READ_LINES), 2000))
        chunk = lines[offset - 1: offset - 1 + limit]
        body = []
        for ln in chunk:
            body.append(ln if len(ln) <= MAX_LINE else ln[:MAX_LINE] + f' …[ligne tronquée, {len(ln)} caractères]')
        end = offset + len(chunk) - 1
        head = f'[{path} — lignes {offset}-{end} sur {len(lines)}]'
        if end < len(lines):
            head += f' (suite avec offset={end + 1})'
        return head + '\n' + '\n'.join(body)

    def search(self, pattern, path='.', regex=False, glob=None):
        if not pattern:
            raise PathError('motif vide')
        rx = re.compile(pattern if regex else re.escape(pattern), re.IGNORECASE)
        out = self._search(rx, path, glob, strip=False)
        if out:
            return out
        # Secours : texte visible coupé par des balises (« Cluster <em>overview</em> »), ou regex passée sans regex=true
        out = self._search(rx, path, glob, strip=True)
        if not out and not regex and re.search(r'\\[.dswb]|\.\*|\[|\(', pattern):
            try:
                out = self._search(re.compile(pattern, re.IGNORECASE), path, glob, strip=False)
            except re.error:
                pass
        return out or '(aucun résultat)'

    def _search(self, rx, path, glob, strip):
        hits = []
        for p in self.walk(path or '.'):
            r = self.rel(p)
            if glob and not fnmatch.fnmatch(os.path.basename(r), glob) and not fnmatch.fnmatch(r, glob):
                continue
            if os.path.getsize(p) > 5_000_000:
                continue
            try:
                with open(p, encoding='utf-8') as f:
                    for i, ln in enumerate(f, 1):
                        m = rx.search(TAG.sub('', ln) if strip else ln)
                        if m:
                            ln = ln.rstrip('\n')
                            if len(ln) > 300:  # contexte autour du résultat (position approchée si balises retirées)
                                if strip:
                                    ln = TAG.sub('', ln)
                                s = max(0, m.start() - 120)
                                ln = ('…' if s else '') + ln[s:s + 300] + '…'
                            hits.append(f'{r}:{i}: {ln}')
                            if len(hits) >= MAX_SEARCH:
                                return '\n'.join(hits) + f'\n… (limité à {MAX_SEARCH} résultats)'
            except (UnicodeDecodeError, OSError):
                continue
        return '\n'.join(hits)

    def write_file(self, path, content, tx=None):
        full = self.resolve(path)
        if os.path.isdir(full):
            raise PathError(f'{path} est un dossier')
        if tx:
            tx.backup(full)
        existed = os.path.exists(full)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, 'w', encoding='utf-8', newline='') as f:
            f.write(content)
        return f'{"Réécrit" if existed else "Créé"} : {path} ({len(content)} caractères)'

    def edit_file(self, path, old_text, new_text, replace_all=False, tx=None):
        full = self.resolve(path)
        if not os.path.isfile(full):
            raise PathError(f'fichier introuvable : {path}')
        if not old_text:
            raise PathError('old_text est vide')
        with open(full, encoding='utf-8', newline='') as f:
            src = f.read()
        n = src.count(old_text)
        if n == 1 or (n > 1 and replace_all):
            out = src.replace(old_text, new_text) if replace_all else src.replace(old_text, new_text, 1)
        elif n > 1:
            raise PathError(f'old_text apparaît {n} fois dans {path} : ajoute du contexte pour qu’il soit unique, ou replace_all=true')
        else:
            span = fuzzy_span(src, old_text)
            if not span:
                raise PathError(f'old_text introuvable dans {path} : relis le fichier et copie le texte exact')
            out = src[:span[0]] + reindent(new_text, src[span[0]:span[1]], old_text) + src[span[1]:]
        if tx:
            tx.backup(full)
        with open(full, 'w', encoding='utf-8', newline='') as f:
            f.write(out)
        return f'Modifié : {path}'


def fuzzy_span(src, old):
    """Secours pour les petits modèles : même suite de lignes en ignorant l'indentation et les espaces de fin."""
    want = [l.strip() for l in old.strip('\n').split('\n')]
    if not any(want):
        return None
    lines = src.split('\n')
    starts, pos = [], 0
    for l in lines:
        starts.append(pos)
        pos += len(l) + 1
    found = [i for i in range(len(lines) - len(want) + 1) if [l.strip() for l in lines[i:i + len(want)]] == want]
    if len(found) != 1:
        return None
    i = found[0]
    end = starts[i + len(want) - 1] + len(lines[i + len(want) - 1])
    return starts[i], end


def reindent(new, matched, old):
    """Recale l'indentation du nouveau texte sur celle trouvée dans le fichier."""
    ind = lambda s: re.match(r'[ \t]*', s).group(0)
    have, gave = ind(matched), ind(old.strip('\n'))
    if have == gave:
        return new.strip('\n')
    out = []
    for l in new.strip('\n').split('\n'):
        out.append(have + l[len(gave):] if l.startswith(gave) else l)
    return '\n'.join(out)


# --- sauvegardes et annulation --------------------------------------------
class Transaction:
    """Fichiers touchés par une demande : contenu d'origine gardé avant la 1re écriture."""

    def __init__(self, store, job_id, prompt):
        self.store, self.id, self.prompt = store, job_id, prompt
        self.originals = {}   # rel -> bytes | None (fichier créé)
        self.order = []

    def backup(self, full):
        rel = self.store.project.rel(full)
        if rel in self.originals:
            return
        if os.path.isfile(full):
            with open(full, 'rb') as f:
                self.originals[rel] = f.read()
        else:
            self.originals[rel] = None
        self.order.append(rel)

    @property
    def files(self):
        return list(self.order)

    def commit(self):
        if self.originals:
            self.store.save(self)


class HistoryStore:
    """.livequill/history/<horodatage>-<id>/ : manifest.json + copies des fichiers d'origine."""

    def __init__(self, project):
        self.project = project
        self.dir = os.path.join(project.work, 'history')
        self.lock = threading.Lock()

    def begin(self, job_id, prompt):
        return Transaction(self, job_id, prompt)

    def save(self, tx):
        ensure_workdir(self.project)
        with self.lock:
            d = os.path.join(self.dir, f'{time.strftime("%Y%m%d-%H%M%S")}-{tx.id}')
            os.makedirs(os.path.join(d, 'files'), exist_ok=True)
            entries = []
            for i, rel in enumerate(tx.order):
                data = tx.originals[rel]
                blob = None
                if data is not None:
                    blob = f'{i}.bak'
                    with open(os.path.join(d, 'files', blob), 'wb') as f:
                        f.write(data)
                entries.append({'path': rel, 'blob': blob})
            with open(os.path.join(d, 'manifest.json'), 'w', encoding='utf-8') as f:
                json.dump({'job': tx.id, 'prompt': tx.prompt, 'time': time.time(), 'files': entries}, f, ensure_ascii=False, indent=1)

    def entries(self):
        if not os.path.isdir(self.dir):
            return []
        return sorted(x for x in os.listdir(self.dir) if os.path.isfile(os.path.join(self.dir, x, 'manifest.json')))

    def count(self):
        return len(self.entries())

    def undo(self):
        """Restaure la dernière modification. Renvoie (job_id, fichiers) ou None."""
        with self.lock:
            entries = self.entries()
            if not entries:
                return None
            d = os.path.join(self.dir, entries[-1])
            with open(os.path.join(d, 'manifest.json'), encoding='utf-8') as f:
                man = json.load(f)
            restored = []
            for e in man['files']:
                full = self.project.resolve(e['path'])
                if e['blob'] is None:
                    if os.path.isfile(full):
                        os.remove(full)
                else:
                    os.makedirs(os.path.dirname(full), exist_ok=True)
                    shutil.copyfile(os.path.join(d, 'files', e['blob']), full)
                restored.append(e['path'])
            done = os.path.join(self.project.work, 'undone')
            os.makedirs(done, exist_ok=True)
            shutil.move(d, os.path.join(done, entries[-1]))
            return man['job'], restored


class Snapshot:
    """Pour les agents CLI qui écrivent eux-mêmes : copie en mémoire avant, puis diff après."""

    def __init__(self, project, max_file=5_000_000, max_total=300_000_000):
        self.project, self.files, total = project, {}, 0
        for p in project.walk():
            try:
                st = os.stat(p)
                if st.st_size > max_file or total + st.st_size > max_total:
                    self.files[p] = (st.st_mtime_ns, st.st_size, None)
                    continue
                with open(p, 'rb') as f:
                    self.files[p] = (st.st_mtime_ns, st.st_size, f.read())
                total += st.st_size
            except OSError:
                pass

    def record_changes(self, tx):
        now = set()
        for p in self.project.walk():
            now.add(p)
            st = os.stat(p)
            old = self.files.get(p)
            if old is None:
                tx.originals.setdefault(self.project.rel(p), None)
                tx.order.append(self.project.rel(p))
            elif (st.st_mtime_ns, st.st_size) != old[:2] and old[2] is not None:
                with open(p, 'rb') as f:
                    if f.read() != old[2]:
                        tx.originals[self.project.rel(p)] = old[2]
                        tx.order.append(self.project.rel(p))
        for p, old in self.files.items():
            if p not in now and old[2] is not None:
                tx.originals[self.project.rel(p)] = old[2]
                tx.order.append(self.project.rel(p))
        return tx.files


def ensure_workdir(project):
    os.makedirs(project.work, exist_ok=True)
    gi = os.path.join(project.work, '.gitignore')
    if not os.path.exists(gi):
        with open(gi, 'w') as f:
            f.write('*\n')   # le dossier s'ignore lui-même : rien à changer dans le .gitignore du projet


class Conversation:
    """Historique par projet (.livequill/conversation.json), en texte seul pour passer d'un modèle à l'autre."""

    def __init__(self, project):
        self.project = project
        self.path = os.path.join(project.work, 'conversation.json')
        self.lock = threading.Lock()

    def load(self):
        try:
            with open(self.path, encoding='utf-8') as f:
                return json.load(f)
        except (FileNotFoundError, ValueError):
            return []

    def _write(self, turns):
        ensure_workdir(self.project)
        with open(self.path + '.tmp', 'w', encoding='utf-8') as f:
            json.dump(turns[-200:], f, ensure_ascii=False, indent=1)
        os.replace(self.path + '.tmp', self.path)

    def add(self, turn):
        with self.lock:
            turns = self.load()
            turn.setdefault('id', uuid.uuid4().hex[:10])
            turns.append(turn)
            self._write(turns)

    def mark(self, job_id, **fields):
        with self.lock:
            turns = self.load()
            for t in turns:
                if t.get('job') == job_id:
                    t.update(fields)
            self._write(turns)

    def clear(self):
        with self.lock:
            self._write([])
