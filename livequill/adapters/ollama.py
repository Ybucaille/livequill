"""Ollama natif (/api/chat en NDJSON). Les capacités viennent de /api/show."""
import json, re, uuid

from .base import Adapter, levels

LEVELED_THINK = re.compile(r'gpt-oss')    # ces modèles prennent think: "low"|"medium"|"high"


class OllamaAdapter(Adapter):
    kind = 'ollama'
    default_base = 'http://127.0.0.1:11434'

    def list_models(self):
        data = self.http.json('GET', f'{self.base}/api/tags', timeout=5)
        out = []
        for m in (data or {}).get('models', []):
            meta = {}
            try:
                show = self.http.json('POST', f'{self.base}/api/show', body={'model': m['name']}, timeout=10)
                meta['capabilities'] = show.get('capabilities') or []
                ctx = [v for k, v in (show.get('model_info') or {}).items() if k.endswith('context_length')]
                if ctx:
                    meta['context'] = ctx[0]
            except Exception:
                pass
            size = (m.get('details') or {}).get('parameter_size')
            out.append({'id': m['name'], 'label': f'{m["name"]}' + (f' · {size}' if size else ''), 'meta': meta})
        return out

    def caps(self, model, meta=None):
        caps = (meta or {}).get('capabilities')
        if caps is None:      # vieille version d'Ollama : on tente les tools, l'agent se rabat sur le mode texte sinon
            caps = ['tools']
        over = self.conf.get('caps') or {}
        effort = None
        if 'thinking' in caps:
            effort = levels('low', 'medium', 'high') if LEVELED_THINK.search(model) else levels('off', 'on')
        return {'tools': over.get('tools', 'tools' in caps), 'vision': over.get('vision', 'vision' in caps),
                'effort': effort, 'fast': False}

    def convert(self, system, messages):
        out = [{'role': 'system', 'content': system}]
        for m in messages:
            if m['role'] == 'tool':
                for r in m['results']:
                    out.append({'role': 'tool', 'content': r['content'] or '(empty)', 'tool_name': r['name']})
                continue
            parts = m['content']
            msg = {'role': m['role'], 'content': '\n\n'.join(p['text'] for p in parts if p['type'] == 'text')}
            imgs = [p['data'] for p in parts if p['type'] == 'image']
            if imgs:
                msg['images'] = imgs
            calls = [{'function': {'name': p['name'], 'arguments': p['args']}} for p in parts if p['type'] == 'tool_call']
            if calls:
                msg['tool_calls'] = calls
            out.append(msg)
        return out

    def build_request(self, model, system, messages, tools, opts):
        body = {'model': model, 'messages': self.convert(system, messages), 'stream': True,
                'options': {'num_ctx': int(self.conf.get('num_ctx') or 16384)}}
        if tools:
            body['tools'] = [{'type': 'function', 'function': t} for t in tools]
        e = opts.get('effort')
        if e in ('low', 'medium', 'high') and LEVELED_THINK.search(model):
            body['think'] = e
        elif e in ('on', 'off'):
            body['think'] = e == 'on'
        return 'POST', f'{self.base}/api/chat', {}, body

    def parse_stream(self, lines):
        text, calls, stop = '', [], 'end'
        for ln in lines:
            if not ln.strip():
                continue
            try:
                ev = json.loads(ln)
            except ValueError:
                continue
            if ev.get('error'):
                raise RuntimeError(ev['error'])
            msg = ev.get('message') or {}
            if msg.get('thinking'):
                yield {'type': 'thinking', 'text': msg['thinking']}
            if msg.get('content'):
                text += msg['content']
                yield {'type': 'delta', 'text': msg['content']}
            for tc in msg.get('tool_calls') or []:
                f = tc.get('function') or {}
                args = f.get('arguments') or {}
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except ValueError:
                        args = {'_invalid_json': args}
                calls.append({'type': 'tool_call', 'id': tc.get('id') or 'call_' + uuid.uuid4().hex[:8], 'name': f.get('name', ''), 'args': args})
            if ev.get('done'):
                stop = 'max_tokens' if ev.get('done_reason') == 'length' else 'end'
        if text:
            yield {'type': 'part', 'part': {'type': 'text', 'text': text}}
        for c in calls:
            yield {'type': 'part', 'part': c}
        yield {'type': 'stop', 'reason': 'tool_use' if calls else stop}
