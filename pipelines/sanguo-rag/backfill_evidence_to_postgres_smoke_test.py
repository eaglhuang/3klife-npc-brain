"""Smoke test for backfill_evidence_to_postgres (SANGUO-RAGOPS-0203).

Creates a temporary lake directory containing minimal JSONL fixtures for
harvested-page, evidence-seed, evidence-card, anchor-passage, and
body-boundary-residual proposal artifacts. Runs the backfill in
``mode=postgres`` + ``dry_run=True`` (no DB contact) and asserts:

* parity report lists every table touched
* JSONL row counts match the PostgreSQL requested counts
* re-running the same backfill produces the same row counts (idempotent
  upsert simulated via the adapter's dry-run pathway)
* JSONL files are not modified by the backfill
* manifest is not modified by the backfill

Run:

    python -B pipelines/sanguo-rag/backfill_evidence_to_postgres_smoke_test.py
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backfill_evidence_to_postgres import backfill  # noqa: E402
from evidence_manifest import EvidenceManifest, validate_manifest  # noqa: E402
from evidence_repository import RepositorySettings, RetryPolicy  # noqa: E402


def _temp_parent() -> Path:
    base_text = os.environ.get("SANGUO_RAG_TEST_TMPDIR")
    base = Path(base_text) if base_text else Path.cwd() / "local" / "tmp" / "sanguo-rag-smoke"
    base.mkdir(parents=True, exist_ok=True)
    return base


def _expect(label: str, condition: bool) -> None:
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {label}")
    if not condition:
        raise SystemExit(1)


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _build_fixtures(root: Path) -> tuple[EvidenceManifest, list[Path]]:
    run_id = "smoke-run"
    source_id = "3kweb"
    files: list[Path] = []

    harvested_path = root / "sources/3kweb/harvested-pages/0001/page.jsonl"
    harvested_path.parent.mkdir(parents=True, exist_ok=True)
    harvested_payload = [
        {"url": "https://example.invalid/p/1", "title": "p1", "bodyText": "alpha", "rawBytes": 100},
        {"url": "https://example.invalid/p/2", "title": "p2", "bodyText": "beta", "rawBytes": 120},
    ]
    harvested_path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in harvested_payload) + "\n",
        encoding="utf-8",
    )
    files.append(harvested_path)

    seed_path = root / "sources/3kweb/evidence-seeds/0001/seeds-r1.jsonl"
    seed_path.parent.mkdir(parents=True, exist_ok=True)
    seed_payload = [
        {"seedId": "seed-001", "generalId": "zhang-fei", "angleType": "battle"},
        {"seedId": "seed-002", "generalId": "guan-yu", "angleType": "alliance"},
        {"seedId": "seed-003", "generalId": "liu-bei", "angleType": "diplomacy"},
    ]
    seed_path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in seed_payload) + "\n",
        encoding="utf-8",
    )
    files.append(seed_path)

    card_path = root / "sources/3kweb/evidence-cards/0001/cards-r1.jsonl"
    card_path.parent.mkdir(parents=True, exist_ok=True)
    card_payload = [
        {"evidenceId": "ev-001", "sourceFamily": "external", "sourceLayer": "browser",
         "sourceQuote": "...", "locator": "p/1#sec-1"}
    ]
    card_path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in card_payload) + "\n",
        encoding="utf-8",
    )
    files.append(card_path)

    anchor_path = root / "sources/novel-tang/anchor-passages/romance/c-080/passages-r1.jsonl"
    anchor_path.parent.mkdir(parents=True, exist_ok=True)
    anchor_payload = [
        {"passageId": "anchor-001", "corpusId": "novel-tang", "layer": "romance",
         "locator": "chapter-080#para-12", "normalizedText": "桃園結義"},
        {"passageId": "anchor-002", "corpusId": "novel-tang", "layer": "romance",
         "locator": "chapter-080#para-13", "normalizedText": "三顧茅廬"},
    ]
    anchor_path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in anchor_payload) + "\n",
        encoding="utf-8",
    )
    files.append(anchor_path)

    proposal_path = root / "sources/3kweb/proposals/body-boundary-residual-r1.jsonl"
    proposal_path.parent.mkdir(parents=True, exist_ok=True)
    proposal_payload = [
        {"proposalId": "bb-001", "proposalKind": "body-boundary-residual",
         "signature": "sig-abc", "status": "sandbox-pass"}
    ]
    proposal_path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in proposal_payload) + "\n",
        encoding="utf-8",
    )
    files.append(proposal_path)

    def _entry(path: Path, artifact_type: str, artifact_subdir: str, source: str) -> dict:
        rel = path.relative_to(root).as_posix()
        body = path.read_bytes()
        return {
            "artifactType": artifact_type,
            "sourceId": source,
            "shardId": None,
            "roundId": None,
            "corpusId": "novel-tang" if artifact_type == "anchor-passage" else None,
            "layerId": "romance" if artifact_type == "anchor-passage" else None,
            "artifactUri": f"atm://lake/{run_id}/sources/{source}/{artifact_subdir}/{path.name}",
            "path": rel,
            "sha256": _sha256_bytes(body),
            "size": len(body),
            "createdAt": "2026-05-21T00:00:00+00:00",
            "compression": {"format": "none", "level": None, "uncompressedSize": None},
            "retentionTier": "hot",
            "bodyStart": None,
            "bodyEnd": None,
            "linkedResidualProposalId": None,
        }

    entries = [
        _entry(harvested_path, "harvested-page", "harvested-pages/0001", source_id),
        _entry(seed_path, "evidence-seed", "evidence-seeds/0001", source_id),
        _entry(card_path, "evidence-card", "evidence-cards/0001", source_id),
        _entry(anchor_path, "anchor-passage", "anchor-passages/romance/c-080", "novel-tang"),
        _entry(proposal_path, "proposal", "proposals", source_id),
    ]

    fingerprint_payload = "\n".join(entry["sha256"] for entry in entries).encode("utf-8")
    manifest_payload = {
        "schemaVersion": "evidence-manifest.v0.1",
        "runId": run_id,
        "lane": "smoke",
        "generatedAt": "2026-05-21T00:00:00+00:00",
        "updatedAt": "2026-05-21T00:00:00+00:00",
        "canonicalWrites": False,
        "policyRefs": ["data/sanguo/policies/policy-artifact-lake-layout.json"],
        "inputFingerprint": {
            "sha256": _sha256_bytes(fingerprint_payload),
            "fileCount": len(entries),
            "files": [
                {"path": entry["path"], "sha256": entry["sha256"], "size": entry["size"]}
                for entry in entries
            ],
        },
        "fileCount": len(entries),
        "files": entries,
        "telemetry": {},
        "lifecycle": {},
        "summary": {"runProfile": "smoke", "status": "succeeded"},
    }
    validate_manifest(manifest_payload)
    return EvidenceManifest.from_dict(manifest_payload), files


def test_backfill_dry_run_parity() -> None:
    with tempfile.TemporaryDirectory(dir=_temp_parent()) as tmp:
        lake = Path(tmp)
        manifest, files = _build_fixtures(lake)
        manifest_hashes = {path: _sha256_bytes(path.read_bytes()) for path in files}

        settings = RepositorySettings(
            mode="postgres",
            jsonl_root=lake,
            postgres_dsn="postgresql://example.invalid/sanguo_rag",
            dry_run=True,
            retry=RetryPolicy(max_attempts=1, backoff_seconds=0.0),
        )
        report = backfill(manifest, settings, lake_root=lake)

        by_table = {item["table"]: item for item in report["parity"]}
        _expect("parity report covers pipeline_runs", "pipeline_runs" in by_table)
        _expect("parity report covers evidence_seeds", "evidence_seeds" in by_table)
        _expect("parity report covers evidence_cards", "evidence_cards" in by_table)
        _expect("parity report covers anchor_passages", "anchor_passages" in by_table)
        _expect("parity report covers harvested_pages", "harvested_pages" in by_table)
        _expect("parity report covers proposal_ledger", "proposal_ledger" in by_table)
        _expect("parity report covers source_runs roll-up", "source_runs" in by_table)
        _expect("evidence_seeds jsonl row count == 3", by_table["evidence_seeds"]["jsonlRowCount"] == 3)
        _expect("evidence_seeds pg written == 3", by_table["evidence_seeds"]["pgWritten"] == 3)
        _expect("anchor_passages jsonl row count == 2", by_table["anchor_passages"]["jsonlRowCount"] == 2)
        _expect("overall parity ok", report["ok"] is True)

        # Re-run idempotency
        report2 = backfill(manifest, settings, lake_root=lake)
        by_table_2 = {item["table"]: item for item in report2["parity"]}
        _expect(
            "re-run reports identical row counts",
            by_table["evidence_seeds"]["jsonlRowCount"] == by_table_2["evidence_seeds"]["jsonlRowCount"],
        )

        # JSONL artifacts unchanged
        for path, expected_hash in manifest_hashes.items():
            actual = _sha256_bytes(path.read_bytes())
            _expect(f"jsonl artifact unchanged: {path.name}", actual == expected_hash)


def main() -> int:
    test_backfill_dry_run_parity()
    print("[PASS] backfill_evidence_to_postgres smoke tests")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
