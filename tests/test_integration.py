"""Tests d'intégration réels : vrai serveur HTTP, vrai sous-processus d'agent CLI, vrai fournisseur HTTP local.

Rien n'est simulé à l'intérieur de LiveQuill : seuls l'agent (un petit programme) et le modèle (un petit serveur
compatible OpenAI) sont des doublures, branchés par les mêmes chemins que les vrais."""
import http.client, http.server, json, os, stat, sys, tempfile, textwrap, threading, time, unittest
from unittest import mock

from helpers import cleanup, temp_project

from livequill.adapters import cli
from livequill.config import Config
from livequill.server import App, make_server

PAGE = '<!doctype html><html><body><h1 class="t">Hello</h1></body></html>'


class Live:
    """Un site servi par un vrai serveur LiveQuill, piloté par son API HTTP comme le fait la barre."""

    def __init__(self, root, config):
        self.app = App(root, 0, config=config)
        self.srv = make_server(self.app)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.port, self.token = self.app.port, self.app.token

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()

    def call(self, method, path, body=None, timeout=30):
        c = http.client.HTTPConnection('127.0.0.1', self.port, timeout=timeout)
        h = {'Host': f'127.0.0.1:{self.port}', 'X-LQ-Token': self.token}
        data = json.dumps(body).encode() if body is not None else None
        if data is not None:
            h['Content-Type'] = 'application/json'
        c.request(method, path, body=data, headers=h)
        r = c.getresponse()
        raw = r.read()
        c.close()
        return r.status, json.loads(raw) if raw[:1] in (b'{', b'[') else raw

    def ask(self, text, provider, model):
        s, r = self.call('POST', '/__lq/api/prompt', {'text': text, 'provider': provider, 'model': model,
                                                      'page': {'pathname': '/', 'hash': ''}})
        assert s == 200, r
        return self.events(r['job'])

    def events(self, job):
        """Lit le flux SSE de la demande jusqu'à « done », exactement comme la barre."""
        c = http.client.HTTPConnection('127.0.0.1', self.port, timeout=120)
        c.request('GET', f'/__lq/api/jobs/{job}/events?t={self.token}&from=0', headers={'Host': f'127.0.0.1:{self.port}'})
        r, evs = c.getresponse(), []
        while True:
            line = r.readline()
            if not line:
                break
            if line.startswith(b'data: '):
                evs.append(json.loads(line[6:]))
                if evs[-1]['type'] == 'done':
                    break
        c.close()
        return evs

    def wait_reload(self, gen, timeout=5):
        end = time.time() + timeout
        while time.time() < end:
            if self.app.reloader.gen != gen:
                return True
            time.sleep(0.05)
        return False


FAKE_CLAUDE = textwrap.dedent('''\
    #!{python}
    """Doublure de Claude Code : lit la demande sur stdin, modifie le projet comme le vrai, parle en stream-json."""
    import json, os, sys
    args, prompt = sys.argv[1:], sys.stdin.read()
    assert '--restricted' in args and '--strict-mcp-config' in args, args
    if 'outside' in prompt:                       # tentative d'écrire hors du projet : refusée comme le vrai le ferait
        print(json.dumps({{"type": "result", "result": "refused", "is_error": False}}))
        sys.exit(0)
    html = open('index.html').read().replace('Hello', 'Bonjour')
    open('index.html', 'w').write(html)
    open('.env.local', 'w').write('X=1')          # un agent peut aussi toucher aux .env : l'annulation doit le défaire
    big = os.path.join('media', 'clip.bin')
    os.makedirs('media', exist_ok=True)
    with open(big, 'wb') as f:
        f.write(b'\\2' * 6_000_000)
    for ev in ({{"type": "assistant", "message": {{"content": [{{"type": "tool_use", "name": "Edit", "input": {{"file_path": os.path.abspath("index.html")}}}}]}}}},
               {{"type": "stream_event", "event": {{"type": "content_block_delta", "delta": {{"type": "text_delta", "text": "Titre traduit."}}}}}},
               {{"type": "result", "result": "Titre traduit.", "is_error": False}}):
        print(json.dumps(ev), flush=True)
''')


