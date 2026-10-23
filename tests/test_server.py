"""Ajout du script dans le HTML, mode dossier et mode proxy, garde-fous des routes /__lq/, config."""
import http.client, http.server, json, os, stat, threading, unittest

from helpers import FakeTransport, cleanup, temp_project

from livequill.config import Config, redact
from livequill.server import App, inject, make_server

PAGE = '<!doctype html><html><head><title>T</title></head><body><h1>Hi</h1></body></html>'


class Inject(unittest.TestCase):
    def test_before_last_body(self):
        out = inject(b'<body><p>a</p></BODY >\n<!-- </body> in comment? no: last wins --></body>', 'TOK').decode()
        self.assertTrue(out.endswith('<script src="/__lq/overlay.js" data-lq-token="TOK" defer></script></body>'))
        self.assertEqual(out.count('overlay.js'), 1)

    def test_fallbacks(self):
        self.assertIn('overlay.js" data-lq-token="T" defer></script></html>', inject(b'<html><p>x</p></html>', 'T').decode())
        self.assertTrue(inject(b'<p>fragment</p>', 'T').decode().endswith('defer></script>'))

    def test_case_insensitive(self):
        self.assertIn(b'</script></Body>', inject(b'<Body>x</Body>', 'T'))


def serve(app):
    srv = make_server(app)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


class ServerBase(unittest.TestCase):
    proxy = None

    def setUp(self):
        self.root = temp_project({'index.html': PAGE, 'style.css': 'h1{}', '.env': 'SECRET=1'})
        self.cfgdir = temp_project()
        self.config = Config(os.path.join(self.cfgdir, 'cfg', 'config.json'))
        self.app = App(self.root, 0, proxy=self.proxy, config=self.config, transport=FakeTransport(
            json_responses={'/api/tags': {'models': [{'name': 'qwen'}]}, '/api/show': {'capabilities': ['tools']}}))
        self.srv = serve(self.app)
        self.port = self.app.port

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        cleanup(self.root, self.cfgdir)

    def req(self, method, path, body=None, headers=None, host=None):
        c = http.client.HTTPConnection('127.0.0.1', self.port, timeout=5)
        h = {'Host': host or f'127.0.0.1:{self.port}'}
        h.update(headers or {})
        data = json.dumps(body).encode() if body is not None else None
        if data is not None:
            h.setdefault('Content-Type', 'application/json')
        c.request(method, path, body=data, headers=h)
        r = c.getresponse()
        out = (r.status, dict(r.getheaders()), r.read())
        c.close()
        return out

    def api(self, method, path, body=None, **kw):
        return self.req(method, path, body, headers={'X-LQ-Token': self.app.token, **kw.get('headers', {})})


