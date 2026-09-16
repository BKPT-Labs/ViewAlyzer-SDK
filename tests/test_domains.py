# SPDX-License-Identifier: LicenseRef-BKPT-Proprietary
"""Author-facing errors and conformance with the application's parser."""
from copy import deepcopy
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys

import pytest

from viewalyzer_sdk.domains import (
    DOMAIN_API_VERSION, DomainValidationError, domain_schema, load_domain,
    main, parse_domain, validate_domain,
)

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples" / "trace-domains"


def example(name="middleware"):
    return json.loads((EXAMPLES / (name + ".vadomain")).read_text(encoding="utf-8"))


def changed(name, path, value):
    d = example(name)
    target = d
    for part in path[:-1]:
        target = target[part]
    target[path[-1]] = value
    return d


INVALID_CASES = [
    ("counter", ("domain_api",), DOMAIN_API_VERSION + 1),
    ("counter", ("domain_api",), 0),
    ("counter", ("domain_api",), True),
    ("counter", ("domain_api",), 1.0),
    ("counter", ("sampled_state", "rate_hz"), 0),
    ("counter", ("sampled_state", "rate_hz"), 1001),
    ("counter", ("sampled_state", "rate_hz"), "20"),
    ("counter", ("sampled_state", "channels", 0, "ty"), "u64"),
    ("counter", ("sampled_state", "channels", 0, "symbol"), None),
    ("counter", ("sampled_state", "channels", 0, "safety"), "conditional"),
    ("counter", ("sampled_state", "channels", 1, "name"), "packets.completed"),
    ("counter", ("derived", 0, "source"), "packets.misspelled"),
    ("counter", ("derived", 0, "name"), "packets.completed"),
    ("counter", ("verdicts", 0, "evidence", 0), "missing"),
    ("counter", ("verdicts", 0, "severity"), "critical"),
    ("counter", ("channels", 0, "display"), "pie"),
    ("counter", ("channels", 0, "match", "unknown"), 1),
    ("counter", ("presentation", "sections", 0, "fields", 0, "action"), "missing"),
    ("ethernet", ("sampled_state", "channels", 0, "symbol"), "ambiguous"),
    ("ethernet", ("sampled_state", "channels", 0, "offset_bytes"), 4),
    ("ethernet", ("sampled_state", "channels", 2, "ring", "entries"), 0),
    ("ethernet", ("sampled_state", "channels", 2, "ring", "entries"), 65536),
    ("ethernet", ("sampled_state", "channels", 2, "ring", "stride_bytes"), 65535),
    ("ethernet", ("sampled_state", "channels", 2, "ring", "own_word_offset"), 22),
    ("ethernet", ("sampled_state", "channels", 2, "ring", "own_mask"), "0"),
    ("ethernet", ("sampled_state", "channels", 2, "ring", "own_mask"), "0x100000000"),
    ("ethernet", ("sampled_state", "channels", 2, "ty"), "f32"),
    ("ethernet", ("sampled_state", "precondition", "equals"), "0x2"),
    ("ethernet", ("sampled_state", "precondition", "mask"), "-1"),
    ("ethernet", ("derived", 0, "shift"), 64),
    ("ethernet", ("derived", 0, "mask"), "0x10000000000000000"),
    ("middleware", ("sampled_state", "channels", 0, "structured", "steps"), []),
    ("middleware", ("sampled_state", "channels", 0, "structured", "steps"), [{"op": "deref", "offset": 4}]),
    ("middleware", ("sampled_state", "channels", 0, "structured", "steps"), [{"op": "deref"}] * 17),
    ("middleware", ("sampled_state", "channels", 0, "structured", "symbol"), "queue()"),
    ("middleware", ("sampled_state", "channels", 0, "counter"), "clear_on_read"),
    ("middleware", ("sampled_state", "channels", 3, "structured", "steps", 0, "index"), -1),
    ("middleware", ("sampled_state", "ram_read_groups", 0, "max_bytes"), 63),
    ("middleware", ("sampled_state", "ram_read_groups", 0, "channels"), ["queue.used", "queue.used"]),
    ("middleware", ("sampled_state", "ram_read_groups", 0, "channels"), ["queue.used", "missing"]),
    ("middleware", ("derived", 1, "table"), {}),
    ("middleware", ("derived", 1, "table"), {"0x1": 100}),
    ("middleware", ("derived", 1, "table"), {"9223372036854775808": 100}),
    ("middleware", ("verdicts", 0, "when", "window_ms"), 0),
    ("middleware", ("verdicts", 0, "when", "min_advance"), 0),
    ("middleware", ("verdicts", 1, "when", "values"), []),
    ("middleware", ("verdicts", 1, "when", "values"), [0] * 257),
    ("middleware", ("verdicts", 2, "when", "ratio"), -0.1),
    ("middleware", ("presentation", "sections", 0, "fields", 0, "reference"), "missing"),
    ("middleware", ("presentation", "sections", 0, "fields", 1, "group"), ["😀" * 33]),
    ("middleware", ("presentation", "sections", 0, "fields", 1, "group"), [" "]),
    ("middleware", ("presentation", "sections", 0, "fields", 1, "group"), ["a"] * 5),
    ("middleware", ("presentation", "sections", 0, "checks"), ["missing"]),
    ("actions", ("actions", 0, "input", "min"), 1.5),
    ("actions", ("actions", 0, "input", "max"), 65536),
    ("actions", ("actions", 0, "input", "min"), 501),
    ("actions", ("actions", 0, "input", "max_bytes"), 4),
    ("actions", ("actions", 0, "target", "source", "steps"), [{"op": "deref"}]),
    ("actions", ("actions", 0, "completion"), "transport"),
    ("actions", ("actions", 1, "input", "choices"), [{"value": "1", "label": "One"}, {"value": "0x1", "label": "Also one"}]),
    ("actions", ("actions", 1, "target", "operation"), 0),
    ("actions", ("actions", 1, "target", "timeout_ms"), 99),
    ("actions", ("actions", 1, "target", "timeout_ms"), 5001),
    ("actions", ("actions", 1, "input"), {"ty": "bytes", "max_bytes": 65}),
    ("actions", ("actions", 2, "input", "ty"), "i32"),
    ("instrument", ("instrument", "params", 0, "default"), "fast"),
    ("instrument", ("instrument", "params", 0, "key"), "rate.value"),
]


