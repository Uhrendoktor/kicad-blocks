"""Tests for the STUSB4500 functional-block schematic and its parent interfaces."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugin" / "kicad-blocks"))
sys.path.insert(0, str(ROOT / "examples" / "stusb4500"))

from generate_reference import _uid, generate_reference_schematic
from kicad_blocks.pcb_layout import _all, _direct, parse_sexpr


def _instance_map(tree: list) -> dict[str, list]:
    result = {}
    for instance in _all(tree, "symbol"):
        if _direct(instance, "lib_id") is None:
            continue
        reference = next(
            (
                str(prop[2])
                for prop in _all(instance, "property")
                if len(prop) >= 3 and str(prop[1]) == "Reference"
            ),
            None,
        )
        if reference:
            result[reference] = instance
    return result


def _property(instance: list, name: str) -> list:
    return next(
        prop for prop in _all(instance, "property")
        if len(prop) >= 3 and str(prop[1]) == name
    )


class STUSB4500ReferenceTests(unittest.TestCase):
    def _generate(self, directory: str) -> tuple[str, list, dict[str, list]]:
        path = generate_reference_schematic(Path(directory) / "reference.kicad_sch")
        content = path.read_text(encoding="utf-8")
        tree = parse_sexpr(content)
        return content, tree, _instance_map(tree)

    def test_block_has_only_explicit_hierarchical_interfaces_and_no_globals(self) -> None:
        with tempfile.TemporaryDirectory(prefix="stusb4500-interfaces-") as temporary:
            content, tree, _ = self._generate(temporary)

        self.assertNotIn("(global_label", content)
        hierarchical = {
            str(label[1]): str(_direct(label, "shape")[1])
            for label in _all(tree, "hierarchical_label")
        }
        self.assertEqual(
            hierarchical,
            {
                "GND": "passive",
                "VSYS": "input",
                "RESET": "input",
                "SCL": "input",
                "SDA": "bidirectional",
                "ALERT#": "output",
                "VSNK": "output",
            },
        )
        self.assertNotIn('(label "GND"', content)
        self.assertGreaterEqual(
            sum(1 for label in _all(tree, "hierarchical_label") if str(label[1]) == "GND"),
            15,
        )
        # CC1/CC2 are inside this block: connector -> ESD -> controller.
        self.assertNotIn('(hierarchical_label "CC1"', content)
        self.assertNotIn('(hierarchical_label "CC2"', content)
        self.assertNotIn('(global_label "CC1"', content)
        self.assertNotIn('(global_label "CC2"', content)

    def test_vsys_is_a_separate_system_input_not_ground(self) -> None:
        with tempfile.TemporaryDirectory(prefix="stusb4500-vsys-") as temporary:
            content, _, instances = self._generate(temporary)

        self.assertIn('(hierarchical_label "VSYS" (shape input)', content)
        self.assertNotIn('(global_label "VSYS"', content)
        # U1.22 is described by the pin map and checked again in KiCad's
        # exported netlist by validate_kicad_cli.py.
        source = (ROOT / "examples" / "stusb4500" / "generate_reference.py").read_text(
            encoding="utf-8"
        )
        self.assertIn('"22": ("VSYS", "hier", "input")', source)
        self.assertNotIn('"22": ("GND"', source)
        self.assertIn("U1", instances)

    def test_parent_level_mcu_and_output_connectors_are_not_inside_the_block(self) -> None:
        with tempfile.TemporaryDirectory(prefix="stusb4500-parent-io-") as temporary:
            content, _, instances = self._generate(temporary)

        for reference in ("J2", "J10", "R3", "R5", "R6"):
            self.assertNotIn(reference, instances)
            self.assertNotIn(f'"{reference}"', content)
        self.assertNotIn("OPTIONAL MCU", content)
        self.assertNotIn("MCU interface", content)
        self.assertNotIn("VMCU", content)
        # The power output is a parent-level hierarchical port on T1.3.
        self.assertIn('(hierarchical_label "VSNK" (shape output)', content)

    def test_left_to_right_functional_modules_are_explicit(self) -> None:
        with tempfile.TemporaryDirectory(prefix="stusb4500-zones-") as temporary:
            content, _tree, instances = self._generate(temporary)

        titles = (
            "USB-C PORT / CC PROTECTION",
            "VBUS BULK PROTECTION",
            "VBUS MONITOR / FILTER",
            "STUSB4500 CONTROLLER",
            "I2C ADDRESS STRAPS",
            "BACK-TO-BACK PMOS SWITCH",
            "VSNK OUTPUT / DISCHARGE",
        )
        for title in titles:
            self.assertIn(f'(text "{title}"', content)
        self.assertNotIn("OPTIONAL MCU / I2C INTERFACE", content)
        self.assertNotIn("ADDRESS / RESET STRAPS", content)

        positions = {}
        for reference in ("J1", "C50", "D4", "U1", "T4", "T1"):
            at = _direct(instances[reference], "at")
            positions[reference] = (float(at[1]), float(at[2]))
        self.assertLess(positions["J1"][0], positions["C50"][0])
        self.assertLess(positions["C50"][0], positions["D4"][0])
        self.assertLess(positions["D4"][0], positions["U1"][0])
        self.assertLess(positions["U1"][0], positions["T4"][0])
        self.assertLess(positions["T4"][0], positions["T1"][0])

    def test_address_straps_are_separate_and_reset_stays_with_controller(self) -> None:
        with tempfile.TemporaryDirectory(prefix="stusb4500-address-") as temporary:
            content, _, instances = self._generate(temporary)

        for reference in ("R51", "R52", "R53", "R54"):
            self.assertIn(reference, instances)
        self.assertIn("R55", instances)
        self.assertLess(float(_direct(instances["R55"], "at")[2]), 180.34)

        # Each address strap is a horizontal, left-to-right resistor chain.
        r51 = _direct(instances["R51"], "at")
        r52 = _direct(instances["R52"], "at")
        r53 = _direct(instances["R53"], "at")
        r54 = _direct(instances["R54"], "at")
        self.assertAlmostEqual(float(r51[2]), float(r53[2]), places=3)
        self.assertAlmostEqual(float(r52[2]), float(r54[2]), places=3)
        self.assertLess(float(r51[1]), float(r53[1]))
        self.assertLess(float(r52[1]), float(r54[1]))
        self.assertLess(abs(float(r51[2]) - float(r52[2])), 15.0)
        self.assertLess(abs(float(r51[1]) - float(r53[1])), 25.0)
        for reference in ("R51", "R52", "R53", "R54"):
            self.assertEqual(float(_direct(instances[reference], "at")[3]), 90.0)
            for field_name in ("Reference", "Value"):
                field_at = _direct(_property(instances[reference], field_name), "at")
                self.assertEqual(float(field_at[3]), 90.0, f"{reference} {field_name} stays horizontal")
        strap_label_positions = {
            (str(node[1]), round(float(_direct(node, "at")[1]), 3),
             round(float(_direct(node, "at")[2]), 3))
            for node in _all(parse_sexpr(content), "label")
            if len(node) > 1 and str(node[1]) in {"ADDR0", "ADDR1"}
        }
        self.assertIn(("ADDR1", 246.380, 172.720), strap_label_positions)
        self.assertIn(("ADDR0", 246.380, 185.420), strap_label_positions)

    def test_back_to_back_pmos_midpoint_and_gate_are_physically_routed(self) -> None:
        with tempfile.TemporaryDirectory(prefix="stusb4500-pmos-link-") as temporary:
            content, _, instances = self._generate(temporary)

        t4 = _direct(instances["T4"], "at")
        t1 = _direct(instances["T1"], "at")
        self.assertEqual(float(t4[3]), 0.0)
        self.assertEqual(float(t1[3]), 180.0)
        self.assertLess(float(t4[1]), float(t1[1]))
        self.assertIn(
            '(wire (pts (xy 320.040 99.060) (xy 355.600 99.060))',
            content,
        )
        self.assertIn(
            '(wire (pts (xy 337.820 99.060) (xy 337.820 128.270))',
            content,
        )
        self.assertIn(
            '(wire (pts (xy 363.220 104.140) (xy 365.760 104.140))',
            content,
        )
        self.assertIn(
            '(wire (pts (xy 365.760 104.140) (xy 365.760 135.890))',
            content,
        )
        self.assertEqual(
            (float(_direct(instances["R2"], "at")[1]), float(_direct(instances["R2"], "at")[2])),
            (391.160, 146.050),
        )
        self.assertEqual(
            (float(_direct(instances["R13"], "at")[1]), float(_direct(instances["R13"], "at")[2])),
            (365.760, 139.700),
        )
        self.assertIn('(label "FET_MID"', content)

    def test_power_path_is_split_into_switch_and_compact_output_stage(self) -> None:
        with tempfile.TemporaryDirectory(prefix="stusb4500-power-stages-") as temporary:
            content, tree, instances = self._generate(temporary)

        self.assertIn('(text "BACK-TO-BACK PMOS SWITCH"', content)
        self.assertIn('(text "VSNK OUTPUT / DISCHARGE"', content)

        positions = {
            reference: _direct(instances[reference], "at")
            for reference in ("T4", "T1", "C10", "D11", "R2")
        }
        self.assertLess(float(positions["T4"][1]), float(positions["T1"][1]))
        self.assertLess(float(positions["T1"][1]), float(positions["C10"][1]))
        output_x = {round(float(positions[reference][1]), 3) for reference in ("C10", "D11", "R2")}
        self.assertEqual(output_x, {391.160})
        self.assertLess(float(positions["C10"][2]), float(positions["D11"][2]))
        self.assertLess(float(positions["D11"][2]), float(positions["R2"][2]))

        frames = {}
        for rectangle in _all(tree, "rectangle"):
            uuid = _direct(rectangle, "uuid")
            if uuid is None:
                continue
            key = str(uuid[1])
            if key in {
                _uid("graphic:rectangle:zone:power-switch"),
                _uid("graphic:rectangle:zone:vsnk-output"),
            }:
                start = _direct(rectangle, "start")
                end = _direct(rectangle, "end")
                frames[key] = (
                    (float(start[1]), float(start[2])),
                    (float(end[1]), float(end[2])),
                )
        self.assertEqual(
            frames[_uid("graphic:rectangle:zone:power-switch")],
            ((297.18, 86.36), (372.11, 175.26)),
        )
        self.assertEqual(
            frames[_uid("graphic:rectangle:zone:vsnk-output")],
            ((374.65, 86.36), (411.48, 175.26)),
        )
        self.assertLess(frames[_uid("graphic:rectangle:zone:power-switch")][1][0],
                        frames[_uid("graphic:rectangle:zone:vsnk-output")][0][0])

    def test_vbus_conditioning_groups_are_explained(self) -> None:
        with tempfile.TemporaryDirectory(prefix="stusb4500-vbus-groups-") as temporary:
            content, _, _ = self._generate(temporary)

        self.assertIn('(text "VBUS BULK PROTECTION"', content)
        self.assertIn('(text "VBUS CLAMP + FILTER"', content)
        self.assertIn('(text "VBUS_VS_DISCH SENSE DIVIDER"', content)
        self.assertIn('(text "VBUS MONITOR / FILTER"', content)

    def test_resistor_values_are_centered_inside_their_symbol_bodies(self) -> None:
        with tempfile.TemporaryDirectory(prefix="stusb4500-resistor-fields-") as temporary:
            _, _, instances = self._generate(temporary)

        resistor_refs = ("R1", "R2", "R4", "R10", "R11", "R13", "R51", "R52", "R53", "R54", "R55")
        for reference in resistor_refs:
            instance = instances[reference]
            symbol_at = _direct(instance, "at")
            value_at = _direct(_property(instance, "Value"), "at")
            self.assertAlmostEqual(float(value_at[1]), float(symbol_at[1]), places=3, msg=reference)
            self.assertAlmostEqual(float(value_at[2]), float(symbol_at[2]), places=3, msg=reference)
            effects = _direct(_property(instance, "Value"), "effects")
            font = _direct(effects, "font")
            size = _direct(font, "size")
            self.assertLessEqual(float(size[1]), 0.72, msg=reference)
            # KiCad centers fields by default. "center" is not a valid
            # horizontal justify direction and makes the entire sheet unloadable.
            self.assertIsNone(_direct(effects, "justify"), msg=reference)
            for field_name in ("Reference", "Value"):
                field_angle = _direct(_property(instance, field_name), "at")[3]
                self.assertEqual(float(field_angle), 90.0, f"{reference} {field_name} angle")

    def test_j1_and_u1_fields_are_outside_their_symbol_bodies(self) -> None:
        with tempfile.TemporaryDirectory(prefix="stusb4500-fields-") as temporary:
            _, _, instances = self._generate(temporary)

        j1 = _direct(instances["J1"], "at")
        j1_y = float(j1[2])
        for field_name, expected_y in (("Reference", 106.680), ("Value", 109.220)):
            at = _direct(_property(instances["J1"], field_name), "at")
            self.assertAlmostEqual(float(at[1]), 45.720, places=3)
            self.assertAlmostEqual(float(at[2]), expected_y, places=3)
            self.assertLess(float(at[2]), j1_y - 27.94)  # above the connector body
            self.assertGreater(float(at[2]), 86.36)       # inside the USB module

        u1 = _direct(instances["U1"], "at")
        u1_x = float(u1[1])
        for field_name, expected_y in (("Reference", 95.250), ("Value", 97.790)):
            prop = _property(instances["U1"], field_name)
            at = _direct(prop, "at")
            self.assertAlmostEqual(float(at[1]), u1_x + 30.48, places=3)
            self.assertAlmostEqual(float(at[2]), expected_y, places=3)
            self.assertGreater(float(at[1]), u1_x + 25.40)
            self.assertLess(float(at[1]), 292.10)
            if field_name == "Reference":
                self.assertEqual(str(_direct(_direct(prop, "effects"), "justify")[1]), "left")
        r55 = _direct(instances["R55"], "at")
        self.assertAlmostEqual(float(r55[1]), float(u1[1]) - 22.860, places=3)
        self.assertLess(abs(float(r55[2]) - float(u1[2])), 15.0)
        self.assertAlmostEqual(float(r55[2]), 127.000, places=3)

    def test_note_box_is_sized_for_one_short_line(self) -> None:
        with tempfile.TemporaryDirectory(prefix="stusb4500-note-") as temporary:
            content, tree, _ = self._generate(temporary)

        self.assertIn('(text "D4 is unspecified; verify its VBUS rating."', content)
        self.assertNotIn('(text "USB 3.1 Type-C receptacle"', content)
        self.assertNotIn('(text "STUSB4500QTR"', content)
        note_uuid = _uid("graphic:rectangle:note:d4-selection")
        notes = [
            rectangle for rectangle in _all(tree, "rectangle")
            if _direct(rectangle, "uuid") and str(_direct(rectangle, "uuid")[1]) == note_uuid
        ]
        self.assertEqual(len(notes), 1)
        start = _direct(notes[0], "start")
        end = _direct(notes[0], "end")
        self.assertLess(float(end[1]) - float(start[1]), 40.0)
        self.assertLess(float(end[2]) - float(start[2]), 8.0)
        self.assertNotIn('(text "REFERENCE ONLY', content)

    def test_no_label_glyph_is_replaced_by_a_global_label(self) -> None:
        with tempfile.TemporaryDirectory(prefix="stusb4500-no-globals-") as temporary:
            content, _, _ = self._generate(temporary)

        self.assertEqual(content.count("(global_label"), 0)
        self.assertIn('(hierarchical_label "GND" (shape passive)', content)
        self.assertIn('(hierarchical_label "VSYS" (shape input)', content)

    def test_u1_top_supply_labels_are_horizontal_and_staggered(self) -> None:
        with tempfile.TemporaryDirectory(prefix="stusb4500-u1-top-labels-") as temporary:
            _content, tree, _ = self._generate(temporary)

        expected = {
            ("VBUS", 243.840, 111.760, 0.0),
            ("VSYS", 246.380, 109.220, 0.0),
            ("VREG_1V2", 256.540, 109.220, 0.0),
            ("VREG_2V7", 254.000, 95.250, 0.0),
        }
        actual = {
            (
                str(label[1]),
                round(float(_direct(label, "at")[1]), 3),
                round(float(_direct(label, "at")[2]), 3),
                round(float(_direct(label, "at")[3]), 3),
            )
            for label in (*_all(tree, "label"), *_all(tree, "hierarchical_label"))
        }
        for item in expected:
            self.assertIn(item, actual)

    def test_bulk_protection_frame_is_tight_to_its_components(self) -> None:
        with tempfile.TemporaryDirectory(prefix="stusb4500-bulk-frame-") as temporary:
            _content, tree, _ = self._generate(temporary)

        zone_uuid = _uid("graphic:rectangle:zone:vbus-bulk")
        zone = next(
            node for node in _all(tree, "rectangle")
            if _direct(node, "uuid") and str(_direct(node, "uuid")[1]) == zone_uuid
        )
        start = _direct(zone, "start")
        end = _direct(zone, "end")
        self.assertAlmostEqual(float(start[1]), 88.900, places=3)
        self.assertAlmostEqual(float(end[1]), 134.620, places=3)
        self.assertAlmostEqual(float(start[2]), 116.840, places=3)
        self.assertAlmostEqual(float(end[2]), 147.320, places=3)
        self.assertLess(float(end[2]) - float(start[2]), 35.0)

    def test_internal_vbus_and_power_path_nets_remain_local(self) -> None:
        with tempfile.TemporaryDirectory(prefix="stusb4500-local-nets-") as temporary:
            content, _, _ = self._generate(temporary)

        for name in (
            "VBUS", "CC1", "CC2", "VBUS_FILTERED", "VBUS_VS_DISCH",
            "VBUS_EN_SNK", "FET_MID", "VSNK_GATE", "DISCH",
            "VREG_1V2", "VREG_2V7", "ADDR0", "ADDR1",
        ):
            self.assertIn(f'(label "{name}"', content)
        self.assertNotIn('(global_label "', content)

    def test_vbus_filtered_nodes_use_local_labels_without_crossing_d4(self) -> None:
        with tempfile.TemporaryDirectory(prefix="stusb4500-vbus-filter-") as temporary:
            content, tree, instances = self._generate(temporary)

        filtered_labels = [
            node for node in _all(tree, "label")
            if len(node) > 1 and str(node[1]) == "VBUS_FILTERED"
        ]
        self.assertEqual(
            len(filtered_labels), 2,
            "D4.1 and R1.1 are the two reused labels on the wired filtered rail",
        )
        self.assertEqual(float(_direct(instances["D4"], "at")[3]), 180.0)
        sense_labels = [
            node for node in _all(tree, "label")
            if len(node) > 1 and str(node[1]) == "VBUS_VS_DISCH"
        ]
        self.assertIn(
            (194.310, 127.000),
            {
                (
                    round(float(_direct(label, "at")[1]), 3),
                    round(float(_direct(label, "at")[2]), 3),
                )
                for label in sense_labels
            },
        )
        for segment in (
            '(wire (pts (xy 163.830 111.760) (xy 172.720 111.760))',
            '(wire (pts (xy 172.720 111.760) (xy 185.420 111.760))',
            '(wire (pts (xy 185.420 119.380) (xy 203.200 119.380))',
        ):
            self.assertIn(segment, content)
        self.assertNotIn("(global_label", content)

    def test_esd_cc_labels_are_perpendicular_to_the_connector_traces(self) -> None:
        with tempfile.TemporaryDirectory(prefix="stusb4500-esd-labels-") as temporary:
            content, tree, _ = self._generate(temporary)

        label_positions = {
            (
                str(label[1]),
                round(float(_direct(label, "at")[1]), 3),
                round(float(_direct(label, "at")[2]), 3),
                round(float(_direct(label, "at")[3]), 3),
            )
            for label in _all(tree, "label")
        }
        self.assertIn(("CC1", 71.120, 132.080, 0.0), label_positions)
        self.assertIn(("CC2", 71.120, 147.320, 0.0), label_positions)
        self.assertIn(
            '(wire (pts (xy 71.120 137.160) (xy 71.120 132.080))',
            content,
        )
        self.assertIn(
            '(wire (pts (xy 71.120 142.240) (xy 71.120 147.320))',
            content,
        )
        self.assertIn('(label "CC1" (at 63.500 119.380', content)
        self.assertIn('(label "CC2" (at 63.500 121.920', content)

    def test_cc2_uses_a_separate_nonzero_route_into_the_esd_array(self) -> None:
        with tempfile.TemporaryDirectory(prefix="stusb4500-cc-routing-") as temporary:
            content, tree, _ = self._generate(temporary)

        self.assertIn('(label "CC1" (at 63.500 119.380', content)
        self.assertIn('(label "CC2" (at 63.500 121.920', content)
        for wire in _all(tree, "wire"):
            points = _direct(wire, "pts")
            coordinates = [
                (float(point[1]), float(point[2]))
                for point in _all(points, "xy")
            ]
            self.assertEqual(len(coordinates), 2)
            self.assertNotEqual(coordinates[0], coordinates[1])

    def test_kicad_text_justification_uses_only_supported_directions(self) -> None:
        with tempfile.TemporaryDirectory(prefix="stusb4500-justify-") as temporary:
            content, tree, _ = self._generate(temporary)

        self.assertNotIn("(justify center", content)
        for node in _all(tree, "justify"):
            for direction in node[1:]:
                self.assertIn(
                    str(direction),
                    {"left", "right", "top", "bottom", "mirror"},
                    f"Unsupported KiCad text justification: {node}",
                )

    def test_all_generated_wires_are_two_point_orthogonal_segments(self) -> None:
        with tempfile.TemporaryDirectory(prefix="stusb4500-wires-") as temporary:
            _content, tree, _ = self._generate(temporary)

        wires = _all(tree, "wire")
        self.assertTrue(wires)
        for wire in wires:
            pts = _direct(wire, "pts")
            coords = [
                (float(point[1]), float(point[2]))
                for point in _all(pts, "xy")
            ]
            self.assertEqual(len(coords), 2)
            (x1, y1), (x2, y2) = coords
            self.assertTrue(
                abs(x1 - x2) < 0.001 or abs(y1 - y2) < 0.001,
                f"Non-orthogonal wire: {coords}",
            )


    def test_decouplers_are_directly_wired_to_u1_supply_pins(self) -> None:
        with tempfile.TemporaryDirectory(prefix="stusb4500-decouplers-") as temporary:
            content, _tree, _instances = self._generate(temporary)

        for segment in (
            '(wire (pts (xy 251.460 114.300) (xy 251.460 92.710))',
            '(wire (pts (xy 251.460 92.710) (xy 226.060 92.710))',
            '(wire (pts (xy 226.060 92.710) (xy 226.060 97.790))',
            '(wire (pts (xy 254.000 114.300) (xy 254.000 90.170))',
            '(wire (pts (xy 254.000 90.170) (xy 246.380 90.170))',
            '(wire (pts (xy 246.380 90.170) (xy 246.380 97.790))',
            '(wire (pts (xy 254.000 90.170) (xy 266.700 90.170))',
            '(wire (pts (xy 266.700 90.170) (xy 266.700 97.790))',
        ):
            self.assertIn(segment, content)

    def test_every_local_label_name_is_reused(self) -> None:
        with tempfile.TemporaryDirectory(prefix="stusb4500-label-reuse-") as temporary:
            _content, tree, _instances = self._generate(temporary)

        uses: dict[str, int] = {}
        for label in _all(tree, "label"):
            name = str(label[1])
            uses[name] = uses.get(name, 0) + 1
        self.assertTrue(uses, "The schematic must retain useful local labels.")
        self.assertTrue(
            all(count > 1 for count in uses.values()),
            f"Every local label should be reused; found {uses}",
        )

    def test_header_helper_and_functional_blocks_are_used(self) -> None:
        with tempfile.TemporaryDirectory(prefix="stusb4500-header-") as temporary:
            content, tree, _instances = self._generate(temporary)

        self.assertIn('(text "STUSB4500 USB-C / POWER DELIVERY SINK"', content)
        self.assertIn('(text "DATASHEET-BASED REFERENCE', content)
        titles = {str(node[1]) for node in _all(tree, "text")}
        self.assertTrue({
            "USB-C PORT / CC PROTECTION", "VBUS BULK PROTECTION",
            "VBUS MONITOR / FILTER", "STUSB4500 CONTROLLER",
            "I2C ADDRESS STRAPS", "BACK-TO-BACK PMOS SWITCH",
            "VSNK OUTPUT / DISCHARGE",
        }.issubset(titles))

    def test_functional_frames_do_not_overlap_each_other(self) -> None:
        with tempfile.TemporaryDirectory(prefix="stusb4500-frame-overlap-") as temporary:
            _content, tree, _instances = self._generate(temporary)

        names = (
            "usb-port", "vbus-bulk", "vbus-monitor", "controller",
            "address-straps", "power-switch", "vsnk-output",
        )
        frames = []
        for name in names:
            frame_uuid = _uid(f"graphic:rectangle:zone:{name}")
            frame = next(
                node for node in _all(tree, "rectangle")
                if _direct(node, "uuid") and str(_direct(node, "uuid")[1]) == frame_uuid
            )
            start, end = _direct(frame, "start"), _direct(frame, "end")
            frames.append((
                name, float(start[1]), float(start[2]),
                float(end[1]), float(end[2]),
            ))
        bounds = {frame[0]: frame for frame in frames}
        self.assertAlmostEqual(bounds["vbus-monitor"][3], 213.360, places=3)
        self.assertAlmostEqual(bounds["controller"][1], 215.900, places=3)
        for index, first in enumerate(frames):
            for second in frames[index + 1:]:
                overlap_x = min(first[3], second[3]) - max(first[1], second[1])
                overlap_y = min(first[4], second[4]) - max(first[2], second[2])
                self.assertFalse(
                    overlap_x > 0 and overlap_y > 0,
                    f"Functional frames overlap: {first[0]} / {second[0]}",
                )


    def test_r55_ground_label_stub_does_not_cross_u1_cc2_pin4(self) -> None:
        with tempfile.TemporaryDirectory(prefix="stusb4500-u1-gnd-cc2-") as temporary:
            content, tree, instances = self._generate(temporary)
        self.assertIn(
            '(wire (pts (xy 231.140 119.380) (xy 223.520 119.380))',
            content,
        )
        self.assertIn(
            '(wire (pts (xy 223.520 119.380) (xy 223.520 123.190))',
            content,
        )
        self.assertAlmostEqual(float(_direct(instances["R55"], "at")[1]), 223.520, places=3)

        ground_pin = (223.520, 130.810)
        ground_stubs = []
        for wire in _all(tree, "wire"):
            points = _direct(wire, "pts")
            coordinates = [
                (round(float(point[1]), 3), round(float(point[2]), 3))
                for point in _all(points, "xy")
            ]
            if coordinates and coordinates[0] == ground_pin:
                ground_stubs.append(coordinates)

        self.assertEqual(len(ground_stubs), 1)
        start, end = ground_stubs[0]
        self.assertEqual(start[0], end[0], "R55 GND label stub must be vertical.")
        self.assertGreater(end[1], start[1], "R55 GND label must point below the resistor.")


if __name__ == "__main__":
    unittest.main()
