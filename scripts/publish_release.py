#!/usr/bin/env python3
"""Publish versioned release assets without replacing existing release bytes."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

API_VERSION = "2022-11-28"


def _request(
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
        "X-GitHub-Api-Version": API_VERSION,
    }
    data = body
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    elif body is not None:
        headers["Content-Type"] = content_type
    try:
        with urlopen(
            Request(url, data=data, headers=headers, method=method), timeout=60
        ) as response:
            raw = response.read()
    except HTTPError as exc:
        if allow_not_found and exc.code == 404:
            return None
        raise RuntimeError(
            f"GitHub API {method} {url} failed with HTTP {exc.code}: "
            f"{exc.read().decode('utf-8', errors='replace')}"
        ) from exc
    return json.loads(raw.decode("utf-8")) if raw else {}


def _digest(path: Path) -> str:
    return f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"


def publish_release(
    assets: list[Path],
    repo: str,
    tag: str,
    version: str,
    source_sha: str,
    changelog: str,
    token: str,
) -> str:
    if tag != f"v{version}":
        raise ValueError(f"Release tag {tag!r} must match VERSION {version!r}.")
    if not assets:
        raise ValueError("At least one release asset is required.")
    root = os.environ.get("GITHUB_API_URL", "https://api.github.com").rstrip("/")
    tag_url = f"{root}/repos/{repo}/releases/tags/{quote(tag, safe='')}"
    release = _request("GET", tag_url, token, allow_not_found=True)
    if release is None:
        release = _request(
            "POST",
            f"{root}/repos/{repo}/releases",
            token,
            payload={
                "tag_name": tag,
                "target_commitish": source_sha,
                "name": f"KiCad Blocks {version}",
                "body": changelog,
                "draft": False,
                "prerelease": False,
                "make_latest": "true",
            },
        )
    if not release or not release.get("id") or release.get("tag_name") != tag:
        raise RuntimeError(
            "Could not create or retrieve the expected versioned release."
        )

    release_id = release["id"]
    upload_url = release.get("upload_url", "").split("{", 1)[0]
    if not upload_url:
        raise RuntimeError("GitHub release is missing upload_url.")

    expected = {}
    for raw_path in assets:
        path = Path(raw_path).expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(path)
        expected[path.name] = (path.stat().st_size, _digest(path))
        existing = next(
            (
                asset
                for asset in release.get("assets", [])
                if asset.get("name") == path.name
            ),
            None,
        )
        if existing is not None:
            if existing.get("size") != path.stat().st_size or existing.get(
                "digest"
            ) != _digest(path):
                raise RuntimeError(
                    f"Release asset {path.name!r} already exists with different bytes; refusing overwrite."
                )
            continue
        _request(
            "POST",
            f"{upload_url}?name={quote(path.name, safe='')}",
            token,
            body=path.read_bytes(),
            content_type="application/octet-stream",
        )

    refreshed = _request("GET", f"{root}/repos/{repo}/releases/{release_id}", token)
    if refreshed is None:
        raise RuntimeError("Versioned release disappeared after upload.")
    for name, (size, digest) in expected.items():
        asset = next(
            (item for item in refreshed.get("assets", []) if item.get("name") == name),
            None,
        )
        if asset is None or asset.get("size") != size or asset.get("digest") != digest:
            raise RuntimeError(
                f"Release asset verification failed for {name!r}: {asset!r}"
            )
    url = f"https://github.com/{repo}/releases/tag/{tag}"
    print(f"Published and verified {url}")
    return url


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY"))
    parser.add_argument("--tag", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--changelog", type=Path, required=True)
    parser.add_argument("assets", nargs="+", type=Path)
    args = parser.parse_args()
    token = os.environ.get("GITHUB_TOKEN")
    if not args.repo:
        parser.error("--repo or GITHUB_REPOSITORY is required.")
    if not token:
        parser.error("GITHUB_TOKEN is required.")
    publish_release(
        args.assets,
        args.repo,
        args.tag,
        args.version,
        args.source_sha,
        args.changelog.read_text(encoding="utf-8"),
        token,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
