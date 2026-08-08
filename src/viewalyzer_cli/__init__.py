"""viewalyzer-cli - Python bindings for the ViewAlyzer headless CLI.

Automate trace capture, analytics queries, and regression assertions against
real embedded targets from Python, pytest, and CI:

    from viewalyzer_cli import ViewAlyzer

    va = ViewAlyzer()
    rec = va.record("board.vacfg.json", output="run1.vadb", duration_s=10)
    assert rec.total_events > 0
    assert rec.inversions()["inversions"] == []
    top = rec.task_stats()[0]
    assert top["cpu_percent"] < 80
"""
from .client import QUERY_VERBS, SCHEMA_VERSION, TIERED_VERBS, UNTIERED_VERBS, ViewAlyzer
from .discovery import ENV_VAR, find_viewalyzer, find_viewalyzer_with_source
from .errors import BinaryNotFound, ViewAlyzerError
from .recording import Recording

__version__ = "1.0.0"

__all__ = [
    "ViewAlyzer",
    "Recording",
    "ViewAlyzerError",
    "BinaryNotFound",
    "find_viewalyzer",
    "find_viewalyzer_with_source",
    "ENV_VAR",
    "SCHEMA_VERSION",
    "QUERY_VERBS",
    "TIERED_VERBS",
    "UNTIERED_VERBS",
    "__version__",
]
