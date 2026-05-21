"""Smoke test for run_evidence_vector_smoke_gate (SANGUO-RAGOPS-0302).

Builds the same fixture lake as M2/M3 smoke tests, promotes the evidence
card to ``accepted`` so the exporter has at least one card record, then
runs the smoke gate in mock provider mode and asserts:

* upsert manifest is generated and entries deduped by policy dedupe keys
* probe finds the expected record (probeOk=True)
* rollback manifest contains delete-by-recordId list
* production namespace (ending with -prod) is blocked without explicit
  --allow-production-namespace
* provider not in policy allowlist is rejected (mock is allowed only as
  an exception)
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backfill_evidence_to_postgres_smoke_test import _build_fixtures  # noqa: E402
from run_evidence_vector_smoke_gate import DEFAULT_POLICY_PATH, run_gate  # noqa: E402


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


def _prepare_lake(tmp: Path) -> tuple[Path, Path]:
    manifest, _ = _build_fixtures(tmp)
    # Promote evidence card to accepted so the exporter has a non-empty
    # evidence_card set.
    card_path = tmp / "sources/3kweb/evidence-cards/0001/cards-r1.jsonl"
    rows = [json.loads(line) for line in card_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    for row in rows:
        row["reviewStatus"] = "accepted"
    card_path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
        encoding="utf-8",
    )
    manifest_path = tmp / "manifest.json"
    payload = {
        "schemaVersion": "evidence-manifest.v0.1",
        "runId": manifest.run_id,
        "lane": manifest.lane,
        "generatedAt": manifest.generated_at,
        "updatedAt": manifest.updated_at,
        "canonicalWrites": manifest.canonical_writes,
        "policyRefs": manifest.policy_refs,
        "inputFingerprint": manifest.input_fingerprint,
        "fileCount": manifest.file_count,
        "files": [],  # filled below
        "telemetry": manifest.telemetry,
        "lifecycle": manifest.lifecycle,
        "summary": manifest.summary,
    }
    payload["files"] = [
        {
            "artifactType": entry.artifact_type,
            "sourceId": entry.source_id,
            "shardId": entry.shard_id,
            "roundId": entry.round_id,
            "corpusId": entry.corpus_id,
            "layerId": entry.layer_id,
            "artifactUri": entry.artifact_uri,
            "path": entry.path,
            "sha256": entry.sha256,
            "size": entry.size,
            "createdAt": entry.created_at,
            "compression": entry.compression,
            "retentionTier": entry.retention_tier,
            "bodyStart": entry.body_start,
            "bodyEnd": entry.body_end,
            "linkedResidualProposalId": entry.linked_residual_proposal_id,
        }
        for entry in manifest.files
    ]
    manifest_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest_path, tmp


def test_gate_happy_path() -> None:
    with tempfile.TemporaryDirectory(dir=_temp_parent()) as tmp:
        manifest_path, lake = _prepare_lake(Path(tmp))
        report = run_gate(
            manifest_path=manifest_path,
            lake_root=lake,
            policy_path=DEFAULT_POLICY_PATH,
            namespace_override=None,
            provider="mock",
            top_k_override=5,
            batch_size_override=10,
            allow_production_namespace=False,
        )
        _expect("gate ok=True", report["ok"] is True)
        _expect("namespace ends with -smoke + runId", report["namespace"].startswith("sanguo-rag-evidence-smoke-"))
        _expect("upsert manifest has entries", report["upsertManifest"]["entryCount"] > 0)
        _expect("rollback manifest has deleteByRecordIds", len(report["rollbackManifest"]["deleteByRecordIds"]) > 0)
        _expect("probe matchCount >= 1", report["probe"]["matchCount"] >= 1)
        _expect("expected record appears in probe", report["probe"]["expectedRecordId"] in report["probe"]["matchedRecordIds"])
        _expect("canonicalWrites carried from manifest", report["canonicalWrites"] is False)


def test_production_namespace_blocked() -> None:
    with tempfile.TemporaryDirectory(dir=_temp_parent()) as tmp:
        manifest_path, lake = _prepare_lake(Path(tmp))
        try:
            run_gate(
                manifest_path=manifest_path,
                lake_root=lake,
                policy_path=DEFAULT_POLICY_PATH,
                namespace_override="sanguo-rag-evidence-prod",
                provider="mock",
                top_k_override=None,
                batch_size_override=None,
                allow_production_namespace=False,
            )
        except SystemExit:
            _expect("production namespace rejected by default", True)
            return
    _expect("production namespace rejected by default", False)


def test_unknown_provider_rejected() -> None:
    with tempfile.TemporaryDirectory(dir=_temp_parent()) as tmp:
        manifest_path, lake = _prepare_lake(Path(tmp))
        try:
            run_gate(
                manifest_path=manifest_path,
                lake_root=lake,
                policy_path=DEFAULT_POLICY_PATH,
                namespace_override=None,
                provider="not-in-allowlist",
                top_k_override=None,
                batch_size_override=None,
                allow_production_namespace=False,
            )
        except SystemExit:
            _expect("unknown provider rejected", True)
            return
    _expect("unknown provider rejected", False)


def test_dedupe_manifest_keys_match_policy() -> None:
    with tempfile.TemporaryDirectory(dir=_temp_parent()) as tmp:
        manifest_path, lake = _prepare_lake(Path(tmp))
        report = run_gate(
            manifest_path=manifest_path,
            lake_root=lake,
            policy_path=DEFAULT_POLICY_PATH,
            namespace_override=None,
            provider="mock",
            top_k_override=None,
            batch_size_override=None,
            allow_production_namespace=False,
        )
        # policy default is ['namespace', 'id', 'sha256']
        _expect(
            "dedupe key fields read from policy",
            report["upsertManifest"]["dedupeKeyFields"] == ["namespace", "id", "sha256"],
        )


def main() -> int:
    tests = [
        test_gate_happy_path,
        test_production_namespace_blocked,
        test_unknown_provider_rejected,
        test_dedupe_manifest_keys_match_policy,
    ]
    for test in tests:
        test()
    print(f"[PASS] {len(tests)} run_evidence_vector_smoke_gate smoke tests")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
