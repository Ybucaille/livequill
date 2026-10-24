"""Agents CLI déjà installés (Claude Code, Codex, Gemini CLI) : ils font leur propre boucle et écrivent les fichiers.

Pont vers les applications desktop : Claude Desktop embarque Claude Code, ChatGPT (et l'app Codex) embarquent Codex.
Sans CLI installée, on utilise l'agent de l'application, déjà connecté au compte de l'utilisateur."""
import glob, json, os, re, shutil, subprocess

from .base import Adapter, levels

AGENTS = {
    'claude': {'label': 'Claude Code', 'models': [('default', 'Par défaut (Claude Code)'), ('opus', 'Opus'), ('sonnet', 'Sonnet'), ('haiku', 'Haiku')],
               'effort': levels('low', 'medium', 'high', 'xhigh', 'max')},
    'codex': {'label': 'Codex', 'models': [('default', 'Par défaut (Codex)')], 'effort': levels('low', 'medium', 'high')},
    'gemini': {'label': 'Gemini CLI', 'models': [('default', 'Par défaut (Gemini CLI)')], 'effort': None},
}


# Où les installeurs officiels posent ces CLI, au cas où LiveQuill est lancé avec un PATH réduit
# (raccourci de bureau, service, autre session) qui ne contient pas ces dossiers.
EXTRA_DIRS = ['~/.local/bin', '~/bin', '~/.npm-global/bin', '~/.bun/bin', '~/.volta/bin', '~/.cargo/bin',
              '~/.claude/local', '/usr/local/bin', '/opt/homebrew/bin']


# Agents embarqués dans les applications desktop (Linux puis macOS). Le dossier de version change à chaque mise à jour
# de l'application : on cherche à chaque fois et on prend la version la plus récente.
BUNDLED = {
    'claude': [('Claude Desktop', '~/.config/Claude/claude-code/*/*/claude'),
               ('Claude Desktop', '~/Library/Application Support/Claude/claude-code/*/*/claude')],
    'codex': [('ChatGPT', '/usr/lib/chatgpt/resources/codex'),
              ('ChatGPT', '/opt/ChatGPT/resources/codex'),
              ('ChatGPT', '/Applications/ChatGPT.app/Contents/Resources/codex'),
              ('ChatGPT', '~/Applications/ChatGPT.app/Contents/Resources/codex'),
              ('Codex', '/Applications/Codex.app/Contents/Resources/codex'),
              ('Codex', '~/Applications/Codex.app/Contents/Resources/codex')],
}


def _version_key(path):
    return [int(n) for n in re.findall(r'\d+', path)]


def locate(name):
    """-> (chemin, application) : la CLI installée d'abord, sinon l'agent embarqué dans une application desktop."""
    exe = shutil.which(name)
    if exe:
        return exe, None
    for d in EXTRA_DIRS:
        p = os.path.join(os.path.expanduser(d), name)
        if os.path.isfile(p) and os.access(p, os.X_OK):
            return p, None
    for app, pattern in BUNDLED.get(name, []):
        hits = [p for p in glob.glob(os.path.expanduser(pattern)) if os.path.isfile(p) and os.access(p, os.X_OK)]
        if hits:
            return max(hits, key=_version_key), app
    return None, None


def find(name):
    return locate(name)[0]


def detect():
    return {name: find(name) for name in AGENTS}


def detect_via():
    """Application desktop qui fournit l'agent, quand ce n'est pas une CLI installée."""
    return {name: locate(name)[1] for name in AGENTS}


def codex_user_model():
    """Le modèle de premier niveau de ~/.codex/config.toml (seule valeur reprise de la config ignorée)."""
    path = os.path.join(os.environ.get('CODEX_HOME') or os.path.expanduser('~/.codex'), 'config.toml')
    try:
        with open(path, encoding='utf-8') as f:
            for line in f:
                if line.lstrip().startswith('['):
                    break                       # fin des clés de premier niveau
                m = re.match(r'\s*model\s*=\s*"([^"]+)"', line)
                if m:
                    return m.group(1)
    except OSError:
        pass
    return None


