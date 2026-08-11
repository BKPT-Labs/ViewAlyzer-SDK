"""High-level client for the ViewAlyzer headless CLI.

    from viewalyzer_cli import ViewAlyzer

    va = ViewAlyzer()                      # finds the installed app
    rec = va.record("board.vacf", output="run1.vadb", duration_s=10)
    assert rec.total_events > 0
    assert rec.inversions()["inversions"] == []

Every method is one CLI invocation (one process, one JSON payload); there is
no daemon and no session state, which is exactly what makes this safe to run
from pytest and CI.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Union

from .discovery import find_viewalyzer
from .errors import BinaryNotFound, ViewAlyzerError
from .recording import Recording
from .runner import (
    DEFAULT_QUERY_TIMEOUT_S,
    RECORD_TIMEOUT_PAD_S,
    BinarySpec,
    Runner,
)

#: The agent wire-protocol version these bindings were written against.
#: Check the CLI's own value once via :meth:`ViewAlyzer.version`.
SCHEMA_VERSION = 1

#: Query verbs that accept ``--tier``.
TIERED_VERBS = ("timeline", "events", "user-traces")
#: Verbs without tiers (full reports / SQL).
UNTIERED_VERBS = (
    "inversions",
    "slices",
    "slice-details",
    "events-all",
    "user-traces-all",
    "sql",
)
QUERY_VERBS = (*TIERED_VERBS, *UNTIERED_VERBS)

# Stable stdout contracts of capture mode (see the CLI Integration Guide):
#   [headless] Recording saved: /abs/path/run1.vadb (5576 KB)
#   [headless] Recording registered: id=f76593b93473
_RECORDING_SAVED_RE = re.compile(r"Recording saved:\s*(.+?)\s*\(\d+\s*KB\)")
_RECORDING_ID_RE = re.compile(r"Recording registered:\s*id=([0-9a-f]{12})")

PathLike = Union[str, Path]
#: A connection config: a ``.vacf`` path, or an inline dict whose keys
#: are the CLI flag names without the leading ``--``.
ConfigSpec = Union[PathLike, Mapping[str, Any]]


class ViewAlyzer:
    """Client bound to one ViewAlyzer executable.

    Args:
        binary: path to the executable, or a full argv prefix (list) for
            wrappers. Omit to auto-discover: ``VIEWALYZER`` env var, then
            ``PATH``, then the OS's standard install locations.
        query_timeout_s: timeout for query/list calls. Captures compute
            their own timeout from the requested duration.
    """

    def __init__(
        self,
        binary: Optional[BinarySpec] = None,
        *,
        query_timeout_s: float = DEFAULT_QUERY_TIMEOUT_S,
    ) -> None:
        if binary is None:
            found = find_viewalyzer()
            if found is None:
                raise BinaryNotFound(
                    "ViewAlyzer executable not found. Install the ViewAlyzer "
                    "app, add it to PATH, or set the VIEWALYZER environment "
                    "variable to the executable's path."
                )
            binary = found
        self._runner = Runner(binary)
        self._query_timeout_s = query_timeout_s

    @property
    def binary(self) -> str:
        """Path of the executable this client drives."""
        return self._runner.binary

    # ----- handshake / utilities ------------------------------------------

    def version(self) -> Dict[str, Any]:
        """``{"schema_version": 1, "app": "ViewAlyzer", "version": "1.2.0"}``.
        Call once at startup; if ``schema_version`` differs from
        :data:`SCHEMA_VERSION`, response shapes may not match these
        bindings."""
        return self._runner.run_json(["--version"], self._query_timeout_s)

    def get_license(self) -> Dict[str, Any]:
        """Activation state and effective policy caps (e.g. the maximum
        recording duration)."""
        return self._runner.run_json(["--get-license"], self._query_timeout_s)

    def analyze_memory(
        self, elf: PathLike, *, map_file: Optional[PathLike] = None
    ) -> Dict[str, Any]:
        """Static flash/RAM breakdown of a firmware image."""
        args: List[Any] = ["--analyze-memory", "--elf", _existing(elf, "ELF")]
        if map_file is not None:
            args += ["--map", _existing(map_file, "map file")]
        return self._runner.run_json(args, self._query_timeout_s)

    def list_symbols(
        self, elf: PathLike, *, filter: Optional[str] = None
    ) -> Dict[str, Any]:
        """Pollable symbols in a firmware ELF (name, address, size, type)."""
        args: List[Any] = ["--list-symbols", "--elf", _existing(elf, "ELF")]
        if filter:
            args += ["--filter", filter]
        return self._runner.run_json(args, self._query_timeout_s)

    def list_probes(
        self,
        *,
        jlink_path: Optional[PathLike] = None,
        stlink_path: Optional[PathLike] = None,
        cube_programmer_path: Optional[PathLike] = None,
    ) -> Dict[str, Any]:
        """Connected debug probes with serial numbers:
        ``{"probes": [{"type": "jlink"|"stlink", "serial", "description"}],
        "warnings": [...]?}``. Pass a serial to a capture via the
        ``jlink-serial`` / ``stlink-serial`` config keys when more than one
        probe is attached (with a single probe the servers auto-select).
        Tool paths are optional overrides for the probe enumerators."""
        args: List[Any] = ["--list-probes"]
        if jlink_path is not None:
            args += ["--jlink", str(jlink_path)]
        if stlink_path is not None:
            args += ["--stlink", str(stlink_path)]
        if cube_programmer_path is not None:
            args += ["--cube-programmer", str(cube_programmer_path)]
        return self._runner.run_json(args, self._query_timeout_s)

    # ----- the recording index --------------------------------------------

    def list_recordings(self) -> Dict[str, Any]:
        """The raw recording-index payload (``{"recordings": [...]}``)."""
        return self._runner.run_json(["--list-recordings"], self._query_timeout_s)

    def recordings(self) -> List[Recording]:
        """The recording index as bound :class:`Recording` handles, newest
        entries as the CLI orders them."""
        result = []
        for entry in self.list_recordings().get("recordings") or []:
            result.append(
                Recording(
                    self,
                    recording_id=entry.get("recording_id"),
                    path=entry.get("path"),
                    info=entry,
                )
            )
        return result

    def open(self, recording: Union[PathLike, str]) -> Recording:
        """A handle on an existing recording: a ``.vadb`` path (enables
        direct SQLite reads) or a 12-hex ``recording_id``."""
        text = str(recording)
        if re.fullmatch(r"[0-9a-f]{12}", text):
            return Recording(self, recording_id=text)
        return Recording(self, path=_existing(recording, "recording"))

    def delete_recording(self, recording: Union[Recording, str]) -> None:
        """**Destructive.** Removes the index entry *and deletes the .vadb
        file on disk*."""
        ref = recording.ref if isinstance(recording, Recording) else str(recording)
        self._run_ok(["--delete-recording", ref])

    def delete_all_recordings(self) -> None:
        """**Destructive.** Clears the index and deletes every indexed
        recording file."""
        self._run_ok(["--delete-all-recordings"])

    # ----- capture --------------------------------------------------------

    def record(
        self,
        config: ConfigSpec,
        *,
        output: PathLike,
        duration_s: float,
        extra_flags: Sequence[str] = (),
        timeout_s: Optional[float] = None,
    ) -> Recording:
        """Record the target's trace stream for *duration_s* seconds.

        *config* is a ``.vacf`` path or an inline dict with the same
        keys (``transport``, ``target-device``, ``cpu-clock-hz``, tool
        paths, ...). *extra_flags* are appended verbatim for flags without a
        dedicated parameter (e.g. ``["--log", path]`` or port overrides);
        CLI flags win over config-file values. Returns a :class:`Recording`
        whose ``path`` is the authoritative on-disk file - the CLI forces
        the ``.vadb`` extension, so it may differ from *output*.
        """
        timeout = timeout_s if timeout_s is not None else duration_s + RECORD_TIMEOUT_PAD_S
        with _config_path(config) as config_path:
            r = self._runner.run(
                [
                    "--config",
                    config_path,
                    "--output",
                    str(output),
                    "--duration",
                    duration_s,
                    *extra_flags,
                ],
                timeout,
            )
        if r.exit_code != 0:
            raise ViewAlyzerError("record_failed", _capture_failure_detail(r))
        combined = f"{r.stdout}\n{r.stderr}"
        saved = _RECORDING_SAVED_RE.search(combined)
        actual = Path(saved.group(1)) if saved else Path(output)
        if not actual.is_file() or actual.stat().st_size == 0:
            raise ViewAlyzerError(
                "record_failed",
                "capture reported success but the recording file is missing "
                f"or empty: {actual}",
            )
        registered = _RECORDING_ID_RE.search(combined)
        return Recording(
            self,
            recording_id=registered.group(1) if registered else None,
            path=actual,
            info={"duration_s": duration_s},
        )

    def record_polls(
        self,
        elf: PathLike,
        symbols: Sequence[str],
        *,
        duration_s: float,
        poll_hz: int = 100,
        config: Optional[ConfigSpec] = None,
        target_device: Optional[str] = None,
        extra_flags: Sequence[str] = (),
        timeout_s: Optional[float] = None,
    ) -> Recording:
        """Sample target variables over the debug probe - no firmware
        instrumentation required. Needs a probe transport (not udp/serial).

        Pass *config* to reuse a connection file, or *target_device* (plus
        any tool-path flags via *extra_flags*, e.g. ``["--arm-gdb", path]``)
        for config-less mode. Returns a :class:`Recording` with the poll
        summary in ``info["summary"]``.
        """
        if not symbols:
            raise ViewAlyzerError("bad_arguments", "no symbols to poll")
        args: List[Any] = [
            "--record-polls",
            "--elf",
            _existing(elf, "ELF"),
            "--symbols",
            ",".join(symbols),
            "--duration-s",
            duration_s,
            "--poll-hz",
            poll_hz,
        ]
        if target_device:
            args += ["--target-device", target_device]
        args += list(extra_flags)
        # Probe handshake + finalization need headroom beyond the poll time.
        timeout = (
            timeout_s
            if timeout_s is not None
            else duration_s + RECORD_TIMEOUT_PAD_S + 15.0
        )
        if config is not None:
            with _config_path(config) as config_path:
                payload = self._runner.run_json(
                    [*args, "--config", config_path], timeout
                )
        else:
            payload = self._runner.run_json(args, timeout)
        return Recording(
            self,
            recording_id=payload.get("recording_id"),
            path=payload.get("path"),
            info=payload,
        )

    # ----- queries --------------------------------------------------------

    def query(
        self,
        verb: str,
        recording: Union[Recording, str, Path],
        *,
        tier: Optional[str] = None,
        budget: Optional[str] = None,
        t_start_us: Optional[int] = None,
        t_end_us: Optional[int] = None,
        bucket_us: Optional[int] = None,
        max_slices: Optional[int] = None,
        sql: Optional[str] = None,
        timeout_s: Optional[float] = None,
    ) -> Dict[str, Any]:
        """One ``--query`` call. Prefer the named helpers on
        :class:`Recording`; this generic form exists for verbs and flags the
        helpers don't cover (*max_slices* applies to ``slice-details``)."""
        if verb not in QUERY_VERBS:
            raise ViewAlyzerError(
                "bad_arguments",
                f"unknown query verb {verb!r}; expected one of {QUERY_VERBS}",
            )
        ref = recording.ref if isinstance(recording, Recording) else str(recording)
        args: List[Any] = ["--query", verb, "--recording", ref]
        if tier is not None:
            args += ["--tier", tier]
        if budget is not None:
            args += ["--budget", budget]
        if t_start_us is not None:
            args += ["--t-start-us", t_start_us]
        if t_end_us is not None:
            args += ["--t-end-us", t_end_us]
        if bucket_us is not None:
            args += ["--bucket-us", bucket_us]
        if max_slices is not None:
            args += ["--max-slices", max_slices]
        if sql is not None:
            args += ["--sql", sql]
        return self._runner.run_json(
            args, timeout_s if timeout_s is not None else self._query_timeout_s
        )

    # ----- private --------------------------------------------------------

    def _run_ok(self, args: Sequence[Any]) -> None:
        r = self._runner.run(args, self._query_timeout_s)
        text = r.stdout.strip()
        if text.startswith("{"):
            try:
                from .errors import raise_for_envelope

                raise_for_envelope(json.loads(text))
            except json.JSONDecodeError:
                pass
        if r.exit_code != 0:
            raise ViewAlyzerError("command_failed", _capture_failure_detail(r))


