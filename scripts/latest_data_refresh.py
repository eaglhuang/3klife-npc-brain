from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import URLError, HTTPError
from urllib.request import Request, urlopen


DEFAULT_HEALTH_URL = "https://threeklife-npc-brain.onrender.com/healthz"
ALLOWED_ARTIFACT_VERSION_KINDS = {"semver", "git-sha", "sha256", "opaque"}
DEFAULT_ARTIFACT_VERSION_BASIS = "json-marker-path:v1-sorted"
SEMVER_PATTERN = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)
VERSION_MARKER_KEYS = (
    "schemaVersion",
    "dataVersion",
    "datasetVersion",
    "snapshotVersion",
    "cacheVersion",
    "generatedAt",
    "version",
    "promptVersion",
    "cacheSchemaVersion",
)


def fetch_json(url: str, timeout_seconds: int = 10) -> dict:
    request = Request(url, headers={"Accept": "application/json"})
    with urlopen(request, timeout=timeout_seconds) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON payload must be an object: {url}")
    return payload


def post(url: str, timeout_seconds: int = 10) -> None:
    request = Request(url, method="POST")
    with urlopen(request, timeout=timeout_seconds):
        return


def is_semver(text: str) -> bool:
    return bool(SEMVER_PATTERN.match(text))


def read_framework_semver(repo_root: Path) -> str:
    cache_path = repo_root / ".atm/runtime/version-cache.json"
    if not cache_path.exists():
        return ""
    try:
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    for key in ("dataVersion", "lastSeenFrameworkVersion", "specVersion"):
        value = str(payload.get(key) or "").strip()
        if value and is_semver(value):
            return value
    return ""


def resolve_expected_data_version(repo_root: Path, expected_version: str) -> tuple[str, str]:
    candidate = str(expected_version or "").strip()
    if candidate and is_semver(candidate):
        return candidate, "arg:expected-data-version"
    framework_semver = read_framework_semver(repo_root)
    if framework_semver:
        return framework_semver, ".atm/runtime/version-cache.json"
    return candidate, "arg:expected-data-version-invalid"


def extract_version_metadata(payload: object) -> dict[str, str]:
    containers: list[dict] = []
    if isinstance(payload, dict):
        containers.append(payload)
        deployment = payload.get("deployment")
        if isinstance(deployment, dict):
            containers.append(deployment)
    metadata = {
        "dataVersion": "",
        "artifactVersion": "",
        "artifactVersionKind": "",
        "artifactVersionBasis": "",
    }
    for container in containers:
        if not metadata["dataVersion"]:
            value = str(container.get("dataVersion") or "").strip()
            if value:
                metadata["dataVersion"] = value
        if not metadata["artifactVersion"]:
            value = str(container.get("artifactVersion") or "").strip()
            if value:
                metadata["artifactVersion"] = value
        if not metadata["artifactVersionKind"]:
            value = str(container.get("artifactVersionKind") or "").strip().lower()
            if value:
                metadata["artifactVersionKind"] = value
        if not metadata["artifactVersionBasis"]:
            value = str(container.get("artifactVersionBasis") or "").strip()
            if value:
                metadata["artifactVersionBasis"] = value
    return metadata


def evaluate_refresh(local_meta: dict[str, str], remote_meta: dict[str, str]) -> tuple[bool, str]:
    local_data_version = str(local_meta.get("dataVersion") or "").strip()
    if not local_data_version or not is_semver(local_data_version):
        return False, "local dataVersion is missing or not semver"

    remote_data_version = str(remote_meta.get("dataVersion") or "").strip()
    if not remote_data_version or not is_semver(remote_data_version):
        return False, "remote dataVersion is missing or not semver"
    if remote_data_version != local_data_version:
        return False, "dataVersion changed"

    local_artifact_version = str(local_meta.get("artifactVersion") or "").strip()
    local_artifact_kind = str(local_meta.get("artifactVersionKind") or "").strip().lower()
    remote_artifact_version = str(remote_meta.get("artifactVersion") or "").strip()
    remote_artifact_kind = str(remote_meta.get("artifactVersionKind") or "").strip().lower()

    if not local_artifact_version or not local_artifact_kind:
        return False, "local artifact metadata is incomplete"
    if local_artifact_kind not in ALLOWED_ARTIFACT_VERSION_KINDS:
        return False, "local artifactVersionKind is unsupported"
    if not remote_artifact_version or not remote_artifact_kind:
        return False, "remote artifact metadata is incomplete"
    if remote_artifact_kind not in ALLOWED_ARTIFACT_VERSION_KINDS:
        return False, "remote artifactVersionKind is unsupported"
    if local_artifact_kind != remote_artifact_kind:
        return False, "artifactVersionKind changed (cross-kind)"
    local_artifact_basis = str(local_meta.get("artifactVersionBasis") or "").strip()
    remote_artifact_basis = str(remote_meta.get("artifactVersionBasis") or "").strip()
    if local_artifact_basis and remote_artifact_basis and local_artifact_basis != remote_artifact_basis:
        return False, "artifactVersionBasis changed"
    if local_artifact_version != remote_artifact_version:
        return False, "artifactVersion changed"
    return True, "dataVersion and artifact identity unchanged"


