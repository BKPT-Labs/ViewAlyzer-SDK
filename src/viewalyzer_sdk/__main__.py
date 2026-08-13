"""``viewalyzer-doctor`` / ``python -m viewalyzer_sdk``: report whether the
SDK can find and talk to the ViewAlyzer executable, then run the app's own
setup health check (external tools, probes). Exits non-zero if the SDK
cannot reach the executable. Handy as the first step of a CI job."""
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
    va = ViewAlyzer(binary)
    try:
        info = va.version()
    except ViewAlyzerError as e:
        print(f"handshake failed: {e}")
        return 3
    print(f"version: {json.dumps(info)}")
    schema = info.get("schema_version")
    if schema != SCHEMA_VERSION:
        print(
            f"warning: CLI schema_version is {schema}, this SDK targets "
            f"{SCHEMA_VERSION} - response shapes may differ."
        )

    # The app's own health check: tools and probes, with hints for anything
    # missing. Advisory only; a missing optional tool is not a failure here.
    try:
        report = va.doctor()
    except ViewAlyzerError as e:
        print(f"doctor check skipped ({e.code}): {e.message}")
        return 0
    for check in report.get("checks") or []:
        status = check.get("status", "?")
        line = f"  [{status:>7}] {check.get('name', check.get('id', '?'))}"
        if check.get("version"):
            line += f" {check['version']}"
        if check.get("path"):
            line += f" ({check['path']})"
        print(line)
        if status != "ok" and check.get("hint"):
            print(f"            hint: {check['hint']}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
