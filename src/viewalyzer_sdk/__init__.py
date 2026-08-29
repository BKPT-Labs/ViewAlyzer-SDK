"""viewalyzer-sdk - Python SDK for the ViewAlyzer headless CLI.

Automate trace capture, analytics queries, and regression assertions against
real embedded targets from Python, pytest, and CI:

    from viewalyzer_sdk import ViewAlyzer

    va = ViewAlyzer()
    rec = va.record("board.vacf", output="run1.vadb", duration_s=10)
    assert rec.total_events > 0
    assert rec.inversions()["inversions"] == []
    top = rec.task_stats()[0]
    assert top["cpu_percent"] < 80
"""
from .client import (
    HWTRACE_ARCHS,
    QUERY_VERBS,
    SCHEMA_VERSION,
    SUPPORTED_SCHEMA_VERSIONS,
    TIERED_VERBS,
    TWO_TIER_VERBS,
    UNTIERED_VERBS,
    ViewAlyzer,
)
from .discovery import ENV_VAR, find_viewalyzer, find_viewalyzer_with_source
from .errors import BinaryNotFound, ViewAlyzerError, ViewAlyzerWarning
from .recording import Recording
from .streaming import StreamEvent, StreamMeta, StreamSample, StreamSession

__version__ = "1.3.0"

__all__ = [
    "ViewAlyzer",
    "Recording",
    "StreamSession",
    "StreamSample",
    "StreamMeta",
    "StreamEvent",
    "ViewAlyzerError",
    "ViewAlyzerWarning",
    "BinaryNotFound",
    "find_viewalyzer",
    "find_viewalyzer_with_source",
    "ENV_VAR",
    "SCHEMA_VERSION",
    "SUPPORTED_SCHEMA_VERSIONS",
    "QUERY_VERBS",
    "TIERED_VERBS",
    "TWO_TIER_VERBS",
    "UNTIERED_VERBS",
    "HWTRACE_ARCHS",
    "__version__",
]
