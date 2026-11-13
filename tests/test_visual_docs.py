"""Unit tests for the visual-documentation publisher's provenance gate."""

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from scripts.publish_visual_docs import ASSET_NAMES, verify_bundle


class VerifyVisualBundleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="kicad-visual-bundle-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source_sha = "a" * 40
        assets = {}
        for name in sorted(ASSET_NAMES):
            content = f"generated-fixture:{name}".encode()
            (self.root / name).write_bytes(content)
            assets[name] = hashlib.sha256(content).hexdigest()
        (self.root / "manifest.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "source_sha": self.source_sha,
                    "assets": assets,
                }
            ),
            encoding="utf-8",
        )

    def test_accepts_exact_inventory_with_matching_source_and_hashes(self) -> None:
        manifest = verify_bundle(self.root, self.source_sha)
        self.assertEqual(set(manifest["assets"]), ASSET_NAMES)

    def test_inventory_covers_all_primary_documented_ui_surfaces(self) -> None:
        expected = {
            "task-hub-light.png",
            "task-hub-edit-maintain-light.png",
            "task-hub-checks-pcb-light.png",
            "instance-configuration-light.png",
            "library-browser-light.png",
            "library-folders-light.png",
            "implementation-variants-light.png",
            "create-block-form-light.png",
        }
        self.assertTrue(expected.issubset(ASSET_NAMES))
        self.assertEqual(set(ASSET_NAMES), set(sorted(ASSET_NAMES)))

    def test_rejects_stale_source_commit(self) -> None:
        with self.assertRaisesRegex(ValueError, "not checked-out source"):
            verify_bundle(self.root, "b" * 40)

    def test_rejects_modified_generated_asset(self) -> None:
        (self.root / "block-picker-workflow.gif").write_bytes(b"tampered")
        with self.assertRaisesRegex(ValueError, "hash mismatch"):
            verify_bundle(self.root, self.source_sha)

    def test_rejects_incomplete_asset_inventory(self) -> None:
        manifest_path = self.root / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["assets"].pop("functional-block-facade.svg")
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "inventory mismatch"):
            verify_bundle(self.root, self.source_sha)


if __name__ == "__main__":
    unittest.main()
