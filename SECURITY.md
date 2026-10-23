# Security policy

LiveQuill runs a local server that lets a language model edit files on your machine, so security reports are taken seriously.

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
