# kicad-clippy: research and architecture proposal

**Status:** Architecture approved; Phase 1 foundation ported to the standalone [Uhrendoktor/kicad-clippy](https://github.com/Uhrendoktor/kicad-clippy) repository.  
**Scope:** A full Rust schematic linter that complements native KiCad ERC.  
**Implementation:** The initial parser, diagnostic contracts, native ERC adapter, CLI, strict Clippy policy, and project documentation are under review in [kicad-clippy PR #1](https://github.com/Uhrendoktor/kicad-clippy/pull/1).  
**Remaining:** Custom SCH001–SCH020 execution, project profiles, SARIF, fix/layout proposals, and GUI integration remain future implementation stages.

## 1. Executive summary

Build **kicad-clippy** as a standalone Rust workspace and executable, using Rust Clippy as an architectural reference rather than embedding or forking the Rust compiler's Clippy implementation.

The linter should have five distinct responsibilities:

1. Parse KiCad schematics while retaining exact source locations and original text.
2. Build a semantic schematic model and derived geometry/connectivity indexes.
3. Run modular, independently configurable rules.
4. Consume native KiCad ERC results rather than recreate the electrical checker.
5. Produce precise diagnostics and structured fix proposals, including verified layout proposals.

The intended pipeline is:

~~~text
.kicad_sch + project/config/profile
        |
        v
lossless S-expression CST + source map
        |
        v
typed semantic model + UUID/hierarchy indexes
        |
        +-------------------------+
        |                         |
        v                         v
kicad-clippy rule registry    native KiCad ERC
        |                    kicad-cli / future IPC
        +------------+------------+
                     |
                     v
             normalized diagnostics
                     |
          +----------+----------+
          |          |          |
          v          v          v
       terminal    JSON/SARIF   fix planner
                                  |
                                  v
                          verified edit plan
                                  |
                                  v
                         KiCad re-parse/ERC
~~~

The first release should be a dependable CLI and Rust library. A GUI is a later adapter over the same diagnostics model, not a second checker implementation.

### Core decisions

| Concern | Decision |
|---|---|
| Implementation language | Rust, stable toolchain |
| Architecture | Standalone linter inspired by Clippy's rule registry, metadata, levels and tests |
| Schematic parser | Reuse and audit the existing Rust lossless S-expression parser; add the missing schematic semantic/index layer |
| Native electrical checks | Invoke KiCad CLI ERC and normalize its JSON report |
| Rule ownership | Built-in generic rules, declarative project profiles, later external rule packs |
| Rule configuration | Stable IDs, allow/warn/deny levels, presets and project overrides |
| Diagnostics | Primary/secondary source spans, source object UUIDs, hierarchy path, explanation, help and documentation URL |
| Output | Human-readable terminal, stable JSON and SARIF |
| Fixes | Structured edit plans with applicability and precondition checks; safe fixes separated from proposals |
| Automatic layout | Declarative constraints and a geometry planner; no blind coordinate rewriting |
| Future GUI | Rust executable/IPC adapter when Eeschema's plugin surface is available; retain the existing Python UI as a possible thin bridge during transition |

## 2. Research findings

### 2.1 What to take from Rust Clippy

Clippy's useful design is not its Rust compiler integration. It is the separation of **rule metadata**, **rule implementations**, **registration**, **configuration**, **diagnostic emission** and **regression tests**.

Clippy groups related checks into passes, while individual lint declarations retain their own identity and metadata. Registration is explicit; multiple lints can share one pass. Pass ordering is deliberate because it affects output. Clippy also separates early syntax-only checks from later checks that need type information. Its tests assert diagnostics, and its structured suggestions can be tested by applying them and comparing the resulting source to a checked-in expected file. See the official [Clippy guide for adding lints](https://doc.rust-lang.org/clippy/development/adding_lints.html).

For kicad-clippy, translate those ideas as follows:

- A rule has a stable ID, name, category, default severity, description, help text, docs URL, tags, options schema and implementation.
- A rule is registered independently of the analysis stage that supplies its data.
- Related rules share derived indexes and analyses. Wire-crossing, label-clearance and wire-obstacle rules should share one geometry index.
- Profiles and project configuration select rule levels and provide options; they do not fork rule implementations.
- Every rule has positive and negative fixtures, expected diagnostics and, where applicable, an expected fixed schematic.
- Rule metadata is machine-readable so the CLI, docs and eventual GUI can render the same explanation.

**Important distinction:** kicad-clippy should not use rustc-private APIs, Clippy internal crates or compiler lint passes. Those are specific to Rust source analysis and are not a reusable general-purpose linter framework. Implement the same architectural pattern on stable Rust.

### 2.2 Native KiCad ERC is an integration point, not an extension API

KiCad's CLI already provides the correct machine interface:

~~~bash
kicad-cli sch erc --format json --severity-all schematic.kicad_sch
~~~

The CLI can emit JSON, include all severity classes and exclusions, and return a violation-dependent exit code when explicitly requested. For normal integration, generate and parse the report first; do not use the violation exit code as a parser-success signal. See the [KiCad CLI documentation](https://docs.kicad.org/master/en/cli/cli.html#_schematic_erc).

The ERC JSON schema is published by KiCad at [erc.v1.json](https://schemas.kicad.org/erc.v1.json). Its report contains per-sheet violations, severity, descriptions, affected item UUIDs and positions, plus ignored-check metadata. This is enough to normalize native ERC into the same diagnostic stream as kicad-clippy's own rules.

A source audit of KiCad's current implementation also matters:

- Eeschema's ERC_TESTER owns the built-in test methods.
- ERC_ITEM maintains a static catalogue of known error types/settings.
- The mapping from native ERC error codes to ERC_ITEM descriptions is compiled into KiCad.
- KiCad 11 adds the asynchronous schematic IPC ERC task. Its result carries native ErcMarker objects, including error type, position, affected KIID(s), sheet paths, description, severity and exclusion state.

Relevant upstream source: [ERC_TESTER](https://github.com/KiCad/kicad-source-mirror/blob/master/eeschema/erc/erc.h), [ERC_ITEM catalogue](https://github.com/KiCad/kicad-source-mirror/blob/master/eeschema/erc/erc_item.cpp), [ERC JSON report writer](https://github.com/KiCad/kicad-source-mirror/blob/master/eeschema/erc/erc_report.cpp), [schematic ERC IPC task](https://github.com/KiCad/kicad-source-mirror/blob/master/eeschema/api/api_handler_sch_tasks.cpp), [ERC task protocol](https://github.com/KiCad/kicad-source-mirror/blob/master/api/proto/schematic/schematic_tasks.proto), and [ERC marker protocol](https://github.com/KiCad/kicad-source-mirror/blob/master/api/proto/schematic/schematic_rules.proto).

There is no general external rule-registration API in that native ERC catalogue. **Do not try to inject kicad-clippy rules into the native ERC engine.** Doing so would require a KiCad C++ fork or upstream changes, tie rule releases to KiCad releases, and create a fragile compatibility boundary.

Instead:

- Native ERC owns electrical correctness.
- kicad-clippy owns additional visual, layout, style, documentation and design-intent checks.
- A single diagnostic model presents both sources consistently.
- Native ERC type IDs/settings keys remain identifiable as native findings; do not rename them as custom rules.

KiCad's current add-on documentation says IPC is language-agnostic, KiCad 9/10 provide PCB Editor plugins, and KiCad 11 adds schematic-editor plugins and headless IPC/export support. Executable plugins can launch a separately compiled program. However, this repository's compatibility matrix currently validates KiCad 10.99.0 as a development nightly and does not yet validate stable KiCad 11's full schematic IPC workflow. Therefore CLI ERC is the first supported path; add the schematic IPC adapter only after the target stable KiCad release is exercised by this repository's integration suite. See [KiCad's add-on developer documentation](https://dev-docs.kicad.org/en/apis-and-binding/ipc-api/for-addon-developers/) and the repository's [compatibility matrix](compatibility.md).

### 2.3 Existing Rust parser tooling

The Rust project [kiutils-rs](https://github.com/Milind220/kiutils-rs) already provides a lossless S-expression parser crate, kiutils_sexpr. Its documented design includes:

- A concrete syntax tree with byte spans on atoms and lists.
- The original source buffer for exact no-op round trips.
- Separate lossless and canonical output modes.
- Single-root and rootless parsing modes.

See [kiutils_sexpr on docs.rs](https://docs.rs/kiutils_sexpr/latest/kiutils_sexpr/).

Its companion kiutils_kicad crate includes a typed schematic document model. However, its current schematic model does not expose all data required by this linter: for example, text-box geometry is not yet represented as a typed collection, and embedded library-symbol primitive geometry is not a sufficient ready-made model for precise symbol-outline tests.

**Recommendation:** reuse the lossless CST layer after auditing its license, versioning and parser behavior; implement a kicad-clippy-owned schematic semantic adapter and indexes. Do not make the current typed AST the sole source of truth until the missing schematic constructs are covered. Preserve unknown nodes and source spans at every stage. The existing parser is MIT-licensed; retain the required notices and pin a reviewed version or vendored revision.

### 2.4 Geometry and graph tooling

Do not write a general geometry engine from scratch.

| Library | Intended use | Boundary |
|---|---|---|
| geo | Segment intersection, distance, containment and planar predicates | KiCad-specific pin rotation, mirroring and electrical meaning remain in our adapter |
| rstar | Spatial index for wires, symbols, fields, labels and text-box bounds | Use to shortlist candidate collisions; exact predicates decide the result |
| petgraph | Derived wire/pin/net graph and graph queries | Net names and hierarchical label semantics remain KiCad-specific |
| miette | Terminal diagnostics, source snippets and multiple labeled spans | Normalized diagnostics stay independent of the terminal renderer |
| serde + serde_json | Config, JSON reports and plugin messages | Version our own output schema |
| toml | Project and profile configuration | Validate config against a versioned schema |
| clap | CLI argument and subcommand parsing | Keep the library usable without the CLI |
| insta / proptest | Snapshot and property-based tests | Use real KiCad fixtures for integration contracts |

Candidates should be pinned and evaluated in the first implementation PR. Keep the core model independent of any one geometry library so it can be replaced if a KiCad-specific edge case requires it.

## 3. Current prototype audit

The current prototype is a useful behavioral specification, but several current implementation details must not survive the port:

1. The checker is implemented in Python and directly imports the STUSB4500 example generator to populate profile globals. This couples a reusable checker to one example.
2. Rule metadata and execution are centralized in a Python module with a hard-coded list of checks.
3. Geometry helpers, schematic traversal, profile loading and CLI rendering live together.
4. Rule findings are plain strings. A finding does not carry an exact source span, related-object spans, a structured fix, or a machine-stable explanation object.
5. Declarative group layout already has useful concepts: anchors, offsets, midpoint relations, pin-relative relations, member sets and automatically derived frame extents.
6. Tests cover generated examples and individual rule functions, but need a general rule-fixture contract and machine-readable diagnostic snapshots.

The current rule IDs are SCH001–SCH019 on main. The open schematic-hardening work also adds SCH020 (component function/rationale documentation). Preserve existing IDs during migration to avoid breaking examples, tests and user documentation. Treat the hardening branch's SCH020 as part of the migration set once that work is merged.

## 4. Proposed architecture

### 4.1 Workspace layout

Start with a small number of real architectural boundaries. Do not create a crate per rule or over-fragment before APIs settle.

~~~text
Cargo.toml
crates/
  kicad-clippy-core/
    source.rs             # source buffers, spans, line/column mapping
    schematic.rs          # typed semantic adapter over KiCad CST
    hierarchy.rs          # sheet instances and UUID paths
    geometry.rs           # KiCad coordinate/transform semantics
    indexes.rs            # spatial and connectivity indexes
    diagnostic.rs         # normalized diagnostic model
    registry.rs           # rule metadata and registration
    engine.rs             # load, analyze, run and normalize
  kicad-clippy-rules/
    geometry/
    labels/
    fields/
    annotations/
    documentation/
    layout/
  kicad-clippy-profile/
    config.rs
    profile.rs            # declarative project design intent
    constraints.rs
  kicad-clippy-erc/
    cli.rs                # kicad-cli ERC JSON adapter
    normalize.rs
    ipc.rs                # future KiCad 11+ adapter
  kicad-clippy-fixes/
    edit.rs
    planner.rs
    validate.rs
  kicad-clippy-cli/
    main.rs
  kicad-clippy-rule-api/  # public API boundary; stabilize only after core
~~~

The initial implementation may combine core and fixes or core and rules in fewer crates if that reduces scaffolding. Preserve the public dependency direction: the core must not depend on the CLI, GUI, example generator or profile for a particular circuit.

### 4.2 Semantic schematic model

The parser's CST is authoritative for exact source locations and edits. The typed model is an indexed view over the CST, not a replacement that discards original syntax.

Minimum indexed entities:

- Root schematic, project version and sheet-instance hierarchy.
- Symbol instances, references, values, properties, library IDs, unit, position, angle and mirror.
- Embedded library-symbol definitions, pins, pin electrical types and visible graphic primitives.
- Wires, junctions, no-connect markers, buses and bus entries.
- Local, global and hierarchical labels, including shape, angle, anchor and sheet path.
- Text, text boxes, rectangles, polylines, circles, arcs and images.
- UUID/KIID-to-source-span and UUID/KIID-to-sheet-path mappings.
- Derived pin world positions, symbol body geometry, wire networks, net-label groups, annotation bounds and frame membership.

Every semantic object should retain a reference to its original CST node/span and, when available, its KiCad UUID. Avoid copying schematic objects into a second mutable truth.

#### Coordinate precision

Use integer KiCad internal units or a fixed-point representation for identity and connectivity calculations. Convert to millimetres for messages and thresholds. Floating-point values can be used for display and some distance calculations, but not as the sole test of whether two electrical coordinates are identical. KiCad connections depend on exact graphical coordinates and grid placement.

#### Hierarchy

A schematic file can be reused in multiple sheet instances. Diagnostics therefore need both the source file location and the sheet-instance UUID path. One source object may appear in more than one hierarchical context. Never flatten away sheet-instance identity when normalizing native ERC or custom findings.

### 4.3 Rule model and registry

Each rule should be declared through a descriptor plus an implementation.

Conceptual metadata:

~~~text
RuleDescriptor {
    id: "SCH010",
    name: "symbol-field-clearance",
    category: "layout",
    default_level: "deny",
    summary: "...",
    explanation: "...",
    help: "...",
    docs_url: "...",
    tags: ["layout", "visual"],
    phase: "geometry",
    options_schema: ...,
    fix_capabilities: ["proposal"],
}
~~~

Conceptual interface:

~~~rust
trait Rule: Send + Sync {
    fn descriptor(&self) -> &'static RuleDescriptor;
    fn run(&self, context: &AnalysisContext, output: &mut DiagnosticSink);
}
~~~

The actual trait should be object-safe or use a generic registry only if that improves the rule-author experience. Rules should read immutable analysis data and emit diagnostics; they should not mutate the schematic.

Use explicit phases such as:

1. Parse and structural validation.
2. Semantic extraction and normalization.
3. Geometry/index construction.
4. Connectivity graph construction.
5. Geometry and presentation rules.
6. Design-profile rules.
7. Documentation rules.
8. Native ERC collection and merge.
9. Fix planning and post-fix validation.

A rule may subscribe to one or more prepared analyses. Build expensive indexes once per schematic, not once per rule. A rule can declare prerequisites; the engine should reject missing dependencies with a clear internal error rather than silently skip the rule.

### 4.4 Rule levels and default presets

Borrow Clippy's levels and grouping model:

- allow: do not report this rule.
- warn: report, but do not fail the default quality gate.
- deny: report and fail when the rule is violated.

Provide explicit command-line overrides and config overrides. Define stable rule groups such as correctness, layout, style, documentation and pedantic.

Proposed presets:

| Preset | Purpose | Default behavior |
|---|---|---|
| recommended | Everyday project checking | Core correctness/layout checks deny; subjective style checks warn; documentation checks warn where appropriate; native ERC required |
| strict | CI/release gate | All enabled custom errors and warnings fail CI; stricter style and documentation rules enabled |
| pedantic | Detailed review | Adds more opinionated spacing, visual rhythm and documentation checks; opt-in |
| native-only | Native KiCad report through the unified output | Runs KiCad ERC without custom kicad-clippy rules |
| profile-specific | Declared design intent | Applies an explicit project profile in addition to the selected base preset |

Do not enable every possible pedantic check at error severity by default. A useful linter must distinguish actual mistakes from project taste and must not create a permanently noisy backlog.

Example project configuration:

~~~toml
version = 1
preset = "recommended"
native_erc = "required"

[rules.SCH006]
level = "warn"

[rules.SCH016]
level = "warn"

[profile]
name = "stusb4500"

[[profile.groups]]
id = "vbus-monitor"
title = "VBUS MONITOR / FILTER"
members = ["D4", "C4", "R1", "R4"]

[profile.rules.SCH004]
level = "deny"
expected_connections = [["U1.21", "C1.1"], ["U1.23", "C2.1"]]
~~~

The exact config schema is an implementation decision. The key requirement is that profile data describes a design; it does not import executable code from an example.

### 4.5 External rule authors

Support custom rules without requiring a fork of the main engine.

**First stable release:** expose a documented Rust rule API and permit additional rules to be compiled into a custom executable. Keep the built-in registry independent of the built-in rule crate.

**After the API stabilizes:** add isolated runtime rule packs using a versioned WASM interface. Extism is a candidate host/runtime. The host should pass a read-only, versioned semantic snapshot and rule configuration; plugins return versioned diagnostic and fix-proposal messages. Do not pass arbitrary mutable CST internals or expose unrestricted file/network access.

Do not use Rust dynamic-library loading as the third-party ABI. Rust does not promise a stable ABI between independently compiled Rust libraries. A runtime plugin protocol needs a deliberately versioned boundary.

A profile is not a plugin. Profiles declare expected connectivity, group membership, spacing/layout constraints and component documentation. Plugins add executable checks for organizations or users who need bespoke analysis.

## 5. Diagnostics, spans and reporting

This is a core acceptance requirement, not cosmetic polish.

### 5.1 Diagnostic structure

Every custom diagnostic should contain:

- Stable code and rule ID.
- Severity/level.
- Short message suitable for a diagnostic list.
- Detailed explanation: what was detected and why it matters.
- Primary source span.
- Zero or more secondary labeled spans for related wires, pins, labels, fields or annotations.
- Sheet-instance path and affected KiCad UUIDs when known.
- One or more physical schematic coordinates, in millimetres.
- Notes/help and a stable documentation URL.
- Optional fix proposal IDs.

Keep source positions separate from physical drawing positions. A text box's source span and its x/y/angle in the drawing are different pieces of data.

Use half-open byte spans into the original source buffer. Build line-start indexes once to convert byte spans to line/column efficiently. Convert to UTF-16 code-unit positions for LSP-compatible consumers. For a multi-sheet hierarchical design, include the sheet UUID path as structured metadata, not only as a formatted path string.

Example terminal result:

~~~text
error[SCH003]: GND label overlaps a wire
  --> power.kicad_sch:418:5
   |
418| (hierarchical_label "GND" ...)
   |     ^^^^^^^^^^^^^^^^^^^^^^^^^ primary label span
   |
   = related: power.kicad_sch:401:5
   |          the intersecting wire segment
   |
   = help: Move the label to a short perpendicular wire stub.
   = note: This rule checks visual label clearance; native ERC remains authoritative for connectivity.
   = docs: https://.../rules/SCH003
~~~

A diagnostic should remain useful when printed without colour. Colour is supplemental.

### 5.2 Output formats

- Human terminal output: use miette for source snippets, multiple labels, severity, help, and docs links.
- Versioned JSON: stable kicad-clippy schema for IDEs, scripts, tests and the future GUI.
- SARIF 2.1.0: GitHub code scanning and other CI consumers. Map rule metadata to rule descriptors; map spans and object locations to physical locations; map fix plans to SARIF fixes and artifact changes.

SARIF fixes should consist of minimal, non-overlapping replacements. See the [OASIS SARIF 2.1.0 specification](https://docs.oasis-open.org/sarif/sarif/v2.1.0/sarif-v2.1.0.html).

Native ERC results should retain their original KiCad error type/settings key, severity, excluded state, description, affected item UUIDs and sheet path inside the normalized record. Do not invent source spans where the ERC JSON only provides an object UUID and physical position. Resolve exact spans through the CST UUID index; fall back to an explicitly approximate location only when exact mapping is unavailable.

## 6. Native ERC integration

### 6.1 First implementation: CLI adapter

The initial adapter should:

1. Detect and report the KiCad CLI version.
2. Run the schematic ERC command with JSON and all relevant severity classes.
3. Capture stdout/stderr and the report path.
4. Parse the report against the KiCad schema/version.
5. Preserve native ignored-check and excluded-violation metadata.
6. Normalize each native finding into the common diagnostic store.
7. Fail clearly when the CLI is unavailable, exits unexpectedly, or emits an unsupported schema.

Do not hide a missing KiCad executable by silently skipping native ERC in CI. Developers may explicitly request custom-only mode for local work, but the recommended/strict CI presets must require native ERC.

Resolve project variables and project context deliberately. Provide explicit variable overrides when the project cannot be discovered from the schematic path. Keep the raw native JSON report as a CI artifact to make the adapter auditable.

### 6.2 Future implementation: KiCad 11 IPC adapter

When the supported Eeschema IPC API is available in the target release, add an IPC adapter that requests the native ERC task and obtains ErcMarker results. This enables a future UI to retrieve native markers without spawning an extra CLI process.

The IPC adapter and CLI adapter must normalize to the same internal native-ERC type. Their output should be compared in integration tests where both are available.

### 6.3 Why not duplicate native ERC?

KiCad already checks the electrical conditions that users expect ERC to cover: connected/unconnected pins, power-driver conditions, pin conflicts, no-connect conflicts, dangling labels/wires, label and bus conflicts, unit/annotation conditions, and other ERC categories. Its set evolves with the KiCad release.

A parallel implementation would drift, disagree with KiCad's project severity settings and exclusions, and make users triage two versions of the same electrical finding. kicad-clippy should add checks that native ERC does not intend to own.

## 7. Migration plan for SCH001–SCH020

Preserve the current IDs during the port. The table below separates genuine kicad-clippy ownership from native ERC coverage and points out where an existing rule must be narrowed to avoid duplicate findings.

| Existing ID | Current intent | Migration owner | Porting decision |
|---|---|---|---|
| SCH001 | Wire geometry: diagonal/crossing/overlap/unmarked branches | kicad-clippy + ERC boundary | Keep orthogonality and visual routing checks. Compare crossings/branches against native ERC first; do not emit duplicate dangling/connectivity findings. |
| SCH002 | Label topology and distinct wire islands | kicad-clippy + ERC boundary | Keep design conventions for label placement and use. Defer dangling/unconnected-label correctness to native ERC. |
| SCH003 | Label glyph/wire clearance | kicad-clippy | Geometry/presentation rule; retain exact label and wire spans. |
| SCH004 | Declared physical net connectivity | Profile rule | Check declared design-intent connections. Use extracted connectivity/netlist; do not emulate ERC's general pin checks. |
| SCH005 | Ground-pin orientation | Profile rule | Schematic convention, not electrical ERC. |
| SCH006 | Component spacing and reference/value offsets | kicad-clippy | Warning by default; offer a layout proposal where unambiguous. |
| SCH007 | Group metadata | Profile rule | Validate declared groups, titles, membership and descriptions. |
| SCH008 | Text-box sizing, padding and overlap | kicad-clippy | General annotation geometry. |
| SCH009 | Group/frame containment | Profile rule | Requires declared frame membership and profile metadata. |
| SCH010 | Symbol field clearance | kicad-clippy | General geometry rule with related object spans and move proposals. |
| SCH011 | Title and annotation structure | kicad-clippy | General text/box rules plus profile-specific block-comment requirements. |
| SCH012 | Frame-content clearance | Profile rule | Enforce declared frame padding/margins. |
| SCH013 | Wire obstacle clearance | kicad-clippy | Use actual symbol primitive geometry; wire endpoints on intended pins remain allowed. |
| SCH014 | Shared-pin net contracts | Profile rule | Compare declared pin groups with the derived net graph; avoid duplicating native electrical failures. |
| SCH015 | Required no-connect markers | Profile rule + ERC boundary | Profile may require particular pins to remain NC; native ERC owns invalid NC/pin connectivity. |
| SCH016 | Wire compactness | Profile rule | Warning by default; compute per declared group and propose route alternatives later. |
| SCH017 | Circuit documentation and source evidence | Profile rule | Validate declared component/group purpose and evidence metadata. |
| SCH018 | Label text collisions | kicad-clippy | General visual collision rule. |
| SCH019 | Label-kind consistency | kicad-clippy + ERC boundary | Keep project-wide conventions; defer native same-local/global-label electrical conflicts to ERC. |
| SCH020 | Component function and rationale documentation | Profile rule | Keep from the current hardening branch; make description requirements declarative and testable. |

The existing catalogue has 19 IDs on main; SCH020 is present on the open hardening branch. Keep that branch's addition in the migration plan, but do not silently assume it has already landed on main.

### Recommended future rule families

New rules should be introduced only after the source model and fix safety are proven. Useful groups include:

- **Geometry:** visible symbol primitives, wire crossings, label and field collisions, text-box margins, clearance, grid alignment.
- **Connectivity conventions:** local/global/hierarchical label policies, expected physical relationships, required NC design contracts.
- **Layout:** component groups, relative alignment, spacing, group frame packing and route compactness.
- **Documentation:** component purpose, source evidence, block metadata and native title-block conventions.
- **Compatibility/structure:** unsupported schema constructs, unknown fields, malformed UUID references and ambiguous hierarchy mapping. Unknown valid fields must be preserved and reported as compatibility information, not automatically treated as errors.

## 8. Fix proposals and automatic layout

A finding and its proposed correction must be separate objects. Not every diagnostic is safely fixable.

### 8.1 Fix applicability

Use explicit applicability levels:

| Level | Meaning | CLI behavior |
|---|---|---|
| machine-applicable | Exact, deterministic edit with proven preconditions | Eligible for non-interactive safe apply |
| maybe-incorrect | A plausible proposal that needs review | Show diff; do not apply under safe mode |
| manual | Explanation only; no trustworthy edit can be derived | Never auto-apply |

A fix plan should contain:

- Title and rationale.
- Applicability.
- Affected source files and original content hash.
- Minimal, sorted, non-overlapping text replacements.
- Expected object UUIDs and current geometry preconditions.
- A postcondition describing which rule should be resolved.
- Any connectivity invariant that must remain unchanged.

Apply all edits for one fix atomically. Abort if the source hash, object UUIDs or expected geometry have changed since the proposal was generated. Never partially apply a multi-object layout fix.

### 8.2 Layout engine

Port the valuable concepts from the current generator, not its STUSB4500-specific code:

- Group owns a stable ID, title, description, component members and optional evidence.
- Anchor component establishes a local coordinate frame.
- Relative constraints include offset, midpoint-offset and pin-relative placement.
- Frame dimensions derive from member pin/geometry extents, annotations, labels and required padding.
- Groups can be packed in a horizontal/vertical flow or aligned to another group.
- The solver returns proposed coordinates and a complete edit plan; it does not mutate the source during analysis.

For example, a user can declare T1 and T4 as a group, set their relative offset, align a gate resistor to their midpoint and attach a group frame. The solver can propose a tighter arrangement while preserving the physical connectivity graph.

Moving a symbol is more complex than changing its origin: attached wire endpoints, pins, labels, fields, junctions, frames and possibly hierarchical instances may need coordinated changes. Therefore:

- Start with safe field/text relocation and simple orthogonal wire endpoint edits.
- Allow multi-object layout moves only when every affected connection is identified and transformed.
- Preserve the exact pre-fix net graph and declared pin-to-net assignments.
- Render the result with KiCad and rerun ERC and kicad-clippy before presenting it as a verified fix.
- Mark ambiguous routing/layout changes maybe-incorrect, and require review.

Do not use a generic optimizer that reduces distances without understanding electrical intent. A visually shorter route can change connectivity or create an unintended branch.

### 8.3 Validation after fixes

A fix is not considered verified merely because the text edit parses. The validation sequence is:

1. Apply edits to a temporary copy.
2. Parse the result with the lossless parser and semantic adapter.
3. Validate object UUID uniqueness and reference integrity.
4. Compare the before/after connectivity graph and declared critical pin-to-net contracts.
5. Run native KiCad ERC.
6. Run the full enabled kicad-clippy rule set.
7. Export an SVG with KiCad CLI; visual review is still required for layout-sensitive changes.
8. Only then offer an atomic write to the user's original file, with backup/recovery behavior.

## 9. CLI and configuration experience

Proposed commands:

~~~text
kicad-clippy lint <schematic>
kicad-clippy lint <schematic> --format json
kicad-clippy lint <schematic> --format sarif --output report.sarif
kicad-clippy lint <schematic> --preset strict
kicad-clippy lint <schematic> --allow SCH006 --deny SCH010
kicad-clippy rules
kicad-clippy explain SCH010
kicad-clippy fix <schematic> --dry-run
kicad-clippy fix <schematic> --safe
kicad-clippy layout <schematic> --propose
~~~

The configuration file should be named consistently, for example kicad-clippy.toml, and be discoverable from the schematic directory upward. It should support:

- Preset selection.
- Rule levels and per-rule options.
- Project profile selection.
- Declared circuit groups, membership and layout constraints.
- Expected pin/net contracts and NC requirements.
- Suppressions that include rule ID, affected object UUID(s), scope and a mandatory reason.
- Native ERC requirement, project-variable overrides and executable selection.

Avoid unstructured comment-based suppressions in the first release. A structured suppression tied to a UUID/object and a reason is auditable and less likely to be silently lost during schematic rewrites.

## 10. Future KiCad integration

KiCad's add-on API is language-agnostic and the current developer documentation describes executable plugins as separate processes launched with a command line. KiCad 11 adds Eeschema plugin support, so a Rust executable is a plausible future integration.

The future UI should consume the same JSON diagnostic objects as the CLI and should support:

- Group diagnostics by sheet and rule.
- Explain the rule with description, rationale and documentation link.
- Select/cross-probe the affected KiCad object(s) when supported by IPC.
- Preview a fix diff or geometric layout proposal.
- Apply only verified or explicitly approved changes.
- Rerun native ERC and custom rules after the edit.

Do not claim that third-party diagnostics can be injected into KiCad's native ERC marker store. The current API provides native ERC markers; an external linter can display its own diagnostics and cross-probe their source objects. Whether a later KiCad API supports custom marker injection must be confirmed upstream before designing around it.

A practical transition is:

1. Rust CLI is the source of truth.
2. Existing Python plugin may invoke the Rust executable as an external process when a UI action is useful.
3. When the target KiCad release supports schematic-editor executable plugins reliably, add a Rust IPC executable adapter.
4. Keep presentation and diagnostic normalization independent of the IPC transport.

## 11. Strict Rust and Python quality gates

The new Rust code should begin with a strict gate so it does not accumulate lint debt.

Recommended workspace policy:

~~~toml
[workspace.lints.rust]
unsafe_code = "forbid"
warnings = "deny"
unused_must_use = "deny"

[workspace.lints.clippy]
all = { level = "deny", priority = -1 }
pedantic = { level = "deny", priority = -1 }
nursery = { level = "warn", priority = -1 }
unwrap_used = "deny"
expect_used = "deny"
panic = "deny"
todo = "deny"
unimplemented = "deny"
dbg_macro = "deny"
~~~

Each workspace crate should opt into the shared policy with a package-level lints.workspace = true setting; workspace lint declarations are not automatically inherited by every member. Pin the minimum stable Rust toolchain in rust-toolchain.toml and test the exact lint set against it. Add narrowly scoped, documented exceptions only where a lint is not actionable or the API shape genuinely requires it. Do not silence lints globally to get a green build.

Required CI commands:

~~~bash
cargo fmt --all -- --check
cargo clippy --workspace --all-targets --all-features -- -D warnings
cargo test --workspace --all-features
cargo test --workspace --doc
~~~

Add dependency/license/audit checks once the workspace dependencies settle. Use a checked-in Cargo.lock for the executable workspace.

The first implementation should be Rust-only. Do not add a new Python checker or duplicate Python rule engine. If the existing Python plugin is changed to bridge to the executable, use the repository's existing isolated test/dependency setup and a strict Ruff configuration; add pyright or mypy for touched interfaces. Avoid introducing a second long-lived Python toolchain or a backlog of newly selected lint categories.

## 12. Testing strategy

Use layers of tests, modeled partly on Clippy's UI and rustfix tests:

1. Parser/semantic adapter tests: real schematic snippets, unknown fields, embedded library symbols, all label types and nested sheet paths.
2. Rule unit tests: minimal input for each pass/fail case; assert code, severity, primary span, secondary spans and fix applicability.
3. Snapshot tests: terminal and JSON/SARIF output; source line/column mapping; stable order.
4. Fix tests: source input, proposed fix, exact output fixture, and proof that edits are minimal and non-overlapping.
5. Property tests: segment intersection, orientation/mirroring transforms, grid snapping and graph invariants.
6. KiCad corpus tests: representative real schematics across supported file versions; preserve unknown constructs.
7. Native ERC integration tests: generate JSON with KiCad CLI, parse its schema, normalize UUIDs/sheet paths and compare known native findings.
8. Post-fix integration: re-run KiCad CLI ERC, netlist checks, kicad-clippy and SVG rendering.
9. GUI smoke tests: only after the IPC adapter/UI phase; source-level tests do not prove selection, cross-probing or visual usability.

Use snapshot tests to make diagnostic text and spans reviewable. Use property-based geometry tests to catch edge cases that a few hand-authored schematic fixtures will miss.

## 13. Staged implementation plan

### Phase 0 — architecture approval (this document)

- Agree that the linter complements native ERC rather than modifying its catalogue.
- Agree the diagnostic, rule, profile and fix boundaries.
- Agree the parser reuse decision and license review.
- Agree the first supported KiCad CLI version and schema policy.

**Exit condition:** Architecture and scope approved; no production linter code yet.

### Phase 1 — foundation and native ERC adapter

- Add Rust workspace, CLI and strict lint gates.
- Integrate the lossless S-expression parser behind a project-owned adapter.
- Build source map, semantic object IDs, UUID and hierarchy indexes.
- Define normalized diagnostic schema and human/JSON/SARIF renderers.
- Implement native ERC CLI invocation and JSON normalizer.
- Provide rule registry, rule metadata and config loader with no custom rules yet.

**Exit condition:** A command can run native ERC, preserve/parse findings and render precise native diagnostics without mutating the schematic.

### Phase 2 — generic custom rules

- Port SCH001–SCH003, SCH008, SCH010, SCH011, SCH013, SCH018 and SCH019 after narrowing overlaps with ERC.
- Implement spatial indexes and exact geometry predicates.
- Add rule descriptions, documentation and span tests.
- Preserve legacy IDs.

**Exit condition:** Generic layout/style rules run on schematics outside the STUSB4500 example.

### Phase 3 — profile rules and migration

- Define declarative project profile configuration.
- Port SCH004–SCH007, SCH009, SCH012, SCH014–SCH017 and SCH020.
- Move STUSB4500 group/constraint metadata out of executable Python generator imports.
- Run old and new checker in comparison mode on the repository's fixture corpus.
- Remove the Python checker only after parity and intentional behavior changes are reviewed.

**Exit condition:** Current reference schematic is covered by the new engine and profile; no rule depends on the Python generator.

### Phase 4 — fix and layout proposals

- Implement source-edit transactions and safe applicability classifications.
- Add field/text placement proposals first.
- Port declarative component/group layout constraints.
- Add connectivity-preservation verification and KiCad post-fix validation.
- Keep ambiguous routing changes as proposals, not automatic edits.

**Exit condition:** Fixes can be previewed, safely applied and validated on a representative fixture set.

### Phase 5 — third-party rules

- Stabilize and document the Rust rule API.
- Begin with custom compiled rule packs.
- Add the isolated WASM rule interface only after the host/diagnostic schemas are stable.
- Publish example rule packs and compatibility guarantees.

**Exit condition:** An external author can create and test a custom rule without modifying the kicad-clippy source tree.

### Phase 6 — KiCad UI integration

- Validate the target KiCad release's Eeschema IPC and executable-plugin support.
- Add IPC transport and cross-probing.
- Build a GUI over the common diagnostic/fix data.
- Test on actual KiCad builds, not only mocked IPC.

**Exit condition:** A user can inspect custom/native diagnostics in KiCad, select their source objects and review fixes without duplicating the analysis logic.

## 14. Non-goals and safeguards

- Do not fork the Rust compiler's Clippy or use rustc-private crates.
- Do not fork KiCad or patch its native ERC registry just to add style rules.
- Do not reimplement native electrical ERC checks in custom rules.
- Do not discard unknown S-expression nodes or rewrite untouched schematics from a lossy AST.
- Do not apply speculative multi-object layout edits silently.
- Do not use floating-point equality as the only basis for electrical coordinate identity.
- Do not let profiles import executable code or access the example generator.
- Do not make external rules mutate the analysis model.
- Do not claim native ERC marker injection for custom rules without an upstream-supported API.
- Do not create a GUI before the CLI diagnostics and fix contracts are stable.

## 15. References and source audit

### Rust and diagnostic architecture

- [Clippy: Adding a lint](https://doc.rust-lang.org/stable/clippy/development/adding_lints.html)
- [Cargo lint configuration](https://doc.rust-lang.org/cargo/reference/manifest.html#the-lints-section)
- [miette diagnostic API](https://docs.rs/miette/latest/miette/)
- [SARIF 2.1.0 specification](https://docs.oasis-open.org/sarif/sarif/v2.1.0/sarif-v2.1.0.html)

### KiCad

- [KiCad CLI schematic ERC](https://docs.kicad.org/master/en/cli/cli.html#_schematic_erc)
- [KiCad IPC add-on developer documentation](https://dev-docs.kicad.org/en/apis-and-binding/ipc-api/for-addon-developers/)
- [KiCad schematic file format](https://dev-docs.kicad.org/en/file-formats/sexpr-schematic/)
- [KiCad ERC report schema](https://schemas.kicad.org/erc.v1.json)
- [KiCad source: ERC tester](https://github.com/KiCad/kicad-source-mirror/blob/master/eeschema/erc/erc.h)
- [KiCad source: ERC item catalogue](https://github.com/KiCad/kicad-source-mirror/blob/master/eeschema/erc/erc_item.cpp)
- [KiCad source: ERC report writer](https://github.com/KiCad/kicad-source-mirror/blob/master/eeschema/erc/erc_report.cpp)
- [KiCad source: schematic ERC task](https://github.com/KiCad/kicad-source-mirror/blob/master/eeschema/api/api_handler_sch_tasks.cpp)
- [KiCad source: ErcTaskResult protocol](https://github.com/KiCad/kicad-source-mirror/blob/master/api/proto/schematic/schematic_tasks.proto)
- [KiCad source: ErcMarker protocol](https://github.com/KiCad/kicad-source-mirror/blob/master/api/proto/schematic/schematic_rules.proto)

### Rust parser and analysis dependencies

- [kiutils-rs repository](https://github.com/Milind220/kiutils-rs)
- [kiutils_sexpr documentation](https://docs.rs/kiutils_sexpr/latest/kiutils_sexpr/)
- [geo](https://docs.rs/geo/latest/geo/)
- [rstar](https://docs.rs/rstar/latest/rstar/)
- [petgraph](https://docs.rs/petgraph/latest/petgraph/)
- [Extism](https://extism.org/docs/)

---

**Recommendation:** Approve this architecture, then implement Phase 1 as a separately reviewable foundation PR. The first implementation should prove source spans, native ERC normalization and stable diagnostics before any large-scale rule port or automatic layout editing.
