"""Accès aux fichiers du projet ciblé : chemins verrouillés au dossier, sauvegardes avant écriture, annulation."""
import errno, filecmp, fnmatch, json, os, re, shutil, stat, threading, time, uuid

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


O_DIR = getattr(os, 'O_DIRECTORY', 0)
O_NOFOLLOW = getattr(os, 'O_NOFOLLOW', 0)
SNAPSHOT_MAX = 2_000_000_000        # au-delà, on refuse de lancer un agent CLI plutôt que de promettre une annulation partielle


class SnapshotTooLarge(RuntimeError):
    pass


def _parent_fd(root, parts, create=False, replace_links=False):
    """Descripteur du dossier qui contient parts[-1], ouvert composant par composant sans jamais suivre de lien
    symbolique : un dossier échangé contre un lien entre la vérification et l'accès fait échouer l'accès."""
    fd = os.open(root, os.O_RDONLY | O_DIR)
    try:
        for name in parts[:-1]:
            if replace_links:
                try:
                    if stat.S_ISLNK(os.stat(name, dir_fd=fd, follow_symlinks=False).st_mode):
                        os.unlink(name, dir_fd=fd)
                except FileNotFoundError:
                    pass
            if create:
                try:
                    os.mkdir(name, 0o777, dir_fd=fd)
                except FileExistsError:
                    pass
            nfd = os.open(name, os.O_RDONLY | O_DIR | O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = nfd
        return fd
    except BaseException:
        os.close(fd)
        raise


def _copy_private(src, dst):
    """Copie d'un fichier (sans suivre de lien) vers un fichier lisible par soi seul."""
    with os.fdopen(os.open(src, os.O_RDONLY | O_NOFOLLOW), 'rb') as fin, \
            os.fdopen(os.open(dst, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'wb') as fout:
        shutil.copyfileobj(fin, fout, 1 << 20)


def _private_dir(path):
    os.makedirs(path, mode=0o700, exist_ok=True)
    os.chmod(path, 0o700)
    return path


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

    def open(self, full, mode='rb', create_dirs=False, **kw):
        """Ouvre un fichier du projet (chemin déjà vérifié par resolve) sans suivre aucun lien symbolique."""
        parts = os.path.relpath(full, self.root).split(os.sep)
        if parts == ['.'] or '..' in parts:
            raise PathError(f'chemin invalide : {full}')
        flags = os.O_RDONLY if mode.startswith('r') else os.O_WRONLY | os.O_CREAT | os.O_TRUNC
        try:
            pfd = _parent_fd(self.root, parts, create=create_dirs)
            try:
                fd = os.open(parts[-1], flags | O_NOFOLLOW, 0o666, dir_fd=pfd)
            finally:
                os.close(pfd)
        except OSError as e:
            if e.errno in (errno.ELOOP, errno.ENOTDIR, errno.EMLINK):
                raise PathError(f'lien symbolique refusé : {self.rel(full)}') from None
            raise
        return os.fdopen(fd, mode, **kw)

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
        with self.open(full, 'r', encoding='utf-8', errors='replace') as f:
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
        with self.open(full, 'w', create_dirs=True, encoding='utf-8', newline='') as f:
            f.write(content)
        return f'{"Réécrit" if existed else "Créé"} : {path} ({len(content)} caractères)'

    def write_bytes(self, path, data, tx=None):
        full = self.resolve(path)
        if os.path.isdir(full):
            raise PathError(f'{path} est un dossier')
        if tx:
            tx.backup(full)
        with self.open(full, 'wb', create_dirs=True) as f:
            f.write(data)
        return f'Copié : {path}'

    def edit_file(self, path, old_text, new_text, replace_all=False, tx=None):
        full = self.resolve(path)
        if not os.path.isfile(full):
            raise PathError(f'fichier introuvable : {path}')
        if not old_text:
            raise PathError('old_text est vide')
        with self.open(full, 'r', encoding='utf-8', newline='') as f:
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
        with self.open(full, 'w', encoding='utf-8', newline='') as f:
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
    """Fichiers touchés par une demande. L'original est copié sur disque, dans un dossier privé, avant la 1re écriture."""

    def __init__(self, store, job_id, prompt):
        self.store, self.id, self.prompt = store, job_id, prompt
        self.originals = {}   # rel -> None (fichier créé) | {'src': copie, 'mode': droits} | {'link': cible}
        self.order = []
        self._tmp = None

    def staging(self):
        if not self._tmp:
            ensure_workdir(self.store.project)
            self._tmp = _private_dir(os.path.join(self.store.project.work, 'tmp', f'tx-{self.id}-{uuid.uuid4().hex[:6]}'))
        return self._tmp

    def add(self, rel, original):
        if rel not in self.originals:
            self.originals[rel] = original
            self.order.append(rel)

    def backup(self, full):
        rel = self.store.project.rel(full)
        if rel in self.originals:
            return
        try:
            st = os.lstat(full)
        except FileNotFoundError:
            return self.add(rel, None)
        if stat.S_ISLNK(st.st_mode):
            return self.add(rel, {'link': os.readlink(full)})
        dst = os.path.join(self.staging(), f'{len(self.order)}.bak')
        _copy_private(full, dst)
        self.add(rel, {'src': dst, 'mode': stat.S_IMODE(st.st_mode)})

    @property
    def files(self):
        return list(self.order)

    def commit(self):
        try:
            if self.originals:
                self.store.save(self)
        finally:
            if self._tmp:
                shutil.rmtree(self._tmp, ignore_errors=True)


class HistoryStore:
    """.livequill/history/<horodatage>-<id>/ : manifest.json + copies des fichiers d'origine (dossiers en 700, copies en 600)."""

    def __init__(self, project):
        self.project = project
        self.dir = os.path.join(project.work, 'history')
        self.lock = threading.Lock()

    def begin(self, job_id, prompt):
        return Transaction(self, job_id, prompt)

    def save(self, tx):
        ensure_workdir(self.project)
        with self.lock:
            _private_dir(self.dir)
            d = _private_dir(os.path.join(self.dir, f'{time.strftime("%Y%m%d-%H%M%S")}-{tx.id}'))
            files = _private_dir(os.path.join(d, 'files'))
            entries = []
            for i, rel in enumerate(tx.order):
                o, e = tx.originals[rel], {'path': rel, 'blob': None}
                if o and 'src' in o:
                    e['blob'], e['mode'] = f'{i}.bak', o['mode']
                    os.replace(o['src'], os.path.join(files, e['blob']))     # même disque : un renommage, pas une copie
                elif o and 'link' in o:
                    e['link'] = o['link']
                entries.append(e)
            with os.fdopen(os.open(os.path.join(d, 'manifest.json'), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), 'w', encoding='utf-8') as f:
                json.dump({'job': tx.id, 'prompt': tx.prompt, 'time': time.time(), 'files': entries}, f, ensure_ascii=False, indent=1)

    def entries(self):
        if not os.path.isdir(self.dir):
            return []
        return sorted(x for x in os.listdir(self.dir) if os.path.isfile(os.path.join(self.dir, x, 'manifest.json')))

    def count(self):
        return len(self.entries())

    def _restore(self, e, d):
        """Remet un fichier dans son état d'origine. Un lien posé à sa place (ou à la place d'un dossier parent) est
        supprimé, jamais suivi : rien ne peut être écrit hors du projet."""
        parts = [x for x in e['path'].split('/') if x not in ('', '.')]
        if not parts or '..' in parts or e['path'].startswith('/') or parts[0] in ('.git', WORKDIR):
            raise PathError(f'chemin invalide dans l’historique : {e["path"]}')
        pfd = _parent_fd(self.project.root, parts, create=True, replace_links=True)
        name = parts[-1]
        try:
            try:
                if stat.S_ISDIR(os.stat(name, dir_fd=pfd, follow_symlinks=False).st_mode):
                    if e['blob'] is None and 'link' not in e:
                        return                      # un dossier créé par l'agent : laissé tel quel
                    raise PathError(f'un dossier occupe la place de {e["path"]}')
                os.unlink(name, dir_fd=pfd)
            except FileNotFoundError:
                pass
            if 'link' in e:
                os.symlink(e['link'], name, dir_fd=pfd)
            elif e['blob'] is not None:
                fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | O_NOFOLLOW, 0o600, dir_fd=pfd)
                with os.fdopen(fd, 'wb') as out, open(os.path.join(d, 'files', e['blob']), 'rb') as src:
                    shutil.copyfileobj(src, out, 1 << 20)
                    os.fchmod(out.fileno(), e.get('mode', 0o644))
        finally:
            os.close(pfd)

    def undo(self):
        """Restaure la dernière modification. Renvoie (job_id, fichiers) ou None. Si un fichier échoue, l'entrée est
        gardée : on peut réessayer (la restauration recommence à zéro, elle est sans effet sur ce qui est déjà remis)."""
        with self.lock:
            entries = self.entries()
            if not entries:
                return None
            d = os.path.join(self.dir, entries[-1])
            with open(os.path.join(d, 'manifest.json'), encoding='utf-8') as f:
                man = json.load(f)
            restored, errors = [], []
            for e in man['files']:
                try:
                    self._restore(e, d)
                    restored.append(e['path'])
                except (OSError, PathError) as ex:
                    errors.append(f'{e["path"]} ({ex})')
            if errors:
                raise PathError('annulation incomplète : ' + ', '.join(errors))
            done = _private_dir(os.path.join(self.project.work, 'undone'))
            shutil.move(d, os.path.join(done, entries[-1]))
            return man['job'], restored


def _scan(project):
    """Fichiers et liens du projet, sans suivre aucun lien. Les .env* sont inclus (un agent CLI peut les toucher) ;
    .git, node_modules et .livequill ne le sont pas."""
    skip = {'.git', 'node_modules', WORKDIR}
    out = {}
    for d, dirs, files in os.walk(project.root):
        keep = []
        for x in dirs:
            if x in skip:
                continue
            if os.path.islink(os.path.join(d, x)):
                files.append(x)             # lien vers un dossier : noté comme lien
            else:
                keep.append(x)
        dirs[:] = keep
        for f in files:
            p = os.path.join(d, f)
            try:
                st = os.lstat(p)
            except OSError:
                continue
            if stat.S_ISREG(st.st_mode) or stat.S_ISLNK(st.st_mode):
                out[p] = st
    return out


class Snapshot:
    """Pour les agents CLI qui écrivent eux-mêmes : copie du projet sur disque avant, comparaison après.

    Détection d'un changement : type, taille, dates de modification et de changement d'inode, inode ; en cas de
    doute, le contenu est comparé. La date de changement (ctime) ne peut pas être remise à l'identique par un
    programme : une réécriture « discrète » est détectée."""

    def __init__(self, project):
        self.project = project
        self.state = _scan(project)
        total = sum(st.st_size for st in self.state.values() if stat.S_ISREG(st.st_mode))
        if total > SNAPSHOT_MAX:
            raise SnapshotTooLarge(f'projet trop volumineux pour garantir l’annulation : {total // 1_000_000} Mo hors .git et '
                                   f'node_modules (limite {SNAPSHOT_MAX // 1_000_000} Mo)')
        ensure_workdir(project)
        self.dir = _private_dir(os.path.join(project.work, 'tmp', f'snap-{uuid.uuid4().hex[:8]}'))
        self.copies = {}
        try:
            for i, (p, st) in enumerate(self.state.items()):
                if stat.S_ISLNK(st.st_mode):
                    self.copies[p] = os.readlink(p)
                else:
                    dst = os.path.join(self.dir, f'{i}.bak')
                    _copy_private(p, dst)
                    self.copies[p] = dst
        except BaseException:
            self.close()
            raise

    @staticmethod
    def _sig(st):
        return stat.S_IFMT(st.st_mode), st.st_size, st.st_mtime_ns, st.st_ctime_ns, st.st_ino

    def _keep(self, tx, p, st):
        rel = self.project.rel(p)
        if stat.S_ISLNK(st.st_mode):
            return tx.add(rel, {'link': self.copies[p]})
        dst = os.path.join(tx.staging(), f'snap-{len(tx.order)}.bak')
        os.replace(self.copies[p], dst)                 # la copie passe à la transaction : close() peut tout effacer
        tx.add(rel, {'src': dst, 'mode': stat.S_IMODE(st.st_mode)})

    def record_changes(self, tx):
        now = _scan(self.project)
        for p, old in self.state.items():
            cur = now.get(p)
            if cur is not None:
                if self._sig(cur) == self._sig(old):
                    continue
                if stat.S_ISREG(old.st_mode) and stat.S_ISREG(cur.st_mode) and filecmp.cmp(p, self.copies[p], shallow=False):
                    continue
                if stat.S_ISLNK(old.st_mode) and stat.S_ISLNK(cur.st_mode) and os.readlink(p) == self.copies[p]:
                    continue
            self._keep(tx, p, old)
        for p in now:
            if p not in self.state:
                tx.add(self.project.rel(p), None)
        return tx.files

    def close(self):
        shutil.rmtree(self.dir, ignore_errors=True)


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