def logged_in(name, exe):
    """True / False si l'agent sait le dire, None sinon (vieille version, commande inconnue)."""
    try:
        if name == 'claude':
            out = subprocess.run([exe, 'auth', 'status', '--json'], capture_output=True, text=True, timeout=20)
            return bool(json.loads(out.stdout).get('loggedIn')) if out.stdout.strip().startswith('{') else None
        if name == 'codex':
            out = subprocess.run([exe, 'login', 'status'], capture_output=True, text=True, timeout=20)
            text = (out.stdout + out.stderr).lower()
            return True if 'logged in' in text and 'not logged in' not in text else (False if 'not logged in' in text else None)
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    return None


class CLIAdapter(Adapter):
    kind = 'cli'
    is_cli = True

    @property
    def agent(self):
        return self.conf.get('command', 'claude')

    def list_models(self):
        a = AGENTS[self.agent]
        extra = [m.strip() for m in (self.conf.get('extra_models') or '').split(',') if m.strip()]
        return [{'id': i, 'label': l, 'meta': {}} for i, l in a['models']] + [{'id': m, 'label': m, 'meta': {}} for m in extra]

    def caps(self, model, meta=None):
        return {'tools': True, 'vision': self.agent in ('claude', 'codex'), 'effort': AGENTS[self.agent]['effort'], 'fast': False}

    def test(self):
        exe, app = locate(self.agent)
        label = AGENTS[self.agent]['label']
        if not exe:
            raise RuntimeError(f'« {self.agent} » est introuvable : installe la CLI, ou l’application '
                               f'{"Claude Desktop" if self.agent == "claude" else "ChatGPT" if self.agent == "codex" else "officielle"}')
        if logged_in(self.agent, exe) is False:
            how = f'ouvre l’application {app} et connecte-toi' if app else ('lance « claude auth login »' if self.agent == 'claude' else 'lance « codex login »')
            raise RuntimeError(f'{label} n’est pas connecté à ton compte : {how}, puis reteste')
        out = subprocess.run([exe, '--version'], capture_output=True, text=True, timeout=20)
        version = (out.stdout or out.stderr).strip()[:80]
        return f'{label} trouvé{f" (via l’app {app})" if app else ""} : {version}', self.list_models()

    def command(self, root, model, opts, images, prompt=''):
        """-> (argv, texte envoyé sur stdin). Le prompt passe par stdin : pas de limite de taille d'argument."""
        exe = find(self.agent) or self.agent
        m = None if model in (None, '', 'default') else model
        e = opts.get('effort')
        if self.agent == 'claude':
            # --restricted : outils de fichiers confinés au dossier du projet, aucun outil qui exécute du code,
            # réglages utilisateur/projet ignorés (hooks compris) ; --strict-mcp-config : aucun serveur MCP de l'utilisateur
            argv = [exe, '-p', '--output-format', 'stream-json', '--verbose', '--include-partial-messages',
                    '--restricted', '--strict-mcp-config', '--tools', 'Read,Edit,Write,Glob,Grep',
                    '--permission-mode', 'acceptEdits', '--no-session-persistence',
                    '--disallowedTools', 'Bash', 'WebFetch', 'WebSearch', 'Read(./.env*)', 'Edit(./.env*)', 'Write(./.env*)',
                    '--allowedTools', 'Read', 'Edit', 'Write', 'Glob', 'Grep']
            if m:
                argv += ['--model', m]
            if e:
                argv += ['--effort', e]
            return argv
        if self.agent == 'codex':
            # --ignore-user-config : ni les serveurs MCP ni les dossiers inscriptibles ajoutés dans ~/.codex/config.toml
            # (un serveur MCP tourne hors de la sandbox) ; la connexion au compte reste celle de CODEX_HOME
            # Sans la config utilisateur, on fixe nous-mêmes : jamais d'approbation demandée (impossible en non
            # interactif ; les commandes restent dans la sandbox) et pas de réseau.
            argv = [exe, 'exec', '--json', '--sandbox', 'workspace-write', '--ignore-user-config',
                    '-c', 'approval_policy="never"', '-c', 'sandbox_workspace_write.network_access=false',
                    '--cd', root, '--skip-git-repo-check', '--ephemeral']
            m = m or codex_user_model()          # « Par défaut » garde le modèle choisi dans ~/.codex/config.toml
            if m:
                argv += ['--model', m]
            if e:
                argv += ['-c', f'model_reasoning_effort="{e}"']
            if images:
                argv += ['--image'] + images     # en dernier : l'option prend plusieurs valeurs
            return argv
        argv = [exe, '--approval-mode', 'auto_edit']
        if m:
            argv += ['-m', m]
        return argv + ['-p', prompt]   # non vérifié : gemini n'est pas installé sur la machine de dev

    def run(self, job, root, prompt, model, opts, images, emit):
        argv = self.command(root, model, opts, images, prompt)
        env = dict(os.environ, NO_COLOR='1')
        if os.path.isabs(argv[0]):     # trouvé hors du PATH : ses outils voisins (node…) doivent rester accessibles
            env['PATH'] = os.pathsep.join([os.path.dirname(argv[0]), env.get('PATH', '')])
        p = subprocess.Popen(argv, cwd=root, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             text=True, bufsize=1, env=env)
        job.on_cancel(p.kill)
        p.stdin.write('' if self.agent == 'gemini' else prompt)
        p.stdin.close()
        final = []
        for ln in p.stdout:
            for ev in self.parse_line(ln.rstrip('\n')):
                if ev['type'] == 'final':
                    final = [ev['text']]
                else:
                    if ev['type'] == 'delta':
                        final.append(ev['text'])
                    emit(ev)
        err = p.stderr.read()
        code = p.wait()
        if code and not job.cancelled:
            raise RuntimeError(f'{self.agent} a échoué (code {code}) : {err.strip()[-600:]}')
        return ''.join(final).strip()

    def parse_line(self, ln):
        if self.agent == 'gemini':
            return [{'type': 'delta', 'text': ln + '\n'}] if ln else []
        try:
            ev = json.loads(ln)
        except ValueError:
            return []
        return (parse_claude if self.agent == 'claude' else parse_codex)(ev)


