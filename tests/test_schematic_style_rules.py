"""Regression tests for generic schematic style rules and profile contracts."""

from __future__ import annotations

import copy
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugin" / "kicad-blocks"))
sys.path.insert(0, str(ROOT / "examples" / "stusb4500"))
sys.path.insert(0, str(ROOT / "scripts"))

from generate_reference import generate_reference_schematic
from kicad_blocks.pcb_layout import _all, _direct, parse_sexpr
from validate_schematic_style import (
    STYLE_RULES,
    _pin_world_info,
    _wire_networks,
    block_annotation_issues,
    group_comment_outline_issues,
    check_generic_schematic_style,
    check_schematic_style,
    common_pin_net_issues,
    required_no_connect_issues,
    symbol_field_clearance_issues,
    wire_compactness_issues,
    wire_obstacle_issues,
)


class SchematicStyleRuleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temporary = tempfile.TemporaryDirectory(prefix="schematic-style-rules-")
        path = generate_reference_schematic(Path(cls.temporary.name) / "reference.kicad_sch")
        cls.source = parse_sexpr(path.read_text(encoding="utf-8"))

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temporary.cleanup()

    def fresh_tree(self) -> list:
        return copy.deepcopy(self.source)

    def test_rule_catalogue_and_generated_reference_pass(self) -> None:
        self.assertEqual(len(STYLE_RULES), 18)
        self.assertEqual(STYLE_RULES["SCH010"]["scope"], "generic")
        self.assertEqual(STYLE_RULES["SCH014"]["scope"], "profile")
        self.assertEqual(check_schematic_style(self.fresh_tree()), [])
        self.assertEqual(check_generic_schematic_style(self.fresh_tree()), [])

    def test_multiline_block_title_is_rejected(self) -> None:
        tree = self.fresh_tree()
        title = next(
            node for node in _all(tree, "text_box")
            if str(node[1]) == "USB-C PORT / CC PROTECTION"
        )
        title[1] = str(title[1]) + "\nWRAPPED"
        self.assertTrue(any("single line" in issue for issue in block_annotation_issues(tree)))

    def test_generic_checker_does_not_import_example_generator(self) -> None:
        script = """
import sys
from pathlib import Path
sys.path.insert(0, str(Path.cwd() / "scripts"))
from validate_schematic_style import check_generic_schematic_style
assert check_generic_schematic_style(["kicad_sch"]) == []
assert "generate_reference" not in sys.modules
"""
        completed = subprocess.run(
            [sys.executable, "-c", script],
            cwd=ROOT,
            capture_output=True,
            check=False,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_generic_multiline_unbordered_text_is_rejected(self) -> None:
        tree = [
            "kicad_sch",
            [
                "text_box", "BLOCK TITLE\nWRAPPED",
                ["at", "10", "10", "0"], ["size", "40", "8"],
                ["stroke", ["width", "-1"]],
                ["uuid", "future-schematic-title"],
            ],
        ]
        self.assertTrue(any(
            "multiline annotation" in issue
            for issue in block_annotation_issues(tree)
        ))

    def test_group_comment_requires_its_own_outline(self) -> None:
        tree = self.fresh_tree()
        note = next(
            node for node in _all(tree, "text_box")
            if str(node[1]).startswith("Type-C receptacle and dual CC-line ESD protection.")
        )
        width = _direct(_direct(note, "stroke"), "width")
        width[1] = "-1"
        self.assertTrue(any(
            "visible box outline" in issue for issue in group_comment_outline_issues(tree)
        ))

    def test_symbol_fields_may_not_straddle_their_body(self) -> None:
        tree = self.fresh_tree()
        d1 = next(
            node for node in _all(tree, "symbol")
            if any(str(prop[1]) == "Reference" and str(prop[2]) == "D1"
                   for prop in _all(node, "property"))
        )
        value = next(prop for prop in _all(d1, "property") if str(prop[1]) == "Value")
        d1_at = _direct(d1, "at")
        value_at = _direct(value, "at")
        value_at[1] = f"{float(d1_at[1]) + 2.54:.3f}"
        value_at[2] = str(d1_at[2])
        self.assertTrue(
            any("D1 Value partially straddles" in issue
                for issue in symbol_field_clearance_issues(tree))
        )

    def test_wire_through_symbol_body_is_rejected(self) -> None:
        tree = self.fresh_tree()
        tree.append([
            "wire", ["pts", ["xy", "234.000", "137.000"],
                     ["xy", "258.000", "137.000"]],
        ])
        self.assertTrue(any("symbol body U1" in issue for issue in wire_obstacle_issues(tree)))

    def test_wire_through_comment_box_is_rejected(self) -> None:
        tree = self.fresh_tree()
        note = next(
            node for node in _all(tree, "text_box")
            if str(node[1]).startswith("Type-C receptacle and dual CC-line ESD protection.")
        )
        at, size = _direct(note, "at"), _direct(note, "size")
        x = float(at[1]) + float(size[1]) / 2
        y = float(at[2]) + float(size[2]) / 2
        tree.append([
            "wire",
            ["pts", ["xy", f"{x - 2.0:.3f}", f"{y:.3f}"],
             ["xy", f"{x + 2.0:.3f}", f"{y:.3f}"]],
        ])
        self.assertTrue(any(
            "annotation text box" in issue for issue in wire_obstacle_issues(tree)
        ))

    def test_shared_decoupler_ground_and_required_nc_marker_are_present(self) -> None:
        tree = self.fresh_tree()
        self.assertEqual(common_pin_net_issues(tree), [])
        self.assertEqual(required_no_connect_issues(tree), [])
        point = _pin_world_info(tree)[("U1", "3")]["point"]
        marker = next(
            node for node in _all(tree, "no_connect")
            if _direct(node, "at") is not None
            and (round(float(_direct(node, "at")[1]), 3),
                 round(float(_direct(node, "at")[2]), 3)) == point
        )
        tree.remove(marker)
        self.assertTrue(any("U1.3" in issue for issue in required_no_connect_issues(tree)))

    def test_common_ground_contract_detects_a_wrong_decoupler_net(self) -> None:
        tree = self.fresh_tree()
        dsu, pin_info = _wire_networks(tree)
        root = dsu.find(pin_info[("C3", "2")]["point"])
        label = None
        for kind in ("label", "hierarchical_label", "global_label"):
            for node in _all(tree, kind):
                at = _direct(node, "at")
                if at is None:
                    continue
                point = (round(float(at[1]), 3), round(float(at[2]), 3))
                if str(node[1]) == "GND" and dsu.find(point) == root:
                    label = node
                    break
            if label is not None:
                break
        self.assertIsNotNone(label)
        label[1] = "GND_LOCAL"
        self.assertTrue(any("common net GND" in issue for issue in common_pin_net_issues(tree)))

    def test_wire_compactness_uses_a_normalized_budget_score(self) -> None:
        tree = self.fresh_tree()
        self.assertEqual(wire_compactness_issues(tree), [])
        self.assertTrue(
            any("score is" in issue
                for issue in wire_compactness_issues(tree, {"power-switch": 1.0}))
        )


if __name__ == "__main__":
    unittest.main()