class StaticMode(ServerBase):
    def test_setup_first_then_site_with_bar(self):
        s, h, _ = self.req('GET', '/', headers={'Accept': 'text/html'})
        self.assertEqual((s, h['Location']), (302, '/__setup?next=%2F'))
        s, h, body = self.req('GET', '/__setup')
        self.assertEqual(s, 200)
        self.assertIn(self.app.token.encode(), body)
        self.assertEqual(h['X-Frame-Options'], 'DENY')
        self.config.upsert('ollama', {'type': 'ollama', 'models': [{'id': 'qwen', 'label': 'qwen'}], 'default_model': 'qwen'})
        s, _, body = self.req('GET', '/')
        self.assertEqual(s, 200)
        self.assertIn(b'data-lq-token="' + self.app.token.encode(), body)
        with open(os.path.join(self.root, 'index.html')) as f:
            self.assertEqual(f.read(), PAGE)           # rien d'écrit sur disque
        s, _, body = self.req('GET', '/style.css')
        self.assertEqual(body, b'h1{}')
        self.assertEqual(self.req('GET', '/.env')[0], 404)
        self.assertEqual(self.req('GET', '/.livequill/conversation.json')[0], 404)

    def test_api_needs_token_origin_and_host(self):
        self.assertEqual(self.req('GET', '/__lq/api/state')[0], 403)
        self.assertEqual(self.req('GET', '/__lq/api/state', headers={'X-LQ-Token': 'wrong'})[0], 403)
        self.assertEqual(self.api('GET', '/__lq/api/state')[0], 200)
        self.assertEqual(self.req('GET', '/__setup', host='evil.example:80')[0], 403)          # DNS rebinding
        self.assertEqual(self.api('POST', '/__lq/api/undo', {}, headers={'Origin': 'http://evil.example'})[0], 403)
        self.assertEqual(self.api('POST', '/__lq/api/undo', {}, headers={'Sec-Fetch-Site': 'cross-site'})[0], 403)
        s, _, _ = self.req('POST', '/__lq/api/undo', None, headers={'X-LQ-Token': self.app.token, 'Content-Type': 'text/plain'})
        self.assertEqual(s, 415)
        self.assertEqual(self.req('GET', f'/__lq/reload?t={self.app.token}x')[0], 403)

    def test_keys_never_reach_the_browser(self):
        s, _, body = self.api('POST', '/__lq/api/providers/save', {
            'type': 'anthropic', 'api_key': 'sk-ant-SECRET-123456', 'models': [{'id': 'claude-opus-5-5', 'label': 'Opus'}]})
        self.assertEqual(json.loads(body)['ok'], True)
        for path in ('/__lq/api/state', '/__lq/api/kinds'):
            s, _, body = self.api('GET', path)
            self.assertNotIn(b'SECRET', body)
        self.assertIn(b'"has_key": true', self.api('GET', '/__lq/api/kinds')[2])
        mode = stat.S_IMODE(os.stat(self.config.path).st_mode)
        self.assertEqual(mode, 0o600)
        self.assertEqual(stat.S_IMODE(os.stat(os.path.dirname(self.config.path)).st_mode), 0o700)

    def test_provider_test_uses_adapter(self):
        s, _, body = self.api('POST', '/__lq/api/providers/test', {'type': 'ollama'})
        r = json.loads(body)
        self.assertTrue(r['ok'])
        self.assertEqual(r['models'][0]['id'], 'qwen')
        self.assertTrue(r['models'][0]['caps']['tools'])


class ProxyMode(ServerBase):
    def setUp(self):
        self.up_root = temp_project({'index.html': PAGE.replace('Hi', 'Upstream'), 'app.js': 'console.log(1)'})
        up_root = self.up_root

        class H(http.server.SimpleHTTPRequestHandler):
            def __init__(s, *a, **k):
                super().__init__(*a, directory=up_root, **k)

            def log_message(s, *a):
                pass
        self.up = http.server.ThreadingHTTPServer(('127.0.0.1', 0), H)
        threading.Thread(target=self.up.serve_forever, daemon=True).start()
        self.proxy = f'http://127.0.0.1:{self.up.server_address[1]}'
        super().setUp()
        self.config.upsert('ollama', {'type': 'ollama', 'models': [{'id': 'qwen', 'label': 'qwen'}], 'default_model': 'qwen'})

    def tearDown(self):
        super().tearDown()
        self.up.shutdown()
        self.up.server_close()
        cleanup(self.up_root)

    def test_html_injected_assets_untouched(self):
        s, h, body = self.req('GET', '/')
        self.assertEqual(s, 200)
        self.assertIn(b'Upstream', body)
        self.assertIn(b'/__lq/overlay.js', body)
        self.assertEqual(int(h['Content-Length']), len(body))
        s, _, body = self.req('GET', '/app.js')
        self.assertEqual(body, b'console.log(1)')
        self.assertEqual(self.api('GET', '/__lq/api/state')[0], 200)   # l'API reste servie par l'outil


