"""Boucle agent : demande -> modèle -> outils fichiers -> modèle… jusqu'à la réponse finale."""
import base64, json, os, re, threading, time, uuid

from . import adapters
from .adapters.base import ToolsUnsupported, text_of
from .sandbox import PathError, Snapshot, ensure_workdir

MAX_STEPS = 30
MAX_TEXT_ATTACHMENT = 100_000

TOOLS = [
    {'name': 'list_files', 'description': 'List project files (relative paths with sizes). Optional sub-folder and glob pattern such as "*.css".',
     'parameters': {'type': 'object', 'properties': {'path': {'type': 'string', 'description': 'Sub-folder, default "."'},
                                                     'pattern': {'type': 'string', 'description': 'Glob filter, e.g. "*.html"'}}}},
    {'name': 'search', 'description': 'Search text in project files (case-insensitive). Returns "file:line: text". Use it to find where to edit in big files.',
     'parameters': {'type': 'object', 'properties': {'pattern': {'type': 'string'}, 'path': {'type': 'string'},
                                                     'regex': {'type': 'boolean', 'description': 'Treat pattern as a regular expression'},
                                                     'glob': {'type': 'string', 'description': 'Only files matching, e.g. "*.css"'}},
                    'required': ['pattern']}},
    {'name': 'read_file', 'description': 'Read a text file. Returns up to `limit` lines from line `offset` (1-based). Very long lines are truncated.',
     'parameters': {'type': 'object', 'properties': {'path': {'type': 'string'}, 'offset': {'type': 'integer'}, 'limit': {'type': 'integer'}},
                    'required': ['path']}},
    {'name': 'edit_file', 'description': 'Replace an exact piece of text in a file. old_text must appear exactly once (copy it from read_file, with its indentation) unless replace_all is true.',
     'parameters': {'type': 'object', 'properties': {'path': {'type': 'string'}, 'old_text': {'type': 'string'}, 'new_text': {'type': 'string'},
                                                     'replace_all': {'type': 'boolean'}},
                    'required': ['path', 'old_text', 'new_text']}},
    {'name': 'write_file', 'description': 'Create a file or overwrite it entirely. Prefer edit_file for changes to existing files.',
     'parameters': {'type': 'object', 'properties': {'path': {'type': 'string'}, 'content': {'type': 'string'}}, 'required': ['path', 'content']}},
    {'name': 'save_attachment', 'description': 'Copy a file the user attached into the project (e.g. an image to use on the page).',
     'parameters': {'type': 'object', 'properties': {'name': {'type': 'string', 'description': 'Attachment name as listed in the request'},
                                                     'path': {'type': 'string', 'description': 'Destination path in the project'}},
                    'required': ['name', 'path']}},
]

NUDGE = ('You stopped without changing any file and without an answer. Continue the task now: use search/read_file to locate '
         'the code, then edit_file to make the change. If it is impossible, explain why in one sentence.')

SYSTEM = """You are a web development agent inside a live-edit tool. The user is looking at their website in a browser and asks for changes in plain language. You edit the project's source files directly with your tools; the page reloads by itself when you are done.

Rules:
- All paths are relative to the project root. .git/, node_modules/, .env* and .livequill/ are off limits.
- Find the relevant code with `search` first (files can be huge), then `read_file` around the matches.
- Make the smallest change that does what was asked, with `edit_file` (old_text copied exactly from the file). Use `write_file` only for new files or tiny files.
- Words like "title", "button" or "text" mean what the user SEES on the page (see the visible headings), not the HTML <title> tag.
- Never reformat or rewrite unrelated code. Do not ask for confirmation: act, then stop.
- When finished, answer in one or two short sentences, in the user's language, saying what you changed. If you could not do it, say why."""

TEXT_PROTOCOL = """

You cannot call functions natively, so use this text protocol. To call a tool, write a fenced block and then STOP your message:
```tool
{"name": "search", "args": {"pattern": "<h1"}}
```
The results come back in the next message. You may put several tool blocks in one message.
Tools: list_files(path?, pattern?), search(pattern, path?, regex?, glob?), read_file(path, offset?, limit?), edit_file(path, old_text, new_text, replace_all?), write_file(path, content), save_attachment(name, path).
You may also edit with a SEARCH/REPLACE block:
path/to/file.html
<<<<<<< SEARCH
exact existing text
=======
replacement text
>>>>>>> REPLACE
When everything is done, reply with a short summary and no tool block."""

