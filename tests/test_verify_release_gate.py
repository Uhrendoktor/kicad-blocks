"""Tests for the exact-main-CI release gate."""

from __future__ import annotations

import importlib.util
import json
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "verify_release_gate.py"
SPEC = importlib.util.spec_from_file_location("verify_release_gate", SCRIPT)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("Could not load the release gate.")
gate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gate)


class _Response:
    def __init__(self, payload: dict):
        self.payload = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return self.payload


class VerifyReleaseGateTests(unittest.TestCase):
    def test_accepts_only_successful_main_push_ci_for_exact_sha(self):
        sha = "a" * 40
        run = {
            "id": 123,
            "html_url": "https://github.com/owner/repo/actions/runs/123",
            "head_sha": sha,
            "head_branch": "main",
            "event": "push",
            "path": ".github/workflows/ci.yml",
            "status": "completed",
            "conclusion": "success",
        }
        payload = {"workflow_runs": [
            {**run, "conclusion": "failure"},
            {**run, "head_branch": "feature"},
            run,
        ]}
        with patch.object(gate, "urlopen", return_value=_Response(payload)):
            result = gate.verify_release_gate(
                "owner/repo", "v1.1.0",
                Path(__file__).resolve().parents[1] / "VERSION",
                sha, "token"
            )
        self.assertEqual(result["id"], 123)

    def test_refuses_failed_or_wrong_commit_ci(self):
        sha = "b" * 40
        payload = {"workflow_runs": [{
            "id": 124,
            "head_sha": "c" * 40,
            "head_branch": "main",
            "event": "push",
            "path": ".github/workflows/ci.yml",
            "status": "completed",
            "conclusion": "success",
        }]}
        with (
            patch.object(gate, "urlopen", return_value=_Response(payload)),
            patch.object(gate.time, "sleep"),
            self.assertRaisesRegex(RuntimeError, "No successful main push CI"),
        ):
            gate.verify_release_gate(
                "owner/repo", "v1.1.0",
                Path(__file__).resolve().parents[1] / "VERSION",
                sha, "token"
            )

    def test_rejects_tag_that_does_not_match_version(self):
        with self.assertRaisesRegex(ValueError, "does not match"):
            gate.verify_release_gate(
                "owner/repo", "v1.2.0",
                Path(__file__).resolve().parents[1] / "VERSION",
                "d" * 40, "token"
            )

    def test_release_workflow_requires_the_tag_commit_to_match_the_packaged_commit(self):
        workflow = (
            Path(__file__).resolve().parents[1]
            / ".github"
            / "workflows"
            / "release.yml"
        ).read_text(encoding="utf-8")
        self.assertIn(
            'git fetch --no-tags origin "refs/tags/$RELEASE_TAG:refs/tags/$RELEASE_TAG"',
            workflow,
        )
        self.assertIn(
            'TAG_TARGET_SHA="$(git rev-parse "$RELEASE_TAG^{commit}")"',
            workflow,
        )
        self.assertIn(
            'if [[ "$TAG_TARGET_SHA" != "$RELEASE_SOURCE_SHA" ]]; then',
            workflow,
        )


if __name__ == "__main__":
    unittest.main()
