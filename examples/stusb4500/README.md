# STUSB4500 USB-C / USB Power Delivery sink

An editable KiCad reconstruction of STMicroelectronics' **STUSB4500QTR** USB Type-C / USB Power Delivery sink application circuit. The circuit is based on **DS12499 Rev. 8, Figure 10, “Typical schematic with MCU” (page 26/42)**.

This example shows both sides of a functional block: the complete implementation schematic and the compact symbol/interface presented to a project.

## See the circuit

The full A3 schematic is rendered by KiCad CLI from the editable `.kicad_sch` file. Open the image at full size to inspect component values, pin connections and the labelled circuit sections.

<p align="center">
  <a href="https://raw.githubusercontent.com/Uhrendoktor/kicad-blocks/refs/heads/docs/previews/previews/functional-block-implementation.svg">
    <img src="https://raw.githubusercontent.com/Uhrendoktor/kicad-blocks/refs/heads/docs/previews/previews/functional-block-implementation.svg" alt="A3 KiCad-rendered STUSB4500 schematic showing USB-C input, VBUS filtering, the controller, power switches and host interface" width="1100">
  </a>
</p>

[Open the editable KiCad schematic](https://raw.githubusercontent.com/Uhrendoktor/kicad-blocks/refs/heads/docs/previews/previews/functional-block-implementation.kicad_sch) · [Open the full-size SVG render](https://raw.githubusercontent.com/Uhrendoktor/kicad-blocks/refs/heads/docs/previews/previews/functional-block-implementation.svg)

## See how it appears as a block

When you place this block in a project, the schematic uses the facade symbol below. Its ten pins are the external interface; the full circuit remains in the separate implementation schematic.

<p align="center">
  <a href="https://raw.githubusercontent.com/Uhrendoktor/kicad-blocks/refs/heads/docs/previews/previews/functional-block-facade.svg">
    <img src="https://raw.githubusercontent.com/Uhrendoktor/kicad-blocks/refs/heads/docs/previews/previews/functional-block-facade.svg" alt="KiCad-rendered STUSB4500 facade symbol with VBUS, GND, CC1, CC2, VSNK and optional MCU interface pins" width="520">
  </a>
</p>

The block browser shows the same entry as a searchable library item, including the symbol preview, external pins, parameter and implementation choices.

<p align="center">
  <img src="https://raw.githubusercontent.com/Uhrendoktor/kicad-blocks/refs/heads/docs/previews/previews/library-browser-details-light.png" alt="KiCad Blocks browser showing the STUSB4500 USB-PD Sink entry, its ten-pin interface, PDO current parameter and implementation choices" width="1000">
</p>

The placement chooser can also present an implementation and a named configuration profile before the facade is placed:

<p align="center">
  <img src="https://raw.githubusercontent.com/Uhrendoktor/kicad-blocks/refs/heads/docs/previews/previews/block-picker-profile.png" alt="STUSB4500 block in the placement chooser with the 5 V implementation and 5 V / 2.4 A PDO1 profile selected" width="1000">
</p>

**About the chooser capture:** CI builds this UI example from the generated STUSB4500 reference schematic. It registers 5 V, 9 V and 12 V variant names and PDO profiles to test the selection controls. In this visual fixture, those variant entries all point to the same static schematic. The profile selection demonstrates the UI metadata; it does not dynamically alter the electrical circuit.

## External interface

The facade exposes these ten pins:

| Pin | Electrical type | Purpose |
| --- | --- | --- |
| VBUS | Input | USB-C input supply |
| GND | Passive | Ground return |
| CC1 | Bidirectional | USB Type-C configuration channel 1 |
| CC2 | Bidirectional | USB Type-C configuration channel 2 |
| VSNK | Output | Protected sink output |
| SCL | Input | Optional host I²C clock |
| SDA | Bidirectional | Optional host I²C data |
| ALERT# | Output | Optional host alert |
| RESET | Input | Optional host reset control |
| VMCU | Input | Optional host-interface supply |

## Circuit sections

The schematic follows the signal flow of ST's reference drawing:

| Section | What is included |
| --- | --- |
| USB-C input | Type-C receptacle and CC-line protection. |
| VBUS filter and monitor | Input capacitance, transient protection, VBUS filtering and the VBUS_VS_DISCH network. |
| PD controller | STUSB4500QTR, regulator decoupling and I²C address straps. |
| Power path | Back-to-back P-channel VBUS switches, discharge/control network and protected VSNK output. |
| Optional host | MCU I²C, ALERT# and RESET signals, plus VMCU and GND. |

The sheet uses A3 landscape and a 2.54 mm placement grid. Functional sections read left to right, from the receptacle and CC protection to VBUS sensing, the controller, the power switches and the protected output. The optional host interface and address/reset straps form a separate lower band.

Electrical relationships use orthogonal KiCad wires for local connections. Longer control and supply nets use matching labels or hierarchical ports rather than long wires across the sheet.

U1.22 (VSYS) remains a separate hierarchical input in the implementation;
it is not tied to GND. The regulator bypass network and the VBUS filter/sense
network use short, explicit pin-level wiring. See the
[STUSB4500 schematic style and electrical checklist](SCHEMATIC_STYLE_CHECKLIST.md)
for the layout, labeling, and electrical-routing rules applied to this example.

## Declarative groups and automatic layout

The generator declares functional groups by component membership rather than by manually positioned frame rectangles. It measures member pins from the loaded KiCad symbols, derives each group's box/title/description panel, and packs the primary stages left-to-right with a constant 2.54 mm gap. The address straps form a secondary row aligned to the controller. Group descriptions carry the datasheet source, page and figure metadata. A screenshot-asset field is reserved, but automatic datasheet image embedding is not implemented yet.

Component origins are now resolved from declarative per-group anchors and relationships: relative offsets, midpoint placement (R10 between T4/T1), and pin-relative placement (R55 from U1.6). The symbol specification no longer carries per-component sheet coordinates. The group packer translates the resolved component topology as a unit and derives the frame from member-pin extents. The constraints are explicit schematic-design intent rather than an automatic net-graph optimizer; a future placer can infer more relationships from connectivity and symbol roles. Each group retains one vertical anchor baseline as a layout tuning parameter; primary horizontal placement, component-relative positions, and frame dimensions are derived.

The schematic Clippy checker runs as part of KiCad CLI validation and exposes 18 stable IDs (SCH001–SCH018). Generic rules cover orthogonal wire geometry, label topology/collisions, symbol-field clearance, single-line block titles, bordered comment panels, wire-vs-symbol obstacles and annotation geometry. Profile rules add this design's physical-net contracts, shared C2/C3 GND, the required U1.3 no-connect marker, group padding, circuit documentation and a scored wire-length budget for the back-to-back FET stage. Run `python scripts/validate_schematic_style.py --list-rules` to list IDs and scope; run `python scripts/validate_schematic_style.py --generic-only PATH_TO_SCHEMATIC` to apply the reusable presentation/geometry subset without importing this example's generator or electrical contracts. See [SCHEMATIC_STYLE_CHECKLIST.md](SCHEMATIC_STYLE_CHECKLIST.md) for details.

## Component set

| Reference(s) | Value / part | Role |
|---|---|---|
| U1 | STUSB4500QTR | USB Power Delivery sink controller |
| J1 | USB Type-C receptacle | USB-C input |
| C1 | 1 µF | VREG_1V2 decoupling |
| C2, C3 | 1 µF | VREG_2V7 regulator/reference decoupling |
| C4 | 1 µF | VBUS_VS_DISCH filter capacitor |
| C10 | 100 nF | VSNK output bypass capacitor |
| C50 | 4.7 µF | VBUS input capacitance |
| D4 | Generic diode symbol; no part specified by ST | Fast-charge path for C4; select a suitably rated part |
| D1 | ESDA25W | Dual CC-line protection array |
| D50, D11 | ESDA25P35-1U1M | VBUS / VSNK transient protection |
| R1 | 470 Ω | VBUS_VS_DISCH filter path |
| R2 | 1 kΩ | DISCH-to-VSNK discharge path |
| R3, R5, R6 | 4.7 kΩ | MCU interface pull-ups |
| R4 | 1 kΩ | VBUS_VS_DISCH monitor network |
| R10 | 100 kΩ | Power-switch gate network |
| R11 | 22 kΩ | Power-switch gate network |
| R13 | 100 Ω | Power-switch control network |
| R51, R52 | NC | Optional address pull-up positions |
| R53, R54 | 100 kΩ | Address pull-downs |
| R55 | 10 kΩ | RESET pull-down |
| T1, T4 | STL6P3LLH6 | P-channel VBUS power-path switches |
| J10 | MPT_0.5_2-2.54 | VSNK output connector |
| J2 | 6-pin connector | Optional MCU SCL/SDA/ALERT#/RESET/VMCU/GND interface |

## Rebuild the schematic

With KiCad CLI and the standard symbol libraries installed, run:

```bash
python examples/stusb4500/generate_reference.py
```

The script writes `examples/stusb4500/implementation.kicad_sch` by default. Pass `--output PATH` to write it elsewhere.

CI exports the generated schematic to SVG and validates its netlist. The standard symbols come from the official [KiCad symbol library repository](https://gitlab.com/kicad/libraries/kicad-symbols), pinned to the revision recorded in `kicad-symbols.lock`. The three-pin ESDA25W symbol is a local functional representation based on ST's datasheet.

## Electrical scope and safety

This is a **datasheet-derived reference reconstruction**, not a production design or production compliance sign-off.

ST does not specify an order code for D4. Before using this circuit, verify the selected diode, protection ratings, component footprints, thermal/current capability, USB Type-C/PD compliance and configured NVM PDO profile for the target application. Confirm all power-path component ratings and the PCB layout for the expected operating conditions.

The CI child-block validation exports a KiCad netlist and checks the critical
pin-to-net assignments. Its standalone ERC profile suppresses only checks that
cannot be resolved for an isolated child sheet: root-sheet hierarchical ports,
externally driven input/power pins, isolated hierarchical-port labels, and
missing project library tables. Internal shorts, pin conflicts, dangling wires
and dangling local labels remain subject to ERC. This is a child-block
validation profile, not a substitute for ERC in the consuming parent project.

**Primary source:** [STUSB4500 datasheet DS12499, Rev. 8](https://www.st.com/resource/en/datasheet/stusb4500.pdf), Figure 10 and Table 1.

**Supporting source:** [ST's explanation of the D4/C4/R1/R4 discharge/filter network](https://community.st.com/interface-and-connectivity-ics-52/stusb4500-can-i-remove-the-typical-circuit-on-stusb4500-s-vbus-vs-disch-pin-38015).
