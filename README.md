<div align="center">

# LiveQuill

**Edit your local website by asking.**<br>
A floating prompt bar for any local site: type a change, the model of your choice edits your real files, and the page reloads.

[![CI](https://github.com/Ybucaille/livequill/actions/workflows/ci.yml/badge.svg)](https://github.com/Ybucaille/livequill/actions/workflows/ci.yml)
[![CodeQL](https://github.com/Ybucaille/livequill/actions/workflows/codeql.yml/badge.svg)](https://github.com/Ybucaille/livequill/actions/workflows/codeql.yml)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Zero dependencies](https://img.shields.io/badge/dependencies-0-success)](pyproject.toml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![PRs welcome](https://img.shields.io/badge/PRs-welcome-ff69b4.svg)](CONTRIBUTING.md)

[**Website**](https://ybucaille.github.io/livequill/) · [Quick start](#quick-start) · [Providers](#providers) · [Roadmap](#roadmap) · [FAQ](#faq)

<a href="https://ybucaille.github.io/livequill/"><img src="docs/demo.gif" alt="LiveQuill demo: first launch, a provider is set up, a site is added and opened, a request turns the title red, then Undo restores it" width="860"></a>

<sub>A real, unedited first run: setup → add a site → “make the hero title red” → Undo → switch theme. <a href="docs/demo.mp4">MP4 version</a>.</sub>

</div>

## Highlights

- **Ask, don't dig.** Describe the change on the page you are looking at. The model finds the code, edits it, and the page reloads.
- **Any model.** Claude, GPT, Gemini, Mistral, OpenRouter, local models through Ollama, LM Studio or llama.cpp, any OpenAI-compatible server, or the Claude Code, Codex and Gemini CLIs you already use. No CLI? The agent built into the **Claude Desktop** or **ChatGPT** app works too.
- **A new site in a minute.** A short wizard creates a site from a ready-made template or a blank page, no technical skills needed. Or add a folder you already have.
- **All your sites in one place.** A dashboard lists your projects with a live thumbnail. Each one is served as-is or behind your dev server (Vite, Next, Astro…).
- **Safe by default.** Sandboxed file tools, a backup before every edit, one-click **Undo**, and API keys that never reach the browser.
- **Every request is a version.** Browse them in the bar, preview any of them live without touching your files, and go back to it in one click.
- **Nothing to install in your project.** The bar is added while pages are served, never written to disk. **Zero dependencies**: just Python.

## Quick start

You need **Python 3.10+**, nothing else. Runs on Linux and macOS (Windows is not supported).

```bash
curl -fsSL https://ybucaille.github.io/livequill/install.sh | sh
livequill
```

The script installs a single file, `livequill.pyz` from the [latest release](https://github.com/Ybucaille/livequill/releases/latest), after checking its SHA-256. It goes in `~/.local/share/livequill`, with the `livequill` command in `~/.local/bin`. It needs no root, no git and no pip. Run it again to upgrade. It is tested on Ubuntu, Debian, Fedora, Rocky Linux, Arch, Alpine, openSUSE and macOS. If Python is missing, it tells you the command for your distribution.

<details>
<summary>Other ways to install</summary>

| | |
|---|---|
| **pipx / uv** | `pipx install livequill` or `uv tool install livequill` (from [PyPI](https://pypi.org/project/livequill/)) |
| **Single file** | Download `livequill.pyz` from a [release](https://github.com/Ybucaille/livequill/releases) and run `python3 livequill.pyz` |
| **From source** | `git clone https://github.com/Ybucaille/livequill.git && ./livequill/bin/livequill` |

</details>

Press <kbd>Ctrl</kbd>+<kbd>C</kbd> in the terminal to stop it.

1. **Setup.** The first launch opens the setup page. Pick a provider: paste an API key, or click a local model or CLI agent detected on your machine. You only see this page once: later, providers are managed from the ⚙ settings of **My sites**.
2. **Create a site.** You land on **My sites**. Click **New site**: pick a starting point (a business website template or a blank page), give it a name, and LiveQuill creates it in `~/LiveQuill/sites`. Already have a site? Choose **Add an existing site** and select its folder.
3. **Open it and ask.** The site opens with the floating bar. Try *“make the hero title red”*, watch it change, then click **Undo**.

Handy shortcuts:

```bash
livequill ~/projects/my-site                            # add this folder and open it
livequill --proxy http://localhost:5173 ~/projects/app   # same, behind your dev server
```

<details>
<summary>All command-line options</summary>

| Option | Description |
|---|---|
| `FOLDER` | Add this folder to your sites (if needed) and open it. Without it, the dashboard opens. |
| `--proxy URL` | Serve `FOLDER` behind an existing dev server instead of serving its files. |
| `--port N` | Dashboard port on `127.0.0.1` (default `8777`). Each site gets its own port after it. |
| `--config FILE` | Alternative config file (default `~/.config/livequill/config.json`). |
| `--no-open` | Do not open the browser. |

</details>

## Screenshots

<table>
<tr>
<td width="50%"><img src="docs/images/hub.jpg" alt="My sites dashboard"></td>
<td width="50%"><img src="docs/images/setup.jpg" alt="Provider setup page"></td>
</tr>
<tr>
<td><b>My sites.</b> Create, add, open or stop each project, with a live thumbnail. Every site runs on its own port, with its own history.</td>
<td><b>Setup.</b> Each provider shows <b>Available</b> (connection checked live), <b>Detected</b> (one click away), or a link to get a key.</td>
</tr>
<tr>
<td><img src="docs/images/wizard.jpg" alt="New site wizard"></td>
<td><img src="docs/images/hero.jpg" alt="Floating bar with the live answer"></td>
</tr>
<tr>
<td><b>New site.</b> A short wizard: kind of site, starting point with a live preview, name and folder. Built for people who never made a website.</td>
<td><b>Live panel.</b> The answer streams in, with the tools called and the files changed. It survives the reload.</td>
</tr>
<tr>
<td colspan="2"><img src="docs/images/themes.png" alt="Claude, ChatGPT, Gemini and Minimal themes"></td>
</tr>
<tr>
<td colspan="2"><b>Themes.</b> Claude, ChatGPT, Gemini and Minimal, in light and dark, following your system.</td>
</tr>
</table>

## What the bar can do

| | |
|---|---|
| **Models** | Every configured model in one menu, grouped by provider. Switch without restarting. **Reasoning** and **Fast** only appear when the model supports them. |
| **Attachments** | + button, paste or drag and drop (15 MB per file). Images go in as vision input when supported, text files are inlined. |
| **Page context** | The current route, the matching HTML file, the visible headings and your text selection are sent along, so “the title” means what you see. |
| **History** | Earlier requests of the project are replayed as plain text, so context survives a model switch. **New** starts over. |
| **Undo** | Restores the files changed by the last request. Works several times in a row, and for CLI agents too. |
| **Versions** | The clock button lists every request as a version: when, which model, which files. Select one to see a live preview of the site at that point, open it in a tab, or go back to it in one click. |
| **Small models** | No tool-call support? LiveQuill falls back to a text protocol (`tool` blocks and `SEARCH/REPLACE` edits) and nudges models that stop halfway. |

## Providers

| Provider | What you need |
|---|---|
| **Anthropic** | API key from [console.anthropic.com](https://console.anthropic.com/settings/keys). Effort levels on Claude 4.6+, **Fast** on Opus 5.5 / 5 / 4.8. |
| **OpenAI** | API key from [platform.openai.com](https://platform.openai.com/api-keys). `reasoning_effort` on o-series and gpt-5. |
| **Google Gemini** | API key from [aistudio.google.com](https://aistudio.google.com/apikey). Thinking budget (2.5) or level (3). |
| **Mistral** | API key from [console.mistral.ai](https://console.mistral.ai/api-keys). |
| **OpenRouter** | API key from [openrouter.ai/keys](https://openrouter.ai/keys). Capabilities are read for each model. |
| **Ollama** | `ollama serve`, then `ollama pull qwen2.5:7b-instruct` (any model listing `tools`). |
| **LM Studio** | *Developer* tab → *Start server*. |
| **llama.cpp** | `llama-server -m model.gguf --jinja`. |
| **Other** | Any OpenAI-compatible server (vLLM, LocalAI, Jan, Groq, DeepSeek…): base URL, optional key, model name. |
| **Claude Code** | `claude` installed and logged in, **or just the Claude Desktop app** (see below). Runs in `--restricted` mode: file tools confined to the project, no shell or web tools, no MCP servers, your user settings and hooks ignored. |
| **Codex** | `codex` installed and logged in (`codex login`), **or just the ChatGPT or Codex app** (see below). Runs in its `workspace-write` sandbox with your `config.toml` ignored (no MCP servers, no extra writable folders, no network). **Codex can still read any file your user can read** and runs shell commands inside its sandbox: a Codex limitation, not something LiveQuill can restrict. |
| **Gemini CLI** | `gemini` installed. *Experimental: not tested yet.* |

### Desktop apps, no CLI needed

The **Claude Desktop** app ships with its own copy of Claude Code, and the **ChatGPT** app (and the Codex app on macOS) ships with Codex. If the CLI is not installed, LiveQuill uses the agent built into the app, with the account you are already signed in with. The setup page shows it as *“Found in the Claude Desktop app”*, the connection test checks that you are signed in, and the newest version is picked every time the app updates itself.

| App | Where LiveQuill looks |
|---|---|
| Claude Desktop | `~/.config/Claude/claude-code/<version>/…/claude` (Linux), `~/Library/Application Support/Claude/claude-code/<version>/…/claude` (macOS) |
| ChatGPT | `/usr/lib/chatgpt/resources/codex` (Linux), `/Applications/ChatGPT.app/Contents/Resources/codex` (macOS) |
| Codex app | `/Applications/Codex.app/Contents/Resources/codex` (macOS) |

An installed CLI always takes priority. The macOS locations follow the same layout as Linux but have not been tested on a real Mac yet.

> [!TIP]
> Keys already in your environment (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GEMINI_API_KEY`, `MISTRAL_API_KEY`, `OPENROUTER_API_KEY`) are detected. Only the variable name is stored, never the key.

## Security

LiveQuill lets a model touch your files, so the guard rails are on by default:

- **Local only.** Servers listen on `127.0.0.1`, reject foreign `Host` headers (DNS rebinding), and require a per-launch session token plus a same-origin request on every API call.
- **Keys stay server-side.** Stored in `~/.config/livequill/config.json` (folder `700`, file `600`), never sent to the browser, masked in error messages and logs.
- **Sandboxed tools.** No path outside the project, no `.git/`, `.env*` or `node_modules/`, and no shell commands for API and local models. Files are opened without following symbolic links at any level, so a path cannot be swapped for a link between the check and the write; the web server applies the same rule to the files it serves.
- **Backups first.** Every file is saved to `<project>/.livequill/history/` (private, `700`/`600`) before it changes. That folder ignores itself in git.
- **Undo for CLI agents.** Claude Code and Codex write files themselves, so the project is copied before each request (everything except `.git/` and `node_modules/`, `.env*` included) and compared afterwards, content included. Undo then restores modified, deleted and created files, and replaces any symbolic link an agent left in their place. If the project is larger than 2 GB, the request is refused rather than run without a reliable undo.

Found a vulnerability? Please report it privately: see [SECURITY.md](SECURITY.md).

## How it works

```mermaid
sequenceDiagram
    participant B as Floating bar
    participant S as LiveQuill (site server)
    participant M as Model (API · local · CLI)
    participant F as Project files
    B->>S: request + page context + attachments
    loop until the model answers without tools
        S->>M: messages + tools
        M-->>S: streamed text / tool calls
        S->>F: search · read · edit (sandboxed, backed up)
        S-->>B: live events (text, tools, files)
    end
    S-->>B: reload
```

<details>
<summary>Project layout</summary>

```
bin/livequill     launcher (no install needed)
livequill/
  cli.py          command line
  server.py       dashboard, per-site servers (folder or proxy), script injection, API, live reload
  agent.py        agent loop, tools, text fallback, page context, history
  sandbox.py      locked-down paths, file tools, backups and undo
  config.py       providers and sites (chmod 600), secret masking
  adapters/       one adapter per provider family
  static/         the bar (overlay.js), setup page, dashboard
site/             showcase website (GitHub Pages)
scripts/          demo recorder, JS syntax check
tests/            unit and integration tests, network mocked
```

</details>

<details>
<summary>Adding a provider</summary>

All provider-specific logic lives in one adapter. `livequill/adapters/base.py` documents the interface and the message format.

```python
from .base import Adapter, levels

class MyAdapter(Adapter):
    kind = 'my-provider'
    default_base = 'https://api.example.com'

    def list_models(self): ...                    # -> [{"id", "label", "meta"}]
    def caps(self, model, meta=None): ...         # -> {"tools", "vision", "effort", "fast"}
    def build_request(self, model, system, messages, tools, opts): ...  # pure, easy to test
    def parse_stream(self, lines): ...            # -> delta / thinking / part / stop events
```

Register it in `KINDS` (`livequill/adapters/__init__.py`), and the setup page and the bar pick it up. An OpenAI-compatible provider is a one-liner: `compatible('name', 'https://…/v1')`.

</details>

## Roadmap

### Planned

- **Dynamic sites with a mock database.** Support for sites with a backend and a database, plugged into a disposable mock database seeded with demo data, so the model can work on data-driven pages without ever touching real data. The new-site wizard already shows the option, greyed out until it lands.
- **More templates and languages.** More starting points in the new-site wizard (portfolio, event, restaurant…), and more interface languages through the language menu.

### Ideas

- **Point and ask.** Click an element on the page, like the browser inspector, to scope the request to it: “make *this* button bigger”.
- **Review before writing.** An optional mode that shows the proposed diff for each file and waits for you to accept or reject it.

Want to help with one of them? Open an issue or a pull request.

## FAQ

### Why the name LiveQuill?

A quill is the pen you write and correct a page with. Here you write the change on the page itself, and the model rewrites the source live, while you watch. It is short, not tied to any model vendor, and easy to type as a command.

### Does my code leave my machine?

Only if you choose a cloud provider. With Ollama, LM Studio or llama.cpp, everything stays on your computer. With a cloud API, your request, the page context and the files the model reads are sent to that provider, like with any coding assistant.

### Can I use it without an API key?

Yes. Install [Ollama](https://ollama.com/download), pull a model that supports tools (`ollama pull qwen2.5:7b-instruct`), and pick it on the setup page. If you already use Claude Code or Codex, LiveQuill can also work through them with your existing login.

### Does it work with React, Vue, Svelte or Next.js?

Yes. Keep your dev server running and add the site with its URL (or run `livequill --proxy http://localhost:5173 ./my-app`). LiveQuill adds the bar to its pages and passes hot-reload WebSockets through, while the model edits your components.

### What if the model breaks something?

Click **Undo** in the bar. The files changed by the last request are restored from the backup taken just before the edit, and you can undo several requests in a row. To go further back, open **Versions** (the clock button), preview the version you want, and click **Go back to this version**.

### Where is my data stored, and how do I uninstall?

Your providers, keys and sites live in `~/.config/livequill/config.json`. Each project gets a `.livequill/` folder with its backups and conversation history. To uninstall, run `curl -fsSL https://ybucaille.github.io/livequill/install.sh | sh -s -- --uninstall` (or `pipx uninstall livequill`, or delete the cloned folder). Then delete `~/.config/livequill/` and the `.livequill/` folders you no longer need.

### I only have the Claude Desktop or ChatGPT app. Does it work?

Yes. LiveQuill finds the agent built into the app and uses it with your existing sign-in, so there is no CLI to install and no API key to create. See [Desktop apps, no CLI needed](#desktop-apps-no-cli-needed).

### Which local model should I pick?

A 7B model or larger that supports tool calls, such as `qwen2.5:7b-instruct`, `qwen3` or `llama3.1`. Smaller models work for simple edits, but get lost more often in big files.

## Contributing

Contributions are welcome: adapters, themes, roadmap items, fixes and translations (the interface is in English; a new language is one JSON file, see CONTRIBUTING). Start with [CONTRIBUTING.md](CONTRIBUTING.md). Every pull request runs the full CI (tests on Linux and macOS for Python 3.10 to 3.14, lint, secret scan, CodeQL) and is reviewed by the maintainer before merging.

```bash
python3 -m unittest discover -s tests     # about 8 s, no network
node tests/e2e/browser.mjs                # real headless Chrome: edit → reload → undo → versions
```

## License

[MIT](LICENSE) © 2026 Ybucaille
