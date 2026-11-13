# Visual evidence

This page presents screenshots captured from the production Tk UI and schematic/PCB assets rendered by KiCad. CI generates the screenshots under a virtual display and exports the native design assets with KiCad CLI. These are real application captures, not hand-drawn interface mockups.

For the complete behavior walkthrough, see the [STUSB4500 example](../examples/stusb4500/README.md). For how-to instructions, see the [workflow index](workflows.md).

## Browse the STUSB4500 example

The block chooser fixture uses the STUSB4500 reference schematic. It demonstrates search, selection, facade preview, and profile controls.

<p align="center">
  <img src="https://raw.githubusercontent.com/Uhrendoktor/kicad-blocks/refs/heads/docs/previews/previews/block-picker-initial.png" alt="Block chooser with the STUSB4500 USB-PD sink entry selected and its facade preview visible" width="900">
</p>

<p align="center">
  <img src="https://raw.githubusercontent.com/Uhrendoktor/kicad-blocks/refs/heads/docs/previews/previews/block-picker-filtered.png" alt="Block chooser filtered to USB-related blocks with the STUSB4500 interface preview" width="900">
</p>

<p align="center">
  <img src="https://raw.githubusercontent.com/Uhrendoktor/kicad-blocks/refs/heads/docs/previews/previews/block-picker-profile.png" alt="STUSB4500 chooser with the 5 V implementation and 5 V / 2.4 A PDO1 profile selected" width="900">
</p>

**Fixture limitation:** the chooser's 5 V, 9 V, and 12 V variant entries point to the same static reference schematic. The profile selections demonstrate the configuration UI; they do not electrically rewrite the reference circuit.

## Inspect a library entry

The browser shows the selected block's description, external pins, parameters, implementation choices, profiles, and PCB layout options.

<p align="center">
  <img src="https://raw.githubusercontent.com/Uhrendoktor/kicad-blocks/refs/heads/docs/previews/previews/library-browser-details-light.png" alt="Functional Block Library showing the STUSB4500 block, its ten external pins, parameter and implementation choices" width="1000">
</p>

Manage the shared library roots with the **Manage Libraries** dialog:

<p align="center">
  <img src="https://raw.githubusercontent.com/Uhrendoktor/kicad-blocks/refs/heads/docs/previews/previews/library-folders-light.png" alt="Manage Libraries window with registered folders and Add library, Create library, Change folder, Unregister, and Set default actions" width="900">
</p>

## Manage implementations

The implementation editor lists the editable **Default** schematic and named variants. Selecting the default row enables **Edit default**.

<p align="center">
  <img src="https://raw.githubusercontent.com/Uhrendoktor/kicad-blocks/refs/heads/docs/previews/previews/implementation-variants-light.png" alt="Implementations dialog with the Default row selected and the Edit default action enabled" width="900">
</p>

The implementation details dialog uses a short **Variant value** label and explains the `KB_VARIANT` field:

<p align="center">
  <img src="https://raw.githubusercontent.com/Uhrendoktor/kicad-blocks/refs/heads/docs/previews/previews/implementation-details-light.png" alt="Implementation details dialog with the Variant value field and explanatory KB_VARIANT hint" width="760">
</p>

## Manage PCB layouts

The PCB layout manager separates the stored layout table, the assignment selector, and its Save/Cancel footer.

<p align="center">
  <img src="https://raw.githubusercontent.com/Uhrendoktor/kicad-blocks/refs/heads/docs/previews/previews/pcb-layout-manager-light.png" alt="PCB layouts manager with layout rows, implementation assignment controls, and Save layouts and Cancel footer buttons" width="1000">
</p>

When creating a named layout from a blank board, the source file controls are hidden:

<p align="center">
  <img src="https://raw.githubusercontent.com/Uhrendoktor/kicad-blocks/refs/heads/docs/previews/previews/add-pcb-layout-blank-light.png" alt="Add PCB layout dialog in create-empty-board mode with no PCB file path field or Choose file button" width="760">
</p>

Choosing an existing PCB file reveals the path entry and file chooser:

<p align="center">
  <img src="https://raw.githubusercontent.com/Uhrendoktor/kicad-blocks/refs/heads/docs/previews/previews/add-pcb-layout-file-light.png" alt="Add PCB layout dialog in existing-file mode with the PCB file path entry and Choose file button visible" width="760">
