# Contributing to LiveQuill

Thanks for taking the time to contribute! Bug reports, new provider adapters, themes and translations are all welcome.

## Ground rules

- **Zero runtime dependencies.** LiveQuill only uses the Python standard library (3.9+), plus plain JavaScript and CSS in the browser. Please do not add packages.
- **Security first.** API keys must never reach the browser or the logs. File access must go through `Project.resolve()`. New `/__lq/` routes must check the session token and the origin.
- **No real network calls in tests.** Use `tests/helpers.FakeTransport` to replay provider responses.
- Match the surrounding style: small functions, short comments that explain *why*, no reformatting of unrelated code.

## Getting started

```bash
git clone https://github.com/Ybucaille/livequill.git
cd livequill
python3 -m unittest discover -s tests
./bin/livequill demo
```

## Common contributions

### A new provider

1. Write an adapter in `livequill/adapters/`. Implement `list_models`, `caps`, `build_request` and `parse_stream` (see `base.py`). If the provider speaks the OpenAI Chat Completions API, one `compatible('name', 'https://…/v1')` line in `adapters/openai.py` is enough.
2. Register it in `KINDS` (`livequill/adapters/__init__.py`) with a label, a group, form fields and the URL of its key or download page.
3. Add tests to `tests/test_adapters.py`: one for request conversion (messages, images, tool calls, tool results) and one for stream parsing.

### A new theme

Add an entry to `THEMES` in `livequill/static/overlay.js`. Each entry has a `shape` (font, radius, sizes) and a `dark` and a `light` color set. It shows up in the palette menu automatically.

## Pull requests

1. Fork the repository and create a branch from `main` (`feat/…`, `fix/…`, `docs/…`).
2. Use [Conventional Commits](https://www.conventionalcommits.org/) messages: `feat(adapters): add Groq`, `fix(server): …`.
3. Run `python3 -m unittest discover -s tests` and check the demo still works (`./bin/livequill demo`).
4. Explain what changed and why in the pull request. Add a screenshot or a short recording for UI changes.

## Reporting bugs

Open an issue with:

- your OS and Python version;
- the provider and model;
- the mode (static or `--proxy`);
- what you expected and what happened.

Run with `LIVEQUILL_DEBUG=1` to print tracebacks, and **remove any API key** from what you paste.
