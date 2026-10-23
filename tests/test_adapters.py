"""Conversion des requêtes et lecture des flux, par adaptateur. Réseau simulé."""
import json, unittest

from helpers import FakeTransport, sse_lines

from livequill import adapters
from livequill.adapters.base import ToolsUnsupported
from livequill.adapters.cli import CLIAdapter, parse_claude, parse_codex
from livequill.net import HTTPError

TOOLS = [{'name': 'read_file', 'description': 'Read', 'parameters': {'type': 'object', 'properties': {'path': {'type': 'string'}},
                                                                    'required': ['path'], 'additionalProperties': False}}]
IMG = {'type': 'image', 'media_type': 'image/png', 'data': 'iVBORw0KGgo='}
CONVO = [
    {'role': 'user', 'content': [{'type': 'text', 'text': 'passe le titre en rouge'}, IMG]},
    {'role': 'assistant', 'content': [{'type': 'text', 'text': 'Je regarde.'},
                                      {'type': 'tool_call', 'id': 'call_1', 'name': 'read_file', 'args': {'path': 'index.html'}}]},
    {'role': 'tool', 'results': [{'id': 'call_1', 'name': 'read_file', 'content': '<h1>x</h1>', 'is_error': False}]},
]


def run(adapter, lines):
    return list(adapter.parse_stream(iter(lines)))


def parts(events):
    return [e['part'] for e in events if e['type'] == 'part']


class Anthropic(unittest.TestCase):
    def setUp(self):
        self.a = adapters.make({'type': 'anthropic', 'api_key': 'sk-ant-test'})

    def test_request(self):
        m, url, h, b = self.a.build_request('claude-opus-5-5', 'SYS', CONVO, TOOLS, {'effort': 'high', 'fast': True})
        self.assertEqual(url, 'https://api.anthropic.com/v1/messages')
        self.assertEqual(h['x-api-key'], 'sk-ant-test')
        self.assertIn('fast-mode-2026-02-01', h['anthropic-beta'])
        self.assertEqual(b['speed'], 'fast')
        self.assertEqual(b['output_config'], {'effort': 'high'})
        self.assertEqual(b['thinking']['type'], 'adaptive')
        self.assertNotIn('temperature', b)
        self.assertEqual(b['tools'][0]['input_schema']['required'], ['path'])
        user, asst, res = b['messages']
        self.assertEqual(user['content'][1], {'type': 'image', 'source': {'type': 'base64', 'media_type': 'image/png', 'data': 'iVBORw0KGgo='}})
        self.assertEqual(asst['content'][1], {'type': 'tool_use', 'id': 'call_1', 'name': 'read_file', 'input': {'path': 'index.html'}})
        self.assertEqual(res, {'role': 'user', 'content': [{'type': 'tool_result', 'tool_use_id': 'call_1', 'content': '<h1>x</h1>'}]})

    def test_fast_only_where_supported(self):
        _, _, h, b = self.a.build_request('claude-haiku-5-5', 'S', CONVO[:1], None, {'effort': 'low', 'fast': True})
        self.assertNotIn('speed', b)
        self.assertFalse(self.a.caps('claude-haiku-5-5')['fast'])
        self.assertTrue(self.a.caps('claude-opus-5-5')['fast'])
        self.assertIsNone(self.a.caps('claude-3-haiku')['effort'])

    def test_stream(self):
        lines = sse_lines(
            ('message_start', {'type': 'message_start'}),
            ('content_block_start', {'type': 'content_block_start', 'index': 0, 'content_block': {'type': 'thinking', 'thinking': ''}}),
            ('content_block_delta', {'type': 'content_block_delta', 'index': 0, 'delta': {'type': 'thinking_delta', 'thinking': 'hmm'}}),
            ('content_block_delta', {'type': 'content_block_delta', 'index': 0, 'delta': {'type': 'signature_delta', 'signature': 'SIG'}}),
            ('content_block_stop', {'type': 'content_block_stop', 'index': 0}),
            ('content_block_start', {'type': 'content_block_start', 'index': 1, 'content_block': {'type': 'text', 'text': ''}}),
            ('content_block_delta', {'type': 'content_block_delta', 'index': 1, 'delta': {'type': 'text_delta', 'text': 'Je '}}),
            ('content_block_delta', {'type': 'content_block_delta', 'index': 1, 'delta': {'type': 'text_delta', 'text': 'lis.'}}),
            ('content_block_stop', {'type': 'content_block_stop', 'index': 1}),
            ('content_block_start', {'type': 'content_block_start', 'index': 2, 'content_block': {'type': 'tool_use', 'id': 'tu1', 'name': 'read_file', 'input': {}}}),
            ('content_block_delta', {'type': 'content_block_delta', 'index': 2, 'delta': {'type': 'input_json_delta', 'partial_json': '{"path": "ind'}}),
            ('content_block_delta', {'type': 'content_block_delta', 'index': 2, 'delta': {'type': 'input_json_delta', 'partial_json': 'ex.html"}'}}),
            ('content_block_stop', {'type': 'content_block_stop', 'index': 2}),
            ('message_delta', {'type': 'message_delta', 'delta': {'stop_reason': 'tool_use'}}),
        )
        ev = run(self.a, lines)
        p = parts(ev)
        self.assertEqual(p[0], {'type': 'raw', 'provider': 'anthropic', 'block': {'type': 'thinking', 'thinking': 'hmm', 'signature': 'SIG'}})
        self.assertEqual(p[1], {'type': 'text', 'text': 'Je lis.'})
        self.assertEqual(p[2], {'type': 'tool_call', 'id': 'tu1', 'name': 'read_file', 'args': {'path': 'index.html'}})
        self.assertEqual(ev[-1], {'type': 'stop', 'reason': 'tool_use'})
        # le bloc de réflexion est renvoyé tel quel au tour suivant
        _, _, _, b = self.a.build_request('claude-opus-5-5', 'S', [{'role': 'assistant', 'content': p}], None, {})
        self.assertEqual(b['messages'][0]['content'][0]['signature'], 'SIG')