SR_BLOCK = re.compile(r'^([^\n<>=`]+?)\s*\n<<<<<<< SEARCH\n(.*?)\n=======\n(.*?)\n>>>>>>> REPLACE', re.S | re.M)


class Cancelled(Exception):
    pass


class Job:
    """Une demande en cours : événements rejouables (SSE) pour que la barre se raccroche après un rechargement."""

    def __init__(self, req):
        self.id = uuid.uuid4().hex[:12]
        self.req = req
        self.events = []
        self.cond = threading.Condition()
        self.done = False
        self.cancelled = False
        self._cancel_cbs = []
        self.files = []

    def emit(self, ev):
        with self.cond:
            ev = dict(ev, i=len(self.events), t=round(time.time(), 3))
            self.events.append(ev)
            self.cond.notify_all()

    def finish(self):
        with self.cond:
            self.done = True
            self.cond.notify_all()

    def on_cancel(self, cb):
        self._cancel_cbs.append(cb)
        if self.cancelled:
            cb()

    def cancel(self):
        self.cancelled = True
        for cb in self._cancel_cbs:
            try:
                cb()
            except Exception:
                pass

    def wait_events(self, start, timeout=15):
        with self.cond:
            if len(self.events) <= start and not self.done:
                self.cond.wait(timeout)
            return self.events[start:], self.done


# --- outils --------------------------------------------------------------
class Toolbox:
    def __init__(self, project, tx, attachments, emit):
        self.p, self.tx, self.att, self.emit = project, tx, attachments, emit

    def run(self, name, args):
        args = args if isinstance(args, dict) else {}
        if '_invalid_json' in args:
            return 'Error: the tool arguments were not valid JSON.', True
        try:
            if name == 'list_files':
                return self.p.list_files(args.get('path') or '.', args.get('pattern')), False
            if name == 'search':
                return self.p.search(str(args.get('pattern', '')), args.get('path') or '.', bool(args.get('regex')), args.get('glob')), False
            if name == 'read_file':
                return self.p.read_file(args['path'], args.get('offset') or 1, args.get('limit') or 400), False
            if name == 'edit_file':
                out = self.p.edit_file(args['path'], args.get('old_text', ''), args.get('new_text', ''), bool(args.get('replace_all')), tx=self.tx)
                self.emit({'type': 'file', 'path': args['path'], 'action': 'edit'})
                return out, False
            if name == 'write_file':
                out = self.p.write_file(args['path'], str(args.get('content', '')), tx=self.tx)
                self.emit({'type': 'file', 'path': args['path'], 'action': 'write'})
                return out, False
            if name == 'save_attachment':
                a = next((a for a in self.att if a['name'] == args.get('name')), None)
                if not a:
                    return f'Error: no attachment named {args.get("name")!r}. Available: {[a["name"] for a in self.att]}', True
                full = self.p.resolve(args['path'])
                self.tx.backup(full)
                os.makedirs(os.path.dirname(full), exist_ok=True)
                with open(full, 'wb') as f:
                    f.write(a['bytes'])
                self.emit({'type': 'file', 'path': args['path'], 'action': 'write'})
                return f'Copié : {args["path"]}', False
            return f'Error: unknown tool {name!r}', True
        except KeyError as e:
            return f'Error: missing argument {e}', True
        except (PathError, OSError, re.error, ValueError) as e:
            return f'Error: {e}', True


def summarize(name, args):
    args = args if isinstance(args, dict) else {}
    target = args.get('pattern') or args.get('path') or ''
    if name == 'search' and args.get('pattern'):
        target = repr(args['pattern'])[:80]
    return f'{name} {target}'.strip()


