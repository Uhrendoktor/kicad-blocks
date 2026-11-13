# Visual documentation and generated previews

KiCad Blocks treats screenshots and animations as generated evidence. UI screenshots are captured from production Tk dialogs; facade and schematic drawings are exported by KiCad CLI. They are not hand-drawn mock-ups.

## Placement workflow

The animation shows the searchable block library, a filtered result and a profile-driven selection in the production placement chooser.

<p align="center">
  <img src="https://raw.githubusercontent.com/Uhrendoktor/kicad-blocks/refs/heads/docs/previews/previews/block-picker-workflow.gif" alt="Search the library, filter to a block and select a configuration profile" width="900">
</p>

## Library browser and folder manager

<p align="center">
  <img src="https://raw.githubusercontent.com/Uhrendoktor/kicad-blocks/refs/heads/docs/previews/previews/library-browser-light.png" alt="Functional Block Library browser in light theme" width="900">
</p>

<p align="center">
  <img src="https://raw.githubusercontent.com/Uhrendoktor/kicad-blocks/refs/heads/docs/previews/previews/library-browser-dark.png" alt="Functional Block Library browser in dark theme" width="900">
</p>

<p align="center">
  <img src="https://raw.githubusercontent.com/Uhrendoktor/kicad-blocks/refs/heads/docs/previews/previews/library-folders-light.png" alt="Registered shared library folders and management controls" width="900">
</p>

The browser capture includes the searchable result list, facade preview and designer-oriented block overview. The Manage Libraries window capture shows registered libraries and the add/create/change/unregister/default-folder actions.

## Task hub: every tab

The task hub is the entry point for less-frequent actions. CI captures all three tabs separately in both light and dark themes.

### Use & build

<p align="center">
  <img src="https://raw.githubusercontent.com/Uhrendoktor/kicad-blocks/refs/heads/docs/previews/previews/task-hub-light.png" alt="Task hub Use and build tab" width="760">
</p>

### Edit & maintain

<p align="center">
  <img src="https://raw.githubusercontent.com/Uhrendoktor/kicad-blocks/refs/heads/docs/previews/previews/task-hub-edit-maintain-light.png" alt="Task hub Edit and maintain tab" width="760">
</p>

### Checks & PCB

<p align="center">
  <img src="https://raw.githubusercontent.com/Uhrendoktor/kicad-blocks/refs/heads/docs/previews/previews/task-hub-checks-pcb-light.png" alt="Task hub Checks and PCB tab" width="760">
</p>

Dark-theme captures are also published as task-hub-dark.png, task-hub-edit-maintain-dark.png and task-hub-checks-pcb-dark.png.

## Create, configure and maintain

<p align="center">
  <img src="https://raw.githubusercontent.com/Uhrendoktor/kicad-blocks/refs/heads/docs/previews/previews/create-block-form-light.png" alt="Create Functional Block form" width="660">
</p>

<p align="center">
  <img src="https://raw.githubusercontent.com/Uhrendoktor/kicad-blocks/refs/heads/docs/previews/previews/instance-configuration-light.png" alt="Configure a block instance with a named profile, variant, parameter and PCB layout" width="850">
</p>

<p align="center">
  <img src="https://raw.githubusercontent.com/Uhrendoktor/kicad-blocks/refs/heads/docs/previews/previews/implementation-variants-light.png" alt="Implementation variant editor" width="900">
</p>

The capture suite also produces screenshots of the implementation-details dialog, variant editor and instance-configuration form in dark theme. The configuration capture changes to the low-voltage profile before saving, so the visible selected profile is checked against its typed parameter value, implementation variant and PCB layout.

## KiCad-native output

<p align="center">
  <img src="https://raw.githubusercontent.com/Uhrendoktor/kicad-blocks/refs/heads/docs/previews/previews/functional-block-facade.svg" alt="Native KiCad-rendered functional block facade" width="520">
</p>

<p align="center">
  <img src="https://raw.githubusercontent.com/Uhrendoktor/kicad-blocks/refs/heads/docs/previews/previews/functional-block-implementation.svg" alt="Native KiCad-rendered STUSB4500 implementation schematic" width="1000">
</p>

[Open the editable KiCad implementation schematic](https://raw.githubusercontent.com/Uhrendoktor/kicad-blocks/refs/heads/docs/previews/previews/functional-block-implementation.kicad_sch).

The reference circuit uses the STUSB4500QTR, a USB Type-C receptacle, protection devices, power-path MOSFETs, passives and the optional MCU interface. Its layout follows **ST DS12499 Rev. 8, Figure 10**; the chooser profiles reflect the example PDO values from Table 19. See the [STUSB4500 example guide](../examples/stusb4500/README.md).

## What is actually verified

The visual acceptance fixture runs on the self-hosted Linux x86_64 runner under Xvfb. It creates a library with multiple blocks, implementation variants, profiles and a parameter; opens the production browser, folder manager, task hub, variant editor, creation form and configuration form; captures each rendered window; and validates that the placement profile selects the intended implementation and parameter.

KiCad CLI exports the facade symbol and implementation schematic to SVG. The CI manifest records the source commit and SHA-256 hashes for every generated asset. The publisher verifies the manifest and file hashes before pushing the generated bundle to the separate docs/previews branch. Pull-request runs upload the same bundle as a workflow artifact for reviewer inspection.

The capture job verifies visible window dimensions and that images are non-blank; the placement GIF frames must be distinct. The task hub smoke test also checks that all three tabs and their expected action buttons exist. GUI and IPC tests separately exercise editor launch/save, native symbol-chooser handoff, instance configuration, project-library registration and schematic/PCB operations.

### Known end-to-end coverage limits

Visual captures establish that the production UI renders at the tested viewport and that the tested form interactions can be completed; they do not mean every KiCad editor operation is driven by mouse automation.

- The Symbol Editor shortcut handoff is unit-tested, but a complete live GUI save/edit/publish sequence is not yet part of CI.
- PCB imported-group ownership and native move-tool dispatch are tested, but PCB Editor's mouse-driven commit/cancel sequence is not automated end-to-end.
- Recursive hierarchical extraction is tested with a two-level fixture and KiCad CLI validation. A manual GUI exercise on a complex multi-sheet project remains recommended.

## Reproduce the visual bundle locally

The environment must provide the supported KiCad CLI, Tkinter, Xvfb, xdotool, Pillow and CairoSVG.

~~~bash
python -m pip install cairosvg pillow
xvfb-run -a -s "-screen 0 1600x1000x24" \
  python tests/capture_visual_docs.py --output-dir artifacts/visual-docs
~~~

The generated files are disposable. Change the UI, capture fixture or test—not the published screenshots by hand.
