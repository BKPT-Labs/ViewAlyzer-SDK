# SPDX-License-Identifier: LicenseRef-BKPT-Proprietary
"""Offline parsing and validation of Trace Domain v1 descriptors.

No executable discovery, subprocesses, target access or instrument execution.
Run ``python -m viewalyzer_sdk.domains --help`` for the command line interface.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from difflib import get_close_matches
import json
import math
from pathlib import Path
import re
import struct
import sys
from typing import Any, Optional, Sequence, Tuple, Union

from ._domain_schema import DOMAIN_API_VERSION, SCHEMA, domain_schema

__all__ = ["DOMAIN_API_VERSION", "DomainIssue", "DomainValidationResult",
           "DomainValidationError", "validate_domain", "parse_domain", "load_domain", "domain_schema"]
PathPart = Union[str, int]


@dataclass(frozen=True)
class DomainIssue:
    """One error, with an unambiguous JSON Pointer and a readable location."""

    parts: Tuple[PathPart, ...]
    message: str

    @property
    def pointer(self) -> str:
        return "".join("/" + str(p).replace("~", "~0").replace("/", "~1") for p in self.parts)

    @property
    def path(self) -> str:
        return "$" + "".join(f"[{p}]" if isinstance(p, int) else "[" + json.dumps(p) + "]" for p in self.parts)

    def as_dict(self) -> dict:
        return {"path": self.path, "pointer": self.pointer, "message": self.message}


@dataclass(frozen=True)
class DomainValidationResult:
    errors: Tuple[DomainIssue, ...]

    @property
    def valid(self) -> bool:
        return not self.errors

    def raise_for_errors(self) -> None:
        if not self.valid:
            raise DomainValidationError(self)


class DomainValidationError(ValueError):
    """Invalid JSON or a descriptor that does not satisfy Trace Domain v1."""

    def __init__(self, result: DomainValidationResult):
        self.result = result
        self.errors = result.errors
        super().__init__("\n".join(f"{e.path}: {e.message}" for e in self.errors))


def _shape(value: Any, spec: dict, path: tuple, errors: list) -> None:
    """Check only the fixed schema shipped above, never caller-supplied schemas."""
    if "$ref" in spec:
        spec = SCHEMA["$defs"][spec["$ref"].split("/")[-1]]
    if "anyOf" in spec:
        if value is not None:
            _shape(value, spec["anyOf"][0], path, errors)
        return
    if "oneOf" in spec:
        variants = spec["oneOf"]
        key = variants[0]["required"][0]
        if not isinstance(value, dict):
            errors.append(DomainIssue(path, "expected an object"))
            return
        for variant in variants:
            if value.get(key) in variant["properties"][key]["enum"]:
                _shape(value, variant, path, errors)
                return
        options = [v["properties"][key]["enum"][0] for v in variants]
        actual = repr(value[key]) if key in value else "missing field"
        errors.append(DomainIssue(path + (key,), "expected one of: " + ", ".join(options) + "; got " + actual))
        return
    kind = spec["type"]
    correct = {"object": isinstance(value, dict), "array": isinstance(value, list),
               "string": isinstance(value, str), "boolean": type(value) is bool,
               "integer": type(value) is int, "number": type(value) in (int, float),
               "null": value is None}[kind]
    if not correct:
        errors.append(DomainIssue(path, "expected " + kind + "; got " + type(value).__name__))
        return
    if kind in ("number", "integer"):
        try:
            finite = math.isfinite(value)
        except OverflowError:
            finite = False
        if not finite:
            errors.append(DomainIssue(path, "number must be finite and representable"))
            return
    if "enum" in spec and value not in spec["enum"]:
        options = ", ".join(map(str, spec["enum"]))
        errors.append(DomainIssue(path, "expected one of: " + options + "; got " + repr(value)))
    if kind == "object":
        properties = spec.get("properties", {})
        for name in spec.get("required", []):
            if name not in value:
                errors.append(DomainIssue(path + (name,), "required field is missing"))
        for name, item in value.items():
            if not isinstance(name, str):
                errors.append(DomainIssue(path, "object keys must be strings"))
            elif name in properties:
                _shape(item, properties[name], path + (name,), errors)
            elif isinstance(spec.get("additionalProperties"), dict):
                _shape(item, spec["additionalProperties"], path + (name,), errors)
            else:
                suggestion = get_close_matches(name, properties, n=1)
                hint = "; did you mean " + repr(suggestion[0]) + "?" if suggestion else ""
                errors.append(DomainIssue(path + (name,), "unknown field" + hint))
    elif kind == "array":
        if len(value) < spec.get("minItems", 0) or len(value) > spec.get("maxItems", math.inf):
            errors.append(DomainIssue(path, f"expected {spec.get('minItems', 0)} to {spec.get('maxItems', 'unbounded')} items"))
        for i, item in enumerate(value):
            _shape(item, spec["items"], path + (i,), errors)
    elif kind == "string":
        try:
            value.encode("utf-8")
        except UnicodeEncodeError:
            errors.append(DomainIssue(path, "string must contain valid Unicode"))
    elif kind in ("number", "integer"):
        if value < spec.get("minimum", -math.inf) or value > spec.get("maximum", math.inf):
            errors.append(DomainIssue(path, f"value {value} must be between {spec.get('minimum', '-infinity')} and {spec.get('maximum', 'infinity')}"))
        if "exclusiveMinimum" in spec and value <= spec["exclusiveMinimum"]:
            errors.append(DomainIssue(path, f"value {value} must be greater than {spec['exclusiveMinimum']}"))


def _uint(text: str, bits: int = 64) -> Optional[int]:
    s = text.strip()
    hex_value = s.startswith(("0x", "0X"))
    digits = s[2:] if hex_value else s
    if not re.fullmatch(r"\+?[0-9a-fA-F]+" if hex_value else r"\+?[0-9]+", digits):
        return None
    try:
        n = int(digits, 16 if hex_value else 10)
    except ValueError:
        return None
    return n if n < 1 << bits else None


def _identifier(s: str, maximum: int) -> bool:
    return len(s) <= maximum and re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*", s) is not None


_BOUNDS = {"u8": (0, 255), "u16": (0, 65535), "u32": (0, 4294967295),
           "i8": (-128, 127), "i16": (-32768, 32767), "i32": (-2147483648, 2147483647),
           "f32": (-3.4028234663852886e38, 3.4028234663852886e38)}
_CAPACITY = {"u8": 1, "i8": 1, "bool": 1, "u16": 2, "i16": 2, "u32": 4, "i32": 4, "f32": 4}


def _encode_choice(input_: dict, text: str) -> bytes:
    ty = input_["ty"]
    if len(text.encode("utf-8")) > 1024:
        raise ValueError("choice value exceeds 1024 UTF-8 bytes")
    cap = _CAPACITY.get(ty, input_.get("max_bytes"))
    s = text.strip()
    if ty == "utf8":
        if "\0" in text:
            raise ValueError("UTF-8 value contains NUL")
        encoded = text.encode("utf-8") + (b"\0" if input_["termination"] == "nul" else b"")
    elif ty == "bytes":
        if not re.fullmatch(r"[0-9a-fA-F\t\n\v\f\r ]*", s):
            raise ValueError("expected hexadecimal byte pairs")
        if any(len(word) % 2 for word in s.split()):
            raise ValueError("hexadecimal byte pair is incomplete")
        encoded = bytes.fromhex(s)
    elif ty == "bool":
        if s not in ("true", "false"):
            raise ValueError("boolean choice must be true or false")
        encoded = bytes([int(s == "true")])
    else:
        if ty == "f32":
            if not re.fullmatch(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?", s):
                raise ValueError("expected a finite float32 value")
            n = float(s)
            try:
                encoded = struct.pack("<f", n)
                rounded = struct.unpack("<f", encoded)[0]
            except (OverflowError, struct.error):
                raise ValueError("value is outside finite float32 range") from None
            if not math.isfinite(n) or not math.isfinite(rounded) or n != 0 and rounded == 0:
                raise ValueError("value is outside finite float32 range")
        else:
            if re.fullmatch(r"0[xX][0-9a-fA-F]+", s):
                n = int(s[2:], 16)
            elif re.fullmatch(r"-?[0-9]+", s):
                n = int(s)
            else:
                raise ValueError("expected a whole decimal or nonnegative hexadecimal integer")
            lo, hi = _BOUNDS[ty]
            if not lo <= n <= hi:
                raise ValueError("integer does not fit " + ty)
            encoded = n.to_bytes(cap, "little", signed=ty.startswith("i"))
        if input_.get("min") is not None and n < input_["min"] or input_.get("max") is not None and n > input_["max"]:
            raise ValueError("choice is outside the declared range")
    if len(encoded) > cap:
        raise ValueError("encoded choice exceeds the payload capacity")
    return encoded


def _semantics(d: dict, errors: list) -> None:
    def error(path, message):
        errors.append(DomainIssue(tuple(path), message))

    def gate(g, path):
        for key in ("peripheral", "register"):
            if not g[key]:
                error(path + (key,), "precondition " + key + " must not be empty")
        mask, equals = _uint(g["mask"], 32), _uint(g["equals"])
        if mask is None:
            error(path + ("mask",), "expected an unsigned 32-bit decimal or hexadecimal string")
        if equals is None or mask is not None and equals & ~mask:
            error(path + ("equals",), "equals must be an unsigned integer contained in the mask")

    def structured(s, path, actions=False):
        limit = 128 if actions else 256
        if not _identifier(s["symbol"], limit):
            error(path + ("symbol",), f"expected a C identifier of at most {limit} bytes")
        if not actions and not s["steps"]:
            error(path + ("steps",), "sampled structured sources need 1 to 16 explicit steps")
        for j, step in enumerate(s["steps"]):
            if step["op"] == "member" and not _identifier(step["name"], limit):
                error(path + ("steps", j, "name"), f"expected a C identifier of at most {limit} bytes")

    if not d["id"] or "." not in d["id"]:
        error(("id",), "use a stable reverse-DNS identifier, such as com.example.device")
    known = set()
    ss = d.get("sampled_state")
    if ss is not None:
        if ss.get("precondition") is not None:
            gate(ss["precondition"], ("sampled_state", "precondition"))
        channels = {}
        for i, ch in enumerate(ss["channels"]):
            p = ("sampled_state", "channels", i)
            name, ty, safety = ch["name"], ch.get("ty", "u32"), ch.get("safety", "read-safe")
            if not name or name in known:
                error(p + ("name",), "sampled channel names must be nonempty and unique")
            known.add(name)
            channels[name] = ch
            if ch.get("precondition") is not None:
                gate(ch["precondition"], p + ("precondition",))
            if safety == "conditional" and ch.get("precondition") is None and ss.get("precondition") is None:
                error(p + ("safety",), "conditional input needs a channel or domain precondition")
            register = ch.get("peripheral") is not None or ch.get("register") is not None
            count = int(register) + sum(ch.get(k) is not None for k in ("symbol", "ring", "structured"))
            if count > 1:
                selected = (["register"] if register else []) + [k for k in ("symbol", "ring", "structured") if ch.get(k) is not None]
                error(p, "conflicting sources: " + ", ".join(selected) + "; select exactly one source")
            if register:
                for key in ("peripheral", "register"):
                    if ch.get(key) is None:
                        error(p + (key,), "register sources require both peripheral and register")
            if count == 0 and safety in ("safe", "read-safe", "conditional"):
                error(p, "readable channel needs a source")
            if ch.get("offset_bytes", 0) > 0 and ch.get("symbol") is None:
                error(p + ("offset_bytes",), "offset_bytes only applies to symbol channels")
            ring = ch.get("ring")
            if ring is not None:
                mask = _uint(ring["own_mask"], 32)
                rp = p + ("ring",)
                if not ring["symbol"]:
                    error(rp + ("symbol",), "ring symbol must not be empty")
                if not ring["entries"]:
                    error(rp + ("entries",), "ring must contain at least one entry")
                if ring["stride_bytes"] < 4:
                    error(rp + ("stride_bytes",), "stride must fit a four-byte ownership word")
                if ring["own_word_offset"] + 4 > ring["stride_bytes"]:
                    error(rp + ("own_word_offset",), "ownership offset plus four bytes exceeds stride_bytes")
                if ring["entries"] * ring["stride_bytes"] > 65535:
                    error(rp + ("entries",), f"entries * stride_bytes is {ring['entries'] * ring['stride_bytes']}; ring read must fit 65535 bytes")
                if not mask:
                    error(rp + ("own_mask",), "ownership mask must be a nonzero unsigned 32-bit decimal or hexadecimal string")
                if ty != "u32":
                    error(p + ("ty",), "ring occupancy requires u32")
            if ch.get("structured") is not None:
                structured(ch["structured"], p + ("structured",))
                if ch.get("counter") == "clear_on_read":
                    error(p + ("counter",), "structured RAM observations cannot be consuming reads")
        group_ids, grouped = set(), set()
        for i, group in enumerate(ss.get("ram_read_groups", [])):
            p = ("sampled_state", "ram_read_groups", i)
            if not group["id"] or group["id"] in group_ids:
                error(p + ("id",), "RAM group IDs must be nonempty and unique")
            group_ids.add(group["id"])
            if group["max_bytes"] % 4:
                error(p + ("max_bytes",), "RAM group bound must be a multiple of four")
            for j, name in enumerate(group["channels"]):
                ch = channels.get(name)
                if ch is None:
                    error(p + ("channels", j), "unknown sampled input: " + name)
                elif name in grouped or (ch.get("symbol") is None and ch.get("structured") is None) or any(ch.get(k) is not None for k in ("ring", "register", "peripheral")) or ch.get("counter") == "clear_on_read" or ch.get("safety", "read-safe") not in ("read-safe", "safe", "conditional"):
                    reason = "input already belongs to a RAM group" if name in grouped else "input must be a readable symbol/structured RAM source without clear_on_read semantics"
                    error(p + ("channels", j), name + ": " + reason)
                grouped.add(name)
    derived_names = set()
    for i, item in enumerate(d.get("derived", [])):
        p = ("derived", i)
        name, source = item["name"], item["source"]
        if not name or name == source or name in derived_names or name in known:
            error(p + ("name",), "derived names must be nonempty, unique and different from their source")
        if ss is not None and source not in known:
            error(p + ("source",), "unknown source; declare derived dependencies before their consumers")
        if item["type"] == "field" and _uint(item["mask"]) is None:
            error(p + ("mask",), "expected an unsigned 64-bit decimal or hexadecimal string")
        if item["type"] == "map":
            if not item["table"]:
                error(p + ("table",), "map table must not be empty")
            for key in item["table"]:
                try:
                    valid_key = bool(re.fullmatch(r"[+-]?[0-9]+", key)) and -(1 << 63) <= int(key) < 1 << 63
                except ValueError:
                    valid_key = False
                if not valid_key:
                    error(p + ("table", key), "map key must be a signed 64-bit decimal integer")
        derived_names.add(name)
        known.add(name)
    verdict_ids = set()
    for i, rule in enumerate(d.get("verdicts", [])):
        p = ("verdicts", i)
        if not rule["id"] or rule["id"] in verdict_ids:
            error(p + ("id",), "verdict IDs must be nonempty and unique")
        verdict_ids.add(rule["id"])
        when = rule["when"]
        if ss is not None:
            for key in ("channel", "while_advances", "reference"):
                if key in when and when[key] not in known:
                    error(p + ("when", key), "unknown input channel: " + when[key])
            for j, name in enumerate(rule.get("evidence", [])):
                if name not in known:
                    error(p + ("evidence", j), "unknown evidence channel: " + name)
    action_ids = set()
    for i, action in enumerate(d.get("actions", [])):
        p = ("actions", i)
        if not _identifier(action["id"], 128) or action["id"] in action_ids:
            error(p + ("id",), "action IDs must be unique C identifiers of at most 128 bytes")
        action_ids.add(action["id"])
        if not action["label"]:
            error(p + ("label",), "action label must not be empty")
        input_, target = action["input"], action["target"]
        ty, kind = input_["ty"], target["kind"]
        buffer = ty in ("bytes", "utf8")
        input_errors = len(errors)
        cap = input_.get("max_bytes") if buffer else _CAPACITY[ty]
        if buffer and (cap is None or not 1 <= cap <= 256):
            error(p + ("input", "max_bytes"), "bytes/utf8 require max_bytes in 1 to 256")
        if not buffer and input_.get("max_bytes") is not None:
            error(p + ("input", "max_bytes"), "max_bytes only applies to bytes/utf8")
        if ty == "utf8" and input_.get("termination") not in ("none", "nul"):
            error(p + ("input", "termination"), "utf8 requires an explicit none or nul termination policy")
        if ty != "utf8" and input_.get("termination") is not None:
            error(p + ("input", "termination"), "termination only applies to utf8")
        for key in ("min", "max"):
            value = input_.get(key)
            if value is None:
                continue
            if buffer or ty == "bool":
                error(p + ("input", key), "numeric bounds do not apply to bytes/utf8/bool")
            else:
                lo, hi = _BOUNDS[ty]
                if not lo <= value <= hi or ty != "f32" and value % 1:
                    error(p + ("input", key), "bound must fit the declared numeric type")
        if input_.get("min") is not None and input_.get("max") is not None and input_["min"] > input_["max"]:
            error(p + ("input",), "input minimum exceeds maximum")
        if len(errors) == input_errors:
            encoded_values = set()
            for j, choice in enumerate(input_.get("choices", [])):
                cp = p + ("input", "choices", j)
                if not choice["label"]:
                    error(cp + ("label",), "choice label must not be empty")
                try:
                    encoded = _encode_choice(input_, choice["value"])
                    if encoded in encoded_values:
                        error(cp + ("value",), "choice duplicates another encoded value")
                    encoded_values.add(encoded)
                except ValueError as exc:
                    error(cp + ("value",), str(exc))
        expected = {"ram": ("readback",), "register": ("readback", "transport"), "mailbox": ("application",)}[kind]
        if action["completion"] not in expected:
            error(p + ("completion",), "completion for " + kind + " must be " + " or ".join(expected))
        if kind == "ram":
            structured(target["source"], p + ("target", "source"), actions=True)
            if target.get("execution", "halted") == "running_scalar" and (buffer or any(s["op"] == "deref" for s in target["source"]["steps"])):
                error(p + ("target", "execution"), "running_scalar requires a fixed scalar path without pointer dereferences")
        elif kind == "register":
            for key in ("peripheral", "register", "field"):
                if target.get(key) == "":
                    error(p + ("target", key), "SVD name must not be empty")
            if ty not in ("u8", "u16", "u32", "bool"):
                error(p + ("input", "ty"), "register actions accept only u8, u16, u32 or bool")
        else:
            if not _identifier(target["symbol"], 128):
                error(p + ("target", "symbol"), "mailbox symbol must be a C identifier of at most 128 bytes")
            if not target["operation"]:
                error(p + ("target", "operation"), "mailbox operation must be nonzero")
            if not 100 <= target["timeout_ms"] <= 5000:
                error(p + ("target", "timeout_ms"), "mailbox timeout must be between 100 and 5000 ms")
            if cap is not None and cap > 64:
                error(p + ("input", "max_bytes"), "mailbox payload capacity must not exceed 64 bytes")
    for i, section in enumerate((d.get("presentation") or {}).get("sections", [])):
        p = ("presentation", "sections", i)
        if not section["title"]:
            error(p + ("title",), "section title must not be empty")
        for j, field in enumerate(section["fields"]):
            fp = p + ("fields", j)
            if field["channel"] not in known:
                error(fp + ("channel",), "unknown sampled or derived channel: " + field["channel"])
            if not field["label"]:
                error(fp + ("label",), "field label must not be empty")
            for key, names in (("reference", known), ("action", action_ids)):
                if field.get(key) is not None and field[key] not in names:
                    error(fp + (key,), "unknown " + key + ": " + field[key])
            for k, group in enumerate(field.get("group", [])):
                if not group.strip() or len(group.encode("utf-8")) > 128:
                    error(fp + ("group", k), "group labels must contain text and fit 128 UTF-8 bytes")
        for j, name in enumerate(section.get("checks", [])):
            if name not in verdict_ids:
                error(p + ("checks", j), "unknown verdict ID: " + name)
    for i, param in enumerate((d.get("instrument") or {}).get("params", [])):
        p = ("instrument", "params", i)
        if not re.fullmatch(r"[A-Za-z0-9_-]+", param["key"]):
            error(p + ("key",), "parameter keys use letters, digits, underscores and hyphens")
        if param.get("type", "string") == "number" and param.get("default", ""):
            try:
                text = param["default"].strip()
                if "_" in text:
                    raise ValueError()
                float(text)
            except ValueError:
                error(p + ("default",), "numeric parameter default must be numeric text")


def validate_domain(descriptor: Any) -> DomainValidationResult:
    """Check a decoded JSON value without changing it or applying defaults.

