# SPDX-License-Identifier: LicenseRef-BKPT-Proprietary
"""The structural part of the Trace Domain v1 contract.

Kept as data so the offline checker and exported JSON Schema use the same
field definitions. Cross-field and reference checks live in domains.py.
"""
from copy import deepcopy

DOMAIN_API_VERSION = 1


def _object(required, **properties):
    return {"type": "object", "additionalProperties": False,
            "required": required.split() if required else [], "properties": properties}


def _array(items, **bounds):
    return {"type": "array", "items": items, **bounds}


def _ref(name):
    return {"$ref": "#/$defs/" + name}


def _optional(schema):
    # Nullable fields correspond to Option<T> in the application contract.
    return {"anyOf": [schema, {"type": "null"}]}


S = {"type": "string"}
B = {"type": "boolean"}
N = {"type": "number"}
U32 = {"type": "integer", "minimum": 0, "maximum": 4294967295}
U64 = {"type": "integer", "minimum": 0, "maximum": 18446744073709551615}
U16 = {"type": "integer", "minimum": 0, "maximum": 65535}
STRINGS = _array(S)
SAMPLE_TYPES = ["u8", "u16", "u32", "i8", "i16", "i32", "f32"]
ACTION_TYPES = SAMPLE_TYPES + ["bool", "bytes", "utf8"]
DISPLAYS = ["graph", "bar", "level", "scatter", "impulse", "enum", "gauge",
            "counter", "table", "histogram", "toggle", "task", "isr", "register", "angle"]
SAFETY = ["read-safe", "safe", "conditional", "intrusive", "unsafe", "unsupported"]
COUNTERS = ["monotonic", "wrapping", "clear_on_read"]


def _enum(*values):
    return {"type": "string", "enum": list(values)}


def _tag(key, tag_value, required, **fields):
    return _object(key + " " + required, **{key: _enum(tag_value), **fields})


