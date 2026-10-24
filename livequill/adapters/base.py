"""Interface commune des adaptateurs.

Format interne des messages (indépendant du fournisseur) :
  {"role": "user", "content": [{"type": "text", "text": ...}, {"type": "image", "media_type": ..., "data": <base64>}]}
  {"role": "assistant", "content": [{"type": "text", "text": ...},
                                    {"type": "tool_call", "id": ..., "name": ..., "args": {...}, "meta": {...}},
                                    {"type": "raw", "provider": "anthropic", "block": {...}}]}
  {"role": "tool", "results": [{"id": ..., "name": ..., "content": str, "is_error": bool}]}

Un adaptateur transforme ce format en requête HTTP (build_request, pure donc testable),
puis lit le flux de réponse (parse_stream) et produit des événements :
  {"type": "delta", "text": ...}       texte affiché en direct
  {"type": "thinking", "text": ...}    réflexion (si le fournisseur la renvoie)
  {"type": "part", "part": {...}}      bloc terminé, ajouté au message assistant
  {"type": "stop", "reason": "end" | "tool_use" | "max_tokens" | "refusal"}
"""
from ..config import api_key
from ..net import HTTPError, Transport


class ToolsUnsupported(Exception):
    """Le modèle refuse les tool calls : l'agent repasse en mode texte."""


class Adapter:
    kind = ''            # identifiant du type de fournisseur
    is_cli = False       # True : l'agent CLI fait lui-même la boucle et écrit les fichiers
    default_base = ''

    def __init__(self, conf, transport=None):
        self.conf = conf
        self.http = transport or Transport()

    @property
    def base(self):
        return (self.conf.get('base_url') or self.default_base).rstrip('/')

    @property
    def key(self):
        return api_key(self.conf)

    # --- à fournir par chaque adaptateur ---------------------------------
    def list_models(self):
        """[{"id": str, "label": str, "meta": {...}}] — meta sert à calculer les capacités."""
        raise NotImplementedError

    def caps(self, model, meta=None):
        """{"tools": bool, "vision": bool, "effort": [[valeur, libellé], ...] | None, "fast": bool}"""
        return {'tools': True, 'vision': False, 'effort': None, 'fast': False}

    def build_request(self, model, system, messages, tools, opts):
        """-> (méthode, url, en-têtes, corps JSON)"""
        raise NotImplementedError

    def parse_stream(self, lines):
        raise NotImplementedError

    # --- commun ----------------------------------------------------------
    def test(self):
        models = self.list_models()
        return f'{len(models)} model(s) available', models

    def chat(self, model, system, messages, tools, opts, job=None):
        method, url, headers, body = self.build_request(model, system, messages, tools, opts)
        try:
            yield from self.parse_stream(self.http.lines(method, url, headers, body, job))
        except HTTPError as e:
            if tools and looks_like_no_tools(e.body):
                raise ToolsUnsupported(e.body) from None
            raise


def looks_like_no_tools(body):
    b = (body or '').lower()
    return 'does not support tools' in b or 'tool use is not supported' in b or ('tools' in b and 'not supported' in b) \
        or 'function calling is not enabled' in b


EFFORT_LABELS = {'off': 'Off', 'on': 'On', 'minimal': 'Minimal', 'low': 'Low', 'medium': 'Medium',
                 'high': 'High', 'xhigh': 'Extra high', 'max': 'Max'}


def levels(*vals):
    return [[v, EFFORT_LABELS.get(v, v)] for v in vals]


def text_of(content):
    return ''.join(p.get('text', '') for p in content if p.get('type') == 'text')
