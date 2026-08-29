"""The viewalyzer-doctor entry point's schema handshake."""
from viewalyzer_sdk import SCHEMA_VERSION, SUPPORTED_SCHEMA_VERSIONS
from viewalyzer_sdk.__main__ import schema_warning


def test_supported_schemas_do_not_warn():
    for schema in SUPPORTED_SCHEMA_VERSIONS:
        assert schema_warning(schema) is None
    assert SCHEMA_VERSION in SUPPORTED_SCHEMA_VERSIONS


def test_unknown_schema_warns():
    for schema in (0, 3, None, "2"):
        text = schema_warning(schema)
        assert text and text.startswith("warning:")
        assert str(schema) in text and str(SCHEMA_VERSION) in text
