# KiCad Blocks

**Build reusable schematic blocks around a normal KiCad library symbol.**

KiCad Blocks keeps a circuit's editable implementation in a shared library while a project schematic contains one ordinary facade symbol and its external pins. Each placed instance can select an implementation variant, configuration profile, typed parameter values, and an optional PCB layout.

[![CI](https://github.com/Uhrendoktor/kicad-blocks/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/Uhrendoktor/kicad-blocks/actions/workflows/ci.yml)
[![MIT License](https://img.shields.io/github/license/Uhrendoktor/kicad-blocks)](LICENSE)
![KiCad 10.99 nightly](https://img.shields.io/badge/KiCad-10.99%20nightly-blue?logo=kicad)
![CPython 3.12 full integration target](https://img.shields.io/badge/CPython-3.12%20full%20integration-blue?logo=python)

**[Documentation map](docs/README.md)** · **[First-block tutorial](docs/tutorials/first-block.md)** · **[How-to guides](docs/workflows.md)** · **[Technical reference](docs/reference.md)** · **[STUSB4500 example](examples/stusb4500/README.md)**

## Why use a block?

A hierarchical sheet is useful for organizing a design inside one project. KiCad Blocks addresses a different need: keep a complete, editable circuit as a reusable library asset, then expose it in each consuming project through a standard symbol.

- **Create** a block from a selected hierarchical sheet. Root-sheet hierarchical labels define the facade pins and their electrical types.
- **Browse and place** blocks from registered shared folders. The plugin uses KiCad's native symbol chooser and placement tool.
- **Configure per instance** with a variant, named profile, typed parameter overrides, and optional PCB layout.
- **Maintain** shared implementations, edit facade artwork, publish revisions, or create project-local copies and synchronize them.
- **Compose automatically** by placing child block facades inside an implementation and wiring their pins normally; KiCad Blocks derives dependencies from the saved schematic and netlist on publish.
- **Reuse PCB work** by storing named board layouts and merging them into a separate copy of a project board.

<p align="center">
  <a href="docs/visual-evidence.md">
    <picture>
      <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/Uhrendoktor/kicad-blocks/refs/heads/docs/previews/previews/library-browser-details-dark.png">
      <img src="https://raw.githubusercontent.com/Uhrendoktor/kicad-blocks/refs/heads/docs/previews/previews/library-browser-details-light.png" alt="KiCad Blocks library browser showing a searchable library, the STUSB4500 facade pins, and implementation choices" width="760">
    </picture>
  </a>
  <br>
  <sub>Browse a library entry, inspect its external interface, and configure the instance before placement.</sub>
</p>

## The important design boundary

The project schematic contains the facade symbol, not the hidden implementation circuit. Native project-level ERC, netlist, and BOM therefore see the facade and its external pins only. Use KiCad Blocks' recursive ERC and BOM actions when you need results for the stored implementation and its nested dependencies.

Applying a PCB layout creates a **separate board copy**. It does not modify a board that is already open in PCB Editor.

## Compatibility at a glance

The current full integration target is **KiCad 10.99.0 development nightly on Linux x86_64 with CPython 3.12**. CI also checks plugin discovery and managed-environment creation with CPython 3.13 and 3.14, but not the full KiCad integration matrix. Windows, macOS, ARM, and other combinations remain unvalidated.

Stable KiCad 10.x does not expose the Schematic Editor IPC plugin surface that this project requires. The current 10.99.0 nightly target is a development build; stable KiCad 11 is not yet validated by this repository's integration suite. See the [KiCad IPC API support matrix](https://dev-docs.kicad.org/en/apis-and-binding/ipc-api/for-addon-developers/).

On Linux, native symbol-placement handoff also uses `xdotool` and KiCad's default Add Symbol shortcut, `A`.

See the [compatibility and test-coverage guide](docs/explanation/compatibility.md) before installing.

## Install the current source version

The repository version is recorded in `VERSION`. A packaged KiCad Blocks 1.1.0 installer ZIP has not yet been published; the current installation path is from source:

~~~bash
git clone https://github.com/Uhrendoktor/kicad-blocks.git
cd kicad-blocks
python3.12 scripts/install_plugin.py --version 10.99
~~~

Then enable the KiCad API in Eeschema's plugin preferences, select the supported CPython interpreter, and restart KiCad. Follow the full [installation and first-run guide](docs/getting-started.md).

## First use

1. **Install and enable the plugin.** Register a shared library folder using the [installation guide](docs/getting-started.md).
2. **Create a reusable implementation.** In Eeschema, select a hierarchical sheet symbol
   in its parent schematic and run **Create Block from Sheet**. Root-sheet labels
   become the facade pins; the copied implementation stays in the shared library.
3. **Place the facade in another project.** Use **Browse and Place Functional Blocks**,
   choose a variant or profile, and confirm the symbol in KiCad's native chooser.
4. **Validate the stored circuit.** Run registered-library validation and use
   recursive ERC/BOM for implementation-level results; project-native
   ERC/netlist/BOM see the facade only.

The [first-block tutorial](docs/tutorials/first-block.md) walks through the
complete sequence, including the expected files and per-instance configuration.

## Documentation

The documentation is organized by reader intent:

| Start here | Use it when |
| --- | --- |
| [Tutorial](docs/tutorials/first-block.md) | You want to create and place your first block from a real hierarchical sheet. |
| [How-to index](docs/workflows.md) | You already know the task: create, configure, publish, synchronize, validate, or apply a PCB layout. |
| [Explanations](docs/explanation/architecture.md) | You want to understand the storage model, variants, automatic child-block dependencies, or product boundaries. |
| [Reference](docs/reference.md) | You need exact action names, instance fields, storage paths, or behavior rules. |
| [Visual evidence](docs/visual-evidence.md) | You want to inspect current production UI screenshots and KiCad-rendered design assets. |
| [STUSB4500 example](examples/stusb4500/README.md) | You want to study the editable datasheet-derived reference circuit. |

## Contributing and support

Start with an [issue](https://github.com/Uhrendoktor/kicad-blocks/issues) for bugs, feature requests, or larger changes. For code and documentation contributions, see the [contributor guide](docs/contributing.md).

When reporting a problem, include the KiCad build, operating system, CPU architecture, CPython version, the action being performed, and relevant KiCad Blocks or validation logs.

## License

KiCad Blocks is released under the [MIT License](LICENSE).
