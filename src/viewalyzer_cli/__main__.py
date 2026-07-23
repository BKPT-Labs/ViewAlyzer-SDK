"""``viewalyzer-doctor`` / ``python -m viewalyzer_cli`` - report whether the
bindings can find and talk to the ViewAlyzer executable, and exit non-zero
if they can't. Handy as the first step of a CI job."""
from __future__ import annotations

import json
import sys

from .client import SCHEMA_VERSION, ViewAlyzer
from .discovery import ENV_VAR, find_viewalyzer_with_source
from .errors import ViewAlyzerError


def main() -> int:
    try:
        binary, source = find_viewalyzer_with_source()
    except ViewAlyzerError as e:
        print(f"error: {e.message}")
        return 2
    if binary is None:
        print("ViewAlyzer executable: not found")
        print(
            "Searched: the %s environment variable, PATH, and the standard "
            "install locations for this OS." % ENV_VAR
        )
        print("Install ViewAlyzer, or set %s to the executable's path." % ENV_VAR)
        return 2
    print(f"ViewAlyzer executable: {binary} (via {source})")
    try:
        info = ViewAlyzer(binary).version()
    except ViewAlyzerError as e:
        print(f"handshake failed: {e}")
        return 3
    print(f"version: {json.dumps(info)}")
    schema = info.get("schema_version")
    if schema != SCHEMA_VERSION:
        print(
            f"warning: CLI schema_version is {schema}, these bindings target "
            f"{SCHEMA_VERSION} - response shapes may differ."
        )
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
