"""Point d'entrée : livequill [DOSSIER] [--proxy URL] [--port N]"""
import argparse, os, sys, threading, webbrowser

from . import APP, __version__
from .config import Config
from .server import App, make_server


def main(argv=None):
    ap = argparse.ArgumentParser(prog=APP, description='Barre de prompt flottante : un modèle modifie ton site local en direct.')
    ap.add_argument('folder', nargs='?', default='.', help='dossier du projet (servi tel quel, ou édité derrière --proxy)')
    ap.add_argument('--proxy', metavar='URL', help='se placer devant un serveur de dev existant, ex. http://localhost:5173')
    ap.add_argument('--port', type=int, default=8777, help='port d’écoute sur 127.0.0.1 (le suivant libre si occupé)')
    ap.add_argument('--config', metavar='FICHIER', help='fichier de config (défaut ~/.config/livequill/config.json)')
    ap.add_argument('--no-open', action='store_true', help='ne pas ouvrir le navigateur')
    ap.add_argument('--version', action='version', version=f'{APP} {__version__}')
    a = ap.parse_args(argv)

    root = os.path.abspath(a.folder)
    if not os.path.isdir(root):
        ap.error(f'dossier introuvable : {root}')
    if a.proxy and not a.proxy.startswith(('http://', 'https://')):
        a.proxy = 'http://' + a.proxy
    app = App(root, a.port, proxy=a.proxy, config=Config(a.config))
    srv = make_server(app)
    url = f'http://127.0.0.1:{app.port}/'
    first = not app.config.configured
    print(f'{APP} {__version__}')
    print(f'  projet : {root}')
    print(f'  mode   : {"proxy → " + a.proxy if a.proxy else "dossier statique"}')
    print(f'  config : {app.config.path}')
    print(f'  ouvre  : {url}{"__setup  (premier lancement)" if first else ""}', flush=True)
    if not a.no_open:
        threading.Timer(0.6, lambda: webbrowser.open(url + ('__setup' if first else ''))).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print('\narrêt')
    return 0


if __name__ == '__main__':
    sys.exit(main())