</p>

The same production dialogs are captured in dark theme. Use the corresponding `-dark` image filenames.

## Configure an instance and use the task hub

The configuration dialog selects a profile, implementation variant, typed parameter values, and optional PCB layout for one placed symbol instance.

<p align="center">
  <img src="https://raw.githubusercontent.com/Uhrendoktor/kicad-blocks/refs/heads/docs/previews/previews/instance-configuration-light.png" alt="Instance configuration dialog showing profile, implementation variant, typed parameter, and PCB layout selectors" width="820">
</p>

The task hub groups less frequent commands by workflow.

### Use & build

<p align="center">
  <img src="https://raw.githubusercontent.com/Uhrendoktor/kicad-blocks/refs/heads/docs/previews/previews/task-hub-light.png" alt="KiCad Blocks task hub Use and build tab with browsing, block creation, and implementation actions" width="760">
</p>

### Edit & maintain

<p align="center">
  <img src="https://raw.githubusercontent.com/Uhrendoktor/kicad-blocks/refs/heads/docs/previews/previews/task-hub-edit-maintain-light.png" alt="KiCad Blocks task hub Edit and maintain tab with configuration, publishing, local-copy, synchronization, and nesting actions" width="760">
</p>

### Checks & PCB

<p align="center">
  <img src="https://raw.githubusercontent.com/Uhrendoktor/kicad-blocks/refs/heads/docs/previews/previews/task-hub-checks-pcb-light.png" alt="KiCad Blocks task hub Checks and PCB tab with library validation, recursive BOM and ERC, and PCB layout actions" width="760">
</p>

## Native KiCad output

The facade is a normal KiCad library symbol:

<p align="center">
  <a href="https://raw.githubusercontent.com/Uhrendoktor/kicad-blocks/refs/heads/docs/previews/previews/functional-block-facade.svg">
    <img src="https://raw.githubusercontent.com/Uhrendoktor/kicad-blocks/refs/heads/docs/previews/previews/functional-block-facade.svg" alt="KiCad-rendered STUSB4500 facade with ten external pins for power, USB-C CC and optional host signals" width="520">
  </a>
</p>

The full implementation is an editable hierarchical schematic:

<p align="center">
  <a href="https://raw.githubusercontent.com/Uhrendoktor/kicad-blocks/refs/heads/docs/previews/previews/functional-block-implementation.svg">
    <img src="https://raw.githubusercontent.com/Uhrendoktor/kicad-blocks/refs/heads/docs/previews/previews/functional-block-implementation.svg" alt="A3 KiCad-rendered STUSB4500 reference schematic arranged from USB-C input through controller and power switches to protected VSNK output" width="1000">
  </a>
</p>

- [Open the editable KiCad schematic](https://raw.githubusercontent.com/Uhrendoktor/kicad-blocks/refs/heads/docs/previews/previews/functional-block-implementation.kicad_sch).
- [Open the generated facade SVG](https://raw.githubusercontent.com/Uhrendoktor/kicad-blocks/refs/heads/docs/previews/previews/functional-block-facade.svg).

## Evidence provenance and coverage

The main-branch CI job captures the production Tk dialogs and KiCad CLI design renders. It records the source commit and SHA-256 hash of each published asset. The publisher verifies the manifest and hashes before updating the separate `docs/previews` branch. Pull-request runs upload the same rendered files as workflow artifacts.

Current screenshots demonstrate the UI state and layout. They do not imply that every mouse-driven editor workflow is automated. The known coverage gaps are listed in [compatibility and test coverage](explanation/compatibility.md).

## Regenerate the visual evidence locally

With the supported KiCad CLI, Tkinter, Xvfb, `xdotool`, Pillow, and CairoSVG installed, run:

~~~bash
python -m pip install cairosvg pillow
xvfb-run -a -s "-screen 0 1600x1000x24" \
  python tests/capture_visual_docs.py --output-dir artifacts/visual-docs
~~~

Treat the generated images as build outputs. Update the production UI or capture fixture, then let CI regenerate and publish the previews. Do not edit published screenshots by hand.
