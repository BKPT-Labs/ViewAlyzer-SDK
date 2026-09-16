# Creating custom Trace Domains

Trace Domain format **v1**. A practical authoring guide and complete field reference for **BKPT Debug** and **ViewAlyzer**.

A Trace Domain turns your knowledge of an embedded subsystem into a reusable `.vadomain` JSON file. It describes what can be observed, how values should be interpreted, which conditions deserve attention, and how to present the evidence. Engineers and AI-assisted tools can use this guide to build, validate and share those files.

You can make a domain for one Ethernet controller IP revision, a particular DMA descriptor layout, an application object in RAM, a USB middleware stack, an RTOS queue or a bench instrument. A useful domain does not need to cover an entire chip family. Its value comes from describing a specific subsystem accurately and stating exactly where it applies.

## What a good community domain promises

1. **A clear scope.** Name the supported controller, silicon revisions, middleware versions, firmware build options and memory layouts. A matching chip name alone does not establish that a middleware object has the expected meaning.
2. **Observable evidence.** Choose registers and firmware values that answer real diagnostic questions. Prefer existing cumulative counters when a short-lived state could disappear between polls.
3. **Honest interpretation.** An unavailable value is missing evidence. A sampled state is an observation at a particular time. Neither should be turned into a fabricated zero, an exact transition timestamp or proof that no fault occurred.
4. **Useful explanations.** Findings should say what was observed, what it may mean, and what the engineer should inspect next. Include supporting channels instead of claiming a root cause that the observations cannot prove.
5. **Reproducible compatibility.** Ship a small example, validated JSON, the required ELF/SVD assumptions and the conditions you tested. Keep hardware-specific vocabulary in your domain so other experts can contribute domains of their own.

The file declares data and supported operations. It cannot add arbitrary formulas, scripts, function calls or a new renderer. Optional typed actions must be declared separately from observations. An optional external-instrument program is executable software distributed alongside a package; it is not JSON logic.

## Start here

