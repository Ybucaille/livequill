"""Anthropic Messages API (HTTP brut, en flux SSE)."""
import json, re

from ..net import sse
from .base import Adapter, levels

VERSION = '2023-06-01'
FAST_MODELS = {'claude-opus-5-5', 'claude-opus-5', 'claude-opus-4-8'}
# Modèles qui acceptent fallbacks: "default" (re-routage côté serveur en cas de refus)
FALLBACK_MODELS = {'claude-fable-5-1', 'claude-opus-5-5', 'claude-opus-5', 'claude-sonnet-5-5'}
KNOWN = [('claude-opus-5-5', 'Opus 5.5'), ('claude-sonnet-5-5', 'Sonnet 5.5'), ('claude-haiku-5-5', 'Haiku 5.5'),
         ('claude-fable-5-1', 'Fable 5.1')]


def effort_levels(model):
    if re.match(r'claude-(opus|sonnet|haiku|fable|mythos)-5', model) or re.match(r'claude-opus-4-[78]', model):
        return levels('low', 'medium', 'high', 'xhigh', 'max')
    if re.match(r'claude-(opus|sonnet)-4-6', model):
        return levels('low', 'medium', 'high', 'max')
    return None


class AnthropicAdapter(Adapter):
    kind = 'anthropic'
    default_base = 'https://api.anthropic.com'

    def headers(self, betas=()):
        h = {'x-api-key': self.key, 'anthropic-version': VERSION}
        if betas:
            h['anthropic-beta'] = ','.join(betas)
        return h

    def list_models(self):
        data = self.http.json('GET', f'{self.base}/v1/models?limit=100', self.headers())
        out = [{'id': m['id'], 'label': m.get('display_name') or m['id'], 'meta': {}} for m in data.get('data', [])]
        return out or [{'id': i, 'label': l, 'meta': {}} for i, l in KNOWN]

    def caps(self, model, meta=None):
        return {'tools': True, 'vision': True, 'effort': effort_levels(model), 'fast': model in FAST_MODELS}

    def convert(self, messages):
        out = []
        for m in messages:
            if m['role'] == 'tool':
                blocks = [{'type': 'tool_result', 'tool_use_id': r['id'], 'content': r['content'] or '(empty)',
                           **({'is_error': True} if r.get('is_error') else {})} for r in m['results']]
                out.append({'role': 'user', 'content': blocks})
                continue
            blocks = []
            for p in m['content']:
                t = p['type']
                if t == 'text' and p['text']:
                    blocks.append({'type': 'text', 'text': p['text']})
                elif t == 'image':
                    blocks.append({'type': 'image', 'source': {'type': 'base64', 'media_type': p['media_type'], 'data': p['data']}})
                elif t == 'tool_call':
                    blocks.append({'type': 'tool_use', 'id': p['id'], 'name': p['name'], 'input': p['args']})
                elif t == 'raw' and p.get('provider') == self.kind:
                    blocks.append(p['block'])   # blocs de réflexion renvoyés tels quels pendant la boucle
            out.append({'role': m['role'], 'content': blocks or [{'type': 'text', 'text': '…'}]})
        return out

    def build_request(self, model, system, messages, tools, opts):
        body = {'model': model, 'max_tokens': 32000, 'stream': True, 'system': system, 'messages': self.convert(messages)}
        if tools:
            body['tools'] = [{'name': t['name'], 'description': t['description'], 'input_schema': t['parameters']} for t in tools]
        betas = []
        if effort_levels(model):
            body['thinking'] = {'type': 'adaptive', 'display': 'summarized'}
            if opts.get('effort') in dict(effort_levels(model)):
                body['output_config'] = {'effort': opts['effort']}
        if opts.get('fast') and model in FAST_MODELS:
            body['speed'] = 'fast'
            betas.append('fast-mode-2026-02-01')
        elif model in FALLBACK_MODELS and self.conf.get('fallbacks', True):
            body['fallbacks'] = 'default'
            betas.append('server-side-fallback-2026-07-01')
        return 'POST', f'{self.base}/v1/messages', self.headers(betas), body

    def parse_stream(self, lines):
        blocks, stop = {}, 'end'
        for _event, data in sse(lines):
            try:
                ev = json.loads(data)
            except ValueError:
                continue
            t = ev.get('type')
            if t == 'error':
                raise RuntimeError(ev.get('error', {}).get('message') or data)
            if t == 'content_block_start':
                b = dict(ev['content_block'])
                if b.get('type') == 'tool_use':
                    b['_json'] = ''
                blocks[ev['index']] = b
            elif t == 'content_block_delta':
                b, d = blocks.get(ev['index']), ev['delta']
                if b is None:
                    continue
                dt = d.get('type')
                if dt == 'text_delta':
                    b['text'] = b.get('text', '') + d['text']
                    yield {'type': 'delta', 'text': d['text']}
                elif dt == 'thinking_delta':
                    b['thinking'] = b.get('thinking', '') + d['thinking']
                    yield {'type': 'thinking', 'text': d['thinking']}
                elif dt == 'signature_delta':
                    b['signature'] = b.get('signature', '') + d['signature']
                elif dt == 'input_json_delta':
                    b['_json'] += d.get('partial_json', '')
            elif t == 'content_block_stop':
                b = blocks.pop(ev['index'], None)
                if not b:
                    continue
                if b['type'] == 'text':
                    yield {'type': 'part', 'part': {'type': 'text', 'text': b.get('text', '')}}
                elif b['type'] == 'tool_use':
                    try:
                        args = json.loads(b['_json'] or '{}')
                    except ValueError:
                        args = {'_invalid_json': b['_json']}
                    yield {'type': 'part', 'part': {'type': 'tool_call', 'id': b['id'], 'name': b['name'], 'args': args}}
                else:   # thinking, redacted_thinking, fallback… : renvoyés tels quels au tour suivant
                    yield {'type': 'part', 'part': {'type': 'raw', 'provider': self.kind, 'block': b}}
            elif t == 'message_delta':
                r = ev.get('delta', {}).get('stop_reason')
                if r:
                    stop = {'tool_use': 'tool_use', 'max_tokens': 'max_tokens', 'refusal': 'refusal'}.get(r, 'end')
        yield {'type': 'stop', 'reason': stop}
