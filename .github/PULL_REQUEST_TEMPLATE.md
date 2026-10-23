## What and why

<!-- What does this PR change, and why? Link the issue it closes: "Closes #123". -->

## How it was tested

<!-- Commands you ran, provider/model you tried, screenshots or a short recording for UI changes. -->

## Checklist

- [ ] `python3 -m unittest discover -s tests` passes
- [ ] New behavior is covered by tests (network calls mocked with `FakeTransport`)
- [ ] No new runtime dependency (standard library only)
- [ ] No API key, token or personal path in the code, tests, logs or screenshots
- [ ] File access goes through `Project.resolve()`, new `/__lq/` routes check the token and the origin
- [ ] README or CONTRIBUTING updated if the usage changed

> Every pull request is reviewed and merged by the maintainer. CI must be green before review.
