"""Tests for plugin-managed recursive BOM and ERC operations."""

from __future__ import annotations

import csv
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

PLUGIN = Path(__file__).resolve().parents[1] / "plugin" / "kicad-blocks"
sys.path.insert(0, str(PLUGIN))

from kicad_blocks.model import parse_pins
from kicad_blocks.operations import export_block_bom, validate_block_tree
from kicad_blocks.repository import add_dependency, create_block


class RecursiveBlockOperationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "library"
        pins = parse_pins("VIN:input, GND:passive, VOUT:output")
        self.child = create_block(self.root, "Power Stage", pins)
        self.parent = create_block(self.root, "Controller", pins)
        self.parent = add_dependency(self.parent, self.child)

    def tearDown(self):
        self.temp.cleanup()

    def test_export_bom_combines_nested_blocks_and_namespaces_references(self):
        output = Path(self.temp.name) / "merged.csv"

        def fake_run(command, capture_output, text, check):
            output_path = Path(command[command.index("--output") + 1])
            implementation_path = Path(command[-1])
            if implementation_path == self.child.implementation:
                reference, value = "R1", "10k"
            else:
                # The parent schematic contains the B1 child facade as a
                # composition placeholder. It must not duplicate the child's
                # expanded contents in the merged block BOM.
                reference, value = "B1", "Power Stage facade"
            output_path.write_text(
                f"Reference,Qty,Value\n{reference},1,{value}\n",
                encoding="utf-8",
            )
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        with patch("kicad_blocks.operations.subprocess.run", side_effect=fake_run):
            result = export_block_bom(self.parent, output, "kicad-cli", self.root)

        self.assertEqual(result["block_count"], 2)
        self.assertEqual(result["row_count"], 1)
        with output.open("r", encoding="utf-8", newline="") as stream:
            rows = list(csv.DictReader(stream))
        self.assertEqual(
            {row["Reference"] for row in rows},
            {"B1/Power Stage::R1"},
        )
        self.assertEqual({row["Block"] for row in rows}, {"B1/Power Stage"})

    def test_repeated_child_instances_produce_separate_recursive_bom_rows(self):
        self.parent = add_dependency(self.parent, self.child, reference="B2")
        output = Path(self.temp.name) / "repeated.csv"

        def fake_run(command, capture_output, text, check):
            output_path = Path(command[command.index("--output") + 1])
            implementation_path = Path(command[-1])
            reference = "R1" if implementation_path == self.child.implementation else "U1"
            output_path.write_text(
                f"Reference,Qty,Value\n{reference},1,10k\n",
                encoding="utf-8",
            )
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        with patch("kicad_blocks.operations.subprocess.run", side_effect=fake_run):
            result = export_block_bom(self.parent, output, "kicad-cli", self.root)

        self.assertEqual(result["block_count"], 3)
        with output.open("r", encoding="utf-8", newline="") as stream:
            rows = list(csv.DictReader(stream))
        refs = [row["Reference"] for row in rows]
        self.assertEqual(refs.count("B1/Power Stage::R1"), 1)
        self.assertEqual(refs.count("B2/Power Stage::R1"), 1)

    def test_recursive_erc_runs_once_for_each_block_and_records_violations(self):
        def fake_run(command, capture_output, text, check):
            report = Path(command[command.index("--output") + 1])
            report.write_text('{"violations":[]}', encoding="utf-8")
            implementation_path = Path(command[-1])
            code = 5 if implementation_path == self.child.implementation else 0
            return SimpleNamespace(returncode=code, stdout="", stderr="")

        with patch("kicad_blocks.operations.subprocess.run", side_effect=fake_run) as run:
            reports = validate_block_tree(
                self.parent,
                Path(self.temp.name) / "erc",
                "kicad-cli",
                self.root,
            )

        self.assertEqual(run.call_count, 2)
        self.assertEqual([item["block"] for item in reports], ["Power Stage", "Controller"])
        self.assertEqual([item["violations"] for item in reports], [True, False])
        self.assertTrue(all(Path(item["report"]).is_file() for item in reports))


if __name__ == "__main__":
    unittest.main()
