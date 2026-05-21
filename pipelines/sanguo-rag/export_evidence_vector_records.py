"""Evidence vector record exporter (SANGUO-RAGOPS-0301).

Reads an evidence manifest (M1-0102) and emits retrieval-ready vector
records for two record types:

* ``anchor_passage`` (from anchor-passage JSONL)
* ``evidence_card`` (from evidence-card JSONL with reviewStatus in the
  hardened set)

Raw seeds, harvested pages, and proposals are intentionally never
exported (governance red line: ``raw seed shall not enter vector DB``).

The exporter is deterministic: identical input manifest + identical
JSONL bytes → identical record JSON and identical sha256 digest.

Reading from PostgreSQL mirror is supported via ``--source postgres``;
in that mode the exporter selects evidence_cards / anchor_passages rows
joined with pipeline_runs and source_runs to recover sourceFamily /
sourceLayer / canonicalWrites context.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from evidence_manifest import EvidenceManifest, load_manifest  # noqa: E402

SCHEMA_VERSION = "evidence-vector-record.v0.1"
EVIDENCE_CARD_HARDENED_STATUSES = frozenset({"accepted", "staged-a"})

NAMESPACE_PREFIX_DEFAULT = "sanguo-rag-evidence"


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def _truncate(text: str, max_chars: int = 480) -> str:
    raw = text.strip()
    if len(raw) <= max_chars:
        return raw
    return raw[: max_chars - 1].rstrip() + "…"


def _ensure_metadata_fields(metadata: dict[str, Any]) -> dict[str, Any]:
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
    missing = required - metadata.keys()
    if missing:
        raise ValueError(f"vector record metadata missing fields: {sorted(missing)}")
    return metadata


def _build_anchor_passage_record(
    row: dict[str, Any],
    *,
    run_id: str,
    canonical_writes: bool,
    artifact_uri: str,
    namespace: str,
) -> dict[str, Any] | None:
    text = str(row.get("normalizedText") or row.get("text") or "").strip()
    if not text:
        return None
    locator = str(row.get("locator") or "")
    text_hash = str(row.get("textHash") or _sha256(text))
    passage_id = str(row.get("passageId") or row.get("id") or _sha256(f"{run_id}::{locator}::{text_hash}"))
    metadata = {
        "recordType": "anchor_passage",
        "runId": run_id,
        "sourceId": str(row.get("sourceId") or row.get("corpusId") or ""),
        "sourceFamily": str(row.get("sourceFamily") or "anchor"),
        "sourceLayer": str(row.get("sourceLayer") or row.get("layer") or ""),
        "generalIds": list(row.get("generalIds") or []),
        "locator": locator,
        "textHash": text_hash,
        "anchorVerdict": "anchored",
        "canonicalWrites": canonical_writes,
        "payloadUri": artifact_uri,
        "corpusId": str(row.get("corpusId") or ""),
    }
    _ensure_metadata_fields(metadata)
    return {
        "schemaVersion": SCHEMA_VERSION,
        "recordId": f"anchor::{passage_id}",
        "recordType": "anchor_passage",
        "text": _truncate(text),
        "namespace": namespace,
        "metadata": metadata,
    }


def _build_evidence_card_record(
    row: dict[str, Any],
    *,
    run_id: str,
    source_id: str,
    canonical_writes: bool,
    artifact_uri: str,
    namespace: str,
    allow_candidate: bool,
) -> dict[str, Any] | None:
    review_status = str(row.get("reviewStatus") or "candidate")
    if not allow_candidate and review_status not in EVIDENCE_CARD_HARDENED_STATUSES:
        return None
    text_parts: list[str] = []
    if row.get("sourceQuote"):
        text_parts.append(f"出處：{_truncate(str(row.get('sourceQuote')))}")
    if row.get("summary"):
        text_parts.append(f"摘要：{_truncate(str(row.get('summary')))}")
    if not text_parts:
        return None
    text = "\n".join(text_parts)
    locator = str(row.get("locator") or "")
    text_hash = str(row.get("quoteHash") or _sha256(text))
    evidence_id = str(row.get("evidenceId") or row.get("id") or _sha256(f"{run_id}::{source_id}::{locator}::{text_hash}"))
    anchor_evidence = row.get("anchorEvidence") or {}
    anchor_verdict = "anchored" if anchor_evidence.get("status") == "anchored" else (
        "candidate" if review_status not in EVIDENCE_CARD_HARDENED_STATUSES else "candidate"
    )
    if anchor_evidence.get("status") == "anchored":
        anchor_verdict = "anchored"
    elif anchor_evidence.get("status"):
        anchor_verdict = str(anchor_evidence["status"])
    metadata = {
        "recordType": "evidence_card",
        "runId": run_id,
        "sourceId": source_id,
        "sourceFamily": str(row.get("sourceFamily") or ""),
        "sourceLayer": str(row.get("sourceLayer") or ""),
        "generalIds": list(row.get("generalIds") or []),
        "locator": locator,
        "textHash": text_hash,
        "anchorVerdict": anchor_verdict,
        "canonicalWrites": canonical_writes,
        "payloadUri": artifact_uri,
        "reviewStatus": review_status,
    }
    _ensure_metadata_fields(metadata)
    return {
        "schemaVersion": SCHEMA_VERSION,
        "recordId": f"evidence::{evidence_id}",
        "recordType": "evidence_card",
        "text": text,
        "namespace": namespace,
        "metadata": metadata,
    }


def _stable_dump(records: Iterable[dict[str, Any]]) -> tuple[str, str]:
    """Serialize records deterministically and return (jsonl_text, sha256)."""
    lines: list[str] = []
    for record in records:
        lines.append(json.dumps(record, ensure_ascii=False, sort_keys=True))
    text = "\n".join(lines) + ("\n" if lines else "")
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return text, digest


# ---------------------------------------------------------------------------
# Low-level helpers for convergence-loop vector smoke (SANGUO-RAGOPS-0605)
# ---------------------------------------------------------------------------

def iter_evidence_cards(path: Path) -> list[dict[str, Any]]:
    """Read evidence card rows from a JSONL file.

    Returns an empty list if the file does not exist or is unreadable.
    Used by the convergence vector smoke runner to load card batches.
    """
    return _read_jsonl(path)


def build_vector_records(
    rows: list[dict[str, Any]],
    *,
    run_id: str,
    source_id: str,
    artifact_uri: str,
    namespace: str,
    canonical_writes: bool = False,
) -> list[dict[str, Any]]:
    """Build evidence-card vector records from a pre-filtered list of rows.

    Unlike ``export_from_manifest()``, this function does **not** filter by
    ``reviewStatus`` — that filtering must be done by the caller before passing
    rows here.  Returns only records that have non-empty text content.
    """
    records: list[dict[str, Any]] = []
    for row in rows:
        record = _build_evidence_card_record(
            row,
            run_id=run_id,
            source_id=source_id,
            canonical_writes=canonical_writes,
            artifact_uri=artifact_uri,
            namespace=namespace,
            allow_candidate=True,  # caller already applied reviewStatus filter
        )
        if record is not None:
            records.append(record)
    return records


def export_from_manifest(
    manifest: EvidenceManifest,
    *,
    lake_root: Path,
    namespace_prefix: str = NAMESPACE_PREFIX_DEFAULT,
    allow_candidate_evidence: bool = False,
) -> dict[str, Any]:
    anchor_records: list[dict[str, Any]] = []
    evidence_records: list[dict[str, Any]] = []

    canonical_writes = bool(manifest.canonical_writes)
    namespace = f"{namespace_prefix}-smoke" if not canonical_writes else f"{namespace_prefix}-prod"

    for entry in manifest.files:
        path = (lake_root / entry.path) if not Path(entry.path).is_absolute() else Path(entry.path)
        rows = _read_jsonl(path)
        if entry.artifact_type == "anchor-passage":
            for row in rows:
                record = _build_anchor_passage_record(
                    row,
                    run_id=manifest.run_id,
                    canonical_writes=canonical_writes,
                    artifact_uri=entry.artifact_uri,
                    namespace=namespace,
                )
                if record is not None:
                    anchor_records.append(record)
        elif entry.artifact_type == "evidence-card":
            for row in rows:
                record = _build_evidence_card_record(
                    row,
                    run_id=manifest.run_id,
                    source_id=entry.source_id,
                    canonical_writes=canonical_writes,
                    artifact_uri=entry.artifact_uri,
                    namespace=namespace,
                    allow_candidate=allow_candidate_evidence,
                )
                if record is not None:
                    evidence_records.append(record)
        else:
            # raw seeds, harvested pages, proposals: never exported
            continue

    # Stable ordering by recordId for deterministic digests
    anchor_records.sort(key=lambda r: r["recordId"])
    evidence_records.sort(key=lambda r: r["recordId"])

    anchor_text, anchor_sha = _stable_dump(anchor_records)
    evidence_text, evidence_sha = _stable_dump(evidence_records)
    combined_text, combined_sha = _stable_dump(anchor_records + evidence_records)

    return {
        "schemaVersion": SCHEMA_VERSION,
        "runId": manifest.run_id,
        "namespace": namespace,
        "canonicalWrites": canonical_writes,
        "generatedAt": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "recordCounts": {
            "anchor_passage": len(anchor_records),
            "evidence_card": len(evidence_records),
            "total": len(anchor_records) + len(evidence_records),
        },
        "deterministicSha256": {
            "anchor_passage": anchor_sha,
            "evidence_card": evidence_sha,
            "combined": combined_sha,
        },
        "anchorRecords": anchor_records,
        "evidenceRecords": evidence_records,
        "anchorJsonl": anchor_text,
        "evidenceJsonl": evidence_text,
        "guards": [
            "raw-seed-not-exported",
            "harvested-page-not-exported",
            "proposal-not-exported",
            "production-namespace-disabled-by-default",
            "deterministic-sort-by-record-id",
        ],
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export evidence vector records (SANGUO-RAGOPS-0301).")
    parser.add_argument("--manifest", required=True, help="evidence manifest JSON path")
    parser.add_argument("--lake-root", default=".", help="root prefix for resolving manifest entries")
    parser.add_argument("--namespace-prefix", default=NAMESPACE_PREFIX_DEFAULT, help="vector namespace prefix")
    parser.add_argument("--allow-candidate-evidence", action="store_true", help="allow evidence cards with reviewStatus != accepted/staged-a")
    parser.add_argument("--output-root", default="", help="optional output root; emits anchor.jsonl + evidence.jsonl + report.json")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    manifest = load_manifest(Path(args.manifest))
    report = export_from_manifest(
        manifest,
        lake_root=Path(args.lake_root),
        namespace_prefix=args.namespace_prefix,
        allow_candidate_evidence=args.allow_candidate_evidence,
    )

    if args.output_root:
        out_root = Path(args.output_root)
        out_root.mkdir(parents=True, exist_ok=True)
        (out_root / "vector-records.anchor_passage.jsonl").write_text(report["anchorJsonl"], encoding="utf-8")
        (out_root / "vector-records.evidence_card.jsonl").write_text(report["evidenceJsonl"], encoding="utf-8")
        slim_report = {k: v for k, v in report.items() if k not in ("anchorJsonl", "evidenceJsonl")}
        (out_root / "vector-records.report.json").write_text(
            json.dumps(slim_report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(
            f"[export_evidence_vector_records] wrote {report['recordCounts']['total']} records "
            f"({report['recordCounts']['anchor_passage']} anchor + {report['recordCounts']['evidence_card']} evidence) "
            f"to {out_root}; combined sha256={report['deterministicSha256']['combined']}"
        )
    else:
        slim_report = {k: v for k, v in report.items() if k not in ("anchorJsonl", "evidenceJsonl")}
        print(json.dumps(slim_report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
