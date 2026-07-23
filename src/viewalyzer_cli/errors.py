"""Exception types for the ViewAlyzer CLI bindings.

One exception class carries every failure, CLI-side or ours. The binary's
error envelope (``{"error": <code>, "message": ..., "suggestion": ...,
"limits": ...}``) maps directly onto it; failures raised by the bindings
themselves use our own codes (``binary_missing``, ``timeout``, ``bad_output``,
``record_failed``, ``file_not_found``, ``bad_arguments``).
"""
from __future__ import annotations

from typing import Any, Optional


class ViewAlyzerError(Exception):
    """A failure from the ViewAlyzer CLI or the bindings around it.

    Attributes:
        code: short machine token, e.g. ``no_such_recording``, ``bad_sql``,
            ``window_too_wide``, ``timeout``, ``binary_missing``.
        message: human-readable explanation.
        suggestion: optional machine-readable next step from the CLI
            (e.g. a narrower time window after ``window_too_wide``).
        limits: optional limit details from the CLI (token/row caps).
    """

    def __init__(
        self,
        code: str,
        message: str,
        *,
        suggestion: Optional[dict] = None,
        limits: Optional[dict] = None,
    ) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.suggestion = suggestion
        self.limits = limits


class BinaryNotFound(ViewAlyzerError):
    """The ViewAlyzer executable could not be located (or an explicitly
    configured path does not exist)."""

    def __init__(self, message: str) -> None:
        super().__init__("binary_missing", message)


def raise_for_envelope(payload: dict) -> dict:
    """Raise :class:`ViewAlyzerError` if *payload* is a CLI error envelope,
    otherwise return it unchanged. The envelope is detected by the ``error``
    key - not by the process exit code, which is non-zero for envelopes too."""
    if "error" in payload:
        raise ViewAlyzerError(
            str(payload.get("error") or "error"),
            str(payload.get("message") or ""),
            suggestion=_as_dict(payload.get("suggestion")),
            limits=_as_dict(payload.get("limits")),
        )
    return payload


def _as_dict(value: Any) -> Optional[dict]:
    return value if isinstance(value, dict) else None
