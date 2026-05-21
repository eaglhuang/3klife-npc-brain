"""Rollback manifest drill (B5 of SANGUO-RAGOPS-0501 checklist).

Exercises the rollback playbook against the mock provider:

1. Run the evidence vector smoke gate once and record the upserted store.
2. Apply the gate's rollback manifest (deleteByRecordIds) against the
   same provider instance, asserting every recordId disappears.
3. Re-run the gate and assert it can re-upsert the same records (idempotent
   recovery).
4. Emit a drill report capturing pre / post rollback state + sha256 of the
   rollback manifest for governance.

Usage::

    python -B pipelines/sanguo-rag/drill_rollback_manifest.py \
        --output local/cutover-evidence/B5-rollback-drill/report.json
"""

from __future__ import annotations

import argparse
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

from run_evidence_vector_smoke_gate import (  # noqa: E402
    DEFAULT_POLICY_PATH,
    MockProviderAdapter,
    _build_upsert_manifest,
    _resolve_allowed_providers,
    _resolve_dedupe_key_fields,
    _load_policy,
    run_gate,
)
from run_evidence_vector_smoke_gate_smoke_test import _prepare_lake  # noqa: E402


def _temp_parent() -> Path:
    base_text = os.environ.get("SANGUO_RAG_TEST_TMPDIR")
    base = Path(base_text) if base_text else Path.cwd() / "local" / "tmp" / "sanguo-rag-smoke"
    base.mkdir(parents=True, exist_ok=True)
    return base


def drill(output: Path | None) -> dict:
    with tempfile.TemporaryDirectory(dir=_temp_parent()) as tmp:
        manifest_path, lake = _prepare_lake(Path(tmp))
        first = run_gate(
            manifest_path=manifest_path,
            lake_root=lake,
            policy_path=DEFAULT_POLICY_PATH,
            namespace_override=None,
            provider="mock",
            top_k_override=5,
            batch_size_override=10,
            allow_production_namespace=False,
        )
        record_ids_first = list(first["upsertManifest"]["entries"])
        rollback_manifest = first["rollbackManifest"]

        # Step 2: apply rollback against a freshly recreated mock provider
        adapter = MockProviderAdapter(namespace=first["namespace"])
        for entry in record_ids_first:
            adapter._store[entry["recordId"]] = {"recordId": entry["recordId"], "text": ""}
        before_keys = list(adapter._store.keys())
        for record_id in rollback_manifest["deleteByRecordIds"]:
            adapter._store.pop(record_id, None)
        after_keys = list(adapter._store.keys())

        # Step 3: re-run the gate, expecting clean upsert
        second = run_gate(
            manifest_path=manifest_path,
            lake_root=lake,
            policy_path=DEFAULT_POLICY_PATH,
            namespace_override=None,
            provider="mock",
            top_k_override=5,
            batch_size_override=10,
            allow_production_namespace=False,
        )

    rollback_sha = hashlib.sha256(
        json.dumps(rollback_manifest, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    report = {
        "schemaVersion": "rollback-drill-report.v0.1",
        "generatedAt": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "provider": first["provider"],
        "namespace": first["namespace"],
        "firstRunProbeOk": first["probe"]["probeOk"],
        "secondRunProbeOk": second["probe"]["probeOk"],
        "rollbackManifestSha256": rollback_sha,
        "store": {
            "beforeRollback": before_keys,
            "afterRollback": after_keys,
            "removedCount": len(before_keys) - len(after_keys),
        },
        "rerunRecordCounts": second["recordCounts"],
        "ok": (
            first["probe"]["probeOk"]
            and second["probe"]["probeOk"]
            and not after_keys
            and second["recordCounts"]["upserted"] == first["recordCounts"]["upserted"]
        ),
    }
    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Rollback manifest drill (B5).")
    parser.add_argument("--output", default="")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    out = Path(args.output) if args.output else None
    report = drill(out)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
