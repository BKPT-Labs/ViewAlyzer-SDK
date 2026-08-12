# Releasing viewalyzer-cli to PyPI

One-time setup and the per-release routine. All commands run from
`ViewAlyzer-App/python/`.

## 0. One-time: accounts

1. Create an account on https://pypi.org (and https://test.pypi.org for dry
   runs). Enable 2FA — required for publishing.
2. The name `viewalyzer-cli` was unclaimed as of 2026-07; the first
   successful upload claims it permanently.

## 1. Per release

```bash
# bump the version in BOTH places (keep them equal):
#   pyproject.toml        -> [project] version
#   src/viewalyzer_cli/__init__.py -> __version__

python -m pip install --upgrade build twine
python -m pytest                      # green suite or no release
python -m build                       # -> dist/*.tar.gz + dist/*.whl
python -m twine check dist/*          # metadata sanity
```

Dry-run against TestPyPI first:

```bash
python -m twine upload --repository testpypi dist/*
pip install --index-url https://test.pypi.org/simple/ viewalyzer-cli
```

Then the real thing:

```bash
python -m twine upload dist/*
```

`twine` asks for an API token (create one under PyPI → Account settings →
API tokens; scope it to this project after the first upload). Use
`__token__` as the username and the token as the password.

Tag the release in git: `git tag py-v0.1.0 && git push --tags`.

## 2. Recommended instead of tokens: Trusted Publishing (GitHub Actions)

PyPI can trust a specific GitHub workflow (OIDC) so no long-lived token
exists at all. On PyPI: project → Settings → Publishing → add the repo,
workflow file name, and environment. Then:

```yaml
# .github/workflows/publish-python.yml
name: publish-python
on:
  push:
    tags: ["py-v*"]
jobs:
  publish:
    runs-on: ubuntu-latest
    environment: pypi
    permissions:
      id-token: write        # OIDC for trusted publishing
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.12" }
      - run: python -m pip install build
      - run: python -m build
        working-directory: python
      - uses: pypa/gh-action-pypi-publish@release/v1
        with:
          packages-dir: python/dist
```

## 3. Versioning policy

- The package version is independent of the app version, but the CLI
  wire-protocol it targets is pinned by `SCHEMA_VERSION` in `client.py`.
  If the app bumps its agent `schema_version`, ship a minor/major bump here
  that handles (or at least detects) both.
- Never re-upload a version; PyPI forbids it. Botched release → bump the
  patch number.

## 4. Naming note

The UDP sender library (`ViewAlyzer/Example-Projects/Desktop-Python-UDP/`) also calls
itself `viewalyzer` in its pyproject but is not on PyPI yet. Decide the
family naming before publishing either package — e.g. this one as
`viewalyzer-cli` and the sender as `viewalyzer-protocol` — because the first
upload of each name wins it.
