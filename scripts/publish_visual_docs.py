"""Validate and publish CI-generated KiCad Blocks documentation previews."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

try:  # Package import for tests; direct import when executed as a script.
    from .visual_asset_inventory import ASSET_NAMES
except ImportError:  # pragma: no cover - exercised by the release CLI.
    from visual_asset_inventory import ASSET_NAMES
BRANCH = "docs/previews"


def run(*args: str, cwd: Path | None = None, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args, cwd=cwd, check=check, capture_output=True, text=True, timeout=60,
    )


def git(
    token: str,
    *args: str,
    cwd: Path | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    credential = base64.b64encode(f"x-access-token:{token}".encode()).decode("ascii")
    command = [
        "git",
        "-c",
        f"http.https://github.com/.extraheader=AUTHORIZATION: basic {credential}",
    ]
    if cwd is not None:
        command.extend(("-C", str(cwd)))
    command.extend(args)
    return run(*command, check=check)


def verify_bundle(source_dir: Path, source_sha: str) -> dict:
    manifest_path = source_dir / "manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(f"Visual documentation manifest is missing: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != 1:
        raise ValueError(f"Unsupported visual documentation schema: {manifest.get('schema_version')!r}")
    if manifest.get("source_sha") != source_sha:
        raise ValueError(
            f"Visual assets represent {manifest.get('source_sha')!r}, not checked-out source {source_sha!r}."
        )
    assets = manifest.get("assets")
    if not isinstance(assets, dict) or set(assets) != ASSET_NAMES:
        raise ValueError(
            f"Visual asset inventory mismatch; expected={sorted(ASSET_NAMES)}, actual={sorted(assets or {})}."
        )
    for name, expected_hash in assets.items():
        path = source_dir / name
        if not path.is_file() or path.stat().st_size == 0:
            raise FileNotFoundError(f"Generated visual asset is missing or empty: {name}")
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected_hash:
            raise ValueError(f"Visual asset provenance hash mismatch: {name}")
    return manifest


def publish(
    source_dir: Path,
    repository: str,
    source_sha: str,
    run_id: str,
    run_number: str,
    token: str,
) -> str:
    manifest = verify_bundle(source_dir, source_sha)
    if not token:
        raise ValueError("A GitHub Actions token is required to publish visual previews.")
    with tempfile.TemporaryDirectory(prefix="kicad-blocks-visual-publish-") as temporary:
        worktree = Path(temporary) / "repository"
        git(
            token, "clone", "--filter=blob:none",
            f"https://github.com/{repository}.git", str(worktree),
        )

        remote_exists = git(
            token, "ls-remote", "--exit-code", "--heads", "origin", BRANCH,
            cwd=worktree, check=False,
        ).returncode == 0
        if remote_exists:
            git(token, "fetch", "origin", BRANCH, cwd=worktree)
            git(token, "checkout", "-B", BRANCH, f"origin/{BRANCH}", cwd=worktree)
        else:
            git(token, "checkout", "-b", BRANCH, cwd=worktree)

        published = worktree / "previews"
        shutil.rmtree(published, ignore_errors=True)
        published.mkdir(parents=True, exist_ok=True)
        for name in sorted(ASSET_NAMES):
            shutil.copy2(source_dir / name, published / name)
        shutil.copy2(source_dir / "manifest.json", published / "manifest.json")
        provenance = {
            "source_commit": source_sha,
            "workflow_run_id": str(run_id),
            "workflow_run_number": str(run_number),
            "assets": manifest["assets"],
            "example": manifest.get("example"),
        }
        (published / "provenance.json").write_text(
            json.dumps(provenance, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

        run("git", "-C", str(worktree), "config", "user.name", "github-actions[bot]")
        run(
            "git", "-C", str(worktree), "config", "user.email",
            "41898282+github-actions[bot]@users.noreply.github.com",
        )
        run("git", "-C", str(worktree), "add", "previews")
        changed = run("git", "-C", str(worktree), "diff", "--cached", "--quiet", check=False).returncode != 0
        if changed:
            run(
                "git", "-C", str(worktree), "commit", "-m",
                f"ci: publish KiCad Blocks visual previews for {source_sha[:12]}",
            )
            git(token, "push", "origin", f"HEAD:{BRANCH}", cwd=worktree)

        git(token, "fetch", "origin", BRANCH, cwd=worktree)
        remote_sha = run("git", "-C", str(worktree), "rev-parse", f"origin/{BRANCH}").stdout.strip()
        local_sha = run("git", "-C", str(worktree), "rev-parse", "HEAD").stdout.strip()
        if remote_sha != local_sha:
            raise RuntimeError(f"Published visual branch mismatch: remote={remote_sha} local={local_sha}")
        print(
            f"kicad-visual-publish=passed source_sha={source_sha} "
            f"asset_count={len(ASSET_NAMES)} branch={BRANCH} commit={remote_sha}"
        )
        return remote_sha


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--run-number", required=True)
    args = parser.parse_args()
    publish(
        args.source_dir.expanduser().resolve(),
        args.repository,
        args.source_sha,
        args.run_id,
        args.run_number,
        os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN") or "",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
