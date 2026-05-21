from __future__ import annotations

import argparse
import html
import json
import os
import re
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote, urljoin, urlsplit, urlunsplit
from urllib.request import Request, urlopen

from repo_layout import pipeline_config_path, pipeline_root, resolve_npc_brain_root, resolve_repo_root
from sanguo_governance_loader import (
    SanguoGovernanceError,
    default_governance_root,
    load_external_source_benchmark_cue_rules,
    load_external_source_benchmark_policy,
    load_relationship_runtime_canon_policy,
)


REPO_ROOT = resolve_repo_root(__file__)
PIPELINE_ROOT = pipeline_root(REPO_ROOT)
DEFAULT_OUTPUT_ROOT = Path("local/codex-smoke/knowledge-growth")
DEFAULT_SOURCE_CONFIG = pipeline_config_path(REPO_ROOT, "external-evidence-sources.json")
DEFAULT_SEED_HARVEST_DEFAULTS = pipeline_config_path(REPO_ROOT, "external-evidence-seed-harvest-defaults.json")
DEFAULT_ALIAS_MAP = Path("artifacts/data-pipeline/sanguo-rag/extracted/alias-dictionary/formal-mention-map.json")
DEFAULT_SCOREBOARD_JSON = "auto"
NPC_BRAIN_ROOT = resolve_npc_brain_root(REPO_ROOT)
DEFAULT_GOVERNANCE_ROOT = default_governance_root()
RELATIONSHIP_RUNTIME_CANON_POLICY: dict[str, Any] = {}
RELATIONSHIP_POLICY_TEXT: dict[str, Any] = {}
A_ROMANCE_REVIEW_CAUTION_ZH_TW = ""
BODY_BOUNDARY_TELEMETRY_POLICY: dict[str, Any] = {}



def apply_external_source_benchmark_governance(policy: dict[str, Any], cue_rules: list[dict[str, Any]]) -> None:
    global SOURCE_CLASSES, DEFAULT_TERM_HIT_KEYWORDS, DEFAULT_PRECHECK_POLICY, DEFAULT_STAGE2_GATE_POLICY
    global DEFAULT_STAGE3_CLASS_GATE_POLICY, BODY_BOUNDARY_TELEMETRY_POLICY
    SOURCE_CLASSES = tuple(str(item).strip() for item in policy.get("sourceClasses") or [] if str(item).strip())
    DEFAULT_PRECHECK_POLICY = dict(policy.get("precheckDefaults") or {})
    DEFAULT_STAGE2_GATE_POLICY = dict(policy.get("stage2GateDefaults") or {})
    DEFAULT_STAGE3_CLASS_GATE_POLICY = {
        str(key): dict(value)
        for key, value in (policy.get("stage3ClassGateDefaults") or {}).items()
        if isinstance(value, dict)
    }
    BODY_BOUNDARY_TELEMETRY_POLICY = dict(policy.get("bodyBoundaryTelemetry") or {})
    by_name = {str(row.get("constantName") or ""): row for row in cue_rules}
    term_row = by_name.get("DEFAULT_TERM_HIT_KEYWORDS", {})
    DEFAULT_TERM_HIT_KEYWORDS = tuple(str(item).strip() for item in term_row.get("terms") or [] if str(item).strip())


def apply_relationship_runtime_canon_governance(policy: dict[str, Any]) -> None:
    global RELATIONSHIP_RUNTIME_CANON_POLICY, RELATIONSHIP_POLICY_TEXT, A_ROMANCE_REVIEW_CAUTION_ZH_TW
    RELATIONSHIP_RUNTIME_CANON_POLICY = dict(policy)
    RELATIONSHIP_POLICY_TEXT = (
        RELATIONSHIP_RUNTIME_CANON_POLICY.get("policyText")
        if isinstance(RELATIONSHIP_RUNTIME_CANON_POLICY.get("policyText"), dict)
        else {}
    )
    A_ROMANCE_REVIEW_CAUTION_ZH_TW = str(
        RELATIONSHIP_POLICY_TEXT.get("aRomanceReviewCautionZhTw")
        or "A-romance may be runtime canon only when sourceFamily/sourceLayer remain explicit."
    )


def resolve_default_cli(cli_name: str) -> Path:
    seen: set[Path] = set()
    ancestors: list[Path] = []
    for anchor in [REPO_ROOT, NPC_BRAIN_ROOT]:
        current = anchor
        for _ in range(len(current.parents) + 1):
            if current not in seen:
                ancestors.append(current)
                seen.add(current)
            if current.parent == current:
                break
            current = current.parent
    for root in ancestors:
        candidate = root / "tools_node" / "agent-clis" / cli_name
        if candidate.exists():
            return candidate
    return REPO_ROOT / "tools_node" / "agent-clis" / cli_name


DEFAULT_SOURCE_HEALTH_CLI = resolve_default_cli("3klife-source-health.js")
DEFAULT_HARVESTER_CLI = resolve_default_cli("3klife-web-page-harvester.js")
DEFAULT_BIOGRAPHY_EXTRACTOR = PIPELINE_ROOT / "extract_harvested_page_evidence_seeds.py"
DEFAULT_GENERIC_EXTRACTOR = PIPELINE_ROOT / "extract_generic_passage_evidence_seeds.py"
DEFAULT_SEED_HARVESTER = PIPELINE_ROOT / "harvest_external_evidence_seeds.py"
DEFAULT_SEED_SCORER = PIPELINE_ROOT / "score_external_evidence_seeds.py"
DEFAULT_SEED_PROMOTER = PIPELINE_ROOT / "promote_seed_to_evidence_card.py"
DEFAULT_SEED_ANCHOR_VERIFIER = PIPELINE_ROOT / "verify_seed_against_anchor_corpus.py"
DEFAULT_ANCHOR_INDEX_ROOT = Path("artifacts/data-pipeline/sanguo-rag/anchor-index")

SOURCE_CLASSES: tuple[str, ...] = ()

DEFAULT_TERM_HIT_KEYWORDS: tuple[str, ...] = ()
DEFAULT_PRECHECK_POLICY: dict[str, Any] = {}
DEFAULT_STAGE2_GATE_POLICY: dict[str, Any] = {}
DEFAULT_STAGE3_CLASS_GATE_POLICY: dict[str, dict[str, Any]] = {}


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def resolve_path(path_text: str | Path) -> Path:
    path = Path(path_text)
    return path if path.is_absolute() else (REPO_ROOT / path).resolve()


def resolve_existing_path(path_text: str | Path, *, fallback_roots: list[Path] | None = None) -> Path:
    base_path = Path(path_text)
    if base_path.is_absolute():
        return base_path

    search_roots = [
        REPO_ROOT,
        NPC_BRAIN_ROOT,
        REPO_ROOT.parent,
        NPC_BRAIN_ROOT.parent,
        REPO_ROOT.parent.parent,
        NPC_BRAIN_ROOT.parent.parent,
    ]
    if fallback_roots:
        search_roots.extend(fallback_roots)

    candidates: list[Path] = []
    seen = set()
    for root in search_roots:
        candidate = (root / base_path).resolve()
        if candidate not in seen:
            seen.add(candidate)
            candidates.append(candidate)

    for candidate in candidates:
        if candidate.exists():
            return candidate

    return candidates[0] if candidates else resolve_path(base_path)


def repo_relative(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(REPO_ROOT.resolve())).replace("\\", "/")
    except ValueError:
        return str(path.resolve())


def read_json(path: Path) -> Any:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8-sig"))


def scoreboard_auto_sentinels() -> set[str]:
    return {"", "auto", "latest", "default"}


def discover_scoreboard_candidates(defaults_payload: dict[str, Any]) -> list[Path]:
    discovery = defaults_payload.get("scoreboardDiscovery") if isinstance(defaults_payload, dict) else {}
    if not isinstance(discovery, dict):
        return []
    patterns = string_list(discovery.get("patterns")) or ["full-roster-scoreboard.json"]
    recursive = bool(discovery.get("recursive", True))
    candidates: list[Path] = []
    for root_text in string_list(discovery.get("roots")):
        root = resolve_path(root_text)
        if not root.exists():
            continue
        for pattern in patterns:
            iterator = root.rglob(pattern) if recursive else root.glob(pattern)
            candidates.extend(path for path in iterator if path.is_file())
    unique = {path.resolve(): path.resolve() for path in candidates}
    return sorted(unique.values(), key=lambda path: path.stat().st_mtime, reverse=True)


def resolve_scoreboard_path(path_text: str | Path) -> Path:
    raw_text = str(path_text or "").strip()
    if raw_text.lower() not in scoreboard_auto_sentinels():
        return resolve_existing_path(raw_text)

    defaults_payload = read_json(DEFAULT_SEED_HARVEST_DEFAULTS)
    candidate_texts = [
        *string_list(defaults_payload.get("scoreboardJson")),
        *string_list(defaults_payload.get("scoreboardJsonCandidates")),
    ]
    for candidate_text in candidate_texts:
        candidate = resolve_existing_path(candidate_text)
        if candidate.exists():
            return candidate

    discovered = discover_scoreboard_candidates(defaults_payload)
    if discovered:
        return discovered[0]

    searched_roots = string_list((defaults_payload.get("scoreboardDiscovery") or {}).get("roots"))
    raise FileNotFoundError(
        "No scoreboard JSON found from external evidence seed defaults. "
        f"defaults={repo_relative(DEFAULT_SEED_HARVEST_DEFAULTS)} roots={searched_roots}"
    )


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        text = line.strip()
        if not text:
            continue
        row = json.loads(text)
        if isinstance(row, dict):
            rows.append(row)
    return rows


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value] if value.strip() else []
    if not isinstance(value, list):
        return []
    return [str(item or "").strip() for item in value if str(item or "").strip()]


def int_policy(policy: dict[str, Any], key: str, fallback: int = 0) -> int:
    try:
        return max(int(policy.get(key) if policy.get(key) is not None else fallback), 0)
    except (TypeError, ValueError):
        return max(int(fallback), 0)


def stable_marker_hash(marker: str) -> str:
    if not marker:
        return ""
    return "sha256:" + stable_sha256_short(marker)


def strip_metadata_header_for_boundary(raw_text: str, telemetry_policy: dict[str, Any]) -> str:
    pattern = str(telemetry_policy.get("metadataHeaderSeparatorPattern") or "").strip()
    if not pattern:
        return raw_text
    parts = re.split(pattern, raw_text, maxsplit=1)
    return parts[1] if len(parts) == 2 else raw_text


def read_page_text_for_boundary(page: dict[str, Any], telemetry_policy: dict[str, Any]) -> tuple[str, str | None]:
    text_path_value = str(page.get("textPath") or "").strip()
    if text_path_value:
        text_path = resolve_existing_path(text_path_value)
        if text_path.exists():
            return strip_metadata_header_for_boundary(
                text_path.read_text(encoding="utf-8-sig", errors="ignore"),
                telemetry_policy,
            ), str(text_path)
    return str(page.get("snippet") or ""), str(resolve_existing_path(text_path_value)) if text_path_value else None


def source_extractor_policy(source_row: dict[str, Any] | None) -> dict[str, Any]:
    raw_policy = (source_row or {}).get("extractorPolicy") if isinstance(source_row, dict) else {}
    return raw_policy if isinstance(raw_policy, dict) else {}


def marker_fields_from_policy(source_row: dict[str, Any] | None, field_names: list[str]) -> list[str]:
    extractor_policy = source_extractor_policy(source_row)
    markers: list[str] = []
    for field_name in field_names:
        markers.extend(string_list(extractor_policy.get(field_name)))
    return markers


