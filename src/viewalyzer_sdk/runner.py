"""Low-level subprocess transport for the ViewAlyzer headless CLI.

This is the only module that knows about argv, stdio, exit codes, and the
JSON envelope. Everything above it works with parsed dicts or a raised
:class:`~viewalyzer_sdk.errors.ViewAlyzerError`.

Contract (see ``docs/CLI.md`` in the ViewAlyzer repository):

- every invocation is ``<binary> --headless <mode flags...>``: one process,
  one job, one payload, exit. No daemon, no session state.
- query/list modes print exactly one JSON object on stdout. On failure they
  print an *error envelope* (``{"error": <code>, ...}``), usually with a
  non-zero exit code. The envelope, not the exit code, is the failure
  signal.
- capture-style modes that still answer JSON (``poll``, ``snapshot``) print
  human-readable progress lines first and the envelope as the LAST line;
  :meth:`Runner.run_json` takes that last JSON line when the whole stdout
  is not one object.
- plain captures print progress lines and the two ``[headless]`` contract
  lines instead; those are handled by the client, not here.
"""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Sequence, Union

from .errors import ViewAlyzerError, raise_for_envelope

#: Default timeout for query/list calls (captures compute their own from the
#: requested duration).
DEFAULT_QUERY_TIMEOUT_S = 30.0
#: Headroom added on top of a capture's duration before giving up on it.
RECORD_TIMEOUT_PAD_S = 15.0

#: What callers may pass as "the binary": a path, or a full argv prefix
#: (the latter lets tests and wrappers interpose, e.g.
#: ``[sys.executable, "fake_cli.py"]``).
BinarySpec = Union[str, Path, Sequence[str]]


@dataclass(frozen=True)
class RunResult:
    exit_code: int
    stdout: str
    stderr: str


class Runner:
    """Stateless executor for one resolved ViewAlyzer binary."""

    def __init__(self, binary: BinarySpec) -> None:
        if isinstance(binary, (str, Path)):
            self._argv0 = [str(binary)]
        else:
            self._argv0 = [str(part) for part in binary]
            if not self._argv0:
                raise ViewAlyzerError("bad_arguments", "empty binary argv")

    @property
    def binary(self) -> str:
        """Display path of the executable (first argv element)."""
        return self._argv0[0]

    def run(self, args: Sequence[Any], timeout_s: float) -> RunResult:
        """Run ``--headless <args>`` and return the raw streams. Raises only
        for process-level failures (missing binary, timeout)."""
        argv = [*self._argv0, "--headless", *[str(a) for a in args]]
        try:
            proc = subprocess.run(
                argv,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_s,
                stdin=subprocess.DEVNULL,
                check=False,
            )
        except FileNotFoundError as e:
            raise ViewAlyzerError(
                "binary_missing",
                f"ViewAlyzer executable not found at {self._argv0[0]}. "
                "Install ViewAlyzer, or point the VIEWALYZER environment "
                "variable at the executable.",
            ) from e
        except subprocess.TimeoutExpired as e:
            raise ViewAlyzerError(
                "timeout",
                f"ViewAlyzer call timed out after {timeout_s:.0f}s "
                f"({' '.join(argv[len(self._argv0):][:4])} ...)",
            ) from e
        return RunResult(proc.returncode, proc.stdout or "", proc.stderr or "")

    def popen(self, args: Sequence[Any]) -> "subprocess.Popen[str]":
        """Start ``--headless <args>`` with piped stdio and return the live
        process. For streaming captures, where the caller consumes stderr
        line-by-line while the CLI runs; the caller owns draining BOTH
        pipes (an undrained one fills and stalls the CLI) and reaping."""
        argv = [*self._argv0, "--headless", *[str(a) for a in args]]
        try:
            return subprocess.Popen(
                argv,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                stdin=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
            )
        except FileNotFoundError as e:
            raise ViewAlyzerError(
                "binary_missing",
                f"ViewAlyzer executable not found at {self._argv0[0]}. "
                "Install ViewAlyzer, or point the VIEWALYZER environment "
                "variable at the executable.",
            ) from e

    def run_json(self, args: Sequence[Any], timeout_s: float) -> Dict[str, Any]:
        """Run a JSON-mode invocation and return the parsed payload.
        Raises :class:`ViewAlyzerError` for error envelopes and malformed
        output."""
        r = self.run(args, timeout_s)
        text = r.stdout.strip()
        if not text:
            raise ViewAlyzerError(
                "bad_output",
                f"empty stdout (exit {r.exit_code}); "
                f"stderr: {r.stderr.strip()[:400]}",
            )
        payload = parse_payload(text)
        if payload is None:
            raise ViewAlyzerError(
                "bad_output",
                f"non-JSON stdout (exit {r.exit_code}): no JSON object line "
                f"found. head: {text[:200]}",
            )
        if not isinstance(payload, dict):
            raise ViewAlyzerError(
                "bad_output",
                f"expected a JSON object, got {type(payload).__name__}",
            )
        return raise_for_envelope(payload)


def parse_payload(text: str) -> Any:
    """The JSON payload in a CLI's stdout: the whole text when it is one
    JSON value (query/list verbs), else the LAST line that parses as a JSON
    object (``poll`` / ``snapshot`` print progress lines before their
    envelope; an error envelope follows a ``[headless] ERROR:`` line).
    None when no line parses."""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    for line in reversed(text.splitlines()):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            return json.loads(line)
        except json.JSONDecodeError:
            continue
    return None
