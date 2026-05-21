"""Evidence manifest emission and resume validation helper for the convergence loop.

SANGUO-RAGOPS-0603. Thin adapter that:

1. **Emits** an evidence manifest at the end of a convergence run, collecting
   sha256 + size for each key output artifact (evidence-cards, scoreboard,
   run summary).
2. **Scans** a prior run's manifest on startup to detect hash mismatches,
   missing files, or canonicalWrites drift before the new run begins.

Design constraints
------------------
* No source-id, filename, general-id, or provider string is hardcoded here.
  Artifact source IDs are derived from the round/run IDs emitted by the runner.
* Manifest emission is opt-in (same SANGUO_RAG_CONVERGENCE_REPO_ENABLED gate);
  it also works standalone when called from no-write / dry-run mode because it
  only reads existing files and writes a metadata JSON (no JSONL modification).
* If emission fails (e.g. file not found), the error is logged and the run
  completes normally — manifest errors must not abort the pipeline.
"""

from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from evidence_manifest import (  # noqa: E402
    EvidenceManifest,
    ManifestEntry,
    ResumeScanReport,
    dump_manifest,
    load_manifest,
    scan_manifest_for_resume,
    validate_manifest,
)

__all__ = [
    "build_convergence_manifest",
    "write_convergence_manifest",
    "scan_prior_convergence_manifest",
    "ConvergenceManifestError",
]


class ConvergenceManifestError(RuntimeError):
    """Non-fatal manifest error; caller should log and continue."""


# ---------------------------------------------------------------------------
# Artifact type mapping
# ---------------------------------------------------------------------------

# Maps final_paths keys to (artifact_type, source_id_suffix)
# source_id = "{run_id}-{suffix}" gives stable, non-hardcoded IDs
_PATH_KEY_TO_ARTIFACT: dict[str, tuple[str, str]] = {
    "externalCardsPath": ("evidence-card", "external-cards"),
    "globalCandidateCardsPath": ("evidence-card", "global-candidate-cards"),
    "scoreboardJsonPath": ("scoreboard", "scoreboard"),
    "eventsOutputPath": ("run", "events-output"),
    "progressPath": ("run", "progress"),
}

# Human-friendly keys to include in the manifest (order preserved)
_MANIFEST_KEYS = list(_PATH_KEY_TO_ARTIFACT.keys())


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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


def _utc_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _repo_relative(path: Path, repo_root: Path) -> str:
    try:
        return str(path.resolve().relative_to(repo_root.resolve())).replace("\\", "/")
    except ValueError:
        return str(path.resolve()).replace("\\", "/")


def _resolve_path(path_text: str, repo_root: Path) -> Path:
    candidate = Path(path_text)
    if candidate.is_absolute():
        return candidate
    return (repo_root / path_text).resolve()


# ---------------------------------------------------------------------------
# Manifest builder
# ---------------------------------------------------------------------------

def build_convergence_manifest(
    *,
    run_id: str,
    final_paths: dict[str, Any],
    summary_payload: dict[str, Any],
    repo_root: Path,
) -> EvidenceManifest:
    """Build an EvidenceManifest from the convergence loop's final_paths dict.

    Only files that exist on disk are included in the manifest. Missing files
    are silently skipped (they are common on dry-run / no-write runs).

    Parameters
    ----------
    run_id:
        The convergence run ID (used as the manifest runId and as part of
        each artifactUri).
    final_paths:
        The ``final_paths`` dict from ``main()`` (keys from baseline output).
    summary_payload:
        The full run summary dict (for stop_reason, dryRun, profile).
    repo_root:
        Repo root for resolving relative paths and computing relative paths
        in artifactUri.
    """
    now = _utc_iso()
    entries: list[ManifestEntry] = []
    fingerprint_inputs: list[str] = []

    seen_paths: set[str] = set()
    for key in _MANIFEST_KEYS:
        artifact_type, source_suffix = _PATH_KEY_TO_ARTIFACT[key]
        path_text = str(final_paths.get(key) or "").strip()
        if not path_text:
            continue
        resolved = _resolve_path(path_text, repo_root)
        resolved_str = str(resolved.resolve())
        if resolved_str in seen_paths or not resolved.exists():
            continue
        seen_paths.add(resolved_str)
        sha256, size = _sha256_and_size(resolved)
        if not sha256:
            continue
        fingerprint_inputs.append(sha256)
        rel_path = _repo_relative(resolved, repo_root)
        source_id = f"{run_id}-{source_suffix}"
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
                shard_id=None,
                round_id=None,
                corpus_id=None,
                layer_id=None,
                compression={"format": "none", "level": None, "uncompressedSize": None},
                retention_tier="hot",
                body_start=None,
                body_end=None,
                linked_residual_proposal_id=None,
            )
        )

    fp_sha256 = hashlib.sha256(
        "\n".join(fingerprint_inputs).encode("utf-8")
    ).hexdigest()
    input_fingerprint = {
        "sha256": fp_sha256,
        "fileCount": len(entries),
        "files": [
            {"path": e.path, "sha256": e.sha256, "size": e.size}
            for e in entries
        ],
    }

    stop_reason = str(summary_payload.get("stopReason") or "completed")
    dry_run = bool(summary_payload.get("dryRun"))
    manifest = EvidenceManifest(
        run_id=run_id,
        generated_at=now,
        updated_at=now,
        canonical_writes=False,
        input_fingerprint=input_fingerprint,
        files=entries,
        lane="convergence-loop",
        policy_refs=["data/sanguo/policies/policy-convergence-evidence-repo.json"],
        telemetry={
            "roundsExecuted": int(summary_payload.get("roundsExecuted") or 0),
            "stopReason": stop_reason,
            "dryRun": dry_run,
            "commandCount": int((summary_payload.get("metrics") or {}).get("commandCount") or 0),
            "commandFailureCount": int(
                (summary_payload.get("metrics") or {}).get("commandFailureCount") or 0
            ),
        },
        lifecycle={},
        summary={
            "runProfile": "convergence-loop",
            "status": stop_reason,
        },
    )
    return manifest


