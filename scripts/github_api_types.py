"""Typed JSON response contracts shared by GitHub release utilities."""

from __future__ import annotations

import json
from typing import NotRequired, TypedDict, cast


type JSONValue = str | int | float | bool | None | list[JSONValue] | dict[str, JSONValue]
type JSONObject = dict[str, JSONValue]


class GitHubApiResponse(TypedDict, total=False):
    """Subset of GitHub release and release-asset fields consumed by this project."""

    id: int
    name: str
    tag_name: str
    target_commitish: str
    body: str
    upload_url: str
    browser_download_url: str
    digest: str
    size: int
    state: str
    draft: bool
    prerelease: bool
    assets: list[GitHubApiResponse]


def decode_github_response(raw: bytes) -> GitHubApiResponse:
    """Decode a GitHub JSON object and reject non-object API responses."""
    if not raw:
        return cast(GitHubApiResponse, {})
    decoded: JSONValue = cast(JSONValue, json.loads(raw.decode("utf-8")))
    if not isinstance(decoded, dict) or not all(isinstance(key, str) for key in decoded):
        raise RuntimeError("GitHub API returned a JSON response that is not an object.")
    return cast(GitHubApiResponse, decoded)
