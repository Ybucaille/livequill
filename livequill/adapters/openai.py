"""OpenAI Chat Completions et tous les compatibles : OpenAI, Mistral, OpenRouter, LM Studio, llama.cpp, endpoint perso."""
import json, re

from ..net import sse
from .base import Adapter, levels

REASONING = re.compile(r'^(o\d|gpt-5)')
OPENAI_VISION = re.compile(r'^(gpt-4o|gpt-4\.1|gpt-5|o\d|chatgpt)')
OPENAI_CHAT = re.compile(r'^(gpt-|o\d|chatgpt)')
OPENAI_SKIP = re.compile(r'(audio|realtime|transcribe|tts|image|search|embedding|moderation|instruct)')
MISTRAL_VISION = re.compile(r'(pixtral|mistral-(medium|small)|magistral)')


class OpenAIAdapter(Adapter):
    kind = 'openai'
    default_base = 'https://api.openai.com/v1'

    def headers(self):
        h = {}
        if self.key:
            h['Authorization'] = f'Bearer {self.key}'
        if self.kind == 'openrouter':
            h['X-Title'] = 'livequill'
        return h

    def list_models(self):
        data = self.http.json('GET', f'{self.base}/models', self.headers())
        out = []
        for m in (data or {}).get('data', []):
            mid = m.get('id')
            if not mid:
                continue
            if self.kind == 'openai' and (not OPENAI_CHAT.match(mid) or OPENAI_SKIP.search(mid)):
                continue
            meta = {}
            if 'supported_parameters' in m:     # OpenRouter décrit les capacités de chaque modèle
                meta['params'] = m.get('supported_parameters') or []
                meta['input'] = (m.get('architecture') or {}).get('input_modalities') or []
            if 'capabilities' in m and isinstance(m['capabilities'], dict):   # Mistral
                meta['mistral'] = m['capabilities']
            out.append({'id': mid, 'label': m.get('name') or mid, 'meta': meta})
        if self.kind == 'custom' and self.conf.get('model') and not any(x['id'] == self.conf['model'] for x in out):
            out.insert(0, {'id': self.conf['model'], 'label': self.conf['model'], 'meta': {}})
        return sorted(out, key=lambda x: x['id'])

    def caps(self, model, meta=None):
        meta = meta or {}
        over = self.conf.get('caps') or {}          # réglage manuel dans la page de setup
        c = {'tools': True, 'vision': False, 'effort': None, 'fast': False}
        if self.kind == 'openai':
            c['vision'] = bool(OPENAI_VISION.match(model))
            if REASONING.match(model):
                c['effort'] = levels('low', 'medium', 'high')
        elif self.kind == 'openrouter':
            p = meta.get('params', [])
            c['tools'] = 'tools' in p if p else True
            c['vision'] = 'image' in meta.get('input', [])
            if 'reasoning' in p:
                c['effort'] = levels('low', 'medium', 'high')
        elif self.kind == 'mistral':
            mc = meta.get('mistral') or {}
            c['tools'] = mc.get('function_calling', True)
            c['vision'] = mc.get('vision', bool(MISTRAL_VISION.search(model)))
        for k in ('tools', 'vision'):
            if k in over:
                c[k] = bool(over[k])
        return c

    def convert(self, system, messages):
        out = [{'role': 'system', 'content': system}]
        for m in messages:
            if m['role'] == 'tool':
                for r in m['results']:
                    out.append({'role': 'tool', 'tool_call_id': r['id'], 'content': r['content'] or '(vide)'})
                continue
            parts = m['content']
            if m['role'] == 'assistant':
                msg = {'role': 'assistant', 'content': ''.join(p['text'] for p in parts if p['type'] == 'text') or None}
                calls = [{'id': p['id'], 'type': 'function', 'function': {'name': p['name'], 'arguments': json.dumps(p['args'], ensure_ascii=False)}}
                         for p in parts if p['type'] == 'tool_call']
                if calls:
                    msg['tool_calls'] = calls
                if msg['content'] is None and not calls:
                    msg['content'] = '…'
                out.append(msg)
                continue
            if all(p['type'] == 'text' for p in parts):
                out.append({'role': 'user', 'content': '\n\n'.join(p['text'] for p in parts)})
            else:
                out.append({'role': 'user', 'content': [
                    {'type': 'text', 'text': p['text']} if p['type'] == 'text'
                    else {'type': 'image_url', 'image_url': {'url': f'data:{p["media_type"]};base64,{p["data"]}'}}
                    for p in parts if p['type'] in ('text', 'image')]})
        return out

    def build_request(self, model, system, messages, tools, opts):
        body = {'model': model, 'messages': self.convert(system, messages), 'stream': True}
        if tools:
            body['tools'] = [{'type': 'function', 'function': t} for t in tools]
        eff = self.caps(model).get('effort') if self.kind != 'openrouter' else levels('low', 'medium', 'high')
        if opts.get('effort') and eff and opts['effort'] in dict(eff):
            if self.kind == 'openrouter':
                body['reasoning'] = {'effort': opts['effort']}
            else:
                body['reasoning_effort'] = opts['effort']
        if self.kind == 'openai':
            body['stream_options'] = {'include_usage': True}
        return 'POST', f'{self.base}/chat/completions', self.headers(), body

    def parse_stream(self, lines):
        text, calls, stop = '', {}, 'end'
        for _, data in sse(lines):
            if data.strip() == '[DONE]':
                break
            try:
                ev = json.loads(data)
            except ValueError:
                continue
            if ev.get('error'):
                raise RuntimeError(ev['error'].get('message') if isinstance(ev['error'], dict) else str(ev['error']))
            for ch in ev.get('choices') or []:
                d = ch.get('delta') or {}
                if d.get('content'):
                    text += d['content']
                    yield {'type': 'delta', 'text': d['content']}
                r = d.get('reasoning_content') or d.get('reasoning')
                if isinstance(r, str) and r:
                    yield {'type': 'thinking', 'text': r}
                for tc in d.get('tool_calls') or []:
                    c = calls.setdefault(tc.get('index', len(calls)), {'id': '', 'name': '', 'args': ''})
                    c['id'] = tc.get('id') or c['id']
                    f = tc.get('function') or {}
                    c['name'] += f.get('name') or ''
                    c['args'] += f.get('arguments') or ''
                fr = ch.get('finish_reason')
                if fr:
                    stop = {'tool_calls': 'tool_use', 'function_call': 'tool_use', 'length': 'max_tokens',
                            'content_filter': 'refusal'}.get(fr, 'end')
        if text:
            yield {'type': 'part', 'part': {'type': 'text', 'text': text}}
        for i, c in sorted(calls.items()):
            try:
                args = json.loads(c['args'] or '{}')
            except ValueError:
                args = {'_invalid_json': c['args']}
            yield {'type': 'part', 'part': {'type': 'tool_call', 'id': c['id'] or f'call_{i}', 'name': c['name'], 'args': args}}
        if calls:
            stop = 'tool_use'
        yield {'type': 'stop', 'reason': stop}


def compatible(kind, base):
    return type(f'{kind.title()}Adapter', (OpenAIAdapter,), {'kind': kind, 'default_base': base})


MistralAdapter = compatible('mistral', 'https://api.mistral.ai/v1')
OpenRouterAdapter = compatible('openrouter', 'https://openrouter.ai/api/v1')
LMStudioAdapter = compatible('lmstudio', 'http://127.0.0.1:1234/v1')
LlamaCppAdapter = compatible('llamacpp', 'http://127.0.0.1:8080/v1')
CustomAdapter = compatible('custom', '')
