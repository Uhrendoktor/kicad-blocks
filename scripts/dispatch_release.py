#!/usr/bin/env python3
"""Dispatch the V1.1 release workflow unless its versioned assets are already published."""

from __future__ import annotations

import argparse
import json
import os
import re
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen


def _request(method: str, url: str, token: str, payload: dict | None = None) -> dict | None:
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    if data is not None:
        headers["Content-Type"] = "application/json"
    try:
        with urlopen(Request(url, data=data, headers=headers, method=method), timeout=45) as response:
            raw = response.read()
    except HTTPError as exc:
        if method == "GET" and exc.code == 404:
            return None
        raise RuntimeError(
            f"GitHub API {method} {url} failed with HTTP {exc.code}: "
            f"{exc.read().decode('utf-8', errors='replace')}"
        ) from exc
    return json.loads(raw.decode("utf-8")) if raw else {}


def dispatch_release(repo: str, tag: str, source_sha: str, version: str, token: str) -> bool:
    if tag != f"v{version}" or not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise ValueError(f"Release tag {tag!r} does not match version {version!r}.")
    if not re.fullmatch(r"[0-9a-f]{40}", source_sha):
        raise ValueError("Release source SHA must be 40 lowercase hexadecimal characters.")

    api = os.environ.get("GITHUB_API_URL", "https://api.github.com").rstrip("/")
    release_url = f"{api}/repos/{repo}/releases/tags/{quote(tag, safe='')}"
    release = _request("GET", release_url, token)
    archive_name = f"kicad-blocks-{version}-linux-x86_64-cp312.zip"
    required_assets = {archive_name, archive_name + ".sha256"}
    if release is not None:
        assets_by_name = {
            asset.get("name"): asset for asset in release.get("assets", [])
        }
        if required_assets.issubset(assets_by_name):
            complete = all(
                assets_by_name[name].get("size", 0) > 0
                and str(assets_by_name[name].get("digest", "")).startswith("sha256:")
                for name in required_assets
            )
            if complete and not release.get("draft", False) and not release.get("prerelease", False):
                print(f"Release {tag} already has its ZIP and checksum; not dispatching again.")
                return False

    payload = {
        "ref": "main",
        "inputs": {
            "tag": tag,
            "source_sha": source_sha,
        },
    }
    _request(
        "POST",
        f"{api}/repos/{repo}/actions/workflows/release.yml/dispatches",
        token,
        payload,
    )
    print(f"Dispatched release workflow for {tag} at {source_sha}.")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY"))
    parser.add_argument("--tag", required=True)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--version", required=True)
    args = parser.parse_args()
    token = os.environ.get("GITHUB_TOKEN")
    if not args.repo:
        parser.error("--repo or GITHUB_REPOSITORY is required.")
    if not token:
        parser.error("GITHUB_TOKEN is required.")
    dispatch_release(args.repo, args.tag, args.source_sha, args.version, token)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
