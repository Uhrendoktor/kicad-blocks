"""Tests for immutable, revision-addressed KiCad IPC wheel publication."""

from __future__ import annotations

import hashlib
import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "publish_pinned_ipc_wheel.py"
SPEC = importlib.util.spec_from_file_location("publish_pinned_ipc_wheel", SCRIPT)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("Could not load the pinned wheel publisher.")
publisher = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(publisher)

SHA = "76b47d25a826a56a301660f5f572dda968c84d59"
WHEEL_NAME = "kicad_python-0.9.0.dev0-cp312-cp312-linux_x86_64.whl"


class PinnedWheelPublisherTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.wheelhouse = Path(self.temp.name)
        self.wheel = self.wheelhouse / WHEEL_NAME
        self.wheel.write_bytes(b"verified-wheel-bytes")
        self.digest = "sha256:" + hashlib.sha256(self.wheel.read_bytes()).hexdigest()
        self.release = {
            "id": 18,
            "tag_name": "kicad-python-76b47d25",
            "upload_url": "https://uploads.github.com/repos/owner/repo/releases/18/assets{?name,label}",
            "assets": [],
        }

    def tearDown(self):
        self.temp.cleanup()

    def test_existing_identical_asset_is_idempotent_and_never_uploaded_again(self):
        asset = {
            "name": WHEEL_NAME,
            "size": self.wheel.stat().st_size,
            "digest": self.digest,
            "browser_download_url": "https://example.invalid/pinned.whl",
        }
        with patch.object(
            publisher, "_request", return_value={**self.release, "assets": [asset]}
        ) as request:
            url = publisher.publish_pinned_wheel(self.wheelhouse, "owner/repo", SHA, "token")
        self.assertEqual(url, asset["browser_download_url"])
        self.assertEqual(request.call_count, 2)
        self.assertEqual(request.call_args_list[0].args[0], "GET")
        self.assertEqual(request.call_args_list[1].args[0], "PATCH")
        self.assertIn("CPython 3.12, 3.13 and 3.14", request.call_args_list[1].kwargs["payload"]["body"])

    def test_existing_asset_with_different_digest_is_not_overwritten(self):
        asset = {
            "name": WHEEL_NAME,
            "size": self.wheel.stat().st_size,
            "digest": "sha256:" + "0" * 64,
            "browser_download_url": "https://example.invalid/wrong.whl",
        }
        with patch.object(
            publisher, "_request", return_value={**self.release, "assets": [asset]}
        ), self.assertRaisesRegex(RuntimeError, "refusing to replace"):
            publisher.publish_pinned_wheel(self.wheelhouse, "owner/repo", SHA, "token")

    def test_new_asset_upload_is_verified_against_the_release_digest(self):
        asset = {
            "name": WHEEL_NAME,
            "size": self.wheel.stat().st_size,
            "digest": self.digest,
            "browser_download_url": "https://example.invalid/pinned.whl",
        }
        calls = []

        def fake_request(method, url, _token, **_kwargs):
            calls.append((method, url))
            if method == "GET" and url.endswith("/releases/tags/kicad-python-76b47d25"):
                return dict(self.release)
            if method == "PATCH" and url.endswith("/releases/18"):
                return {**self.release, "body": _kwargs["payload"]["body"]}
            if method == "POST" and url.startswith("https://uploads.github.com/"):
                return {}
            if method == "GET" and url.endswith("/releases/18"):
                return {**self.release, "assets": [asset]}
            raise AssertionError((method, url))

        with patch.object(publisher, "_request", side_effect=fake_request):
            url = publisher.publish_pinned_wheel(self.wheelhouse, "owner/repo", SHA, "token")
        self.assertEqual(url, asset["browser_download_url"])
        self.assertEqual([method for method, _ in calls], ["GET", "PATCH", "POST", "GET"])

    def test_publisher_selects_the_matching_tag_for_each_supported_python(self):
        for minor in (12, 13, 14):
            with self.subTest(minor=minor):
                self.assertEqual(
                    publisher.expected_wheel_abi_tag(
                        implementation="cpython",
                        version_info=(3, minor, 4),
                    ),
                    f"cp3{minor}-cp3{minor}-linux_x86_64",
                )
        with self.assertRaisesRegex(ValueError, "CPython 3.12, 3.13 or 3.14"):
            publisher.expected_wheel_abi_tag(
                implementation="cpython",
                version_info=(3, 15, 0),
            )

    def test_invalid_source_sha_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "40-character"):
            publisher.publish_pinned_wheel(self.wheelhouse, "owner/repo", "latest", "token")


if __name__ == "__main__":
    unittest.main()
