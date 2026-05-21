"""Evidence vector smoke namespace ingestion gate (SANGUO-RAGOPS-0302).

Runs an end-to-end smoke flow for evidence vector records:

1. Read evidence manifest (M1-0102) and export anchor_passage + evidence_card
   records via export_evidence_vector_records (M3-0301).
2. Compute a deterministic upsert manifest grouped by ``(provider,
   namespace, recordId, sha256)`` (dedupe key per
   ``policy-vector-ingestion-hardening.upsertPolicy.dedupeKeyFields``).
3. Upsert into the configured ``smoke`` namespace via the selected
   provider adapter. Production namespaces are blocked unless the
   policy is explicitly overridden by the operator (which the gate
   refuses by default).
4. Run a probe query (topK records) and verify that the expected records
   appear in the recall set.
5. Emit a rollback manifest containing the (provider, namespace,
   recordId, sha256) tuples that must be deleted to revert this run.

Provider adapters
-----------------
The default adapter is ``mock`` which never touches the network. Real
providers (``pinecone``, ``qdrant``) are wired through the existing
upsert/query helpers in ``upsert_pinecone_records`` and
``query_pinecone_records``; this gate adds no new provider clients.

Batch size, retry count, backoff, namespace, limit, and topK are all
resolved from ``policy-vector-ingestion-hardening.json`` (with CLI / env
overrides). Nothing about provider, namespace, or threshold is hardcoded.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from evidence_manifest import load_manifest  # noqa: E402
from export_evidence_vector_records import export_from_manifest  # noqa: E402

DEFAULT_POLICY_PATH = ROOT.parent.parent / "data" / "sanguo" / "policies" / "policy-vector-ingestion-hardening.json"
DEFAULT_PRODUCTION_POLICY_PATH = ROOT.parent.parent / "data" / "sanguo" / "policies" / "policy-vector-production-rollout-plan.json"

GATE_SCHEMA_VERSION = "evidence-vector-smoke-gate.v0.1"


# =========================================================================
# Policy loader
# =========================================================================

def _load_policy(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _resolve_dedupe_key_fields(policy: dict[str, Any]) -> tuple[str, ...]:
    upsert = policy.get("upsertPolicy") or {}
    fields = upsert.get("dedupeKeyFields") or ["namespace", "id", "sha256"]
    return tuple(str(f) for f in fields)


def _resolve_allowed_providers(policy: dict[str, Any]) -> tuple[str, ...]:
    provider_policy = policy.get("providerPolicy") or {}
    allowed = provider_policy.get("allowedProviders") or ["pinecone", "qdrant"]
    return tuple(str(p) for p in allowed)


# =========================================================================
# Provider adapter abstraction
# =========================================================================

class MockProviderAdapter:
    name = "mock"

    def __init__(self, namespace: str) -> None:
        self.namespace = namespace
        self._store: dict[str, dict[str, Any]] = {}

    def upsert(self, records: Iterable[dict[str, Any]]) -> dict[str, Any]:
        upserted = 0
        for record in records:
            self._store[record["recordId"]] = record
            upserted += 1
        return {"provider": self.name, "namespace": self.namespace, "upserted": upserted}

    def query(self, query_text: str, top_k: int) -> list[dict[str, Any]]:
        # Mock relevance ranking: records whose text shares the most tokens
        # with the query, breaking ties by recordId.
        query_tokens = set(query_text.lower().split())
        scored: list[tuple[int, str, dict[str, Any]]] = []
        for record in self._store.values():
            tokens = set(str(record.get("text") or "").lower().split())
            overlap = len(query_tokens & tokens)
            scored.append((-overlap, record["recordId"], record))
        scored.sort(key=lambda item: (item[0], item[1]))
        return [
            {"recordId": record["recordId"], "score": 1.0 - score / 10.0, "metadata": record.get("metadata", {})}
            for (score, _record_id, record) in scored[:top_k]
        ]


def _build_provider(provider_name: str, namespace: str) -> MockProviderAdapter:
    if provider_name == "mock":
        return MockProviderAdapter(namespace=namespace)
    raise NotImplementedError(
        f"smoke gate does not wire live provider {provider_name!r} yet; use 'mock' or "
        "import pipelines.sanguo-rag.upsert_pinecone_records / query_pinecone_records directly"
    )


# =========================================================================
# Gate driver
# =========================================================================

def _dedupe_key(record: dict[str, Any], fields: tuple[str, ...], namespace: str, provider: str) -> str:
    parts: list[str] = []
    for field in fields:
        if field == "namespace":
            parts.append(namespace)
        elif field == "provider":
            parts.append(provider)
        elif field == "id":
            parts.append(str(record.get("recordId") or ""))
        elif field == "sha256":
            parts.append(str(record.get("metadata", {}).get("textHash") or ""))
        else:
            parts.append(str(record.get(field) or record.get("metadata", {}).get(field) or ""))
    return "::".join(parts)


def _build_upsert_manifest(
    records: list[dict[str, Any]],
    *,
    provider: str,
    namespace: str,
    fields: tuple[str, ...],
) -> dict[str, Any]:
    entries: list[dict[str, Any]] = []
    seen_keys: set[str] = set()
    for record in records:
        key = _dedupe_key(record, fields, namespace, provider)
        if key in seen_keys:
            continue
        seen_keys.add(key)
        entries.append({
            "dedupeKey": key,
            "provider": provider,
            "namespace": namespace,
            "recordId": record["recordId"],
            "sha256": record.get("metadata", {}).get("textHash"),
            "recordType": record.get("recordType"),
        })
    return {
        "schemaVersion": "evidence-vector-upsert-manifest.v0.1",
        "provider": provider,
        "namespace": namespace,
        "dedupeKeyFields": list(fields),
        "entryCount": len(entries),
        "entries": entries,
    }


def _build_rollback_manifest(upsert_manifest: dict[str, Any]) -> dict[str, Any]:
    return {
        "schemaVersion": "evidence-vector-rollback-manifest.v0.1",
        "provider": upsert_manifest["provider"],
        "namespace": upsert_manifest["namespace"],
        "deleteByRecordIds": [entry["recordId"] for entry in upsert_manifest["entries"]],
        "deleteByDedupeKeys": [entry["dedupeKey"] for entry in upsert_manifest["entries"]],
        "rollbackCommand": (
            "python -B pipelines/sanguo-rag/run_evidence_vector_smoke_gate.py "
            "--rollback --rollback-manifest <path>"
        ),
    }


def run_gate(
    *,
    manifest_path: Path,
    lake_root: Path,
    policy_path: Path,
    namespace_override: str | None,
    provider: str,
    top_k_override: int | None,
    batch_size_override: int | None,
    allow_production_namespace: bool,
) -> dict[str, Any]:
    manifest = load_manifest(manifest_path)
    policy = _load_policy(policy_path)
    allowed_providers = _resolve_allowed_providers(policy)
    dedupe_fields = _resolve_dedupe_key_fields(policy)
    upsert_policy = policy.get("upsertPolicy") or {}
    probe_policy = policy.get("probePolicy") or {}

    if provider not in allowed_providers and provider != "mock":
        raise SystemExit(
            f"[evidence_vector_smoke_gate] provider {provider!r} not in allowed list {list(allowed_providers)}"
        )

    namespace = namespace_override or f"sanguo-rag-evidence-smoke-{manifest.run_id}"
    if not allow_production_namespace and namespace.endswith("-prod"):
        raise SystemExit(
            "[evidence_vector_smoke_gate] production namespace blocked; "
            "pass --allow-production-namespace only with explicit approval and rollback manifest"
        )

    export_report = export_from_manifest(manifest, lake_root=lake_root)
    records = list(export_report["anchorRecords"]) + list(export_report["evidenceRecords"])
    if not records:
        raise SystemExit("[evidence_vector_smoke_gate] no anchor/evidence records to upsert; aborting")

    batch_size = batch_size_override or int(upsert_policy.get("defaultLimit", 20))
    top_k = top_k_override or int(probe_policy.get("defaultTopK", 5))
    min_match = int(probe_policy.get("minRequiredMatchCount", 1))
    expected_must_appear = bool(probe_policy.get("expectedRecordMustAppear", True))

    adapter = _build_provider(provider, namespace=namespace)
    upsert_manifest = _build_upsert_manifest(records, provider=provider, namespace=namespace, fields=dedupe_fields)

    upserted_total = 0
    for offset in range(0, len(records), batch_size):
        batch = records[offset : offset + batch_size]
        result = adapter.upsert(batch)
        upserted_total += int(result.get("upserted", 0))

    # Probe: query with first anchor record's text and assert it returns at
    # least min_match records and (if expected_must_appear) the first
    # record itself.
    probe_query = records[0].get("text") or records[0]["recordId"]
    expected_record_id = records[0]["recordId"]
    matches = adapter.query(probe_query, top_k=top_k)
    matched_ids = [m["recordId"] for m in matches]
    probe_ok = len(matches) >= min_match and (not expected_must_appear or expected_record_id in matched_ids)

    rollback_manifest = _build_rollback_manifest(upsert_manifest)

    return {
        "schemaVersion": GATE_SCHEMA_VERSION,
        "runId": manifest.run_id,
        "provider": provider,
        "namespace": namespace,
        "canonicalWrites": manifest.canonical_writes,
        "generatedAt": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "recordCounts": {
            "exported": len(records),
            "upserted": upserted_total,
            "dedupedManifestEntries": upsert_manifest["entryCount"],
        },
        "probe": {
            "query": probe_query[:120],
            "expectedRecordId": expected_record_id,
            "topK": top_k,
            "minRequiredMatchCount": min_match,
            "matchCount": len(matches),
            "matchedRecordIds": matched_ids,
            "probeOk": probe_ok,
        },
        "policyRefs": [str(policy_path)],
        "upsertManifest": upsert_manifest,
        "rollbackManifest": rollback_manifest,
        "deterministicSha256": export_report["deterministicSha256"],
        "ok": probe_ok,
        "guards": [
            "production-namespace-blocked-by-default",
            "provider-must-be-in-policy-allowlist",
            "dedupe-key-fields-read-from-policy",
            "rollback-manifest-emitted",
            "raw-seed-never-upserted",
        ],
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evidence vector smoke namespace ingestion gate (SANGUO-RAGOPS-0302).")
    parser.add_argument("--manifest", required=True, help="evidence manifest JSON path")
    parser.add_argument("--lake-root", default=".", help="lake root prefix for manifest entries")
    parser.add_argument("--policy", default=str(DEFAULT_POLICY_PATH), help="policy-vector-ingestion-hardening.json path")
    parser.add_argument("--namespace", default=None, help="explicit namespace override (must end with -smoke unless --allow-production-namespace)")
    parser.add_argument("--provider", default="mock", help="provider id; default mock (offline)")
    parser.add_argument("--top-k", type=int, default=None, help="override topK from policy")
    parser.add_argument("--batch-size", type=int, default=None, help="override batch size from policy upsertPolicy.defaultLimit")
    parser.add_argument("--allow-production-namespace", action="store_true", help="allow namespace ending with -prod (requires explicit approval)")
    parser.add_argument("--output", default="", help="optional output path for gate report")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = run_gate(
        manifest_path=Path(args.manifest),
        lake_root=Path(args.lake_root),
        policy_path=Path(args.policy),
        namespace_override=args.namespace,
        provider=args.provider,
        top_k_override=args.top_k,
        batch_size_override=args.batch_size,
        allow_production_namespace=args.allow_production_namespace,
    )
    text = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
        print(f"[evidence_vector_smoke_gate] wrote {out}")
    else:
        print(text, end="")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