def load_boundary_cleanup_markers(telemetry_policy: dict[str, Any]) -> dict[str, list[str]]:
    rule_path_value = str(telemetry_policy.get("pageTextCleanupRulePath") or "").strip()
    if not rule_path_value:
        return {"noiseMarkers": [], "tailTrimMarkers": []}
    rule_path = resolve_existing_path(rule_path_value)
    if not rule_path.exists():
        return {"noiseMarkers": [], "tailTrimMarkers": []}
    roles = telemetry_policy.get("cleanupRuleConstantRoles")
    role_map = roles if isinstance(roles, dict) else {}
    extractors = set(string_list(telemetry_policy.get("cleanupRuleExtractors")))
    noise_names = set(string_list(role_map.get("noiseMarkers")))
    tail_names = set(string_list(role_map.get("tailTrimMarkers")))
    markers = {"noiseMarkers": [], "tailTrimMarkers": []}
    for row in read_jsonl(rule_path):
        extractor = str(row.get("extractor") or "").strip()
        if extractors and extractor not in extractors:
            continue
        constant_name = str(row.get("constantName") or "").strip()
        values = string_list(row.get("value"))
        if constant_name in noise_names:
            markers["noiseMarkers"].extend(values)
        if constant_name in tail_names:
            markers["tailTrimMarkers"].extend(values)
    for key in list(markers):
        seen: set[str] = set()
        unique = []
        for marker in sorted(markers[key], key=len, reverse=True):
            if marker in seen:
                continue
            seen.add(marker)
            unique.append(marker)
        markers[key] = unique
    return markers


