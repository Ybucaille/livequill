"""Création de sites (modèles, dossier proposé), vignettes, réglages, setup réservé au premier lancement, page d'attente."""
import http.client, json, os, threading, unittest
from unittest import mock

from helpers import FakeTransport, cleanup, temp_project

from livequill import scaffold
from livequill.config import Config
from livequill.server import App, dictionary, localized, make_server


class Scaffold(unittest.TestCase):
    def setUp(self):
        self.dir = temp_project()

    def tearDown(self):
        cleanup(self.dir)

    def test_slug_and_suggestion(self):
        self.assertEqual(scaffold.slugify('Acme Bakery'), 'acme-bakery')
        self.assertEqual(scaffold.slugify('Café du Coin !'), 'cafe-du-coin')
        self.assertEqual(scaffold.slugify('   '), 'my-site')
        self.assertEqual(scaffold.suggest(self.dir, 'Acme'), os.path.join(os.path.realpath(self.dir), 'acme'))
        os.makedirs(os.path.join(self.dir, 'acme'))
        self.assertTrue(scaffold.suggest(self.dir, 'Acme').endswith('/acme'))          # dossier vide : réutilisable
        with open(os.path.join(self.dir, 'acme', 'x.txt'), 'w') as f:
            f.write('x')
        self.assertTrue(scaffold.suggest(self.dir, 'Acme').endswith('/acme-2'))        # déjà pris : suffixe

    def test_create_from_each_template(self):
        for tid in scaffold.TEMPLATES:
            with self.subTest(template=tid):
                root = scaffold.create(os.path.join(self.dir, tid, 'site'), 'Bob <b>& Co', tid)
                with open(os.path.join(root, 'index.html'), encoding='utf-8') as f:
                    page = f.read()
                self.assertIn('Bob &lt;b&gt;&amp; Co', page)                         # nom échappé dans le HTML
                self.assertNotIn('{{', page)
                self.assertTrue(os.path.isfile(os.path.join(root, 'style.css')))

    def test_refuses_non_empty_folder_and_home(self):
        with open(os.path.join(self.dir, 'keep.txt'), 'w') as f:
            f.write('mine')
        self.assertRaises(ValueError, scaffold.create, self.dir, 'X', 'blank')
        self.assertRaises(ValueError, scaffold.create, os.path.expanduser('~'), 'X', 'blank')
        self.assertRaises(ValueError, scaffold.create, os.path.join(self.dir, 'new'), 'X', 'nope')
        with open(os.path.join(self.dir, 'keep.txt')) as f:
            self.assertEqual(f.read(), 'mine')


