"""Outils communs aux tests : projet temporaire et faux transport HTTP (aucun appel réseau)."""
import json, os, shutil, sys, tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def temp_project(files=None):
    root = tempfile.mkdtemp(prefix='lq-test-')
    for rel, content in (files or {}).items():
        p = os.path.join(root, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, 'w', encoding='utf-8') as f:
            f.write(content)
    return root


def cleanup(*paths):
    for p in paths:
        shutil.rmtree(p, ignore_errors=True)


def sse_lines(*events):
    """Objets JSON -> lignes SSE telles qu'un fournisseur les envoie."""
    out = []
    for ev in events:
        if isinstance(ev, tuple):
            out += [f'event: {ev[0]}', 'data: ' + (ev[1] if isinstance(ev[1], str) else json.dumps(ev[1])), '']
        else:
            out += ['data: ' + (ev if isinstance(ev, str) else json.dumps(ev)), '']
    return out


class FakeTransport:
    """Rejoue des réponses préparées et garde les requêtes envoyées."""

    def __init__(self, streams=(), json_responses=None):
        self.streams = list(streams)        # une liste de lignes par appel en flux
        self.json_responses = json_responses or {}
        self.requests = []

    def lines(self, method, url, headers=None, body=None, job=None):
        self.requests.append({'method': method, 'url': url, 'headers': headers, 'body': body})
        if not self.streams:
            raise AssertionError('appel en flux inattendu')
        item = self.streams.pop(0)
        if isinstance(item, Exception):
            raise item
        yield from item

    def json(self, method, url, headers=None, body=None, timeout=15):
        self.requests.append({'method': method, 'url': url, 'headers': headers, 'body': body})
        for key, resp in self.json_responses.items():
            if key in url:
                return resp(body) if callable(resp) else resp
        raise OSError('Connection refused')
