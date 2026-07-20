# Releasing agent-trading-guardrails (free)

This package is MIT-licensed — free to use forever. This doc is how to *publish*
it so people can install it. Everything here is free.

## TL;DR

1. Push this code to a **public** GitHub repo.
2. Create a free account on [PyPI](https://pypi.org) (and
   [TestPyPI](https://test.pypi.org) to rehearse).
3. Set up **Trusted Publishing** (below) — then cutting a GitHub Release
   auto-publishes to PyPI with no tokens to manage.

Once on PyPI: `pip install agent-trading-guardrails`.

## One-time: PyPI Trusted Publishing (recommended, tokenless)

Trusted Publishing lets GitHub Actions upload to PyPI over OIDC — no API tokens,
no secrets in the repo.

1. Log in to PyPI → **Your projects** → **Publishing** →
   **Add a pending publisher**. Enter:
   - PyPI Project Name: `agent-trading-guardrails`
   - Owner: `jmeitz-claw`
   - Repository name: `agent-trading-guardrails`
   - Workflow name: `publish.yml`
   - Environment name: `pypi`
2. In the GitHub repo → **Settings → Environments → New environment** →
   name it `pypi`. (Optionally add a required reviewer so releases are gated.)

That's the whole setup. The included `.github/workflows/publish.yml` does the rest.

## Cutting a release

```bash
# 1. bump the version in pyproject.toml and guardrails/__init__.py (__version__)
# 2. tag and push
git tag v0.1.0
git push origin v0.1.0
# 3. on GitHub: Releases → Draft a new release → pick tag v0.1.0 → Publish
```

Publishing the GitHub Release triggers `publish.yml`, which builds the wheel +
sdist and uploads them to PyPI via Trusted Publishing. Done.

## Manual fallback (API token)

If you'd rather not use Actions:

```bash
python -m pip install build twine
python -m build                      # -> dist/*.whl and *.tar.gz
python -m twine upload dist/*        # paste a PyPI API token when prompted
```

Rehearse against TestPyPI first:

```bash
python -m twine upload --repository testpypi dist/*
pip install --index-url https://test.pypi.org/simple/ agent-trading-guardrails
```

## Versioning

Semantic versioning. `0.x` = pre-1.0, API may change. Bump the version in **two**
places and keep them in sync: `pyproject.toml` `version` and
`guardrails/__init__.py` `__version__`.
