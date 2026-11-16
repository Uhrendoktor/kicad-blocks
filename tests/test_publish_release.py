"""Tests for checksum-verified versioned release publication."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "publish_release.py"
SPEC = importlib.util.spec_from_file_location("publish_release", SCRIPT)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("Could not load the release publisher.")
publisher = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(publisher)


class _Response:
    def __init__(self, payload):
        self.payload = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return self.payload


class PublishReleaseTests(unittest.TestCase):
    def test_release_assets_are_uploaded_and_verified_by_digest(self):
        with tempfile.TemporaryDirectory() as temp:
            archive = Path(temp) / "kicad-blocks-1.1.0-linux-x86_64.zip"
            checksum = Path(temp) / (archive.name + ".sha256")
            archive.write_bytes(b"archive payload")
            checksum.write_text(
                hashlib.sha256(archive.read_bytes()).hexdigest() + "  " + archive.name + "\n",
                encoding="ascii",
            )
            assets = []
            calls = []
            release = {
                "id": 99,
                "tag_name": "v1.1.0",
                "upload_url": "https://uploads.github.com/repos/owner/repo/releases/99/assets{?name,label}",
                "assets": [],
            }

            def fake_request(method, url, _token, **kwargs):
                calls.append((method, url))
                if method == "GET" and url.endswith("/releases/tags/v1.1.0"):
                    return None
                if method == "POST" and url.endswith("/releases"):
                    return dict(release)
                if method == "POST" and url.startswith("https://uploads.github.com/"):
                    name = url.split("name=", 1)[1]
                    payload = kwargs["body"]
                    from urllib.parse import unquote

                    name = unquote(name)
                    assets.append(
                        {
                            "name": name,
                            "size": len(payload),
                            "digest": "sha256:" + hashlib.sha256(payload).hexdigest(),
                        }
                    )
                    return {}
                if method == "GET" and url.endswith("/releases/99"):
                    return {**release, "assets": list(assets)}
                raise AssertionError((method, url))

            with patch.object(publisher, "_request", side_effect=fake_request):
                result = publisher.publish_release(
                    [archive, checksum],
                    "owner/repo",
                    "v1.1.0",
                    "1.1.0",
                    "e" * 40,
                    "# Changelog",
                    "token",
                )
            self.assertEqual(result, "https://github.com/owner/repo/releases/tag/v1.1.0")
            self.assertEqual(len(assets), 2)
            self.assertEqual(
                [method for method, _ in calls], ["GET", "POST", "POST", "POST", "GET"]
            )

    def test_existing_asset_with_changed_digest_is_never_replaced(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "release.zip"
            path.write_bytes(b"new bytes")
            release = {
                "id": 10,
                "tag_name": "v1.1.0",
                "upload_url": "https://uploads.github.com/repos/owner/repo/releases/10/assets{?name,label}",
                "assets": [
                    {
                        "name": "release.zip",
                        "size": path.stat().st_size,
                        "digest": "sha256:" + "0" * 64,
                    }
                ],
            }
            with (
                patch.object(publisher, "_request", return_value=release),
                self.assertRaisesRegex(RuntimeError, "refusing overwrite"),
            ):
                publisher.publish_release(
                    [path], "owner/repo", "v1.1.0", "1.1.0", "f" * 40, "# Changelog", "token"
                )


if __name__ == "__main__":
    unittest.main()
