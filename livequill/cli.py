"""Point d'entrée : livequill [DOSSIER] [--proxy URL] [--port N]"""
import argparse, os, sys, threading, webbrowser

from . import APP, __version__
from .config import Config
from .server import App, make_server


def main(argv=None):
    ap = argparse.ArgumentParser(prog=APP, description='Barre de prompt flottante : un modèle modifie tes sites locaux en direct.')
    ap.add_argument('folder', nargs='?', help='ajoute ce dossier à tes sites et l’ouvre (sinon : tableau de bord des sites)')
    ap.add_argument('--proxy', metavar='URL', help='pour ce dossier, se placer devant un serveur de dev, ex. http://localhost:5173')
    ap.add_argument('--port', type=int, default=8777, help='port du tableau de bord sur 127.0.0.1 (les sites prennent les suivants)')
    ap.add_argument('--config', metavar='FICHIER', help='fichier de config (défaut ~/.config/livequill/config.json)')
    ap.add_argument('--no-open', action='store_true', help='ne pas ouvrir le navigateur')
    ap.add_argument('--version', action='version', version=f'{APP} {__version__}')
    a = ap.parse_args(argv)
    if a.proxy and not a.folder:
        ap.error('--proxy s’utilise avec un dossier : livequill --proxy http://localhost:5173 ./mon-projet')

    hub = App(None, a.port, config=Config(a.config))
    srv = make_server(hub)
    url = hub.sites.url
    open_url = url
    print(f'{APP} {__version__}')
    print(f'  tableau de bord : {url}')
    print(f'  config          : {hub.config.path}')
    if a.folder:
        root = os.path.abspath(a.folder)
        if not os.path.isdir(root):
            ap.error(f'dossier introuvable : {root}')
        try:
            sid = hub.sites.ensure(root, a.proxy)
        except ValueError as e:
            ap.error(str(e))
        port = hub.sites.start(sid)
        open_url = f'http://127.0.0.1:{port}/'
        print(f'  site            : {open_url}  ({root}{" → proxy " + a.proxy if a.proxy else ""})')
    for s in hub.config.sites():
        if not a.folder or s['root'] != os.path.realpath(a.folder):
            print(f'  autre site      : {s["name"]} ({s["root"]})')
    if not hub.config.configured:   # le tableau de bord et les sites redirigent d'eux-mêmes vers /__setup
        print('  premier lancement : configure un fournisseur sur la page de setup', flush=True)
    if not a.no_open:
        threading.Timer(0.6, lambda: webbrowser.open(open_url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print('\narrêt')
    finally:
        hub.sites.stop_all()
    return 0


if __name__ == '__main__':
    sys.exit(main())
