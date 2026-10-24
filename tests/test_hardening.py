"""Non-régression des défauts trouvés à l'audit : annulation après un agent CLI, liens symboliques, TOCTOU, isolation."""
import http.client, os, threading, time, unittest
from unittest import mock

from helpers import FakeTransport, cleanup, temp_project

from livequill.adapters.cli import CLIAdapter
from livequill.config import Config
from livequill.sandbox import HistoryStore, PathError, Project, Snapshot, SnapshotTooLarge
from livequill.server import App, make_server


def read(path):
    with open(path, 'rb') as f:
        return f.read()


class CliUndo(unittest.TestCase):
    """Un agent CLI écrit lui-même : l'instantané pris avant doit permettre de tout annuler."""

    def setUp(self):
        self.root = temp_project({'index.html': 'v1', 'css/site.css': 'a{}', '.env': 'SECRET=1'})
        self.outside = temp_project({'target.txt': 'outside'})
        self.p = Project(self.root)
        self.h = HistoryStore(self.p)

    def tearDown(self):
        cleanup(self.root, self.outside)

    def run_agent(self, change):
        snap = Snapshot(self.p)
        change()
        tx = self.h.begin('cli', 'demande')
        files = snap.record_changes(tx)
        tx.commit()
        snap.close()
        return files

    def test_large_file_is_restored(self):
        big = os.path.join(self.root, 'video.bin')
        with open(big, 'wb') as f:
            f.write(b'\1' * 6_000_000)                       # au-dessus de l'ancienne limite de 5 Mo
        self.run_agent(lambda: open(big, 'wb').write(b'\2' * 6_000_000))
        self.h.undo()
        self.assertEqual(read(big), b'\1' * 6_000_000)

    def test_env_files_are_restored(self):
        def change():
            with open(os.path.join(self.root, '.env'), 'w') as f:
                f.write('SECRET=changed')
            with open(os.path.join(self.root, '.env.local'), 'w') as f:
                f.write('X=1')
        self.assertEqual(sorted(self.run_agent(change)), ['.env', '.env.local'])
        self.h.undo()
        self.assertEqual(read(os.path.join(self.root, '.env')), b'SECRET=1')
        self.assertFalse(os.path.exists(os.path.join(self.root, '.env.local')))

    def test_same_size_and_restored_mtime_is_detected(self):
        path = os.path.join(self.root, 'index.html')
        st = os.stat(path)

        def change():
            with open(path, 'w') as f:
                f.write('v2')                                   # même taille
            os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns))  # et date de modification remise à l'identique
        self.assertEqual(self.run_agent(change), ['index.html'])
        self.h.undo()
        self.assertEqual(read(path), b'v1')

    def test_file_replaced_by_outside_symlink(self):
        path = os.path.join(self.root, 'index.html')

        def change():
            os.remove(path)
            os.symlink(os.path.join(self.outside, 'target.txt'), path)
        self.run_agent(change)
        self.h.undo()
        self.assertFalse(os.path.islink(path))
        self.assertEqual(read(path), b'v1')
        self.assertEqual(read(os.path.join(self.outside, 'target.txt')), b'outside')   # rien écrit dehors

    def test_dir_replaced_by_outside_symlink(self):
        css = os.path.join(self.root, 'css')

        def change():
            os.remove(os.path.join(css, 'site.css'))
            os.rmdir(css)
            os.symlink(self.outside, css)
        self.run_agent(change)
        self.h.undo()
        self.assertFalse(os.path.islink(css))
        self.assertEqual(read(os.path.join(css, 'site.css')), b'a{}')
        self.assertFalse(os.path.exists(os.path.join(self.outside, 'site.css')))

    def test_too_large_project_is_refused_before_running(self):
        with mock.patch('livequill.sandbox.SNAPSHOT_MAX', 10):
            with self.assertRaises(SnapshotTooLarge):
                Snapshot(self.p)

    def test_permissions_of_backups(self):
        env = os.path.join(self.root, '.env')
        os.chmod(env, 0o600)
        self.run_agent(lambda: open(env, 'w').write('SECRET=2'))
        for d, _, files in os.walk(os.path.join(self.root, '.livequill', 'history')):
            for f in files:
                if f.endswith('.bak'):
                    self.assertEqual(os.stat(os.path.join(d, f)).st_mode & 0o077, 0)   # la copie du secret n'est pas lisible par les autres


class SymlinkRace(unittest.TestCase):
    """Le chemin vérifié ne doit pas pouvoir être remplacé par un lien avant l'écriture (TOCTOU)."""

    def setUp(self):
        self.root = temp_project({'index.html': 'v1'})
        self.outside = temp_project({'target.txt': 'outside'})

    def tearDown(self):
        cleanup(self.root, self.outside)

    def test_swap_to_symlink_after_check(self):
        p = Project(self.root)
        real_resolve = p.resolve
        target = os.path.join(self.outside, 'target.txt')

        def racy(rel, **kw):
            full = real_resolve(rel, **kw)
            if os.path.exists(full) and not os.path.islink(full):    # un autre processus échange le fichier
                os.remove(full)
                os.symlink(target, full)
            return full
        p.resolve = racy
        with self.assertRaises((PathError, OSError)):
            p.write_file('index.html', 'pwned')
        self.assertEqual(read(target), b'outside')


