"""Tests for the STUSB4500 functional-block schematic and its parent interfaces."""

from __future__ import annotations

import sys
import tempfile
import unittest
from itertools import pairwise
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugin" / "kicad-blocks"))
sys.path.insert(0, str(ROOT / "examples" / "stusb4500"))
sys.path.insert(0, str(ROOT / "scripts"))

from generate_reference import SCHEMATIC_GROUPS, _uid, generate_reference_schematic
from kicad_blocks.pcb_layout import _all, _direct, parse_sexpr
from validate_schematic_style import (
    STYLE_RULES,
    _text_visual_bounds,
    _wire_networks,
    check_schematic_style,
    label_wire_overlap_issues,
)


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
            "VSNK OUTPUT",
        )
        for title in titles:
            self.assertIn(f'(text_box "{title}"', content)
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
            content, tree, instances = self._generate(temporary)

        for reference in ("R51", "R52", "R53", "R54"):
            self.assertIn(reference, instances)
            self.assertEqual(float(_direct(instances[reference], "at")[3]), 90.0)
        self.assertIn("R55", instances)
        self.assertLess(float(_direct(instances["R55"], "at")[2]), 240.0)

        r51, r52, r53, r54 = (
            _direct(instances[reference], "at")
            for reference in ("R51", "R52", "R53", "R54")
        )
        self.assertAlmostEqual(float(r51[2]), float(r53[2]), places=3)
        self.assertAlmostEqual(float(r52[2]), float(r54[2]), places=3)
        self.assertLess(float(r51[1]), float(r53[1]))
        self.assertLess(float(r52[1]), float(r54[1]))
        self.assertLess(abs(float(r51[2]) - float(r52[2])), 15.0)

        strap_names = {
            str(node[1]) for node in _all(tree, "label")
            if len(node) > 1 and str(node[1]) in {"ADDR0", "ADDR1"}
        }
        self.assertEqual(strap_names, {"ADDR0", "ADDR1"})
        self.assertNotIn("(global_label", content)


    def test_back_to_back_pmos_midpoint_is_directly_wired_to_r10(self) -> None:
        with tempfile.TemporaryDirectory(prefix="stusb4500-pmos-link-") as temporary:
            content, tree, _instances = self._generate(temporary)

        dsu, pins = _wire_networks(tree)
        t4 = pins[("T4", "3")]["point"]
        t1 = pins[("T1", "2")]["point"]
        r10 = pins[("R10", "1")]["point"]
        self.assertEqual(dsu.find(t4), dsu.find(t1))
        self.assertEqual(dsu.find(t1), dsu.find(r10))
        self.assertAlmostEqual((t4[0] + t1[0]) / 2, r10[0], places=3)
        self.assertNotIn('(label "FET_MID"', content)

    def test_power_path_is_split_into_switch_and_compact_output_stage(self) -> None:
        with tempfile.TemporaryDirectory(prefix="stusb4500-power-stages-") as temporary:
            content, tree, instances = self._generate(temporary)

        self.assertIn('(text_box "BACK-TO-BACK PMOS SWITCH"', content)
        self.assertIn('(text_box "VSNK OUTPUT"', content)
        positions = {
            reference: _direct(instances[reference], "at")
            for reference in ("T4", "T1", "C10", "D11", "R2")
        }
        self.assertLess(float(positions["T4"][1]), float(positions["T1"][1]))
        self.assertLess(float(positions["T1"][1]), float(positions["C10"][1]))
        output_x = {round(float(positions[reference][1]), 3) for reference in ("C10", "D11", "R2")}
        self.assertEqual(len(output_x), 1)
        self.assertLess(float(positions["C10"][2]), float(positions["D11"][2]))
        self.assertLess(float(positions["D11"][2]), float(positions["R2"][2]))

        def frame(name: str):
            node = next(
                node for node in _all(tree, "rectangle")
                if _direct(node, "uuid")
                and str(_direct(node, "uuid")[1]) == _uid(f"graphic:rectangle:zone:{name}")
            )
            start, end = _direct(node, "start"), _direct(node, "end")
            return (float(start[1]), float(start[2]), float(end[1]), float(end[2]))

        switch, output = frame("power-switch"), frame("vsnk-output")
        self.assertAlmostEqual(output[0] - switch[2], 2.54, places=3)



    def test_vbus_conditioning_groups_are_explained(self) -> None:
        with tempfile.TemporaryDirectory(prefix="stusb4500-vbus-groups-") as temporary:
            content, tree, _ = self._generate(temporary)

        text_by_uuid = {
            str(_direct(node, "uuid")[1]): str(node[1])
            for node in _all(tree, "text_box") if _direct(node, "uuid") is not None
        }
        group = next(item for item in SCHEMATIC_GROUPS if item["id"] == "vbus-monitor")
        description_uuid = _uid("graphic:text:group-description:vbus-monitor")
        self.assertIn(description_uuid, text_by_uuid)
        self.assertIn(group["description"], text_by_uuid[description_uuid])
        self.assertIn("ST DS12499 REV. 8 | PAGE 26 | FIGURE 10", text_by_uuid[description_uuid])
        self.assertIn("VBUS BULK PROTECTION", content)
        self.assertIn("VBUS MONITOR / FILTER", content)

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



    def test_j1_and_u1_fields_are_close_and_inside_their_groups(self) -> None:
        with tempfile.TemporaryDirectory(prefix="stusb4500-fields-") as temporary:
            _, _, instances = self._generate(temporary)

        j1 = _direct(instances["J1"], "at")
        for field_name in ("Reference", "Value"):
            at = _direct(_property(instances["J1"], field_name), "at")
            self.assertAlmostEqual(float(at[1]), float(j1[1]), places=3)
            self.assertLess(float(at[2]), float(j1[2]) - 27.94)

        u1 = _direct(instances["U1"], "at")
        for field_name, expected_y in (("Reference", float(u1[2]) - 17.78),
                                       ("Value", float(u1[2]) - 15.24)):
            prop = _property(instances["U1"], field_name)
            at = _direct(prop, "at")
            self.assertAlmostEqual(float(at[1]), float(u1[1]) + 15.24, places=3)
            self.assertAlmostEqual(float(at[2]), expected_y, places=3)
            if field_name == "Reference":
                self.assertEqual(str(_direct(_direct(prop, "effects"), "justify")[1]), "left")
            else:
                self.assertEqual(str(_direct(_direct(prop, "effects"), "justify")[1]), "left")

        r55 = _direct(instances["R55"], "at")
        self.assertAlmostEqual(float(r55[1]), float(u1[1]) - 22.86, places=3)
        self.assertLess(abs(float(r55[2]) - float(u1[2])), 15.0)

    def test_group_descriptions_and_source_evidence_are_generated(self) -> None:
        with tempfile.TemporaryDirectory(prefix="stusb4500-group-descriptions-") as temporary:
            content, tree, _ = self._generate(temporary)

        text_by_uuid = {
            str(_direct(node, "uuid")[1]): str(node[1])
            for node in _all(tree, "text_box") if _direct(node, "uuid") is not None
        }
        for group in SCHEMATIC_GROUPS:
            title_uuid = _uid(f"graphic:text:zone-title:{group['id']}")
            description_uuid = _uid(f"graphic:text:group-description:{group['id']}")
            self.assertIn(title_uuid, text_by_uuid)
            self.assertIn(description_uuid, text_by_uuid)
            self.assertIn(group["description"], text_by_uuid[description_uuid])
            self.assertIn("ST DS12499 REV. 8 | PAGE 26 | FIGURE 10", text_by_uuid[description_uuid])
        self.assertNotIn("D4 is unspecified; verify its VBUS rating.", content)
        header_title = next(
            node for node in _all(tree, "text_box")
            if str(node[1]) == "STUSB4500 USB-C / POWER DELIVERY SINK"
        )
        self.assertGreater(float(_direct(header_title, "size")[1]), 350.0)
        self.assertEqual(float(_direct(_direct(header_title, "stroke"), "width")[1]), -1.0)

    def test_no_label_glyph_is_replaced_by_a_global_label(self) -> None:
        with tempfile.TemporaryDirectory(prefix="stusb4500-no-globals-") as temporary:
            content, _, _ = self._generate(temporary)

        self.assertEqual(content.count("(global_label"), 0)
        self.assertIn('(hierarchical_label "GND" (shape passive)', content)
        self.assertIn('(hierarchical_label "VSYS" (shape input)', content)


    def test_regulator_decouplers_use_direct_wires_not_pin_labels(self) -> None:
        with tempfile.TemporaryDirectory(prefix="stusb4500-u1-top-labels-") as temporary:
            content, tree, _ = self._generate(temporary)

        local_labels = {str(node[1]) for node in _all(tree, "label")}
        self.assertNotIn("VREG_1V2", local_labels)
        self.assertIn("VREG_2V7", local_labels)
        self.assertNotIn('(label "FET_MID"', content)
        self.assertIn('(wire (pts ', content)


    def test_bulk_protection_frame_is_derived_from_member_components(self) -> None:
        with tempfile.TemporaryDirectory(prefix="stusb4500-bulk-frame-") as temporary:
            _content, tree, instances = self._generate(temporary)

        zone_uuid = _uid("graphic:rectangle:zone:vbus-bulk")
        zone = next(
            node for node in _all(tree, "rectangle")
            if _direct(node, "uuid") and str(_direct(node, "uuid")[1]) == zone_uuid
        )
        start, end = _direct(zone, "start"), _direct(zone, "end")
        for reference in ("C50", "D50"):
            at = _direct(instances[reference], "at")
            self.assertLess(float(start[1]), float(at[1]))
            self.assertLess(float(start[2]), float(at[2]))
            self.assertGreater(float(end[1]), float(at[1]))
            self.assertGreater(float(end[2]), float(at[2]))
        self.assertGreater(float(end[1]) - float(start[1]), 20.0)


    def test_internal_nets_and_hierarchical_vsnk_scope_are_consistent(self) -> None:
        with tempfile.TemporaryDirectory(prefix="stusb4500-local-nets-") as temporary:
            content, tree, _ = self._generate(temporary)

        labels = {str(node[1]) for node in _all(tree, "label")}
        hierarchical = {str(node[1]) for node in _all(tree, "hierarchical_label")}
        for name in (
            "VBUS", "CC1", "CC2", "VBUS_FILTERED", "VBUS_VS_DISCH",
            "VBUS_EN_SNK", "VSNK_GATE", "DISCH", "VREG_2V7", "ADDR0", "ADDR1",
        ):
            self.assertIn(name, labels)
        self.assertNotIn("VSNK", labels)
        self.assertIn("VSNK", hierarchical)
        self.assertNotIn("VREG_1V2", labels)
        self.assertNotIn("FET_MID", labels)
        self.assertNotIn('(global_label "', content)


    def test_decoupler_ground_uses_one_physical_hierarchical_port(self) -> None:
        with tempfile.TemporaryDirectory(prefix="stusb4500-shared-gnd-") as temporary:
            content, tree, _instances = self._generate(temporary)
        dsu, pins = _wire_networks(tree)
        c2 = pins[("C2", "2")]["point"]
        c3 = pins[("C3", "2")]["point"]
        self.assertEqual(dsu.find(c2), dsu.find(c3))
        ground_labels = []
        for node in _all(tree, "hierarchical_label"):
            at = _direct(node, "at")
            if str(node[1]) == "GND" and at is not None:
                point = (round(float(at[1]), 3), round(float(at[2]), 3))
                if dsu.find(point) == dsu.find(c2):
                    ground_labels.append(node)
        self.assertEqual(len(ground_labels), 1)
        self.assertNotIn('(label "GND"', content)

    def test_controller_bypass_caps_are_repositioned_for_short_c1_route(self) -> None:
        with tempfile.TemporaryDirectory(prefix="stusb4500-bypass-layout-") as temporary:
            _content, tree, instances = self._generate(temporary)
        u1_x = float(_direct(instances["U1"], "at")[1])
        self.assertAlmostEqual(float(_direct(instances["C1"], "at")[1]), u1_x + 5.08, places=3)
        self.assertAlmostEqual(float(_direct(instances["C2"], "at")[1]), u1_x + 15.24, places=3)
        self.assertAlmostEqual(float(_direct(instances["C3"], "at")[1]), u1_x + 20.32, places=3)
        dsu, pins = _wire_networks(tree)
        c1 = pins[("C1", "1")]["point"]
        u1 = pins[("U1", "21")]["point"]
        self.assertEqual(c1[0], u1[0])
        self.assertEqual(dsu.find(c1), dsu.find(u1))

    def test_back_to_back_fets_are_condensed(self) -> None:
        with tempfile.TemporaryDirectory(prefix="stusb4500-compact-fets-") as temporary:
            _content, _tree, instances = self._generate(temporary)
        t4_x = float(_direct(instances["T4"], "at")[1])
        t1_x = float(_direct(instances["T1"], "at")[1])
        self.assertAlmostEqual(t1_x - t4_x, 25.40, places=3)

    def test_vbus_filtered_nodes_use_local_labels_without_crossing_d4(self) -> None:
        with tempfile.TemporaryDirectory(prefix="stusb4500-vbus-filter-") as temporary:
            content, tree, instances = self._generate(temporary)

        filtered_labels = [
            node for node in _all(tree, "label")
            if len(node) > 1 and str(node[1]) == "VBUS_FILTERED"
        ]
        self.assertEqual(len(filtered_labels), 2)
        self.assertEqual(float(_direct(instances["D4"], "at")[3]), 180.0)
        sense_labels = [
            node for node in _all(tree, "label")
            if len(node) > 1 and str(node[1]) == "VBUS_VS_DISCH"
        ]
        self.assertEqual(len(sense_labels), 2)
        r1, r4 = _direct(instances["R1"], "at"), _direct(instances["R4"], "at")
        self.assertAlmostEqual(abs(float(r4[1]) - float(r1[1])), 5.08, places=3)
        self.assertAlmostEqual(float(r4[2]), float(r1[2]), places=3)
        for wire in _all(tree, "wire"):
            coords = [
                (float(p[1]), float(p[2]))
                for p in _all(_direct(wire, "pts"), "xy")
            ]
            self.assertEqual(len(coords), 2)
            self.assertTrue(abs(coords[0][0] - coords[1][0]) < 0.001
                            or abs(coords[0][1] - coords[1][1]) < 0.001)
        self.assertNotIn("(global_label", content)


    def test_esd_array_ground_faces_down_and_cc_labels_remain_visible(self) -> None:
        with tempfile.TemporaryDirectory(prefix="stusb4500-esd-labels-") as temporary:
            content, tree, instances = self._generate(temporary)

        self.assertEqual(float(_direct(instances["D1"], "at")[3]), 270.0)
        label_names = [str(node[1]) for node in _all(tree, "label")]
        self.assertGreaterEqual(label_names.count("CC1"), 2)
        self.assertGreaterEqual(label_names.count("CC2"), 2)
        self.assertNotIn('(global_label "CC1"', content)
        self.assertNotIn('(global_label "CC2"', content)


    def test_cc2_route_uses_only_valid_orthogonal_segments(self) -> None:
        with tempfile.TemporaryDirectory(prefix="stusb4500-cc-routing-") as temporary:
            _content, tree, _ = self._generate(temporary)

        for wire in _all(tree, "wire"):
            coordinates = [
                (float(point[1]), float(point[2]))
                for point in _all(_direct(wire, "pts"), "xy")
            ]
            self.assertEqual(len(coordinates), 2)
            self.assertNotEqual(coordinates[0], coordinates[1])
            self.assertTrue(
                abs(coordinates[0][0] - coordinates[1][0]) < 0.001
                or abs(coordinates[0][1] - coordinates[1][1]) < 0.001
            )

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
            _content, tree, _instances = self._generate(temporary)

        labels = {str(node[1]) for node in _all(tree, "label")}
        self.assertNotIn("VREG_1V2", labels)
        self.assertNotIn("FET_MID", labels)
        self.assertEqual(check_schematic_style(tree), [])

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

        self.assertIn('(text_box "STUSB4500 USB-C / POWER DELIVERY SINK"', content)
        self.assertIn('(text_box "DATASHEET-BASED REFERENCE', content)
        titles = {str(node[1]) for node in _all(tree, "text_box")}
        self.assertTrue({
            "USB-C PORT / CC PROTECTION", "VBUS BULK PROTECTION",
            "VBUS MONITOR / FILTER", "STUSB4500 CONTROLLER",
            "I2C ADDRESS STRAPS", "BACK-TO-BACK PMOS SWITCH",
            "VSNK OUTPUT",
        }.issubset(titles))


    def test_component_origins_are_derived_from_group_constraints(self) -> None:
        with tempfile.TemporaryDirectory(prefix="stusb4500-group-layout-") as temporary:
            _content, _tree, instances = self._generate(temporary)

        for group in SCHEMATIC_GROUPS:
            layout = group["layout"]
            members = set(group["members"])
            targets = {str(rule["component"]) for rule in layout["constraints"]}
            self.assertIn(layout["anchor"], members)
            self.assertEqual(
                {layout["anchor"]} | targets, members,
                f"Every component in {group['id']} must be constrained from its group anchor.",
            )

        def origin(reference: str) -> tuple[float, float]:
            at = _direct(instances[reference], "at")
            return float(at[1]), float(at[2])

        def delta(first: str, second: str) -> tuple[float, float]:
            a, b = origin(first), origin(second)
            return round(b[0] - a[0], 3), round(b[1] - a[1], 3)

        self.assertEqual(delta("J1", "D1"), (33.02, 0.0))
        self.assertEqual(delta("C50", "D50"), (20.32, 0.0))
        self.assertEqual(delta("D4", "C4"), (12.70, 3.81))
        self.assertEqual(delta("C4", "R1"), (12.70, 0.0))
        self.assertEqual(delta("R1", "R4"), (5.08, 0.0))
        self.assertEqual(delta("U1", "C1"), (-20.32, -35.56))
        self.assertEqual(delta("U1", "C2"), (0.0, -35.56))
        self.assertEqual(delta("C2", "C3"), (5.08, 0.0))
        self.assertEqual(delta("R51", "R52"), (0.0, 12.70))
        self.assertEqual(delta("R51", "R53"), (20.32, 0.0))
        self.assertEqual(delta("T4", "T1"), (40.64, 0.0))
        t4, t1, r10 = origin("T4"), origin("T1"), origin("R10")
        self.assertAlmostEqual(r10[0], (t4[0] + t1[0]) / 2, places=3)
        self.assertAlmostEqual(r10[1] - t4[1], 17.78, places=3)
        self.assertEqual(delta("R10", "R11"), (7.62, 7.62))
        self.assertEqual(delta("R11", "R13"), (20.32, 0.0))
        self.assertEqual(delta("C10", "D11"), (0.0, 25.40))
        self.assertEqual(delta("C10", "R2"), (0.0, 46.99))

    def test_functional_frames_are_derived_and_packed_with_constant_spacing(self) -> None:
        with tempfile.TemporaryDirectory(prefix="stusb4500-frame-overlap-") as temporary:
            _content, tree, _instances = self._generate(temporary)

        frames = {}
        for group in SCHEMATIC_GROUPS:
            frame_uuid = _uid(f"graphic:rectangle:zone:{group['id']}")
            frame = next(
                node for node in _all(tree, "rectangle")
                if _direct(node, "uuid") and str(_direct(node, "uuid")[1]) == frame_uuid
            )
            start, end = _direct(frame, "start"), _direct(frame, "end")
            frames[group["id"]] = (
                float(start[1]), float(start[2]), float(end[1]), float(end[2]),
            )
        main = sorted(
            (group for group in SCHEMATIC_GROUPS if group["lane"] == "main"),
            key=lambda group: int(group["order"]),
        )
        for first, second in pairwise(main):
            gap = frames[second["id"]][0] - frames[first["id"]][2]
            self.assertAlmostEqual(gap, 2.54, places=3)
        for i, first in enumerate(SCHEMATIC_GROUPS):
            a = frames[first["id"]]
            for second in SCHEMATIC_GROUPS[i + 1:]:
                b = frames[second["id"]]
                overlap_x = min(a[2], b[2]) - max(a[0], b[0])
                overlap_y = min(a[3], b[3]) - max(a[1], b[1])
                self.assertFalse(
                    overlap_x > 0 and overlap_y > 0,
                    f"Functional frames overlap: {first['id']} / {second['id']}",
                )

    def test_r51_r52_use_populated_values_with_dnp_flags(self) -> None:
        with tempfile.TemporaryDirectory(prefix="stusb4500-dnp-") as temporary:
            _, _, instances = self._generate(temporary)

        for reference in ("R51", "R52"):
            instance = instances[reference]
            self.assertEqual(str(_property(instance, "Value")[2]), "100k")
            self.assertEqual(str(_direct(instance, "dnp")[1]).lower(), "yes")

    def test_style_linter_exposes_stable_rule_ids(self) -> None:
        rule_ids = tuple(STYLE_RULES)
        self.assertEqual(rule_ids, tuple(f"SCH{index:03d}" for index in range(1, 19)))
        self.assertTrue(all(rule["severity"] in {"error", "warning"} for rule in STYLE_RULES.values()))
        self.assertTrue(all(rule["name"] and rule["description"] for rule in STYLE_RULES.values()))

    def test_label_glyph_wire_overlap_is_a_dedicated_error(self) -> None:
        with tempfile.TemporaryDirectory(prefix="stusb4500-label-overlap-") as temporary:
            _content, tree, _instances = self._generate(temporary)

        label = next(
            node for node in _all(tree, "label")
            if str(node[1]) == "CC1"
        )
        x, y, width, height = _text_visual_bounds(str(label[1]), label)
        wire_y = y + height / 2
        tree.append([
            "wire",
            ["pts",
                ["xy", f"{x - 1.0:.3f}", f"{wire_y:.3f}"],
                ["xy", f"{x + width + 1.0:.3f}", f"{wire_y:.3f}"],
            ],
            ["stroke", ["width", "0"], ["type", "default"]],
            ["uuid", "00000000-0000-0000-0000-000000000001"],
        ])
        issues = label_wire_overlap_issues(tree)
        self.assertTrue(
            any("CC1" in issue and "overlaps a wire" in issue for issue in issues),
            issues,
        )

    def test_native_text_boxes_have_uniform_padding_and_style_audit_passes(self) -> None:
        with tempfile.TemporaryDirectory(prefix="stusb4500-style-audit-") as temporary:
            _content, tree, _ = self._generate(temporary)

        self.assertEqual(_all(tree, "text"), [])
        self.assertGreater(len(_all(tree, "text_box")), 0)
        style_issues = check_schematic_style(tree)
        for issue in style_issues:
            print(f"SCHEMATIC_STYLE: {issue}")
        self.assertEqual(style_issues, [], "Generated schematic style violations")


    def test_r55_ground_label_stub_remains_local_and_does_not_cross_cc2(self) -> None:
        with tempfile.TemporaryDirectory(prefix="stusb4500-u1-gnd-cc2-") as temporary:
            _content, tree, instances = self._generate(temporary)
        u1 = _direct(instances["U1"], "at")
        r55 = _direct(instances["R55"], "at")
        self.assertAlmostEqual(float(r55[1]), float(u1[1]) - 22.86, places=3)
        self.assertLess(abs(float(r55[2]) - float(u1[2])), 15.0)
        self.assertEqual(check_schematic_style(tree), [])


if __name__ == "__main__":
    unittest.main()