class RealCliAgent(unittest.TestCase):
    """Un vrai sous-processus lancé par LiveQuill, avec instantané, rechargement et annulation de bout en bout."""

    def setUp(self):
        self.root = temp_project({'index.html': PAGE})
        os.makedirs(os.path.join(self.root, 'media'))
        with open(os.path.join(self.root, 'media', 'clip.bin'), 'wb') as f:
            f.write(b'\1' * 6_000_000)
        self.bin = tempfile.mkdtemp(prefix='lq-bin-')
        exe = os.path.join(self.bin, 'claude')
        with open(exe, 'w') as f:
            f.write(FAKE_CLAUDE.format(python=sys.executable))
        os.chmod(exe, stat.S_IRWXU)
        self.cfgdir = temp_project()
        config = Config(os.path.join(self.cfgdir, 'c.json'))
        config.upsert('cli-claude', {'type': 'cli-claude', 'command': 'claude', 'models': [{'id': 'haiku', 'label': 'Haiku'}],
                                     'default_model': 'haiku'})
        self.patches = [mock.patch.dict(os.environ, {'PATH': self.bin + os.pathsep + '/usr/bin:/bin'}),
                        mock.patch.object(cli, 'EXTRA_DIRS', []), mock.patch.object(cli, 'BUNDLED', {})]
        for p in self.patches:
            p.start()
        self.live = Live(self.root, config)

    def tearDown(self):
        self.live.close()
        for p in self.patches:
            p.stop()
        cleanup(self.root, self.bin, self.cfgdir)

    def file(self, rel):
        with open(os.path.join(self.root, rel), 'rb') as f:
            return f.read()

    def test_edit_reload_undo(self):
        gen = self.live.app.reloader.gen
        evs = self.live.ask('traduis le titre en français', 'cli-claude', 'haiku')
        done = evs[-1]
        self.assertEqual(done['type'], 'done', evs)
        self.assertFalse(done.get('error'), evs)
        self.assertIn(b'Bonjour', self.file('index.html'))
        self.assertEqual(sorted(done['files']), ['.env.local', 'index.html', 'media/clip.bin'])
        self.assertTrue(self.live.wait_reload(gen), 'la page ouverte doit recevoir « reload »')
        self.assertTrue(any(e['type'] == 'tool' and e['summary'] == 'Edit index.html' for e in evs), evs)  # chemin affiché relatif

        gen = self.live.app.reloader.gen
        s, r = self.live.call('POST', '/__lq/api/undo', {})
        self.assertTrue(r['ok'], r)
        self.assertEqual(self.file('index.html'), PAGE.encode())
        self.assertEqual(self.file('media/clip.bin'), b'\1' * 6_000_000)
        self.assertFalse(os.path.exists(os.path.join(self.root, '.env.local')))
        self.assertTrue(self.live.wait_reload(gen))
        self.assertEqual(self.live.call('GET', '/__lq/api/state')[1]['conversation'][-1]['status'], 'undone')
        self.assertFalse(os.listdir(os.path.join(self.root, '.livequill', 'tmp')))   # copies temporaires nettoyées


class FakeOpenAI(http.server.BaseHTTPRequestHandler):
    """Fournisseur compatible OpenAI : 1er appel → edit_file, 2e appel → réponse finale (le résultat d'outil doit arriver)."""
    calls = []

    def log_message(self, *a):
        pass

    def do_GET(self):
        body = json.dumps({'data': [{'id': 'fake-model'}]}).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        req = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        FakeOpenAI.calls.append(req)
        if not any(m['role'] == 'tool' for m in req['messages']):
            chunks = [{'choices': [{'delta': {'tool_calls': [{'index': 0, 'id': 'c1', 'function': {'name': 'edit_file', 'arguments': ''}}]}}]},
                      {'choices': [{'delta': {'tool_calls': [{'index': 0, 'function': {'arguments': json.dumps(
                          {'path': 'index.html', 'old_text': 'Hello', 'new_text': 'Salut'})}}]}, 'finish_reason': 'tool_calls'}]}]
        else:
            chunks = [{'choices': [{'delta': {'content': 'Titre changé.'}, 'finish_reason': 'stop'}]}]
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.end_headers()
        for c in chunks:
            self.wfile.write(b'data: ' + json.dumps(c).encode() + b'\n\n')
            self.wfile.flush()
        self.wfile.write(b'data: [DONE]\n\n')


class RealHttpProvider(unittest.TestCase):
    """La vraie pile réseau (urllib, SSE) contre un serveur local : boucle agent, outils, sauvegarde, annulation."""

    def setUp(self):
        FakeOpenAI.calls = []
        self.model = http.server.ThreadingHTTPServer(('127.0.0.1', 0), FakeOpenAI)
        threading.Thread(target=self.model.serve_forever, daemon=True).start()
        self.root = temp_project({'index.html': PAGE})
        self.cfgdir = temp_project()
        config = Config(os.path.join(self.cfgdir, 'c.json'))
        config.upsert('custom', {'type': 'custom', 'base_url': f'http://127.0.0.1:{self.model.server_address[1]}/v1',
                                 'models': [{'id': 'fake-model', 'label': 'fake'}], 'default_model': 'fake-model'})
        self.live = Live(self.root, config)

    def tearDown(self):
        self.live.close()
        self.model.shutdown()
        self.model.server_close()
        cleanup(self.root, self.cfgdir)

    def test_tool_loop_over_http(self):
        evs = self.live.ask('change le titre', 'custom', 'fake-model')
        self.assertEqual(evs[-1]['type'], 'done', evs)
        self.assertEqual(evs[-1]['answer'], 'Titre changé.')
        with open(os.path.join(self.root, 'index.html')) as f:
            self.assertIn('Salut', f.read())
        self.assertEqual(len(FakeOpenAI.calls), 2)
        self.assertEqual(FakeOpenAI.calls[1]['messages'][-1]['role'], 'tool')
        self.assertTrue(self.live.call('POST', '/__lq/api/undo', {})[1]['ok'])
        with open(os.path.join(self.root, 'index.html')) as f:
            self.assertEqual(f.read(), PAGE)


if __name__ == '__main__':
    unittest.main()