# --- construction du contexte ---------------------------------------------
def decode_attachments(project, files):
    """Pièces jointes (data URL) -> enregistrées dans .livequill/uploads/ + décodées pour le modèle."""
    out = []
    if not files:
        return out
    ensure_workdir(project)
    up = os.path.join(project.work, 'uploads')
    os.makedirs(up, exist_ok=True)
    for i, f in enumerate(files):
        name = re.sub(r'[^\w.-]+', '_', os.path.basename(str(f.get('name') or 'fichier')))[:80] or 'fichier'
        raw = str(f.get('data', ''))
        mime = str(f.get('type') or 'application/octet-stream')
        m = re.match(r'data:([^;,]+)?(;base64)?,', raw)
        if m and m.group(1):
            mime = m.group(1)
        data = base64.b64decode(raw.split(',', 1)[-1]) if ';base64' in raw[:100] or not raw.startswith('data:') else raw.split(',', 1)[-1].encode()
        path = os.path.join(up, f'{time.strftime("%Y%m%d-%H%M%S")}-{i}-{name}')
        with open(path, 'wb') as fh:
            fh.write(data)
        out.append({'name': name, 'mime': mime, 'bytes': data, 'path': path})
    return out


def attachment_parts(atts, vision):
    parts, notes = [], []
    for a in atts:
        if a['mime'].startswith('image/') and vision and a['mime'] in ('image/png', 'image/jpeg', 'image/gif', 'image/webp'):
            parts.append({'type': 'image', 'media_type': a['mime'], 'data': base64.b64encode(a['bytes']).decode()})
            notes.append(f'- {a["name"]} (image, shown below)')
            continue
        try:
            txt = a['bytes'].decode('utf-8')
            if len(txt) > MAX_TEXT_ATTACHMENT:
                txt = txt[:MAX_TEXT_ATTACHMENT] + '\n…[tronqué]'
            notes.append(f'- {a["name"]} (text):\n```\n{txt}\n```')
        except UnicodeDecodeError:
            notes.append(f'- {a["name"]} ({a["mime"]}, {len(a["bytes"])} bytes — not viewable by you, but save_attachment can copy it into the project)')
    return parts, notes


def page_hint(project, page):
    """Fichier HTML qui correspond à l'URL affichée (mode dossier statique)."""
    path = (page or {}).get('pathname') or '/'
    rel = path.lstrip('/')
    for cand in ([rel] if rel and not rel.endswith('/') else []) + [rel.rstrip('/') + '/index.html' if rel else 'index.html', rel + '.html']:
        cand = cand.lstrip('/')
        try:
            if cand and os.path.isfile(project.resolve(cand)):
                return cand
        except PathError:
            pass
    return None


def history_messages(turns, limit=8):
    msgs = []
    for t in [t for t in turns if t.get('status') in ('ok', 'undone')][-limit:]:
        msgs.append({'role': 'user', 'content': [{'type': 'text', 'text': f'[Earlier request, page {t.get("page", "?")}]\n{t["prompt"]}'}]})
        a = t.get('answer') or '(done)'
        if t.get('files'):
            a += f'\n(Files changed: {", ".join(t["files"])})'
        if t.get('status') == 'undone':
            a += '\n(The user then UNDID this change: those files are back to their previous version.)'
        msgs.append({'role': 'assistant', 'content': [{'type': 'text', 'text': a}]})
    return msgs


def build_prompt(project, req, notes, mode):
    page = req.get('page') or {}
    url = f'{page.get("pathname", "/")}{page.get("search", "")}{page.get("hash", "")}'
    lines = [f'Page the user is looking at: {url}' + (f' — title “{page["title"]}”' if page.get('title') else '')]
    hint = page_hint(project, page) if mode == 'static' else None
    if hint:
        lines.append(f'This page is served from the file: {hint}')
    if req.get('outline'):
        lines.append('Visible headings on the page (tag.class "text"):\n' + req['outline'])
    if req.get('selection'):
        lines.append(f'Text the user selected on the page: “{req["selection"]}”')
    lines.append('Project files:\n' + project.list_files(limit=120))
    if notes:
        lines.append('Attachments:\n' + '\n'.join(notes))
    lines.append(f'Request:\n{req["text"] or "(see attachments)"}')
    return '\n\n'.join(lines)


