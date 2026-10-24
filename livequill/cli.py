"""Point d'entrée : livequill [DOSSIER] [--proxy URL] [--port N]"""
import argparse, os, sys, threading, webbrowser

from . import APP, __version__
from .config import Config
from .server import App, make_server


def main(argv=None):
    ap = argparse.ArgumentParser(prog=APP, description='A floating prompt bar: a model edits your local sites live.')
    ap.add_argument('folder', nargs='?', help='add this folder to your sites and open it (otherwise: the sites dashboard)')
    ap.add_argument('--proxy', metavar='URL', help='for this folder, sit in front of a dev server, e.g. http://localhost:5173')
    ap.add_argument('--port', type=int, default=8777, help='dashboard port on 127.0.0.1 (sites take the next ones)')
    ap.add_argument('--config', metavar='FILE', help='config file (default ~/.config/livequill/config.json)')
    ap.add_argument('--no-open', action='store_true', help='do not open the browser')
    ap.add_argument('--version', action='version', version=f'{APP} {__version__}')
    a = ap.parse_args(argv)
    if a.proxy and not a.folder:
        ap.error('--proxy needs a folder: livequill --proxy http://localhost:5173 ./my-project')

    hub = App(None, a.port, config=Config(a.config))
    srv = make_server(hub)
    url = hub.sites.url
    open_url = url
    print(f'{APP} {__version__}')
    print(f'  dashboard  : {url}')
    print(f'  config     : {hub.config.path}')
    if a.folder:
        root = os.path.abspath(a.folder)
        if not os.path.isdir(root):
            ap.error(f'folder not found: {root}')
        try:
            sid = hub.sites.ensure(root, a.proxy)
        except ValueError as e:
            ap.error(str(e))
        port = hub.sites.start(sid)
        open_url = f'http://127.0.0.1:{port}/'
        print(f'  site       : {open_url}  ({root}{" → proxy " + a.proxy if a.proxy else ""})')
    for s in hub.config.sites():
        if not a.folder or s['root'] != os.path.realpath(a.folder):
            print(f'  other site : {s["name"]} ({s["root"]})')
    if not hub.config.configured:   # le tableau de bord et les sites redirigent d'eux-mêmes vers /__setup
        print('  first launch: set up a provider on the setup page', flush=True)
    if not a.no_open:
        threading.Timer(0.6, lambda: webbrowser.open(open_url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print('\nstopped')
    finally:
        hub.sites.stop_all()
    return 0


if __name__ == '__main__':
    sys.exit(main())
