"""Smoke test for dual_write_parity_gate (SANGUO-RAGOPS-0204).

Builds a small fixture lake, runs the parity gate in dry-run / dual mode,
and asserts:

* gate returns ok=True on a healthy manifest
* row count parity per table
* sha256 parity per table
* canonicalWrites is carried from manifest
* run/source coverage matches manifest
* gate fails (ok=False) when read-path feature flag is flipped to
  ``postgres``
* JSONL artifacts remain unchanged
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backfill_evidence_to_postgres_smoke_test import _build_fixtures  # noqa: E402
from dual_write_parity_gate import (  # noqa: E402
    ERROR_KIND_READ_PATH_FLIPPED,
    run_parity_gate,
    write_error_ledger,
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


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def test_gate_passes_on_healthy_manifest() -> None:
    with tempfile.TemporaryDirectory(dir=_temp_parent()) as tmp:
        lake = Path(tmp)
        manifest, files = _build_fixtures(lake)
        before = {path: _sha256_bytes(path.read_bytes()) for path in files}
        report = run_parity_gate(manifest, lake_root=lake, dry_run=True)
        _expect("gate ok=True on healthy manifest", report["ok"] is True)
        _expect("gate produced rowCountChecks", len(report["rowCountChecks"]) > 0)
        _expect("gate produced sha256Checks", len(report["sha256Checks"]) > 0)
        _expect("canonicalWritesCheck passes", report["canonicalWritesCheck"]["ok"] is True)
        _expect("runSourceCoverage covers all sources", report["runSourceCoverage"]["reportedRowCount"] >= 2)
        for path, expected in before.items():
            _expect(f"jsonl artifact unchanged: {path.name}", _sha256_bytes(path.read_bytes()) == expected)


def test_gate_fails_when_read_path_flipped() -> None:
    with tempfile.TemporaryDirectory(dir=_temp_parent()) as tmp:
        lake = Path(tmp)
        manifest, _ = _build_fixtures(lake)
        report = run_parity_gate(
            manifest,
            lake_root=lake,
            dry_run=True,
            feature_flag_read_path="postgres",
        )
        _expect("gate ok=False when read-path flipped to postgres", report["ok"] is False)
        kinds = {err["kind"] for err in report["errors"]}
        _expect("read-path-flipped error recorded", ERROR_KIND_READ_PATH_FLIPPED in kinds)
        ledger_path = lake / "parity-errors.jsonl"
        written = write_error_ledger(ledger_path, report)
        _expect("error ledger writes one failure row", written == 1)
        ledger_rows = [json.loads(line) for line in ledger_path.read_text(encoding="utf-8").splitlines()]
        _expect("error ledger row carries run id", ledger_rows[0]["runId"] == manifest.run_id)
        _expect("error ledger row carries error kind", ledger_rows[0]["error"]["kind"] == ERROR_KIND_READ_PATH_FLIPPED)


def test_gate_row_and_sha_parity() -> None:
    with tempfile.TemporaryDirectory(dir=_temp_parent()) as tmp:
        lake = Path(tmp)
        manifest, _ = _build_fixtures(lake)
        report = run_parity_gate(manifest, lake_root=lake, dry_run=True)
        for entry in report["rowCountChecks"]:
            _expect(
                f"row count parity for {entry['table']}",
                entry["jsonlOnlyRowCount"] == entry["dualRowCount"],
            )
        for entry in report["sha256Checks"]:
            _expect(
                f"sha256 parity for {entry['table']}",
                entry["jsonlOnlySha256"] == entry["dualSha256"],
            )


def main() -> int:
    tests = [
        test_gate_passes_on_healthy_manifest,
        test_gate_fails_when_read_path_flipped,
        test_gate_row_and_sha_parity,
    ]
    for test in tests:
        test()
    print(f"[PASS] {len(tests)} dual_write_parity_gate smoke tests")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
