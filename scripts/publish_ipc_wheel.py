#!/usr/bin/env python3
"""Publish the tested KiCad Python development wheel to a stable GitHub Release asset."""

from __future__ import annotations

import argparse
import json
import os
import ssl
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

RELEASE_TAG = "kicad-python-dev"
ASSET_PREFIX = "kicad_python-0.9.0.dev0-"
TRANSIENT_HTTP_STATUS = {408, 425, 429, 500, 502, 503, 504}


def _retry_delay(attempt: int, error: BaseException) -> float:
    """Return a bounded backoff, respecting a numeric Retry-After header."""
    headers = getattr(error, "headers", None)
    retry_after = headers.get("Retry-After") if headers is not None else None
    if retry_after:
        try:
            return min(30.0, max(1.0, float(retry_after)))
        except ValueError:
            pass
    return min(20.0, 2.0 ** (attempt + 1))


def _api_request(
    method: str,
    url: str,
    token: str,
    *,
    payload: dict | None = None,
    body: bytes | None = None,
    content_type: str = "application/vnd.github+json",
    allow_not_found: bool = False,
    timeout: float = 45.0,
    max_retries: int = 3,
) -> dict | None:
    """Call GitHub's API with bounded retries for transient network/API failures."""
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

    for attempt in range(max_retries + 1):
        request = Request(url, data=data, headers=headers, method=method)
        try:
            with urlopen(request, timeout=timeout) as response:
                raw = response.read()
            if not raw:
                return {}
            try:
                return json.loads(raw.decode("utf-8"))
            except json.JSONDecodeError as exc:
                raise RuntimeError(f"GitHub API returned non-JSON data for {url}.") from exc
        except HTTPError as exc:
            details = exc.read().decode("utf-8", errors="replace")
            if allow_not_found and exc.code == 404:
                return None
            if exc.code in TRANSIENT_HTTP_STATUS and attempt < max_retries:
                delay = _retry_delay(attempt, exc)
                print(
                    f"GitHub API {method} returned HTTP {exc.code}; "
                    f"retrying in {delay:g}s ({attempt + 1}/{max_retries})."
                )
                time.sleep(delay)
                continue
            raise RuntimeError(
                f"GitHub API {method} {url} failed with HTTP {exc.code}: {details}"
            ) from exc
        except (TimeoutError, ssl.SSLError, URLError, ConnectionError) as exc:
            if attempt >= max_retries:
                raise RuntimeError(
                    f"GitHub API {method} {url} failed after {attempt + 1} attempts: {exc}"
                ) from exc
            delay = _retry_delay(attempt, exc)
            print(
                f"GitHub API {method} network error: {exc}; "
                f"retrying in {delay:g}s ({attempt + 1}/{max_retries})."
            )
            time.sleep(delay)
    raise AssertionError("Unreachable GitHub API retry state.")


def _release_url(repo: str) -> str:
    api = os.environ.get("GITHUB_API_URL", "https://api.github.com").rstrip("/")
    return f"{api}/repos/{repo}/releases/tags/{quote(RELEASE_TAG, safe='')}"


def _get_or_create_release(repo: str, token: str) -> dict:
    release_url = _release_url(repo)
    release = _api_request("GET", release_url, token, allow_not_found=True)
    if release is not None:
        return release

    api = os.environ.get("GITHUB_API_URL", "https://api.github.com").rstrip("/")
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
    try:
        created = _api_request(
            "POST",
            f"{api}/repos/{repo}/releases",
            token,
            payload=payload,
        )
        if created is None:
            raise RuntimeError("GitHub did not return the newly created release.")
        return created
    except RuntimeError:
        # POST may have succeeded even if the TLS response was lost. Re-read
        # the tag before deciding that release creation actually failed.
        release = _api_request("GET", release_url, token, allow_not_found=True)
        if release is not None:
            return release
        raise


def _matching_asset(release: dict, name: str, size: int) -> dict | None:
    for asset in release.get("assets", []):
        if (
            asset.get("name") == name
            and asset.get("size") == size
            and asset.get("state", "uploaded") == "uploaded"
            and asset.get("browser_download_url")
        ):
            return asset
    return None


def publish_wheel(wheelhouse: Path, repo: str, token: str) -> str:
    wheelhouse = Path(wheelhouse).expanduser().resolve()
    wheels = sorted(path for path in wheelhouse.glob(f"{ASSET_PREFIX}*.whl") if path.is_file())
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
                allow_not_found=True,
            )

    upload_url = release.get("upload_url", "").split("{", 1)[0]
    if not upload_url:
        raise RuntimeError("GitHub release response is missing upload_url.")
    url = f"{upload_url}?name={quote(wheel.name, safe='')}"
    wheel_size = wheel.stat().st_size
    try:
        uploaded = _api_request(
            "POST",
            url,
            token,
            body=wheel.read_bytes(),
            content_type="application/octet-stream",
        )
    except RuntimeError:
        # A timed-out upload may nevertheless have reached GitHub. Verify the
        # release state before retrying the whole publication or failing CI.
        refreshed_release = _api_request("GET", _release_url(repo), token, allow_not_found=True)
        uploaded = None
        if refreshed_release is not None:
            uploaded = _matching_asset(refreshed_release, wheel.name, wheel_size)
        if uploaded is None:
            raise

    if uploaded is None:
        raise RuntimeError("GitHub did not return or retain the uploaded wheel asset.")
    if uploaded.get("name") != wheel.name or uploaded.get("size") != wheel_size:
        raise RuntimeError(f"GitHub release asset verification failed: {uploaded!r}.")
    download_url = uploaded.get("browser_download_url")
    if not download_url:
        raise RuntimeError("GitHub did not return a release asset download URL.")
    print(f"Published {wheel.name} ({wheel_size} bytes) to {download_url}")
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