| Goal | Read next |
|---|---|
| Make a first domain | [A first working domain](#a-first-working-domain) |
| Check a file without hardware | [Validate with the Python SDK](#validate-with-the-python-sdk) |
| Observe controller registers | [Sampled inputs](#sampled-inputs) and [Ethernet example](#ethernet-example) |
| Follow firmware objects and pointers | [Structured sources](#structured-sources) and [Middleware example](#middleware-example) |
| Add editable controls | [Typed actions](#typed-actions) |
| Generate a domain with an AI tool | [Authoring with AI](#authoring-with-ai) |
| Look up every supported property | [File format and compatibility](#file-format-and-compatibility) and [Validation boundaries](#validation-boundaries) |

## Choose the observation contract first

Before writing JSON, make a short list of the questions your domain answers. For each value, identify its source, type, units, valid states and read effects.

| Source | Appropriate use | Prerequisites |
|---|---|---|
| `peripheral` + `register` | Controller status, hardware counters, configuration bits | Correct device SVD, exact register names and safe read semantics |
| `symbol` | A scalar already maintained by firmware | The matching ELF and correct scalar type; a nonzero member offset must match that exact build |
| `structured` | RAM objects, middleware structs, pointers and fixed arrays | Matching ELF with DWARF debug information and supported writable RAM allocations |
| `ring` | Occupancy of fixed-stride descriptors | Matching ELF, verified array extent, ownership word and mask |
| Existing recorded channels | Firmware instrumentation or imported instrument data | Channel names, namespaces and units from the producing recorder |

The same peripheral IP can appear behind different register names or addresses in different MCUs. SVD references let the application resolve addresses. They do not make an incorrect register interpretation portable. Likewise, named DWARF paths adapt to supported layout changes, but do not establish semantic compatibility between middleware releases.

Record compatibility notes in a companion README. The descriptor has no general-purpose `compatibility`, `description`, `license`, `url` or arbitrary metadata property at the top level. Unknown properties are rejected. Use the supported channel `note`/`reason`, section `description` and finding `explain` fields where appropriate.

## A first working domain

### Step 1: identify two existing firmware counters

This example assumes the matching firmware ELF contains two unsigned 32-bit globals named `packets_completed` and `packet_errors`. Use actual retained symbols from your firmware. You do not need to add recorder calls to observe existing RAM values.

These declarations illustrate the expected types; your firmware remains responsible for maintaining the counters:

```c
#include <stdint.h>
uint32_t packets_completed;
uint32_t packet_errors;
```

### Step 2: save the complete descriptor

Save the following as `packet-counter.vadomain`. JSON uses double quotes and has no comments or trailing commas. The filename is for people; `id` is the stable identity used by the applications.

```json
{
  "id": "com.example.packet-counter",
  "name": "Packet Counter",
  "version": "1.0.0",
  "domain_api": 1,
  "publisher": "Example",
  "sampled_state": {
    "rate_hz": 20,
    "channels": [
      {"name": "packets.completed", "symbol": "packets_completed", "ty": "u32", "counter": "wrapping"},
      {"name": "packets.errors", "symbol": "packet_errors", "ty": "u32", "counter": "wrapping"}
    ]
  },
  "derived": [
    {"type": "rate", "name": "packets.rate", "source": "packets.completed", "unit": "packets/s"}
  ],
  "channels": [
    {"match": {"ns": "poll", "name_prefix": "packets."}, "display": "counter", "unit": "packets", "interpolation": "step"},
    {"match": {"ns": "derived", "name": "packets.rate"}, "display": "graph", "unit": "packets/s", "graph": true}
  ],
  "verdicts": [
    {"id": "packet-errors", "name": "Packet errors increased", "severity": "warning",
     "when": {"type": "counter_advanced", "channel": "packets.errors"},
     "explain": "The firmware error counter advanced. Inspect the driver's error reason and compare with traffic load.",
     "evidence": ["packets.errors", "packets.rate"]}
  ],
  "presentation": {
    "sections": [
      {"title": "Packets", "view": "cards", "fields": [
        {"channel": "packets.completed", "label": "Completed", "unit": "packets"},
        {"channel": "packets.errors", "label": "Errors", "unit": "packets"},
        {"channel": "packets.rate", "label": "Throughput", "unit": "packets/s"}
      ], "checks": ["packet-errors"]}
    ]
  }
}
```

The sampled inputs provide the evidence. The derived rate uses the completed counter. The finding observes error-counter advances and includes traffic rate as context. The presentation supplies readable cards; channel rules supply chart formatting.

### Step 3: validate before loading

Install the SDK with the Python interpreter you use for your project:

```bash
python -m pip install --upgrade viewalyzer-sdk
python -m viewalyzer_sdk.domains packet-counter.vadomain
```

A valid file prints `VALID: packet-counter.vadomain` and exits with status zero. Validation needs no ViewAlyzer executable, probe, license activation or connected board. The validator is included in ViewAlyzer SDK 1.4.0 and later.

### Step 4: load it in BKPT Debug

1. Open a project and select the ELF from the firmware build running on the target.
2. Run **BKPT Debug: Install Custom Trace Domain...** from the Command Palette, or open **BKPT Debug → Views → Trace Domains** and choose **Install custom trace domain...**.
3. Select the file, then choose **Enable for this workspace**. Installation and workspace activation are separate steps.
4. Start debugging and run the target. Inspect channel availability before interpreting values.
5. Use **Graph** to show a value in **Traces**. Use the domain's **Settings** to choose inputs and requested polling rate. Findings appear in **Trace Domains** when valid observations satisfy their rules.

This RAM-only example needs no SVD. Register sources and register preconditions do need the matching SVD. Disabling a required input also makes dependent derived values and checks unavailable; hiding its graph does not disable sampling.

### Step 5: load it in ViewAlyzer

1. In the sidebar's **Trace Domains** section choose **Load Domain File**, select the `.vadomain`, and enable it.
2. Select the matching ELF, target and probe in the capture configuration. Add the SVD when the domain uses registers.
3. For polling-only capture, turn **Software Trace** off. Enable it when combining domain polling with supported firmware recorder data.
4. Start capture. The domain's **gear** opens its cards and tables; its **pencil** opens the descriptor editor.
5. Stop capture to review derived traces and findings in **Analyzer**. Saved observations remain read-only.

Each application keeps its own installation and enable settings. A valid descriptor does not select a target, supply an ELF or claim a probe. Use one application at a time with a probe.

### Step 6: exercise a known condition

Observe a healthy interval, then exercise an error case through your application's normal test controls. Confirm the raw error counter changes, the finding names the expected observation, and the evidence agrees. Check behavior when a symbol or input is unavailable. Do not mistake a successful schema check for a hardware validation result.

## Validate with the Python SDK

The installed command and Python module have the same interface:

```bash
viewalyzer-domain packet-counter.vadomain
python -m viewalyzer_sdk.domains packet-counter.vadomain queue.vadomain
python -m viewalyzer_sdk.domains --json packet-counter.vadomain
python -m viewalyzer_sdk.domains --schema > trace-domain.schema.json
```

Pass one or more filenames. Both `.vadomain` and `.json` content are accepted. There is no silent repair or version conversion. The validator never reads target memory, sends an action or starts an instrument program.

| Exit status | Meaning |
|---|---|
| `0` | Every supplied file passed validation |
| `1` | At least one file has invalid JSON, an invalid field or an incompatible relationship |
| `2` | A file could not be read, or the command arguments were invalid |

If a batch contains both unreadable and invalid files, exit status `2` takes precedence. Every readable file is still checked.

An error names the field and the reason. For example:

```text
INVALID: queue.vadomain
  $["sampled_state"]["rate_hz"]: value 0 must be greater than 0
  $["sampled_state"]["rate_hzz"]: unknown field; did you mean 'rate_hz'?
```

For machine consumers, `--json` returns an array with one entry per file. Each entry contains `file`, `valid`, `domain_api` (the validator's supported format) and `errors`; a valid entry also contains `id`. Each error contains a readable `path`, a standard JSON Pointer `pointer`, and `message`. Array indices are zero-based. An empty pointer refers to the whole document. JSON syntax errors include the decoder's line and column.

```json
[
  {
    "file": "queue.vadomain",
    "valid": false,
    "domain_api": 1,
    "errors": [
      {"path": "$[\"derived\"][0][\"source\"]", "pointer": "/derived/0/source", "message": "unknown source; declare derived dependencies before their consumers"}
    ]
  }
]
```

Use the Python API when generating descriptors:

```python
from viewalyzer_sdk.domains import (
    DOMAIN_API_VERSION,
    DomainValidationError,
    domain_schema,
    load_domain,
    parse_domain,
    validate_domain,
)

# Read UTF-8 JSON, validate it, and return the original decoded dictionary.
try:
    descriptor = load_domain("packet-counter.vadomain")
except DomainValidationError as exc:
    for issue in exc.errors:
        print(issue.pointer, issue.message)

# Validate an object you generated in memory, without modifying it.
result = validate_domain({
    "id": "com.example.monitor", "name": "Monitor", "version": "1.0.0",
    "domain_api": DOMAIN_API_VERSION,
})
if not result.valid:
    for issue in result.errors:
        print(issue.path, issue.message)
result.raise_for_errors()

# parse_domain(text) checks a JSON string; domain_schema() returns a fresh
# dictionary containing the structural JSON Schema for editor integrations.
```

| Python API | Result and errors |
|---|---|
| `validate_domain(value)` | `DomainValidationResult`; accepts a decoded JSON value, with `valid`, `errors` and `raise_for_errors()` |
| `parse_domain(text)` | Validated dictionary; raises `DomainValidationError` for content errors |
| `load_domain(path)` | Reads UTF-8 and returns the validated dictionary; content errors raise `DomainValidationError`, filesystem errors raise `OSError` |
| `domain_schema()` | A fresh JSON Schema dictionary; no shared mutable state |
| `DomainIssue` | `parts`, `path`, `pointer`, `message`, and `as_dict()` |
| `DomainValidationError` | A `ValueError` with `result` and `errors` describing the failed validation |
| `DOMAIN_API_VERSION` | Current descriptor format supported by this SDK validator |

The exported JSON Schema checks structure, property types, required fields and many local bounds. Use the SDK validator as well for channel references, ordering, duplicate identifiers, read policies, ring extents, action encodings and other cross-field rules. Configure an editor's schema association externally: `$schema` is not an accepted property inside the descriptor.

Structural errors are reported first. Once those are fixed, the checker reports semantic relationships. Duplicate JSON keys, non-finite numbers and invalid Unicode are rejected. Omitted defaults are interpreted for validation but are not inserted into the returned dictionary. Prefer omitting unused optional fields to writing `null`; only nullable fields accept explicit `null`.

## File format and compatibility

Save the descriptor as UTF-8 JSON with the `.vadomain` extension. Use valid JSON: no comments or trailing commas. A package is a folder containing the descriptor and any supporting files, such as an external instrument recorder.

`domain_api: 1` selects the complete version-one format described here: sampled sources, pointer/member/array traversal, derived channels, findings, cards and tables, graph choices, RAM read groups, typed actions and instrument declarations. Omitting `domain_api` also selects version one. Always include it in shared files so the required format is explicit.

`version` is your domain's own release string. It is independent of `domain_api`, the SDK package version and the applications' version numbers. Increment your domain version when you publish changes. Change `domain_api` only when adopting a new documented format supported by the reader. Do not change the number to bypass a compatibility error.

A descriptor is rejected if it uses an unsupported API version, an unknown field, or an invalid value. Check the error shown when installing the file, or run `viewalyzer-cli domains` to inspect loading errors. For a version error, use a compatible application version or a descriptor supplied for your installed version.

## Top level

This is a complete minimal descriptor. Add the sections your domain needs; omit unused sections.

```json
{
    "id": "com.example.device",
    "name": "Device Monitor",
    "version": "1.0.0",
    "domain_api": 1,
    "publisher": "Example"
}
```

| Field | Required | Meaning |
|---|---|---|
| `id` | Yes | Stable domain identifier. Use a reverse-DNS name such as `com.example.device`. ViewAlyzer uses it to identify installed versions and overrides. |
| `name` | Yes | Display name in domain lists and findings. |
| `version` | Yes | Your descriptor's version string, such as `1.0.0`. |
| `domain_api` | No; defaults to `1` | Descriptor format version. |
| `publisher` | No | Author or organization shown in the domain list. |
| `channels` | No | Rules for channel units, grouping, names, and display style. |
| `sampled_state` | No | Registers and firmware values to observe through the debug probe. |
| `derived` | No | Channels calculated from observed values. |
| `verdicts` | No | Conditions that produce findings with explanations and evidence. |
| `presentation` | No | Collapsible cards or tables grouping sampled and derived values. |
| `actions` | No | Explicit typed operations, executable only in BKPT Debug. |
| `instrument` | No | External instrument recorder to run with a ViewAlyzer capture. |
| `series_kinds` | No | Additional event kinds whose numeric values should appear as series. |
| `namespaces` | No | Additional namespaces whose objects should be treated as data channels. |

## Channel display rules

Each entry in `channels` selects channels by namespace and name. The first matching rule wins, so put exact-name rules before broader prefix rules. Styling applies to the channel's presentation; recorded sample values are preserved.

This rule displays a sampled ring occupancy as a level bar:

```json
{
    "match": { "ns": "poll", "name": "eth.rx_ring" },
    "unit": "desc",
    "display": "level",
    "graph": true,
    "group": "DMA",
    "min": 0,
    "max": 4,
    "warn": 3,
    "interpolation": "step"
}
```

| Field | Meaning |
|---|---|
| `match.ns` | Channel namespace; see the table below. |
| `match.name` | Exact channel name. Takes precedence over `name_prefix` within the same rule. |
| `match.name_prefix` | Selects names beginning with this text. For example, `eth.` selects Ethernet channels named with that prefix. Omit both name fields to select the whole namespace. |
| `unit` | Suffix shown beside values, such as `pkts`, `Hz`, or `m/s2`. |
| `display` | Display style: `graph`, `bar`, `level`, `scatter`, `impulse`, `enum`, `gauge`, `counter`, `table`, `histogram`, `toggle`, `register`, `angle`, `task`, or `isr`. Overrides the firmware's display hint. |
| `graph` | Optional boolean default Graph choice. A saved user choice takes precedence. Does not affect sampling or history. |
| `group` | String label grouping charts. Distinct from a presentation field's nested `group` array. |
| `min`, `max`, `warn` | Fixed scale and warning threshold for a `level` bar. |
| `names` | Value labels, such as `{"0": "Stopped", "1": "Running"}`. Per-channel labels chosen in the app take precedence. |
| `interpolation` | `step` or `linear`. Use `step` for sampled states and counters so the chart holds each observed value until the next sample. A per-chart selection takes precedence. |

| Namespace | Values |
|---|---|
| `poll` | Domain inputs and manually polled firmware symbols. |
| `derived` | Values calculated by the `derived` section. |
| `user_trace` | User traces emitted by the firmware recorder. |
| `external` | Imported or captured instrument samples. |

### Graph defaults

In both apps, each value's **Graph** control chooses whether to display its history in Trace/Traces. It stays available in cards/tables either way. The first matching channel rule wins; if that rule omits `graph`, use the automatic default rather than searching later rules.

With `presentation`, declared counters, ring occupancy and derived rates start graphed; other fields start in cards/tables. Without `presentation`, the legacy enabled graph default is preserved. Use explicit `graph: true` for motion or other useful signals, and `graph: false` for addresses, configuration codes or duplicate representations. Users can override any default. **Reset graphs** restores these defaults without changing expansion or sampling settings. Preferences are separate in each app; BKPT Debug also scopes them to the workspace.

## Sampled inputs

`sampled_state` tells the debug probe which target values to read while the firmware runs. Register sources use the device's SVD file. Firmware symbols, structure members, and rings use the ELF from the firmware build. Domains that only read firmware memory need no SVD unless they also declare register preconditions.

For desktop domain-only capture, enable an applicable sampled domain and turn **Software Trace** off. No recorder control block is required. Enable Software Trace for combined recorder capture. RAM snapshots still require recorder firmware.

This section observes an existing firmware counter without adding logging calls:

```json
{
    "sampled_state": {
        "rate_hz": 20,
        "channels": [
            {
                "name": "sensor.samples",
                "symbol": "imu_sample_count",
                "ty": "u32",
                "counter": "wrapping"
            }
        ]
    }
}
```

| Field | Meaning |
|---|---|
| `svd_device` | Device SVD name, without `.svd`. ViewAlyzer searches `VA_SVD_DIR`, its `svd/` folder, and STM32CubeCLT installations. Use the exact peripheral and register names in that file. |
| `target_device_prefix` | List of target-name prefixes to which the domain applies, such as `["STM32U575"]`. An empty list permits any target. A mismatch skips sampling with a reason. |
| `rate_hz` | Requested samples per second; defaults to `20`. Must be positive and no greater than `1000`. Actual acquisition can be slower. |
| `coalesce_gap_bytes` | Maximum gap between registers that may be included in one combined read. Defaults to `0`, allowing only adjacent registers to be combined. A larger value permits reading intervening bytes; use it only when those bytes are safe to read. |
| `precondition` | Register condition required for every input in this section. |
| `channels` | List of sampled inputs. |

### Input fields

Give each input a unique `name` and one source: a `peripheral`/`register` pair, a `symbol`, a `ring`, or a `structured` path.

| Field | Meaning |
|---|---|
| `name` | Channel name used by display rules, derived channels, and findings. |
| `peripheral`, `register` | Exact names in the selected SVD. Supply both for a register source. |
| `symbol` | Global symbol in the matching firmware ELF. |
| `ty` | Scalar type: `u8`, `u16`, `u32`, `i8`, `i16`, `i32`, or `f32`. Defaults to `u32`. |
| `offset_bytes` | Byte offset within a `symbol`; defaults to `0`. The selected value must fit within that symbol's size. |
| `ring` | Descriptor-ring source whose value is the number of matching entries. See Rings below. |
| `structured` | Named structure members, explicit pointer steps and fixed-array indices. |
| `counter` | `monotonic`, `wrapping`, or `clear_on_read`. Omit for values that are not counters. |
| `safety` | Read policy; defaults to `read-safe`. See Read policies below. |
| `precondition` | Additional register condition required for this input. |
| `reason` | Explanation shown when an input cannot be sampled. |
| `note` | Context for interpreting the value, such as cache behavior or counter reset conditions. |

Use `monotonic` for an increasing total. Use `wrapping` for a counter that rolls over at the width of `ty`, so its rollover can be accounted for in comparisons. With `clear_on_read`, each sample is the increment since the previous read; use `value_at_or_above` to detect a nonzero interval. Reading a clear-on-read counter consumes its value, so only observe it when doing so is compatible with the firmware's use of that counter.

For a fixed member offset, verify the layout against the exact firmware build. This example reads a 16-bit member two bytes into `lwip_stats`:

```json
{
    "name": "lwip.link_recv",
    "symbol": "lwip_stats",
    "offset_bytes": 2,
    "ty": "u16",
    "counter": "wrapping"
}
```

### Preconditions

A precondition tests `(register & mask) == equals`. Both `mask` and `equals` are hexadecimal or decimal strings. Use a condition to check, for example, that a peripheral clock is enabled before reading its registers.

```json
{
    "peripheral": "RCC_S",
    "register": "RCC_AHB5ENR",
    "mask": "0x02000000",
    "equals": "0x02000000",
    "description": "Ethernet peripheral clock is disabled"
}
```

The register names and mask above are device-specific. Replace them with values from your device's SVD and reference manual. A failed condition makes the affected inputs unavailable and displays `description`.

In ViewAlyzer captures, preconditions are evaluated at capture start. Start capture after the required peripheral initialization; a condition that fails at startup disables its inputs for that capture.

### Read policies

| `safety` | Behavior |
|---|---|
| `read-safe` or `safe` | Read when applicable preconditions pass. |
| `conditional` | Read only with a declared precondition, either on the input or on `sampled_state`. |
| `intrusive` | Not sampled. Use `reason` to explain how a read would disturb the target. |
| `unsafe` | Not sampled. Use `reason` to explain the risk to target state. |
| `unsupported` | Not sampled. Use `reason` to explain why the value is inaccessible through the debug port. |

Sampling never executes a write action. Typed actions are a separate, explicit BKPT Debug operation; desktop views remain read-only. Reads can still have device-specific effects; choose sources and conditions using the device documentation and the firmware's ownership of each resource.

### Rings

A ring source reads a fixed-size array located by an ELF symbol and counts entries whose ownership bits match the requested state.

```json
{
    "name": "eth.rx_ring",
    "ring": {
        "symbol": "DMARxDscrTab",
        "entries": 4,
        "stride_bytes": 24,
        "own_word_offset": 12,
        "own_mask": "0x80000000",
        "count_set": false
    },
    "note": "Receive descriptors awaiting reclamation by the application"
}
```

`entries` is the array length; `stride_bytes` is the size of each entry. `own_word_offset` locates the ownership word within each entry. `count_set: true` counts entries where `word & own_mask` is nonzero; `false` counts entries where it is zero. The example's layout and ownership convention must match your firmware.

The ring requires a matching ELF, and its total size must fit within 65,535 bytes. Set the entry count, stride, offset, and mask to match the application's actual array layout.

## Structured sources

Use `structured` to select members by name and follow pointers explicitly. This avoids hard-coding member offsets. The matching firmware ELF must contain debug information describing the types.

This input follows the global pointer `device_root` and reads its `state` member:

```json
{
    "name": "device.state",
    "ty": "u32",
    "structured": {
        "symbol": "device_root",
        "steps": [
            { "op": "deref" },
            { "op": "member", "name": "state" }
        ]
    }
}
```

`member` selects a struct or union member; `deref` follows a pointer. For a global struct instead of a pointer, start with `member`. Paths contain 1–16 steps. Use the input's `ty` to match the final scalar type.

A path also accepts `{"op":"index","index":1}` for a fixed-size C array. Bounds and element sizes come from DWARF. Pointer indexing, variable-length/multidimensional arrays and out-of-bounds indices are rejected. Use an explicit `deref` step when an array element is a pointer.

Structured sources require a 32-bit little-endian ELF with matching type information. Reads must remain within writable allocated ELF sections. Missing symbols or types, unsupported bitfields, mismatched scalar types, null pointers, out-of-range addresses, and failed reads make the input unavailable with a reason. Structured sources do not support `clear_on_read`.

Pointers and the selected value are read again for each observation. A detected pointer change produces a gap, so rates and checks do not span two different object instances. Separately read fields are not an atomic snapshot, and pointer checks cannot detect every change between reads, including reuse at the same address. Keep the selected ELF matched to the flashed firmware.

## Optional RAM read groups

`sampled_state.ram_read_groups` declares bounded opportunities to read related RAM fields together. It is an acquisition hint: a consumer may keep separate reads. It is not a promise of higher achieved rate or an atomic snapshot.

```json
{
    "id": "queue_header",
    "channels": ["queue.used", "queue.capacity"],
    "max_bytes": 64,
    "allow_padding": true
}
```

| Field | Required | Meaning |
|---|---|---|
| `id` | Yes | Nonempty group ID, unique within `ram_read_groups` |
| `channels` | Yes | 2–32 distinct declared sampled inputs; each input may belong to only one group |
| `max_bytes` | Yes | Maximum aligned read span, 4–4096 bytes and a multiple of four |
| `allow_padding` | No; default `false` | Explicitly permits reading padding and unselected RAM fields inside the bounded span |

At most 32 groups are allowed. Members must be readable `symbol` or `structured` inputs; registers, rings, `clear_on_read`, `unsafe`, `intrusive` and `unsupported` inputs cannot belong. ELF-derived bounds still apply. A group cannot authorize reading MMIO or memory outside supported allocations. Without `allow_padding`, every byte in the aligned span must belong to a selected field.

Selecting fewer than two members allows individual reads; selecting none reads nothing for that group. Unselected fields never become observations merely because their bytes were inside a permitted read. Groups do not change dependency requirements for derived values or findings. Pointer validation and failed-read handling still apply.

## Derived channels

Each entry in `derived` calculates a channel from a sampled or recorded value. Derived channels use the `derived` namespace and can be displayed, queried, or used by findings. Put dependencies before the derived channels that use them.

This array extracts a named state from a register and calculates a counter's rate:

```json
[
    {
        "type": "field",
        "name": "eth.rx_dma_state",
        "source": "eth.dma_debug",
        "shift": 8,
        "mask": "0xF",
        "values": { "0": "Stopped", "4": "Suspended", "7": "Transferring" }
    },
    {
        "type": "rate",
        "name": "eth.tx_rate",
        "source": "eth.tx_good",
        "unit": "pkts/s"
    }
]
```

| `type` | Fields | Result |
|---|---|---|
| `field` | `name`, `source`, `shift`, `mask`, optional `values` | `(value >> shift) & mask`, retains observation cadence for sampled domains; recorded on change for externally recorded channels. `values` maps numeric values to state labels. |
| `rate` | `name`, `source`, optional `unit` | Counter advance per second between adjacent samples. Negative deltas are clamped to zero. |
| `map` | `name`, `source`, `table`, optional `unit` | Numeric lookup, such as `{"4": 104, "5": 208}` to convert configuration codes to Hz. Unmapped values pass through. Sampled domains retain observation cadence; externally recorded values are recorded on change. |

For a domain with `sampled_state`, every source must refer to a declared input or an earlier derived channel. Names must be unique. Derived channels are available during live observation in BKPT Debug and when ViewAlyzer finalizes or opens a recording.

## Findings and verdicts

Each entry in `verdicts` defines a condition, severity, explanation, and supporting channels. ViewAlyzer shows the resulting findings in the [Analyzer](https://bkptlabs.com/docs/viewalyzer/analyzer.html). CLI users can retrieve them with [query verdicts](https://bkptlabs.com/docs/viewalyzer-cli/queries.html). BKPT Debug shows them in its Trace Domains view.

```json
{
    "id": "app-not-draining",
    "name": "Application not draining",
    "severity": "warning",
    "when": {
        "type": "counter_stalled",
        "channel": "lwip.link_recv",
        "while_advances": "eth.rx_good",
        "window_ms": 250
    },
    "explain": "Frames continued arriving while the stack stopped accepting them. Check the receive task and buffer pool.",
    "evidence": ["lwip.link_recv", "eth.rx_good", "eth.rx_ring"]
}
```

| Field | Meaning |
|---|---|
| `id` | Unique, stable identifier for the rule. |
| `name` | Short finding title. |
| `severity` | `error`, `warning` (default), `suspicious`, `flag`, or `info`. |
| `when` | Detector and its inputs, from the table below. |
| `explain` | Explanation shown with the finding. Describe what happened and what to check next. |
| `evidence` | Channels to include with their values at the finding's onset and end. Named states use their labels. |

### Detectors

| `when.type` | Fields | Condition |
|---|---|---|
| `sustained_at_or_above` | `channel`, `value`, `sustained_ms` | Observed value stays at or above the threshold for the duration. |
| `sustained_at_or_below` | `channel`, `value`, `sustained_ms` | Observed value stays at or below the threshold for the duration. |
| `counter_advanced` | `channel` | Counter increases. Use for cumulative counts, such as an error total. Wrapping counters are compared using their unwrapped total. |
| `value_at_or_above` | `channel`, `value` | A sample reaches the threshold. Useful for `clear_on_read` counters, where each sample already represents an interval. |
| `value_in` | `channel`, `values` | An observed value matches one of the listed discrete codes. Repeated observations form intervals, not counts of operations. |
| `counter_stalled` | `channel`, `while_advances`, `window_ms`, optional `min_advance` | One counter stops while the other advances over the window. `min_advance` defaults to `1` and sets the required advance of `while_advances`. |
| `ratio_below` | `channel`, `reference`, `ratio`, `sustained_ms` | The channel stays below `reference * ratio` for the duration while both values are available. |

In a domain with sampled inputs, declare every channel used by a detector or listed as evidence. Choose the observation and duration to suit the expected behavior: a 20 Hz poll may miss a state that changes and returns between two samples. A cumulative counter can preserve evidence of those short events. The absence of a finding does not establish that no brief event occurred.

## Cards and tables


`presentation.sections` contains 1–12 sections, each with 1–32 fields.

| Section field | Meaning |
|---|---|
| `title` | Required nonempty heading |
| `view` | Required `cards` or `table` |
| `description` | Optional explanatory text |
| `collapsed` | Optional initial state. Omitted: tables closed, cards open. Saved expansion choices take precedence |
| `fields` | Required array of fields below |
| `checks` | Optional verdict IDs associated with this section |

| Presentation field | Meaning |
|---|---|
| `channel` | Required declared sampled or derived channel name |
| `label` | Required nonempty display label |
| `group` | Optional array of up to four nonempty labels, each at most 128 UTF-8 bytes. Omitted/empty means directly in the section |
| `reference` | Optional channel supplying positive capacity/total |
| `unit` | Optional display unit |
| `action` | Optional declared action ID; editable only in BKPT Debug |

For example, a field with `"group": ["Characteristic", "Identity"]` appears
under those two nested disclosures. Groups start closed. Direct fields precede
child groups, and sibling groups follow their first appearance in the fields
array. The app does not infer hierarchy from channel names or device type.

Expansion is remembered separately from Graph choices. Closing a section or
group does not stop reads, hide history, disable checks or change Graph choices.
**Expand all**/**Collapse all** change expansion; **Reset graphs** restores only
the graph defaults. Each app keeps its own preferences. Renaming a section or
group can reset that branch's expansion choice.

For graph defaults, the first matching channel rule is decisive. If it omits
`graph`, use the automatic fallback, not a later rule: with `presentation`,
declared counters, rings and derived rates start graphed; other channels do not.
Without `presentation`, channels retain the legacy enabled default. Put exact
rules before broad prefixes when they need different defaults.

Fields may add `reference` (another channel containing a positive
capacity/total) and `unit`, for example `{"channel":"queue.used","reference":"queue.capacity","label":"Control queue","unit":"messages"}`.
Both observations must be available; missing or invalid capacity does not render
as a successful empty queue. Sections may add `checks: ["verdict-id"]` to surface
associated observations and retained incident history in Debug. These are
observed incidents, not declarations that a fault remains active. Replay uses
the history available at the selected time. The desktop analyzer continues to
provide its full findings report when capture stops.

```json
{"presentation":{"sections":[{"title":"Device","view":"cards","description":"Current sampled state","fields":[{"channel":"device.state","label":"State"}]}]}}
```

Each section uses `cards` or `table`; 1–12 sections and 1–32 fields per section.
Fields reference declared input or derived channels and use their existing enum
names. There is no HTML, script or arbitrary layout execution. Both BKPT Debug and
ViewAlyzer render these same sections with neutral rectangular observations. Debug
shows live values alongside checks and retained findings. ViewAlyzer shows live values
while capturing. Its dedicated domain view follows the hovered or pinned trace
cursor in saved recordings, or the recording end when no cursor is selected.
Existing trace views retain histories. Samples older than three requested
periods (at least 250 ms) display as unavailable in snapshots.

## Typed actions

In BKPT Debug, a presentation field can reference a declared action, for example
`{"channel":"device.state","label":"State","action":"set_state"}`. Editing a
draft never writes: the user must explicitly select **Send**. ViewAlyzer keeps
all observations read-only, even when the descriptor declares an action.
Actions have a unique identifier `id`, a visible `label`, `input`, `target` and
mandatory `completion`. Unknown properties, target kinds and policies are rejected.
At most 32 actions are accepted per domain. No scripts, addresses, expressions or
function calls can be submitted by the editor.

### Inputs and encoding

| `input.ty` | Encoding and bounds |
| --- | --- |
| `u8`, `u16`, `u32`, `i8`, `i16`, `i32` | Little-endian integer; decimal or nonnegative `0x` hexadecimal; optional `min`/`max` within the type |
| `f32` | Finite little-endian float32; optional `min`/`max`; normal float32 rounding, overflow and underflow-to-zero rejected |
| `bool` | `true` or `false`, one byte 1 or 0 |
| `bytes` | Complete hex byte pairs, contiguous or separated by whitespace; mandatory `max_bytes` in 1–256 |
| `utf8` | UTF-8 text; mandatory `max_bytes` in 1–256 and `termination: "none"` or `"nul"`; bound includes the terminator; embedded NUL rejected |

Optional `choices` is a list of up to 64 `{ "value": "1", "label": "Enabled" }`
entries. Values must pass the same encoder and be distinct after encoding. With
choices present, only those encoded values are accepted. Inputs are never silently
truncated or wrapped. The same type, range and encoding rules apply when validating a draft and when sending it. The editor sends strings, not encoded bytes.

### RAM symbols and members

```json
{"id":"set_limit","label":"Limit","input":{"ty":"u16","min":0,"max":500},
 "target":{"kind":"ram","source":{"symbol":"settings","steps":[{"op":"member","name":"limit"}]},"execution":"running_scalar"},
 "completion":"readback"}
```

The matching ELF/DWARF determines the exact type, size, member/array offsets and
writable allocated SRAM bounds. It must match the input type; integer widths or
signedness are not reinterpreted. Const, union, bitfield and pointer-valued leaves
are rejected. Buffers require an exactly sized one-dimensional byte/char array.
A shorter buffer value is zero-padded to that storage capacity. Completion reports
fresh readback, including a mismatch if firmware overwrote or did not retain it.

`execution` defaults to `halted`. Pointer traversal is halted-only, bounded to ELF
allocations, read fresh and rechecked before writing; null/changing pointers fail.
`running_scalar` explicitly permits aligned 1/2/4-byte scalar edits at fixed paths
without dereferencing pointers. This is a background debug-bus operation, not a
transaction with firmware. The application owns concurrency with interrupts, DMA
and other cores; halting one core does not stop those other bus masters.

### SVD registers and fields

```json
{"id":"clear_flags","label":"Clear flags","input":{"ty":"u32"},
 "target":{"kind":"register","peripheral":"PORT","register":"STATUS","field":"READY","execution":"running_scalar"},
 "completion":"transport"}
```

BKPT Debug resolves names and access policies from the selected SVD. Only unsigned integer/bool inputs and unambiguous 8/16/32-bit
register layouts are supported. Register/field access rights, writable masks,
reserved bits, numeric/enumerated write constraints and read effects are enforced.
Normal partial writes preserve unrelated bits with read-modify-write and require
a halted core and side-effect-free readable register. Running actions allow direct
writes only. Homogeneous `oneToClear`, `oneToSet` and `oneToToggle` effects write
zero to untouched flags; mixed effects, zero-to effects, write-once, writeAsRead,
ambiguous derived/alias layouts and missing field masks are refused.

`completion: "readback"` requires a safe read. `"transport"` reports the probe's
write acknowledgement only, with no claim about the resulting register value.
A descriptor cannot override SVD restrictions. SVD accuracy remains a prerequisite.

### Application mailbox

```json
{"id":"set_state","label":"State",
 "input":{"ty":"u8","choices":[{"value":"0","label":"Off"},{"value":"1","label":"On"}]},
 "target":{"kind":"mailbox","symbol":"domain_command","operation":1,"timeout_ms":3000},
 "completion":"application"}
```

Mailbox commands require running firmware. The firmware callback runs in its
appropriate application context, validates operation/length/value independently,
and invokes the real application API. Observation mirrors are not command targets.
Operation mappings and payload interpretation belong to the descriptor/firmware.
Timeout is 100–5000 ms and the maximum payload is 64 bytes.

Command ABI 1 is a word-aligned 104-byte object with these exact DWARF member names
and offsets. Every integer is `uint32_t`, little-endian; payload is `uint8_t[64]`.

| Offset | Member | Owner |
| --- | --- | --- |
| 0, 4, 8 | `magic` = `0x56414331`, `abi` = 1, `capacity` = 64 | Firmware initialization |
| 12 | `request_seq` | Host publishes last |
| 16, 20, 24 | `request_nonce`, `operation`, `length` | Host stages |
| 28 | `payload` | Host stages |
| 92, 96, 100 | `response_seq`, `response_nonce`, `status` | Firmware completion |

An idle mailbox has equal request/response sequences. The host stages and verifies
the body before publishing a new nonzero sequence. Firmware consumes only a new
sequence, copies and validates the bounded request, then publishes status and nonce
before response sequence using appropriate memory barriers. Status 0 means accepted;
other values are application errors. Host completion requires both sequence and
nonce to match. One host owns a mailbox; concurrent independent writers are unsupported.

### Host lifecycle and compatibility

BKPT Debug keeps drafts separate from sampled values and writes only on explicit
Send. It shows pending, accepted, rejected and unknown completion separately.
Overlapping destinations are serialized by refusing concurrent requests. A timed-out
or disconnected write may have executed: it is never retried automatically. A busy
mailbox refuses another command until firmware acknowledges the previous sequence.

Send is bound to the loaded descriptor revision, debug session, ELF, target state
and destination resolution. The host rechecks these at queued IO dispatch, validates
the ELF file hash for DWARF targets, and compares sampled ELF load-image bytes with
target memory. A mismatch or unavailable signature blocks writes. This detects
ordinary stale firmware; sparse signatures are not full-image attestation. Modified
RAM load images or patched code may also fail the signature check. Keep the matching
firmware/ELF/source set. The host never silently halts, resumes, resets or reflashes.

History, recordings and disconnected/saved-task views are read-only. Changing the
domain or session discards drafts and cannot replay a prior Send.
The desktop renders observations with a visible read-only limitation;
its analyzer does not execute actions. No action is executed while parsing,
recording, restoring a layout or applying a descriptor to a recording.

## External instrument recorders

An `instrument` section configures a separate recorder program that runs during a ViewAlyzer capture. The program streams the [external-series format](https://bkptlabs.com/docs/viewalyzer-cli/external-series.html); its samples are aligned with and merged into the recording. Include the program and its required assets in the package folder. Instrument recording is configured in ViewAlyzer.

```json
{
    "instrument": {
        "cmd": "\"{dir}/bin/recorder\" --stream --rate {param.rate}",
        "warmup_s": 4,
        "sync_name": "jsync",
        "params": [
            {
                "key": "rate",
                "label": "Sample rate",
                "type": "number",
                "default": "1000",
                "min": 1,
                "max": 10000
            }
        ]
    }
}
```

| Field | Meaning |
|---|---|
| `cmd` | Recorder command. `{dir}` expands to the descriptor's folder; `{param.key}` expands to a parameter value. |
| `warmup_s` | Seconds to wait after launching the recorder before target capture starts; default `2`, constrained to `0`–`30` at runtime. |
| `sync_name` | Name of the recording's synchronization channel; default `jsync`. |
| `params` | Editable parameter fields shown beneath the domain in the sidebar. Each has a `key`, optional `label`, `type` (`number` or `string`, default `string`), and a string `default` (default empty). Numeric fields may include `min` and `max`. |

Set the parameters and enable **Record with capture** in the sidebar before capturing. See [Instrument time sync](https://bkptlabs.com/docs/viewalyzer-cli/time-sync.html) for synchronization setup and alignment quality.

## Worked example

This complete descriptor observes a firmware counter, calculates its rate, and raises a warning when the observed rate stays at or below 10 samples per second for at least one second. It requires a matching ELF containing a 32-bit unsigned global named `imu_sample_count` that increments once per sample. Change the symbol and threshold to match your application.

Save the following as `sensor-monitor.vadomain`, then install it using **Load Domain File** in ViewAlyzer or **Install custom trace domain…** in BKPT Debug.

```json
{
    "id": "com.example.sensor-monitor",
    "name": "Sensor Monitor",
    "version": "1.0.0",
    "domain_api": 1,
    "sampled_state": {
        "rate_hz": 20,
        "channels": [
            {
                "name": "sensor.samples",
                "symbol": "imu_sample_count",
                "ty": "u32",
                "counter": "wrapping"
            }
        ]
    },
    "derived": [
        {
            "type": "rate",
            "name": "sensor.sample_rate",
            "source": "sensor.samples",
            "unit": "samples/s"
        }
    ],
    "verdicts": [
        {
            "id": "low-sample-rate",
            "name": "Sensor sample rate is low",
            "severity": "warning",
            "when": {
                "type": "sustained_at_or_below",
                "channel": "sensor.sample_rate",
                "value": 10,
                "sustained_ms": 1000
            },
            "explain": "The observed sample rate stayed at or below 10 samples/s for at least one second. Check the sensor configuration and acquisition task.",
            "evidence": ["sensor.samples", "sensor.sample_rate"]
        }
    ],
    "channels": [
        {
            "match": { "ns": "poll", "name": "sensor.samples" },
            "unit": "samples",
            "display": "counter",
            "group": "Sensor"
        },
        {
            "match": { "ns": "derived", "name": "sensor.sample_rate" },
            "unit": "samples/s",
            "display": "graph",
            "group": "Sensor",
            "interpolation": "step"
        }
    ]
}
```

Enable the domain, select the matching ELF, and capture while the firmware runs. In ViewAlyzer, stop capture to inspect the derived rate in Trace and any findings in Analyzer. In BKPT Debug, follow the channels in Traces and findings in Trace Domains.

## Troubleshooting

| Symptom | What to check |
|---|---|
| Descriptor will not load | Check JSON syntax, field names, display names, and `domain_api`. Omit unused sections instead of supplying incomplete objects. |
| Unknown input or evidence channel | Match names exactly and define each derived dependency before its consumer. |
| Register input is unavailable | Check the selected SVD, peripheral/register names, target match, peripheral clock, and precondition message. |
| Firmware input is unavailable | Select the ELF from the flashed build. Check symbol names, type information, member layout, and pointer availability. |
| A finding does not appear | Check that the domain and its required inputs are enabled, valid samples exist, and enough time has elapsed for the rule's duration. Review the actual sampling rate. |
| Channel styling is missing | Check `match.ns`, spelling, and rule order. Put exact matches before prefix matches. |

For a hardware example with controller registers, structured sources, derived fields, and presentation sections, use the downloadable domain in the [USBX on ThreadX walkthrough](https://bkptlabs.com/docs/trace-domains/usbx-threadx-u575.html).

## Ethernet example

The downloadable [Ethernet example](../../examples/trace-domains/ethernet.vadomain) combines a guarded hardware counter, a decoded status word, a descriptor ring and a sustained-backlog finding. Its `ExampleMCU`, `CLOCK` and `ETH` register names are illustrative, not a claim of compatibility with any real device.

To adapt it to a specific Ethernet IP:

1. Choose the exact controller revision and firmware driver configuration. Verify register names and access policies in the selected SVD and reference manual.
2. Verify how clock, reset and counter modes affect observation. Encode valid modes with preconditions and explain failures in their descriptions.
3. Verify the ring symbol's actual element size, entry count, ownership word and polarity. Descriptor ownership may mean DMA-owned or CPU-owned depending on the IP and direction.
4. Set a target-name prefix for intended devices. Document additional IP and firmware compatibility constraints separately.
5. Choose a backlog duration meaningful at the achieved polling rate. Ring occupancy alone does not prove packet loss or identify the stalled task.

```json
{
  "id": "com.example.mac-monitor",
  "name": "Example Ethernet MAC",
  "version": "1.0.0",
  "domain_api": 1,
  "sampled_state": {
    "svd_device": "ExampleMCU",
    "target_device_prefix": ["ExampleMCU"],
    "rate_hz": 10,
    "precondition": {"peripheral": "CLOCK", "register": "ENABLE", "mask": "0x1", "equals": "0x1", "description": "The Ethernet clock must be enabled."},
    "channels": [
      {"name": "eth.rx_good", "peripheral": "ETH", "register": "RX_GOOD", "counter": "wrapping", "safety": "conditional",
       "precondition": {"peripheral": "ETH", "register": "COUNTER_CONTROL", "mask": "0x4", "equals": "0", "description": "Disable reset-on-read before observing this counter."}},
      {"name": "eth.status", "peripheral": "ETH", "register": "STATUS", "note": "This example assumes STATUS has no read side effects."},
      {"name": "eth.rx_pending", "ring": {"symbol": "rx_descriptors", "entries": 4, "stride_bytes": 24, "own_word_offset": 12, "own_mask": "0x80000000", "count_set": false}},
      {"name": "eth.payload", "safety": "unsupported", "reason": "Payload contents are outside this monitor's observation contract."}
    ]
  },
  "derived": [
    {"type": "field", "name": "eth.state", "source": "eth.status", "shift": 1, "mask": "0x3", "values": {"0": "Stopped", "1": "Running", "2": "Suspended", "3": "Error"}},
    {"type": "rate", "name": "eth.rx_rate", "source": "eth.rx_good", "unit": "frames/s"}
  ],
  "verdicts": [
    {"id": "rx-backlog", "name": "Receive backlog", "when": {"type": "sustained_at_or_above", "channel": "eth.rx_pending", "value": 3, "sustained_ms": 500},
     "explain": "At least three receive descriptors stayed pending across the observed window. Check whether the receive task is reclaiming descriptors.", "evidence": ["eth.rx_pending", "eth.rx_rate", "eth.state"]}
  ],
  "channels": [
    {"match": {"ns": "poll", "name": "eth.rx_pending"}, "display": "level", "unit": "descriptors", "min": 0, "max": 4, "warn": 3, "interpolation": "step"}
  ],
  "presentation": {"sections": [
    {"title": "Receive path", "view": "table", "collapsed": false, "fields": [
      {"channel": "eth.state", "label": "DMA state"},
      {"channel": "eth.rx_pending", "label": "Pending descriptors"},
      {"channel": "eth.rx_rate", "label": "Receive rate", "unit": "frames/s"}
    ], "checks": ["rx-backlog"]}
  ]}
}
```

The unavailable payload row illustrates how a domain can explain an observation it cannot provide. It does not read the payload. `coalesce_gap_bytes` remains zero; increasing it would explicitly authorize reads across otherwise undeclared register gaps.

## Middleware example

The downloadable [middleware example](../../examples/trace-domains/middleware.vadomain) follows a queue pointer, selects a fixed array element, presents occupancy against capacity, and compares observed progress with expected progress. It needs no register SVD because it has no register sources or preconditions.

The names are an example contract for your firmware, not built-in queue or RTOS knowledge. Assume retained globals with DWARF matching these declarations:

```c
#include <stdint.h>
struct queue_state {
    uint32_t used;
    uint32_t capacity;
    uint32_t state;
};
struct queue_slot { uint32_t mode; };
struct queue_state *active_queue;
struct queue_slot queue_slots[2];
uint32_t queue_enqueued;
uint32_t queue_drained;
```

The pointed-to queue must occupy a supported writable ELF allocation. Your firmware initializes and updates it; declaring a symbol alone does not produce observations. A null pointer displays as unavailable. The `state` and `mode` mappings belong to this example's firmware convention. Replace them with your middleware's documented values.

```json
{
  "id": "com.example.middleware-queue",
  "name": "Middleware Queue",
  "version": "1.0.0",
  "domain_api": 1,
  "sampled_state": {
    "rate_hz": 20,
    "channels": [
      {"name": "queue.used", "structured": {"symbol": "active_queue", "steps": [{"op": "deref"}, {"op": "member", "name": "used"}]}},
      {"name": "queue.capacity", "structured": {"symbol": "active_queue", "steps": [{"op": "deref"}, {"op": "member", "name": "capacity"}]}},
      {"name": "queue.state", "structured": {"symbol": "active_queue", "steps": [{"op": "deref"}, {"op": "member", "name": "state"}]}},
      {"name": "queue.mode", "structured": {"symbol": "queue_slots", "steps": [{"op": "index", "index": 1}, {"op": "member", "name": "mode"}]}},
      {"name": "queue.enqueued", "symbol": "queue_enqueued", "counter": "wrapping"},
      {"name": "queue.drained", "symbol": "queue_drained", "counter": "wrapping"}
    ],
    "ram_read_groups": [{"id": "queue_header", "channels": ["queue.used", "queue.capacity", "queue.state"], "max_bytes": 64, "allow_padding": true}]
  },
  "derived": [
    {"type": "rate", "name": "queue.drain_rate", "source": "queue.drained", "unit": "messages/s"},
    {"type": "map", "name": "queue.expected_rate", "source": "queue.mode", "table": {"0": 0, "1": 50, "2": 100}, "unit": "messages/s"}
  ],
  "channels": [
    {"match": {"ns": "poll", "name": "queue.state"}, "display": "enum", "names": {"0": "Idle", "1": "Active", "2": "Fault"}, "graph": false}
  ],
  "verdicts": [
    {"id": "drain-stalled", "name": "Consumer stopped progressing", "when": {"type": "counter_stalled", "channel": "queue.drained", "while_advances": "queue.enqueued", "window_ms": 1000, "min_advance": 10}, "evidence": ["queue.used", "queue.capacity"]},
    {"id": "fault-state", "name": "Firmware reports a fault", "severity": "error", "when": {"type": "value_in", "channel": "queue.state", "values": [2]}, "explain": "Inspect the firmware fault reason before restarting this queue."},
    {"id": "slow-drain", "name": "Drain rate below expectation", "when": {"type": "ratio_below", "channel": "queue.drain_rate", "reference": "queue.expected_rate", "ratio": 0.8, "sustained_ms": 2000}, "evidence": ["queue.mode"]}
  ],
  "presentation": {"sections": [
    {"title": "Queue", "view": "cards", "fields": [
      {"channel": "queue.used", "reference": "queue.capacity", "label": "Occupancy", "unit": "messages"},
      {"channel": "queue.state", "label": "State", "group": ["Status"]},
      {"channel": "queue.drain_rate", "label": "Drain rate", "unit": "messages/s", "group": ["Performance"]},
      {"channel": "queue.expected_rate", "label": "Expected rate", "unit": "messages/s", "group": ["Performance"]}
    ], "checks": ["drain-stalled", "fault-state", "slow-drain"]}
  ]}
}
```

For USBX, ThreadX, lwIP, FreeRTOS or another middleware library, inspect the exact headers, configuration and ELF. Do not guess member names from a different version. A pending request, waiting thread or full queue can be normal; combine observations and use an explanation that reflects what the evidence establishes.

## Complete actions example

The downloadable [actions example](../../examples/trace-domains/actions.vadomain) includes all three action targets: a bounded RAM scalar edit, an application mailbox request, and an SVD-defined flag write. The RAM scalar must actually be safe for asynchronous firmware access. The mailbox must implement the documented ABI. The illustrative `PORT.STATUS.READY` field must have suitable write-one semantics and permissions in the selected device's SVD; it is not valid for an arbitrary status register.

```json
{
  "id": "com.example.device-controls",
  "name": "Device Controls",
  "version": "1.0.0",
  "domain_api": 1,
  "sampled_state": {"channels": [
    {"name": "settings.limit", "symbol": "sample_limit", "ty": "u16"},
    {"name": "device.enabled", "symbol": "device_enabled", "ty": "u8"},
    {"name": "device.flags", "peripheral": "PORT", "register": "STATUS"}
  ]},
  "actions": [
    {"id": "set_limit", "label": "Set sample limit", "input": {"ty": "u16", "min": 0, "max": 500},
     "target": {"kind": "ram", "source": {"symbol": "sample_limit", "steps": []}, "execution": "running_scalar"}, "completion": "readback"},
    {"id": "set_enabled", "label": "Enable device", "input": {"ty": "u8", "choices": [{"value": "0", "label": "Off"}, {"value": "1", "label": "On"}]},
     "target": {"kind": "mailbox", "symbol": "domain_command", "operation": 1, "timeout_ms": 3000}, "completion": "application"},
    {"id": "clear_ready", "label": "Clear ready flag", "input": {"ty": "u32", "choices": [{"value": "1", "label": "Clear"}]},
     "target": {"kind": "register", "peripheral": "PORT", "register": "STATUS", "field": "READY", "execution": "running_scalar"}, "completion": "transport"}
  ],
  "presentation": {"sections": [
    {"title": "Controls", "view": "cards", "fields": [
      {"channel": "settings.limit", "label": "Sample limit", "action": "set_limit"},
      {"channel": "device.enabled", "label": "Device enabled", "action": "set_enabled"},
      {"channel": "device.flags", "label": "Flags", "action": "clear_ready"}
    ]}
  ]}
}
```

All three actions remain explicit user operations in BKPT Debug. ViewAlyzer displays the associated observations read-only. Schema validation does not establish whether an action's destination is compatible with the user's ELF or SVD.

## External instrument example

The downloadable [instrument example](../../examples/trace-domains/instrument.vadomain) assumes an independently supplied `sensor-recorder` program on the user's executable search path. It must write the documented external-series NDJSON stream to standard output, send diagnostics to standard error and participate in the required time synchronization. This example does not include that program.

```json
{
  "id": "com.example.bench-sensor",
  "name": "Bench Sensor",
  "version": "1.0.0",
  "domain_api": 1,
  "namespaces": ["external"],
  "series_kinds": ["external_trace"],
  "instrument": {
    "cmd": "sensor-recorder --stream --rate {param.rate}",
    "warmup_s": 2,
    "sync_name": "sync",
    "params": [{"key": "rate", "label": "Sample rate", "type": "number", "default": "1000", "min": 1, "max": 10000}]
  },
  "channels": [
    {"match": {"ns": "external", "name": "current"}, "unit": "mA", "display": "graph", "group": "Bench", "interpolation": "linear"}
  ],
  "verdicts": [
    {"id": "high-current", "name": "High current", "when": {"type": "sustained_at_or_above", "channel": "current", "value": 200, "sustained_ms": 500}, "evidence": ["current"]}
  ]
}
```

An instrument declaration launches software when recording is enabled. Distribute trusted recorder binaries or source with installation instructions for supported operating systems. Quote command paths containing spaces and account for platform executable names. Offline validation checks the declaration, not executable existence, process output, synchronization quality or the device connection. BKPT Debug's sampled-domain workflow does not run instrument sidecars.

## Validation boundaries

The file is checked in stages. An offline pass establishes that the descriptor has the supported shape and internally consistent references. The application later resolves real hardware and firmware information.

| Check | Offline SDK | Application or target required |
|---|---|---|
| JSON syntax, types, unknown properties, supported format | Yes | No |
| Source exclusivity, limits, names, derived ordering and references | Yes | No |
| Action input bounds, choice encodings and completion policies | Yes | No |
| Whether a selected SVD contains the named register or field | No | Selected device SVD |
| Register read/write effects and actual access restrictions | No | Correct SVD and hardware context |
| Whether symbols, members, array indices and scalar types match | No | Matching ELF with supported debug information |
| Runtime pointer validity and current values | No | Live target |
| Achieved polling rate, cache coherence and firmware concurrency | No | Live target and application-specific evaluation |
| Whether diagnostic thresholds describe a real fault | No | Domain expertise and representative tests |
| Instrument executable, stream and synchronization | No | Instrument and recorder program |

### Numeric and structural limits

The following details matter when generating files programmatically. Integers must be JSON integers, not strings, booleans or decimal tokens such as `1.0`, except fields explicitly described as numeric strings.

| Field or object | Constraint |
|---|---|
| `domain_api` | Current supported format, `1`; omit to use the default |
| `sampled_state.rate_hz` | Finite number greater than zero and at most 1000; default 20 |
| `coalesce_gap_bytes` | Unsigned 64-bit integer; default zero |
| `offset_bytes` | Unsigned 32-bit integer; default zero; nonzero only with `symbol` |
| Precondition `mask` / `equals` | Unsigned decimal or `0x` strings; mask fits 32 bits; all set bits in `equals` must be inside the mask |
| Ring `entries`, `stride_bytes`, `own_word_offset` | Unsigned 16-bit integers; entries positive; stride at least four; ownership offset plus four fits the stride; entries times stride at most 65,535 |
| Ring `own_mask` / sampled type | Nonzero unsigned 32-bit decimal/hex string; ring channel uses `u32` |
| Sampled `structured.symbol` / member names | ASCII C identifiers, at most 256 bytes; 1–16 steps |
| Structured `index` | Unsigned 32-bit integer; actual array bounds checked against the ELF |
| Derived field `shift` / `mask` | Shift 0–63; unsigned 64-bit decimal/hex mask applied after shifting |
| Derived map `table` | Nonempty object with signed 64-bit decimal integer keys and finite numeric values |
| Duration values | Unsigned 64-bit integer milliseconds; `window_ms` positive; `sustained_ms` may be zero |
| `min_advance` / `ratio` | Finite and positive / finite and nonnegative, respectively |
| `value_in.values` | 1–256 finite numbers |
| Presentation | 1–12 sections, 1–32 fields each; nonempty titles and labels; up to four group levels with nonblank labels no larger than 128 UTF-8 bytes |
| Action ID and target C identifiers | At most 128 ASCII bytes; action IDs unique within the domain |
| RAM action path | Zero to 16 steps; zero selects the root scalar or buffer, unlike a sampled structured source |
| Actions and choices | At most 32 actions; at most 64 choices per input; choice labels nonempty and encoded choice values unique |
| Buffer capacity | `max_bytes` 1–256 for `bytes`/`utf8`; mailbox payload at most 64 bytes |
| Mailbox | Nonzero unsigned 32-bit operation; unsigned integer timeout 100–5000 ms |
| Instrument parameter key | One or more ASCII letters, digits, underscores or hyphens |

The sampled types are `u8`, `u16`, `u32`, `i8`, `i16`, `i32` and `f32`; the default is `u32`. `bool` is an action input type, not a sampled type. Unknown enum values are errors. Channel names and identifiers are case-sensitive. Sampled and derived names must be unique within their domain, and verdict IDs must be unique within their own list. An action ID uses a C identifier, so use `set_limit`, not `set-limit`.

Arrays default to empty when optional. Optional text such as publisher, explanation and section description defaults to empty. Fields represented as optional values may accept `null`, including the top-level `sampled_state`, `presentation` and `instrument`, source alternatives, notes, channel-style options and action bounds. A required scalar or list does not become optional by assigning `null`.

The parser checks format validity, not editorial quality. Give domains, channels and findings useful names even where the format allows an empty string. Use `step` or `linear` for interpolation. Include a reason for every deliberately unavailable input. A reverse-DNS identifier should belong to a namespace you can maintain; the parser's basic identifier check does not prove ownership.

## Authoring with AI

Give an AI assistant this guide, the intended diagnostic question, the supported target and IP revision, relevant SVD entries, exact firmware headers and the matching build configuration. Provide only information you are entitled to share. Require an inventory of source names and assumptions before generating a descriptor.

A useful generation workflow is:

1. Identify observable inputs and their evidence sources. Mark unresolved names or semantics as open questions instead of inventing them.
2. Generate one complete version-one descriptor with supported properties only. Keep declared sampled inputs separate from display rules and derived values.
3. Run the offline validator and fix the fields named by its diagnostics. Revalidate after every change.
4. Check symbol/type resolution in the application. Resolve unavailable inputs using the matching ELF/SVD rather than deleting evidence from a diagnostic rule until it passes.
5. Test healthy behavior, a known abnormal condition and unavailable data. Review explanations for claims stronger than the samples support.
6. Have a domain expert review read side effects, ownership, counter semantics, action policies and thresholds before sharing the pack.

Do not ask the file to evaluate expressions such as `queue.used / queue.capacity`, add executable callbacks, invent detector names or embed arbitrary register addresses. Use the listed derived operators, a presentation `reference`, named SVD registers and supported source paths.

## Share and maintain a domain

Distribute the `.vadomain` with a README containing its purpose, tested devices and IP revisions, firmware or middleware versions, relevant build options, ELF/SVD requirements, observation limits, units and counter semantics. Include test steps and the domain's license and attribution. Keep licenses and extra documentation in companion files rather than adding unsupported JSON properties.

Use a stable `id` so an update represents the same domain, and increment `version` as the domain evolves. Two installations with the same ID can replace or override each other; fork to a distinct ID for a different compatibility contract. Remove machine-specific paths and credentials from examples. Select symbols rather than copying addresses from one linked firmware image.

For each release, rerun validation and test the intended application workflows. State exactly which firmware and device combinations were exercised. A specialized, well-tested domain for one Ethernet IP revision is more useful than an unverified claim to support every Ethernet controller.
