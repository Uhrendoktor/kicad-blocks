# Visual evidence

This page presents production Tk UI captures, an animated placement-chooser walkthrough, and schematic assets rendered by KiCad. CI captures the application under a virtual display and exports native design assets with KiCad CLI. These are real application captures, not hand-drawn interface mockups.

For the complete behavior walkthrough, see the [STUSB4500 example](../examples/stusb4500/README.md). For task procedures, see the [workflow index](workflows.md).

## Browse and place the STUSB4500 example

The chooser fixture uses the STUSB4500 reference schematic. The animation shows the current placement flow: browse the registered library, filter for USB-related blocks, inspect the facade, and select an implementation and configuration profile before placement.

<p align="center">
  <img src="https://raw.githubusercontent.com/Uhrendoktor/kicad-blocks/refs/heads/docs/previews/previews/block-picker-workflow.gif" alt="Animated KiCad Blocks placement chooser: browse the STUSB4500 block, filter the library for USB blocks, and select the 5 V implementation with the 5 V / 2.4 A PDO1 profile" width="900">
</p>

Static captures of the same chooser states are available: [initial library view](https://raw.githubusercontent.com/Uhrendoktor/kicad-blocks/refs/heads/docs/previews/previews/block-picker-initial.png) · [USB filter applied](https://raw.githubusercontent.com/Uhrendoktor/kicad-blocks/refs/heads/docs/previews/previews/block-picker-filtered.png) · [profile selected](https://raw.githubusercontent.com/Uhrendoktor/kicad-blocks/refs/heads/docs/previews/previews/block-picker-profile.png).

**Fixture limitation:** the chooser's 5 V, 9 V, and 12 V variant entries point to the same static reference schematic. The profile selections demonstrate the configuration UI; they do not electrically rewrite the reference circuit.

## Create a block from a sheet

After selecting a hierarchical sheet in Eeschema, **Create Block from Sheet** opens the production form shown below. It identifies the source sheet and lets the designer enter a reusable block name and optional description.

<p align="center">
  <img src="https://raw.githubusercontent.com/Uhrendoktor/kicad-blocks/refs/heads/docs/previews/previews/create-block-form-light.png" alt="Create a reusable block from this sheet dialog showing the selected STUSB4500 source sheet, block-name field, optional description, and Create Block action" width="760">
</p>

The selected root sheet's hierarchical labels define the new facade interface. Its child sheets remain inside the stored implementation, and the source project is left unchanged. The [dark-theme capture](https://raw.githubusercontent.com/Uhrendoktor/kicad-blocks/refs/heads/docs/previews/previews/create-block-form-dark.png) shows the same form.

## Inspect a library entry

The Functional Block Library presents registered blocks in a searchable list with two preview modes. The **Facade** tab shows the generated library symbol; the **Schematic** tab renders the actual selected implementation with KiCad CLI. When a block has named implementations, the circuit selector switches between them; hierarchical implementations also expose a sheet selector.

<p align="center">
  <img src="https://raw.githubusercontent.com/Uhrendoktor/kicad-blocks/refs/heads/docs/previews/previews/library-browser-light.png" alt="Functional Block Library with searchable block rows, facade preview, and Place block, Implementation, Edit facade, Variants, Open PCB, PCB layouts, Refresh, and Libraries actions" width="1000">
</p>

The selected STUSB4500 entry exposes its description, external pins, parameters, implementation choices, profiles, and PCB layout options:

<p align="center">
  <img src="https://raw.githubusercontent.com/Uhrendoktor/kicad-blocks/refs/heads/docs/previews/previews/library-browser-details-light.png" alt="Functional Block Library showing the STUSB4500 block, its ten external pins, parameter and implementation choices" width="1000">
</p>

The schematic view previews the editable implementation itself, independently of the facade. The selector starts at **Default** and can switch to a named variant; hierarchical designs expose their child sheets in the **Sheet** selector.

<p align="center">
  <img src="https://raw.githubusercontent.com/Uhrendoktor/kicad-blocks/refs/heads/docs/previews/previews/library-browser-schematic-light.png" alt="Functional Block Library with the Schematic tab selected, showing the STUSB4500 implementation rendered by KiCad" width="1000">
</p>

The same schematic preview is captured in the [dark theme](https://raw.githubusercontent.com/Uhrendoktor/kicad-blocks/refs/heads/docs/previews/previews/library-browser-schematic-dark.png).

Manage shared library roots with the **Manage Libraries** dialog:

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

When creating a named layout from a blank board, the source-file controls are hidden:

<p align="center">
  <img src="https://raw.githubusercontent.com/Uhrendoktor/kicad-blocks/refs/heads/docs/previews/previews/add-pcb-layout-blank-light.png" alt="Add PCB layout dialog in create-empty-board mode with no PCB file path field or Choose file button" width="760">
</p>

Choosing an existing PCB file reveals the path entry and file chooser:

<p align="center">
  <img src="https://raw.githubusercontent.com/Uhrendoktor/kicad-blocks/refs/heads/docs/previews/previews/add-pcb-layout-file-light.png" alt="Add PCB layout dialog in existing-file mode with the PCB file path entry and Choose file button visible" width="760">
</p>

The production UI captures are also available in dark theme; use the corresponding `-dark` filenames on the [previews branch](https://github.com/Uhrendoktor/kicad-blocks/tree/docs/previews/previews).

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
  <img src="https://raw.githubusercontent.com/Uhrendoktor/kicad-blocks/refs/heads/docs/previews/previews/task-hub-edit-maintain-light.png" alt="KiCad Blocks task hub Edit and maintain tab with configure selected instance, edit facade symbol, publish implementation, make selected instance local, and synchronize local copy actions" width="760">
</p>

Child-block composition is no longer a separate task-hub command. Open a parent implementation, place child facades through Eeschema's normal **Add Symbol** action, and wire them as usual. KiCad Blocks derives the child dependency graph from the saved schematic and KiCad's exported netlist when the implementation is published and before recursive checks or exports. There is no manual parent/child selection or pin-map form. See [compose block dependencies](how-to/nesting-and-validation.md).

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

The main-branch CI job captures the production UI and KiCad CLI design renders. It records the exact source commit and SHA-256 hash of each asset. The publisher verifies the manifest and hashes before updating the separate `docs/previews` branch. The [preview provenance record](https://raw.githubusercontent.com/Uhrendoktor/kicad-blocks/refs/heads/docs/previews/previews/provenance.json) identifies the source commit and workflow run for the currently published screenshots, GIF, and KiCad renders. Pull-request runs upload the generated files as workflow artifacts.

These visuals demonstrate UI states and layout; they do not imply that every mouse-driven editor workflow is automated. The known coverage gaps are listed in [compatibility and test coverage](explanation/compatibility.md).

## Regenerate the visual evidence locally

With the supported KiCad CLI, Tkinter, Xvfb, `xdotool`, Pillow, and CairoSVG installed, run:

~~~
python -m pip install cairosvg pillow
xvfb-run -a -s "-screen 0 1600x1000x24" \
  python tests/capture_visual_docs.py --output-dir artifacts/visual-docs
~~~

Treat generated images as build outputs. Update the production UI or capture fixture, then let CI regenerate and publish the previews. Do not edit published screenshots or GIFs by hand.
