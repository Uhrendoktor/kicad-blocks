# KiCad Blocks

**Reusable, black-box functional blocks for KiCad schematics.**

KiCad Blocks turns a hierarchical schematic sheet into a reusable library symbol. Place the block like any other KiCad symbol, while its editable implementation, configuration and optional routed PCB layout stay packaged as a separate library asset.

<p align="center">
  <img src="https://raw.githubusercontent.com/Uhrendoktor/kicad-blocks/refs/heads/docs/previews/previews/block-picker-workflow.gif" alt="Search the functional-block library, filter results and choose a configuration profile" width="900">
</p>

<p align="center">
  <a href="docs/getting-started.md">Get started</a> ·
  <a href="docs/workflows.md">Explore workflows</a> ·
  <a href="docs/reference.md">Feature reference</a> ·
  <a href="docs/visual-evidence.md">See the visual guide</a>
</p>

## Why functional blocks?

Keep a complex circuit out of the top-level schematic without hiding its engineering detail. The project sees a normal KiCad symbol and its external pins; the implementation remains a native, editable KiCad schematic in the block library.

<p align="center">
  <img src="https://raw.githubusercontent.com/Uhrendoktor/kicad-blocks/refs/heads/docs/previews/previews/functional-block-facade.svg" alt="KiCad-native USB-C power functional-block facade" width="520">
</p>

<p align="center">
  <img src="https://raw.githubusercontent.com/Uhrendoktor/kicad-blocks/refs/heads/docs/previews/previews/functional-block-implementation.svg" alt="Editable STUSB4500 USB-PD sink reference implementation schematic" width="1000">
</p>

The reference implementation is based on the STUSB4500QTR application circuit in **ST DS12499 Rev. 8, Figure 10**. It includes the USB-C connector, protection devices, power-path MOSFETs, passives and MCU interface. The [STUSB4500 example guide](examples/stusb4500/README.md) describes the circuit and the example 5 V, 9 V and 12 V PDO profiles.

## Features at a glance

| Capability | What it does |
| --- | --- |
| **Create from a sheet** | Extract a selected hierarchical sheet symbol or the currently open sheet. Facade pins and electrical types come from root-sheet hierarchical labels. |
| **Searchable block library** | Browse multiple registered folders, search names/descriptions/pins/variants, preview the facade and place with KiCad's native symbol chooser. |
| **Editable implementations** | Keep implementation schematics in a separate `.kicad_block` folder. Edit in Eeschema and publish changes to the library. |
| **Variants and profiles** | Select alternative implementation schematics and named profiles that bind a variant, typed parameter overrides and a PCB layout. |
| **Per-instance configuration** | Configure one placed symbol without changing other instances or the shared library. |
| **Project-local copies** | Fork a library block for one project and synchronize changes in either direction, detecting conflicting edits. |
| **Nested composition** | Compose child blocks into a parent implementation using explicit pin-to-net mapping and rotation. |
| **Recursive checks and BOM** | Validate library assets/dependencies, run recursive ERC and export BOM data for nested implementations. |
| **PCB layout reuse** | Merge one or more pre-routed layouts into a separate board copy, then move each imported group with KiCad's native PCB placement tool. |

## A tour of the interface

The library browser is the primary selection and placement workflow. It shows the available blocks, their connection interface and implementation options together.

<p align="center">
  <img src="https://raw.githubusercontent.com/Uhrendoktor/kicad-blocks/refs/heads/docs/previews/previews/library-browser-light.png" alt="Functional Block Library browser in light theme" width="900">
</p>

Less-frequent commands live in the task hub, grouped by the work being performed.

<p align="center">
  <img src="https://raw.githubusercontent.com/Uhrendoktor/kicad-blocks/refs/heads/docs/previews/previews/task-hub-light.png" alt="KiCad Blocks task hub: Use and build" width="760">
</p>

<p align="center">
  <img src="https://raw.githubusercontent.com/Uhrendoktor/kicad-blocks/refs/heads/docs/previews/previews/task-hub-edit-maintain-light.png" alt="KiCad Blocks task hub: Edit and maintain" width="760">
</p>

<p align="center">
  <img src="https://raw.githubusercontent.com/Uhrendoktor/kicad-blocks/refs/heads/docs/previews/previews/task-hub-checks-pcb-light.png" alt="KiCad Blocks task hub: Checks and PCB" width="760">
</p>

The [visual guide](docs/visual-evidence.md) includes the light/dark variants, folder manager, create form, instance configuration and variant editor.

## Get started

1. Check the [supported environment](docs/getting-started.md#supported-environment) before installing. The validated target is KiCad **10.99.0 development nightly**, Linux **x86_64**, and CPython **3.12–3.14**.
2. Follow [installation and first launch](docs/getting-started.md). Enable the KiCad IPC API and select the matching Python interpreter in KiCad's plugin preferences.
3. In Eeschema, use **Browse and Place Functional Blocks** to explore a library, or **Create Block from Selected Sheet** to turn a hierarchical sheet into a reusable block.

> **Release status — 13 November 2026:** V1.1.0 is the version recorded in the repository, but its installable release archive has not yet been published. Check [GitHub Releases](https://github.com/Uhrendoktor/kicad-blocks/releases) for the archive before following the ZIP installation steps.

## Documentation

- **[Getting started](docs/getting-started.md)** — prerequisites, installation, KiCad preferences, first launch and troubleshooting.
- **[Feature workflows](docs/workflows.md)** — step-by-step instructions for creating, placing, configuring, editing, nesting, validating and exporting blocks.
- **[Feature and data reference](docs/reference.md)** — action map, block storage, instance fields, parameters, layouts, compatibility and functional boundaries.
- **[Visual evidence](docs/visual-evidence.md)** — real UI captures, KiCad-native renders, provenance and test coverage.
- **[STUSB4500 example](examples/stusb4500/README.md)** — a complete reference block and its editable source.
- **[Changelog](CHANGELOG.md)** — release history and known coverage limits.

## How blocks fit into a project

A top-level schematic contains the facade symbol and its external connections. The block library stores the internal schematic and optional implementation variants/layouts. A project-local sidecar maps each placed instance to its independent local copy when needed.

This is intentionally **not** a synthetic hierarchical sheet. Native project-level BOM, netlist and ERC operate on the facade and its pins. Use the plugin's recursive BOM/ERC actions when you need results from internal implementations and nested dependencies.

## Supported environment

| Component | Validated target |
| --- | --- |
| KiCad | **10.99.0 development nightly** (pre-release development build for KiCad 11) |
| Operating system / CPU | Linux x86_64 |
| Python | CPython 3.12, 3.13 or 3.14, with Tkinter |
| KiCad API | IPC API enabled in Eeschema; enable it in PCB Editor for imported-layout placement |

Stable KiCad 10.x, KiCad 11 stable, Windows, macOS, ARM and other interpreter ABIs are not validated by this version. The installer may recognize newer KiCad version directories, but that does not make them supported targets. Read the [full compatibility notes](docs/getting-started.md#supported-environment) before installation.

## Contribute and report issues

For a bug report, include the KiCad build, operating system, CPU architecture, Python version selected in KiCad, the action being performed and any relevant log or validation report. For feature requests, describe the schematic workflow and the expected KiCad-native behavior.

- [Open an issue](https://github.com/Uhrendoktor/kicad-blocks/issues)
- [Review the repository](https://github.com/Uhrendoktor/kicad-blocks)
- [Read the changelog](CHANGELOG.md)
