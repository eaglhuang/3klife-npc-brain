"""Generate review-only cleanup proposals from body-boundary telemetry residuals.

The script intentionally does not mutate cleanup rules. It mines repeated tail
fragments from already bounded harvested pages and writes proposal ledgers that
can be reviewed before any marker is promoted into governance data.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from repo_layout import resolve_repo_root


REPO_ROOT = resolve_repo_root(__file__)
DEFAULT_SOURCE_CONFIG_PATH = REPO_ROOT / "pipelines/sanguo-rag/config/anchor-index-build-sources.json"
DEFAULT_OUTPUT_ROOT = Path("local/codex-smoke/knowledge-growth/body-boundary-residual-proposals")
DEFAULT_POLICY: dict[str, Any] = {
    "enabled": True,
    "outputSubdir": "",
    "proposalFileName": "body-boundary-residual-cleanup-proposals.jsonl",
    "observationFileName": "body-boundary-residual-observations.jsonl",
    "summaryFileName": "body-boundary-residual-cleanup-summary.json",
    "tailWindowChars": 800,
    "minTailRelativeOffset": 0.55,
    "minCandidateChars": 6,
    "maxCandidateChars": 80,
    "tokenNgramSizes": [2, 3, 4, 5],
    "minSupportPageCount": 2,
    "minDistinctSourceCount": 1,
    "maxProposals": 50,
    "maxSampleContexts": 5,
    "contextChars": 80,
    "tailOffsetSort": "earliest",
    "excludeExistingRuleMarkers": True,
    "targetRulePath": "",
    "suggestedTargets": [],
}
TOKEN_RE = re.compile(r"[\u4e00-\u9fff]|[A-Za-z0-9][A-Za-z0-9._:/%-]*")


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def resolve_path(path_text: str | Path) -> Path:
    path = Path(path_text)
    return path if path.is_absolute() else (REPO_ROOT / path).resolve()


def stable_hash(text: str, length: int = 16) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:length]


def read_json(path: Path) -> Any:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8-sig"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        text = line.strip()
        if text:
            rows.append(json.loads(text))
    return rows


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def repo_relative(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(REPO_ROOT.resolve())).replace("\\", "/")
    except ValueError:
        return str(path.resolve())


def string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value] if value.strip() else []
    if not isinstance(value, list):
        return []
    return [str(item or "").strip() for item in value if str(item or "").strip()]


def int_list(value: Any, fallback: list[int]) -> list[int]:
    values: list[int] = []
    if isinstance(value, list):
        for item in value:
            try:
                token = int(item)
            except (TypeError, ValueError):
                continue
            if token > 0:
                values.append(token)
    return values or list(fallback)


def active_policy(source_payload: dict[str, Any], override: dict[str, Any] | None = None) -> dict[str, Any]:
    policy = dict(DEFAULT_POLICY)
    configured = source_payload.get("bodyBoundaryResidualProposal")
    if isinstance(configured, dict):
        policy.update(configured)
    if isinstance(override, dict):
        policy.update(override)
    return policy


def segmentation_policy(source_payload: dict[str, Any]) -> dict[str, Any]:
    configured = source_payload.get("segmentationPolicy")
    return configured if isinstance(configured, dict) else {}


def configured_paths(payload: dict[str, Any], direct_key: str, glob_key: str) -> list[Path]:
    paths: list[Path] = []
    for row in payload.get(direct_key) or []:
        if not isinstance(row, dict) or row.get("enabled") is False:
            continue
        raw_path = str(row.get("path") or row.get("pagesJsonl") or row.get("summaryJson") or "").strip()
        if raw_path:
            paths.append(resolve_path(raw_path))
    for row in payload.get(glob_key) or []:
        if not isinstance(row, dict) or row.get("enabled") is False:
            continue
        root_text = str(row.get("root") or "").strip()
        pattern = str(row.get("pattern") or "").strip()
        if not root_text or not pattern:
            continue
        root = resolve_path(root_text)
        if not root.exists():
            continue
        iterator = root.rglob(pattern) if row.get("recursive", True) else root.glob(pattern)
        paths.extend(sorted(path for path in iterator if path.is_file()))
    seen: set[Path] = set()
    unique: list[Path] = []
    for path in paths:
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        unique.append(resolved)
    return unique


def pages_jsonl_paths_from_summaries(summary_paths: list[Path]) -> list[Path]:
    paths: list[Path] = []
    for summary_path in summary_paths:
        payload = read_json(summary_path)
        pages_jsonl = str(((payload.get("inputs") or {}).get("pagesJsonl")) or "").strip()
        if pages_jsonl:
            paths.append(resolve_path(pages_jsonl))
    return paths


def discovered_pages_paths(source_payload: dict[str, Any]) -> list[Path]:
    paths = configured_paths(source_payload, "harvestedPageSources", "harvestedPageGlobSources")
    summary_paths = configured_paths(
        source_payload,
        "harvestedPageSummarySources",
        "harvestedPageSummaryGlobSources",
    )
    paths.extend(pages_jsonl_paths_from_summaries(summary_paths))
    seen: set[Path] = set()
    unique: list[Path] = []
    for path in paths:
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        unique.append(resolved)
    return unique


def strip_metadata_header(raw_text: str, policy: dict[str, Any]) -> str:
    if not bool(policy.get("stripMetadataHeader", True)):
        return raw_text
    pattern = str(policy.get("metadataHeaderSeparatorPattern") or r"\r?\n\r?\n")
    parts = re.split(pattern, raw_text, maxsplit=1)
    return parts[1] if len(parts) == 2 else raw_text


def read_page_text(record: dict[str, Any], policy: dict[str, Any]) -> str:
    text_path_raw = str(record.get("textPath") or "").strip()
    if text_path_raw:
        text_path = resolve_path(text_path_raw)
        if text_path.exists():
            return strip_metadata_header(text_path.read_text(encoding="utf-8-sig", errors="ignore"), policy)
    for key in string_list(policy.get("textFields")):
        value = str(record.get(key) or "").strip()
        if value:
            return value
    return str(record.get("snippet") or "")


def telemetry_match_keys(record: dict[str, Any], policy: dict[str, Any]) -> list[str]:
    keys: list[str] = []
    for field_name in string_list(policy.get("bodyBoundaryTelemetryMatchFields")):
        value = str(record.get(field_name) or "").strip()
        if value:
            keys.append(f"{field_name}:{value}")
    return keys


def telemetry_paths_for_pages(pages_path: Path, page_rows: list[dict[str, Any]], policy: dict[str, Any]) -> list[Path]:
    paths: list[Path] = []
    for row in page_rows:
        for field_name in string_list(policy.get("bodyBoundaryTelemetryPathFields")):
            raw_path = str(row.get(field_name) or "").strip()
            if raw_path:
                paths.append(resolve_path(raw_path))
    for file_name in string_list(policy.get("bodyBoundaryTelemetryFileNames")):
        paths.append((pages_path.parent / file_name).resolve())
    seen: set[Path] = set()
    unique: list[Path] = []
    for path in paths:
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        unique.append(resolved)
    return unique


def telemetry_index_for_pages(pages_path: Path, page_rows: list[dict[str, Any]], policy: dict[str, Any]) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for telemetry_path in telemetry_paths_for_pages(pages_path, page_rows, policy):
        for row in read_jsonl(telemetry_path):
            for key in telemetry_match_keys(row, policy):
                index.setdefault(key, row)
    return index


def matching_telemetry(record: dict[str, Any], index: dict[str, dict[str, Any]], policy: dict[str, Any]) -> dict[str, Any] | None:
    for key in telemetry_match_keys(record, policy):
        row = index.get(key)
        if not row:
            continue
        if bool(policy.get("bodyBoundaryTelemetryRequireTextHash", True)):
            record_hash = str(record.get("textHash") or "").strip()
            telemetry_hash = str(row.get("textHash") or "").strip()
            if record_hash and telemetry_hash and record_hash != telemetry_hash:
                continue
        return row
    return None


def normalize_marker_text(raw_text: str) -> str:
    return re.sub(r"\s+", " ", raw_text).strip(" \t\r\n:;,.!?|/-_")


def token_offsets(text: str) -> list[tuple[str, int, int]]:
    return [(match.group(0), match.start(), match.end()) for match in TOKEN_RE.finditer(text)]


def page_identity(record: dict[str, Any]) -> str:
    parts = [
        str(record.get("sourceId") or "").strip(),
        str(record.get("pageId") or "").strip(),
        str(record.get("url") or "").strip(),
        str(record.get("textHash") or "").strip(),
    ]
    return "page:" + stable_hash("|".join(parts))


def existing_cleanup_markers(source_payload: dict[str, Any], policy: dict[str, Any]) -> set[str]:
    rule_path_text = str(policy.get("targetRulePath") or source_payload.get("pageTextCleanupRulePath") or "").strip()
    if not rule_path_text:
        return set()
    rule_path = resolve_path(rule_path_text)
    markers: set[str] = set()
    for row in read_jsonl(rule_path):
        for value in string_list(row.get("value")):
            markers.add(value)
    return markers


def proposal_targets(policy: dict[str, Any]) -> list[dict[str, str]]:
    targets: list[dict[str, str]] = []
    for row in policy.get("suggestedTargets") or []:
        if not isinstance(row, dict):
            continue
        extractor = str(row.get("extractor") or "").strip()
        constant_name = str(row.get("constantName") or "").strip()
        if extractor and constant_name:
            targets.append({"extractor": extractor, "constantName": constant_name})
    return targets


def candidate_observations_for_page(
    *,
    pages_path: Path,
    page: dict[str, Any],
    telemetry: dict[str, Any],
    raw_text: str,
    source_payload: dict[str, Any],
    policy: dict[str, Any],
) -> list[dict[str, Any]]:
    try:
        body_start = int(telemetry.get("bodyStartOffset") or 0)
        body_end = int(telemetry.get("bodyEndOffset") or len(raw_text))
    except (TypeError, ValueError):
        return []
    body_start = min(max(body_start, 0), len(raw_text))
    body_end = min(max(body_end, body_start), len(raw_text))
    if body_end <= body_start:
        return []

    body_text = raw_text[body_start:body_end]
    tail_window_chars = max(int(policy.get("tailWindowChars") or 0), 1)
    min_relative = float(policy.get("minTailRelativeOffset") or 0.0)
    min_chars = max(int(policy.get("minCandidateChars") or 1), 1)
    max_chars = max(int(policy.get("maxCandidateChars") or min_chars), min_chars)
    context_chars = max(int(policy.get("contextChars") or 0), 0)
    ngram_sizes = int_list(policy.get("tokenNgramSizes"), [2, 3, 4, 5])
    tail_start = max(len(body_text) - tail_window_chars, 0)
    tail_text = body_text[tail_start:]
    tokens = token_offsets(tail_text)
    observations: list[dict[str, Any]] = []
    seen_on_page: set[str] = set()
    for ngram_size in ngram_sizes:
        if ngram_size > len(tokens):
            continue
        for idx in range(0, len(tokens) - ngram_size + 1):
            start = tokens[idx][1]
            end = tokens[idx + ngram_size - 1][2]
            absolute_start = tail_start + start
            relative_offset = absolute_start / max(len(body_text), 1)
            if relative_offset < min_relative:
                continue
            marker = normalize_marker_text(tail_text[start:end])
            if len(marker) < min_chars or len(marker) > max_chars:
                continue
            marker_hash = stable_hash(marker)
            if marker_hash in seen_on_page:
                continue
            seen_on_page.add(marker_hash)
            context_start = max(tail_start + start - context_chars, 0)
            context_end = min(tail_start + end + context_chars, len(body_text))
            observations.append(
                {
                    "candidateMarker": marker,
                    "candidateMarkerHash": marker_hash,
                    "pagesJsonl": repo_relative(pages_path),
                    "pageIdentity": page_identity(page),
                    "pageId": page.get("pageId"),
                    "sourceId": page.get("sourceId"),
                    "url": page.get("url"),
                    "telemetryId": telemetry.get("telemetryId"),
                    "bodyEndReason": telemetry.get("bodyEndReason"),
                    "bodyStartOffset": body_start,
                    "bodyEndOffset": body_end,
                    "bodyTextLength": len(body_text),
                    "tailRelativeOffset": round(relative_offset, 4),
                    "context": body_text[context_start:context_end],
                    "canonicalWrites": False,
                }
            )
    return observations


def build_proposals_from_observations(
    observations: list[dict[str, Any]],
    *,
    source_payload: dict[str, Any],
    policy: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    existing_markers = existing_cleanup_markers(source_payload, policy) if bool(policy.get("excludeExistingRuleMarkers", True)) else set()
    min_support = max(int(policy.get("minSupportPageCount") or 1), 1)
    min_sources = max(int(policy.get("minDistinctSourceCount") or 1), 1)
    max_samples = max(int(policy.get("maxSampleContexts") or 1), 1)
    max_proposals = max(int(policy.get("maxProposals") or 0), 0)
    prefer_latest_tail = str(policy.get("tailOffsetSort") or "earliest").strip().lower() == "latest"
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in observations:
        marker = str(row.get("candidateMarker") or "").strip()
        if not marker or marker in existing_markers:
            continue
        buckets[marker].append(row)

    bucket_rows: list[dict[str, Any]] = []
    proposals: list[dict[str, Any]] = []
    now = utc_now()
    for marker, rows in buckets.items():
        page_keys = {str(row.get("pageIdentity") or f"{row.get('sourceId')}#{row.get('pageId') or row.get('url')}") for row in rows}
        source_ids = sorted({str(row.get("sourceId") or "") for row in rows if str(row.get("sourceId") or "").strip()})
        avg_tail_offset = sum(float(row.get("tailRelativeOffset") or 0.0) for row in rows) / max(len(rows), 1)
        bucket = {
            "candidateMarker": marker,
            "candidateMarkerHash": stable_hash(marker),
            "supportObservationCount": len(rows),
            "supportPageCount": len(page_keys),
            "distinctSourceCount": len(source_ids),
            "sourceIds": source_ids,
            "avgTailRelativeOffset": round(avg_tail_offset, 4),
            "canonicalWrites": False,
        }
        bucket_rows.append(bucket)
        if len(page_keys) < min_support or len(source_ids) < min_sources:
            continue
        proposals.append(
            {
                "schemaVersion": "body-boundary-residual-cleanup-proposal.v0.1",
                "proposalId": f"body-boundary-residual:{stable_hash(marker)}",
                "proposalType": "pageTextCleanupTailMarker",
                "suggestedMarker": marker,
                "suggestedMarkerHash": stable_hash(marker),
                "suggestedTargets": proposal_targets(policy),
                "targetRulePath": policy.get("targetRulePath") or source_payload.get("pageTextCleanupRulePath") or "",
                "supportObservationCount": len(rows),
                "supportPageCount": len(page_keys),
                "distinctSourceCount": len(source_ids),
                "sourceIds": source_ids,
                "avgTailRelativeOffset": round(avg_tail_offset, 4),
                "sampleContexts": rows[:max_samples],
                "sandboxStatus": "pending",
                "requiresHumanGatedApply": True,
                "canonicalWrites": False,
                "generatedAt": now,
            }
        )
    proposals.sort(
        key=lambda row: (
            -int(row.get("supportPageCount") or 0),
            float(row.get("avgTailRelativeOffset") or 0.0)
            if not prefer_latest_tail
            else -float(row.get("avgTailRelativeOffset") or 0.0),
            str(row.get("suggestedMarker") or ""),
        )
    )
    if max_proposals:
        proposals = proposals[:max_proposals]
    bucket_rows.sort(
        key=lambda row: (
            -int(row.get("supportPageCount") or 0),
            float(row.get("avgTailRelativeOffset") or 0.0)
            if not prefer_latest_tail
            else -float(row.get("avgTailRelativeOffset") or 0.0),
            str(row.get("candidateMarker") or ""),
        )
    )
    return proposals, bucket_rows


def build_body_boundary_residual_cleanup_proposals(
    *,
    pages_paths: list[str | Path] | None = None,
    source_payload: dict[str, Any] | None = None,
    source_config: str | Path | None = DEFAULT_SOURCE_CONFIG_PATH,
    output_root: str | Path = DEFAULT_OUTPUT_ROOT,
    policy_override: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if source_payload is not None:
        payload = dict(source_payload)
    elif source_config:
        payload = dict(read_json(resolve_path(source_config)))
    else:
        payload = {}
    policy = active_policy(payload, policy_override)
    out_root = resolve_path(output_root)
    output_subdir = str(policy.get("outputSubdir") or "").strip()
    if output_subdir:
        out_root = out_root / output_subdir
    proposal_path = out_root / str(policy.get("proposalFileName") or DEFAULT_POLICY["proposalFileName"])
    observation_path = out_root / str(policy.get("observationFileName") or DEFAULT_POLICY["observationFileName"])
    summary_path = out_root / str(policy.get("summaryFileName") or DEFAULT_POLICY["summaryFileName"])

    if not bool(policy.get("enabled", True)):
        summary = {
            "schemaVersion": "body-boundary-residual-cleanup-summary.v0.1",
            "generatedAt": utc_now(),
            "enabled": False,
            "proposalCount": 0,
            "canonicalWrites": False,
        }
        write_json(summary_path, summary)
        return summary

    page_paths = [resolve_path(path) for path in pages_paths or discovered_pages_paths(payload)]
    page_paths = list(dict.fromkeys(path.resolve() for path in page_paths))
    seg_policy = segmentation_policy(payload)
    observations: list[dict[str, Any]] = []
    page_stats: list[dict[str, Any]] = []
    for pages_path in page_paths:
        rows = read_jsonl(pages_path)
        telemetry_index = telemetry_index_for_pages(pages_path, rows, seg_policy)
        page_observation_count = 0
        page_telemetry_match_count = 0
        for page in rows:
            telemetry = matching_telemetry(page, telemetry_index, seg_policy)
            if not telemetry:
                continue
            page_telemetry_match_count += 1
            raw_text = read_page_text(page, seg_policy)
            if not raw_text:
                continue
            page_observations = candidate_observations_for_page(
                pages_path=pages_path,
                page=page,
                telemetry=telemetry,
                raw_text=raw_text,
                source_payload=payload,
                policy=policy,
            )
            observations.extend(page_observations)
            page_observation_count += len(page_observations)
        page_stats.append(
            {
                "pagesJsonl": repo_relative(pages_path),
                "pageCount": len(rows),
                "telemetryMatchCount": page_telemetry_match_count,
                "observationCount": page_observation_count,
            }
        )

    proposals, candidate_buckets = build_proposals_from_observations(observations, source_payload=payload, policy=policy)
    write_jsonl(proposal_path, proposals)
    write_jsonl(observation_path, candidate_buckets)
    summary = {
        "schemaVersion": "body-boundary-residual-cleanup-summary.v0.1",
        "generatedAt": utc_now(),
        "enabled": True,
        "pagesPathCount": len(page_paths),
        "pageStats": page_stats,
        "rawObservationCount": len(observations),
        "candidateBucketCount": len(candidate_buckets),
        "proposalCount": len(proposals),
        "proposalPath": str(proposal_path.resolve()),
        "observationPath": str(observation_path.resolve()),
        "policy": {
            "tailWindowChars": policy.get("tailWindowChars"),
            "minTailRelativeOffset": policy.get("minTailRelativeOffset"),
            "minCandidateChars": policy.get("minCandidateChars"),
            "maxCandidateChars": policy.get("maxCandidateChars"),
            "tokenNgramSizes": policy.get("tokenNgramSizes"),
            "minSupportPageCount": policy.get("minSupportPageCount"),
            "minDistinctSourceCount": policy.get("minDistinctSourceCount"),
            "tailOffsetSort": policy.get("tailOffsetSort"),
            "excludeExistingRuleMarkers": policy.get("excludeExistingRuleMarkers"),
        },
        "canonicalWrites": False,
    }
    write_json(summary_path, summary)
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate body-boundary residual cleanup proposals.")
    parser.add_argument("--source-config", default=str(DEFAULT_SOURCE_CONFIG_PATH))
    parser.add_argument("--pages-jsonl", action="append", default=[])
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    summary = build_body_boundary_residual_cleanup_proposals(
        pages_paths=args.pages_jsonl or None,
        source_config=args.source_config,
        output_root=args.output_root,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
