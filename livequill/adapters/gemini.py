"""Google Gemini (generateContent en flux SSE)."""
import json, uuid

from ..net import sse
from .base import Adapter, levels

BUDGETS = {'low': 1024, 'medium': 8192, 'high': 24576}


def effort_levels(model):
    if model.startswith('gemini-3'):
        return levels('low', 'high')
    if model.startswith('gemini-2.5'):
        return levels('low', 'medium', 'high')
    return None


def clean_schema(s):
    """Gemini n'accepte qu'un sous-ensemble d'OpenAPI : on retire ce qu'il refuse."""
    if isinstance(s, dict):
        return {k: clean_schema(v) for k, v in s.items() if k not in ('additionalProperties', '$schema', 'default')}
    if isinstance(s, list):
        return [clean_schema(x) for x in s]
    return s


class GeminiAdapter(Adapter):
    kind = 'gemini'
    default_base = 'https://generativelanguage.googleapis.com/v1beta'

    def headers(self):
        return {'x-goog-api-key': self.key}

    def list_models(self):
        data = self.http.json('GET', f'{self.base}/models?pageSize=200', self.headers())
        out = []
        for m in (data or {}).get('models', []):
            if 'generateContent' not in m.get('supportedGenerationMethods', []):
                continue
            mid = m['name'].split('/', 1)[-1]
            if not mid.startswith('gemini'):
                continue
            out.append({'id': mid, 'label': m.get('displayName') or mid, 'meta': {}})
        return out

    def caps(self, model, meta=None):
        return {'tools': True, 'vision': True, 'effort': effort_levels(model), 'fast': False}

    def convert(self, messages):
        contents = []
        for m in messages:
            if m['role'] == 'tool':
                contents.append({'role': 'user', 'parts': [
                    {'functionResponse': {'name': r['name'], 'response': {'error' if r.get('is_error') else 'content': r['content']}}}
                    for r in m['results']]})
                continue
            parts = []
            for p in m['content']:
                if p['type'] == 'text' and p['text']:
                    parts.append({'text': p['text']})
                elif p['type'] == 'image':
                    parts.append({'inlineData': {'mimeType': p['media_type'], 'data': p['data']}})
                elif p['type'] == 'tool_call':
                    # La part d'origine contient la thoughtSignature exigée par Gemini 3
                    parts.append((p.get('meta') or {}).get('gemini_part') or {'functionCall': {'name': p['name'], 'args': p['args']}})
            contents.append({'role': 'model' if m['role'] == 'assistant' else 'user', 'parts': parts or [{'text': '…'}]})
        return contents

    def build_request(self, model, system, messages, tools, opts):
        body = {'systemInstruction': {'parts': [{'text': system}]}, 'contents': self.convert(messages)}
        if tools:
            body['tools'] = [{'functionDeclarations': [{'name': t['name'], 'description': t['description'],
                                                        'parameters': clean_schema(t['parameters'])} for t in tools]}]
        lv = effort_levels(model)
        if lv:
            tc = {'includeThoughts': True}
            e = opts.get('effort')
            if e in dict(lv):
                if model.startswith('gemini-3'):
                    tc['thinkingLevel'] = e
                else:
                    tc['thinkingBudget'] = BUDGETS[e]
            body['generationConfig'] = {'thinkingConfig': tc}
        return 'POST', f'{self.base}/models/{model}:streamGenerateContent?alt=sse', self.headers(), body

    def parse_stream(self, lines):
        text, calls, stop = '', [], 'end'
        for _, data in sse(lines):
            try:
                ev = json.loads(data)
            except ValueError:
                continue
            if ev.get('error'):
                raise RuntimeError(ev['error'].get('message', data))
            for cand in ev.get('candidates') or []:
                for p in (cand.get('content') or {}).get('parts') or []:
                    if 'functionCall' in p:
                        fc = p['functionCall']
                        calls.append({'type': 'tool_call', 'id': fc.get('id') or 'call_' + uuid.uuid4().hex[:8],
                                      'name': fc['name'], 'args': fc.get('args') or {}, 'meta': {'gemini_part': p}})
                    elif p.get('thought') and p.get('text'):
                        yield {'type': 'thinking', 'text': p['text']}
                    elif p.get('text'):
                        text += p['text']
                        yield {'type': 'delta', 'text': p['text']}
                fr = cand.get('finishReason')
                if fr:
                    stop = {'MAX_TOKENS': 'max_tokens', 'SAFETY': 'refusal', 'PROHIBITED_CONTENT': 'refusal'}.get(fr, 'end')
        if text:
            yield {'type': 'part', 'part': {'type': 'text', 'text': text}}
        for c in calls:
            yield {'type': 'part', 'part': c}
        yield {'type': 'stop', 'reason': 'tool_use' if calls else stop}
