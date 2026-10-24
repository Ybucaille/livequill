"""Création d'un site : dossier proposé, copie d'un modèle (page vierge ou modèle de base), nom du site inséré dans les pages."""
import html, os, re, time, unicodedata
from importlib import resources

# Ordre d'affichage dans l'assistant. Textes en anglais : l'interface les traduit si une autre langue est choisie.
TEMPLATES = {
    'blank': {'name': 'Blank page', 'description': 'A clean page with just the name of your site. Describe what you want, the model builds it.'},
    'business': {'name': 'Business website', 'description': 'A one-page company site with services, about and contact sections, ready to adapt.'},
}
IGNORED = {'.DS_Store', 'Thumbs.db', '.livequill', 'desktop.ini'}


def default_dir(config):
    return os.path.expanduser(config.data.get('sites_dir') or os.path.join('~', 'LiveQuill', 'sites'))


def slugify(name):
    s = unicodedata.normalize('NFKD', name).encode('ascii', 'ignore').decode().lower()
    s = re.sub(r'[^a-z0-9]+', '-', s).strip('-')[:60].strip('-')
    return s or 'my-site'


def is_empty(path):
    return not os.path.exists(path) or (os.path.isdir(path) and not (set(os.listdir(path)) - IGNORED))


def suggest(parent, name):
    """Dossier du nouveau site dans `parent` : le nom en minuscules sans accents, suffixé s'il est déjà pris."""
    base = os.path.join(os.path.realpath(os.path.expanduser(parent)), slugify(name))
    path, n = base, 2
    while not is_empty(path):
        path, n = f'{base}-{n}', n + 1
    return path


def files(tid):
    """(chemin relatif, contenu) des fichiers du modèle, lus depuis le paquet (fonctionne aussi dans livequill.pyz)."""
    if tid not in TEMPLATES:
        raise ValueError('unknown template')
    out = []

    def walk(node, prefix):
        for child in sorted(node.iterdir(), key=lambda c: c.name):
            rel = prefix + child.name
            if child.is_dir():
                walk(child, rel + '/')
            else:
                out.append((rel, child.read_bytes()))
    walk(resources.files(__package__).joinpath('templates', tid), '')
    return out


def render(rel, data, name):
    if rel.endswith(('.html', '.htm', '.css', '.js', '.md')):
        data = data.replace(b'{{SITE_NAME}}', html.escape(name).encode()).replace(b'{{YEAR}}', time.strftime('%Y').encode())
    return data


def create(root, name, tid):
    """Crée le site dans `root` (absent ou vide). Ne remplace jamais un fichier existant."""
    root = os.path.realpath(os.path.expanduser(root))
    home = os.path.realpath(os.path.expanduser('~'))
    if root in ('/', home) or root.startswith(('/proc', '/sys', '/dev')):
        raise ValueError('choose a folder for the site, not the root or your home folder')
    if os.path.exists(root) and not os.path.isdir(root):
        raise ValueError('a file with this name already exists')
    if not is_empty(root):
        raise ValueError('this folder is not empty: pick an empty folder, or add it as an existing site')
    content = files(tid)
    os.makedirs(root, exist_ok=True)
    for rel, data in content:
        full = os.path.join(root, *rel.split('/'))
        os.makedirs(os.path.dirname(full), exist_ok=True)
        fd = os.open(full, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0), 0o644)
        with os.fdopen(fd, 'wb') as f:
            f.write(render(rel, data, name))
    return root
