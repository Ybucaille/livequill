"""Registre des fournisseurs. Ajouter un fournisseur = écrire un adaptateur et l'inscrire dans KINDS."""
from .anthropic import AnthropicAdapter
from .base import Adapter, ToolsUnsupported
from .cli import CLIAdapter
from .gemini import GeminiAdapter
from .ollama import OllamaAdapter
from .openai import CustomAdapter, LlamaCppAdapter, LMStudioAdapter, MistralAdapter, OpenAIAdapter, OpenRouterAdapter

# group : « cloud », « local » ou « cli » (rangement de la page de setup)
# fields : champs du formulaire — key (clé API), base_url, model (nom libre), extra_models, num_ctx
KINDS = {
    # --- API cloud : url = page où créer la clé --------------------------------------------------------
    'anthropic': {'label': 'Anthropic', 'group': 'cloud', 'adapter': AnthropicAdapter, 'fields': ['key'], 'env': 'ANTHROPIC_API_KEY',
                  'url': 'https://console.anthropic.com/settings/keys', 'url_label': 'Créer une clé',
                  'help': 'Clé sur console.anthropic.com → Settings → API keys'},
    'openai': {'label': 'OpenAI', 'group': 'cloud', 'adapter': OpenAIAdapter, 'fields': ['key'], 'env': 'OPENAI_API_KEY',
               'url': 'https://platform.openai.com/api-keys', 'url_label': 'Créer une clé',
               'help': 'Clé sur platform.openai.com → API keys'},
    'gemini': {'label': 'Google Gemini', 'group': 'cloud', 'adapter': GeminiAdapter, 'fields': ['key'], 'env': 'GEMINI_API_KEY',
               'url': 'https://aistudio.google.com/apikey', 'url_label': 'Créer une clé',
               'help': 'Clé sur aistudio.google.com → Get API key'},
    'mistral': {'label': 'Mistral', 'group': 'cloud', 'adapter': MistralAdapter, 'fields': ['key'], 'env': 'MISTRAL_API_KEY',
                'url': 'https://console.mistral.ai/api-keys', 'url_label': 'Créer une clé',
                'help': 'Clé sur console.mistral.ai → API keys'},
    'openrouter': {'label': 'OpenRouter', 'group': 'cloud', 'adapter': OpenRouterAdapter, 'fields': ['key'], 'env': 'OPENROUTER_API_KEY',
                   'url': 'https://openrouter.ai/keys', 'url_label': 'Créer une clé',
                   'help': 'Clé sur openrouter.ai/keys — donne accès à des centaines de modèles'},
    # --- modèles locaux : url = page de téléchargement ; « Autre » pour tout serveur compatible OpenAI ----
    'ollama': {'label': 'Ollama', 'group': 'local', 'adapter': OllamaAdapter, 'fields': ['base_url', 'num_ctx'],
               'url': 'https://ollama.com/download', 'url_label': 'Télécharger',
               'help': 'ollama serve, puis ollama pull qwen2.5:7b-instruct (ou un autre modèle qui gère les tools)'},
    'lmstudio': {'label': 'LM Studio', 'group': 'local', 'adapter': LMStudioAdapter, 'fields': ['base_url'],
                 'url': 'https://lmstudio.ai', 'url_label': 'Télécharger',
                 'help': 'Onglet Developer → Start server (port 1234 par défaut)'},
    'llamacpp': {'label': 'llama.cpp server', 'group': 'local', 'adapter': LlamaCppAdapter, 'fields': ['base_url'],
                 'url': 'https://github.com/ggml-org/llama.cpp', 'url_label': 'Installer',
                 'help': 'llama-server -m modele.gguf --jinja (port 8080 par défaut, --jinja active les tools)'},
    'custom': {'label': 'Autre', 'group': 'local', 'adapter': CustomAdapter, 'fields': ['base_url', 'key', 'model'], 'multi': True,
               'help': 'N’importe quel serveur compatible OpenAI (/v1/chat/completions) : vLLM, LocalAI, Jan, Groq, DeepSeek, un proxy maison…'},
    # --- agents CLI : login = commande pour se connecter au compte du fournisseur ------------------------
    'cli-claude': {'label': 'Claude Code (CLI)', 'group': 'cli', 'adapter': CLIAdapter, 'command': 'claude', 'fields': ['extra_models'],
                   'url': 'https://code.claude.com/docs', 'url_label': 'Installer', 'login': 'claude, puis /login',
                   'help': 'Utilise « claude -p » dans le dossier du projet, avec ton compte Claude Code (CLI, ou celle intégrée à l’app Claude Desktop)'},
    'cli-codex': {'label': 'Codex (CLI)', 'group': 'cli', 'adapter': CLIAdapter, 'command': 'codex', 'fields': ['extra_models'],
                  'url': 'https://github.com/openai/codex', 'url_label': 'Installer', 'login': 'codex login',
                  'help': 'Utilise « codex exec » dans le dossier du projet, avec ton compte (CLI, ou celle intégrée à l’app ChatGPT ou Codex)'},
    'cli-gemini': {'label': 'Gemini CLI', 'group': 'cli', 'adapter': CLIAdapter, 'command': 'gemini', 'fields': ['extra_models'],
                   'url': 'https://github.com/google-gemini/gemini-cli', 'url_label': 'Installer', 'login': 'gemini (connexion au 1er lancement)',
                   'help': 'Utilise « gemini -p » dans le dossier du projet'},
}


def make(conf, transport=None):
    kind = KINDS[conf['type']]
    conf = dict(conf)
    if 'command' in kind:
        conf.setdefault('command', kind['command'])
    return kind['adapter'](conf, transport)


def public_kinds():
    return {k: {x: v for x, v in d.items() if x != 'adapter'} for k, d in KINDS.items()}


__all__ = ['Adapter', 'KINDS', 'ToolsUnsupported', 'make', 'public_kinds']
