"""Exception types for the ViewAlyzer SDK.

One exception class carries every failure, CLI-side or SDK-side. The CLI's
error envelope (``{"error": <code>, "message": ..., "suggestion": ...,
"limits": ...}``) maps directly onto it; failures raised by the SDK itself
use its own codes (``binary_missing``, ``timeout``, ``bad_output``,
``record_failed``, ``file_not_found``, ``bad_arguments``).
"""
from __future__ import annotations

from typing import Any, Optional


class ViewAlyzerError(Exception):
    """A failure from the ViewAlyzer CLI or the SDK around it.

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


class FingerprintMismatch(ViewAlyzerError):
    """A failed strict fingerprint gate; ``report`` retains the CLI evidence."""

    def __init__(self, report: dict) -> None:
        self.report = report
        verdict = report.get("verdict", "unknown")
        compatibility = _as_dict(report.get("compatibility")) or {}
        issues = report.get("measurement_issues")
        reasons = list(issues) if isinstance(issues, list) else []
        fields = compatibility.get("fields")
        reasons += [row.get("message", "") for row in (fields if isinstance(fields, list) else [])
                    if isinstance(row, dict) and row.get("rule") == "require" and row.get("status") != "match"]
        reasons = [str(reason).strip() for reason in reasons if reason and str(reason).strip()]
        if not reasons:
            results = report.get("results")
            reasons += [row.get("message") or str(row.get("metric", ""))
                        for row in (results if isinstance(results, list) else [])
                        if isinstance(row, dict) and row.get("status") in ("fail", "missing")]
            reasons = [str(reason).strip() for reason in reasons if reason and str(reason).strip()]
        if not reasons:
            reasons.append(str(compatibility.get("message") or "").strip() or "See comparison report")
        super().__init__(f"fingerprint_{verdict}", "; ".join(str(r) for r in reasons if r))


class BinaryNotFound(ViewAlyzerError):
    """The ViewAlyzer executable could not be located (or an explicitly
    configured path does not exist)."""

    def __init__(self, message: str) -> None:
        super().__init__("binary_missing", message)


class ViewAlyzerWarning(UserWarning):
    """Category of the SDK's ``warnings.warn`` calls: a request the CLI
    accepts but does not act on (e.g. ``symbols`` on a capture). Filter or
    escalate it with the standard ``warnings`` machinery."""


def raise_for_envelope(payload: dict) -> dict:
    """Raise :class:`ViewAlyzerError` if *payload* is a CLI error envelope,
    otherwise return it unchanged. The envelope is detected by the ``error``
    key, not by the process exit code (which is non-zero for envelopes too).
    """
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
