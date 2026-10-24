#!/usr/bin/env python3
"""Construit dist/livequill.pyz : LiveQuill en un seul fichier exécutable (python3 livequill.pyz).

Archive reproductible : fichiers triés, dates fixes. Seuls le code et livequill/static/ y entrent.

    python3 scripts/build-pyz.py [--out dist/livequill.pyz]
"""
import argparse, os, stat, zipfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAIN = b'import sys\nfrom livequill.cli import main\nsys.exit(main())\n'
STAMP = (1980, 1, 1, 0, 0, 0)


def files():
    pkg = os.path.join(REPO, 'livequill')
    for d, dirs, names in os.walk(pkg):
        dirs[:] = sorted(x for x in dirs if x != '__pycache__')
        for n in sorted(names):
            if n.endswith('.py') or os.path.relpath(d, pkg).split(os.sep)[0] == 'static':
                full = os.path.join(d, n)
                yield full, os.path.relpath(full, REPO).replace(os.sep, '/')


def entry(name):
    info = zipfile.ZipInfo(name, STAMP)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o644 << 16
    return info


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', default=os.path.join(REPO, 'dist', 'livequill.pyz'))
    out = ap.parse_args().out
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    with open(out, 'wb') as f:
        f.write(b'#!/usr/bin/env python3\n')
        with zipfile.ZipFile(f, 'w') as z:
            z.writestr(entry('__main__.py'), MAIN)
            for full, name in files():
                with open(full, 'rb') as src:
                    z.writestr(entry(name), src.read())
    os.chmod(out, os.stat(out).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    print(out)


if __name__ == '__main__':
    main()
