import os, unittest

from helpers import cleanup, temp_project

from livequill.sandbox import Conversation, HistoryStore, PathError, Project, Snapshot


class PathProtection(unittest.TestCase):
    def setUp(self):
        self.root = temp_project({'index.html': '<h1>Hi</h1>\n', 'css/site.css': 'h1{}', '.env': 'SECRET=1',
                                  '.env.local': 'X=1', '.git/config': '[core]', 'node_modules/a/index.js': '1'})
        self.outside = temp_project({'secret.txt': 'top secret'})
        self.p = Project(self.root)

    def tearDown(self):
        cleanup(self.root, self.outside)

    def test_ok_paths(self):
        self.assertEqual(self.p.resolve('index.html'), os.path.join(os.path.realpath(self.root), 'index.html'))
        self.assertTrue(self.p.resolve('./css/site.css').endswith('site.css'))
        self.assertTrue(self.p.resolve('css\\site.css').endswith('site.css'))   # séparateur Windows
        self.assertTrue(self.p.resolve('new/dir/file.js').endswith('file.js'))  # fichier à créer

    def test_refuse_parent_and_absolute(self):
        for bad in ('../secret.txt', 'css/../../x', '/etc/passwd', '~/x', 'C:/Windows', '', 'a\0b'):
            with self.subTest(bad=bad):
                self.assertRaises(PathError, self.p.resolve, bad)

    def test_refuse_sensitive(self):
        for bad in ('.git/config', '.git', '.env', '.env.local', 'sub/.env.production', 'node_modules/a/index.js',
                    '.livequill/history', 'css/node_modules/x'):
            with self.subTest(bad=bad):
                self.assertRaises(PathError, self.p.resolve, bad)

    def test_refuse_symlink_escape(self):
        os.symlink(self.outside, os.path.join(self.root, 'link'))
        os.symlink(os.path.join(self.outside, 'secret.txt'), os.path.join(self.root, 'secret.txt'))
        os.symlink(os.path.join(self.root, '.git'), os.path.join(self.root, 'gitlink'))
        for bad in ('link/secret.txt', 'secret.txt', 'link', 'gitlink/config'):
            with self.subTest(bad=bad):
                self.assertRaises(PathError, self.p.resolve, bad)
        self.assertRaises(PathError, self.p.write_file, 'link/new.txt', 'x')
        listed = self.p.list_files()
        self.assertNotIn('secret.txt', listed)
        self.assertNotIn('.env', listed)
        self.assertNotIn('.git', listed)

    def test_tools_refuse_too(self):
        self.assertRaises(PathError, self.p.read_file, '.env')
        self.assertRaises(PathError, self.p.edit_file, '../x', 'a', 'b')
        self.assertNotIn('SECRET', self.p.search('SECRET'))


class FileTools(unittest.TestCase):
    def setUp(self):
        self.root = temp_project({'index.html': '<body>\n  <h1 class="t">Cluster <em>overview</em></h1>\n  <p>a</p>\n  <p>a</p>\n</body>\n'})
        self.p = Project(self.root)

    def tearDown(self):
        cleanup(self.root)

    def read(self):
        with open(os.path.join(self.root, 'index.html')) as f:
            return f.read()

    def test_edit_unique_and_ambiguous(self):
        self.p.edit_file('index.html', 'class="t"', 'class="t" style="color:red"')
        self.assertIn('style="color:red"', self.read())
        self.assertRaises(PathError, self.p.edit_file, 'index.html', '<p>a</p>', '<p>b</p>')
        self.p.edit_file('index.html', '<p>a</p>', '<p>b</p>', replace_all=True)
        self.assertEqual(self.read().count('<p>b</p>'), 2)

    def test_edit_tolerates_indentation(self):
        self.p.edit_file('index.html', '<h1 class="t">Cluster <em>overview</em></h1>\n<p>a</p>', '<h1>X</h1>\n<p>a</p>')
        self.assertIn('  <h1>X</h1>\n  <p>a</p>', self.read())

    def test_search_through_tags(self):
        self.assertIn('index.html:2:', self.p.search('Cluster overview'))
        self.assertEqual(self.p.search('nope'), '(no results)')

    def test_read_truncates_long_lines(self):
        with open(os.path.join(self.root, 'big.css'), 'w') as f:
            f.write('a{}\n' + 'x' * 5000 + '\nb{}\n')
        out = self.p.read_file('big.css')
        self.assertIn('line truncated', out)
        self.assertLess(len(out), 1000)


class UndoHistory(unittest.TestCase):
    def setUp(self):
        self.root = temp_project({'index.html': 'v1'})
        self.p = Project(self.root)
        self.h = HistoryStore(self.p)

    def tearDown(self):
        cleanup(self.root)

    def test_undo_restores_and_deletes_created(self):
        tx = self.h.begin('job1', 'demande')
        self.p.write_file('index.html', 'v2', tx=tx)
        self.p.write_file('new.css', 'x', tx=tx)
        tx.commit()
        self.assertEqual(self.h.count(), 1)
        job, files = self.h.undo()
        self.assertEqual(job, 'job1')
        self.assertEqual(sorted(files), ['index.html', 'new.css'])
        with open(os.path.join(self.root, 'index.html')) as f:
            self.assertEqual(f.read(), 'v1')
        self.assertFalse(os.path.exists(os.path.join(self.root, 'new.css')))
        self.assertIsNone(self.h.undo())
        with open(os.path.join(self.root, '.livequill', '.gitignore')) as f:
            self.assertEqual(f.read().strip(), '*')

    def test_snapshot_for_cli_agents(self):
        snap = Snapshot(self.p)
        with open(os.path.join(self.root, 'index.html'), 'w') as f:
            f.write('v2 edited by an external agent')
        tx = self.h.begin('cli', 'x')
        self.assertEqual(snap.record_changes(tx), ['index.html'])
        tx.commit()
        self.h.undo()
        with open(os.path.join(self.root, 'index.html')) as f:
            self.assertEqual(f.read(), 'v1')

    def test_conversation(self):
        c = Conversation(self.p)
        c.add({'job': 'a', 'prompt': 'p', 'status': 'ok'})
        c.mark('a', status='undone')
        self.assertEqual(c.load()[0]['status'], 'undone')
        c.clear()
        self.assertEqual(c.load(), [])


if __name__ == '__main__':
    unittest.main()
