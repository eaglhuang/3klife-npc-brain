from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

from repo_layout import resolve_repo_root


REPO_ROOT = resolve_repo_root(__file__)


def git_data_version(repo_root: Path | None = None) -> str:
    root = repo_root or REPO_ROOT
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _iter_version_fingerprint_entries(paths: list[Path]) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for path in paths:
        if not path.exists():
            entries.append({"path": str(path), "exists": False})
            continue
        if path.is_dir():
            for child in sorted(path.rglob("*")):
                if not child.is_file():
                    continue
                stat = child.stat()
                entries.append(
                    {
                        "path": str(child),
                        "exists": True,
                        "size": stat.st_size,
                        "mtimeNs": stat.st_mtime_ns,
                    }
                )
            continue
        stat = path.stat()
        entries.append(
            {
                "path": str(path),
                "exists": True,
                "size": stat.st_size,
                "mtimeNs": stat.st_mtime_ns,
            }
        )
    return entries


def artifact_version(paths: list[Path], *, repo_root: Path | None = None) -> str:
    root = repo_root or REPO_ROOT
    resolved_paths = [path if path.is_absolute() else root / path for path in paths]
    entries = _iter_version_fingerprint_entries(resolved_paths)
    digest = hashlib.sha256(
        json.dumps(entries, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    return digest[:24]


def build_version_metadata(
    *,
    schema_version: str,
    artifact_paths: list[Path | str] | None = None,
    repo_root: Path | None = None,
) -> dict[str, Any]:
    root = repo_root or REPO_ROOT
    normalized_paths = [Path(path) for path in (artifact_paths or [])]
    artifact_paths_list = [path if path.is_absolute() else root / path for path in normalized_paths]
    data_version = git_data_version(root)
    metadata: dict[str, Any] = {
        "schemaVersion": schema_version,
        "dataVersion": data_version,
        "dataVersionSource": "git-sha",
    }
    if artifact_paths_list:
        metadata["artifactVersion"] = artifact_version(artifact_paths_list, repo_root=root)
        metadata["artifactVersionSource"] = "file-mtime-size-fingerprint"
    return metadata
