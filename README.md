# KiCad Blocks

**Reusable, black-box functional blocks for KiCad schematics.**

Turn a hierarchical schematic sheet into a reusable library symbol. Place the block like any other KiCad symbol, while its editable implementation, configuration and optional routed PCB layout remain packaged as a separate library asset.

<p align="center">
  <img src="https://raw.githubusercontent.com/Uhrendoktor/kicad-blocks/refs/heads/docs/previews/previews/block-picker-workflow.gif" alt="Search the functional-block library, filter results and choose a configuration profile" width="900">
</p>

<p align="center">
  <a href="docs/getting-started.md">Get started</a> ·
  <a href="docs/workflows.md">Explore workflows</a> ·
  <a href="docs/reference.md">Feature reference</a> ·
  <a href="docs/visual-evidence.md">Visual gallery</a>
</p>

![CI](https://github.com/Uhrendoktor/kicad-blocks/actions/workflows/ci.yml/badge.svg?branch=main)
![License](https://img.shields.io/github/license/Uhrendoktor/kicad-blocks)
![GitHub stars](https://img.shields.io/github/stars/Uhrendoktor/kicad-blocks?style=social)

## Why functional blocks?

A block keeps a complex circuit out of the top-level schematic without hiding its engineering detail. The project sees a normal KiCad symbol and its external pins; the implementation remains a native, editable KiCad schematic in the block library.

<p align="center">
  <img src="https://raw.githubusercontent.com/Uhrendoktor/kicad-blocks/refs/heads/docs/previews/previews/functional-block-facade.svg" alt="KiCad-native USB-C power functional-block facade" width="520">
</p>

<p align="center">
  <img src="https://raw.githubusercontent.com/Uhrendoktor/kicad-blocks/refs/heads/docs/previews/previews/functional-block-implementation.svg" alt="Editable STUSB4500 USB-PD sink reference implementation schematic" width="1000">
</p>

The reference implementation is based on the STUSB4500QTR application circuit in **ST DS12499 Rev. 8, Figure 10**. It includes the USB-C connector, protection devices, power-path MOSFETs, passives and MCU interface. The [example guide](examples/stusb4500/README.md) describes its contents and the 5 V, 9 V and 12 V PDO profiles.

## Features at a glance

| Capability | What it does |
| --- | --- |
| **Create from a sheet** | Extract a selected hierarchical sheet symbol or the currently open sheet. Facade pins and electrical types come from root-sheet hierarchical labels. |
| **Searchable block library** | Browse several registered libraries, search names/descriptions/pins/variants, preview the facade and place with KiCad's native symbol chooser. |
| **Editable implementations** | Keep the implementation in a separate .kicad_block folder. Edit it in Eeschema and publish changes back to the library. |
| **Variants and profiles** | Select implementation variants and named profiles that combine a variant, typed parameter overrides and an optional PCB layout. |
| **Per-instance configuration** | Configure one placed symbol without changing other instances or the shared library. |
| **Project-local copies** | Fork a library block for one project and reconcile changes in either direction, with divergent edits detected rather than silently overwritten. |
| **Nested composition** | Compose child blocks into a parent implementation with explicit pin-to-net mapping and rotation, while retaining reusable source assets. |
| **Recursive checks and BOM** | Validate libraries and dependencies, run recursive ERC, and export BOM data for a block and its nested dependencies. |
| **PCB layout reuse** | Merge one or several pre-routed block layouts into a separate board copy, then move each imported group with KiCad's native PCB placement tool. |

## A tour of the interface

The library browser is the main selection and placement workflow. It shows available blocks, their connection interface and implementation options.

<p align="center">
  <img src="https://raw.githubusercontent.com/Uhrendoktor/kicad-blocks/refs/heads/docs/previews/previews/library-browser-light.png" alt="Functional Block Library browser in the light theme" width="900">
</p>

Less-frequent actions are grouped in the task hub. Each tab is captured from the real application.

<p align="center">
  <img src="https://raw.githubusercontent.com/Uhrendoktor/kicad-blocks/refs/heads/docs/previews/previews/task-hub-light.png" alt="KiCad Blocks task hub: Use and build" width="760">
</p>

<p align="center">
  <img src="https://raw.githubusercontent.com/Uhrendoktor/kicad-blocks/refs/heads/docs/previews/previews/task-hub-edit-maintain-light.png" alt="KiCad Blocks task hub: Edit and maintain" width="760">
</p>

<p align="center">
  <img src="https://raw.githubusercontent.com/Uhrendoktor/kicad-blocks/refs/heads/docs/previews/previews/task-hub-checks-pcb-light.png" alt="KiCad Blocks task hub: Checks and PCB" width="760">
</p>

See the [visual evidence page](docs/visual-evidence.md) for additional light/dark captures, the instance-configuration form, folder manager, variant editor and screenshot-generation details.

## Get started

1. Check the [supported environment](docs/getting-started.md#supported-environment) before installing. The current validated target is KiCad **10.99.0 development nightly**, Linux **x86_64**, and CPython **3.12–3.14**.
2. Follow the [installation and first-run guide](docs/getting-started.md). Enable the KiCad IPC API and select the matching Python interpreter in KiCad's plugin preferences.
3. In Eeschema, use **Browse and Place Functional Blocks** to explore a library, or **Create Block from Selected Sheet** to turn an existing hierarchical sheet into a reusable block.

> **Release status (13 November 2026):** V1.1.0 is the current version recorded in the repository, but its installable release package has not yet been published. Check [GitHub Releases](https://github.com/Uhrendoktor/kicad-blocks/releases) for an available archive before following the ZIP installation commands.

## Documentation

- **[Getting started](docs/getting-started.md)** — prerequisites, installation, KiCad preferences, first launch and troubleshooting.
- **[Feature workflows](docs/workflows.md)** — step-by-step instructions for creating, placing, configuring, editing, nesting, validating and exporting blocks.
- **[Feature and data reference](docs/reference.md)** — action map, block storage, instance fields, parameters, layouts, compatibility and boundaries.
- **[Visual evidence](docs/visual-evidence.md)** — generated application screenshots, native KiCad renders, provenance and UI test coverage.
- **[STUSB4500 example](examples/stusb4500/README.md)** — a complete reference block and its editable source.
- **[Changelog](CHANGELOG.md)** — release history and known coverage limits.

## How it fits into a KiCad project

A top-level schematic contains the facade symbol and its external connections. The block library stores the internal schematic and optional implementation variants/layouts. A project-local sidecar maps each placed instance to its independent local copy when needed.

This is intentionally **not** a synthetic hierarchical sheet. Native project-level BOM, netlist and ERC operate on the facade and its pins. Use the plugin's recursive BOM/ERC actions when you need results from internal implementations and nested dependencies.

## Supported environment

| Component | Validated target |
| --- | --- |
| KiCad | **10.99.0 development nightly** (the pre-release development build for KiCad 11) |
| Operating system / CPU | Linux x86_64 |
| Python | CPython 3.12, 3.13 or 3.14, with Tkinter |
| KiCad API | IPC API enabled in Eeschema; enable it in PCB Editor for imported-layout placement |

Stable KiCad 10.x, KiCad 11 stable, Windows, macOS, ARM and other interpreter ABIs are not validated by this version. The installer may recognize newer KiCad version directories, but that does not make them supported targets. Read the [full compatibility notes](docs/getting-started.md#supported-environment) before installation.

## Contributing and reporting issues

Bug reports should include the KiCad build, operating system, CPU architecture, Python version selected in KiCad, the action being performed and the relevant KiCad Blocks log or validation report. For feature requests, describe the schematic workflow and expected KiCad-native behavior.

- [Open an issue](https://github.com/Uhrendoktor/kicad-blocks/issues)
- [Review the source](https://github.com/Uhrendoktor/kicad-blocks)
- [Read the release notes](CHANGELOG.md)