class OpenAICompatible(unittest.TestCase):
    def test_request_and_stream(self):
        a = adapters.make({'type': 'openai', 'api_key': 'sk-test'})
        _, url, h, b = a.build_request('gpt-5', 'SYS', CONVO, TOOLS, {'effort': 'low'})
        self.assertEqual(url, 'https://api.openai.com/v1/chat/completions')
        self.assertEqual(h['Authorization'], 'Bearer sk-test')
        self.assertEqual(b['reasoning_effort'], 'low')
        sysm, user, asst, tool = b['messages']
        self.assertEqual(sysm, {'role': 'system', 'content': 'SYS'})
        self.assertEqual(user['content'][1]['image_url']['url'], 'data:image/png;base64,iVBORw0KGgo=')
        self.assertEqual(asst['tool_calls'][0]['function'], {'name': 'read_file', 'arguments': '{"path": "index.html"}'})
        self.assertEqual(tool, {'role': 'tool', 'tool_call_id': 'call_1', 'content': '<h1>x</h1>'})
        self.assertEqual(b['tools'][0]['type'], 'function')
        lines = sse_lines(
            {'choices': [{'delta': {'content': 'Ok'}}]},
            {'choices': [{'delta': {'tool_calls': [{'index': 0, 'id': 'c9', 'function': {'name': 'read_file', 'arguments': '{"pa'}}]}}]},
            {'choices': [{'delta': {'tool_calls': [{'index': 0, 'function': {'arguments': 'th": "a.css"}'}}]}, 'finish_reason': 'tool_calls'}]},
            '[DONE]')
        p = parts(run(a, lines))
        self.assertEqual(p, [{'type': 'text', 'text': 'Ok'}, {'type': 'tool_call', 'id': 'c9', 'name': 'read_file', 'args': {'path': 'a.css'}}])

    def test_local_servers_and_custom(self):
        for kind, base in (('lmstudio', 'http://127.0.0.1:1234/v1'), ('llamacpp', 'http://127.0.0.1:8080/v1'),
                           ('mistral', 'https://api.mistral.ai/v1'), ('openrouter', 'https://openrouter.ai/api/v1')):
            _, url, _, b = adapters.make({'type': kind, 'api_key': 'k'}).build_request('m', 'S', CONVO[:1], None, {})
            self.assertEqual(url, base + '/chat/completions')
            self.assertNotIn('tools', b)
        a = adapters.make({'type': 'custom', 'base_url': 'http://localhost:9000/v1/', 'model': 'my-model'})
        _, url, h, _ = a.build_request('my-model', 'S', CONVO[:1], None, {})
        self.assertEqual(url, 'http://localhost:9000/v1/chat/completions')
        self.assertNotIn('Authorization', h)   # clé facultative

    def test_openrouter_caps_from_listing(self):
        t = FakeTransport(json_responses={'/models': {'data': [
            {'id': 'x/vision', 'name': 'X', 'supported_parameters': ['tools', 'reasoning'], 'architecture': {'input_modalities': ['text', 'image']}},
            {'id': 'y/plain', 'supported_parameters': ['temperature'], 'architecture': {'input_modalities': ['text']}}]}})
        a = adapters.make({'type': 'openrouter', 'api_key': 'k'}, t)
        ms = {m['id']: a.caps(m['id'], m['meta']) for m in a.list_models()}
        self.assertEqual((ms['x/vision']['vision'], ms['x/vision']['tools'], bool(ms['x/vision']['effort'])), (True, True, True))
        self.assertEqual((ms['y/plain']['tools'], ms['y/plain']['effort']), (False, None))


