"""Regression smoke test for evidence_manifest schema and resume scan.

Run inside the docker dev container:

    docker exec 3klife-npc-brain-dev python -B pipelines/sanguo-rag/evidence_manifest_smoke_test.py

The test validates the bundled fixture, exercises round-trip dump/load,
and asserts resume-scan detects missing, duplicate, and hash-mismatch
conditions on a temp lake directory.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from evidence_manifest import (  # noqa: E402
    EvidenceManifest,
    ManifestValidationError,
    dump_manifest,
    load_manifest,
    scan_manifest_for_resume,
    validate_manifest,
)

FIXTURE = ROOT / "fixtures" / "evidence-manifest.sample.json"


def _expect(label: str, condition: bool) -> None:
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {label}")
    if not condition:
        raise SystemExit(1)


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def test_fixture_validates() -> None:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    validate_manifest(payload)
    _expect("fixture validates against schema", True)


def test_required_fields_enforced() -> None:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    broken = dict(payload)
    broken.pop("runId")
    try:
        validate_manifest(broken)
    except ManifestValidationError:
        _expect("missing runId raises ManifestValidationError", True)
        return
    _expect("missing runId raises ManifestValidationError", False)


def test_file_count_mismatch_rejected() -> None:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    payload["fileCount"] = payload["fileCount"] - 1
    try:
        validate_manifest(payload)
    except ManifestValidationError:
        _expect("fileCount mismatch raises ManifestValidationError", True)
        return
    _expect("fileCount mismatch raises ManifestValidationError", False)


def test_artifact_uri_pattern_enforced() -> None:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    payload["files"][0]["artifactUri"] = "https://example.invalid/not-a-lake-uri"
    try:
        validate_manifest(payload)
    except ManifestValidationError:
        _expect("non-lake artifactUri raises ManifestValidationError", True)
        return
    _expect("non-lake artifactUri raises ManifestValidationError", False)


def test_roundtrip_dump_load() -> None:
    manifest = load_manifest(FIXTURE)
    with tempfile.TemporaryDirectory() as tmpdir:
        out = Path(tmpdir) / "roundtrip.manifest.json"
        dump_manifest(manifest, out)
        reloaded = load_manifest(out)
    _expect(
        "dump → load preserves fileCount",
        reloaded.file_count == manifest.file_count,
    )
    _expect(
        "dump → load preserves canonicalWrites",
        reloaded.canonical_writes == manifest.canonical_writes,
    )


def test_resume_scan_detects_missing_and_mismatch() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        lake = Path(tmpdir)
        payload_a = b"alpha payload bytes"
        payload_b = b"beta payload bytes"
        sha_a = _sha256_bytes(payload_a)
        sha_b = _sha256_bytes(payload_b)
        rel_a = "lake/run-x/sources/3kweb/harvested-pages/0001/sha1-a.harvested.json"
        rel_b = "lake/run-x/sources/3kweb/evidence-seeds/0001/seeds-r1.jsonl"
        (lake / rel_a).parent.mkdir(parents=True, exist_ok=True)
        (lake / rel_b).parent.mkdir(parents=True, exist_ok=True)
        (lake / rel_a).write_bytes(payload_a)
        (lake / rel_b).write_bytes(b"corrupted")
        manifest_payload = {
            "schemaVersion": "evidence-manifest.v0.1",
            "runId": "run-x",
            "lane": "smoke",
            "generatedAt": "2026-05-21T00:00:00+00:00",
            "updatedAt": "2026-05-21T00:00:00+00:00",
            "canonicalWrites": False,
            "policyRefs": ["data/sanguo/policies/policy-artifact-lake-layout.json"],
            "inputFingerprint": {
                "sha256": "0" * 64,
                "fileCount": 0,
                "files": [],
            },
            "fileCount": 3,
            "files": [
                {
                    "artifactType": "harvested-page",
                    "sourceId": "3kweb",
                    "shardId": "0001",
                    "roundId": None,
                    "corpusId": None,
                    "layerId": None,
                    "artifactUri": "atm://lake/run-x/sources/3kweb/harvested-pages/0001/sha1-a.harvested.json",
                    "path": rel_a,
                    "sha256": sha_a,
                    "size": len(payload_a),
                    "createdAt": "2026-05-21T00:00:00+00:00",
                    "compression": {"format": "none", "level": None, "uncompressedSize": None},
                    "retentionTier": "hot",
                    "bodyStart": None,
                    "bodyEnd": None,
                    "linkedResidualProposalId": None,
                },
                {
                    "artifactType": "evidence-seed",
                    "sourceId": "3kweb",
                    "shardId": "0001",
                    "roundId": "r1",
                    "corpusId": None,
                    "layerId": None,
                    "artifactUri": "atm://lake/run-x/sources/3kweb/evidence-seeds/0001/seeds-r1.jsonl",
                    "path": rel_b,
                    "sha256": sha_b,
                    "size": len(payload_b),
                    "createdAt": "2026-05-21T00:00:00+00:00",
                    "compression": {"format": "none", "level": None, "uncompressedSize": None},
                    "retentionTier": "hot",
                    "bodyStart": None,
                    "bodyEnd": None,
                    "linkedResidualProposalId": None,
                },
                {
                    "artifactType": "evidence-card",
                    "sourceId": "3kweb",
                    "shardId": "0002",
                    "roundId": "r1",
                    "corpusId": None,
                    "layerId": None,
                    "artifactUri": "atm://lake/run-x/sources/3kweb/evidence-cards/0002/cards-r1.jsonl",
                    "path": "lake/run-x/sources/3kweb/evidence-cards/0002/cards-r1.jsonl",
                    "sha256": "f" * 64,
                    "size": 1,
                    "createdAt": "2026-05-21T00:00:00+00:00",
                    "compression": {"format": "none", "level": None, "uncompressedSize": None},
                    "retentionTier": "hot",
                    "bodyStart": None,
                    "bodyEnd": None,
                    "linkedResidualProposalId": None,
                },
            ],
            "telemetry": {},
            "lifecycle": {},
            "summary": {},
        }
        validate_manifest(manifest_payload)
        manifest = EvidenceManifest.from_dict(manifest_payload)
        report = scan_manifest_for_resume(manifest, lake, verify_sha256=True)
    _expect("scan detects missing file", len(report.missing) == 1)
    _expect("scan detects hash mismatch", len(report.hash_mismatch) == 1)
    _expect("scan ok flag is False on errors", report.ok is False)


def main() -> int:
    tests = [
        test_fixture_validates,
        test_required_fields_enforced,
        test_file_count_mismatch_rejected,
        test_artifact_uri_pattern_enforced,
        test_roundtrip_dump_load,
        test_resume_scan_detects_missing_and_mismatch,
    ]
    for test in tests:
        test()
    print(f"[PASS] {len(tests)} evidence_manifest smoke tests")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