def write_convergence_manifest(
    manifest: EvidenceManifest,
    *,
    run_root: Path,
) -> Path:
    """Validate and write the manifest to ``run_root/evidence-manifest.json``.

    Returns the path written. Raises ``ConvergenceManifestError`` on failure.
    """
    out_path = run_root / "evidence-manifest.json"
    try:
        # Validate before writing
        payload = _manifest_to_payload(manifest)
        validate_manifest(payload)
        dump_manifest(manifest, out_path)
        return out_path
    except Exception as exc:
        raise ConvergenceManifestError(
            f"failed to write convergence manifest: {exc}"
        ) from exc


def _manifest_to_payload(manifest: EvidenceManifest) -> dict[str, Any]:
    """Convert EvidenceManifest to the raw dict format expected by validate_manifest."""
    return {
        "schemaVersion": manifest.schema_version,
        "runId": manifest.run_id,
        "lane": manifest.lane or "",
        "generatedAt": manifest.generated_at,
        "updatedAt": manifest.updated_at,
        "canonicalWrites": manifest.canonical_writes,
        "policyRefs": list(manifest.policy_refs),
        "inputFingerprint": dict(manifest.input_fingerprint),
        "fileCount": manifest.file_count,
        "files": [
            {
                "artifactType": e.artifact_type,
                "sourceId": e.source_id,
                "shardId": e.shard_id,
                "roundId": e.round_id,
                "corpusId": e.corpus_id,
                "layerId": e.layer_id,
                "artifactUri": e.artifact_uri,
                "path": e.path,
                "sha256": e.sha256,
                "size": e.size,
                "createdAt": e.created_at,
                "compression": e.compression or {"format": "none", "level": None, "uncompressedSize": None},
                "retentionTier": e.retention_tier or "hot",
                "bodyStart": e.body_start,
                "bodyEnd": e.body_end,
                "linkedResidualProposalId": e.linked_residual_proposal_id,
            }
            for e in manifest.files
        ],
        "telemetry": dict(manifest.telemetry),
        "lifecycle": dict(manifest.lifecycle),
        "summary": dict(manifest.summary),
    }


# ---------------------------------------------------------------------------
# Resume scanner
# ---------------------------------------------------------------------------

def scan_prior_convergence_manifest(
    manifest_path: Path,
    *,
    repo_root: Path,
    verify_sha256: bool = True,
) -> ResumeScanReport:
    """Load and scan a prior convergence run's manifest for resume safety.

    Parameters
    ----------
    manifest_path:
        Path to the prior run's ``evidence-manifest.json``.
    repo_root:
        Used as ``lake_root`` for resolving relative artifact paths.
    verify_sha256:
        If True, hash each present file and report mismatches.
        Default True; set False for faster offline checks.

    Returns
    -------
    ResumeScanReport
        ``.ok`` is True when no missing, duplicate, hash-mismatch, or
        unexpected entries are detected.
    """
    if not manifest_path.exists():
        raise ConvergenceManifestError(
            f"prior convergence manifest not found: {manifest_path}"
        )
    manifest = load_manifest(manifest_path)
    return scan_manifest_for_resume(
        manifest,
        lake_root=repo_root,
        verify_sha256=verify_sha256,
    )