@pytest.mark.parametrize("name,path,value", INVALID_CASES)
def test_invalid_schema_or_relationship_reports_a_useful_location(name, path, value):
    result = validate_domain(changed(name, path, value))
    assert not result.valid
    assert all(e.message and e.path.startswith("$") and e.pointer.startswith("/") for e in result.errors)
    assert any(e.parts[:1] == path[:1] for e in result.errors)


@pytest.mark.parametrize("path", sorted(EXAMPLES.glob("*.vadomain")), ids=lambda p: p.stem)
def test_complete_examples_load_without_an_app(path):
    assert load_domain(path)["domain_api"] == DOMAIN_API_VERSION


def test_authoring_guide_json_is_copyable_and_complete_descriptors_validate():
    guide = (ROOT / "docs" / "guide" / "trace-domains.md").read_text(encoding="utf-8")
    blocks = re.findall(r"^```json\n(.*?)^```", guide, re.MULTILINE | re.DOTALL)
    assert blocks
    complete = 0
    for block in blocks:
        data = json.loads(block)
        if isinstance(data, dict) and {"id", "name", "version"} <= data.keys():
            validate_domain(data).raise_for_errors()
            complete += 1
    assert complete >= 6


def test_current_features_are_valid_with_implicit_version_and_defaults_do_not_mutate_input():
    d = example("actions")
    del d["domain_api"]
    original = deepcopy(d)
    assert validate_domain(d).valid
    assert parse_domain(json.dumps(d)) == original
    assert d == original
    schema = domain_schema()
    schema["properties"].clear()
    assert "id" in domain_schema()["properties"]


@pytest.mark.parametrize("text", ['{"id":"a.b","id":"c.d"}', '{"x":NaN}', '{"x":Infinity}', '{"x":1e999}', '{"id":"a.b",}', '[]', 'null', '"text"', '{"id":"a.b","name":"\\ud800","version":"1"}'])
def test_rejects_ambiguous_nonstandard_or_non_object_json(text):
    with pytest.raises(DomainValidationError):
        parse_domain(text)


def test_errors_identify_duplicate_keys_nonfinite_values_and_misspelled_fields():
    for text, pointer, reason in [
        ('{"sampled_state":{"rate_hz":20,"rate_hz":40}}', "/sampled_state/rate_hz", "duplicate"),
        ('{"sampled_state":{"rate_hz":NaN}}', "/sampled_state/rate_hz", "finite"),
    ]:
        with pytest.raises(DomainValidationError) as caught:
            parse_domain(text)
        assert caught.value.errors[0].pointer == pointer
        assert reason in caught.value.errors[0].message
    d = example("counter")
    d["sampled_state"]["rate_hzz"] = 50
    issue = validate_domain(d).errors[0]
    assert issue.pointer == "/sampled_state/rate_hzz"
    assert "did you mean 'rate_hz'" in issue.message
    d = changed("ethernet", ("sampled_state", "channels", 2, "ring", "own_word_offset"), 24)
    issue = validate_domain(d).errors[0]
    assert issue.pointer == "/sampled_state/channels/2/ring/own_word_offset"
    assert "exceeds stride_bytes" in issue.message


def test_derived_dependencies_must_be_ordered_for_sampled_domains():
    d = example("counter")
    d["derived"] = [{"type": "rate", "name": "packets.rate", "source": "first"},
                    {"type": "rate", "name": "first", "source": "packets.completed"}]
    assert not validate_domain(d).valid
    d["derived"].reverse()
    assert validate_domain(d).valid


