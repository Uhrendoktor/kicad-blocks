#!/usr/bin/env python3
"""Publish the tested KiCad Python development wheel to a stable GitHub Release asset."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

RELEASE_TAG = "kicad-python-dev"
ASSET_PREFIX = "kicad_python-0.9.0.dev0-"


def _api_request(
    method: str,
    url: str,
    token: str,
    *,
    payload: dict | None = None,
    body: bytes | None = None,
    content_type: str = "application/vnd.github+json",
    allow_not_found: bool = False,
) -> dict | None:
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    data = body
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    elif body is not None:
        headers["Content-Type"] = content_type

    request = Request(url, data=data, headers=headers, method=method)
    try:
        with urlopen(request, timeout=90) as response:
            raw = response.read()
    except HTTPError as exc:
        details = exc.read().decode("utf-8", errors="replace")
        if allow_not_found and exc.code == 404:
            return None
        raise RuntimeError(
            f"GitHub API {method} {url} failed with HTTP {exc.code}: {details}"
        ) from exc
    if not raw:
        return {}
    try:
        return json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"GitHub API returned non-JSON data for {url}.") from exc


def _get_or_create_release(repo: str, token: str) -> dict:
    api = os.environ.get("GITHUB_API_URL", "https://api.github.com").rstrip("/")
    release_url = (
        f"{api}/repos/{repo}/releases/tags/{quote(RELEASE_TAG, safe='')}"
    )
    release = _api_request("GET", release_url, token, allow_not_found=True)
    if release is not None:
        return release

    payload = {
        "tag_name": RELEASE_TAG,
        "target_commitish": "main",
        "name": "KiCad Python IPC wheel (nightly)",
        "body": (
            "Prebuilt KiCad Python schematic/PCB IPC bindings for the tested "
            "Linux x86_64, CPython 3.12 KiCad nightly environment. The asset is "
            "refreshed by successful main-branch self-hosted CI runs."
        ),
        "draft": False,
        "prerelease": True,
    }
    return _api_request(
        "POST",
        f"{api}/repos/{repo}/releases",
        token,
        payload=payload,
    )


def publish_wheel(wheelhouse: Path, repo: str, token: str) -> str:
    wheelhouse = Path(wheelhouse).expanduser().resolve()
    wheels = sorted(
        path for path in wheelhouse.glob(f"{ASSET_PREFIX}*.whl") if path.is_file()
    )
    if len(wheels) != 1:
        raise FileNotFoundError(
            f"Expected exactly one {ASSET_PREFIX}*.whl in {wheelhouse}; "
            f"found {[path.name for path in wheels]!r}."
        )
    wheel = wheels[0]
    if "cp312-cp312-linux_x86_64" not in wheel.name:
        raise ValueError(
            f"Unsupported KiCad IPC wheel target {wheel.name!r}; expected "
            "the currently supported cp312 Linux x86_64 wheel."
        )

    api = os.environ.get("GITHUB_API_URL", "https://api.github.com").rstrip("/")
    release = _get_or_create_release(repo, token)
    release_id = release.get("id")
    if not release_id:
        raise RuntimeError("GitHub did not return a release ID.")

    for asset in release.get("assets", []):
        if asset.get("name") == wheel.name:
            _api_request(
                "DELETE",
                f"{api}/repos/{repo}/releases/assets/{asset['id']}",
                token,
            )

    upload_url = release.get("upload_url", "").split("{", 1)[0]
    if not upload_url:
        raise RuntimeError("GitHub release response is missing upload_url.")
    url = f"{upload_url}?name={quote(wheel.name, safe='')}"
    uploaded = _api_request(
        "POST",
        url,
        token,
        body=wheel.read_bytes(),
        content_type="application/octet-stream",
    )
    if uploaded.get("name") != wheel.name or uploaded.get("size") != wheel.stat().st_size:
        raise RuntimeError(f"GitHub release asset verification failed: {uploaded!r}.")
    download_url = uploaded.get("browser_download_url")
    if not download_url:
        raise RuntimeError("GitHub did not return a release asset download URL.")
    print(f"Published {wheel.name} ({wheel.stat().st_size} bytes) to {download_url}")
    return download_url


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wheelhouse", type=Path)
    parser.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY"))
    args = parser.parse_args()
    token = os.environ.get("GITHUB_TOKEN")
    if not args.repo:
        parser.error("--repo or GITHUB_REPOSITORY is required.")
    if not token:
        parser.error("GITHUB_TOKEN is required to publish the release asset.")
    publish_wheel(args.wheelhouse, args.repo, token)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