class Gemini(unittest.TestCase):
    def test_request_and_stream(self):
        a = adapters.make({'type': 'gemini', 'api_key': 'g-key'})
        _, url, h, b = a.build_request('gemini-2.5-flash', 'SYS', CONVO, TOOLS, {'effort': 'high'})
        self.assertTrue(url.endswith('/models/gemini-2.5-flash:streamGenerateContent?alt=sse'))
        self.assertEqual(h['x-goog-api-key'], 'g-key')
        self.assertNotIn('additionalProperties', json.dumps(b['tools']))
        self.assertEqual(b['generationConfig']['thinkingConfig']['thinkingBudget'], 24576)
        user, model, fn = b['contents']
        self.assertEqual(user['parts'][1], {'inlineData': {'mimeType': 'image/png', 'data': 'iVBORw0KGgo='}})
        self.assertEqual(model['role'], 'model')
        self.assertEqual(fn['parts'][0]['functionResponse']['name'], 'read_file')
        call = {'functionCall': {'name': 'read_file', 'args': {'path': 'a'}}, 'thoughtSignature': 'TS'}
        p = parts(run(a, sse_lines({'candidates': [{'content': {'parts': [{'text': 'Hop'}, call]}, 'finishReason': 'STOP'}]})))
        self.assertEqual(p[0], {'type': 'text', 'text': 'Hop'})
        self.assertEqual(p[1]['args'], {'path': 'a'})
        # la thoughtSignature repart avec l'appel
        _, _, _, b2 = a.build_request('gemini-3-pro', 'S', [{'role': 'assistant', 'content': p}], None, {'effort': 'low'})
        self.assertEqual(b2['contents'][0]['parts'][1]['thoughtSignature'], 'TS')
        self.assertEqual(b2['generationConfig']['thinkingConfig']['thinkingLevel'], 'low')


