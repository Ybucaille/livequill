"""Versions du site : un état après chaque demande, aperçu vivant d'une version, retour à n'importe quelle version."""
import http.client, json, os, threading, unittest

from helpers import FakeTransport, cleanup, temp_project

from livequill.config import Config
from livequill.sandbox import HistoryStore, PathError, Project
from livequill.server import App, make_server


def change(project, history, job, prompt, edits):
    """Une demande : sauvegarde puis écriture de chaque fichier (None = suppression)."""
    tx = history.begin(job, prompt)
    for rel, content in edits.items():
        full = project.resolve(rel)
        tx.backup(full)
        if content is None:
            os.remove(full)
        else:
            project.write_file(rel, content)
    tx.commit()


class History(unittest.TestCase):
    def setUp(self):
        self.root = temp_project({'index.html': 'v0', 'a.css': 'a0'})
        self.p = Project(self.root)
        self.h = HistoryStore(self.p)
        change(self.p, self.h, 'zz1', 'premier', {'index.html': 'v1'})
        change(self.p, self.h, 'aa2', 'deuxième', {'index.html': 'v2', 'new.js': 'n2'})
        change(self.p, self.h, 'mm3', 'troisième', {'a.css': 'a3', 'new.js': None})
        self.ids = [v['id'] for v in self.h.versions()]       # du plus récent au plus ancien

    def tearDown(self):
        cleanup(self.root)

    def read_at(self, version, rel):
        src = self.h.file_at(version, rel)
        if src is None:
            return None
        with open(os.path.join(self.root, rel) if src == 'current' else src) as f:
            return f.read()

    def test_list(self):
        self.assertEqual([v['prompt'] for v in self.h.versions()], ['troisième', 'deuxième', 'premier'])
        self.assertEqual(self.h.versions()[1]['files'], ['index.html', 'new.js'])

    def test_file_at_each_version(self):
        latest, middle, first = self.ids
        self.assertEqual(self.read_at(latest, 'index.html'), 'v2')
        self.assertEqual(self.read_at(middle, 'index.html'), 'v2')
        self.assertEqual(self.read_at(middle, 'a.css'), 'a0')
        self.assertEqual(self.read_at(middle, 'new.js'), 'n2')            # supprimé ensuite, mais présent à cette version
        self.assertEqual(self.read_at(first, 'index.html'), 'v1')
        self.assertIsNone(self.read_at(first, 'new.js'))                  # n'existait pas encore
        self.assertEqual(self.read_at('origin', 'index.html'), 'v0')
        self.assertEqual(self.read_at('origin', 'a.css'), 'a0')
        self.assertRaises(PathError, self.h.file_at, 'nope', 'index.html')

    def test_restore_to_any_version(self):
        first = self.ids[-1]
        done = self.h.restore_to(first)
        self.assertEqual([job for job, _ in done], ['mm3', 'aa2'])
        with open(os.path.join(self.root, 'index.html')) as f:
            self.assertEqual(f.read(), 'v1')
        self.assertFalse(os.path.exists(os.path.join(self.root, 'new.js')))
        self.h.restore_to('origin')
        with open(os.path.join(self.root, 'index.html')) as f:
            self.assertEqual(f.read(), 'v0')
        self.assertEqual(self.h.count(), 0)
        self.assertRaises(PathError, self.h.restore_to, 'nope')


class PreviewRoute(unittest.TestCase):
    def setUp(self):
        self.root = temp_project({'index.html': '<body><h1>v0</h1><link href="style.css"></body>', 'style.css': 'h1{}', '.env': 'S=1'})
        self.outside = temp_project({'secret.txt': 'TOP'})
        os.symlink(os.path.join(self.outside, 'secret.txt'), os.path.join(self.root, 'leak.txt'))
        self.cfgdir = temp_project()
        config = Config(os.path.join(self.cfgdir, 'c.json'))
        config.upsert('ollama', {'type': 'ollama', 'models': [{'id': 'q', 'label': 'q'}], 'default_model': 'q'})
        self.app = App(self.root, 0, config=config, transport=FakeTransport())
        change(self.app.project, self.app.history, 'j1', 'rouge', {'index.html': '<body><h1>v1</h1></body>'})
        self.srv = make_server(self.app)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        cleanup(self.root, self.outside, self.cfgdir)

    def get(self, path, method='GET', body=None, token=False):
        c = http.client.HTTPConnection('127.0.0.1', self.app.port, timeout=5)
        h = {'Host': f'127.0.0.1:{self.app.port}'}
        if token:
            h['X-LQ-Token'] = self.app.token
        if body is not None:
            h['Content-Type'] = 'application/json'
        c.request(method, path, body=json.dumps(body).encode() if body is not None else None, headers=h)
        r = c.getresponse()
        out = r.status, r.getheader('Content-Type'), r.read()
        c.close()
        return out

    def test_preview_and_restore(self):
        s, ctype, body = self.get('/__lq/preview/origin/')
        self.assertEqual(s, 200)
        self.assertIn(b'<h1>v0</h1>', body)
        self.assertIn('Aperçu · avant toute modification'.encode(), body)
        self.assertNotIn(b'overlay.js', body)                              # pas de barre dans un aperçu
        self.assertEqual(self.get('/__lq/preview/origin/style.css')[2], b'h1{}')
        self.assertIn(b'<h1>v1</h1>', self.get('/')[2])                    # le site, lui, est à la version actuelle
        for bad in ('/__lq/preview/origin/.env', '/__lq/preview/origin/leak.txt', '/__lq/preview/nope/', '/__lq/preview/origin/../x'):
            with self.subTest(bad=bad):
                self.assertEqual(self.get(bad)[0], 404)
        versions = json.loads(self.get('/__lq/api/versions', token=True)[2])['versions']
        self.assertEqual([v['prompt'] for v in versions], ['rouge'])
        s, _, body = self.get('/__lq/api/versions/restore', 'POST', {'id': 'origin'}, token=True)
        self.assertTrue(json.loads(body)['ok'])
        self.assertIn(b'<h1>v0</h1>', self.get('/')[2])
        self.assertEqual(self.get('/__lq/api/versions/restore', 'POST', {'id': 'origin'})[0], 403)   # sans jeton


if __name__ == '__main__':
    unittest.main()
