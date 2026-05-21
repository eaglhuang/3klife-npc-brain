"""Validate the evidence backfill against real extracted/* JSONL data.

This is a read-only harness used to confirm the M2 coercion + schema
can ingest real Sanguo-RAG canonical artifacts without modification.
It runs the backfill in ``mode=postgres`` + ``dry_run=True`` against a
synthesised manifest that points at on-disk JSONL files. No bytes are
written to PostgreSQL; no JSONL artifact is modified.

Usage::

    python -B pipelines/sanguo-rag/validate_real_data_backfill.py \
        --output local/cutover-evidence/real-data-validation/report.json
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
from evidence_manifest import EvidenceManifest, validate_manifest  # noqa: E402
from evidence_repository import RepositorySettings, RetryPolicy  # noqa: E402


REAL_SOURCES = [
    {
        "artifactType": "evidence-card",
        "sourceId": "romance-canon",
        "relative": "artifacts/data-pipeline/sanguo-rag/extracted/events/events.jsonl",
    },
    {
        "artifactType": "evidence-card",
        "sourceId": "generic-battle-candidates",
        "relative": "artifacts/data-pipeline/sanguo-rag/extracted/events/generic-battle-candidates.jsonl",
    },
    {
        "artifactType": "evidence-card",
        "sourceId": "female-interaction-candidates",
        "relative": "artifacts/data-pipeline/sanguo-rag/extracted/events/female-interaction-candidates.jsonl",
    },
]


def _sha256_file(path: Path) -> tuple[str, int]:
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


def _build_synthetic_manifest(run_id: str) -> dict[str, Any]:
    files: list[dict[str, Any]] = []
    fingerprint_input: list[str] = []
    for entry in REAL_SOURCES:
        path = REPO / entry["relative"]
        sha256, size = _sha256_file(path)
        if not sha256:
            continue
        files.append({
            "artifactType": entry["artifactType"],
            "sourceId": entry["sourceId"],
            "shardId": None,
            "roundId": None,
            "corpusId": None,
            "layerId": None,
            "artifactUri": f"atm://lake/{run_id}/sources/{entry['sourceId']}/evidence-cards/0001/{Path(entry['relative']).name}",
            "path": entry["relative"],
            "sha256": sha256,
            "size": size,
            "createdAt": "2026-05-21T00:00:00+00:00",
            "compression": {"format": "none", "level": None, "uncompressedSize": None},
            "retentionTier": "hot",
            "bodyStart": None,
            "bodyEnd": None,
            "linkedResidualProposalId": None,
        })
        fingerprint_input.append(sha256)
    fingerprint = hashlib.sha256("\n".join(fingerprint_input).encode("utf-8")).hexdigest()
    payload = {
        "schemaVersion": "evidence-manifest.v0.1",
        "runId": run_id,
        "lane": "real-data-validation",
        "generatedAt": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "updatedAt": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "canonicalWrites": True,
        "policyRefs": ["data/sanguo/policies/policy-artifact-lake-layout.json"],
        "inputFingerprint": {
            "sha256": fingerprint,
            "fileCount": len(files),
            "files": [{"path": entry["path"], "sha256": entry["sha256"], "size": entry["size"]} for entry in files],
        },
        "fileCount": len(files),
        "files": files,
        "telemetry": {},
        "lifecycle": {},
        "summary": {"runProfile": "real-data-validation", "status": "succeeded"},
    }
    validate_manifest(payload)
    return payload


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate backfill against real extracted/ JSONL.")
    parser.add_argument("--output", default="")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    run_id = "real-data-validation-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    manifest_payload = _build_synthetic_manifest(run_id)
    manifest = EvidenceManifest.from_dict(manifest_payload)
    settings = RepositorySettings(
        mode="postgres",
        jsonl_root=REPO,
        postgres_dsn="postgresql://example.invalid/sanguo_rag",
        dry_run=True,
        retry=RetryPolicy(max_attempts=1, backoff_seconds=0.0),
    )
    report = backfill(manifest, settings, lake_root=REPO)
    summary = {
        "schemaVersion": "real-data-validation-report.v0.1",
        "generatedAt": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "runId": run_id,
        "manifestSha256": hashlib.sha256(
            json.dumps(manifest_payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest(),
        "manifestFileCount": manifest.file_count,
        "totalSkippedRows": len(report["skipped"]),
        "tables": [
            {
                "table": item["table"],
                "jsonlRowCount": item["jsonlRowCount"],
                "pgRequested": item["pgRequested"],
                "parityOk": item["parityOk"],
            }
            for item in report["parity"]
        ],
        "ok": report["ok"],
    }
    text = json.dumps(summary, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
        print(f"[validate_real_data_backfill] wrote {out}; ok={summary['ok']}")
    else:
        print(text, end="")
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
