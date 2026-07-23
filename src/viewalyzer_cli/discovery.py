"""Locate the ViewAlyzer executable on this machine.

Search order (first hit wins):

1. The ``VIEWALYZER`` environment variable - an explicit path to the
   executable. If set but pointing at a missing file this *raises* rather
   than silently falling through: an explicit setting that is wrong should
   be loud, not shadowed by whatever else is on the machine.
2. ``PATH`` - both ``ViewAlyzer`` and ``viewalyzer`` are tried (Linux
   installs symlink the lowercase name; Windows resolution appends ``.exe``
   automatically).
3. Well-known install locations per OS:
   - Windows: ``%ProgramFiles%\\ViewAlyzer\\ViewAlyzer.exe`` and the
     per-user ``%LOCALAPPDATA%\\Programs\\ViewAlyzer\\ViewAlyzer.exe``.
   - macOS: ``/Applications/ViewAlyzer.app`` and ``~/Applications/...``.
   - Linux: ``/usr/local/bin/viewalyzer``, ``~/.local/bin/viewalyzer``,
     and an AppImage under ``/opt/ViewAlyzer``.
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from typing import Iterator, Mapping, Optional, Tuple

from .errors import BinaryNotFound

#: Environment variable naming an explicit path to the ViewAlyzer executable.
ENV_VAR = "VIEWALYZER"


def find_viewalyzer(
    env: Optional[Mapping[str, str]] = None,
) -> Optional[Path]:
    """Return the path to the ViewAlyzer executable, or ``None`` if not found.

    Raises :class:`BinaryNotFound` only when :data:`ENV_VAR` is set but does
    not point at an existing file (a misconfiguration worth surfacing).
    """
    path, _source = find_viewalyzer_with_source(env)
    return path


def find_viewalyzer_with_source(
    env: Optional[Mapping[str, str]] = None,
) -> Tuple[Optional[Path], str]:
    """Like :func:`find_viewalyzer` but also reports where the hit came from:
    one of ``"env"``, ``"path"``, ``"install"``, or ``"not found"``."""
    env = os.environ if env is None else env

    explicit = env.get(ENV_VAR, "").strip()
    if explicit:
        p = Path(explicit)
        if p.is_file():
            return p, "env"
        raise BinaryNotFound(
            f"{ENV_VAR} is set to {explicit!r} but no file exists there. "
            f"Fix or unset the {ENV_VAR} environment variable."
        )

    for name in ("ViewAlyzer", "viewalyzer"):
        hit = shutil.which(name, path=env.get("PATH"))
        if hit:
            return Path(hit), "path"

    for candidate in _well_known_locations(env):
        if candidate.is_file():
            return candidate, "install"

    return None, "not found"


def _well_known_locations(env: Mapping[str, str]) -> Iterator[Path]:
    if sys.platform == "win32":
        roots = [
            env.get("ProgramFiles"),
            env.get("ProgramFiles(x86)"),
        ]
        local = env.get("LOCALAPPDATA")
        if local:
            roots.append(str(Path(local) / "Programs"))
        for root in roots:
            if root:
                yield Path(root) / "ViewAlyzer" / "ViewAlyzer.exe"
    elif sys.platform == "darwin":
        app_rel = Path("ViewAlyzer.app") / "Contents" / "MacOS" / "ViewAlyzer"
        yield Path("/Applications") / app_rel
        yield Path.home() / "Applications" / app_rel
    else:
        yield Path("/usr/local/bin/viewalyzer")
        yield Path.home() / ".local" / "bin" / "viewalyzer"
        opt = Path("/opt/ViewAlyzer")
        yield opt / "ViewAlyzer"
        if opt.is_dir():
            try:
                yield from sorted(opt.glob("ViewAlyzer*.AppImage"))
            except OSError:
                pass
