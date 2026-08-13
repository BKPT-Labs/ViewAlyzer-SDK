# Releasing viewalyzer-sdk to PyPI

One-time setup and the per-release routine. All commands run from
`ViewAlyzer-App/python/`.

## 0. One-time: accounts and the name

1. Create an account on https://pypi.org (and https://test.pypi.org for dry
   runs). Enable 2FA (required for publishing).
2. The name `viewalyzer-sdk` must be unclaimed at first upload; the first
   successful upload claims it permanently. Check
   https://pypi.org/project/viewalyzer-sdk/ before the first release.

## 1. Per release

```bash
# bump src/viewalyzer_sdk/__init__.py __version__
# (pyproject.toml reads it dynamically; there is only one place to bump)

python -m pip install --upgrade build twine
python -m pytest                      # green suite or no release
python -m build                       # -> dist/*.tar.gz + dist/*.whl
python -m twine check dist/*          # metadata sanity
```

Dry-run against TestPyPI first:

```bash
python -m twine upload --repository testpypi dist/*
pip install --index-url https://test.pypi.org/simple/ viewalyzer-sdk
```

Then the real thing:

```bash
python -m twine upload dist/*
```

`twine` asks for an API token (create one under PyPI -> Account settings ->
API tokens; scope it to this project after the first upload). Use
`__token__` as the username and the token as the password.

Tag the release in git: `git tag py-v1.0.0 && git push --tags`.

## 2. Recommended instead of tokens: Trusted Publishing (GitHub Actions)

PyPI can trust a specific GitHub workflow (OIDC) so no long-lived token
exists at all. On PyPI: project -> Settings -> Publishing -> add the repo,
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
- Never re-upload a version; PyPI forbids it. Botched release -> bump the
  patch number.

## 4. Naming note

This package was renamed from `viewalyzer-cli` to `viewalyzer-sdk`
(2026-08, before any PyPI release; nothing to migrate). Future products
get sibling packages rather than growing this one; a `bkpt-sdk` umbrella
package that depends on the family can come later. The UDP sender library
(`ViewAlyzer-Examples/Desktop-Python-UDP/`) also calls itself `viewalyzer`
in its pyproject but is not on PyPI yet; settle its name (e.g.
`viewalyzer-protocol`) before publishing it, because the first upload of
each name wins it.
