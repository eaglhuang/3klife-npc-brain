from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


UPSTREAM_URL = "https://github.com/fastapi/fastapi.git"
DEFAULT_SNAPSHOT_RELATIVE_PATH = Path("local/public-source-snapshots/fastapi-0.136.3")
DEFAULT_ARTIFACT_ROOT = Path("artifacts/external-public-repo/fastapi/2026-06-27")


ENDPOINT_EXCERPT_SNIPPET = r"""
import json
import os
import subprocess
from pathlib import Path

import fastapi
from fastapi.testclient import TestClient

from app.main import create_app

os.environ["NPC_LLM_PROVIDER_ORDER"] = "deterministic"
os.environ["NPC_BRAIN_DEPLOY_API_KEY"] = "smoke-test-api-key"

client = TestClient(create_app())
repo_root = Path.cwd()
git_sha = subprocess.run(
    ["git", "rev-parse", "HEAD"],
    cwd=repo_root,
    check=True,
    capture_output=True,
    text=True,
).stdout.strip()
service_headers = {"X-API-Key": os.environ["NPC_BRAIN_DEPLOY_API_KEY"]}

health = client.get("/healthz")
contexts = client.get("/v1/npc/context-options", params={"generalId": "zhang-fei"}, headers=service_headers)
keywords = client.get("/v1/npc/keyword-options", params={"generalId": "zhang-fei"}, headers=service_headers)
keyword_payload = keywords.json()
selected_keyword_keys = [
    item["keywordKey"]
    for items in keyword_payload["categories"].values()
    for item in items[:1]
][:3]
dialogue = client.post(
    "/v1/npc/dialogue",
    json={
        "generalId": "zhang-fei",
        "contextKey": "changban-bridge",
        "selectedKeywordKeys": selected_keyword_keys + ["unknown-key"],
        "maxChars": 90,
    },
)

payload = dialogue.json()
result = {
    "fastapiVersion": fastapi.__version__,
    "fastapiModulePath": fastapi.__file__,
    "hostGitHead": git_sha,
    "health": {
        "statusCode": health.status_code,
        "ok": health.json().get("ok"),
        "service": health.json().get("service"),
        "schemaVersion": health.json().get("schemaVersion"),
        "dataVersion": health.json().get("dataVersion"),
        "artifactVersion": health.json().get("artifactVersion"),
        "fastapiSnapshot": health.json().get("fastapiSnapshot"),
    },
    "contexts": {
        "statusCode": contexts.status_code,
        "count": len(contexts.json().get("options", [])),
        "firstContextKey": (contexts.json().get("options") or [{}])[0].get("contextKey"),
    },
    "keywords": {
        "statusCode": keywords.status_code,
        "categoryCount": len(keyword_payload.get("categories", {})),
        "selectedKeywordKeys": selected_keyword_keys,
    },
    "dialogue": {
        "statusCode": dialogue.status_code,
        "textPreview": payload.get("text", "")[:90],
        "evidenceRefCount": len(payload.get("evidenceRefs", [])),
        "rejectedKeywordKeys": payload.get("rejectedKeywordKeys", []),
        "llmModelPreset": payload.get("llmModelPreset"),
    },
}
helper = getattr(fastapi, "get_public_source_snapshot_metadata", None)
if callable(helper):
    result["helperMetadata"] = helper()
print(json.dumps(result, ensure_ascii=False))
"""


