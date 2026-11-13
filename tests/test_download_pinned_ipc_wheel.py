"""Tests for immutable ABI-specific KiCad IPC wheel reuse."""

from __future__ import annotations

import hashlib
import io
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from scripts import download_pinned_ipc_wheel as downloader


class DownloadPinnedWheelTests(unittest.TestCase):
    def setUp(self) -> None:
        self.source_sha = "76b47d25a826a56a301660f5f572dda968c84d59"
        self.wheel_name = "kicad_python-0.9.0.dev0-cp312-cp312-linux_x86_64.whl"
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("kicad_python-0.9.0.dev0.dist-info/WHEEL", "Wheel-Version: 1.0\n")
        self.payload = buffer.getvalue()
        self.digest = f"sha256:{hashlib.sha256(self.payload).hexdigest()}"
        self.release = {
            "tag_name": "kicad-python-76b47d25",
            "assets": [{
                "name": self.wheel_name,
                "state": "uploaded",
                "browser_download_url": "https://example.invalid/wheel.whl",
                "digest": self.digest,
                "size": len(self.payload),
            }],
        }

    def test_reuses_exact_abi_wheel_after_digest_and_archive_validation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with (
                patch.object(downloader, "expected_wheel_name", return_value=self.wheel_name),
                patch.object(downloader, "_api_get_release", return_value=self.release),
                patch.object(downloader, "_download_asset", return_value=self.payload),
            ):
                path = downloader.download_pinned_wheel(
                    Path(temporary), "Uhrendoktor/kicad-blocks", self.source_sha, "token"
                )
            self.assertIsNotNone(path)
            if path is None:
                self.fail("Validated cached wheel path was unexpectedly None.")
            self.assertEqual(path.name, self.wheel_name)
            self.assertEqual(path.read_bytes(), self.payload)

    def test_returns_none_when_release_or_abi_asset_is_missing(self) -> None:
        with (
            patch.object(downloader, "expected_wheel_name", return_value=self.wheel_name),
            patch.object(downloader, "_api_get_release", return_value=None),
        ):
            self.assertIsNone(
                downloader.download_pinned_wheel(
                    Path(tempfile.gettempdir()), "Uhrendoktor/kicad-blocks", self.source_sha, "token"
                )
            )
        release_without_asset = {**self.release, "assets": []}
        with (
            patch.object(downloader, "expected_wheel_name", return_value=self.wheel_name),
            patch.object(downloader, "_api_get_release", return_value=release_without_asset),
        ):
            self.assertIsNone(
                downloader.download_pinned_wheel(
                    Path(tempfile.gettempdir()), "Uhrendoktor/kicad-blocks", self.source_sha, "token"
                )
            )

    def test_rejects_corrupt_or_changed_asset_bytes(self) -> None:
        with (
            tempfile.TemporaryDirectory() as temporary,
            patch.object(downloader, "expected_wheel_name", return_value=self.wheel_name),
            patch.object(downloader, "_api_get_release", return_value=self.release),
            patch.object(downloader, "_download_asset", return_value=b"wrong bytes"),
            self.assertRaisesRegex(RuntimeError, "digest mismatch"),
        ):
            downloader.download_pinned_wheel(
                Path(temporary), "Uhrendoktor/kicad-blocks", self.source_sha, "token"
            )

    def test_rejects_wrong_source_sha(self) -> None:
        with self.assertRaisesRegex(ValueError, "40-character SHA"):
            downloader.download_pinned_wheel(
                Path(tempfile.gettempdir()), "Uhrendoktor/kicad-blocks", "not-a-sha", "token"
            )


if __name__ == "__main__":
    unittest.main()
