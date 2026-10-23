"""Agents CLI déjà installés (Claude Code, Codex, Gemini CLI) : ils font leur propre boucle et écrivent les fichiers."""
import json, os, shutil, subprocess

from .base import Adapter, levels

AGENTS = {
    'claude': {'label': 'Claude Code', 'models': [('default', 'Par défaut (Claude Code)'), ('opus', 'Opus'), ('sonnet', 'Sonnet'), ('haiku', 'Haiku')],
               'effort': levels('low', 'medium', 'high', 'xhigh', 'max')},
    'codex': {'label': 'Codex', 'models': [('default', 'Par défaut (config Codex)')], 'effort': levels('low', 'medium', 'high')},
    'gemini': {'label': 'Gemini CLI', 'models': [('default', 'Par défaut (Gemini CLI)')], 'effort': None},
}


# Où les installeurs officiels posent ces CLI, au cas où LiveQuill est lancé avec un PATH réduit
# (raccourci de bureau, service, autre session) qui ne contient pas ces dossiers.
EXTRA_DIRS = ['~/.local/bin', '~/bin', '~/.npm-global/bin', '~/.bun/bin', '~/.volta/bin', '~/.cargo/bin',
              '~/.claude/local', '/usr/local/bin', '/opt/homebrew/bin']


def find(name):
    """Chemin de l'exécutable : d'abord le PATH, puis les dossiers d'installation habituels."""
    exe = shutil.which(name)
    if exe:
        return exe
    for d in EXTRA_DIRS:
        p = os.path.join(os.path.expanduser(d), name)
        if os.path.isfile(p) and os.access(p, os.X_OK):
            return p
    return None


def detect():
    return {name: find(name) for name in AGENTS}


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
        exe = find(self.agent)
        if not exe:
            raise RuntimeError(f'« {self.agent} » est introuvable dans le PATH')
        out = subprocess.run([exe, '--version'], capture_output=True, text=True, timeout=20)
        return f'{AGENTS[self.agent]["label"]} trouvé : {(out.stdout or out.stderr).strip()[:80]}', self.list_models()

    def command(self, root, model, opts, images, prompt=''):
        """-> (argv, texte envoyé sur stdin). Le prompt passe par stdin : pas de limite de taille d'argument."""
        exe = find(self.agent) or self.agent
        m = None if model in (None, '', 'default') else model
        e = opts.get('effort')
        if self.agent == 'claude':
            # Pas de Bash ni d'accès web : l'agent ne fait que lire et éditer des fichiers
            argv = [exe, '-p', '--output-format', 'stream-json', '--verbose', '--include-partial-messages',
                    '--permission-mode', 'acceptEdits', '--no-session-persistence',
                    '--disallowedTools', 'Bash', 'WebFetch', 'WebSearch', 'Read(./.env*)', 'Edit(./.env*)',
                    '--allowedTools', 'Read', 'Edit', 'Write', 'Glob', 'Grep']
            if m:
                argv += ['--model', m]
            if e:
                argv += ['--effort', e]
            return argv
        if self.agent == 'codex':
            argv = [exe, 'exec', '--json', '--sandbox', 'workspace-write', '--cd', root, '--skip-git-repo-check', '--ephemeral']
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