def marker_occurrence_candidates(
    text: str,
    markers: list[str],
    *,
    role: str,
    boundary: str,
    limit_chars: int,
    offset_shift: int,
    offset_at_marker_end: bool = False,
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    search_text = text[:limit_chars] if limit_chars > 0 else text
    for marker in markers:
        start = 0
        while marker:
            index = search_text.find(marker, start)
            if index < 0:
                break
            offset = index + len(marker) if offset_at_marker_end else index + offset_shift
            candidates.append(
                {
                    "role": role,
                    "boundary": boundary,
                    "offset": offset,
                    "markerHash": stable_marker_hash(marker),
                }
            )
            start = index + len(marker)
    return candidates


def select_boundary_candidate(
    candidates: list[dict[str, Any]],
    *,
    strategy: str,
    fallback_offset: int,
    min_offset: int = 0,
    max_offset: int | None = None,
) -> dict[str, Any] | None:
    filtered = []
    for candidate in candidates:
        offset = int(candidate.get("offset") or 0)
        if offset < min_offset:
            continue
        if max_offset is not None and offset > max_offset:
            continue
        filtered.append({**candidate, "offset": offset})
    if not filtered:
        return None
    if strategy == "minOffset":
        return min(filtered, key=lambda row: row["offset"])
    if strategy == "maxOffset":
        return max(filtered, key=lambda row: row["offset"])
    return min(filtered, key=lambda row: abs(row["offset"] - fallback_offset))


def select_boundary_candidate_by_priority(
    candidates: list[dict[str, Any]],
    *,
    role_priority: list[str],
    strategy: str,
    fallback_offset: int,
    min_offset: int = 0,
    max_offset: int | None = None,
) -> dict[str, Any] | None:
    if not role_priority:
        return select_boundary_candidate(
            candidates,
            strategy=strategy,
            fallback_offset=fallback_offset,
            min_offset=min_offset,
            max_offset=max_offset,
        )
    for role in role_priority:
        scoped = [candidate for candidate in candidates if str(candidate.get("role") or "") == role]
        choice = select_boundary_candidate(
            scoped,
            strategy=strategy,
            fallback_offset=fallback_offset,
            min_offset=min_offset,
            max_offset=max_offset,
        )
        if choice:
            return choice
    return None


def derive_body_boundary_telemetry(
    *,
    page: dict[str, Any],
    raw_text: str,
    source_row: dict[str, Any] | None,
    term_hit_keywords: list[str],
    cleanup_markers: dict[str, list[str]],
    telemetry_policy: dict[str, Any],
    text_path: str | None,
) -> dict[str, Any]:
    start_limit = int_policy(telemetry_policy, "bodyStartSearchLimitChars", len(raw_text))
    term_context = int_policy(telemetry_policy, "termHitStartContextChars", 0)
    start_candidates: list[dict[str, Any]] = []
    for marker in marker_fields_from_policy(source_row, string_list(telemetry_policy.get("bodyStartMarkerFields"))):
        start_candidates.extend(
            marker_occurrence_candidates(
                raw_text,
                [marker],
                role="sourcePolicy.bodyStartMarkers",
                boundary="start",
                limit_chars=start_limit,
                offset_shift=len(marker),
            )
        )
    start_candidates.extend(
        marker_occurrence_candidates(
            raw_text,
            cleanup_markers.get("noiseMarkers") or [],
            role="cleanup.noiseMarkers",
            boundary="start",
            limit_chars=start_limit,
            offset_shift=0,
            offset_at_marker_end=True,
        )
    )
    for keyword in term_hit_keywords:
        for candidate in marker_occurrence_candidates(
            raw_text,
            [keyword],
            role="sourcePolicy.termHitKeywords",
            boundary="start",
            limit_chars=start_limit,
            offset_shift=-term_context,
        ):
            candidate["offset"] = max(int(candidate.get("offset") or 0), 0)
            start_candidates.append(candidate)

    start_choice = select_boundary_candidate_by_priority(
        start_candidates,
        role_priority=string_list(telemetry_policy.get("bodyStartCandidateRolePriority")),
        strategy=str(telemetry_policy.get("startCandidateStrategy") or ""),
        fallback_offset=0,
        max_offset=len(raw_text),
    )
    body_start = int(start_choice.get("offset") or 0) if start_choice else 0

    end_candidates: list[dict[str, Any]] = []
    for marker in marker_fields_from_policy(source_row, string_list(telemetry_policy.get("bodyEndMarkerFields"))):
        end_candidates.extend(
            marker_occurrence_candidates(
                raw_text,
                [marker],
                role="sourcePolicy.bodyEndMarkers",
                boundary="end",
                limit_chars=0,
                offset_shift=0,
            )
        )
    end_candidates.extend(
        marker_occurrence_candidates(
            raw_text,
            cleanup_markers.get("tailTrimMarkers") or [],
            role="cleanup.tailTrimMarkers",
            boundary="end",
            limit_chars=0,
            offset_shift=0,
        )
    )
    min_end = max(body_start + int_policy(telemetry_policy, "bodyEndMinOffset", 0), 0)
    end_choice = select_boundary_candidate_by_priority(
        end_candidates,
        role_priority=string_list(telemetry_policy.get("bodyEndCandidateRolePriority")),
        strategy=str(telemetry_policy.get("endCandidateStrategy") or ""),
        fallback_offset=len(raw_text),
        min_offset=min_end,
        max_offset=len(raw_text),
    )
    body_end = int(end_choice.get("offset") or len(raw_text)) if end_choice else len(raw_text)
    body_start = min(max(body_start, 0), len(raw_text))
    body_end = min(max(body_end, body_start), len(raw_text))
    body_text = raw_text[body_start:body_end]
    context_chars = int_policy(telemetry_policy, "contextChars", 0)
    telemetry_id = f"body-boundary:{page.get('sourceId') or ''}:{stable_sha256_short(str(page.get('pageId') or page.get('url') or text_path or ''))}"
    return {
        "schemaVersion": str(telemetry_policy.get("schemaVersion") or ""),
        "telemetryId": telemetry_id,
        "pageId": page.get("pageId"),
        "sourceId": page.get("sourceId"),
        "url": page.get("url"),
        "textPath": text_path,
        "textHash": page.get("textHash"),
        "rawTextLength": len(raw_text),
        "bodyStartOffset": body_start,
        "bodyEndOffset": body_end,
        "bodyTextLength": len(body_text),
        "bodyTextHash": "sha256:" + stable_sha256_short(body_text),
        "bodyStartReason": start_choice.get("role") if start_choice else "unbounded",
        "bodyEndReason": end_choice.get("role") if end_choice else "unbounded",
        "bodyStartMarkerHash": start_choice.get("markerHash") if start_choice else "",
        "bodyEndMarkerHash": end_choice.get("markerHash") if end_choice else "",
        "bodyStartContext": raw_text[body_start : body_start + context_chars] if context_chars > 0 else "",
        "bodyEndContext": raw_text[max(body_end - context_chars, 0) : body_end] if context_chars > 0 else "",
        "canonicalWrites": False,
        "generatedAt": utc_now(),
    }


def materialize_body_boundary_telemetry(
    *,
    harvest_root: Path,
    source_row: dict[str, Any] | None,
    term_hit_keywords: list[str] | None,
    telemetry_policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    active_policy = dict(telemetry_policy or BODY_BOUNDARY_TELEMETRY_POLICY)
    if not bool(active_policy.get("enabled")):
        return {"enabled": False, "pageCount": 0, "telemetryCount": 0}
    output_file = str(active_policy.get("outputFile") or "").strip()
    if not output_file:
        return {"enabled": True, "pageCount": 0, "telemetryCount": 0, "status": "missing-output-file"}
    pages_jsonl = harvest_root / "pages.jsonl"
    pages = read_jsonl(pages_jsonl)
    cleanup_markers = load_boundary_cleanup_markers(active_policy)
    telemetry_path = harvest_root / output_file
    telemetry_rows: list[dict[str, Any]] = []
    for page in pages:
        raw_text, text_path = read_page_text_for_boundary(page, active_policy)
        if not raw_text:
            continue
        telemetry = derive_body_boundary_telemetry(
            page=page,
            raw_text=raw_text,
            source_row=source_row,
            term_hit_keywords=term_hit_keywords or [],
            cleanup_markers=cleanup_markers,
            telemetry_policy=active_policy,
            text_path=text_path,
        )
        telemetry_rows.append(telemetry)
        telemetry_path_field = str(active_policy.get("pageTelemetryPathField") or "").strip()
        telemetry_id_field = str(active_policy.get("pageTelemetryIdField") or "").strip()
        if telemetry_path_field:
            page[telemetry_path_field] = str(telemetry_path.resolve())
        if telemetry_id_field:
            page[telemetry_id_field] = telemetry["telemetryId"]
    if pages_jsonl.exists():
        write_jsonl(pages_jsonl, pages)
    write_jsonl(telemetry_path, telemetry_rows)
    bounded_rows = [
        row
        for row in telemetry_rows
        if int(row.get("bodyStartOffset") or 0) > 0 or int(row.get("bodyEndOffset") or 0) < int(row.get("rawTextLength") or 0)
    ]
    return {
        "enabled": True,
        "schemaVersion": str(active_policy.get("schemaVersion") or ""),
        "path": str(telemetry_path.resolve()),
        "pageCount": len(pages),
        "telemetryCount": len(telemetry_rows),
        "boundedPageCount": len(bounded_rows),
        "noiseMarkerCount": len(cleanup_markers.get("noiseMarkers") or []),
        "tailTrimMarkerCount": len(cleanup_markers.get("tailTrimMarkers") or []),
        "canonicalWrites": False,
    }


def attach_body_boundary_summary(harvest_summary: dict[str, Any], boundary_summary: dict[str, Any]) -> dict[str, Any]:
    if not boundary_summary or not boundary_summary.get("enabled"):
        return harvest_summary
    outputs = harvest_summary.setdefault("outputs", {})
    metrics = harvest_summary.setdefault("metrics", {})
    if boundary_summary.get("path"):
        outputs["bodyBoundaryTelemetryJsonl"] = str(boundary_summary["path"])
    metrics["bodyBoundaryTelemetryCount"] = int(boundary_summary.get("telemetryCount") or 0)
    metrics["bodyBoundaryBoundedPageCount"] = int(boundary_summary.get("boundedPageCount") or 0)
    harvest_summary["bodyBoundaryTelemetry"] = boundary_summary
    summary_path_text = str(outputs.get("summaryJson") or "").strip()
    if summary_path_text:
        summary_path = resolve_existing_path(summary_path_text)
        write_json(summary_path, harvest_summary)
    return harvest_summary


def load_source_row_from_payload(payload: dict[str, Any], source_id: str) -> dict[str, Any] | None:
    rows = payload.get("sources") if isinstance(payload, dict) else []
    if not isinstance(rows, list):
        return None
    for row in rows:
        if isinstance(row, dict) and str(row.get("sourceId") or "").strip() == source_id:
            return row
    return None


def load_source_row(path: Path, source_id: str) -> dict[str, Any] | None:
    payload = read_json(path)
    return load_source_row_from_payload(payload if isinstance(payload, dict) else {}, source_id)


def to_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def to_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def normalize_string_list(raw_values: Any, fallback: list[str] | tuple[str, ...] | None = None) -> list[str]:
    if isinstance(raw_values, str):
        values = [raw_values]
    elif isinstance(raw_values, list) or isinstance(raw_values, tuple):
        values = [str(value or "") for value in raw_values]
    else:
        values = []
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        token = str(value or "").strip()
        if not token or token in seen:
            continue
        seen.add(token)
        normalized.append(token)
    if normalized:
        return normalized
    if fallback is not None:
        return normalize_string_list(fallback, fallback=None)
    return []


def resolve_precheck_policy(
    *,
    source_class: str,
    source_row: dict[str, Any] | None,
    source_config_payload: dict[str, Any],
) -> dict[str, Any]:
    pipeline_policies = source_config_payload.get("pipelinePolicies") if isinstance(source_config_payload, dict) else {}
    if not isinstance(pipeline_policies, dict):
        pipeline_policies = {}
    default_policy = pipeline_policies.get("precheckDefaults") if isinstance(pipeline_policies.get("precheckDefaults"), dict) else {}
    class_policy_map = (
        pipeline_policies.get("sourceClassPrecheck")
        if isinstance(pipeline_policies.get("sourceClassPrecheck"), dict)
        else {}
    )
    class_policy = class_policy_map.get(source_class) if isinstance(class_policy_map.get(source_class), dict) else {}
    source_policy = (source_row or {}).get("precheckPolicy") if isinstance((source_row or {}).get("precheckPolicy"), dict) else {}
    likely_threshold = to_int(
        source_policy.get("likelyThreshold"),
        to_int(class_policy.get("likelyThreshold"), to_int(default_policy.get("likelyThreshold"), int(DEFAULT_PRECHECK_POLICY["likelyThreshold"]))),
    )
    possible_threshold = to_int(
        source_policy.get("possibleThreshold"),
        to_int(class_policy.get("possibleThreshold"), to_int(default_policy.get("possibleThreshold"), int(DEFAULT_PRECHECK_POLICY["possibleThreshold"]))),
    )
    return {
        "likelyThreshold": max(likely_threshold, possible_threshold),
        "possibleThreshold": possible_threshold,
        "minimumTermHitCount": max(
            0,
            to_int(
                source_policy.get("minimumTermHitCount"),
                to_int(
                    class_policy.get("minimumTermHitCount"),
                    to_int(default_policy.get("minimumTermHitCount"), int(DEFAULT_PRECHECK_POLICY["minimumTermHitCount"])),
                ),
            ),
        ),
        "hintKeywords": normalize_string_list(
            source_policy.get("hintKeywords"),
            fallback=normalize_string_list(
                class_policy.get("hintKeywords"),
                fallback=normalize_string_list(default_policy.get("hintKeywords"), fallback=DEFAULT_PRECHECK_POLICY["hintKeywords"]),
            ),
        ),
        "loginPatterns": normalize_string_list(
            source_policy.get("loginPatterns"),
            fallback=normalize_string_list(
                class_policy.get("loginPatterns"),
                fallback=normalize_string_list(default_policy.get("loginPatterns"), fallback=DEFAULT_PRECHECK_POLICY["loginPatterns"]),
            ),
        ),
        "javascriptShellContentTypePrefixes": normalize_string_list(
            source_policy.get("javascriptShellContentTypePrefixes"),
            fallback=normalize_string_list(
                class_policy.get("javascriptShellContentTypePrefixes"),
                fallback=normalize_string_list(
                    default_policy.get("javascriptShellContentTypePrefixes"),
                    fallback=DEFAULT_PRECHECK_POLICY["javascriptShellContentTypePrefixes"],
                ),
            ),
        ),
        "loginGatedMaxTermHitCount": max(
            0,
            to_int(
                source_policy.get("loginGatedMaxTermHitCount"),
                to_int(
                    class_policy.get("loginGatedMaxTermHitCount"),
                    to_int(default_policy.get("loginGatedMaxTermHitCount"), int(DEFAULT_PRECHECK_POLICY["loginGatedMaxTermHitCount"])),
                ),
            ),
        ),
        "loginGatedMaxBytesRead": max(
            0,
            to_int(
                source_policy.get("loginGatedMaxBytesRead"),
                to_int(
                    class_policy.get("loginGatedMaxBytesRead"),
                    to_int(default_policy.get("loginGatedMaxBytesRead"), int(DEFAULT_PRECHECK_POLICY["loginGatedMaxBytesRead"])),
                ),
            ),
        ),
    }


def resolve_stage2_gate_policy(
    *,
    source_class: str,
    source_row: dict[str, Any] | None,
    source_config_payload: dict[str, Any],
) -> dict[str, float]:
    pipeline_policies = source_config_payload.get("pipelinePolicies") if isinstance(source_config_payload, dict) else {}
    if not isinstance(pipeline_policies, dict):
        pipeline_policies = {}
    defaults = pipeline_policies.get("stage2GateDefaults") if isinstance(pipeline_policies.get("stage2GateDefaults"), dict) else {}
    class_map = pipeline_policies.get("stage2ClassGate") if isinstance(pipeline_policies.get("stage2ClassGate"), dict) else {}
    class_policy = class_map.get(source_class) if isinstance(class_map.get(source_class), dict) else {}
    source_policy = (source_row or {}).get("stage2GatePolicy") if isinstance((source_row or {}).get("stage2GatePolicy"), dict) else {}
    return {
        "fetchSuccessRateMin": to_float(
            source_policy.get("fetchSuccessRateMin"),
            to_float(class_policy.get("fetchSuccessRateMin"), to_float(defaults.get("fetchSuccessRateMin"), float(DEFAULT_STAGE2_GATE_POLICY["fetchSuccessRateMin"]))),
        ),
        "relevantPageRateMin": to_float(
            source_policy.get("relevantPageRateMin"),
            to_float(class_policy.get("relevantPageRateMin"), to_float(defaults.get("relevantPageRateMin"), float(DEFAULT_STAGE2_GATE_POLICY["relevantPageRateMin"]))),
        ),
        "errorRateMax": to_float(
            source_policy.get("errorRateMax"),
            to_float(class_policy.get("errorRateMax"), to_float(defaults.get("errorRateMax"), float(DEFAULT_STAGE2_GATE_POLICY["errorRateMax"]))),
        ),
        "duplicateLinkRateMax": to_float(
            source_policy.get("duplicateLinkRateMax"),
            to_float(class_policy.get("duplicateLinkRateMax"), to_float(defaults.get("duplicateLinkRateMax"), float(DEFAULT_STAGE2_GATE_POLICY["duplicateLinkRateMax"]))),
        ),
    }


def resolve_stage3_gate_policy(
    *,
    source_class: str,
    source_row: dict[str, Any] | None,
    source_config_payload: dict[str, Any],
) -> dict[str, float]:
    pipeline_policies = source_config_payload.get("pipelinePolicies") if isinstance(source_config_payload, dict) else {}
    if not isinstance(pipeline_policies, dict):
        pipeline_policies = {}
    default_class_map = (
        pipeline_policies.get("stage3ClassGateDefaults")
        if isinstance(pipeline_policies.get("stage3ClassGateDefaults"), dict)
        else {}
    )
    class_default_policy = (
        default_class_map.get(source_class)
        if isinstance(default_class_map.get(source_class), dict)
        else {}
    )
    class_fallback = DEFAULT_STAGE3_CLASS_GATE_POLICY.get(source_class) or {}
    source_policy = (source_row or {}).get("stage3GatePolicy") if isinstance((source_row or {}).get("stage3GatePolicy"), dict) else {}
    merged: dict[str, float] = {}
    for key, fallback_value in class_fallback.items():
        merged[key] = to_float(
            source_policy.get(key),
            to_float(class_default_policy.get(key), float(fallback_value)),
        )
    return merged


def infer_source_class(source_row: dict[str, Any] | None) -> str:
    if source_row and source_row.get("sourceClass") in SOURCE_CLASSES:
        return str(source_row["sourceClass"])
    adapter_type = str((source_row or {}).get("adapterType") or "").strip()
    source_family = str((source_row or {}).get("sourceFamily") or "").strip()
    if adapter_type in {"wikisource", "scan_pdf", "gutenberg_text"}:
        return "primary-text-site"
    if "character" in source_family or "biography" in source_family:
        return "high-yield-character-site"
    return "community-worldbuilding-site"


def run_command(command: list[str], *, cwd: Path = REPO_ROOT) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    completed = subprocess.run(
        command,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="ignore",
        check=False,
        env=env,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            "Command failed (rc={rc}): {cmd}\nSTDOUT:\n{stdout}\nSTDERR:\n{stderr}".format(
                rc=completed.returncode,
                cmd=" ".join(command),
                stdout=completed.stdout.strip(),
                stderr=completed.stderr.strip(),
            )
        )
    return completed


def run_json_command(command: list[str], *, cwd: Path = REPO_ROOT) -> dict[str, Any]:
    completed = run_command(command, cwd=cwd)
    stdout = completed.stdout.strip()
    if not stdout:
        raise RuntimeError(f"Expected JSON output but stdout was empty: {' '.join(command)}")
    return json.loads(stdout)


def source_health_precheck_via_python(
    *,
    source_id: str,
    url: str,
    timeout_seconds: float,
    source_row: dict[str, Any] | None,
) -> dict[str, Any]:
    request_url = normalize_request_url(url)
    term_keywords = normalize_term_hit_keywords((source_row or {}).get("termHitKeywords"))
    payload: dict[str, Any] = {
        "version": "1.0.0",
        "generatedAt": utc_now(),
        "mode": "source-health-python-fallback",
        "sourceId": source_id,
        "url": url,
        "finalUrl": request_url,
        "httpStatus": 0,
        "liveStatus": "fetch-error",
        "reason": "",
        "title": "",
        "snippet": "",
        "termHitCount": 0,
        "bytesRead": 0,
        "contentType": "",
        "healthBackend": "python-urllib",
        "canonicalWrites": False,
    }
    try:
        request = Request(
            request_url,
            headers={
                "User-Agent": "Mozilla/5.0 (3KLife Source Health Fallback)",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,text/plain;q=0.8,*/*;q=0.5",
            },
        )
        with urlopen(request, timeout=max(timeout_seconds, 1.0)) as response:
            content = response.read(128_000)
            content_type = str(response.headers.get("Content-Type") or "")
            charset = detect_charset_from_bytes(content_type, content)
            raw_text = content.decode(charset, errors="ignore")
            plain_text = strip_html_to_text(raw_text) if "<" in raw_text and ">" in raw_text else raw_text
            title = extract_title_from_html(raw_text) or source_id
            status = int(getattr(response, "status", 0) or response.getcode() or 0)
            payload.update(
                {
                    "finalUrl": str(getattr(response, "url", "") or request_url),
                    "httpStatus": status,
                    "liveStatus": "ok" if status == 200 else "http-error",
                    "reason": "ok" if status == 200 else f"httpStatus={status}",
                    "title": title,
                    "snippet": plain_text[:1200],
                    "termHitCount": count_term_hits(plain_text, term_keywords),
                    "bytesRead": len(content),
                    "contentType": content_type,
                    "charset": charset,
                }
            )
    except Exception as exc:
        payload["reason"] = f"{type(exc).__name__}: {exc}"
    return payload


def bool_ratio(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return numerator / denominator


def duplicate_link_rate(discovered: int, selected: int) -> float:
    if discovered <= 0 or selected <= 0 or selected < discovered:
        return 0.0
    return max(0.0, 1.0 - bool_ratio(selected, discovered))


def stage1_precheck(
    *,
    source_id: str,
    url: str,
    timeout_seconds: float,
    source_health_cli: Path,
    source_health_mode: str,
    source_config_path: Path,
    source_row: dict[str, Any] | None,
    precheck_policy: dict[str, Any],
) -> tuple[dict[str, Any], list[str], bool]:
    mode = str(source_health_mode or "auto").strip().lower()
    if mode == "off":
        payload = {
            "version": "1.0.0",
            "generatedAt": utc_now(),
            "mode": "source-health-disabled",
            "sourceId": source_id,
            "url": url,
            "httpStatus": 200,
            "liveStatus": "source-health-disabled",
            "reason": "source-health-mode=off",
            "title": source_id,
            "snippet": source_id,
            "termHitCount": max(1, to_int(precheck_policy.get("minimumTermHitCount"), 1)),
            "bytesRead": 0,
            "contentType": "text/plain",
            "healthBackend": "disabled",
            "canonicalWrites": False,
        }
    elif mode == "python" or (mode == "auto" and not source_health_cli.exists()):
        payload = source_health_precheck_via_python(
            source_id=source_id,
            url=url,
            timeout_seconds=timeout_seconds,
            source_row=source_row,
        )
    else:
        command = [
            "node",
            str(source_health_cli),
            "--source-id",
            source_id,
            "--url",
            url,
            "--timeout-seconds",
            str(max(timeout_seconds, 1.0)),
            "--sources-config",
            str(source_config_path),
            "--json",
        ]
        for keyword in normalize_term_hit_keywords((source_row or {}).get("termHitKeywords")):
            command.extend(["--term-hit-keyword", str(keyword)])
        payload = run_json_command(command)
    snippet = str(payload.get("snippet") or "")
    title = str(payload.get("title") or "")
    combined = f"{title}\n{snippet}".lower()
    reasons: list[str] = []
    minimum_term_hit_count = max(0, to_int(precheck_policy.get("minimumTermHitCount"), 1))
    login_patterns = normalize_string_list(precheck_policy.get("loginPatterns"), fallback=DEFAULT_PRECHECK_POLICY["loginPatterns"])
    javascript_prefixes = normalize_string_list(
        precheck_policy.get("javascriptShellContentTypePrefixes"),
        fallback=DEFAULT_PRECHECK_POLICY["javascriptShellContentTypePrefixes"],
    )
    login_gated_max_hits = max(0, to_int(precheck_policy.get("loginGatedMaxTermHitCount"), 1))
    login_gated_max_bytes = max(0, to_int(precheck_policy.get("loginGatedMaxBytesRead"), 8000))
    if int(payload.get("httpStatus") or 0) != 200:
        reasons.append(f"httpStatus={payload.get('httpStatus')}")
    if int(payload.get("termHitCount") or 0) < minimum_term_hit_count:
        reasons.append(f"termHitCount<{minimum_term_hit_count}")
    if not (snippet.strip() or title.strip()):
        reasons.append("deterministic-text-empty")
    login_hit = any(pattern.lower() in combined for pattern in login_patterns)
    if login_hit and int(payload.get("termHitCount") or 0) <= login_gated_max_hits and int(payload.get("bytesRead") or 0) < login_gated_max_bytes:
        reasons.append("login-gated")
    content_type = str(payload.get("contentType") or "").lower()
    if any(content_type.startswith(prefix.lower()) for prefix in javascript_prefixes):
        reasons.append("javascript-shell-content-type")
    passed = not reasons
    return payload, reasons, passed


def write_single_source_health_summary(path: Path, source_id: str, source_url: str, source_class: str, precheck: dict[str, Any]) -> None:
    write_json(
        path,
        {
            "version": "1.0.0",
            "generatedAt": utc_now(),
            "mode": "benchmark-single-source-health-summary",
            "canonicalWrites": False,
            "sourceChecks": [
                {
                    "sourceId": source_id,
                    "sourceClass": source_class,
                    "baseUrl": source_url,
                    **precheck,
                }
            ],
        },
    )


def gather_angle_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counter = Counter(str(row.get("angleType") or "") for row in rows if isinstance(row, dict))
    return dict(sorted((angle, count) for angle, count in counter.items() if angle))


def angle_label_zh_tw(angle_type: str) -> str:
    mapping = {
        "identity": "身分",
        "relationship": "關係",
        "event": "事件",
        "title": "官職/稱號",
        "trait": "特質",
        "role": "角色/定位",
        "location": "地點",
        "habit": "習慣",
        "activity": "活動",
        "worldbuilding_note": "世界觀補充",
        "dialogue_seed": "對話素材",
        "source_conflict": "來源衝突",
    }
    return mapping.get(angle_type, angle_type)


def build_review_summary_zh_tw(row: dict[str, Any]) -> str:
    person_label = str(row.get("matchedName") or row.get("personId") or "").strip() or "此人物"
    angle_type = str(row.get("angleType") or "").strip()
    quote = str(row.get("quote") or "")
    source_layer = str(row.get("sourceLayer") or "").strip()
    layer_label = {
        "history": "史料層",
        "romance": "演義層",
        "worldbuilding": "世界觀層",
        "encyclopedia": "整理層",
    }.get(source_layer, "來源層")
    base = {
        "identity": f"這句主要在確認「{person_label}」的身分或人物對應，審核時先看是不是明確在介紹同一個人。",
        "relationship": f"這句主要在看「{person_label}」與其他人物的親屬、婚配或從屬關係，審核時先抓主語與關係方向。",
        "event": f"這句主要在看「{person_label}」參與的事件或行動，審核時先確認動作主體與事件邊界。",
        "title": f"這句主要在看「{person_label}」的官職、封號或稱謂，審核時先確認它是不是正式頭銜。",
        "trait": f"這句主要在看「{person_label}」的性格、容貌或能力描寫，審核時先確認這不是單純事件敘述。",
        "role": f"這句主要在看「{person_label}」的角色定位或身份功能，審核時先分清楚它是不是人物關係而不是官職。",
        "location": f"這句主要在看「{person_label}」涉及的地點線索，審核時先確認地名是不是明確落地。",
        "habit": f"這句主要在看「{person_label}」的習慣或偏好，審核時先分清楚它是不是穩定特徵而不是單次事件。",
        "activity": f"這句主要在看「{person_label}」做過的生活或任務活動，審核時先抓動作主體與行為類型。",
        "worldbuilding_note": f"這句偏向「{person_label}」的演義/整理型補充素材，適合世界觀用途，審核時先分清楚它不是正史硬證。",
    }.get(angle_type, f"這句是在補「{person_label}」的 {angle_label_zh_tw(angle_type)} 線索。")

    caution_parts: list[str] = []
    if "、" in quote or quote.count("，") >= 2:
        caution_parts.append("這句同時提到多人，審核時要先確認真正掛到誰身上。")
    if len(quote) >= 80:
        caution_parts.append("句子偏長，建議先看前半句主語，再看後半句補述。")
    if source_layer == "romance":
        caution_parts.append(A_ROMANCE_REVIEW_CAUTION_ZH_TW)
    elif source_layer == "worldbuilding":
        caution_parts.append("這是整理/世界觀層資料，適合 seed 或 B 級旁證。")
    else:
        caution_parts.append(f"目前歸在 {layer_label}，可優先當成嚴格交叉驗證的候選。")
    if row.get("relationshipSubjectHint") and row.get("relationshipObjectHint") and row.get("relationshipAnchorLabel"):
        caution_parts.append(
            f"目前降噪器暫判主客體為「{row['relationshipSubjectHint']} -> {row['relationshipObjectHint']}」，關係詞是「{row['relationshipAnchorLabel']}」。"
        )
    if row.get("relationshipLegalityPassed") is False:
        caution_parts.append(
            f"此句未通過合法關係組合檢查（{row.get('relationshipLegalityReason') or 'unknown'}），建議只做人工參考不自動升級。"
        )
    if person_label.startswith("子") or person_label in {"王立", "子桓", "子孝"}:
        caution_parts.append("這個名字像字號或泛稱，審核時要特別確認不是誤掛到別的歷史人物。")
    return f"{base} {' '.join(caution_parts)}".strip()


def body_example_quality(row: dict[str, Any]) -> tuple[float, float]:
    person_label = str(row.get("matchedName") or row.get("personId") or "").strip()
    person_id = str(row.get("personId") or "").strip()
    quote = str(row.get("quote") or "")
    score = float(row.get("seedConfidenceScore") or 0.0)
    if person_id.startswith("romance-person-"):
        score -= 20.0
    if person_label.startswith("子") or person_label in {"王立", "子桓", "子孝"}:
        score -= 18.0
    if re.match(r"^\d+\s", quote):
        score -= 8.0
    if len(person_label) >= 3:
        score += 6.0
    if 20 <= len(quote) <= 80:
        score += 3.0
    if any(token in quote for token in ("字", "妻", "女", "殺", "攻", "嫁", "娶", "官", "將軍")):
        score += 2.0
    return score, float(row.get("seedConfidenceScore") or 0.0)


def body_text_examples(ranking_summary: dict[str, Any], limit: int = 8) -> list[dict[str, Any]]:
    ranked = ranking_summary.get("rankedSeeds") if isinstance(ranking_summary, dict) else []
    if not isinstance(ranked, list):
        return []
    candidates: list[dict[str, Any]] = []
    for row in ranked:
        if not isinstance(row, dict):
            continue
        if str(row.get("contentSource") or "") != "page-text":
            continue
        candidate = {
            "personId": str(row.get("generalId") or row.get("candidatePersonId") or "").strip(),
            "matchedName": str(row.get("matchedName") or row.get("generalId") or row.get("candidatePersonId") or "").strip(),
            "angleType": str(row.get("angleType") or "").strip(),
            "angleLabelZhTw": angle_label_zh_tw(str(row.get("angleType") or "").strip()),
            "seedConfidenceScore": float(row.get("seedConfidenceScore") or 0.0),
            "pageTitle": row.get("pageTitle"),
            "sourceUrl": row.get("sourceUrl"),
            "locator": row.get("locator"),
            "quote": row.get("translatedTraditionalText") or row.get("quote") or row.get("seedText"),
            "originalQuote": row.get("quote") or row.get("seedText"),
            "sourceLayer": row.get("sourceLayer"),
            "relationshipSubjectHint": row.get("relationshipSubjectHint"),
            "relationshipObjectHint": row.get("relationshipObjectHint"),
            "relationshipAnchorLabel": row.get("relationshipAnchorLabel"),
            "relationshipLegalityPassed": row.get("relationshipLegalityPassed"),
            "relationshipLegalityReason": row.get("relationshipLegalityReason"),
        }
        candidate["reviewSummaryZhTw"] = build_review_summary_zh_tw(candidate)
        candidates.append(candidate)
    candidates.sort(key=body_example_quality, reverse=True)
    examples: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for candidate in candidates:
        dedupe_key = (
            str(candidate.get("personId") or ""),
            str(candidate.get("angleType") or ""),
            str(candidate.get("quote") or ""),
        )
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        examples.append(candidate)
        if len(examples) >= limit:
            break
    return examples


def detect_charset_from_bytes(content_type: str, content: bytes) -> str:
    header_match = re.search(r"charset\s*=\s*[\"']?([a-zA-Z0-9._-]+)", str(content_type or ""), flags=re.I)
    if header_match:
        value = header_match.group(1).strip().lower()
        if value == "utf8":
            return "utf-8"
        if value in {"gb2312", "gb_2312-80", "gb18030"}:
            return "gbk"
        return value
    probe = content[:2048].decode("ascii", errors="ignore")
    meta_match = re.search(r"charset\s*=\s*[\"']?\s*([a-zA-Z0-9._-]+)", probe, flags=re.I)
    if meta_match:
        value = meta_match.group(1).strip().lower()
        if value == "utf8":
            return "utf-8"
        if value in {"gb2312", "gb_2312-80", "gb18030"}:
            return "gbk"
        return value
    return "utf-8"


def strip_html_to_text(raw_html: str) -> str:
    text = re.sub(r"(?is)<script[^>]*>.*?</script>", " ", raw_html)
    text = re.sub(r"(?is)<style[^>]*>.*?</style>", " ", text)
    text = re.sub(r"(?is)<br\s*/?>", "\n", text)
    text = re.sub(r"(?is)</p\s*>", "\n", text)
    text = re.sub(r"(?is)<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def extract_title_from_html(raw_html: str) -> str:
    match = re.search(r"(?is)<title[^>]*>(.*?)</title>", raw_html)
    if not match:
        return ""
    return strip_html_to_text(match.group(1))[:180]


def normalize_term_hit_keywords(raw_keywords: Any) -> list[str]:
    if isinstance(raw_keywords, str):
        values = [raw_keywords]
    elif isinstance(raw_keywords, list):
        values = [str(value or "") for value in raw_keywords]
    else:
        values = list(DEFAULT_TERM_HIT_KEYWORDS)
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        token = str(value or "").strip()
        if not token or token in seen:
            continue
        seen.add(token)
        normalized.append(token)
    return normalized or list(DEFAULT_TERM_HIT_KEYWORDS)


def count_term_hits(text: str, term_hit_keywords: list[str] | None = None) -> int:
    patterns = term_hit_keywords or list(DEFAULT_TERM_HIT_KEYWORDS)
    return sum(text.count(pattern) for pattern in patterns)


def normalize_request_url(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit(
        (
            parts.scheme,
            parts.netloc,
            quote(parts.path, safe="/%"),
            quote(parts.query, safe="=&%"),
            quote(parts.fragment, safe="%"),
        )
    )


def normalize_crawl_url(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, parts.query, ""))


class LinkHrefExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.hrefs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        for name, value in attrs:
            if name.lower() == "href" and value:
                self.hrefs.append(str(value).strip())
                return


def extract_hrefs(raw_html: str) -> list[str]:
    parser = LinkHrefExtractor()
    try:
        parser.feed(raw_html)
    except Exception:
        return list(parser.hrefs)
    return list(parser.hrefs)


def link_match_candidates(url: str) -> list[str]:
    parts = urlsplit(url)
    encoded_path = quote(parts.path, safe="/%")
    encoded_query = quote(parts.query, safe="=&%")
    path_query = urlunsplit(("", "", encoded_path, encoded_query, ""))
    absolute = urlunsplit((parts.scheme, parts.netloc, encoded_path, encoded_query, ""))
    raw_path_query = urlunsplit(("", "", parts.path, parts.query, ""))
    return [
        absolute,
        normalize_crawl_url(url),
        parts.path,
        encoded_path,
        path_query,
        raw_path_query,
        unquote(parts.path),
        unquote(path_query),
        unquote(raw_path_query),
    ]


def matches_link_patterns(url: str, patterns: list[str]) -> bool:
    if not patterns:
        return True
    candidates = link_match_candidates(url)
    for pattern in patterns:
        try:
            if any(re.search(pattern, candidate) for candidate in candidates):
                return True
        except re.error:
            if any(pattern in candidate for candidate in candidates):
                return True
    return False


def discover_policy_links(
    *,
    raw_html: str,
    base_url: str,
    link_include: list[str],
    link_exclude: list[str],
    same_origin: bool,
) -> list[str]:
    base_parts = urlsplit(base_url)
    base_origin = (base_parts.scheme.lower(), base_parts.netloc.lower())
    discovered: list[str] = []
    seen: set[str] = set()
    for href in extract_hrefs(raw_html):
        if not href or href.startswith(("#", "javascript:", "mailto:")):
            continue
        absolute_url = normalize_crawl_url(urljoin(base_url, html.unescape(href)))
        parts = urlsplit(absolute_url)
        if parts.scheme not in {"http", "https"}:
            continue
        if same_origin and (parts.scheme.lower(), parts.netloc.lower()) != base_origin:
            continue
        if not matches_link_patterns(absolute_url, link_include):
            continue
        if link_exclude and matches_link_patterns(absolute_url, link_exclude):
            continue
        normalized = normalize_request_url(absolute_url)
        if normalized in seen:
            continue
        seen.add(normalized)
        discovered.append(normalized)
    return discovered


def fetch_html_document(
    *,
    url: str,
    timeout_seconds: float,
    user_agent: str,
) -> dict[str, Any]:
    request_url = normalize_request_url(url)
    request = Request(
        request_url,
        headers={
            "User-Agent": user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,text/plain;q=0.8,*/*;q=0.5",
        },
    )
    with urlopen(request, timeout=max(timeout_seconds, 1.0)) as response:
        content = response.read()
        content_type = str(response.headers.get("Content-Type") or "")
        charset = detect_charset_from_bytes(content_type, content)
        raw_html = content.decode(charset, errors="ignore")
        plain_text = strip_html_to_text(raw_html) if "<" in raw_html and ">" in raw_html else raw_html
        status = int(getattr(response, "status", 0) or response.getcode() or 0)
        final_url = normalize_crawl_url(str(getattr(response, "url", "") or request_url))
        return {
            "url": request_url,
            "finalUrl": final_url,
            "httpStatus": status,
            "liveStatus": "ok" if status == 200 else "http-error",
            "contentType": content_type,
            "charset": charset,
            "bytesRead": len(content),
            "rawHtml": raw_html,
            "plainText": plain_text,
            "title": extract_title_from_html(raw_html),
        }


def harvest_single_page(
    *,
    source_id: str,
    source_url: str,
    run_root: Path,
    timeout_seconds: float,
    source_row: dict[str, Any] | None = None,
    term_hit_keywords: list[str] | None = None,
) -> dict[str, Any]:
    harvest_root = run_root / "harvest"
    harvest_root.mkdir(parents=True, exist_ok=True)
    request_url = normalize_request_url(source_url)
    request = Request(
        request_url,
        headers={
            "User-Agent": "Mozilla/5.0 (3KLife Single Page Benchmark Harvester)",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        },
    )
    with urlopen(request, timeout=max(timeout_seconds, 1.0)) as response:
        content = response.read()
        content_type = str(response.headers.get("Content-Type") or "")
        charset = detect_charset_from_bytes(content_type, content)
        raw_html = content.decode(charset, errors="ignore")
        title = extract_title_from_html(raw_html)
        plain_text = strip_html_to_text(raw_html)
        text_hash = f"sha256:{stable_sha256_short(plain_text)}"
    page_text_dir = harvest_root / "page-texts"
    page_text_dir.mkdir(parents=True, exist_ok=True)
    page_text_path = page_text_dir / f"0001-{stable_sha256_short(source_url)}.txt"
    page_text_path.write_text(
        "\n".join(
            [
                f"sourceId: {source_id}",
                f"url: {source_url}",
                f"title: {title}",
                f"textHash: {text_hash}",
                "canonicalWrites: false",
                "",
                plain_text,
                "",
            ]
        ),
        encoding="utf-8",
    )
    hit_count = count_term_hits(plain_text, term_hit_keywords)
    page_row = {
        "pageId": f"page:{source_id}:{stable_sha256_short(source_url)}",
        "sourceId": source_id,
        "url": source_url,
        "discoveredFrom": source_url,
        "pageIndex": 1,
        "httpStatus": 200,
        "liveStatus": "ok",
        "contentType": content_type,
        "charset": charset,
        "bytesRead": len(content),
        "title": title,
        "termHitCount": hit_count,
        "relevanceLevel": "likely-relevant" if hit_count >= 3 else "possible-relevant",
        "textHash": text_hash,
        "textPath": str(page_text_path.resolve()),
        "snippet": plain_text[:800],
        "textLength": len(plain_text),
        "canonicalWrites": False,
    }
    pages_jsonl = harvest_root / "pages.jsonl"
    pages_jsonl.write_text(json.dumps(page_row, ensure_ascii=False) + "\n", encoding="utf-8")
    errors_jsonl = harvest_root / "fetch-errors.jsonl"
    errors_jsonl.write_text("", encoding="utf-8")
    boundary_summary = materialize_body_boundary_telemetry(
        harvest_root=harvest_root,
        source_row=source_row,
        term_hit_keywords=term_hit_keywords or [],
    )
    summary = {
        "version": "1.0.0",
        "generatedAt": utc_now(),
        "mode": "single-page-harvest",
        "sourceId": source_id,
        "canonicalWrites": False,
        "metrics": {
            "discoveredLinkCount": 1,
            "selectedLinkCount": 1,
            "fetchedPageCount": 1,
            "relevantPageCount": 1 if page_row["termHitCount"] > 0 else 0,
            "errorCount": 0,
        },
        "outputs": {
            "pagesJsonl": str(pages_jsonl.resolve()),
            "errorsJsonl": str(errors_jsonl.resolve()),
            "summaryJson": str((harvest_root / "harvest-summary.json").resolve()),
            "summaryMarkdown": str((harvest_root / "harvest-summary.zh-TW.md").resolve()),
            "pageTextDir": str(page_text_dir.resolve()),
        },
        "samplePages": [
            {
                "title": title,
                "url": source_url,
                "termHitCount": page_row["termHitCount"],
            }
        ],
    }
    attach_body_boundary_summary(summary, boundary_summary)
    write_json(harvest_root / "harvest-summary.json", summary)
    (harvest_root / "harvest-summary.zh-TW.md").write_text(
        "\n".join(
            [
                "# Single Page Harvest Summary",
                "",
                f"- Source: `{source_id}`",
                f"- URL: `{source_url}`",
                f"- Title: {title}",
                f"- Term Hit Count: `{page_row['termHitCount']}`",
                f"- canonicalWrites: `{False}`",
                "",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return summary


def harvest_policy_via_python_fallback(
    *,
    source_id: str,
    source_url: str,
    source_row: dict[str, Any] | None,
    harvest_policy: dict[str, Any],
    run_root: Path,
    timeout_seconds: float,
    max_pages: int,
    link_include: list[str],
    link_exclude: list[str],
    same_origin: bool,
    term_hit_keywords: list[str],
) -> dict[str, Any]:
    harvest_root = run_root / "harvest"
    harvest_root.mkdir(parents=True, exist_ok=True)
    page_text_dir = harvest_root / "page-texts"
    page_text_dir.mkdir(parents=True, exist_ok=True)
    index_url = str(harvest_policy.get("indexUrl") or source_url)
    index_doc = fetch_html_document(
        url=index_url,
        timeout_seconds=timeout_seconds,
        user_agent="Mozilla/5.0 (3KLife Policy Benchmark Harvester)",
    )
    discovered_links = discover_policy_links(
        raw_html=str(index_doc.get("rawHtml") or ""),
        base_url=str(index_doc.get("finalUrl") or index_url),
        link_include=link_include,
        link_exclude=link_exclude,
        same_origin=same_origin,
    )
    selected_urls = discovered_links[: max(1, max_pages)]
    used_index_as_page = False
    if not selected_urls:
        selected_urls = [str(index_doc.get("finalUrl") or index_url)]
        used_index_as_page = True

    pages: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for page_index, page_url in enumerate(selected_urls, start=1):
        try:
            if used_index_as_page and page_index == 1:
                doc = dict(index_doc)
            else:
                doc = fetch_html_document(
                    url=page_url,
                    timeout_seconds=timeout_seconds,
                    user_agent="Mozilla/5.0 (3KLife Policy Benchmark Harvester)",
                )
            plain_text = str(doc.get("plainText") or "")
            title = str(doc.get("title") or source_id)
            text_hash = f"sha256:{stable_sha256_short(plain_text)}"
            resolved_url = str(doc.get("finalUrl") or page_url)
            page_text_path = page_text_dir / f"{page_index:04d}-{stable_sha256_short(resolved_url)}.txt"
            page_text_path.write_text(
                "\n".join(
                    [
                        f"sourceId: {source_id}",
                        f"url: {resolved_url}",
                        f"title: {title}",
                        f"textHash: {text_hash}",
                        "canonicalWrites: false",
                        "",
                        plain_text,
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            hit_count = count_term_hits(plain_text, term_hit_keywords)
            pages.append(
                {
                    "pageId": f"page:{source_id}:{stable_sha256_short(resolved_url)}",
                    "sourceId": source_id,
                    "url": resolved_url,
                    "discoveredFrom": index_url,
                    "pageIndex": page_index,
                    "httpStatus": int(doc.get("httpStatus") or 0),
                    "liveStatus": str(doc.get("liveStatus") or "ok"),
                    "contentType": str(doc.get("contentType") or ""),
                    "charset": str(doc.get("charset") or ""),
                    "bytesRead": int(doc.get("bytesRead") or 0),
                    "title": title,
                    "termHitCount": hit_count,
                    "relevanceLevel": "likely-relevant" if hit_count >= 3 else "possible-relevant",
                    "textHash": text_hash,
                    "textPath": str(page_text_path.resolve()),
                    "snippet": plain_text[:800],
                    "textLength": len(plain_text),
                    "canonicalWrites": False,
                }
            )
        except Exception as exc:
            errors.append(
                {
                    "sourceId": source_id,
                    "url": page_url,
                    "pageIndex": page_index,
                    "errorType": type(exc).__name__,
                    "message": str(exc),
                    "canonicalWrites": False,
                }
            )

    pages_jsonl = harvest_root / "pages.jsonl"
    pages_jsonl.write_text(
        "".join(json.dumps(page, ensure_ascii=False) + "\n" for page in pages),
        encoding="utf-8",
    )
    errors_jsonl = harvest_root / "fetch-errors.jsonl"
    errors_jsonl.write_text(
        "".join(json.dumps(error, ensure_ascii=False) + "\n" for error in errors),
        encoding="utf-8",
    )
    boundary_summary = materialize_body_boundary_telemetry(
        harvest_root=harvest_root,
        source_row=source_row,
        term_hit_keywords=term_hit_keywords,
    )
    summary = {
        "version": "1.1.0",
        "generatedAt": utc_now(),
        "mode": "policy-harvest-python-fallback",
        "sourceId": source_id,
        "canonicalWrites": False,
        "metrics": {
            "discoveredLinkCount": len(discovered_links),
            "selectedLinkCount": len(selected_urls),
            "fetchedPageCount": len(pages),
            "relevantPageCount": sum(1 for page in pages if int(page.get("termHitCount") or 0) > 0),
            "errorCount": len(errors),
        },
        "outputs": {
            "pagesJsonl": str(pages_jsonl.resolve()),
            "errorsJsonl": str(errors_jsonl.resolve()),
            "summaryJson": str((harvest_root / "harvest-summary.json").resolve()),
            "summaryMarkdown": str((harvest_root / "harvest-summary.zh-TW.md").resolve()),
            "pageTextDir": str(page_text_dir.resolve()),
        },
        "discovery": {
            "indexUrl": index_url,
            "finalIndexUrl": str(index_doc.get("finalUrl") or index_url),
            "linkInclude": list(link_include),
            "linkExclude": list(link_exclude),
            "sameOrigin": same_origin,
            "usedIndexAsFallbackPage": used_index_as_page,
        },
        "samplePages": [
            {
                "title": page.get("title"),
                "url": page.get("url"),
                "termHitCount": page.get("termHitCount"),
            }
            for page in pages[:10]
        ],
    }
    attach_body_boundary_summary(summary, boundary_summary)
    write_json(harvest_root / "harvest-summary.json", summary)
    (harvest_root / "harvest-summary.zh-TW.md").write_text(
        "\n".join(
            [
                "# Policy Harvest Python Fallback Summary",
                "",
                f"- Source: `{source_id}`",
                f"- Index URL: `{index_url}`",
                f"- Discovered Links: `{len(discovered_links)}`",
                f"- Selected Pages: `{len(selected_urls)}`",
                f"- Fetched Pages: `{len(pages)}`",
                f"- Error Count: `{len(errors)}`",
                f"- canonicalWrites: `{False}`",
                "",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return summary


def stable_sha256_short(text: str, length: int = 16) -> str:
    import hashlib

    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:length]


def harvest_single_page_via_harvester(
    *,
    source_id: str,
    source_url: str,
    run_root: Path,
    timeout_seconds: float,
    source_config_path: Path,
    harvester_cli: Path,
    term_hit_keywords: list[str] | None = None,
) -> dict[str, Any]:
    harvest_root = run_root / "harvest"
    command = [
        "node",
        str(harvester_cli),
        "--source-id",
        source_id,
        "--index-url",
        source_url,
        "--max-pages",
        "1",
        "--concurrency",
        "1",
        "--timeout-seconds",
        str(max(timeout_seconds, 1.0)),
        "--sources-config",
        str(source_config_path),
        "--output-root",
        str(harvest_root),
        "--include-index-page",
        "--same-origin",
        "--json",
    ]
    for keyword in term_hit_keywords or []:
        command.extend(["--term-hit-keyword", str(keyword)])
    return run_json_command(command)


def harvest_source(
    *,
    source_id: str,
    source_url: str,
    source_row: dict[str, Any] | None,
    source_config_path: Path,
    args: argparse.Namespace,
    harvester_cli: Path,
    run_root: Path,
) -> tuple[dict[str, Any] | None, list[str]]:
    harvest_policy = (source_row or {}).get("harvestPolicy") or {}
    if not harvest_policy:
        single_page_policy = (source_row or {}).get("singlePagePolicy") or {}
        if single_page_policy:
            try:
                term_keywords = normalize_term_hit_keywords((source_row or {}).get("termHitKeywords"))
                harvest_summary = harvest_single_page_via_harvester(
                    source_id=source_id,
                    source_url=source_url,
                    run_root=run_root,
                    timeout_seconds=args.timeout_seconds,
                    source_config_path=source_config_path,
                    harvester_cli=harvester_cli,
                    term_hit_keywords=term_keywords,
                )
                boundary_summary = materialize_body_boundary_telemetry(
                    harvest_root=run_root / "harvest",
                    source_row=source_row,
                    term_hit_keywords=term_keywords,
                )
                return attach_body_boundary_summary(harvest_summary, boundary_summary), []
            except Exception as exc:
                try:
                    fallback = harvest_single_page(
                        source_id=source_id,
                        source_url=source_url,
                        run_root=run_root,
                        timeout_seconds=args.timeout_seconds,
                        source_row=source_row,
                        term_hit_keywords=normalize_term_hit_keywords((source_row or {}).get("termHitKeywords")),
                    )
                    fallback["harvestBackend"] = "python-single-page-fallback"
                    fallback["fallbackReason"] = f"{type(exc).__name__}: {exc}"
                    return fallback, []
                except Exception as fallback_exc:
                    return None, [
                        f"single-page-harvester-failed:{type(exc).__name__}",
                        f"python-single-page-fallback-failed:{type(fallback_exc).__name__}",
                    ]
        return None, ["missing-harvestPolicy-or-singlePagePolicy"]
    link_include = args.link_include or list(harvest_policy.get("linkInclude") or [])
    link_exclude = list(harvest_policy.get("linkExclude") or [])
    same_origin = bool(args.same_origin or harvest_policy.get("sameOrigin"))
    link_extraction_mode = str(harvest_policy.get("linkExtractionMode") or "").strip()
    table_class_contains = str(harvest_policy.get("tableClassContains") or "").strip()
    api_url = str(harvest_policy.get("apiUrl") or "").strip()
    api_method = str(harvest_policy.get("apiMethod") or "").strip()
    api_list_path = str(harvest_policy.get("apiListPath") or "").strip()
    api_url_field = str(harvest_policy.get("apiUrlField") or "").strip()
    api_title_field = str(harvest_policy.get("apiTitleField") or "").strip()
    api_snippet_field = str(harvest_policy.get("apiSnippetField") or "").strip()
    api_people_field = str(harvest_policy.get("apiPeopleField") or "").strip()
    api_headers = harvest_policy.get("apiHeaders") if isinstance(harvest_policy.get("apiHeaders"), dict) else {}
    api_body_template = harvest_policy.get("apiBodyTemplate")
    api_start_page = harvest_policy.get("apiStartPage")
    api_max_index_pages = harvest_policy.get("apiMaxIndexPages")
    table_column_index: int | None = None
    if harvest_policy.get("tableColumnIndex") is not None:
        try:
            table_column_index = max(0, int(harvest_policy.get("tableColumnIndex")))
        except (TypeError, ValueError):
            table_column_index = None
    max_pages = max(1, int(args.sample_size))
    policy_max_pages_raw = harvest_policy.get("maxPages")
    if policy_max_pages_raw is not None:
        try:
            policy_max_pages = max(1, int(policy_max_pages_raw))
            max_pages = min(max_pages, policy_max_pages)
        except (TypeError, ValueError):
            pass
    harvest_root = run_root / "harvest"
    command = [
        "node",
        str(harvester_cli),
        "--source-id",
        source_id,
        "--index-url",
        str(harvest_policy.get("indexUrl") or source_url),
        "--max-pages",
        str(max_pages),
        "--concurrency",
        str(max(1, int(args.concurrency))),
        "--timeout-seconds",
        str(max(args.timeout_seconds, 1.0)),
        "--sources-config",
        str(source_config_path),
        "--output-root",
        str(harvest_root),
        "--json",
    ]
    for keyword in normalize_term_hit_keywords((source_row or {}).get("termHitKeywords")):
        command.extend(["--term-hit-keyword", str(keyword)])
    for pattern in link_include:
        command.extend(["--link-include", str(pattern)])
    for pattern in link_exclude:
        command.extend(["--link-exclude", str(pattern)])
    if link_extraction_mode:
        command.extend(["--link-extraction-mode", link_extraction_mode])
    if table_class_contains:
        command.extend(["--table-class-contains", table_class_contains])
    if table_column_index is not None:
        command.extend(["--table-column-index", str(table_column_index)])
    if api_url:
        command.extend(["--api-url", api_url])
    if api_method:
        command.extend(["--api-method", api_method])
    if api_headers:
        command.extend(["--api-headers-json", json.dumps(api_headers, ensure_ascii=False)])
    if api_body_template is not None:
        command.extend(["--api-body-template", json.dumps(api_body_template, ensure_ascii=False)])
    if api_list_path:
        command.extend(["--api-list-path", api_list_path])
    if api_url_field:
        command.extend(["--api-url-field", api_url_field])
    if api_title_field:
        command.extend(["--api-title-field", api_title_field])
    if api_snippet_field:
        command.extend(["--api-snippet-field", api_snippet_field])
    if api_people_field:
        command.extend(["--api-people-field", api_people_field])
    if api_start_page is not None:
        command.extend(["--api-start-page", str(api_start_page)])
    if api_max_index_pages is not None:
        command.extend(["--api-max-index-pages", str(api_max_index_pages)])
    if same_origin:
        command.append("--same-origin")
    try:
        if not harvester_cli.exists():
            raise FileNotFoundError(str(harvester_cli))
        harvest_summary = run_json_command(command)
        boundary_summary = materialize_body_boundary_telemetry(
            harvest_root=harvest_root,
            source_row=source_row,
            term_hit_keywords=normalize_term_hit_keywords((source_row or {}).get("termHitKeywords")),
        )
        return attach_body_boundary_summary(harvest_summary, boundary_summary), []
    except Exception as exc:
        try:
            term_keywords = normalize_term_hit_keywords((source_row or {}).get("termHitKeywords"))
            fallback = harvest_policy_via_python_fallback(
                source_id=source_id,
                source_url=source_url,
                source_row=source_row,
                harvest_policy=harvest_policy,
                run_root=run_root,
                timeout_seconds=args.timeout_seconds,
                max_pages=max_pages,
                link_include=link_include,
                link_exclude=link_exclude,
                same_origin=same_origin,
                term_hit_keywords=term_keywords,
            )
            fallback["harvestBackend"] = "python-policy-fallback"
            fallback["fallbackReason"] = f"{type(exc).__name__}: {exc}"
            return fallback, []
        except Exception as fallback_exc:
            return None, [
                f"harvester-failed:{type(exc).__name__}",
                f"python-policy-fallback-failed:{type(fallback_exc).__name__}",
            ]


def evaluate_stage2(harvest_summary: dict[str, Any], gate_policy: dict[str, float]) -> tuple[dict[str, Any], list[str]]:
    discovered = int(((harvest_summary.get("metrics") or {}).get("discoveredLinkCount") or 0))
    selected = int(((harvest_summary.get("metrics") or {}).get("selectedLinkCount") or 0))
    fetched = int(((harvest_summary.get("metrics") or {}).get("fetchedPageCount") or 0))
    relevant = int(((harvest_summary.get("metrics") or {}).get("relevantPageCount") or 0))
    errors = int(((harvest_summary.get("metrics") or {}).get("errorCount") or 0))
    metrics = {
        "samplePageCount": selected,
        "fetchedPageCount": fetched,
        "relevantPageCount": relevant,
        "fetchSuccessRate": bool_ratio(fetched, max(selected, 1)),
        "relevantPageRate": bool_ratio(relevant, max(fetched, 1)),
        "errorRate": bool_ratio(errors, max(selected, 1)),
        "duplicateLinkRate": duplicate_link_rate(discovered, selected),
        "outputs": harvest_summary.get("outputs") or {},
    }
    reasons: list[str] = []
    fetch_success_rate_min = to_float(gate_policy.get("fetchSuccessRateMin"), float(DEFAULT_STAGE2_GATE_POLICY["fetchSuccessRateMin"]))
    relevant_page_rate_min = to_float(gate_policy.get("relevantPageRateMin"), float(DEFAULT_STAGE2_GATE_POLICY["relevantPageRateMin"]))
    error_rate_max = to_float(gate_policy.get("errorRateMax"), float(DEFAULT_STAGE2_GATE_POLICY["errorRateMax"]))
    duplicate_link_rate_max = to_float(gate_policy.get("duplicateLinkRateMax"), float(DEFAULT_STAGE2_GATE_POLICY["duplicateLinkRateMax"]))
    if metrics["fetchSuccessRate"] < fetch_success_rate_min:
        reasons.append(f"fetchSuccessRate<{fetch_success_rate_min:.2f}")
    if metrics["relevantPageRate"] < relevant_page_rate_min:
        reasons.append(f"relevantPageRate<{relevant_page_rate_min:.2f}")
    if metrics["errorRate"] > error_rate_max:
        reasons.append(f"errorRate>{error_rate_max:.2f}")
    if metrics["duplicateLinkRate"] > duplicate_link_rate_max:
        reasons.append(f"duplicateLinkRate>{duplicate_link_rate_max:.2f}")
    return metrics, reasons


def run_seed_pipeline(
    *,
    source_id: str,
    source_class: str,
    run_root: Path,
    harvest_root: Path,
    source_config_path: Path,
    alias_map_path: Path,
    scoreboard_path: Path,
    single_source_health_path: Path,
    anchor_first_verification: bool,
    anchor_index_root: Path,
    anchor_verification_topk: int,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    extracted_root = run_root / "extracted-seeds"
    standard_root = run_root / "standard-pipeline"
    extractor_path = resolve_path(
        DEFAULT_BIOGRAPHY_EXTRACTOR if source_class == "high-yield-character-site" else DEFAULT_GENERIC_EXTRACTOR
    )
    extractor_command = [
        sys.executable,
        str(extractor_path),
        "--source-id",
        source_id,
        "--pages-jsonl",
        str(harvest_root / "pages.jsonl"),
        "--source-config",
        str(source_config_path),
        "--alias-map",
        str(alias_map_path),
        "--scoreboard-json",
        str(scoreboard_path),
        "--output-root",
        str(extracted_root),
        "--overwrite",
    ]
    if source_class != "high-yield-character-site":
        extractor_command.extend(["--source-class", source_class])
    run_command(extractor_command)
    extract_summary = read_json(extracted_root / "manual-evidence-seeds-summary.json")

    run_command(
        [
            sys.executable,
            str(resolve_path(DEFAULT_SEED_HARVESTER)),
            "--no-default-external-evidence-cards",
            "--manual-seeds-jsonl",
            str(extracted_root / "manual-evidence-seeds.jsonl"),
            "--scoreboard-json",
            str(scoreboard_path),
            "--source-health-summary",
            str(single_source_health_path),
            "--output-root",
            str(standard_root),
            "--overwrite",
        ]
    )
    scored_seed_input_path = standard_root / "external-evidence-seeds.jsonl"
    if anchor_first_verification:
        anchor_root = standard_root / "anchor-verification"
        run_command(
            [
                sys.executable,
                str(resolve_path(DEFAULT_SEED_ANCHOR_VERIFIER)),
                "--seeds-jsonl",
                str(scored_seed_input_path),
                "--anchor-index-root",
                str(anchor_index_root),
                "--output-root",
                str(anchor_root),
                "--topk",
                str(max(int(anchor_verification_topk), 1)),
            ]
        )
        scored_seed_input_path = anchor_root / "seed-anchor-verification.jsonl"
    run_command(
        [
            sys.executable,
            str(resolve_path(DEFAULT_SEED_SCORER)),
            "--seeds-jsonl",
            str(scored_seed_input_path),
            "--output-root",
            str(standard_root),
            "--overwrite",
        ]
    )
    run_command(
        [
            sys.executable,
            str(resolve_path(DEFAULT_SEED_PROMOTER)),
            "--ranking-json",
            str(standard_root / "external-evidence-seed-ranking.json"),
            "--output-root",
            str(standard_root),
            "--overwrite",
        ]
    )
    ranking_summary = read_json(standard_root / "external-evidence-seed-ranking.json")
    candidate_summary = read_json(standard_root / "candidate-evidence-card-summary.json")
    return extract_summary, ranking_summary, candidate_summary


def stage3_metrics_common(
    *,
    extract_summary: dict[str, Any],
    ranking_summary: dict[str, Any],
    candidate_summary: dict[str, Any],
    fetched_pages: int,
    run_root: Path,
) -> dict[str, Any]:
    extract_metrics = extract_summary.get("metrics") or {}
    ranking_metrics = ranking_summary.get("metrics") or {}
    candidate_metrics = candidate_summary.get("metrics") or {}
    page_count = int(extract_metrics.get("pageCount") or 0)
    seed_count = int(ranking_metrics.get("seedCount") or 0)
    candidate_count = int(candidate_metrics.get("candidateCardCount") or 0)
    preview_count = int(ranking_metrics.get("previewCount") or 0)
    canonical_people = int(extract_metrics.get("uniqueCanonicalGeneralCount") or 0)
    shadow_people = int(extract_metrics.get("uniqueShadowPersonCount") or 0)
    canonical_match_page_count = int(extract_metrics.get("matchedCanonicalPageCount") or 0)
    claim_bearing_passages = int(extract_metrics.get("claimBearingPassageCount") or 0)
    quote_locator_hash_coverage = float(extract_metrics.get("quoteLocatorHashCoverage") or 0.0)
    return {
        "seedCount": seed_count,
        "candidateCardCount": candidate_count,
        "previewCount": preview_count,
        "canonicalPeople": canonical_people,
        "shadowPeople": shadow_people,
        "seedPerPage": bool_ratio(seed_count, max(fetched_pages, 1)),
        "candidateCardPerPage": bool_ratio(candidate_count, max(fetched_pages, 1)),
        "canonicalMatchPageRate": bool_ratio(canonical_match_page_count, max(page_count, 1)),
        "pageTextSeedCount": int(extract_metrics.get("pageTextSeedCount") or 0),
        "claimBearingPassageCount": claim_bearing_passages,
        "quoteLocatorHashCoverage": quote_locator_hash_coverage,
        "outputs": {
            "extractSummary": repo_relative(run_root / "extracted-seeds" / "manual-evidence-seeds-summary.json"),
            "anchorVerificationSummary": repo_relative(
                run_root / "standard-pipeline" / "anchor-verification" / "seed-anchor-verification-summary.json"
            ),
            "rankingJson": repo_relative(run_root / "standard-pipeline" / "external-evidence-seed-ranking.json"),
            "candidateSummary": repo_relative(run_root / "standard-pipeline" / "candidate-evidence-card-summary.json"),
        },
    }


def evaluate_stage3(source_class: str, metrics: dict[str, Any], gate_policy: dict[str, float]) -> list[str]:
    reasons: list[str] = []
    if source_class == "high-yield-character-site":
        seed_per_page_min = to_float(gate_policy.get("seedPerPageMin"), 1.0)
        candidate_card_per_page_min = to_float(gate_policy.get("candidateCardPerPageMin"), 0.40)
        canonical_match_page_rate_min = to_float(gate_policy.get("canonicalMatchPageRateMin"), 0.40)
        shadow_people_min = to_int(gate_policy.get("shadowPeopleMin"), 15)
        if metrics["seedPerPage"] < seed_per_page_min:
            reasons.append(f"seedPerPage<{seed_per_page_min:.2f}")
        if metrics["candidateCardPerPage"] < candidate_card_per_page_min:
            reasons.append(f"candidateCardPerPage<{candidate_card_per_page_min:.2f}")
        if metrics["canonicalMatchPageRate"] < canonical_match_page_rate_min and metrics["shadowPeople"] < shadow_people_min:
            reasons.append(
                f"canonicalMatchPageRate<{canonical_match_page_rate_min:.2f} and shadowPeople<{shadow_people_min}"
            )
        return reasons
    if source_class == "primary-text-site":
        quote_locator_hash_coverage_min = to_float(gate_policy.get("quoteLocatorHashCoverageMin"), 0.90)
        claim_bearing_passage_count_min = to_int(gate_policy.get("claimBearingPassageCountMin"), 20)
        if metrics["quoteLocatorHashCoverage"] < quote_locator_hash_coverage_min:
            reasons.append(f"quoteLocatorHashCoverage<{quote_locator_hash_coverage_min:.2f}")
        if metrics["claimBearingPassageCount"] < claim_bearing_passage_count_min:
            reasons.append(f"claimBearingPassageCount<{claim_bearing_passage_count_min}")
        return reasons
    if source_class == "community-worldbuilding-site":
        seed_per_page_min = to_float(gate_policy.get("seedPerPageMin"), 0.80)
        candidate_card_per_page_min = to_float(gate_policy.get("candidateCardPerPageMin"), 0.20)
        page_text_seed_count_min = to_int(gate_policy.get("pageTextSeedCountMin"), 1)
        claim_bearing_passage_count_min = to_int(gate_policy.get("claimBearingPassageCountMin"), 1)
        if metrics["seedPerPage"] < seed_per_page_min:
            reasons.append(f"seedPerPage<{seed_per_page_min:.2f}")
        if metrics["candidateCardPerPage"] < candidate_card_per_page_min:
            reasons.append(f"candidateCardPerPage<{candidate_card_per_page_min:.2f}")
        if metrics["pageTextSeedCount"] < page_text_seed_count_min:
            reasons.append(f"pageTextSeedCount<{page_text_seed_count_min}")
        if metrics["claimBearingPassageCount"] < claim_bearing_passage_count_min:
            reasons.append(f"claimBearingPassageCount<{claim_bearing_passage_count_min}")
        return reasons
    return ["unsupported-sourceClass"]


def render_markdown(summary: dict[str, Any]) -> str:
    precheck = summary["stage1Precheck"]
    policy_block = summary.get("policies") or {}
    precheck_policy = policy_block.get("precheckPolicy") or {}
    stage2_policy = policy_block.get("stage2GatePolicy") or {}
    stage3_policy = policy_block.get("stage3GatePolicy") or {}
    harvest = summary.get("stage2Harvest") or {}
    yield_stage = summary.get("stage3Yield") or {}
    lines = [
        "# 外部網站採證 Benchmark",
        "",
        f"- Source: `{summary['sourceId']}`",
        f"- Source Class: `{summary['sourceClass']}`",
        f"- URL: {summary['url']}",
        f"- Final Verdict: `{summary['finalVerdict']}`",
        f"- canonicalWrites: `{summary['canonicalWrites']}`",
        f"- Generated At: `{summary['generatedAt']}`",
        "",
        "## Stage 1 Precheck",
        "",
        f"- HTTP Status: `{precheck.get('httpStatus')}`",
        f"- termHitCount: `{precheck.get('termHitCount')}`",
        (
            f"- Precheck Policy: `likely={precheck_policy.get('likelyThreshold')} / "
            f"possible={precheck_policy.get('possibleThreshold')} / minHit={precheck_policy.get('minimumTermHitCount')}`"
        ),
        f"- Stage 1 Passed: `{summary['stage1Passed']}`",
        f"- Failure Reasons: `{', '.join(summary['stage1FailureReasons']) or 'none'}`",
        "",
    ]
    if harvest:
        lines.extend(
            [
                "## Stage 2 Harvest",
                "",
                f"- Selected Pages: `{harvest.get('samplePageCount')}`",
                f"- Fetched Pages: `{harvest.get('fetchedPageCount')}`",
                f"- Relevant Page Rate: `{harvest.get('relevantPageRate', 0.0):.2%}`",
                f"- Fetch Success Rate: `{harvest.get('fetchSuccessRate', 0.0):.2%}`",
                f"- Duplicate Link Rate: `{harvest.get('duplicateLinkRate', 0.0):.2%}`",
                f"- Stage 2 Passed: `{summary.get('stage2Passed')}`",
                f"- Failure Reasons: `{', '.join(summary.get('stage2FailureReasons') or []) or 'none'}`",
                (
                    f"- Stage 2 Policy: `success>={stage2_policy.get('fetchSuccessRateMin')} / "
                    f"relevant>={stage2_policy.get('relevantPageRateMin')} / "
                    f"error<={stage2_policy.get('errorRateMax')} / dup<={stage2_policy.get('duplicateLinkRateMax')}`"
                ),
                "",
            ]
        )
    if yield_stage:
        lines.extend(
            [
                "## Stage 3 Yield",
                "",
                f"- Seed Count: `{yield_stage.get('seedCount')}`",
                f"- Candidate Card Count: `{yield_stage.get('candidateCardCount')}`",
                f"- Preview Count: `{yield_stage.get('previewCount')}`",
                f"- Canonical People: `{yield_stage.get('canonicalPeople')}`",
                f"- Shadow People: `{yield_stage.get('shadowPeople')}`",
                f"- Seed / Page: `{yield_stage.get('seedPerPage', 0.0):.2f}`",
                f"- Candidate Card / Page: `{yield_stage.get('candidateCardPerPage', 0.0):.2f}`",
                f"- Canonical Match Page Rate: `{yield_stage.get('canonicalMatchPageRate', 0.0):.2%}`",
                f"- Claim-bearing Passages: `{yield_stage.get('claimBearingPassageCount', 0)}`",
                f"- Quote/Locator/Hash Coverage: `{yield_stage.get('quoteLocatorHashCoverage', 0.0):.2%}`",
                f"- Stage 3 Passed: `{summary.get('stage3Passed')}`",
                f"- Failure Reasons: `{', '.join(summary.get('stage3FailureReasons') or []) or 'none'}`",
                f"- Stage 3 Policy: `{json.dumps(stage3_policy, ensure_ascii=False)}`",
                "",
                "## 內文採樣例",
                "",
                "| Person | Angle | Score | Quote | 中文審核摘要 |",
                "| --- | --- | ---: | --- | --- |",
            ]
        )
        examples = summary.get("bodyTextExamples") or []
        if examples:
            for row in examples:
                quote = str(row.get("quote") or "").replace("\n", " ").replace("|", "\\|")
                if len(quote) > 110:
                    quote = quote[:107] + "..."
                review_summary = str(row.get("reviewSummaryZhTw") or "").replace("\n", " ").replace("|", "\\|")
                if len(review_summary) > 120:
                    review_summary = review_summary[:117] + "..."
                lines.append(
                    f"| `{row['personId']}` | `{row['angleLabelZhTw']}` | {float(row['seedConfidenceScore']):.2f} | {quote} | {review_summary} |"
                )
        else:
            lines.append("| _none_ | _none_ | 0.00 | no page-text seeds | 無正文 seed |")
        lines.append("")
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Benchmark one external evidence source through deterministic three-stage gates.")
    parser.add_argument("--source-id", required=True)
    parser.add_argument("--url", default=None)
    parser.add_argument("--source-class", default=None)
    parser.add_argument("--sample-size", type=int, default=30)
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    parser.add_argument("--source-config", default=str(DEFAULT_SOURCE_CONFIG))
    parser.add_argument("--governance-root", default=str(DEFAULT_GOVERNANCE_ROOT))
    parser.add_argument("--external-source-benchmark-policy", default=None)
    parser.add_argument("--external-source-benchmark-cue-rules", default=None)
    parser.add_argument("--alias-map", default=str(DEFAULT_ALIAS_MAP))
    parser.add_argument(
        "--scoreboard-json",
        default=DEFAULT_SCOREBOARD_JSON,
        help="Scoreboard JSON path, or 'auto' to resolve from external-evidence-seed-harvest-defaults.json.",
    )
    parser.add_argument("--source-health-cli", default=str(DEFAULT_SOURCE_HEALTH_CLI))
    parser.add_argument("--source-health-mode", choices=["auto", "node", "python", "off"], default="auto")
    parser.add_argument("--harvester-cli", default=str(DEFAULT_HARVESTER_CLI))
    parser.add_argument("--anchor-first-verification", dest="anchor_first_verification", action="store_true")
    parser.add_argument("--no-anchor-first-verification", dest="anchor_first_verification", action="store_false")
    parser.set_defaults(anchor_first_verification=True)
    parser.add_argument("--anchor-index-root", default=str(DEFAULT_ANCHOR_INDEX_ROOT))
    parser.add_argument("--anchor-verification-topk", type=int, default=8)
    parser.add_argument("--timeout-seconds", type=float, default=20.0)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--link-include", action="append", default=[])
    parser.add_argument("--same-origin", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        benchmark_policy = load_external_source_benchmark_policy(
            args.governance_root,
            external_source_benchmark_policy=args.external_source_benchmark_policy,
        )
        benchmark_cue_rules = load_external_source_benchmark_cue_rules(
            args.governance_root,
            external_source_benchmark_cue_rules=args.external_source_benchmark_cue_rules,
        )
        relationship_policy = load_relationship_runtime_canon_policy(args.governance_root)
    except SanguoGovernanceError as exc:
        raise SystemExit(f"[benchmark_external_source] FAIL {exc}") from None
    apply_external_source_benchmark_governance(benchmark_policy, benchmark_cue_rules)
    apply_relationship_runtime_canon_governance(relationship_policy)

    source_config_path = resolve_path(args.source_config)
    source_config_payload = read_json(source_config_path)
    if not isinstance(source_config_payload, dict):
        source_config_payload = {}
    source_row = load_source_row_from_payload(source_config_payload, args.source_id)
    source_class = args.source_class or infer_source_class(source_row)
    source_url = str(args.url or (source_row or {}).get("baseUrl") or "").strip()
    if not source_url:
        raise SystemExit("source url is required when sourceId is not found or has no baseUrl")
    if source_class not in SOURCE_CLASSES:
        raise SystemExit(f"unsupported sourceClass: {source_class}")
    precheck_policy = resolve_precheck_policy(
        source_class=source_class,
        source_row=source_row,
        source_config_payload=source_config_payload,
    )
    stage2_gate_policy = resolve_stage2_gate_policy(
        source_class=source_class,
        source_row=source_row,
        source_config_payload=source_config_payload,
    )
    stage3_gate_policy = resolve_stage3_gate_policy(
        source_class=source_class,
        source_row=source_row,
        source_config_payload=source_config_payload,
    )

    run_id = args.run_id or f"benchmark-{args.source_id}-{utc_stamp()}"
    run_root = resolve_path(args.output_root) / run_id
    if run_root.exists() and any(run_root.iterdir()) and not args.overwrite:
        raise SystemExit(f"Output already exists: {repo_relative(run_root)}")
    run_root.mkdir(parents=True, exist_ok=True)

    source_health_cli = resolve_path(args.source_health_cli)
    harvester_cli = resolve_path(args.harvester_cli)
    alias_map_path = resolve_existing_path(args.alias_map)
    scoreboard_path = resolve_scoreboard_path(args.scoreboard_json)
    anchor_index_root = resolve_existing_path(args.anchor_index_root)
    single_source_health_path = run_root / "single-source-health-summary.json"
    benchmark_summary_path = run_root / "benchmark-summary.json"
    benchmark_markdown_path = run_root / "benchmark-summary.zh-TW.md"

    precheck_payload, stage1_reasons, stage1_passed = stage1_precheck(
        source_id=args.source_id,
        url=source_url,
        timeout_seconds=args.timeout_seconds,
        source_health_cli=source_health_cli,
        source_health_mode=args.source_health_mode,
        source_config_path=source_config_path,
        source_row=source_row,
        precheck_policy=precheck_policy,
    )
    write_single_source_health_summary(single_source_health_path, args.source_id, source_url, source_class, precheck_payload)

    stage2_reasons: list[str] = []
    stage3_reasons: list[str] = []
    harvest_summary: dict[str, Any] | None = None
    stage2_metrics: dict[str, Any] | None = None
    extract_summary: dict[str, Any] | None = None
    ranking_summary: dict[str, Any] | None = None
    candidate_summary: dict[str, Any] | None = None
    stage3_metrics: dict[str, Any] | None = None
    final_verdict = "reject"

    if stage1_passed:
        harvest_summary, stage2_reasons = harvest_source(
            source_id=args.source_id,
            source_url=source_url,
            source_row=source_row,
            source_config_path=source_config_path,
            args=args,
            harvester_cli=harvester_cli,
            run_root=run_root,
        )
        if harvest_summary:
            stage2_metrics, auto_stage2_reasons = evaluate_stage2(harvest_summary, gate_policy=stage2_gate_policy)
            stage2_reasons.extend(auto_stage2_reasons)

    if stage1_passed and harvest_summary and not stage2_reasons:
        extract_summary, ranking_summary, candidate_summary = run_seed_pipeline(
            source_id=args.source_id,
            source_class=source_class,
            run_root=run_root,
            harvest_root=run_root / "harvest",
            source_config_path=source_config_path,
            alias_map_path=alias_map_path,
            scoreboard_path=scoreboard_path,
            single_source_health_path=single_source_health_path,
            anchor_first_verification=bool(args.anchor_first_verification),
            anchor_index_root=anchor_index_root,
            anchor_verification_topk=max(int(args.anchor_verification_topk), 1),
        )
        fetched_pages = int(((harvest_summary.get("metrics") or {}).get("fetchedPageCount") or 0))
        stage3_metrics = stage3_metrics_common(
            extract_summary=extract_summary,
            ranking_summary=ranking_summary,
            candidate_summary=candidate_summary,
            fetched_pages=fetched_pages,
            run_root=run_root,
        )
        stage3_reasons = evaluate_stage3(source_class, stage3_metrics, gate_policy=stage3_gate_policy)
        final_verdict = "approve" if not stage3_reasons else "reject"
    elif stage1_passed and stage2_reasons == ["missing-harvestPolicy-or-singlePagePolicy"]:
        final_verdict = "manual-only"

    body_examples = body_text_examples(ranking_summary or {}, limit=8)
    angle_counts = gather_angle_counts((ranking_summary or {}).get("rankedSeeds") or [])
    summary = {
        "version": "2.0.0",
        "generatedAt": utc_now(),
        "mode": "external-source-benchmark",
        "sourceId": args.source_id,
        "sourceClass": source_class,
        "url": source_url,
        "canonicalWrites": False,
        "runId": run_id,
        "paths": {
            "runRoot": repo_relative(run_root),
            "singleSourceHealthSummary": repo_relative(single_source_health_path),
        },
        "policies": {
            "precheckPolicy": precheck_policy,
            "stage2GatePolicy": stage2_gate_policy,
            "stage3GatePolicy": stage3_gate_policy,
            "anchorFirstVerification": bool(args.anchor_first_verification),
            "anchorIndexRoot": repo_relative(anchor_index_root),
            "anchorVerificationTopk": max(int(args.anchor_verification_topk), 1),
        },
        "stage1Precheck": precheck_payload,
        "stage1Passed": stage1_passed,
        "stage1FailureReasons": stage1_reasons,
        "stage2Harvest": stage2_metrics,
        "stage2Passed": (not stage2_reasons) if stage2_metrics else None,
        "stage2FailureReasons": stage2_reasons,
        "stage3Yield": stage3_metrics,
        "stage3Passed": (not stage3_reasons) if stage3_metrics else None,
        "stage3FailureReasons": stage3_reasons,
        "angleCounts": angle_counts,
        "bodyTextExamples": body_examples,
        "finalVerdict": final_verdict,
    }
    write_json(benchmark_summary_path, summary)
    benchmark_markdown_path.write_text(render_markdown(summary), encoding="utf-8")
    sys.stdout.buffer.write((json.dumps(summary, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
