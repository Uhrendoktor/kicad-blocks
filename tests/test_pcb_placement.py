"""Tests for persistent KiCad PCB imported-block placement and native move dispatch."""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugin" / "kicad-blocks"
sys.path.insert(0, str(PLUGIN))

from kicad_blocks.pcb_placement import (
    move_placement_at_cursor,
    placement_manifest_path,
    read_pcb_placement_manifest,
    resolve_placement_item_ids,
    write_pcb_placement_manifest,
)


class FakeBoard:
    def __init__(self, values):
        self.items = [SimpleNamespace(id=SimpleNamespace(value=value)) for value in values]
        self.moved = None

    def get_items(self):
        return self.items

    def interactive_move(self, item_ids):
        self.moved = list(item_ids)


class PcbPlacementManifestTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.board_path = Path(self.temp.name) / "output.kicad_pcb"
        self.board_path.write_text("(kicad_pcb)\n", encoding="utf-8")
        self.first = "00000000-0000-0000-0000-000000000101"
        self.second = "00000000-0000-0000-0000-000000000102"

    def tearDown(self):
        self.temp.cleanup()

    def test_single_merge_manifest_round_trip_and_native_move_uses_only_recorded_items(self):
        result = {
            "instance_reference": "X1",
            "block": "Power Stage",
            "layout_name": "Compact",
            "interactive_item_uuids": [self.first, self.second],
        }
        sidecar = write_pcb_placement_manifest(self.board_path, result)
        self.assertEqual(sidecar, placement_manifest_path(self.board_path))
        payload = read_pcb_placement_manifest(self.board_path)
        self.assertEqual(payload["placements"][0]["instance_reference"], "X1")
        self.assertEqual(payload["placements"][0]["layout_name"], "Compact")

        unrelated = "00000000-0000-0000-0000-000000000199"
        board = FakeBoard([self.first, unrelated, self.second])
        moved = move_placement_at_cursor(board, payload["placements"][0])
        self.assertEqual(moved, 2)
        self.assertEqual([item.value for item in board.moved], [self.first, self.second])

    def test_pcb_editor_entrypoint_uses_active_board_file_and_native_move(self):
        result = {
            "instance_reference": "X1",
            "block": "Power Stage",
            "layout_name": "Compact",
            "interactive_item_uuids": [self.first, self.second],
        }
        write_pcb_placement_manifest(self.board_path, result)
        board = FakeBoard([self.first, self.second])
        board.document = SimpleNamespace(
            board_filename=self.board_path.name,
            project=SimpleNamespace(path=str(self.board_path.parent)),
        )

        class FakeKiCad:
            def get_board(self):
                return board

        kipy = ModuleType("kipy")
        kipy.KiCad = FakeKiCad
        entrypoint = PLUGIN / "place_imported_layout.py"
        spec = importlib.util.spec_from_file_location("kblocks_pcb_action_test", entrypoint)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"Could not load PCB action entrypoint: {entrypoint}")
        action = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(action)

        with patch.dict(sys.modules, {"kipy": kipy}):
            action.main()

        self.assertEqual(
            [item.value for item in board.moved],
            [self.first, self.second],
        )

    def test_batch_merge_keeps_instance_groups_separate(self):
        result = {
            "placements": [
                {
                    "instance_reference": "X1",
                    "block": "Power Stage",
                    "layout_name": "Standard",
                    "interactive_item_uuids": [self.first],
                },
                {
                    "instance_reference": "X2",
                    "block": "Power Stage",
                    "layout_name": "Compact",
                    "interactive_item_uuids": [self.second],
                },
            ]
        }
        write_pcb_placement_manifest(self.board_path, result)
        placements = read_pcb_placement_manifest(self.board_path)["placements"]
        board = FakeBoard([self.first, self.second])
        first_ids = resolve_placement_item_ids(board, placements[0])
        second_ids = resolve_placement_item_ids(board, placements[1])
        self.assertEqual([item.value for item in first_ids], [self.first])
        self.assertEqual([item.value for item in second_ids], [self.second])

    def test_manifest_cannot_be_overwritten(self):
        result = {
            "instance_reference": "X1",
            "interactive_item_uuids": [self.first],
        }
        write_pcb_placement_manifest(self.board_path, result)
        sidecar = placement_manifest_path(self.board_path)
        original = sidecar.read_bytes()
        with self.assertRaises(FileExistsError):
            write_pcb_placement_manifest(self.board_path, result)
        self.assertEqual(sidecar.read_bytes(), original)

    def test_manifest_rejects_different_active_board_path(self):
        result = {"instance_reference": "X1", "interactive_item_uuids": [self.first]}
        write_pcb_placement_manifest(self.board_path, result)
        other = Path(self.temp.name) / "other.kicad_pcb"
        other.write_text("(kicad_pcb)\n", encoding="utf-8")
        wrong_board_sidecar = placement_manifest_path(other)
        wrong_board_sidecar.write_bytes(
            placement_manifest_path(self.board_path).read_bytes()
        )
        with self.assertRaisesRegex(ValueError, "different board path"):
            read_pcb_placement_manifest(other)

    def test_manifest_rejects_unknown_schema_and_duplicate_item_ids(self):
        result = {"instance_reference": "X1", "interactive_item_uuids": [self.first]}
        sidecar = write_pcb_placement_manifest(self.board_path, result)
        payload = json.loads(sidecar.read_text(encoding="utf-8"))
        payload["schema_version"] = 999
        sidecar.write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Unsupported.*schema"):
            read_pcb_placement_manifest(self.board_path)

        payload["schema_version"] = 1
        payload["placements"][0]["item_uuids"] = [self.first, self.first.upper()]
        sidecar.write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "duplicated"):
            read_pcb_placement_manifest(self.board_path)

    def test_missing_board_item_refuses_partial_move(self):
        board = FakeBoard([self.first])
        placement = {"item_uuids": [self.first, self.second]}
        with self.assertRaisesRegex(ValueError, "could not all be found"):
            resolve_placement_item_ids(board, placement)
        self.assertIsNone(board.moved)


if __name__ == "__main__":
    unittest.main()
