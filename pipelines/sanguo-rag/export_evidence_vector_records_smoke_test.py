"""Smoke test for export_evidence_vector_records (SANGUO-RAGOPS-0301).

Builds a small lake with anchor-passage + evidence-card JSONL plus
forbidden artifact types (raw-page, evidence-seed, proposal). Runs the
exporter and asserts:

* only anchor_passage and evidence_card records are emitted
* evidence-card records with reviewStatus=candidate are dropped by
  default (require --allow-candidate-evidence to include)
* metadata covers the required fields (recordType, runId, sourceId,
  sourceFamily, sourceLayer, generalIds, locator, textHash,
  anchorVerdict, canonicalWrites, payloadUri)
* deterministic sha256 is identical across two consecutive runs
* namespace defaults to ``-smoke`` when canonicalWrites=False
* sorted by recordId so output is deterministic
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
from export_evidence_vector_records import (  # noqa: E402
    EVIDENCE_CARD_HARDENED_STATUSES,
    SCHEMA_VERSION,
    export_from_manifest,
)


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


def test_exporter_emits_only_allowed_record_types() -> None:
    with tempfile.TemporaryDirectory(dir=_temp_parent()) as tmp:
        lake = Path(tmp)
        manifest, _ = _build_fixtures(lake)
        report = export_from_manifest(manifest, lake_root=lake)
        record_types = {r["recordType"] for r in report["anchorRecords"]} | {r["recordType"] for r in report["evidenceRecords"]}
        _expect("only allowed record types emitted", record_types <= {"anchor_passage", "evidence_card"})
        _expect("no raw seed in output", all(r["recordType"] != "evidence_seed" for r in report["anchorRecords"] + report["evidenceRecords"]))


def test_default_drops_candidate_evidence() -> None:
    with tempfile.TemporaryDirectory(dir=_temp_parent()) as tmp:
        lake = Path(tmp)
        manifest, _ = _build_fixtures(lake)
        report_default = export_from_manifest(manifest, lake_root=lake)
        _expect(
            "evidence-card with reviewStatus=candidate is dropped by default",
            report_default["recordCounts"]["evidence_card"] == 0,
        )
        # Modify the fixture card to accepted status by re-reading & re-writing
        card_path = lake / "sources/3kweb/evidence-cards/0001/cards-r1.jsonl"
        rows = [json.loads(line) for line in card_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        for row in rows:
            row["reviewStatus"] = "accepted"
        card_path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")
        report_accepted = export_from_manifest(manifest, lake_root=lake)
        _expect(
            "evidence-card with reviewStatus=accepted is included",
            report_accepted["recordCounts"]["evidence_card"] == 1,
        )


def test_metadata_fields_required() -> None:
    with tempfile.TemporaryDirectory(dir=_temp_parent()) as tmp:
        lake = Path(tmp)
        manifest, _ = _build_fixtures(lake)
        report = export_from_manifest(manifest, lake_root=lake)
        required = {
            "recordType",
            "runId",
            "sourceId",
            "sourceFamily",
            "sourceLayer",
            "generalIds",
            "locator",
            "textHash",
            "anchorVerdict",
            "canonicalWrites",
            "payloadUri",
        }
        for record in report["anchorRecords"]:
            missing = required - record["metadata"].keys()
            _expect(f"anchor record metadata complete ({record['recordId']})", not missing)
            _expect(
                f"anchor record canonicalWrites carried ({record['recordId']})",
                record["metadata"]["canonicalWrites"] == manifest.canonical_writes,
            )
        _expect("schemaVersion stamped on each record", all(r["schemaVersion"] == SCHEMA_VERSION for r in report["anchorRecords"]))


def test_deterministic_sha_across_runs() -> None:
    with tempfile.TemporaryDirectory(dir=_temp_parent()) as tmp:
        lake = Path(tmp)
        manifest, _ = _build_fixtures(lake)
        report_a = export_from_manifest(manifest, lake_root=lake)
        report_b = export_from_manifest(manifest, lake_root=lake)
        _expect(
            "anchor sha256 deterministic",
            report_a["deterministicSha256"]["anchor_passage"] == report_b["deterministicSha256"]["anchor_passage"],
        )
        _expect(
            "combined sha256 deterministic",
            report_a["deterministicSha256"]["combined"] == report_b["deterministicSha256"]["combined"],
        )


def test_namespace_defaults_smoke_when_not_canonical() -> None:
    with tempfile.TemporaryDirectory(dir=_temp_parent()) as tmp:
        lake = Path(tmp)
        manifest, _ = _build_fixtures(lake)
        report = export_from_manifest(manifest, lake_root=lake, namespace_prefix="sanguo-rag-evidence")
        _expect("namespace ends in -smoke when canonicalWrites=False", report["namespace"].endswith("-smoke"))


def test_sorted_by_record_id() -> None:
    with tempfile.TemporaryDirectory(dir=_temp_parent()) as tmp:
        lake = Path(tmp)
        manifest, _ = _build_fixtures(lake)
        report = export_from_manifest(manifest, lake_root=lake)
        anchor_ids = [r["recordId"] for r in report["anchorRecords"]]
        _expect("anchor records sorted by recordId", anchor_ids == sorted(anchor_ids))


def main() -> int:
    tests = [
        test_exporter_emits_only_allowed_record_types,
        test_default_drops_candidate_evidence,
        test_metadata_fields_required,
        test_deterministic_sha_across_runs,
        test_namespace_defaults_smoke_when_not_canonical,
        test_sorted_by_record_id,
    ]
    for test in tests:
        test()
    print(f"[PASS] {len(tests)} export_evidence_vector_records smoke tests")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