class HubSites(unittest.TestCase):
    def setUp(self):
        self.dir = temp_project()
        self.outside = temp_project({'secret.txt': 'TOP'})
        self.config = Config(os.path.join(self.dir, 'cfg', 'config.json'))
        self.config.upsert('ollama', {'type': 'ollama', 'models': [{'id': 'q', 'label': 'q'}], 'default_model': 'q'})
        self.config.set_option('sites_dir', os.path.join(self.dir, 'sites'))
        os.makedirs(os.path.join(self.dir, 'sites'))
        self.hub = App(None, 0, config=self.config, transport=FakeTransport())
        self.srv = make_server(self.hub)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def tearDown(self):
        self.hub.sites.stop_all()
        self.srv.shutdown()
        self.srv.server_close()
        cleanup(self.dir, self.outside)

    def req(self, method, path, body=None, token=True, port=None):
        port = port or self.hub.port
        c = http.client.HTTPConnection('127.0.0.1', port, timeout=5)
        h = {'Host': f'127.0.0.1:{port}'}
        if token:
            h['X-LQ-Token'] = self.hub.token
        if body is not None:
            h['Content-Type'] = 'application/json'
        c.request(method, path, body=json.dumps(body).encode() if body is not None else None, headers=h)
        r = c.getresponse()
        out = r.status, dict(r.getheaders()), r.read()
        c.close()
        return out

    def api(self, path, body=None):
        s, _, raw = self.req('POST' if body is not None else 'GET', '/__lq/api/' + path, body)
        return s, json.loads(raw)

    def test_create_site_from_template(self):
        s, r = self.api('templates')
        self.assertEqual([t['id'] for t in r['templates']], list(scaffold.TEMPLATES))
        s, r = self.api('sites/suggest?name=Acme%20Bakery')
        self.assertEqual(r['root'], os.path.join(os.path.realpath(self.dir), 'sites', 'acme-bakery'))
        s, r = self.api('sites/create', {'name': 'Acme Bakery', 'template': 'business'})
        self.assertEqual(s, 200, r)
        site = r['site']
        self.assertEqual(site['root'], os.path.join(os.path.realpath(self.dir), 'sites', 'acme-bakery'))
        listed = self.api('sites')[1]['sites'][0]
        self.assertEqual(listed['thumb'], f'/__lq/thumb/{site["id"]}/')
        self.assertEqual(self.api('sites/create', {'name': 'x', 'root': site['root']})[0], 400)       # dossier plus vide
        self.assertEqual(self.api('sites/create', {'name': ''})[0], 400)

    def test_template_preview_and_thumbnails_run_no_script(self):
        s, h, body = self.req('GET', '/__lq/template/business/?name=Acme%20%3Cb%3E', token=False)
        self.assertEqual(s, 200)
        self.assertIn(b'Acme &lt;b&gt;', body)
        self.assertIn("script-src 'none'", h['Content-Security-Policy'])
        self.assertEqual(self.req('GET', '/__lq/template/business/style.css', token=False)[0], 200)
        self.assertEqual(self.req('GET', '/__lq/template/nope/', token=False)[0], 404)

        root = temp_project({'index.html': '<h1>Home</h1>', '.env': 'SECRET=1'})
        os.symlink(os.path.join(self.outside, 'secret.txt'), os.path.join(root, 'leak.txt'))
        sid = self.api('sites/save', {'root': root})[1]['site']['id']
        s, h, body = self.req('GET', f'/__lq/thumb/{sid}/', token=False)
        self.assertEqual((s, body), (200, b'<h1>Home</h1>'))
        self.assertIn("script-src 'none'", h['Content-Security-Policy'])
        for bad in ('.env', 'leak.txt', '../x', 'nope.html'):
            with self.subTest(bad=bad):
                self.assertEqual(self.req('GET', f'/__lq/thumb/{sid}/{bad}', token=False)[0], 404)
        self.assertEqual(self.req('GET', '/__lq/thumb/unknown/', token=False)[0], 404)
        cleanup(root)

    def test_settings(self):
        s, r = self.api('settings')
        self.assertEqual((r['lang'], r['langs'][0]['code']), ('en', 'en'))
        self.assertEqual(r['sites_dir'], os.path.join(self.dir, 'sites'))
        self.assertEqual(self.api('settings', {'lang': 'xx'})[0], 400)
        self.assertEqual(self.api('settings', {'sites_dir': '/nope/nope'})[0], 400)
        s, r = self.api('settings', {'sites_dir': self.outside})
        self.assertEqual(r['sites_dir'], os.path.realpath(self.outside))
        self.assertEqual(self.req('GET', '/__lq/api/settings', token=False)[0], 403)

    def test_setup_only_at_first_launch(self):
        s, h, _ = self.req('GET', '/__setup', token=False)
        self.assertEqual((s, h['Location']), (302, self.hub.sites.url + '#settings'))
        root = temp_project({'index.html': '<h1>x</h1>'})
        sid = self.api('sites/save', {'root': root})[1]['site']['id']
        port = self.hub.sites.start(sid)
        s, h, _ = self.req('GET', '/__setup?next=%2Fabout.html', token=False, port=port)
        self.assertEqual(h['Location'], self.hub.sites.url + f'?back=http%3A%2F%2F127.0.0.1%3A{port}%2Fabout.html#settings')
        cleanup(root)

    def test_settings_page_opens_over_the_site(self):
        root = temp_project({'index.html': '<h1>x</h1>'})
        sid = self.api('sites/save', {'root': root})[1]['site']['id']
        port = self.hub.sites.start(sid)
        site_app = self.hub.sites.running[sid][0]
        s, h, body = self.req('GET', '/__lq/settings', token=False, port=port)
        self.assertEqual(s, 200)
        self.assertEqual(h['Content-Security-Policy'], "frame-ancestors 'self'")      # seul le site lui-même peut l'encadrer
        self.assertIn(site_app.token.encode(), body)
        self.assertIn(b'/__lq/settings.js', body)
        self.assertEqual(self.req('GET', '/__lq/settings.js', token=False, port=port)[0], 200)
        cleanup(root)

    def test_folder_without_home_page_shows_the_bar(self):
        root = temp_project({'about.html': '<h1>About</h1>'})
        sid = self.api('sites/save', {'root': root})[1]['site']['id']
        port = self.hub.sites.start(sid)
        s, _, body = self.req('GET', '/', token=False, port=port)
        self.assertEqual(s, 200)
        self.assertIn(b'No home page yet', body)
        self.assertIn(b'href="about.html"', body)
        self.assertIn(b'/__lq/overlay.js', body)
        self.assertIsNone(self.api('sites')[1]['sites'][0]['thumb'])
        cleanup(root)

    def test_reveal_opens_the_file_manager(self):
        root = temp_project({'index.html': 'x'})
        sid = self.api('sites/save', {'root': root})[1]['site']['id']
        with mock.patch('livequill.server.subprocess.Popen') as popen:
            self.assertEqual(self.api('sites/reveal', {'id': sid})[0], 200)
        self.assertEqual(popen.call_args[0][0][1], os.path.realpath(root))
        self.assertEqual(self.api('sites/reveal', {'id': 'nope'})[0], 400)
        cleanup(root)

    def test_browse_lists_sites_and_places(self):
        os.makedirs(os.path.join(self.dir, 'sites', 'a'))
        with open(os.path.join(self.dir, 'sites', 'a', 'index.html'), 'w') as f:
            f.write('x')
        os.makedirs(os.path.join(self.dir, 'sites', 'b'))
        s, r = self.api('browse?path=' + os.path.join(self.dir, 'sites'))
        self.assertEqual(r['dirs'], [{'name': 'a', 'site': True}, {'name': 'b', 'site': False}])
        self.assertIn('LiveQuill sites', [p['name'] for p in r['places']])
        self.assertFalse(r['here']['empty'])


class Localized(unittest.TestCase):
    def test_markers_are_replaced(self):
        page = localized('hub.html', 'en', 'TOK')
        self.assertNotIn(b'__LQ_', page)
        self.assertIn(b'content="TOK"', page)
        js = localized('ui.js', 'en')
        self.assertIn(b"const LANG = 'en'", js)
        self.assertIn(b'"code": "en"', js)
        self.assertEqual(dictionary('xx'), {})


if __name__ == '__main__':
    unittest.main()