# --- exécution ------------------------------------------------------------
def run(ctx, job):
    """ctx : project, config, history, conversation, mode, transport (facultatif)."""
    req, project = job.req, ctx.project
    conf = ctx.config.get(req['provider'])
    if not conf:
        raise RuntimeError('Fournisseur inconnu : reconfigure-le dans les réglages')
    ad = adapters.make(conf, getattr(ctx, 'transport', None))
    model = req['model']
    meta = next((m.get('meta') for m in conf.get('models', []) if m['id'] == model), None)
    caps = ad.caps(model, meta)
    opts = {'effort': req.get('effort') if caps.get('effort') else None, 'fast': bool(req.get('fast') and caps.get('fast'))}
    tx = ctx.history.begin(job.id, req['text'])
    atts = decode_attachments(project, req.get('files'))
    job.emit({'type': 'start', 'provider': req['provider'], 'model': model, 'prompt': req['text'],
              'label': conf.get('label') or adapters.KINDS[conf['type']]['label']})
    try:
        if ad.is_cli:
            answer = run_cli(ctx, job, ad, model, opts, atts, tx)
        else:
            answer = run_api(ctx, job, ad, model, opts, caps, atts, tx, conf)
    finally:
        tx.commit()
        job.files = tx.files
    return answer, tx.files


def run_cli(ctx, job, ad, model, opts, atts, tx):
    project, req = ctx.project, job.req
    notes = [f'- {a["name"]}: {project.rel(a["path"])}' for a in atts]
    prompt = build_prompt(project, req, notes, ctx.mode)
    hist = history_messages(ctx.conversation.load(), 5)
    if hist:
        prompt = 'Earlier in this session:\n' + '\n'.join(f'{m["role"]}: {text_of(m["content"])}' for m in hist) + '\n\n' + prompt
    prompt = SYSTEM + '\n\nRun no shell commands. Attachments are image/text files you can read at the paths given.\n\n' + prompt
    snap = Snapshot(project)
    try:
        def emit(ev):    # chemins affichés relatifs au projet
            for k in ('summary', 'path'):
                if isinstance(ev.get(k), str):
                    ev[k] = ev[k].replace(project.root + os.sep, '').replace(project.root, '.')
            job.emit(ev)
        answer = ad.run(job, project.root, prompt, model, opts, [a['path'] for a in atts if a['mime'].startswith('image/')], emit)
    finally:
        for f in snap.record_changes(tx):
            job.emit({'type': 'file', 'path': f, 'action': 'edit'})
    return answer


