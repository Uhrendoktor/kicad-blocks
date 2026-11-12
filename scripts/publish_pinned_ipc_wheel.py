#!/usr/bin/env python3
"""Publish one immutable, revision-addressed KiCad Python IPC wheel asset."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

WHEEL_PREFIX = "kicad_python-0.9.0.dev0-"
API_VERSION = "2022-11-28"
SUPPORTED_PYTHON_MINOR_VERSIONS = frozenset({12, 13, 14})


def expected_wheel_abi_tag(
    implementation: str | None = None,
    version_info=None,
) -> str:
    """Return the wheel ABI/platform suffix for the running supported CPython."""
    selected_implementation = implementation or sys.implementation.name
    selected_version = version_info or sys.version_info
    major, minor = int(selected_version[0]), int(selected_version[1])
    if (
        selected_implementation.casefold() != "cpython"
        or major != 3
        or minor not in SUPPORTED_PYTHON_MINOR_VERSIONS
    ):
        raise ValueError(
            "Pinned KiCad IPC wheels can only be published using CPython 3.12, "
            "3.13 or 3.14."
        )
    abi = f"cp{major}{minor}"
    return f"{abi}-{abi}-linux_x86_64"


def _request(
    method: str,
    url: str,
    token: str,
    *,
    payload: dict | None = None,
    body: bytes | None = None,
    content_type: str = "application/vnd.github+json",
) -> dict | None:
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": API_VERSION,
    }
    data = body
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    elif body is not None:
        headers["Content-Type"] = content_type
    try:
        with urlopen(Request(url, data=data, headers=headers, method=method), timeout=60) as response:
            raw = response.read()
    except HTTPError as exc:
        if exc.code == 404 and method == "GET":
            return None
        raise RuntimeError(
            f"GitHub API {method} {url} failed with HTTP {exc.code}: "
            f"{exc.read().decode('utf-8', errors='replace')}"
        ) from exc
    return json.loads(raw.decode("utf-8")) if raw else {}


def _api_root() -> str:
    return os.environ.get("GITHUB_API_URL", "https://api.github.com").rstrip("/")


def _get_or_create_release(repo: str, tag: str, source_sha: str, token: str) -> dict:
    root = _api_root()
    url = f"{root}/repos/{repo}/releases/tags/{quote(tag, safe='')}"
    release = _request("GET", url, token)
    if release is not None:
        if release.get("tag_name") != tag:
            raise RuntimeError(f"GitHub returned the wrong release for tag {tag!r}.")
        target_body = (
            "Immutable dependency assets for KiCad Blocks releases."
            + chr(10) * 2
            + f"Built from official kicad-python commit {source_sha}. "
            + "Targets: CPython 3.12, 3.13 and 3.14, Linux x86_64."
        )
        if release.get("body") != target_body:
            release = _request(
                "PATCH",
                f"{root}/repos/{repo}/releases/{release['id']}",
                token,
                payload={"body": target_body},
            )
        return release

    payload = {
        "tag_name": tag,
        "target_commitish": "main",
        "name": f"KiCad Python IPC bindings ({source_sha[:8]})",
        "body": (
            "Immutable dependency assets for KiCad Blocks releases.\n\n"
            f"Built from official kicad-python commit {source_sha}. "
            "Targets: CPython 3.12, 3.13 and 3.14, Linux x86_64."
        ),
        "draft": False,
        "prerelease": False,
    }
    try:
        created = _request("POST", f"{root}/repos/{repo}/releases", token, payload=payload)
    except RuntimeError:
        # A concurrent CI run may have created the same release. Only reuse it
        # after reading the tag back from GitHub.
        created = _request("GET", url, token)
        if created is None:
            raise
    if created is None or not created.get("id"):
        raise RuntimeError(f"Could not create or retrieve pinned wheel release {tag!r}.")
    return created


def publish_pinned_wheel(wheelhouse: Path, repo: str, source_sha: str, token: str) -> str:
    if not re.fullmatch(r"[0-9a-f]{40}", source_sha):
        raise ValueError("The pinned kicad-python source revision must be a 40-character SHA.")
    wheelhouse = Path(wheelhouse).expanduser().resolve()
    wheels = sorted(
        item for item in wheelhouse.glob(f"{WHEEL_PREFIX}*.whl") if item.is_file()
    )
    if len(wheels) != 1:
        raise FileNotFoundError(
            f"Expected one {WHEEL_PREFIX}*.whl in {wheelhouse}, got {[p.name for p in wheels]!r}."
        )
    wheel = wheels[0]
    expected_abi = expected_wheel_abi_tag()
    if not wheel.name.endswith(f"-{expected_abi}.whl"):
        raise ValueError(
            f"Unsupported pinned wheel target: {wheel.name!r}; "
            f"the publishing interpreter requires *-{expected_abi}.whl."
        )
    digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
    expected_digest = f"sha256:{digest}"
    tag = f"kicad-python-{source_sha[:8]}"
    release = _get_or_create_release(repo, tag, source_sha, token)
    release_id = release.get("id")
    if not release_id:
        raise RuntimeError("The pinned wheel release has no release ID.")

    existing = next(
        (asset for asset in release.get("assets", []) if asset.get("name") == wheel.name),
        None,
    )
    if existing is not None:
        if existing.get("digest") != expected_digest or existing.get("size") != wheel.stat().st_size:
            raise RuntimeError(
                "Pinned dependency asset already exists with different bytes; refusing to replace "
                f"the immutable release asset. Expected {expected_digest}, "
                f"found {existing.get('digest')!r}."
            )
        return str(existing["browser_download_url"])

    upload_url = release.get("upload_url", "").split("{", 1)[0]
    if not upload_url:
        raise RuntimeError("Pinned wheel release response is missing upload_url.")
    _request(
        "POST",
        f"{upload_url}?name={quote(wheel.name, safe='')}",
        token,
        body=wheel.read_bytes(),
        content_type="application/octet-stream",
    )

    refreshed = _request(
        "GET",
        f"{_api_root()}/repos/{repo}/releases/{release_id}",
        token,
    )
    if refreshed is None:
        raise RuntimeError("Pinned wheel release disappeared after upload.")
    uploaded = next(
        (asset for asset in refreshed.get("assets", []) if asset.get("name") == wheel.name),
        None,
    )
    if (
        uploaded is None
        or uploaded.get("digest") != expected_digest
        or uploaded.get("size") != wheel.stat().st_size
        or not uploaded.get("browser_download_url")
    ):
        raise RuntimeError(
            f"Pinned wheel upload could not be verified against {expected_digest}: {uploaded!r}"
        )
    print(f"Verified immutable dependency asset: {uploaded['browser_download_url']} ({expected_digest})")
    return str(uploaded["browser_download_url"])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wheelhouse", type=Path)
    parser.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY"))
    parser.add_argument("--source-sha", required=True)
    args = parser.parse_args()
    token = os.environ.get("GITHUB_TOKEN")
    if not args.repo:
        parser.error("--repo or GITHUB_REPOSITORY is required.")
    if not token:
        parser.error("GITHUB_TOKEN is required.")
    publish_pinned_wheel(args.wheelhouse, args.repo, args.source_sha, token)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