def tool_summary(name, inp):
    path = inp.get('file_path') or inp.get('path') or inp.get('pattern') or ''
    return {'type': 'tool', 'name': name, 'summary': f'{name} {path}'.strip(), 'path': path}


def parse_claude(ev):
    t = ev.get('type')
    if t == 'stream_event':
        e = ev.get('event') or {}
        d = e.get('delta') or {}
        if e.get('type') == 'content_block_delta' and d.get('type') == 'text_delta':
            return [{'type': 'delta', 'text': d['text']}]
        if e.get('type') == 'content_block_delta' and d.get('type') == 'thinking_delta':
            return [{'type': 'thinking', 'text': d['thinking']}]
        return []
    if t == 'assistant':
        return [tool_summary(b['name'], b.get('input') or {}) for b in (ev.get('message') or {}).get('content', [])
                if b.get('type') == 'tool_use']
    if t == 'result':
        if ev.get('is_error'):
            raise RuntimeError(str(ev.get('result') or ev.get('subtype')))
        return [{'type': 'final', 'text': ev.get('result') or ''}]
    return []


def parse_codex(ev):
    t = ev.get('type', '')
    if t in ('error', 'turn.failed'):
        raise RuntimeError(str(ev.get('message') or ev.get('error') or ev))
    if t != 'item.completed':
        return []
    it = ev.get('item') or {}
    k = it.get('type') or it.get('item_type')
    if k in ('agent_message', 'assistant_message'):
        return [{'type': 'delta', 'text': (it.get('text') or '') + '\n'}]
    if k == 'reasoning':
        return [{'type': 'thinking', 'text': it.get('text') or ''}]
    if k == 'file_change':
        return [{'type': 'tool', 'name': 'edit', 'summary': f'edit {c.get("path")}', 'path': c.get('path')} for c in it.get('changes') or []]
    if k == 'command_execution':
        return [{'type': 'tool', 'name': 'shell', 'summary': f'$ {it.get("command")}'}]
    return []
