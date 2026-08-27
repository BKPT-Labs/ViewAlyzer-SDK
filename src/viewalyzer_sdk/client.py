"""High-level client for the ViewAlyzer headless CLI.

    from viewalyzer_sdk import ViewAlyzer

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
from typing import (
    TYPE_CHECKING,
    Any,
    Dict,
    List,
    Mapping,
    Optional,
    Sequence,
    Union,
)

from .discovery import find_viewalyzer
from .errors import BinaryNotFound, ViewAlyzerError
from .recording import Recording
from .runner import (
    DEFAULT_QUERY_TIMEOUT_S,
    RECORD_TIMEOUT_PAD_S,
    BinarySpec,
    Runner,
)

if TYPE_CHECKING:  # pragma: no cover - import cycle guard
    from .streaming import StreamSession

#: The agent wire-protocol version this SDK was written against.
#: Check the CLI's own value once via :meth:`ViewAlyzer.version`.
SCHEMA_VERSION = 1

#: Query verbs with three tiers (``summary`` | ``bucketed`` | ``raw``).
TIERED_VERBS = ("timeline", "events", "user-traces")
#: Query verbs with two tiers (``summary`` | ``raw``).
TWO_TIER_VERBS = ("timers", "etm")
#: Query verbs without tiers.
UNTIERED_VERBS = (
    "inversions",
    "cpu",
    "comms",
    "series",
    "sql",
    "fingerprint",
    "compare",
    "slices",
    "slice-details",
    "events-all",
    "user-traces-all",
)
QUERY_VERBS = (*TIERED_VERBS, *TWO_TIER_VERBS, *UNTIERED_VERBS)

# Stable stdout contracts of capture mode (docs/CLI.md in the ViewAlyzer repository):
#   [headless] Recording saved: /abs/path/run1.vadb (5576 KB)
#   [headless] Recording registered: id=f76593b93473
_RECORDING_SAVED_RE = re.compile(r"Recording saved:\s*(.+?)\s*\(\d+\s*KB\)")
_RECORDING_ID_RE = re.compile(r"Recording registered:\s*id=([0-9a-f]{12})")

#: Default timeout for :meth:`ViewAlyzer.snapshot` (probe attach + RAM read
#: + parse; no capture duration is involved).
DEFAULT_SNAPSHOT_TIMEOUT_S = 120.0

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
        :data:`SCHEMA_VERSION`, response shapes may not match this SDK."""
        return self._runner.run_json(["version"], self._query_timeout_s)

    def doctor(self) -> Dict[str, Any]:
        """Setup health check: probes per kind, serial ports, the recordings
        directory and the target registry, with a ``hint`` for anything
        missing. Returns ``{"checks": [{"id", "name", "required", "status":
        "ok"|"missing"|"none", "path"?, "version"?, "detail", "hint"?},
        ...], ...}``. A missing optional item is a report entry, not an
        error."""
        return self._runner.run_json(["doctor"], max(self._query_timeout_s, 60.0))

    def analyze_memory(
        self, elf: PathLike, *, map_file: Optional[PathLike] = None
    ) -> Dict[str, Any]:
        """Static flash/RAM breakdown of a firmware image. With *map_file*
        (the GNU ld linker MAP) the payload adds ``has_map_data`` and
        ``map``: region capacities with used/free/percent, the sections the
        linker placed, and the input sections it discarded."""
        args: List[Any] = ["memory", "--elf", _existing(elf, "ELF")]
        if map_file is not None:
            args += ["--map", _existing(map_file, "map file")]
        return self._runner.run_json(args, self._query_timeout_s)

    def list_symbols(
        self, elf: PathLike, *, filter: Optional[str] = None
    ) -> Dict[str, Any]:
        """Pollable symbols in a firmware ELF (name, address, size, type)."""
        args: List[Any] = ["symbols", "--elf", _existing(elf, "ELF")]
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
        probe is attached (with a single probe the drivers auto-select).

        The tool-path keyword arguments are accepted for compatibility with
        1.x scripts and have no effect: ViewAlyzer drives probes directly."""
        del jlink_path, stlink_path, cube_programmer_path
        return self._runner.run_json(["probes"], self._query_timeout_s)

    # ----- licensing ------------------------------------------------------
    # Kept for 1.x compatibility. ViewAlyzer currently has no license
    # backend, so these raise ``ViewAlyzerError("unsupported", ...)`` rather
    # than disappearing from the API; they become real calls when it does.

    def get_license(self) -> Dict[str, Any]:
        """Local license state. Not available in this ViewAlyzer build."""
        raise _licensing_unavailable()

    def activate_license(self, key: str, *, timeout_s: float = 60.0) -> Dict[str, Any]:
        """Activate this machine with a key. Not available in this build."""
        del key, timeout_s
        raise _licensing_unavailable()

    def validate_license(self, *, timeout_s: float = 60.0) -> Dict[str, Any]:
        """Refresh the license state. Not available in this build."""
        del timeout_s
        raise _licensing_unavailable()

    def deactivate_license(self, *, timeout_s: float = 60.0) -> Dict[str, Any]:
        """Release this machine's seat. Not available in this build."""
        del timeout_s
        raise _licensing_unavailable()

    # ----- the recording index --------------------------------------------

    def list_recordings(self) -> Dict[str, Any]:
        """The raw recording-index payload (``{"recordings": [...]}``)."""
        return self._runner.run_json(["recordings"], self._query_timeout_s)

    def recordings(self) -> List[Recording]:
        """The recording index as bound :class:`Recording` handles, in the
        CLI's order (newest first)."""
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
        self._run_ok(["recordings", "--delete-recording", ref])

    def delete_all_recordings(self) -> None:
        """**Destructive.** Clears the index and deletes every indexed
        recording file."""
        self._run_ok(["recordings", "--delete-all-recordings"])

    # ----- capture --------------------------------------------------------

    def record(
        self,
        config: ConfigSpec,
        *,
        output: PathLike,
        duration_s: float,
        elf: Optional[PathLike] = None,
        symbols: Union[str, Sequence[str]] = (),
        poll_hz: Optional[int] = None,
        extra_flags: Sequence[str] = (),
        timeout_s: Optional[float] = None,
    ) -> Recording:
        """Record the target's trace stream for *duration_s* seconds.

        *config* is a ``.vacf`` path or an inline dict with the same
        keys (``transport``, ``target-device``, ``cpu-clock-hz``, tool
        paths, ...). CLI flags win over config-file values.

        Pass *elf* and *symbols* to add a symbol watch to the same capture:
        the named variables are memory-polled over the debug probe while
        the trace records (at *poll_hz*, if given) and land in the same
        recording as extra traces. Each symbol is ``name`` or
        ``name:type`` with type one of ``u8 u16 u32 i8 i16 i32 f32``.
        For RAM-buffer transports, *elf* alone also lets the CLI resolve
        the ring's control-block address from the firmware image.

        *extra_flags* are appended verbatim for flags without a dedicated
        parameter (e.g. ``["--log", path]`` or port overrides).

        Returns a :class:`Recording` whose ``path`` is the authoritative
        on-disk file; the CLI forces the ``.vadb`` extension, so it may
        differ from *output*.
        """
        symbols = _as_symbol_list(symbols)
        args: List[Any] = [
            "--output",
            str(output),
            "--duration",
            duration_s,
        ]
        if elf is not None:
            args += ["--elf", _existing(elf, "ELF")]
        if symbols:
            if elf is None:
                raise ViewAlyzerError(
                    "bad_arguments", "symbols need an elf to resolve against"
                )
            args += ["--symbols", ",".join(symbols)]
        if poll_hz is not None:
            args += ["--poll-hz", poll_hz]
        args += list(extra_flags)

        timeout = timeout_s if timeout_s is not None else duration_s + RECORD_TIMEOUT_PAD_S
        with _config_path(config) as config_path:
            r = self._runner.run(["capture", "--config", config_path, *args], timeout)
        if r.exit_code != 0:
            # A blocked capture may carry a machine-readable envelope on
            # stdout (e.g. cooldown_active with retry_after_s); prefer its
            # code over the generic one.
            _raise_any_error_envelope(r.stdout)
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

    def stream(
        self,
        config: ConfigSpec,
        *,
        output: PathLike,
        duration_s: float,
        elf: Optional[PathLike] = None,
        symbols: Union[str, Sequence[str]] = (),
        poll_hz: Optional[int] = None,
        extra_flags: Sequence[str] = (),
        timeout_s: Optional[float] = None,
    ) -> "StreamSession":
        """Record like :meth:`record`, but stream live samples while the
        capture runs.

        Starts the same capture as :meth:`record` (same *config*, *elf* /
        *symbols* / *poll_hz* semantics, same ``.vadb`` landing on disk)
        with the CLI's ``--stream`` tap enabled, and returns a
        :class:`~viewalyzer_sdk.streaming.StreamSession` immediately, while
        the CLI is still connecting. Iterate the session for
        :class:`~viewalyzer_sdk.streaming.StreamSample` points as they
        arrive (firmware user traces, polled symbols, and ``--dwt-watch``
        hardware watches via *extra_flags*; NOT slices, PC samples, or
        other timeline events, which land in the recording only), stop
        early with :meth:`~viewalyzer_sdk.streaming.StreamSession.stop`,
        and take the finished Recording from
        :meth:`~viewalyzer_sdk.streaming.StreamSession.result`::

            with va.stream("board.vacf", output="run.vadb",
                           duration_s=60) as s:
                for sample in s:
                    chart.add(sample.name, sample.t_s, sample.value)
            rec = s.result()

        Sample ``t_us`` is the live arrival timeline (t=0 at the first
        sample); the recording keeps exact device-clock timestamps, so use
        the Recording for analysis and the stream for display. Early stop
        goes through ``--stop-file`` on every OS (plus SIGINT on POSIX).

        *timeout_s* bounds the whole session (default: *duration_s* plus
        connect/finalize headroom); past it, iteration raises and the CLI
        is killed.
        """
        from .streaming import StreamSession  # late: avoids a module cycle

        symbols = _as_symbol_list(symbols)
        args: List[Any] = [
            "--output",
            str(output),
            "--duration",
            duration_s,
        ]
        if elf is not None:
            args += ["--elf", _existing(elf, "ELF")]
        if symbols:
            if elf is None:
                raise ViewAlyzerError(
                    "bad_arguments", "symbols need an elf to resolve against"
                )
            args += ["--symbols", ",".join(symbols)]
        if poll_hz is not None:
            args += ["--poll-hz", poll_hz]
        args += list(extra_flags)
        return StreamSession(
            self,
            self._runner,
            config,
            args,
            output=output,
            duration_s=duration_s,
            timeout_s=timeout_s,
        )

    def record_polls(
        self,
        elf: PathLike,
        symbols: Union[str, Sequence[str]],
        *,
        duration_s: float,
        poll_hz: int = 100,
        config: Optional[ConfigSpec] = None,
        target_device: Optional[str] = None,
        extra_flags: Sequence[str] = (),
        timeout_s: Optional[float] = None,
    ) -> Recording:
        """Sample target variables over the debug probe; no firmware
        instrumentation required. Needs a probe transport (not udp/serial).

        Pass *config* to reuse a connection file, or *target_device* plus
        the transport and any other connection flags via *extra_flags*
        (e.g. ``["--transport", "stlink-rambuf"]``) for config-less mode. Each symbol is ``name`` or ``name:type``.
        Returns a :class:`Recording` with the poll summary in
        ``info["summary"]``.
        """
        symbols = _as_symbol_list(symbols)
        if not symbols:
            raise ViewAlyzerError("bad_arguments", "no symbols to poll")
        args: List[Any] = [
            "poll",
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

    def snapshot(
        self,
        config: ConfigSpec,
        *,
        output: PathLike,
        elf: Optional[PathLike] = None,
        extra_flags: Sequence[str] = (),
        timeout_s: float = DEFAULT_SNAPSHOT_TIMEOUT_S,
    ) -> Recording:
        """Post-mortem snapshot: attach to the target WITHOUT reset, read
        the firmware's RAM trace ring through the probe, and save the
        recovered window as a normal ``.vadb`` recording.

        Requires a RAM-buffer transport (``stlink-rambuf`` or
        ``jlink-rambuf``) in *config*. There is no duration: the target
        recorded untethered, this reads out what is in RAM. Pass *elf* (or
        a ``rambuf-address`` config key) to skip the RAM scan for the
        ring's control block.

        Returns a :class:`Recording` with the snapshot summary (ring kind,
        events, window bytes, wrap state, ...) in ``info["summary"]``.
        Raises ``ViewAlyzerError("empty_snapshot", ...)`` when the ring
        holds no parseable events."""
        args: List[Any] = ["snapshot", "--output", str(output)]
        if elf is not None:
            args += ["--elf", _existing(elf, "ELF")]
        args += list(extra_flags)
        with _config_path(config) as config_path:
            payload = self._runner.run_json(
                [*args, "--config", config_path], timeout_s
            )
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
        elf: Optional[PathLike] = None,
        extra_flags: Sequence[str] = (),
        timeout_s: Optional[float] = None,
    ) -> Dict[str, Any]:
        """One ``query`` call. Prefer the named helpers on
        :class:`Recording`; this generic form exists for flag combinations
        the helpers don't cover. *extra_flags* are appended verbatim (the
        ``series`` and ``fingerprint`` helpers use this for their
        verb-specific flags)."""
        if verb not in QUERY_VERBS:
            raise ViewAlyzerError(
                "bad_arguments",
                f"unknown query verb {verb!r}; expected one of {QUERY_VERBS}",
            )
        ref = recording.ref if isinstance(recording, Recording) else str(recording)
        args: List[Any] = ["query", verb, "--recording", ref]
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
        if elf is not None:
            args += ["--elf", _existing(elf, "ELF")]
        args += list(extra_flags)
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


def _licensing_unavailable() -> ViewAlyzerError:
    return ViewAlyzerError(
        "unsupported",
        "Licensing is not available in this ViewAlyzer build; nothing was "
        "activated or changed.",
    )


def _existing(path: PathLike, what: str) -> Path:
    p = Path(path)
    if not p.is_file():
        raise ViewAlyzerError("file_not_found", f"{what} not found: {p}")
    return p


def _as_symbol_list(symbols: Union[str, Sequence[str]]) -> List[str]:
    """Accept one symbol as a bare string, or any sequence of symbols."""
    if isinstance(symbols, str):
        return [symbols]
    return list(symbols)


def _raise_any_error_envelope(stdout: str) -> None:
    """Scan capture-mode stdout for a JSON error envelope and raise it.
    Capture output mixes progress lines with (at most one) envelope."""
    for line in stdout.splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                from .errors import raise_for_envelope

                raise_for_envelope(payload)


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