def run_command(
    command: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
) -> dict[str, Any]:
    completed = subprocess.run(
        command,
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return {
        "command": command,
        "cwd": str(cwd),
        "returnCode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }


def require_success(result: dict[str, Any], label: str) -> None:
    if result["returnCode"] != 0:
        raise RuntimeError(f"{label} failed: {result['command']}\n{result['stdout']}\n{result['stderr']}")


def git_value(args: list[str], cwd: Path) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    )
    return completed.stdout.strip()


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def append_command_log(path: Path, mode: str, results: list[dict[str, Any]]) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(f"## {mode}\n")
        for result in results:
            handle.write(f"$ {' '.join(result['command'])}\n")
            handle.write(f"cwd: {result['cwd']}\n")
            handle.write(f"returnCode: {result['returnCode']}\n")
            if result["stdout"]:
                handle.write("stdout:\n")
                handle.write(result["stdout"])
                if not result["stdout"].endswith("\n"):
                    handle.write("\n")
            if result["stderr"]:
                handle.write("stderr:\n")
                handle.write(result["stderr"])
                if not result["stderr"].endswith("\n"):
                    handle.write("\n")
            handle.write("\n")


def refresh_hash_manifest(artifact_root: Path) -> None:
    manifest_path = artifact_root / "artifact-hash-manifest.sha256"
    entries: list[str] = []
    for path in sorted(p for p in artifact_root.rglob("*") if p.is_file() and p != manifest_path):
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        entries.append(f"{digest}  {path.relative_to(artifact_root).as_posix()}")
    manifest_path.write_text("\n".join(entries) + ("\n" if entries else ""), encoding="utf-8")


def build_provenance(repo_root: Path, snapshot_root: Path) -> dict[str, Any]:
    return {
        "capturedAt": datetime.now(timezone.utc).isoformat(),
        "upstreamUrl": UPSTREAM_URL,
        "snapshotHead": git_value(["rev-parse", "HEAD"], snapshot_root),
        "snapshotDescribe": git_value(["describe", "--tags", "--always"], snapshot_root),
        "snapshotStatusShort": git_value(["status", "--short"], snapshot_root),
        "snapshotRoot": str(snapshot_root.resolve()),
        "hostRepoRoot": str(repo_root.resolve()),
        "hostRepoHead": git_value(["rev-parse", "HEAD"], repo_root),
        "hostRepoStatusShort": git_value(["status", "--short"], repo_root),
    }


def maybe_build_summary(artifact_root: Path) -> None:
    baseline_path = artifact_root / "baseline.json"
    post_change_path = artifact_root / "post-change.json"
    provenance_path = artifact_root / "provenance.json"
    if not (baseline_path.is_file() and post_change_path.is_file() and provenance_path.is_file()):
        return

    provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    post_change = json.loads(post_change_path.read_text(encoding="utf-8"))
    post_snapshot = post_change["endpointExcerpt"]["health"].get("fastapiSnapshot") or {}
    helper_snapshot = post_change["endpointExcerpt"].get("helperMetadata") or {}
    summary = {
        "claimType": "public-source-snapshot-governance-case",
        "paperSafeClaim": (
            "ATM exercised a provenance-pinned FastAPI public-source snapshot inside the live 3klife-npc-brain host "
            "repository and preserved replayable validation evidence before and after a small host-visible snapshot modification."
        ),
        "nonClaim": "This case does not claim governance over the FastAPI upstream maintainer workflow.",
        "upstreamUrl": provenance["upstreamUrl"],
        "snapshotHead": provenance["snapshotHead"],
        "hostRepoHead": provenance["hostRepoHead"],
        "baselineSmokePass": baseline["commands"]["httpSmoke"]["returnCode"] == 0,
        "postChangeSmokePass": post_change["commands"]["httpSmoke"]["returnCode"] == 0,
        "baselineFastapiModulePath": baseline["endpointExcerpt"]["fastapiModulePath"],
        "postChangeFastapiModulePath": post_change["endpointExcerpt"]["fastapiModulePath"],
        "postChangeCaseTag": (post_snapshot or {}).get("caseTag"),
        "postChangeHelper": (helper_snapshot or {}).get("helper"),
        "postChangeSnapshotHead": (helper_snapshot or {}).get("repoHead"),
        "artifactRoot": str(artifact_root.resolve()),
        "touchedPaths": [
            "local/public-source-snapshots/fastapi-0.136.3/fastapi/__init__.py",
            "app/main.py",
        ],
    }
    write_json(artifact_root / "summary.json", summary)

    markdown = "\n".join(
        [
            "# FastAPI Public-Source Snapshot Case",
            "",
            summary["paperSafeClaim"],
            "",
            f"- Non-claim: {summary['nonClaim']}",
            f"- Upstream URL: {summary['upstreamUrl']}",
            f"- Snapshot HEAD: `{summary['snapshotHead']}`",
            f"- Host repo HEAD: `{summary['hostRepoHead']}`",
            f"- Baseline smoke pass: `{summary['baselineSmokePass']}`",
            f"- Post-change smoke pass: `{summary['postChangeSmokePass']}`",
            f"- Post-change case tag: `{summary['postChangeCaseTag']}`",
            f"- Artifact root: `{summary['artifactRoot']}`",
        ]
    )
    (artifact_root / "paper-safe-summary.md").write_text(markdown + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["baseline", "post-change"], required=True)
    parser.add_argument("--artifact-root", default=str(DEFAULT_ARTIFACT_ROOT))
    parser.add_argument("--snapshot-root", default=str(DEFAULT_SNAPSHOT_RELATIVE_PATH))
    parser.add_argument("--case-tag", default="")
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parents[1]
    artifact_root = (repo_root / args.artifact_root).resolve()
    snapshot_root = (repo_root / args.snapshot_root).resolve()
    artifact_root.mkdir(parents=True, exist_ok=True)

    python_exe = repo_root / ".venv" / "Scripts" / "python.exe"
    if not python_exe.is_file():
        raise FileNotFoundError(f"expected Python runtime at {python_exe}")

    provenance_path = artifact_root / "provenance.json"
    if not provenance_path.exists():
        write_json(provenance_path, build_provenance(repo_root, snapshot_root))

    env = os.environ.copy()
    env["PYTHONPATH"] = str(snapshot_root)
    env["NPC_LLM_PROVIDER_ORDER"] = "deterministic"
    env["PYTHONIOENCODING"] = "utf-8"
    if args.case_tag:
        env["NPC_BRAIN_FASTAPI_CASE_TAG"] = args.case_tag

    import_probe = run_command(
        [str(python_exe), "-c", "import fastapi, json; print(json.dumps({'version': fastapi.__version__, 'modulePath': fastapi.__file__}, ensure_ascii=False))"],
        cwd=repo_root,
        env=env,
    )
    smoke = run_command([str(python_exe), "-m", "app.http_smoke_test"], cwd=repo_root, env=env)
    endpoint_excerpt = run_command([str(python_exe), "-c", ENDPOINT_EXCERPT_SNIPPET], cwd=repo_root, env=env)

    for label, result in (
        ("import probe", import_probe),
        ("http smoke", smoke),
        ("endpoint excerpt", endpoint_excerpt),
    ):
        require_success(result, label)

    payload = {
        "mode": args.mode,
        "capturedAt": datetime.now(timezone.utc).isoformat(),
        "caseTag": args.case_tag or None,
        "commands": {
            "importProbe": import_probe,
            "httpSmoke": smoke,
            "endpointExcerpt": endpoint_excerpt,
        },
        "endpointExcerpt": json.loads(endpoint_excerpt["stdout"]),
    }
    write_json(artifact_root / f"{args.mode}.json", payload)
    append_command_log(artifact_root / "commands.log", args.mode, [import_probe, smoke, endpoint_excerpt])
    maybe_build_summary(artifact_root)
    refresh_hash_manifest(artifact_root)


if __name__ == "__main__":
    main()