def _existing(path: PathLike, what: str) -> Path:
    p = Path(path)
    if not p.is_file():
        raise ViewAlyzerError("file_not_found", f"{what} not found: {p}")
    return p


def _capture_failure_detail(r: Any) -> str:
    """The CLI writes its `[headless] ERROR: ...` diagnostics to *stdout*;
    prefer those lines over a bare exit code."""
    combined = f"{r.stdout}\n{r.stderr}"
    errors = [ln.strip() for ln in combined.splitlines() if "ERROR" in ln]
    if errors:
        return "; ".join(dict.fromkeys(errors))
    if r.stderr.strip():
        return r.stderr.strip()
    tail = [ln.strip() for ln in r.stdout.splitlines() if ln.strip()][-3:]
    if tail:
        return f"exited {r.exit_code}: {' / '.join(tail)}"
    return f"ViewAlyzer exited {r.exit_code}"


class _config_path:
    """Context manager turning a ConfigSpec into an on-disk path. Inline
    dicts are written to a temp ``.vacf`` and removed afterwards."""

    def __init__(self, config: ConfigSpec) -> None:
        self._config = config
        self._temp: Optional[Path] = None

    def __enter__(self) -> Path:
        if isinstance(self._config, Mapping):
            fd, name = tempfile.mkstemp(suffix=".vacf", prefix="viewalyzer-")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(dict(self._config), f)
            self._temp = Path(name)
            return self._temp
        return _existing(self._config, "connection config")

    def __exit__(self, *exc: Any) -> None:
        if self._temp is not None:
            try:
                self._temp.unlink()
            except OSError:
                pass
