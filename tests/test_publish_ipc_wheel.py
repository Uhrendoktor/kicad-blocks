"""Network-failure handling tests for the KiCad IPC wheel release publisher."""

from __future__ import annotations

import importlib.util
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "publish_ipc_wheel.py"
SPEC = importlib.util.spec_from_file_location("publish_ipc_wheel", SCRIPT)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("Could not load the wheel publisher module.")
publisher = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(publisher)


class _Response:
    def __init__(self, payload: bytes):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return self.payload


class PublishIpcWheelTests(unittest.TestCase):
    def test_api_request_retries_tls_timeout_then_succeeds(self):
        with (
            patch.object(
                publisher,
                "urlopen",
                side_effect=[
                    TimeoutError("TLS handshake timed out"),
                    _Response(b'{"ok":true}'),
                ],
            ) as urlopen,
            patch.object(publisher.time, "sleep") as sleep,
        ):
            result = publisher._api_request(
                "GET", "https://api.github.com/test", "token", timeout=1, max_retries=2
            )

        self.assertEqual(result, {"ok": True})
        self.assertEqual(urlopen.call_count, 2)
        sleep.assert_called_once_with(2.0)

    def test_api_request_does_not_retry_permanent_http_error(self):
        error = HTTPError(
            "https://api.github.com/test",
            403,
            "Forbidden",
            {},
            io.BytesIO(b'{"message":"forbidden"}'),
        )
        with (
            patch.object(publisher, "urlopen", side_effect=error) as urlopen,
            patch.object(publisher.time, "sleep") as sleep,
            self.assertRaisesRegex(RuntimeError, "HTTP 403"),
        ):
            publisher._api_request("GET", "https://api.github.com/test", "token")

        urlopen.assert_called_once()
        sleep.assert_not_called()

    def test_publish_recovers_when_upload_succeeded_but_response_was_lost(self):
        with tempfile.TemporaryDirectory() as temp:
            wheel = Path(temp) / (
                "kicad_python-0.9.0.dev0-cp312-cp312-linux_x86_64.whl"
            )
            wheel.write_bytes(b"wheel-bytes")
            release_url = (
                "https://api.github.com/repos/owner/repo/releases/tags/kicad-python-dev"
            )
            upload_url = "https://uploads.github.com/repos/owner/repo/releases/1/assets"
            asset = {
                "name": wheel.name,
                "size": wheel.stat().st_size,
                "state": "uploaded",
                "browser_download_url": "https://example.invalid/" + wheel.name,
            }
            release = {
                "id": 1,
                "upload_url": upload_url + "{?name,label}",
                "assets": [],
            }
            calls = []

            def fake_request(method, url, _token, **_kwargs):
                calls.append((method, url))
                if method == "GET" and url == release_url:
                    return {**release, "assets": [asset] if len(calls) > 1 else []}
                if method == "POST" and url.startswith(upload_url):
                    raise RuntimeError(
                        "upload response timed out after server accepted bytes"
                    )
                return {}

            with (
                patch.object(publisher, "_api_request", side_effect=fake_request),
                patch.dict(
                    publisher.os.environ, {"GITHUB_API_URL": "https://api.github.com"}
                ),
            ):
                result = publisher.publish_wheel(Path(temp), "owner/repo", "token")

        self.assertEqual(result, asset["browser_download_url"])
        self.assertEqual(
            [method for method, _url in calls],
            ["GET", "POST", "GET"],
        )


if __name__ == "__main__":
    unittest.main()
