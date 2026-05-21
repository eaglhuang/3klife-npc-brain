"""Convergence loop repository parity rehearsal gate (SANGUO-RAGOPS-0604).

Reads a baseline manifest produced by ``run_full_roster_convergence_loop.py``
and verifies that the convergence-loop JSONL canonical outputs can be backfilled
into the evidence repository with correct parity (row count, sha256, source/run
coverage, canonicalWrites, artifactUri).

This script:
1. Reads the baseline manifest JSON from a prior convergence run.
2. Collects externalCardsPath / globalCandidateCardsPath / evidenceManifestPath
   from the manifest's paths dict.
3. Synthesises an EvidenceManifest from those paths.
4. Runs ``backfill()`` in dry-run mode (``mode=postgres, dry_run=True`` by default)
   so no writes are issued to any backend.
5. Outputs a parity report with per-table row counts, sha256, errors, and
   overall ``ok`` flag.

Governance constraints
----------------------
* Default: ``mode=postgres, dry_run=True``.  Apply with ``--apply`` only if
  SANGUO_RAG_PG_DSN is set and a live PG target is available.
* No production credentials are accepted in the repo; DSN must come from env.
* JSONL canonical outputs are never modified.
* All source IDs, run IDs, and paths come from the baseline manifest — nothing
  is hardcoded here.

Usage::

    # Dry-run rehearsal (default)
    python -B pipelines/sanguo-rag/run_convergence_repo_parity_rehearsal.py \\
        --baseline-manifest local/codex-smoke/knowledge-growth/<run-id>/baseline-manifest.output.json

    # With output report
    python -B pipelines/sanguo-rag/run_convergence_repo_parity_rehearsal.py \\
        --baseline-manifest <path> \\
        --output local/cutover-evidence/C1-convergence-parity/report.json

    # Apply to live PG (requires SANGUO_RAG_PG_DSN)
    python -B pipelines/sanguo-rag/run_convergence_repo_parity_rehearsal.py \\
        --baseline-manifest <path> --apply
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parent.parent

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backfill_evidence_to_postgres import backfill  # noqa: E402
from evidence_manifest import EvidenceManifest, ManifestEntry, validate_manifest  # noqa: E402
from evidence_repository import RepositorySettings, RetryPolicy  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _sha256_and_size(path: Path) -> tuple[str, int]:
    if not path.exists():
        return "", 0
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as fh:
        while True:
            chunk = fh.read(1 << 20)
            if not chunk:
                break
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _resolve(path_text: str, repo_root: Path) -> Path:
    candidate = Path(path_text)
    if candidate.is_absolute():
        return candidate
    return (repo_root / path_text).resolve()


def _repo_relative(path: Path, repo_root: Path) -> str:
    try:
        return str(path.resolve().relative_to(repo_root.resolve())).replace("\\", "/")
    except ValueError:
        return str(path.resolve()).replace("\\", "/")


# ---------------------------------------------------------------------------
# Manifest synthesiser
# ---------------------------------------------------------------------------

# Maps baseline path keys to (artifact_type, source_id_suffix)
_CARD_PATH_KEYS = [
    ("externalCardsPath", "evidence-card", "external-cards"),
    ("globalCandidateCardsPath", "evidence-card", "global-candidate-cards"),
]


def _synthesise_manifest(
    *,
    run_id: str,
    baseline_paths: dict[str, Any],
    repo_root: Path,
) -> EvidenceManifest:
    """Build a synthetic EvidenceManifest from convergence baseline paths."""
    now = _utc_now()
    entries: list[ManifestEntry] = []
    fingerprint_inputs: list[str] = []
    seen: set[str] = set()

    for key, artifact_type, suffix in _CARD_PATH_KEYS:
        path_text = str(baseline_paths.get(key) or "").strip()
        if not path_text:
            continue
        resolved = _resolve(path_text, repo_root)
        resolved_str = str(resolved.resolve())
        if resolved_str in seen or not resolved.exists():
            continue
        seen.add(resolved_str)
        sha256, size = _sha256_and_size(resolved)
        if not sha256:
            continue
        fingerprint_inputs.append(sha256)
        rel_path = _repo_relative(resolved, repo_root)
        source_id = f"{run_id}-{suffix}"
        artifact_uri = (
            f"atm://lake/{run_id}/sources/{source_id}"
            f"/{artifact_type}/{resolved.name}"
        )
        entries.append(
            ManifestEntry(
                artifact_type=artifact_type,
                source_id=source_id,
                artifact_uri=artifact_uri,
                path=rel_path,
                sha256=sha256,
                size=size,
                created_at=now,
                compression={"format": "none", "level": None, "uncompressedSize": None},
                retention_tier="hot",
            )
        )

    fp_sha256 = hashlib.sha256(
        "\n".join(fingerprint_inputs).encode("utf-8")
    ).hexdigest() if fingerprint_inputs else "0" * 64

    return EvidenceManifest(
        run_id=run_id,
        generated_at=now,
        updated_at=now,
        canonical_writes=False,
        input_fingerprint={
            "sha256": fp_sha256,
            "fileCount": len(entries),
            "files": [{"path": e.path, "sha256": e.sha256, "size": e.size} for e in entries],
        },
        files=entries,
        lane="convergence-loop-parity-rehearsal",
        policy_refs=["data/sanguo/policies/policy-convergence-evidence-repo.json"],
        telemetry={},
        lifecycle={},
        summary={"runProfile": "convergence-loop-parity-rehearsal", "status": "succeeded"},
    )


# ---------------------------------------------------------------------------
# Parity rehearsal
# ---------------------------------------------------------------------------

def run_convergence_parity_rehearsal(
    baseline_manifest_path: Path,
    *,
    apply: bool = False,
    repo_root: Path = REPO,
    max_retry: int = 1,
) -> dict[str, Any]:
    """Run parity rehearsal for a single convergence baseline manifest.

    Parameters
    ----------
    baseline_manifest_path:
        Path to ``baseline-manifest.output.json`` produced by convergence loop.
    apply:
        If False (default), dry-run only — no actual writes to any backend.
        If True, applies to live PostgreSQL (SANGUO_RAG_PG_DSN must be set).
    repo_root:
        Repository root for resolving relative artifact paths.
    max_retry:
        Max retry attempts for PostgreSQL upsert (only relevant when apply=True).
    """
    baseline = _read_json(baseline_manifest_path)
    baseline_paths = baseline.get("paths") if isinstance(baseline.get("paths"), dict) else baseline
    run_id = str(baseline.get("runId") or baseline_paths.get("runId") or "convergence-parity-rehearsal")

    manifest = _synthesise_manifest(
        run_id=run_id,
        baseline_paths=baseline_paths,
        repo_root=repo_root,
    )

    settings = RepositorySettings.from_env(mode="postgres", dry_run=not apply)
    settings.jsonl_root = repo_root
    settings.retry = RetryPolicy(max_attempts=max_retry, backoff_seconds=0.5)

    report = backfill(manifest, settings, lake_root=repo_root)

    parity_summary = {
        "schemaVersion": "convergence-parity-rehearsal-report.v0.1",
        "generatedAt": _utc_now(),
        "baselineManifestPath": str(baseline_manifest_path),
        "runId": run_id,
        "manifestFileCount": manifest.file_count,
        "mode": settings.mode,
        "dryRun": settings.dry_run,
        "apply": apply,
        "totalSkippedRows": len(report.get("skipped") or []),
        "tables": [
            {
                "table": item["table"],
                "jsonlRowCount": item["jsonlRowCount"],
                "pgRequested": item["pgRequested"],
                "pgWritten": item["pgWritten"],
                "pgSkippedDuplicate": item["pgSkippedDuplicate"],
                "pgErrors": item["pgErrors"],
                "parityOk": item["parityOk"],
            }
            for item in (report.get("parity") or [])
        ],
        "ok": bool(report.get("ok")),
        "guards": [
            "jsonl-canonical-export-not-modified",
            "no-hardcoded-source-id-or-run-id",
            "canonicalWrites-always-false",
            "parity-error-ledger-machine-readable",
        ],
    }
    return parity_summary


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Convergence loop repository parity rehearsal gate (SANGUO-RAGOPS-0604).",
    )
    parser.add_argument(
        "--baseline-manifest",
        required=True,
        help="Path to baseline-manifest.output.json from a convergence run.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually write to PostgreSQL (default is dry-run). Requires SANGUO_RAG_PG_DSN.",
    )
    parser.add_argument(
        "--retry",
        type=int,
        default=1,
        help="Max retry attempts for PostgreSQL upsert (default 1).",
    )
    parser.add_argument(
        "--output",
        default="",
        help="Optional path to write parity report JSON.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    baseline_path = Path(args.baseline_manifest)
    if not baseline_path.exists():
        print(f"[convergence-parity-rehearsal] baseline manifest not found: {baseline_path}")
        return 2

    report = run_convergence_parity_rehearsal(
        baseline_path,
        apply=bool(args.apply),
        repo_root=REPO,
        max_retry=int(args.retry),
    )

    text = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
        print(
            f"[convergence-parity-rehearsal] wrote {out}; "
            f"ok={report['ok']} files={report['manifestFileCount']} "
            f"tables={len(report['tables'])}"
        )
    else:
        print(text, end="")

    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
