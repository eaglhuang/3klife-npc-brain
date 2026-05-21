"""Smoke test for run_vector_ingestion_gate dry-run safety.

Builds a minimal runtime vector fixture and runs ``run_vector_ingestion_gate``
with ``--dry-run``. The test asserts the command returns a plan and does not
write vector-ready outputs, backend check reports, or state files.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent
FIXTURE_GENERAL_ID = "fixture-general"


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


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n", encoding="utf-8")


def _build_fixture(root: Path) -> dict[str, Path]:
    events = root / "events" / "events.jsonl"
    keyword_root = root / "keyword-options"
    persona_root = root / "persona-cards"
    _write_jsonl(
        events,
        [
            {
                "eventId": "fixture-event-1",
                "eventKey": "fixture-event-1",
                "eventType": "life",
                "reviewStatus": "ready",
                "generalIds": [FIXTURE_GENERAL_ID],
                "summary": "dry-run smoke event",
                "sourceRefs": ["fixture#p1"],
                "canonicalWrites": False,
            }
        ],
    )
    _write_json(
        keyword_root / f"{FIXTURE_GENERAL_ID}.keywords.json",
        {
            "generalId": FIXTURE_GENERAL_ID,
            "keywordVersion": "smoke",
            "categories": {
                "personality": [
                    {
                        "keywordKey": "bold",
                        "label": "bold",
                        "generalIds": [FIXTURE_GENERAL_ID],
                        "confidence": 0.9,
                    }
                ]
            },
        },
    )
    _write_json(
        persona_root / f"{FIXTURE_GENERAL_ID}.persona.json",
        {
            "generalId": FIXTURE_GENERAL_ID,
            "displayName": "Fixture General",
            "personaVersion": "smoke",
            "evidenceRefs": ["fixture#p1"],
            "relationshipAnchors": [],
            "keywordAnchors": [],
        },
    )
    return {
        "events": events,
        "keywordRoot": keyword_root,
        "personaRoot": persona_root,
        "vectorReadyRoot": root / "vector-ready",
        "apiReadinessRoot": root / "api-readiness",
        "statePath": root / "vector-ready" / "state.json",
        "checkReportPath": root / "api-readiness" / "vector-check.json",
    }


def test_dry_run_does_not_write_outputs() -> None:
    with tempfile.TemporaryDirectory(dir=_temp_parent()) as tmp:
        root = Path(tmp)
        paths = _build_fixture(root)
        cmd = [
            sys.executable,
            str(ROOT / "run_vector_ingestion_gate.py"),
            "--events",
            str(paths["events"]),
            "--keyword-root",
            str(paths["keywordRoot"]),
            "--persona-root",
            str(paths["personaRoot"]),
            "--vector-ready-root",
            str(paths["vectorReadyRoot"]),
            "--api-readiness-root",
            str(paths["apiReadinessRoot"]),
            "--state-path",
            str(paths["statePath"]),
            "--check-report-path",
            str(paths["checkReportPath"]),
            "--providers",
            "qdrant",
            "--general-id",
            FIXTURE_GENERAL_ID,
            "--dry-run",
            "--skip-upsert",
            "--skip-probe",
            "--skip-readiness",
        ]
        result = subprocess.run(
            cmd,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if result.returncode != 0:
            print(result.stdout)
            print(result.stderr)
        _expect("dry-run command exits 0", result.returncode == 0)
        plan = json.loads(result.stdout)
        _expect("dry-run plan schema", plan["schemaVersion"] == "vector-ingestion-dry-run-plan.v0.1")
        _expect("dry-run plan would not write state", plan["wouldWriteState"] is False)
        _expect("dry-run plan would not write check report", plan["wouldWriteCheckReport"] is False)
        _expect("state file not created", not paths["statePath"].exists())
        _expect("check report not created", not paths["checkReportPath"].exists())
        _expect("vector ready root not created", not paths["vectorReadyRoot"].exists())


def main() -> int:
    test_dry_run_does_not_write_outputs()
    print("[PASS] run_vector_ingestion_gate dry-run smoke test")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
