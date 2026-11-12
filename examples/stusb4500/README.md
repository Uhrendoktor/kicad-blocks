# STUSB4500 USB-PD sink reference block

This example is an editable KiCad reconstruction of STMicroelectronics'
**STUSB4500QTR** USB Type-C / USB Power Delivery sink controller application
from **DS12499 Rev. 8, Figure 10, “Typical schematic with MCU” (page 26/42)**.

## Layout and connectivity

The schematic follows the signal flow of ST's reference drawing:

- USB Type-C receptacle and CC-line protection at the left.
- VBUS input capacitance, transient protection and the VBUS_VS_DISCH
  filter/monitor network beside the receptacle.
- STUSB4500, regulator decoupling and I²C address straps in the center.
- Back-to-back P-channel VBUS switches, discharge/control network and
  protected VSNK output at the upper right.
- Optional MCU I²C, ALERT# and RESET interface at the lower right.

Remote connections use matching KiCad local labels instead of guessed
coordinate-sorted wire routes. The labels keep the sheet uncluttered while
preserving unambiguous electrical connectivity. CI exports a KiCad netlist
and checks critical pin-to-net assignments in addition to rendering the
schematic.

## Component set

| Reference(s) | Value / part | Datasheet role |
|---|---|---|
| U1 | STUSB4500QTR | Standalone USB-PD sink controller |
| J1 | USB Type-C receptacle | USB-C input |
| C1 | 1 µF | VREG_1V2 decoupling |
| C2, C3 | 1 µF | 2V7 regulator/reference decoupling |
| C4 | 1 µF | VBUS_VS_DISCH filter capacitor |
| C10 | 100 nF | VSNK output bypass capacitor |
| C50 | 4.7 µF | VBUS input capacitance |
| D4 | Generic diode symbol; ST does not specify a part number | Fast-charge path for C4; select a suitably rated part |
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

## Reproduce

With KiCad CLI and its standard symbol libraries installed:

```bash
python examples/stusb4500/generate_reference.py
```

The CI pipeline generates the editable `.kicad_sch`, exports the native KiCad
SVG preview, and checks the exported electrical netlist. Standard symbols
come from the official [KiCad symbol library repository](https://gitlab.com/kicad/libraries/kicad-symbols),
pinned to the 10.0.5 release in `kicad-symbols.lock`. The three-pin ESDA25W
symbol is a local functional representation based on ST's device datasheet.

## Design limitations

This is a **datasheet-derived reference reconstruction**, not a production
design or a claim of electrical-rule-check sign-off. The datasheet does not
specify a D4 order code. Verify the selected diode, protection ratings,
component footprints, thermal/current capability, USB Type-C/PD compliance,
and configured NVM PDO profile for the target application before
manufacturing. Confirm all power-path component ratings and PCB layout for
the expected operating conditions.

**Primary source:** [STUSB4500 datasheet DS12499, Rev. 8](https://www.st.com/resource/en/datasheet/stusb4500.pdf), Figure 10 and Table 1.

**Supporting source:** [ST's explanation of the D4/C4/R1/R4 discharge/filter network](https://community.st.com/interface-and-connectivity-ics-52/stusb4500-can-i-remove-the-typical-circuit-on-stusb4500-s-vbus-vs-disch-pin-38015).