def extract_markers(payload: object) -> dict[str, str]:
    if not isinstance(payload, dict):
        return {}
    markers: dict[str, str] = {}
    for key in VERSION_MARKER_KEYS:
        value = payload.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            markers[key] = text
    return markers


def _list_git_tracked_files(repo_root: Path) -> set[str]:
    try:
        completed = subprocess.run(
            ["git", "ls-files", "-z"],
            cwd=repo_root,
            check=True,
            capture_output=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return set()
    raw = completed.stdout.decode("utf-8", errors="ignore")
    tracked = {
        entry.replace("\\", "/")
        for entry in raw.split("\x00")
        if entry.strip()
    }
    return tracked


def _read_tracked_json_from_head(repo_root: Path, rel_path: str) -> object:
    try:
        completed = subprocess.run(
            ["git", "show", f"HEAD:{rel_path}"],
            cwd=repo_root,
            check=True,
            capture_output=True,
        )
        text = completed.stdout.decode("utf-8")
        return json.loads(text)
    except (subprocess.CalledProcessError, FileNotFoundError, UnicodeDecodeError, json.JSONDecodeError):
        path = repo_root / rel_path
        return json.loads(path.read_text(encoding="utf-8"))


def scan_artifact_versions(repo_root: Path, roots: list[Path]) -> dict[str, object]:
    files: list[dict[str, object]] = []
    digester = hashlib.sha256()
    total_files = 0
    tracked = _list_git_tracked_files(repo_root)
    tracked_candidates = 0
    using_tracked_only = bool(tracked)

    if using_tracked_only:
        root_prefixes: list[str] = []
        for root in roots:
            absolute_root = root if root.is_absolute() else repo_root / root
            try:
                rel_root = absolute_root.relative_to(repo_root)
            except ValueError:
                continue
            prefix = str(rel_root).replace("\\", "/").rstrip("/") + "/"
            root_prefixes.append(prefix)

        candidate_paths = sorted(
            path
            for path in tracked
            if path.endswith(".json") and any(path.startswith(prefix) for prefix in root_prefixes)
        )
        tracked_candidates = len(candidate_paths)
        for rel_path in candidate_paths:
            total_files += 1
            try:
                payload = _read_tracked_json_from_head(repo_root, rel_path)
            except Exception:
                continue
            markers = extract_markers(payload)
            if not markers:
                continue
            ordered_markers = {key: markers[key] for key in sorted(markers)}
            fingerprint_row = json.dumps(
                {"path": rel_path, "markers": ordered_markers},
                ensure_ascii=False,
                sort_keys=True,
            )
            digester.update(fingerprint_row.encode("utf-8"))
            files.append(
                {
                    "path": rel_path,
                    "markers": ordered_markers,
                }
            )

    else:
        for root in roots:
            absolute_root = root if root.is_absolute() else repo_root / root
            if not absolute_root.exists():
                continue
            for path in sorted(absolute_root.rglob("*.json"), key=lambda item: str(item).replace("\\", "/")):
                total_files += 1
                try:
                    payload = json.loads(path.read_text(encoding="utf-8"))
                except Exception:
                    continue
                markers = extract_markers(payload)
                if not markers:
                    continue
                rel_path = str(path.relative_to(repo_root)).replace("\\", "/")
                ordered_markers = {key: markers[key] for key in sorted(markers)}
                fingerprint_row = json.dumps(
                    {"path": rel_path, "markers": ordered_markers},
                    ensure_ascii=False,
                    sort_keys=True,
                )
                digester.update(fingerprint_row.encode("utf-8"))
                files.append(
                    {
                        "path": rel_path,
                        "markers": ordered_markers,
                    }
                )
    return {
        "versionDigest": digester.hexdigest()[:24] if files else "no-markers",
        "fileCount": total_files,
        "markerFileCount": len(files),
        "trackedOnly": using_tracked_only,
        "trackedCandidateCount": tracked_candidates,
        "files": files[:50],
    }


def resolve_data_version(health_payload: dict, expected_version: str) -> str:
    data_version = str(health_payload.get("dataVersion") or "").strip()
    if data_version:
        return data_version
    deployment = health_payload.get("deployment") if isinstance(health_payload.get("deployment"), dict) else {}
    deployment_data_version = str((deployment or {}).get("dataVersion") or "").strip()
    if deployment_data_version:
        return deployment_data_version
    deployment_commit = str((deployment or {}).get("renderGitCommit") or "").strip()
    if deployment_commit:
        return deployment_commit
    return expected_version


def run(args: argparse.Namespace) -> int:
    repo_root = Path(args.repo_root).resolve()
    expected_data_version, expected_data_version_source = resolve_expected_data_version(
        repo_root, str(args.expected_data_version or "").strip()
    )
    artifact_scan = scan_artifact_versions(repo_root, [Path(root) for root in args.artifact_roots])
    local_metadata = {
        "dataVersion": expected_data_version,
        "artifactVersion": str(artifact_scan["versionDigest"]),
        "artifactVersionKind": args.artifact_version_kind,
        "artifactVersionBasis": DEFAULT_ARTIFACT_VERSION_BASIS,
    }

    report: dict[str, object] = {
        "schemaVersion": "latest-data-refresh.v1",
        "dataVersion": expected_data_version,
        "dataVersionSource": expected_data_version_source,
        "artifactVersion": artifact_scan["versionDigest"],
        "artifactVersionKind": args.artifact_version_kind,
        "artifactVersionBasis": DEFAULT_ARTIFACT_VERSION_BASIS,
        "artifactVersionFileCount": artifact_scan["markerFileCount"],
        "artifactVersionScannedFiles": artifact_scan["fileCount"],
        "healthUrl": args.health_url,
        "expectedDataVersion": expected_data_version,
        "verdict": "preflight",
    }

    deploy_triggered = False
    deadline = time.monotonic() + args.timeout_seconds
    last_health: dict | None = None
    poll_phase = "precheck"

    while True:
        try:
            health_payload = fetch_json(args.health_url, timeout_seconds=args.http_timeout_seconds)
            last_health = health_payload
            remote_metadata = extract_version_metadata(health_payload)
            remote_data_version = resolve_data_version(health_payload, expected_data_version)
            if not remote_metadata["dataVersion"] and remote_data_version:
                remote_metadata["dataVersion"] = remote_data_version
            report["remoteMetadata"] = remote_metadata
            matches, reason = evaluate_refresh(local_metadata, remote_metadata)
            if matches:
                report["deployTriggered"] = deploy_triggered
                report["verdict"] = "fresh"
                report["reason"] = "metadata unchanged, refresh skipped" if not deploy_triggered else "metadata matched after refresh"
                report["refreshDecision"] = {
                    "phase": poll_phase,
                    "metadataMatch": True,
                    "reason": reason,
                }
                print(json.dumps(report, ensure_ascii=False, indent=2))
                return 0

            report["refreshDecision"] = {
                "phase": poll_phase,
                "metadataMatch": False,
                "reason": reason,
            }
            if not deploy_triggered:
                if args.deploy_hook_url:
                    post(args.deploy_hook_url, timeout_seconds=args.http_timeout_seconds)
                    deploy_triggered = True
                    report["deployTriggered"] = True
                    poll_phase = "post-deploy"
                else:
                    report["deployTriggered"] = False
                    report["verdict"] = "stale-cache"
                    report["reason"] = reason
                    print(json.dumps(report, ensure_ascii=False, indent=2))
                    return 1
        except (HTTPError, URLError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
            report["lastError"] = str(exc)
            if not deploy_triggered and args.deploy_hook_url:
                post(args.deploy_hook_url, timeout_seconds=args.http_timeout_seconds)
                deploy_triggered = True
                report["deployTriggered"] = True
                poll_phase = "post-deploy"

        if time.monotonic() >= deadline:
            report["deployTriggered"] = deploy_triggered
            report["verdict"] = "stale-cache"
            report["reason"] = "health check timed out before metadata match"
            if last_health is not None:
                report["lastHealth"] = last_health
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 1
        time.sleep(args.poll_interval_seconds)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Calibrate latest data freshness for scheduled startup/deploy flows.")
    parser.add_argument("--repo-root", default=".", help="Repository root used to resolve artifacts and git SHA.")
    parser.add_argument("--health-url", default=DEFAULT_HEALTH_URL, help="Remote health endpoint to inspect.")
    parser.add_argument("--deploy-hook-url", default="", help="Optional Render deploy hook URL to trigger before polling.")
    parser.add_argument(
        "--expected-data-version",
        default="",
        help="Expected semver dataVersion. Defaults to .atm/runtime/version-cache.json when available.",
    )
    parser.add_argument(
        "--artifact-version-kind",
        default="sha256",
        choices=sorted(ALLOWED_ARTIFACT_VERSION_KINDS),
        help="Artifact identity kind used for comparison.",
    )
    parser.add_argument("--timeout-seconds", type=int, default=900, help="Maximum time to wait for freshness confirmation.")
    parser.add_argument("--poll-interval-seconds", type=int, default=15, help="Seconds to wait between health checks.")
    parser.add_argument("--http-timeout-seconds", type=int, default=10, help="HTTP timeout for each request.")
    parser.add_argument(
        "--artifact-root",
        dest="artifact_roots",
        action="append",
        default=["artifacts/data-pipeline/sanguo-rag/extracted"],
        help="Artifact root to scan for version markers. Repeatable.",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
