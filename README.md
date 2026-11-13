# KiCad Blocks

**Share complete KiCad circuit setups as reusable library components.**

A block is placed as one library symbol, while its full editable schematic stays in the shared library. Each placed instance can select its own circuit variant. Clone or download the library, register it once, and browse and place blocks in any project—without copying implementation sheets and symbol files.

[![CI](https://github.com/Uhrendoktor/kicad-blocks/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/Uhrendoktor/kicad-blocks/actions/workflows/ci.yml)
[![MIT License](https://img.shields.io/github/license/Uhrendoktor/kicad-blocks)](LICENSE)
![KiCad 10.99 nightly](https://img.shields.io/badge/KiCad-10.99%20nightly-blue?logo=kicad)
![Python 3.12–3.14](https://img.shields.io/badge/Python-3.12%E2%80%933.14-blue?logo=python)

**[Get started](docs/getting-started.md)** · **[Feature workflows](docs/workflows.md)** · **[Visual gallery](docs/visual-evidence.md)** · **[STUSB4500 example](examples/stusb4500/README.md)**

## What stays in the library

- **Complete schematic implementation.** The editable hierarchical-sheet circuit stays in the library; each project uses its facade symbol.
- **Variants and profiles.** Store alternative implementations and named configurations—for example, different output voltages or component parameters—and choose one independently for each placed instance.
- **Project-local copies.** Make an independent copy for a project and synchronize it with the shared version later. Conflicting edits are detected rather than silently overwritten.
- **Related design data.** Include nested block dependencies and optional pre-routed PCB layouts. Recursive ERC and BOM actions can inspect the implementation.

> KiCad's native [Design Blocks](https://docs.kicad.org/10.0/en/eeschema/eeschema.html#design-blocks) already share schematic fragments and optional PCB layouts. KiCad Blocks exposes a separately stored, nestable implementation through a normal library symbol, with implementation variants selectable per instance.

## How it works

Create a block from a hierarchical sheet. Its root-sheet hierarchical labels become the external pins of a compact facade symbol. The original circuit is stored separately as a `.kicad_block` library asset, ready to edit, configure and reuse.

<p align="center">
  <a href="docs/visual-evidence.md">
    <picture>
      <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/Uhrendoktor/kicad-blocks/refs/heads/docs/previews/previews/library-browser-details-dark.png">
      <img src="https://raw.githubusercontent.com/Uhrendoktor/kicad-blocks/refs/heads/docs/previews/previews/library-browser-details-light.png" alt="KiCad Blocks library browser showing a searchable block library, the STUSB4500 facade pins and its configuration overview" width="620">
    </picture>
  </a>
  <br>
  <sub>Browse blocks, inspect their interface and place them with KiCad's native symbol tool.</sub>
</p>

## What you can do

- **Create** — extract a reusable block from a selected hierarchical sheet; facade pins and electrical types are derived from its labels.
- **Browse and place** — search across registered libraries, inspect pins and implementation choices, then place the facade as a normal KiCad symbol.
- **Configure** — choose per-instance implementation variants, named profiles, typed parameters and optional PCB layouts.
- **Maintain and compose** — edit and publish implementations, create project-local copies, synchronize changes and nest reusable blocks.
- **Verify and reuse** — validate libraries, run recursive ERC, export recursive BOM data and merge stored PCB layouts into a separate board copy.

> **Model boundary:** KiCad's top-level schematic, netlist and standard ERC see the facade symbol and its external pins—not the separate internal circuit. Use the recursive BOM/ERC actions when you need implementation-level results.

## A real circuit example

The repository includes an editable KiCad reconstruction of the **STUSB4500QTR USB-C Power Delivery sink** application circuit, based on ST's DS12499 Rev. 8, Figure 10. It provides a concrete example of a complete circuit implementation and its external interface.

See the [STUSB4500 example guide](examples/stusb4500/README.md) for connectivity, components and source schematic.

## Get started

**Validated target:** KiCad **10.99.0 development nightly**, Linux **x86_64**, CPython **3.12–3.14** with Tkinter. Stable KiCad 10.x, KiCad 11 stable, Windows, macOS and ARM are not validated.

1. Check the [full compatibility and prerequisites](docs/getting-started.md#supported-environment).
2. Follow the [installation and first-run guide](docs/getting-started.md). The V1.1.0 plugin ZIP is not yet published; check [Releases](https://github.com/Uhrendoktor/kicad-blocks/releases) for an installable package before using the archive instructions.
3. In Eeschema, choose **Browse and Place Functional Blocks** to use a library, or **Create Block from Selected Sheet** to make your first block.

## Documentation

| Guide | Contents |
| --- | --- |
| [Getting started](docs/getting-started.md) | Supported environment, installation, KiCad setup and troubleshooting. |
| [Feature workflows](docs/workflows.md) | Creating, placing, configuring, editing, nesting, validating and PCB reuse. |
| [Feature reference](docs/reference.md) | Actions, block storage, instance fields, parameters and technical boundaries. |
| [Visual evidence](docs/visual-evidence.md) | Full light/dark UI gallery, native KiCad renders and test coverage. |
| [Changelog](CHANGELOG.md) | Version history and known coverage limits. |

## Support and contributions

[Open an issue](https://github.com/Uhrendoktor/kicad-blocks/issues) for bugs or feature requests. Include your KiCad build, operating system, CPU architecture, selected Python version, the action you were using and any relevant KiCad Blocks logs or validation report.

Contributions are welcome. Start with an issue to discuss larger changes, then submit a pull request.

## License

KiCad Blocks is released under the [MIT License](LICENSE).
