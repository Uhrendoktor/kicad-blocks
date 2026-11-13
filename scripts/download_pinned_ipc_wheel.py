#!/usr/bin/env python3
"""Download the immutable KiCad IPC wheel for this pinned source and CPython ABI."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import re
import sys
import tempfile
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen
import zipfile

API_VERSION = "2022-11-28"
SUPPORTED_PYTHON_MINOR_VERSIONS = frozenset({12, 13, 14})
WHEEL_PREFIX = "kicad_python-0.9.0.dev0-"


def expected_wheel_name() -> str:
    implementation = sys.implementation.name.casefold()
    major, minor = sys.version_info.major, sys.version_info.minor
    if (
        implementation != "cpython"
        or major != 3
        or minor not in SUPPORTED_PYTHON_MINOR_VERSIONS
    ):
        raise ValueError(
            "Pinned KiCad IPC wheels support CPython 3.12, 3.13 and 3.14 only."
        )
    abi = f"cp{major}{minor}"
    return f"{WHEEL_PREFIX}{abi}-{abi}-linux_x86_64.whl"


def _api_get_release(repo: str, tag: str, token: str) -> dict | None:
    api = os.environ.get("GITHUB_API_URL", "https://api.github.com").rstrip("/")
    request = Request(
        f"{api}/repos/{repo}/releases/tags/{quote(tag, safe='')}",
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": API_VERSION,
            "User-Agent": "kicad-blocks-ci",
        },
    )
    try:
        with urlopen(request, timeout=45) as response:
            payload = response.read()
    except HTTPError as exc:
        if exc.code == 404:
            return None
        raise RuntimeError(
            f"GitHub release lookup failed with HTTP {exc.code}: "
            f"{exc.read().decode('utf-8', errors='replace')}"
        ) from exc
    try:
        result = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            "GitHub returned invalid JSON for the pinned KiCad IPC release."
        ) from exc
    if not isinstance(result, dict):
        raise RuntimeError(
            "GitHub returned an unexpected pinned KiCad IPC release response."
        )
    return result


def _download_asset(url: str) -> bytes:
    request = Request(
        url,
        headers={"Accept": "application/octet-stream", "User-Agent": "kicad-blocks-ci"},
    )
    with urlopen(request, timeout=90) as response:
        return response.read()


def download_pinned_wheel(
    wheelhouse: Path,
    repo: str,
    source_sha: str,
    token: str,
) -> Path | None:
    """Download one ABI-matched immutable wheel, validating the GitHub digest."""
    if not re.fullmatch(r"[0-9a-f]{40}", source_sha):
        raise ValueError(
            "The pinned kicad-python source revision must be a 40-character SHA."
        )
    if not repo or "/" not in repo:
        raise ValueError("Repository must be in owner/name form.")
    if not token:
        raise ValueError(
            "GITHUB_TOKEN is required to inspect pinned dependency releases."
        )

    wheel_name = expected_wheel_name()
    tag = f"kicad-python-{source_sha[:8]}"
    release = _api_get_release(repo, tag, token)
    if release is None:
        return None
    if release.get("tag_name") != tag:
        raise RuntimeError(
            f"GitHub returned a different release than the requested immutable tag {tag!r}."
        )

    matches = [
        asset for asset in release.get("assets", []) if asset.get("name") == wheel_name
    ]
    if not matches:
        return None
    if len(matches) != 1:
        raise RuntimeError(
            f"Pinned release contains duplicate assets named {wheel_name!r}."
        )
    asset = matches[0]
    if asset.get("state", "uploaded") != "uploaded":
        raise RuntimeError(f"Pinned wheel asset {wheel_name!r} is not fully uploaded.")
    url = asset.get("browser_download_url")
    expected_digest = asset.get("digest")
    expected_size = asset.get("size")
    if (
        not url
        or not isinstance(expected_digest, str)
        or not re.fullmatch(r"sha256:[0-9a-f]{64}", expected_digest)
    ):
        raise RuntimeError(
            f"Pinned wheel release asset is missing its URL or SHA-256 digest: {asset!r}."
        )

    payload = _download_asset(url)
    actual_digest = f"sha256:{hashlib.sha256(payload).hexdigest()}"
    if actual_digest != expected_digest:
        raise RuntimeError(
            f"Immutable KiCad IPC wheel digest mismatch for {wheel_name}: "
            f"expected {expected_digest}, received {actual_digest}."
        )
    if expected_size is not None and len(payload) != int(expected_size):
        raise RuntimeError(
            f"Immutable KiCad IPC wheel size mismatch for {wheel_name}: "
            f"expected {expected_size}, received {len(payload)}."
        )
    if not zipfile.is_zipfile(io.BytesIO(payload)):
        raise RuntimeError(
            f"Downloaded KiCad IPC asset is not a valid wheel archive: {wheel_name}."
        )

    wheelhouse = Path(wheelhouse).expanduser().resolve()
    wheelhouse.mkdir(parents=True, exist_ok=True)
    destination = wheelhouse / wheel_name
    if destination.exists():
        existing_digest = (
            f"sha256:{hashlib.sha256(destination.read_bytes()).hexdigest()}"
        )
        if existing_digest != expected_digest:
            raise RuntimeError(
                f"Refusing to overwrite a different local wheel at {destination}."
            )
        return destination

    with tempfile.NamedTemporaryFile(
        dir=wheelhouse, prefix=f".{wheel_name}.", delete=False
    ) as temporary:
        temporary_path = Path(temporary.name)
        temporary.write(payload)
    temporary_path.replace(destination)
    print(f"Reused immutable KiCad IPC wheel: {destination} ({actual_digest})")
    return destination


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
    downloaded = download_pinned_wheel(
        args.wheelhouse, args.repo, args.source_sha, token
    )
    if downloaded is None:
        print("No matching immutable wheel asset exists; a source build is required.")
        return 3
    print(f"pinned-wheel-reuse=passed file={downloaded.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
