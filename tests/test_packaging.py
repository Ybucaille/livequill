"""L'archive livequill.pyz (installation officielle) démarre et trouve ses fichiers statiques sans dossier sur disque."""
import os, subprocess, sys, unittest

from helpers import cleanup, temp_project

import livequill

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class Zipapp(unittest.TestCase):
    def setUp(self):
        self.dir = temp_project()
        self.pyz = os.path.join(self.dir, 'livequill.pyz')
        subprocess.run([sys.executable, os.path.join(REPO, 'scripts', 'build-pyz.py'), '--out', self.pyz], check=True, capture_output=True)

    def tearDown(self):
        cleanup(self.dir)

    def test_runs_and_serves_static_files_from_the_archive(self):
        out = subprocess.run([sys.executable, self.pyz, '--version'], capture_output=True, text=True, check=True).stdout
        self.assertEqual(out.strip(), f'livequill {livequill.__version__}')
        code = ('import livequill.server as s; assert s.__file__.startswith(%r), s.__file__; '
                'print(all(len(s.asset(n)) > 1000 for n in ("overlay.js", "setup.html", "hub.html")))' % self.pyz)
        out = subprocess.run([sys.executable, '-I', '-c', 'import sys; sys.path.insert(0, %r); %s' % (self.pyz, code)],
                             capture_output=True, text=True, cwd=self.dir)
        self.assertEqual(out.stdout.strip(), 'True', out.stderr)

    def test_versions_agree(self):
        with open(os.path.join(REPO, 'pyproject.toml')) as f:
            self.assertIn(f'version = "{livequill.__version__}"', f.read())


if __name__ == '__main__':
    unittest.main()