DEFS = {
    "gate": _object("peripheral register mask equals description",
                    peripheral=S, register=S, mask=S, equals=S, description=S),
    "step": {"oneOf": [
        _tag("op", "deref", ""),
        _tag("op", "member", "name", name=S),
        _tag("op", "index", "index", index=U32),
    ]},
    "structured": _object("symbol steps", symbol=S, steps=_array(_ref("step"), maxItems=16)),
    "ring": _object("symbol entries stride_bytes own_word_offset own_mask count_set",
                    symbol=S, entries=U16, stride_bytes=U16, own_word_offset=U16,
                    own_mask=S, count_set=B),
    "sample": _object("name", name=S, peripheral=_optional(S), register=_optional(S),
                      symbol=_optional(S), ty=_enum(*SAMPLE_TYPES), safety=_enum(*SAFETY),
                      reason=_optional(S), note=_optional(S),
                      precondition=_optional(_ref("gate")), offset_bytes=U32,
                      counter=_optional(_enum(*COUNTERS)), ring=_optional(_ref("ring")),
                      structured=_optional(_ref("structured"))),
    "ram_group": _object("id channels max_bytes", id=S,
                         channels=_array(S, minItems=2, maxItems=32),
                         max_bytes={"type": "integer", "minimum": 4, "maximum": 4096},
                         allow_padding=B),
    "sampled_state": _object("channels", svd_device=S, target_device_prefix=STRINGS,
                             rate_hz={"type": "number", "exclusiveMinimum": 0, "maximum": 1000},
                             coalesce_gap_bytes=U64, precondition=_optional(_ref("gate")),
                             channels=_array(_ref("sample")),
                             ram_read_groups=_array(_ref("ram_group"), maxItems=32)),
    "channel_match": _object("ns", ns=S, name=_optional(S), name_prefix=_optional(S)),
    "channel_rule": _object("match", **{
        "match": _ref("channel_match"), "unit": _optional(S),
        "display": _optional(_enum(*DISPLAYS)), "graph": _optional(B),
        "group": _optional(S), "names": _optional({"type": "object", "additionalProperties": S}),
        "interpolation": _optional(S), "min": _optional(N), "max": _optional(N), "warn": _optional(N),
    }),
    "derived": {"oneOf": [
        _tag("type", "field", "name source shift mask", name=S, source=S,
             shift={"type": "integer", "minimum": 0, "maximum": 63}, mask=S,
             values=_optional({"type": "object", "additionalProperties": S})),
        _tag("type", "rate", "name source", name=S, source=S, unit=_optional(S)),
        _tag("type", "map", "name source table", name=S, source=S,
             table={"type": "object", "additionalProperties": N}, unit=_optional(S)),
    ]},
    "when": {"oneOf": [
        _tag("type", "sustained_at_or_above", "channel value sustained_ms", channel=S, value=N, sustained_ms=U64),
        _tag("type", "sustained_at_or_below", "channel value sustained_ms", channel=S, value=N, sustained_ms=U64),
        _tag("type", "counter_advanced", "channel", channel=S),
        _tag("type", "value_at_or_above", "channel value", channel=S, value=N),
        _tag("type", "value_in", "channel values", channel=S, values=_array(N, minItems=1, maxItems=256)),
        _tag("type", "counter_stalled", "channel while_advances window_ms", channel=S,
             while_advances=S, window_ms={**U64, "minimum": 1}, min_advance={"type": "number", "exclusiveMinimum": 0}),
        _tag("type", "ratio_below", "channel reference ratio sustained_ms", channel=S,
             reference=S, ratio={"type": "number", "minimum": 0}, sustained_ms=U64),
    ]},
    "verdict": _object("id name when", id=S, name=S,
                       severity=_enum("error", "warning", "suspicious", "flag", "info"),
                       when=_ref("when"), explain=S, evidence=STRINGS),
    "field": _object("channel label", channel=S, label=S, group=_array(S, maxItems=4),
                     reference=_optional(S), unit=S, action=_optional(S)),
    "section": _object("title view fields", title=S, view=_enum("cards", "table"),
                       description=S, collapsed=_optional(B),
                       fields=_array(_ref("field"), minItems=1, maxItems=32), checks=STRINGS),
    "presentation": _object("sections", sections=_array(_ref("section"), minItems=1, maxItems=12)),
    "choice": _object("value label", value=S, label=S),
    "input": _object("ty", ty=_enum(*ACTION_TYPES), min=_optional(N), max=_optional(N),
                     choices=_array(_ref("choice"), maxItems=64),
                     max_bytes=_optional(U64), termination=_optional(S)),
    "target": {"oneOf": [
        _tag("kind", "ram", "source", source=_ref("structured"), execution=_enum("halted", "running_scalar")),
        _tag("kind", "register", "peripheral register", peripheral=S, register=S, field=_optional(S),
             execution=_enum("halted", "running_scalar")),
        _tag("kind", "mailbox", "symbol operation timeout_ms", symbol=S, operation=U32, timeout_ms=U32),
    ]},
    "action": _object("id label input target completion", id=S, label=S, input=_ref("input"),
                      target=_ref("target"), completion=_enum("readback", "transport", "application")),
    "param": _object("key", key=S, label=S, type=_enum("number", "string"), default=S,
                     min=_optional(N), max=_optional(N)),
    "instrument": _object("cmd", cmd=S, warmup_s=N, sync_name=_optional(S), params=_array(_ref("param"))),
}

SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "Trace Domain v1",
    "description": "Structural schema. Use viewalyzer-domain for cross-field and channel-reference validation.",
    **_object("id name version", id=S, name=S, version=S, domain_api={"type": "integer", "enum": [DOMAIN_API_VERSION]},
              publisher=S, channels=_array(_ref("channel_rule")), series_kinds=STRINGS,
              namespaces=STRINGS, instrument=_optional(_ref("instrument")),
              sampled_state=_optional(_ref("sampled_state")), derived=_array(_ref("derived")),
              verdicts=_array(_ref("verdict")), presentation=_optional(_ref("presentation")),
              actions=_array(_ref("action"), maxItems=32)),
    "$defs": DEFS,
}


def domain_schema():
    """Return an independent JSON Schema document for Trace Domain v1."""
    return deepcopy(SCHEMA)
