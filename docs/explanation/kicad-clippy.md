
# Rust schematic linting

kicad-clippy is the Rust-based schematic linting tool being introduced in this repository. Its first implementation milestone establishes the parser, shared diagnostic model, and adapter to KiCad's own Electrical Rules Checker (ERC).

## Current implementation boundary

The current binary validates that a schematic is a well-formed KiCad S-expression document, then invokes the installed KiCad CLI to run native ERC. Native ERC remains the authority for electrical-rule findings. The Rust adapter normalizes the per-sheet JSON report into diagnostics while retaining sheet paths, affected-item UUIDs, severity, exclusion state, and physical schematic coordinates.

**This first milestone does not yet run custom SCH001–SCH020 rules.** Rule migration, project profiles, source-level diagnostic spans, fix proposals, and the graphical editor integration are subsequent implementation stages. Do not treat a successful parser/ERC run as a complete schematic-style audit.

## Build

Run these commands from the repository root with a stable Rust toolchain installed:

~~~bash
cargo fmt --all -- --check
cargo clippy --workspace --all-targets --all-features -- -D warnings
cargo test --workspace --all-features
cargo test --workspace --doc
~~~

## Run

Run native ERC and show human-readable diagnostics:

~~~bash
cargo run -p kicad-clippy -- lint path/to/design.kicad_sch
~~~

Select the KiCad CLI executable explicitly when it is not named kicad-cli or kicad-cli-nightly:

~~~bash
cargo run -p kicad-clippy -- lint path/to/design.kicad_sch --kicad-cli /path/to/kicad-cli
~~~

Emit machine-readable diagnostics:

~~~bash
cargo run -p kicad-clippy -- lint path/to/design.kicad_sch --format json
~~~

Parse the schematic without running native ERC:

~~~bash
cargo run -p kicad-clippy -- lint path/to/design.kicad_sch --skip-erc
~~~

Native ERC runs by default. The skip option is intended for parser-only development and environments where KiCad is not installed; it must not be used as a substitute for electrical validation.

## Architecture

The workspace separates shared data contracts, the lossless schematic parser, the native ERC adapter, and the CLI. The shared model deliberately distinguishes half-open UTF-8 byte spans from KiCad's millimetre coordinates. A native ERC marker generally has a physical location and KiCad item UUID, not an exact source-text span.

See the [kicad-clippy architecture proposal](https://github.com/Uhrendoktor/kicad-blocks/pull/53) for the planned rule catalogue, profiles, fix safety model, layout validation, and future editor integration.
