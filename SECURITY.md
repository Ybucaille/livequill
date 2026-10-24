# Security policy

LiveQuill runs a local server that lets a language model edit files on your machine, so security reports are taken seriously.

## How LiveQuill protects you

- **Local only.** Servers listen on `127.0.0.1`, reject foreign `Host` headers (DNS rebinding), and require a per-launch session token plus a same-origin request on every API call.
- **Keys stay server-side.** They are stored in `~/.config/livequill/config.json` (folder `700`, file `600`), never sent to the browser, and masked in error messages and logs. A key taken from an environment variable (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`…) is not stored at all: only the variable name is.
- **Sandboxed tools.** API and local models only get file tools: no path outside the project, no `.git/`, `.env*` or `node_modules/`, and no shell commands. Files are opened without following symbolic links at any level, so a path cannot be swapped for a link between the check and the write. The web server applies the same rule to the files it serves.
- **Backups first.** Every file is saved to `<project>/.livequill/history/` (private, `700`/`600`) before it changes. That folder ignores itself in git.
- **Undo for CLI agents.** Claude Code and Codex write files themselves, so the project is copied before each request (everything except `.git/` and `node_modules/`, `.env*` included) and compared afterwards, content included. Undo then restores modified, deleted and created files, and replaces any symbolic link an agent left in their place. If the project is larger than 2 GB, the request is refused rather than run without a reliable undo.
- **No script in previews.** Site thumbnails and template previews in the dashboard run no script (iframe sandbox and `script-src 'none'`), so a site's code can never read the dashboard's session token.

### Coding agents

| Agent | How it is confined |
|---|---|
| **Claude Code** | Runs with `--restricted`: file tools confined to the project, no shell or web tools, no MCP servers, your user settings and hooks ignored. |
| **Codex** | Runs in its `workspace-write` sandbox with your `config.toml` ignored: no MCP servers, no extra writable folders, no network. **Codex can still read any file your user can read** and runs shell commands inside its sandbox. This is a Codex limitation that LiveQuill cannot restrict. |
| **Gemini CLI** | Experimental, not tested yet. |

## Reporting a vulnerability

Please **do not open a public issue**. Report it privately through GitHub instead: [Report a vulnerability](https://github.com/Ybucaille/livequill/security/advisories/new).

Please include:

- what an attacker can do, and under which conditions (local user, malicious web page, malicious model output…);
- steps or a proof of concept;
- the version or commit you tested.

You will get an answer within 7 days. Once a fix is released, you will be credited in the advisory, unless you prefer otherwise.

## Scope

In scope, for example:

- an API key reaching the browser, the logs or an error message;
- a file tool reading or writing outside the project folder, or into `.git/`, `.env*` or `node_modules/`;
- another website or origin calling the local `/__lq/` API (CSRF, DNS rebinding, missing token check);
- a model running shell commands through LiveQuill's own tools.

Out of scope: what an external CLI agent (Claude Code, Codex, Gemini CLI) does with its own permissions, and model answers that are wrong but stay inside the project.

## Supported versions

Only the latest release on `main` receives security fixes.
