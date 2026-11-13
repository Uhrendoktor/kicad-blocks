"""Tests for shared-library manifest, asset and dependency validation."""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugin" / "kicad-blocks"
from kicad_blocks.model import parse_parameters, parse_pins
from kicad_blocks.operations import validate_block_library
from kicad_blocks.repository import create_block


class LibraryValidationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "library"
        self.reports = Path(self.temp.name) / "reports"
        self.root.mkdir()

    def tearDown(self):
        self.temp.cleanup()

    def create_block(self):
        return create_block(
            self.root,
            "Validated Power Block",
            parse_pins("VIN:input, GND:passive, VOUT:output"),
            parameters=parse_parameters("OUTPUT_VOLTAGE:number=3.3"),
        )

    def test_valid_library_writes_machine_readable_report(self):
        block = self.create_block()

        report = validate_block_library(self.root, self.reports, run_erc=False)

        self.assertEqual(report["status"], "passed")
        self.assertEqual(report["block_count"], 1)
        self.assertEqual(report["passed_count"], 1)
        self.assertEqual(report["failed_count"], 0)
        self.assertEqual(report["blocks"][0]["name"], block.name)
        self.assertTrue(Path(report["report"]).is_file())
        saved = json.loads(Path(report["report"]).read_text(encoding="utf-8"))
        self.assertEqual(saved["blocks"][0]["status"], "passed")

    def test_asset_changes_are_reported_as_hash_and_revision_mismatches(self):
        block = self.create_block()
        implementation = Path(block.implementation)
        implementation.write_text(
            '(kicad_sch (version 20250114) (generator "tampered"))\n',
            encoding="utf-8",
        )

        report = validate_block_library(self.root, self.reports, run_erc=False)

        self.assertEqual(report["status"], "failed")
        result = report["blocks"][0]
        self.assertEqual(result["status"], "failed")
        self.assertIn("implementation.kicad_sch", result["hash_mismatches"])
        self.assertTrue(any("checksum" in error.casefold() for error in result["errors"]))

    def test_missing_assets_are_reported_without_aborting_other_blocks(self):
        first = self.create_block()
        second = create_block(
            self.root,
            "Second Validated Block",
            parse_pins("IN:input, OUT:output"),
        )
        (first.path / first.manifest["implementation_file"]).unlink()

        report = validate_block_library(self.root, self.reports, run_erc=False)

        self.assertEqual(report["block_count"], 2)
        failed = next(item for item in report["blocks"] if item["block_id"] == first.manifest["block_id"])
        passed = next(item for item in report["blocks"] if item["block_id"] == second.manifest["block_id"])
        self.assertEqual(failed["status"], "failed")
        self.assertIn("implementation.kicad_sch", failed["missing_assets"])
        self.assertEqual(passed["status"], "passed")

    def test_corrupt_manifest_is_reported_and_does_not_hide_other_blocks(self):
        good = self.create_block()
        broken = self.root / "broken.kicad_block"
        broken.mkdir()
        (broken / "manifest.json").write_text("{broken json", encoding="utf-8")

        report = validate_block_library(self.root, self.reports, run_erc=False)

        self.assertEqual(report["block_count"], 2)
        good_result = next(item for item in report["blocks"] if item["block_id"] == good.manifest["block_id"])
        broken_result = next(item for item in report["blocks"] if item["name"] == "broken")
        self.assertEqual(good_result["status"], "passed")
        self.assertEqual(broken_result["status"], "failed")
        self.assertTrue(any("Manifest" in error for error in broken_result["errors"]))

    def test_full_validation_runs_recursive_erc_when_kicad_cli_is_available(self):
        configured_cli = os.environ.get("KICAD_CLI")
        cli_available = bool(
            (configured_cli and Path(configured_cli).is_file())
            or shutil.which("kicad-cli-nightly")
            or shutil.which("kicad-cli")
        )
        if not cli_available:
            self.skipTest("KiCad CLI is not available in this environment.")

        self.create_block()
        report = validate_block_library(self.root, self.reports, run_erc=True)

        self.assertEqual(report["block_count"], 1)
        result = report["blocks"][0]
        self.assertIn(result["status"], ("passed", "warnings"))
        self.assertEqual(len(result["erc"]), 1)
        self.assertTrue(Path(result["erc"][0]["report"]).is_file())

    def test_empty_library_is_valid(self):
        report = validate_block_library(self.root, self.reports, run_erc=False)
        self.assertEqual(report["status"], "passed")
        self.assertEqual(report["block_count"], 0)
        self.assertEqual(report["failed_count"], 0)


if __name__ == "__main__":
    unittest.main()