Structural errors are reported first. When the structure is valid, semantic
checks report bad references, duplicate names, policy conflicts and bounds.
Successful validation cannot establish ELF/SVD compatibility or target safety.
"""
    errors: list = []
    try:
        _shape(descriptor, SCHEMA, (), errors)
        if not errors:
            _semantics(descriptor, errors)
    except RecursionError:
        errors.append(DomainIssue((), "descriptor nesting is too deep or contains a reference cycle"))
    return DomainValidationResult(tuple(errors))


class _ObjectPairs(list):
    pass


@dataclass
class _NonFinite:
    token: str


def _decoded(value, path, errors):
    if isinstance(value, _ObjectPairs):
        result = {}
        for key, item in value:
            if key in result:
                errors.append(DomainIssue(path + (key,), "duplicate JSON object key; keep one value for this field"))
            result[key] = _decoded(item, path + (key,), errors)
        return result
    if isinstance(value, list):
        return [_decoded(item, path + (i,), errors) for i, item in enumerate(value)]
    if isinstance(value, _NonFinite):
        errors.append(DomainIssue(path, value.token + " is not a valid JSON number; use a finite number"))
        return None
    return value


def parse_domain(text: str) -> dict:
    """Parse strict JSON and return the validated, unmodified descriptor.

Raises DomainValidationError for JSON syntax and schema/semantic errors.
Duplicate keys, NaN, Infinity and invalid Unicode are rejected.
"""
    try:
        raw = json.loads(text, object_pairs_hook=_ObjectPairs, parse_constant=_NonFinite)
        errors: list = []
        descriptor = _decoded(raw, (), errors)
    except (ValueError, RecursionError) as exc:
        raise DomainValidationError(DomainValidationResult((DomainIssue((), str(exc)),))) from exc
    DomainValidationResult(tuple(errors)).raise_for_errors()
    validate_domain(descriptor).raise_for_errors()
    return descriptor


def load_domain(path: Union[str, Path]) -> dict:
    """Load a UTF-8 file. Raises OSError for IO, DomainValidationError for content."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except UnicodeError as exc:
        raise DomainValidationError(DomainValidationResult((DomainIssue((), "file must be UTF-8"),))) from exc
    return parse_domain(text)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Validate Trace Domain v1 JSON offline (no app, probe or target required).")
    parser.add_argument("files", nargs="*", help=".vadomain or JSON files to check")
    parser.add_argument("--json", action="store_true", help="print a machine-readable result array")
    parser.add_argument("--schema", action="store_true", help="print the structural JSON Schema and exit")
    args = parser.parse_args(argv)
    if args.schema:
        if args.files or args.json:
            parser.error("--schema cannot be combined with files or --json")
        print(json.dumps(domain_schema(), indent=2))
        return 0
    if not args.files:
        parser.error("provide at least one descriptor file")
    reports, code = [], 0
    for filename in args.files:
        report = {"file": filename, "valid": False, "domain_api": DOMAIN_API_VERSION, "errors": []}
        try:
            descriptor = load_domain(filename)
            report.update(valid=True, id=descriptor["id"])
        except DomainValidationError as exc:
            report["errors"] = [e.as_dict() for e in exc.errors]
            code = max(code, 1)
        except OSError as exc:
            report["errors"] = [DomainIssue((), "cannot read file: " + str(exc)).as_dict()]
            code = 2
        reports.append(report)
    if args.json:
        print(json.dumps(reports, indent=2))
    else:
        for report in reports:
            print(("VALID" if report["valid"] else "INVALID") + ": " + report["file"])
            for issue in report["errors"]:
                print("  " + issue["path"] + ": " + issue["message"])
    return code


if __name__ == "__main__":
    sys.exit(main())
