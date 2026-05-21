"""Smoke tests for run_large_run_rehearsal (SANGUO-RAGOPS-0401).

Loads the sample sources fixture, runs the rehearsal in each of the four
policy-declared modes, and asserts the report + ledger satisfy the
acceptance criteria.
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

from run_large_run_rehearsal import DEFAULT_POLICY, run_rehearsal  # noqa: E402

SOURCES_FIXTURE = ROOT / "fixtures" / "large-run-sources-smoke.json"


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


def _load_sources() -> list[dict]:
    payload = json.loads(SOURCES_FIXTURE.read_text(encoding="utf-8"))
    return list(payload["sources"])


REQUIRED_TELEMETRY_FIELDS = {
    "roundId",
    "sourceId",
    "sourceFamily",
    "sourceLayer",
    "fetchCount",
    "harvestedCount",
    "seedCount",
    "cardCount",
    "newEvidenceCount",
    "timeoutCount",
    "rawBytes",
    "artifactBytes",
    "resumeScanSeconds",
    "postgresRowCount",
    "vectorRecordCount",
    "roiScore",
    "backpressureSignal",
}


def test_modes_present() -> None:
    sources = _load_sources()
    for mode in ["no-write", "jsonl-only", "dual-write", "vector-smoke"]:
        result = run_rehearsal(policy_path=DEFAULT_POLICY, mode_id=mode, sources=sources, output_root=None)
        _expect(f"mode {mode} runs", result["report"]["mode"] == mode)
        _expect(f"mode {mode} report contains rounds", result["report"]["totals"]["roundsExecuted"] >= 1)


def test_telemetry_ledger_fields() -> None:
    sources = _load_sources()
    result = run_rehearsal(policy_path=DEFAULT_POLICY, mode_id="dual-write", sources=sources, output_root=None)
    rows = result["ledger"]["rows"]
    _expect("ledger has rows", len(rows) > 0)
    for row in rows:
        missing = REQUIRED_TELEMETRY_FIELDS - row.keys()
        _expect(f"telemetry row fields complete for {row.get('roundId')}::{row.get('sourceId')}", not missing)


def test_artifact_budget_stops_run() -> None:
    sources = _load_sources()
    # Write a tight policy into a temp file so the artifact budget is hit
    # in the first few rounds. This proves the backpressure path runs end
    # to end without modifying the production policy.
    with tempfile.TemporaryDirectory(dir=_temp_parent()) as tmp:
        policy = json.loads(DEFAULT_POLICY.read_text(encoding="utf-8"))
        policy["budgets"]["maxArtifactBytesPerRun"] = 8 * 1024 * 1024  # 8 MiB
        policy["budgets"]["maxRawBytesPerRound"] = 4 * 1024 * 1024
        policy["budgets"]["maxRawBytesPerSource"] = 2 * 1024 * 1024
        tight_policy = Path(tmp) / "policy-large-run-rehearsal.tight.json"
        tight_policy.write_text(json.dumps(policy, ensure_ascii=False, indent=2), encoding="utf-8")
        result = run_rehearsal(policy_path=tight_policy, mode_id="no-write", sources=sources, output_root=None)
    _expect(
        "rehearsal stops with artifact-budget signal under tight policy",
        result["report"]["stopReason"] == "artifact-budget-exhausted",
    )


def test_low_yield_triggers_stop() -> None:
    sources = _load_sources()
    # Reduce new evidence below minNewEvidencePerRound
    sparse = []
    for src in sources:
        src = dict(src)
        src["expectedNewEvidence"] = 0
        sparse.append(src)
    result = run_rehearsal(policy_path=DEFAULT_POLICY, mode_id="no-write", sources=sparse, output_root=None)
    _expect(
        "low yield triggers consecutive-low-yield stop",
        result["report"]["stopReason"] == "consecutive-low-yield",
    )


def test_output_root_writes_files() -> None:
    sources = _load_sources()
    with tempfile.TemporaryDirectory(dir=_temp_parent()) as tmp:
        out_root = Path(tmp)
        run_rehearsal(policy_path=DEFAULT_POLICY, mode_id="no-write", sources=sources, output_root=out_root)
        _expect("rehearsal-report.json written", (out_root / "rehearsal-report.json").exists())
        _expect("backpressure-telemetry-ledger.json written", (out_root / "backpressure-telemetry-ledger.json").exists())


def main() -> int:
    tests = [
        test_modes_present,
        test_telemetry_ledger_fields,
        test_artifact_budget_stops_run,
        test_low_yield_triggers_stop,
        test_output_root_writes_files,
    ]
    for test in tests:
        test()
    print(f"[PASS] {len(tests)} run_large_run_rehearsal smoke tests")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
