"""Tests for idempotent dispatch of the V1.1 release workflow."""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "dispatch_release.py"
SPEC = importlib.util.spec_from_file_location("dispatch_release", SCRIPT)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("Could not load the release dispatcher.")
dispatcher = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(dispatcher)

VERSION = "1.1.0"
TAG = "v1.1.0"
SHA = "a" * 40
ARCHIVE = "kicad-blocks-1.1.0-linux-x86_64-cp312.zip"


class DispatchReleaseTests(unittest.TestCase):
    def test_dispatches_release_workflow_when_release_is_not_published(self):
        calls = []

        def fake_request(method, url, _token, payload=None):
            calls.append((method, url, payload))
            if method == "GET":
                return None
            return {}

        with patch.object(dispatcher, "_request", side_effect=fake_request):
            result = dispatcher.dispatch_release("owner/repo", TAG, SHA, VERSION, "token")
        self.assertTrue(result)
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[1][2], {
            "ref": "main",
            "inputs": {"tag": TAG, "source_sha": SHA},
        })

    def test_does_not_dispatch_when_both_release_assets_exist(self):
        release = {
            "tag_name": TAG,
            "draft": False,
            "prerelease": False,
            "assets": [
                {"name": ARCHIVE, "size": 123, "digest": "sha256:" + "1" * 64},
                {"name": ARCHIVE + ".sha256", "size": 88, "digest": "sha256:" + "2" * 64},
            ],
        }
        with patch.object(dispatcher, "_request", return_value=release) as request:
            result = dispatcher.dispatch_release("owner/repo", TAG, SHA, VERSION, "token")
        self.assertFalse(result)
        request.assert_called_once()

    def test_rejects_tag_and_source_sha_mismatches(self):
        with self.assertRaisesRegex(ValueError, "does not match"):
            dispatcher.dispatch_release("owner/repo", "v1.2.0", SHA, VERSION, "token")
        with self.assertRaisesRegex(ValueError, "40 lowercase"):
            dispatcher.dispatch_release("owner/repo", TAG, "latest", VERSION, "token")


if __name__ == "__main__":
    unittest.main()
