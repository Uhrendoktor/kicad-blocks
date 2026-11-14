# Documentation

Choose a guide by the kind of help you need. This documentation uses four complementary forms: a tutorial teaches one complete first-use path, how-to guides solve specific tasks, explanations describe the design and trade-offs, and reference pages state exact behavior.

## Tutorial

Start with the [first-block tutorial](tutorials/first-block.md) to create a block from a hierarchical sheet, inspect its interface, and place it in a project.

## How-to guides

Use a task guide when you already know the outcome you want.

- [Install and enable the plugin](getting-started.md)
- [Create, find, and place blocks](how-to/create-and-place.md)
- [Configure an instance](how-to/configure.md)
- [Edit, publish, and synchronize implementations](how-to/publish-and-sync.md)
- [Compose block dependencies and validate libraries](how-to/nesting-and-validation.md)
- [Manage and apply PCB layouts](how-to/pcb-layouts.md)

The [how-to index](workflows.md) groups these tasks by workflow.

## Explanations

Read these pages to understand why KiCad Blocks behaves as it does.

- [Architecture and design model](explanation/architecture.md) — facade symbols, stored implementations, variants, parameters, local copies, automatic child-block dependencies and the native KiCad boundary.
- [Compatibility and test coverage](explanation/compatibility.md) — tested environment, interpreter coverage, unvalidated targets, and known gaps.

## Testing and contribution

- [Test inventory](testing/TEST_INVENTORY.md) — every unit test, its purpose, and each native KiCad/GUI acceptance runner.

## Reference

- [Feature reference](reference.md) — action names, block and project files, instance fields, parameter syntax, PCB merge behavior, report files, and settings.
- [Visual evidence](visual-evidence.md) — current plugin screenshots and native KiCad renders.
- [STUSB4500 example](../examples/stusb4500/README.md) — a datasheet-derived editable circuit and its generated evidence.
- [Changelog](../CHANGELOG.md) — version history and changes not yet assigned to a release.

## Contribute

The [contributor guide](contributing.md) describes the documentation structure, editorial conventions, and validation checks. When product behavior changes, update the relevant guide in the same pull request as the code.