class StaticSymlinks(unittest.TestCase):
    """Le serveur de fichiers ne suit pas un lien vers l'extérieur ni vers un fichier interdit."""

    def setUp(self):
        self.root = temp_project({'index.html': '<body>hi</body>', '.env': 'SECRET=1'})
        self.outside = temp_project({'secret.txt': 'TOP-SECRET'})
        os.symlink(os.path.join(self.outside, 'secret.txt'), os.path.join(self.root, 'leak.txt'))
        os.symlink(os.path.join(self.root, '.env'), os.path.join(self.root, 'env.txt'))
        os.symlink(self.outside, os.path.join(self.root, 'outdir'))
        self.cfgdir = temp_project()
        config = Config(os.path.join(self.cfgdir, 'c.json'))
        config.upsert('ollama', {'type': 'ollama', 'models': [{'id': 'q', 'label': 'q'}], 'default_model': 'q'})
        self.app = App(self.root, 0, config=config, transport=FakeTransport())
        self.srv = make_server(self.app)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        cleanup(self.root, self.outside, self.cfgdir)

    def get(self, path):
        c = http.client.HTTPConnection('127.0.0.1', self.app.port, timeout=5)
        c.request('GET', path, headers={'Host': f'127.0.0.1:{self.app.port}'})
        r = c.getresponse()
        out = r.status, r.read()
        c.close()
        return out

    def test_links_are_not_followed_outside(self):
        for path in ('/leak.txt', '/env.txt', '/outdir/secret.txt'):
            with self.subTest(path=path):
                status, body = self.get(path)
                self.assertEqual(status, 404)
                self.assertNotIn(b'SECRET', body)
        self.assertEqual(self.get('/index.html')[0], 200)


class SingleReload(unittest.TestCase):
    """Une annulation ou un retour de version recharge la page une fois, pas deux (signal + surveillance des fichiers)."""

    def setUp(self):
        self.root = temp_project({'index.html': 'v1'})
        from livequill.server import Reloader
        self.r = Reloader(Project(self.root))
        time.sleep(0.6)                                  # premier état relevé par la surveillance

    def tearDown(self):
        self.r.alive = False
        cleanup(self.root)

    def test_explicit_bump_is_not_repeated_by_the_watcher(self):
        with open(os.path.join(self.root, 'index.html'), 'w') as f:
            f.write('v2')
        self.r.bump(0.05)
        time.sleep(1.6)
        self.assertEqual(self.r.gen, 1)

    def test_manual_edit_still_reloads(self):
        with open(os.path.join(self.root, 'index.html'), 'w') as f:
            f.write('v3, by hand')
        time.sleep(1.2)
        self.assertEqual(self.r.gen, 1)


class AgentIsolation(unittest.TestCase):
    def test_claude_is_confined(self):
        argv = CLIAdapter({'command': 'claude'}).command('/p', 'haiku', {}, [])
        for flag in ('--restricted', '--strict-mcp-config'):
            self.assertIn(flag, argv)
        self.assertIn('Write(./.env*)', argv)
        self.assertEqual(argv[argv.index('--tools') + 1], 'Read,Edit,Write,Glob,Grep')

    def test_codex_ignores_user_config(self):
        argv = CLIAdapter({'command': 'codex'}).command('/p', 'default', {}, [])
        self.assertIn('--ignore-user-config', argv)     # pas de serveurs MCP ni de dossiers inscriptibles ajoutés par l'utilisateur
        self.assertEqual(argv[argv.index('--sandbox') + 1], 'workspace-write')
        self.assertIn('approval_policy="never"', argv)
        self.assertIn('sandbox_workspace_write.network_access=false', argv)

    def test_codex_keeps_user_model(self):
        import tempfile
        home = tempfile.mkdtemp()
        with open(os.path.join(home, 'config.toml'), 'w') as f:
            f.write('model = "gpt-x"\napproval_policy = "never"\n[profiles.a]\nmodel = "other"\n')
        with mock.patch.dict(os.environ, {'CODEX_HOME': home}):
            argv = CLIAdapter({'command': 'codex'}).command('/p', 'default', {}, [])
            self.assertEqual(argv[argv.index('--model') + 1], 'gpt-x')
            argv = CLIAdapter({'command': 'codex'}).command('/p', 'o4-mini', {}, [])
            self.assertEqual(argv[argv.index('--model') + 1], 'o4-mini')
        cleanup(home)


if __name__ == '__main__':
    unittest.main()