class HubMode(unittest.TestCase):
    """Tableau de bord : setup au premier lancement, puis gestion des sites, chacun sur son port."""

    def setUp(self):
        self.site_root = temp_project({'index.html': PAGE})
        self.cfgdir = temp_project()
        self.config = Config(os.path.join(self.cfgdir, 'config.json'))
        self.hub = App(None, 0, config=self.config, transport=FakeTransport())
        self.srv = serve(self.hub)

    def tearDown(self):
        self.hub.sites.stop_all()
        self.srv.shutdown()
        self.srv.server_close()
        cleanup(self.site_root, self.cfgdir)

    def req(self, method, path, body=None, port=None, token=None):
        port = port or self.hub.port
        c = http.client.HTTPConnection('127.0.0.1', port, timeout=5)
        h = {'Host': f'127.0.0.1:{port}'}
        if token:
            h['X-LQ-Token'] = token
        data = json.dumps(body).encode() if body is not None else None
        if data is not None:
            h['Content-Type'] = 'application/json'
        c.request(method, path, body=data, headers=h)
        r = c.getresponse()
        out = (r.status, dict(r.getheaders()), r.read())
        c.close()
        return out

    def test_first_run_then_sites(self):
        s, h, _ = self.req('GET', '/')
        self.assertEqual((s, h['Location']), (302, '/__setup?next=%2F'))
        self.config.upsert('ollama', {'type': 'ollama', 'models': [{'id': 'qwen', 'label': 'qwen'}], 'default_model': 'qwen'})
        s, _, body = self.req('GET', '/')
        self.assertEqual(s, 200)
        self.assertIn(b'Mes sites', body)
        self.assertIn(self.hub.token.encode(), body)
        tok = self.hub.token
        self.assertEqual(json.loads(self.req('GET', '/__lq/api/sites', token=tok)[2])['sites'], [])
        # dossier invalide, racine refusée
        self.assertEqual(self.req('POST', '/__lq/api/sites/save', {'root': '/nope/nope'}, token=tok)[0], 400)
        self.assertEqual(self.req('POST', '/__lq/api/sites/save', {'root': '/'}, token=tok)[0], 400)
        s, _, body = self.req('POST', '/__lq/api/sites/save', {'root': self.site_root, 'name': 'Mon site'}, token=tok)
        site = json.loads(body)['site']
        self.assertEqual(site['name'], 'Mon site')
        self.assertEqual(self.req('POST', '/__lq/api/sites/save', {'root': self.site_root}, token=tok)[0], 400)  # doublon
        url = json.loads(self.req('POST', '/__lq/api/sites/start', {'id': site['id']}, token=tok)[2])['url']
        port = int(url.rsplit(':', 1)[1].strip('/'))
        self.assertNotEqual(port, self.hub.port)
        s, _, page = self.req('GET', '/', port=port)
        self.assertIn(b'/__lq/overlay.js', page)
        site_app = self.hub.sites.running[site['id']][0]
        kinds = json.loads(self.req('GET', '/__lq/api/kinds', port=port, token=site_app.token)[2])
        self.assertEqual(kinds['hub_url'], self.hub.sites.url)
        listed = json.loads(self.req('GET', '/__lq/api/sites', token=tok)[2])['sites'][0]
        self.assertTrue(listed['running'])
        self.assertEqual(self.config.get_site(site['id'])['port'], port)      # port mémorisé
        self.req('POST', '/__lq/api/sites/delete', {'id': site['id']}, token=tok)
        self.assertEqual(self.config.sites(), [])
        self.assertTrue(os.path.isfile(os.path.join(self.site_root, 'index.html')))   # le dossier n'est jamais supprimé

    def test_api_split_between_hub_and_sites(self):
        tok = self.hub.token
        self.assertEqual(self.req('GET', '/__lq/api/state', token=tok)[0], 404)
        self.assertEqual(self.req('POST', '/__lq/api/prompt', {'text': 'x'}, token=tok)[0], 404)
        self.assertEqual(self.req('GET', '/__lq/api/sites')[0], 403)                  # sans jeton
        r = json.loads(self.req('GET', '/__lq/api/browse?path=' + self.site_root, token=tok)[2])
        self.assertEqual(r['path'], os.path.realpath(self.site_root))
        self.assertIn('html', r['hints'])

    def test_ensure_from_command_line(self):
        sid = self.hub.sites.ensure(self.site_root)
        self.assertEqual(self.hub.sites.ensure(self.site_root), sid)
        self.hub.sites.ensure(self.site_root, 'http://localhost:5173')
        self.assertEqual(self.config.get_site(sid)['proxy'], 'http://localhost:5173')


class ConfigFile(unittest.TestCase):
    def test_keep_key_on_empty_update_and_redact(self):
        d = temp_project()
        try:
            c = Config(os.path.join(d, 'config.json'))
            c.upsert('openai', {'type': 'openai', 'api_key': 'sk-abcdef123'})
            c.upsert('openai', {'type': 'openai', 'api_key': ''})
            self.assertEqual(Config(c.path).get('openai')['api_key'], 'sk-abcdef123')
            self.assertNotIn('api_key', c.public_providers()['openai'])
            self.assertEqual(redact('Bearer sk-abcdef123 refused', c.secrets()), 'Bearer •••• refused')
        finally:
            cleanup(d)


if __name__ == '__main__':
    unittest.main()
