# Documentation

Choose the page that matches what you are trying to do.

## Start here

- **[Getting started](getting-started.md)** — check the supported setup, install the current development version, register a library and place your first block.
- **[STUSB4500 example](../examples/stusb4500/README.md)** — see a complete reference circuit, its library symbol and the way it appears in KiCad Blocks.

## Do a task

Use the [workflow guides](workflows.md) when you know the result you want:

- Create a block from an existing hierarchical sheet.
- Find, configure and place a block.
- Edit a shared implementation or customize one project.
- Combine blocks, run checks or reuse a PCB layout.

## Understand what you are looking at

- **[Visual guide](visual-evidence.md)** — real KiCad renders and screenshots of the plugin.
- **[Feature reference](reference.md)** — exact action names, folder contents, instance fields, parameter rules and current limitations.

## Develop and verify

- **[Test inventory](testing-inventory.md)** — every pytest case, the purpose of each test, and the standalone KiCad GUI/IPC acceptance scripts.
- **[Quality gates](quality-gates.md)** — strict test/lint/type/dead-code checks and the reviewed static-analysis boundaries.
- **[Repository audit](repository-audit.md)** — findings on test overlap, duplicated entrypoint logic, compatibility wrappers, and cleanup decisions.

## A note about the examples

The STUSB4500 page documents the editable reference schematic. Some UI screenshots are generated from an automated library fixture built around that schematic; the page labels those captures so they are not mistaken for proof that the schematic changes electrically when a profile is selected.
