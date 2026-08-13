# Releasing viewalyzer-sdk to PyPI

Releases publish automatically from GitHub Actions via PyPI Trusted
Publishing (OIDC, no stored tokens). All commands run from the repo root.

## 0. One-time setup

1. On PyPI (with 2FA enabled): Account -> Publishing -> add a pending
   trusted publisher with EXACTLY these values:
   - PyPI Project Name: `viewalyzer-sdk`
   - Owner: `BKPT-Labs`
   - Repository name: `ViewAlyzer-SDK`
   - Workflow name: `publish.yml`
   - Environment name: `pypi`
2. On GitHub: repo Settings -> Environments -> create an environment named
   `pypi`. Optionally add required reviewers to gate releases.
3. Note: a pending publisher does not reserve the name; the first
   successful upload creates and claims the `viewalyzer-sdk` project.

## 1. Per release

```bash
# bump __version__ in src/viewalyzer_sdk/__init__.py
# (pyproject.toml reads it dynamically; one place to bump)

python -m pytest        # green suite or no release
git commit -am "release: v1.0.1"
git tag v1.0.1
git push origin main v1.0.1
```

The tag push triggers `.github/workflows/publish.yml`: it runs the tests,
builds the sdist and wheel, and publishes to PyPI through the trusted
publisher. Watch the run under the repo's Actions tab.

## 2. Manual fallback (no CI)

```bash
python -m pip install --upgrade build twine
python -m pytest
python -m build
python -m twine check dist/*
python -m twine upload --repository testpypi dist/*   # dry run
python -m twine upload dist/*                         # real
```

`twine` asks for an API token (username `__token__`); prefer the CI path
so no long-lived token exists.

## 3. Versioning policy

- The package version is independent of the app version, but the CLI
  wire-protocol it targets is pinned by `SCHEMA_VERSION` in `client.py`.
  If the app bumps its agent `schema_version`, ship a minor/major bump
  here that handles (or at least detects) both.
- Never re-upload a version; PyPI forbids it. Botched release -> bump the
  patch number.

## 4. Naming note

This package was renamed from `viewalyzer-cli` to `viewalyzer-sdk` and
moved out of `ViewAlyzer-App/python/` into this repository (2026-08,
before any PyPI release). Future products get sibling packages rather
than growing this one; a `bkpt-sdk` umbrella package that depends on the
family can come later. The UDP sender library
(`ViewAlyzer-Examples/Desktop-Python-UDP/`) also calls itself
`viewalyzer` in its pyproject but is not on PyPI yet; settle its name
(e.g. `viewalyzer-protocol`) before publishing it, because the first
upload of each name wins it.