class Ollama(unittest.TestCase):
    def test_request_and_stream(self):
        a = adapters.make({'type': 'ollama'})
        _, url, _, b = a.build_request('qwen2.5:7b-instruct', 'SYS', CONVO, TOOLS, {})
        self.assertEqual(url, 'http://127.0.0.1:11434/api/chat')
        self.assertEqual(b['options']['num_ctx'], 16384)
        sysm, user, asst, tool = b['messages']
        self.assertEqual(user['images'], ['iVBORw0KGgo='])
        self.assertEqual(asst['tool_calls'], [{'function': {'name': 'read_file', 'arguments': {'path': 'index.html'}}}])
        self.assertEqual(tool, {'role': 'tool', 'content': '<h1>x</h1>', 'tool_name': 'read_file'})
        lines = [json.dumps({'message': {'content': 'Je '}}), json.dumps({'message': {'content': 'cherche'}}),
                 json.dumps({'message': {'content': '', 'tool_calls': [{'function': {'name': 'read_file', 'arguments': {'path': 'x'}}}]}}),
                 json.dumps({'done': True, 'done_reason': 'stop'})]
        ev = run(a, lines)
        self.assertEqual(parts(ev)[0], {'type': 'text', 'text': 'Je cherche'})
        self.assertEqual(parts(ev)[1]['name'], 'read_file')
        self.assertEqual(ev[-1]['reason'], 'tool_use')

    def test_caps_and_think(self):
        a = adapters.make({'type': 'ollama'})
        self.assertEqual(a.caps('llama3.2:3b', {'capabilities': ['completion', 'tools']}),
                         {'tools': True, 'vision': False, 'effort': None, 'fast': False})
        c = a.caps('qwen3:8b', {'capabilities': ['completion', 'tools', 'thinking', 'vision']})
        self.assertEqual([v for v, _ in c['effort']], ['off', 'on'])
        _, _, _, b = a.build_request('qwen3:8b', 'S', CONVO[:1], None, {'effort': 'on'})
        self.assertIs(b['think'], True)
        _, _, _, b = a.build_request('gpt-oss:20b', 'S', CONVO[:1], None, {'effort': 'high'})
        self.assertEqual(b['think'], 'high')

    def test_no_tools_error(self):
        a = adapters.make({'type': 'ollama'}, FakeTransport([HTTPError(400, '{"error":"gemma does not support tools"}')]))
        with self.assertRaises(ToolsUnsupported):
            list(a.chat('gemma', 'S', CONVO[:1], TOOLS, {}))


class CLI(unittest.TestCase):
    def test_commands(self):
        argv = CLIAdapter({'command': 'claude'}).command('/p', 'haiku', {'effort': 'low'}, [])
        self.assertIn('--permission-mode', argv)
        self.assertIn('acceptEdits', argv)
        self.assertIn('Bash', argv[argv.index('--disallowedTools'):argv.index('--allowedTools')])
        self.assertEqual(argv[argv.index('--model') + 1], 'haiku')
        argv = CLIAdapter({'command': 'codex'}).command('/p', 'default', {'effort': 'high'}, ['/p/a.png'])
        self.assertNotIn('--model', argv)
        self.assertEqual(argv[-2:], ['--image', '/p/a.png'])
        self.assertIn('workspace-write', argv)

    def test_parsers(self):
        self.assertEqual(parse_claude({'type': 'stream_event', 'event': {'type': 'content_block_delta', 'delta': {'type': 'text_delta', 'text': 'a'}}}),
                         [{'type': 'delta', 'text': 'a'}])
        self.assertEqual(parse_claude({'type': 'assistant', 'message': {'content': [{'type': 'tool_use', 'name': 'Edit', 'input': {'file_path': 'x.html'}}]}})[0]['path'], 'x.html')
        self.assertEqual(parse_claude({'type': 'result', 'result': 'fini'}), [{'type': 'final', 'text': 'fini'}])
        self.assertEqual(parse_codex({'type': 'item.completed', 'item': {'type': 'file_change', 'changes': [{'path': 'a.css'}]}})[0]['path'], 'a.css')
        with self.assertRaises(RuntimeError):
            parse_codex({'type': 'turn.failed', 'error': 'quota'})


if __name__ == '__main__':
    unittest.main()