def test_external_input_vocabulary_can_be_unknown_until_capture():
    d = example("instrument")
    d["derived"] = [{"type": "rate", "name": "computed", "source": "external_input"}]
    assert validate_domain(d).valid
    d["sampled_state"] = {"channels": []}
    assert not validate_domain(d).valid


@pytest.mark.parametrize("input_,valid", [
    ({"ty": "bool", "choices": [{"value": "true", "label": "On"}]}, True),
    ({"ty": "bool", "min": 0}, False),
    ({"ty": "bool", "choices": [{"value": "1", "label": "On"}]}, False),
    ({"ty": "f32", "choices": [{"value": "1.5", "label": "Gain"}]}, True),
    ({"ty": "f32", "choices": [{"value": "1e-99", "label": "Too small"}]}, False),
    ({"ty": "f32", "choices": [{"value": "3.5e38", "label": "Too big"}]}, False),
    ({"ty": "f32", "choices": [{"value": "1", "label": "A"}, {"value": "1.00000001", "label": "B"}]}, False),
    ({"ty": "utf8", "max_bytes": 5, "termination": "nul", "choices": [{"value": "😀", "label": "Text"}]}, True),
    ({"ty": "utf8", "max_bytes": 4, "termination": "nul", "choices": [{"value": "😀", "label": "Text"}]}, False),
    ({"ty": "utf8", "max_bytes": 5}, False),
    ({"ty": "utf8", "max_bytes": 5, "termination": "none", "choices": [{"value": "a\0", "label": "NUL"}]}, False),
    ({"ty": "bytes", "max_bytes": 2, "choices": [{"value": "01 ff", "label": "Bytes"}]}, True),
    ({"ty": "bytes", "max_bytes": 2, "choices": [{"value": "1 ff", "label": "Partial"}]}, False),
    ({"ty": "bytes", "max_bytes": 0}, False),
    ({"ty": "i16", "choices": [{"value": "-32768", "label": "Low"}]}, True),
    ({"ty": "u8", "choices": [{"value": "256", "label": "Overflow"}]}, False),
])
def test_action_choice_encoding_limits(input_, valid):
    d = example("actions")
    action = d["actions"][0]
    action["input"] = input_
    action["target"]["execution"] = "halted"
    assert validate_domain(d).valid is valid


def test_cli_reports_all_files_and_exit_codes(tmp_path, capsys):
    bad = tmp_path / "bad.vadomain"
    bad.write_text('{"id":"invalid"}', encoding="utf-8")
    good = str(EXAMPLES / "counter.vadomain")
    assert main(["--json", good, str(bad)]) == 1
    reports = json.loads(capsys.readouterr().out)
    assert [r["valid"] for r in reports] == [True, False]
    assert reports[1]["errors"][0]["pointer"] == "/name"
    assert main([str(tmp_path / "missing")]) == 2
    assert "cannot read file" in capsys.readouterr().out
    bad.write_bytes(b"\xff")
    assert main([str(bad)]) == 1
    assert "UTF-8" in capsys.readouterr().out
    assert main(["--schema"]) == 0
    assert json.loads(capsys.readouterr().out)["properties"]["domain_api"]["enum"] == [DOMAIN_API_VERSION]


def test_module_command_does_not_need_cli_discovery():
    env = dict(os.environ, VIEWALYZER_CLI=str(ROOT / "does-not-exist"))
    result = subprocess.run([sys.executable, "-m", "viewalyzer_sdk.domains", str(EXAMPLES / "counter.vadomain")],
                            capture_output=True, text=True, env=env)
    assert result.returncode == 0, result.stderr
    assert "VALID:" in result.stdout
    assert not result.stderr


def test_malformed_in_memory_data_and_extreme_integer_strings_return_errors():
    for d in [math.nan, {"id": "a.b", "name": "A", "version": "1", "channels": {}},
              changed("ethernet", ("derived", 0, "mask"), "1" * 10000),
              changed("middleware", ("derived", 1, "table"), {"1" * 10000: 2})]:
        assert not validate_domain(d).valid


def test_conformance_with_application_parser_when_available():
    """Optional executable reads JSON lines, prints true/false for each line.

    Set VIEWALYZER_DOMAIN_CHECKER to a helper built against the current
    application's Descriptor::parse. SDK-only installs skip this integration.
    """
    checker = os.environ.get("VIEWALYZER_DOMAIN_CHECKER")
    if not checker:
        pytest.skip("application parser helper is not configured")
    corpus = [example(p.stem) for p in sorted(EXAMPLES.glob("*.vadomain"))]
    corpus += [changed(name, path, value) for name, path, value in INVALID_CASES]
    for name in ("actions", "middleware"):
        d = example(name)
        del d["domain_api"]
        corpus.append(d)
    result = subprocess.run([checker], input="".join(json.dumps(d) + "\n" for d in corpus),
                            capture_output=True, text=True, check=True)
    observed = result.stdout.splitlines()
    expected = [str(validate_domain(d).valid).lower() for d in corpus]
    assert len(observed) == len(expected)
    for i, (got, want) in enumerate(zip(observed, expected)):
        assert got == want, (i, corpus[i], got, want)