def run_api(ctx, job, ad, model, opts, caps, atts, tx, conf):
    project, req = ctx.project, job.req
    text_mode = not caps.get('tools') or conf.get('tool_mode') == 'text'
    img_parts, notes = attachment_parts(atts, caps.get('vision'))
    user = {'role': 'user', 'content': [{'type': 'text', 'text': build_prompt(project, req, notes, ctx.mode)}] + img_parts}
    messages = history_messages(ctx.conversation.load()) + [user]
    tools = Toolbox(project, tx, atts, job.emit)
    answer, nudged = '', False
    for _step in range(MAX_STEPS):
        if job.cancelled:
            raise Cancelled()
        system = SYSTEM + (TEXT_PROTOCOL if text_mode else '')
        parts, stop = [], 'end'
        try:
            for ev in ad.chat(model, system, messages, None if text_mode else TOOLS, opts, job):
                if job.cancelled:
                    raise Cancelled()
                t = ev['type']
                if t == 'delta':
                    job.emit({'type': 'text', 'text': ev['text']})
                elif t == 'thinking':
                    job.emit({'type': 'thinking', 'text': ev['text']})
                elif t == 'part':
                    parts.append(ev['part'])
                elif t == 'stop':
                    stop = ev['reason']
        except ToolsUnsupported:
            if text_mode:
                raise
            text_mode = True
            job.emit({'type': 'info', 'text': 'Ce modèle ne gère pas les tool calls : passage en mode texte.'})
            continue
        if job.cancelled:
            raise Cancelled()
        messages.append({'role': 'assistant', 'content': parts or [{'type': 'text', 'text': ''}]})
        if stop == 'refusal':
            raise RuntimeError('Le modèle a refusé la demande.')
        text = text_of(parts)
        calls = [p for p in parts if p['type'] == 'tool_call']
        if not text_mode and not calls and parse_text_calls(text)[0]:
            # Petit modèle qui écrit ses appels en texte (« {"name": …} </tool_call> ») : on passe au protocole texte
            text_mode = True
            job.emit({'type': 'info', 'text': 'Appel d’outil écrit en texte : passage en mode texte.'})
        if text_mode:
            calls, text = parse_text_calls(text)
            edits = SR_BLOCK.findall(text)
            results = []
            for path, old, new in edits:
                out, err = tools.run('edit_file', {'path': path.strip().strip('`'), 'old_text': old, 'new_text': new})
                results.append(f'SEARCH/REPLACE {path.strip()}: {out}')
            if not calls and not results:
                if not text.strip() and not tx.files and not nudged:
                    nudged = True
                    messages.append({'role': 'user', 'content': [{'type': 'text', 'text': NUDGE}]})
                    continue
                answer = text.strip()
                break
            for c in calls:
                job.emit({'type': 'tool', 'name': c['name'], 'summary': summarize(c['name'], c['args'])})
                out, err = tools.run(c['name'], c['args'])
                results.append(f'Result of {c["name"]}{" (ERROR)" if err else ""}:\n{out}')
                job.emit({'type': 'tool_result', 'ok': not err, 'summary': first_line(out)})
            if not calls:      # que des SEARCH/REPLACE : c'est la réponse finale
                answer = SR_STRIP.sub('', text).strip() or 'Modification appliquée.'
                job.emit({'type': 'tool_result', 'ok': True, 'summary': '; '.join(results)})
                break
            messages.append({'role': 'user', 'content': [{'type': 'text', 'text': '\n\n'.join(results)}]})
            job.emit({'type': 'text', 'text': '\n'})
            continue
        if not calls:
            if stop == 'max_tokens':
                raise RuntimeError('Réponse coupée : limite de tokens atteinte.')
            if not text.strip() and not tx.files and not nudged:
                nudged = True      # petit modèle qui s'arrête en route : une relance, une seule
                messages.append({'role': 'user', 'content': [{'type': 'text', 'text': NUDGE}]})
                continue
            answer = text.strip()
            break
        results = []
        for c in calls:
            job.emit({'type': 'tool', 'name': c['name'], 'summary': summarize(c['name'], c['args'])})
            out, err = tools.run(c['name'], c['args'])
            job.emit({'type': 'tool_result', 'ok': not err, 'summary': first_line(out)})
            results.append({'id': c['id'], 'name': c['name'], 'content': truncate(out), 'is_error': err})
        messages.append({'role': 'tool', 'results': results})
        if text:
            job.emit({'type': 'text', 'text': '\n'})
    else:
        answer = f'Arrêt après {MAX_STEPS} étapes.'
    return answer.strip()


SR_STRIP = re.compile(r'^[^\n<>=`]+?\s*\n<<<<<<< SEARCH\n.*?\n>>>>>>> REPLACE\n?', re.S | re.M)


TOOL_NAMES = {t['name'] for t in TOOLS}
TAGS = re.compile(r'</?tool_call>|```(?:tool|json)?[ \t]*\n?|```')


def parse_text_calls(text):
    """Appels d'outils écrits en texte : tout objet JSON {"name": <outil>, "args"|"arguments": {...}}.
    Renvoie (appels, texte sans ces appels)."""
    calls, spans, dec, i = [], [], json.JSONDecoder(), 0
    while True:
        i = text.find('{', i)
        if i < 0:
            break
        try:
            obj, end = dec.raw_decode(text, i)
        except ValueError:
            i += 1
            continue
        if isinstance(obj, dict) and obj.get('name') in TOOL_NAMES:
            args = obj.get('args') or obj.get('arguments') or obj.get('parameters') or {}
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except ValueError:
                    args = {'_invalid_json': args}
            calls.append({'type': 'tool_call', 'id': 'txt_' + uuid.uuid4().hex[:6], 'name': obj['name'], 'args': args})
            spans.append((i, end))
        i = end
    for a, b in reversed(spans):
        text = text[:a] + text[b:]
    if spans:
        text = TAGS.sub('', text)
    return calls, text


def first_line(s):
    s = str(s).strip().split('\n', 1)[0]
    return s[:160]


def truncate(s, n=60_000):
    return s if len(s) <= n else s[:n] + '\n…[résultat tronqué]'
