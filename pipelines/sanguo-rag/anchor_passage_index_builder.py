"""
Anchor Passage Index Builder — SANGUO-AUTO-0202
將 anchor corpus 切成可重建的 passage，產生穩定 locator 與 textHash。
每個 passage 有 corpusId、sourceFamily、layer、locator、textHash、normalizedText。
相同輸入產生相同 hash（確定性）。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import quote, urlsplit

from anchor_corpus_registry import load_anchor_corpus_registry
from propose_body_boundary_residual_cleanup import build_body_boundary_residual_cleanup_proposals
from repo_layout import resolve_repo_root

REPO_ROOT = resolve_repo_root(__file__)

DEFAULT_REGISTRY_PATH = REPO_ROOT / "pipelines/sanguo-rag/config/anchor-corpus-registry.json"
DEFAULT_SOURCE_CONFIG_PATH = REPO_ROOT / "pipelines/sanguo-rag/config/anchor-index-build-sources.json"
DEFAULT_OUTPUT_ROOT = Path("artifacts/data-pipeline/sanguo-rag/anchor-index")
PASSAGE_INDEX_SCHEMA = "anchor.passage.index.v0.1"
MIN_PASSAGE_CHARS = 20
MAX_PASSAGE_CHARS = 2000
DEFAULT_SEGMENTATION_POLICY = {
    "minPassageChars": MIN_PASSAGE_CHARS,
    "maxPassageChars": MAX_PASSAGE_CHARS,
    "paragraphSplitPattern": r"\n{2,}",
    "sentenceBoundaryPattern": r"(?<=[。！？!?])",
    "stripMetadataHeader": True,
    "metadataHeaderSeparatorPattern": r"\r?\n\r?\n",
    "locatorField": "full-text",
    "textFields": ["plainText", "text", "content", "body", "snippet"],
    "evidenceTextFields": ["quote", "sourceQuote", "evidenceText", "seedText", "translatedTraditionalText"],
    "corpusFilePatterns": ["*.txt"],
    "stripHtmlComments": False,
    "stripHtmlTags": False,
    "aliasStatuses": ["high-confidence", "accepted"],
    "minAliasChars": 2,
    "maxPersonIdsPerPassage": 12,
    "cleanupRuleExtractors": [],
    "cleanupRuleConstantRoles": {},
    "removeNoiseMarkerOccurrences": False,
    "dropNoiseLines": False,
    "tailTrimMinIndex": 800,
    "bodyEndMinIndex": 48,
    "applyBodyBoundaryTelemetry": False,
    "bodyBoundaryTelemetryFileNames": [],
    "bodyBoundaryTelemetryPathFields": [],
    "bodyBoundaryTelemetryMatchFields": [],
    "bodyBoundaryTelemetryRequireTextHash": True,
}


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def resolve_path(p: str | Path) -> Path:
    path = Path(p)
    return path if path.is_absolute() else (REPO_ROOT / path).resolve()


def normalize_text(raw: str, replacements: list[dict[str, str]] | None = None) -> str:
    text = raw.strip()
    for row in replacements or []:
        src = str(row.get("from") or "")
        if src:
            text = text.replace(src, str(row.get("to") or ""))
    text = re.sub(r"\s+", " ", text)
    return text


def stable_text_hash(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def read_json(path: Path) -> Any:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8-sig"))


def read_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        text = line.strip()
        if not text:
            continue
        row = json.loads(text)
        if isinstance(row, dict):
            yield row


def segmentation_policy(payload: dict[str, Any] | None) -> dict[str, Any]:
    policy = dict(DEFAULT_SEGMENTATION_POLICY)
    configured = (payload or {}).get("segmentationPolicy")
    if isinstance(configured, dict):
        for key, value in configured.items():
            if key in policy:
                policy[key] = value
    policy["minPassageChars"] = max(int(policy.get("minPassageChars") or MIN_PASSAGE_CHARS), 1)
    policy["maxPassageChars"] = max(int(policy.get("maxPassageChars") or MAX_PASSAGE_CHARS), policy["minPassageChars"])
    policy["minAliasChars"] = max(int(policy.get("minAliasChars") or 1), 1)
    policy["maxPersonIdsPerPassage"] = max(int(policy.get("maxPersonIdsPerPassage") or 0), 0)
    policy["tailTrimMinIndex"] = max(int(policy.get("tailTrimMinIndex") or 0), 0)
    policy["bodyEndMinIndex"] = max(int(policy.get("bodyEndMinIndex") or 0), 0)
    return policy


def normalization_replacements(payload: dict[str, Any] | None) -> list[dict[str, str]]:
    rows = (payload or {}).get("normalizationReplacements")
    if not isinstance(rows, list):
        return []
    return [
        {"from": str(row.get("from") or ""), "to": str(row.get("to") or "")}
        for row in rows
        if isinstance(row, dict) and str(row.get("from") or "")
    ]


def string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value] if value.strip() else []
    if not isinstance(value, list):
        return []
    return [str(item or "").strip() for item in value if str(item or "").strip()]


def unique_by_length(values: list[str]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for value in sorted(values, key=len, reverse=True):
        if value in seen:
            continue
        seen.add(value)
        unique.append(value)
    return unique


def split_into_passages(text: str, policy: dict[str, Any] | None = None) -> list[str]:
    """以段落為單位切分，段落太長時再以句號切分。"""
    active = policy or DEFAULT_SEGMENTATION_POLICY
    min_chars = int(active.get("minPassageChars") or MIN_PASSAGE_CHARS)
    max_chars = int(active.get("maxPassageChars") or MAX_PASSAGE_CHARS)
    paragraph_pattern = str(active.get("paragraphSplitPattern") or r"\n{2,}")
    sentence_pattern = str(active.get("sentenceBoundaryPattern") or r"(?<=[。！？!?])")
    paragraphs = [p.strip() for p in re.split(paragraph_pattern, text) if p.strip()]
    passages: list[str] = []
    for para in paragraphs:
        if len(para) <= max_chars:
            passages.append(para)
        else:
            sentences = re.split(sentence_pattern, para)
            chunk = ""
            for sent in sentences:
                if len(chunk) + len(sent) > max_chars and chunk:
                    passages.append(chunk.strip())
                    chunk = sent
                else:
                    chunk += sent
            if chunk.strip():
                passages.append(chunk.strip())
    return [p for p in passages if len(p) >= min_chars]


def clean_corpus_text(raw_text: str, policy: dict[str, Any]) -> str:
    text = raw_text
    if bool(policy.get("stripHtmlComments", False)):
        text = re.sub(r"<!--.*?-->", " ", text, flags=re.DOTALL)
    if bool(policy.get("stripHtmlTags", False)):
        text = re.sub(r"<[^>\n]{1,200}>", " ", text)
    return text


def build_passage_record(
    corpus: dict[str, Any],
    chapter_id: str,
    para_idx: int,
    raw_text: str,
    replacements: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    normalized = normalize_text(raw_text, replacements)
    locator = f"{corpus['locatorPrefix']}#{chapter_id}#p{para_idx}"
    return {
        "corpusId": corpus["corpusId"],
        "sourceFamily": corpus["sourceFamily"],
        "layer": corpus["layer"],
        "trustTier": corpus["trustTier"],
        "locator": locator,
        "textHash": stable_text_hash(normalized),
        "normalizedText": normalized,
        "charCount": len(normalized),
    }


def index_corpus_directory(
    corpus: dict[str, Any],
    corpus_dir: Path,
    policy: dict[str, Any],
    replacements: list[dict[str, str]] | None = None,
    *,
    file_patterns: list[str] | None = None,
    recursive: bool = False,
    alias_entries: list[dict[str, Any]] | None = None,
    source_kind: str | None = None,
) -> Iterator[dict[str, Any]]:
    """讀取 corpus 目錄下的文本文件，產生 passage records。"""
    if not corpus_dir.exists():
        return
    patterns = file_patterns or string_list(policy.get("corpusFilePatterns")) or ["*.txt"]
    seen_files: set[Path] = set()
    text_files: list[Path] = []
    for pattern in patterns:
        iterator = corpus_dir.rglob(pattern) if recursive else corpus_dir.glob(pattern)
        for candidate in iterator:
            if not candidate.is_file():
                continue
            resolved = candidate.resolve()
            if resolved in seen_files:
                continue
            seen_files.add(resolved)
            text_files.append(candidate)
    for text_file in sorted(text_files, key=lambda item: str(item).lower()):
        chapter_id = text_file.stem
        raw_text = clean_corpus_text(text_file.read_text(encoding="utf-8-sig", errors="ignore"), policy)
        passages = split_into_passages(raw_text, policy)
        for para_idx, passage in enumerate(passages):
            record = build_passage_record(corpus, chapter_id, para_idx, passage, replacements)
            record["sourcePath"] = str(text_file.resolve())
            if source_kind:
                record["sourceKind"] = source_kind
            person_ids = person_ids_for_text(record["normalizedText"], alias_entries or [], policy)
            if person_ids:
                record["personIds"] = person_ids
            yield record


def normalize_layer(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text.endswith("-history") or text == "history":
        return "history"
    if text.endswith("-romance") or text == "romance":
        return "romance"
    return text


def corpus_for_evidence_record(
    record: dict[str, Any],
    corpora: list[dict[str, Any]],
) -> dict[str, Any] | None:
    family = str(record.get("sourceFamily") or record.get("sourcePolicyId") or record.get("sourceId") or "").strip()
    layer = normalize_layer(record.get("claimLayer") or record.get("sourceLayerRaw") or record.get("sourceLayer"))
    for corpus in corpora:
        if family and family == str(corpus.get("sourceFamily") or ""):
            return corpus
    for corpus in corpora:
        if layer and layer == str(corpus.get("layer") or ""):
            return corpus
    return None


def evidence_text(record: dict[str, Any], fields: list[str] | None = None) -> str:
    for key in fields or list(DEFAULT_SEGMENTATION_POLICY["evidenceTextFields"]):
        value = str(record.get(key) or "").strip()
        if value:
            return value
    return ""


def evidence_locator(record: dict[str, Any], corpus: dict[str, Any], ordinal: int) -> str:
    prefix = str(corpus.get("locatorPrefix") or corpus.get("corpusId") or "anchor")
    raw_locator = str(record.get("locator") or "").strip()
    if not raw_locator:
        refs = [str(ref or "").strip() for ref in (record.get("evidenceRefs") or []) if str(ref or "").strip()]
        raw_locator = refs[0] if refs else str(record.get("sourceEvidenceId") or record.get("claimId") or record.get("seedId") or ordinal)
    if raw_locator.startswith(prefix + "#"):
        return raw_locator
    return f"{prefix}#{raw_locator}"


def build_passage_record_from_evidence(
    record: dict[str, Any],
    corpus: dict[str, Any],
    ordinal: int,
    *,
    policy: dict[str, Any],
    replacements: list[dict[str, str]] | None = None,
) -> dict[str, Any] | None:
    fields = [str(item) for item in policy.get("evidenceTextFields") or [] if str(item).strip()]
    text = evidence_text(record, fields=fields)
    normalized = normalize_text(text, replacements)
    if len(normalized) < int(policy.get("minPassageChars") or MIN_PASSAGE_CHARS):
        return None
    text_hash = str(record.get("textHash") or "").strip() or stable_text_hash(normalized)
    person_ids = []
    for key in ("generalId", "fromId", "toId"):
        value = str(record.get(key) or "").strip()
        if value:
            person_ids.append(value)
    for value in record.get("generalIds") or []:
        token = str(value or "").strip()
        if token:
            person_ids.append(token)
    return {
        "corpusId": corpus["corpusId"],
        "sourceFamily": corpus["sourceFamily"],
        "layer": corpus["layer"],
        "trustTier": record.get("trustTier") or corpus["trustTier"],
        "locator": evidence_locator(record, corpus, ordinal),
        "textHash": text_hash,
        "normalizedText": normalized,
        "charCount": len(normalized),
        "personIds": sorted(set(person_ids)),
        "sourceRecordId": record.get("evidenceId") or record.get("sourceEvidenceId") or record.get("claimId") or record.get("seedId"),
    }


def index_evidence_jsonl(
    evidence_path: Path,
    corpora: list[dict[str, Any]],
    *,
    policy: dict[str, Any],
    replacements: list[dict[str, str]] | None = None,
) -> Iterator[dict[str, Any]]:
    for ordinal, record in enumerate(read_jsonl(evidence_path), 1):
        corpus = corpus_for_evidence_record(record, corpora)
        if not corpus:
            continue
        passage = build_passage_record_from_evidence(record, corpus, ordinal, policy=policy, replacements=replacements)
        if passage:
            passage["sourcePath"] = str(evidence_path)
            yield passage


def dedupe_passages(passages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for passage in passages:
        key = (
            str(passage.get("corpusId") or ""),
            str(passage.get("locator") or ""),
            str(passage.get("textHash") or ""),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(passage)
    return deduped


def source_config_payload(source_config: str | Path | None) -> dict[str, Any]:
    if not source_config:
        return {}
    return read_json(resolve_path(source_config))


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
    for candidate in paths:
        resolved = candidate.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        unique.append(resolved)
    return unique


def configured_corpus_directory_sources(payload: dict[str, Any], policy: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in payload.get("corpusDirectorySources") or []:
        if not isinstance(row, dict) or row.get("enabled") is False:
            continue
        corpus_id = str(row.get("corpusId") or "").strip()
        root_text = str(row.get("root") or "").strip()
        if not corpus_id or not root_text:
            continue
        file_patterns = string_list(row.get("filePatterns")) or string_list(policy.get("corpusFilePatterns")) or ["*.txt"]
        rows.append(
            {
                "corpusId": corpus_id,
                "root": root_text,
                "filePatterns": file_patterns,
                "recursive": bool(row.get("recursive", False)),
                "kind": str(row.get("kind") or "configured-corpus-directory"),
                "canonicalWrites": False,
            }
        )
    return rows


def source_config_paths(source_config: str | Path | None) -> list[Path]:
    return configured_paths(source_config_payload(source_config), "sources", "globSources")


def read_source_policies(path: Path | None) -> dict[str, dict[str, Any]]:
    if path is None or not path.exists():
        return {}
    payload = read_json(path)
    policies: dict[str, dict[str, Any]] = {}
    for row in payload.get("sources") or []:
        if not isinstance(row, dict):
            continue
        source_id = str(row.get("sourceId") or "").strip()
        if source_id:
            policies[source_id] = row
    return policies


def source_policy_path(payload: dict[str, Any]) -> Path | None:
    raw_path = str(payload.get("sourcePolicyPath") or "").strip()
    return resolve_path(raw_path) if raw_path else None


def alias_map_path(payload: dict[str, Any]) -> Path | None:
    raw_path = str(payload.get("aliasMapPath") or "").strip()
    return resolve_path(raw_path) if raw_path else None


def page_text_cleanup_rule_path(payload: dict[str, Any]) -> Path | None:
    raw_path = str(payload.get("pageTextCleanupRulePath") or "").strip()
    return resolve_path(raw_path) if raw_path else None


def load_page_text_cleanup_markers(
    path: Path | None,
    policy: dict[str, Any],
) -> dict[str, Any]:
    roles = policy.get("cleanupRuleConstantRoles")
    role_map = roles if isinstance(roles, dict) else {}
    extractors = set(string_list(policy.get("cleanupRuleExtractors")))
    tail_names = set(string_list(role_map.get("tailTrimMarkers")))
    noise_names = set(string_list(role_map.get("noiseMarkers")))
    markers = {
        "tailTrimMarkers": [],
        "noiseMarkers": [],
        "ruleCount": 0,
        "path": str(path) if path else None,
    }
    if path is None or not path.exists():
        return markers
    for row in read_jsonl(path):
        extractor = str(row.get("extractor") or "").strip()
        constant_name = str(row.get("constantName") or "").strip()
        if extractors and extractor not in extractors:
            continue
        values = string_list(row.get("value"))
        if not values:
            continue
        if constant_name in tail_names:
            markers["tailTrimMarkers"].extend(values)
            markers["ruleCount"] += 1
        if constant_name in noise_names:
            markers["noiseMarkers"].extend(values)
            markers["ruleCount"] += 1
    markers["tailTrimMarkers"] = unique_by_length(markers["tailTrimMarkers"])
    markers["noiseMarkers"] = unique_by_length(markers["noiseMarkers"])
    return markers


def load_alias_entries(path: Path | None, policy: dict[str, Any]) -> list[dict[str, Any]]:
    if path is None or not path.exists():
        return []
    payload = read_json(path)
    allowed_statuses = {str(item) for item in policy.get("aliasStatuses") or [] if str(item).strip()}
    min_chars = int(policy.get("minAliasChars") or 1)
    entries: list[dict[str, Any]] = []
    for row in payload.get("entries") or []:
        if not isinstance(row, dict):
            continue
        status = str(row.get("status") or "")
        alias = normalize_text(str(row.get("alias") or row.get("normalized") or ""))
        general_ids = [str(item) for item in row.get("generalIds") or [] if str(item).strip()]
        if allowed_statuses and status not in allowed_statuses:
            continue
        if len(alias) < min_chars or not general_ids:
            continue
        entries.append({"alias": alias, "generalIds": general_ids})
    entries.sort(key=lambda row: len(str(row.get("alias") or "")), reverse=True)
    return entries


def person_ids_for_text(text: str, alias_entries: list[dict[str, Any]], policy: dict[str, Any]) -> list[str]:
    if not alias_entries:
        return []
    max_ids = int(policy.get("maxPersonIdsPerPassage") or 0)
    found: list[str] = []
    seen: set[str] = set()
    for row in alias_entries:
        alias = str(row.get("alias") or "")
        if not alias or alias not in text:
            continue
        for general_id in row.get("generalIds") or []:
            token = str(general_id or "").strip()
            if token and token not in seen:
                seen.add(token)
                found.append(token)
                if max_ids and len(found) >= max_ids:
                    return found
    return found


def corpus_for_page_record(
    record: dict[str, Any],
    corpora: list[dict[str, Any]],
    source_policies: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    source_id = str(record.get("sourceId") or "").strip()
    policy = source_policies.get(source_id, {})
    family = str(record.get("sourceFamily") or policy.get("sourceFamily") or "").strip()
    layer = normalize_layer(record.get("sourceLayer") or policy.get("sourceLayer"))
    for corpus in corpora:
        if family and family == str(corpus.get("sourceFamily") or ""):
            return corpus
    for corpus in corpora:
        if layer and layer == str(corpus.get("layer") or ""):
            return corpus
    return None


def page_slug(url: str, fallback: str) -> str:
    if url:
        path = urlsplit(url).path.rstrip("/")
        if path:
            return quote(path.rsplit("/", 1)[-1], safe="%")
    return quote(fallback, safe="%")


def strip_metadata_header(raw_text: str, policy: dict[str, Any]) -> str:
    if not bool(policy.get("stripMetadataHeader", True)):
        return raw_text
    pattern = str(policy.get("metadataHeaderSeparatorPattern") or r"\r?\n\r?\n")
    parts = re.split(pattern, raw_text, maxsplit=1)
    return parts[1] if len(parts) == 2 else raw_text


def extractor_policy(source_policy: dict[str, Any]) -> dict[str, Any]:
    raw_policy = source_policy.get("extractorPolicy") if isinstance(source_policy, dict) else {}
    return raw_policy if isinstance(raw_policy, dict) else {}


def extractor_marker_list(source_policy: dict[str, Any], key: str) -> list[str]:
    return string_list(extractor_policy(source_policy).get(key))


def trim_with_configured_markers(text: str, source_policy: dict[str, Any], policy: dict[str, Any]) -> str:
    value = text
    for marker in extractor_marker_list(source_policy, "bodyStartMarkers"):
        index = value.find(marker)
        if index >= 0:
            value = value[index + len(marker) :]
            break
    min_end = int(policy.get("bodyEndMinIndex") or 0)
    for marker in extractor_marker_list(source_policy, "bodyEndMarkers"):
        index = value.find(marker)
        if index >= min_end:
            value = value[:index]
            break
    return value


def trim_tail_markers(text: str, markers: list[str], policy: dict[str, Any]) -> str:
    value = text
    min_index = int(policy.get("tailTrimMinIndex") or 0)
    for marker in markers:
        index = value.find(marker)
        if index >= min_index:
            value = value[:index]
    return value


def remove_noise_markers(text: str, markers: list[str], policy: dict[str, Any]) -> str:
    value = text
    if bool(policy.get("removeNoiseMarkerOccurrences", False)):
        for marker in markers:
            value = value.replace(marker, " ")
    if bool(policy.get("dropNoiseLines", False)):
        kept_lines = []
        for line in value.splitlines():
            if any(marker in line for marker in markers):
                continue
            kept_lines.append(line)
        value = "\n".join(kept_lines)
    return value


def clean_harvested_page_text(
    text: str,
    source_policy: dict[str, Any],
    cleanup_markers: dict[str, Any],
    policy: dict[str, Any],
    replacements: list[dict[str, str]] | None = None,
) -> str:
    value = normalize_text(text, replacements)
    value = trim_with_configured_markers(value, source_policy, policy)
    value = trim_tail_markers(value, cleanup_markers.get("tailTrimMarkers") or [], policy)
    value = remove_noise_markers(value, cleanup_markers.get("noiseMarkers") or [], policy)
    value = trim_tail_markers(value, cleanup_markers.get("tailTrimMarkers") or [], policy)
    return normalize_text(value, replacements)


def body_boundary_telemetry_keys(record: dict[str, Any], policy: dict[str, Any]) -> list[str]:
    keys: list[str] = []
    for field_name in string_list(policy.get("bodyBoundaryTelemetryMatchFields")):
        value = str(record.get(field_name) or "").strip()
        if value:
            keys.append(f"{field_name}:{value}")
    return keys


def body_boundary_telemetry_paths(
    pages_path: Path,
    page_rows: list[dict[str, Any]],
    policy: dict[str, Any],
) -> list[Path]:
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


def load_body_boundary_telemetry_index(
    pages_path: Path,
    page_rows: list[dict[str, Any]],
    policy: dict[str, Any],
) -> dict[str, Any]:
    index: dict[str, dict[str, Any]] = {}
    paths = body_boundary_telemetry_paths(pages_path, page_rows, policy)
    for telemetry_path in paths:
        if not telemetry_path.exists():
            continue
        for row in read_jsonl(telemetry_path):
            for key in body_boundary_telemetry_keys(row, policy):
                index.setdefault(key, row)
    return {"index": index, "pathCount": len(paths), "loadedPathCount": sum(1 for path in paths if path.exists())}


def matching_body_boundary_telemetry(
    record: dict[str, Any],
    telemetry_index: dict[str, Any],
    policy: dict[str, Any],
) -> dict[str, Any] | None:
    index = telemetry_index.get("index") if isinstance(telemetry_index, dict) else {}
    if not isinstance(index, dict):
        return None
    for key in body_boundary_telemetry_keys(record, policy):
        row = index.get(key)
        if not isinstance(row, dict):
            continue
        if bool(policy.get("bodyBoundaryTelemetryRequireTextHash", True)):
            record_hash = str(record.get("textHash") or "").strip()
            telemetry_hash = str(row.get("textHash") or "").strip()
            if record_hash and telemetry_hash and record_hash != telemetry_hash:
                continue
        return row
    return None


def apply_body_boundary_telemetry(
    text: str,
    record: dict[str, Any],
    telemetry_index: dict[str, Any],
    policy: dict[str, Any],
) -> tuple[str, dict[str, Any] | None]:
    if not bool(policy.get("applyBodyBoundaryTelemetry", False)):
        return text, None
    telemetry = matching_body_boundary_telemetry(record, telemetry_index, policy)
    if not telemetry:
        return text, None
    try:
        start = int(telemetry.get("bodyStartOffset") or 0)
        end = int(telemetry.get("bodyEndOffset") or len(text))
    except (TypeError, ValueError):
        return text, None
    start = min(max(start, 0), len(text))
    end = min(max(end, start), len(text))
    if end <= start:
        return text, None
    if end - start < int(policy.get("minPassageChars") or MIN_PASSAGE_CHARS):
        return text, None
    return text[start:end], telemetry


def read_page_text(record: dict[str, Any], policy: dict[str, Any]) -> tuple[str, str | None]:
    text_path_raw = str(record.get("textPath") or "").strip()
    if text_path_raw:
        text_path = resolve_path(text_path_raw)
        if text_path.exists():
            return strip_metadata_header(text_path.read_text(encoding="utf-8-sig", errors="ignore"), policy), str(text_path)
    for key in policy.get("textFields") or []:
        value = str(record.get(str(key)) or "").strip()
        if value:
            return value, None
    return "", str(resolve_path(text_path_raw)) if text_path_raw else None


def harvested_page_locator(
    record: dict[str, Any],
    corpus: dict[str, Any],
    passage_idx: int,
    policy: dict[str, Any],
) -> str:
    prefix = str(corpus.get("locatorPrefix") or corpus.get("corpusId") or "anchor")
    raw_locator = str(record.get("locator") or "").strip()
    if raw_locator.startswith(prefix + "#"):
        base = raw_locator
    elif raw_locator:
        base = f"{prefix}#{raw_locator}"
    else:
        fallback = str(record.get("pageId") or record.get("sourceId") or passage_idx)
        base = f"{prefix}#slug={page_slug(str(record.get('url') or ''), fallback)}"
    field = str(policy.get("locatorField") or "full-text")
    return f"{base};field={field};passage={passage_idx}"


def index_harvested_pages_jsonl(
    pages_path: Path,
    corpora: list[dict[str, Any]],
    source_policies: dict[str, dict[str, Any]],
    alias_entries: list[dict[str, Any]],
    *,
    policy: dict[str, Any],
    replacements: list[dict[str, str]] | None = None,
    cleanup_markers: dict[str, Any] | None = None,
) -> Iterator[dict[str, Any]]:
    page_rows = list(read_jsonl(pages_path))
    telemetry_index = load_body_boundary_telemetry_index(pages_path, page_rows, policy)
    for page_ordinal, record in enumerate(page_rows, 1):
        corpus = corpus_for_page_record(record, corpora, source_policies)
        if not corpus:
            continue
        raw_text, text_path = read_page_text(record, policy)
        if not raw_text:
            continue
        source_id = str(record.get("sourceId") or "")
        source_policy = source_policies.get(source_id, {})
        bounded_text, boundary_telemetry = apply_body_boundary_telemetry(raw_text, record, telemetry_index, policy)
        cleaned_text = clean_harvested_page_text(
            bounded_text,
            source_policy,
            cleanup_markers or {},
            policy,
            replacements,
        )
        if not cleaned_text:
            continue
        passages = split_into_passages(cleaned_text, policy)
        for passage_idx, passage in enumerate(passages, 1):
            normalized = normalize_text(passage, replacements)
            if len(normalized) < int(policy.get("minPassageChars") or MIN_PASSAGE_CHARS):
                continue
            row = {
                "corpusId": corpus["corpusId"],
                "sourceFamily": corpus["sourceFamily"],
                "layer": corpus["layer"],
                "trustTier": record.get("trustTier") or source_policies.get(source_id, {}).get("trustTier") or corpus["trustTier"],
                "locator": harvested_page_locator(record, corpus, passage_idx, policy),
                "textHash": stable_text_hash(normalized),
                "pageTextHash": record.get("textHash"),
                "normalizedText": normalized,
                "charCount": len(normalized),
                "sourceId": record.get("sourceId"),
                "sourceUrl": record.get("url"),
                "pageTitle": record.get("title"),
                "sourcePath": str(pages_path),
                "sourceTextPath": text_path,
                "sourceRecordId": record.get("pageId") or f"page:{page_ordinal}",
                "canonicalWrites": False,
            }
            if boundary_telemetry:
                row["bodyBoundaryTelemetryApplied"] = True
                row["bodyBoundaryTelemetryId"] = boundary_telemetry.get("telemetryId")
                row["bodyStartOffset"] = boundary_telemetry.get("bodyStartOffset")
                row["bodyEndOffset"] = boundary_telemetry.get("bodyEndOffset")
            person_ids = person_ids_for_text(normalized, alias_entries, policy)
            if person_ids:
                row["personIds"] = person_ids
            yield row


def pages_jsonl_paths_from_summaries(summary_paths: list[Path]) -> tuple[list[Path], list[dict[str, Any]]]:
    paths: list[Path] = []
    stats: list[dict[str, Any]] = []
    for summary_path in summary_paths:
        if not summary_path.exists():
            stats.append({"path": str(summary_path), "status": "missing", "pagesJsonl": None})
            continue
        payload = read_json(summary_path)
        pages_jsonl = str(((payload.get("inputs") or {}).get("pagesJsonl")) or "").strip()
        if not pages_jsonl:
            stats.append({"path": str(summary_path), "status": "no-pages-jsonl", "pagesJsonl": None})
            continue
        resolved = resolve_path(pages_jsonl)
        paths.append(resolved)
        stats.append({"path": str(summary_path), "status": "ok" if resolved.exists() else "missing-pages-jsonl", "pagesJsonl": str(resolved)})
    return paths, stats


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def build_anchor_index(
    registry_path: str | Path | None = None,
    corpus_roots: dict[str, str] | None = None,
    evidence_jsonl_paths: list[str | Path] | None = None,
    harvested_pages_jsonl_paths: list[str | Path] | None = None,
    source_config: str | Path | None = DEFAULT_SOURCE_CONFIG_PATH,
    output_root: str | Path = DEFAULT_OUTPUT_ROOT,
) -> dict[str, Any]:
    registry = load_anchor_corpus_registry(registry_path)
    source_payload = source_config_payload(source_config)
    policy = segmentation_policy(source_payload)
    replacements = normalization_replacements(source_payload)
    source_policies = read_source_policies(source_policy_path(source_payload))
    alias_entries = load_alias_entries(alias_map_path(source_payload), policy)
    cleanup_markers = load_page_text_cleanup_markers(page_text_cleanup_rule_path(source_payload), policy)
    out_root = resolve_path(output_root)
    corpus_roots = corpus_roots or {}
    corpora = registry.get("corpora", [])
    configured_corpus_sources = configured_corpus_directory_sources(source_payload, policy)

    stats: list[dict[str, Any]] = []
    total_passages = 0

    for corpus in corpora:
        corpus_id = corpus["corpusId"]
        corpus_sources: list[dict[str, Any]] = []
        corpus_dir_str = corpus_roots.get(corpus_id)
        if corpus_dir_str:
            corpus_sources.append(
                {
                    "corpusId": corpus_id,
                    "root": corpus_dir_str,
                    "filePatterns": string_list(policy.get("corpusFilePatterns")) or ["*.txt"],
                    "recursive": False,
                    "kind": "cli-corpus-root",
                    "canonicalWrites": False,
                }
            )
        corpus_sources.extend(row for row in configured_corpus_sources if row.get("corpusId") == corpus_id)
        if not corpus_sources:
            print(f"[SKIP] {corpus_id}: no corpus_root provided, skipping.")
            stats.append({"corpusId": corpus_id, "passageCount": 0, "status": "skipped"})
            continue

        passages: list[dict[str, Any]] = []
        source_stats: list[dict[str, Any]] = []
        for source in corpus_sources:
            corpus_dir = resolve_path(str(source.get("root") or ""))
            if not corpus_dir.exists():
                source_stats.append(
                    {
                        "root": str(corpus_dir),
                        "kind": source.get("kind"),
                        "filePatterns": list(source.get("filePatterns") or []),
                        "passageCount": 0,
                        "status": "missing",
                    }
                )
                continue
            rows = list(
                index_corpus_directory(
                    corpus,
                    corpus_dir,
                    policy,
                    replacements,
                    file_patterns=list(source.get("filePatterns") or []),
                    recursive=bool(source.get("recursive", False)),
                    alias_entries=alias_entries,
                    source_kind=str(source.get("kind") or ""),
                )
            )
            passages.extend(rows)
            source_stats.append(
                {
                    "root": str(corpus_dir),
                    "kind": source.get("kind"),
                    "filePatterns": list(source.get("filePatterns") or []),
                    "passageCount": len(rows),
                    "status": "ok",
                }
            )
        passages = dedupe_passages(passages)
        out_path = out_root / f"{corpus_id}-passages.jsonl"
        write_jsonl(out_path, passages)
        total_passages += len(passages)
        status = "ok" if passages else "no-passages"
        if not passages and all(row.get("status") == "missing" for row in source_stats):
            status = "missing"
        stats.append(
            {
                "corpusId": corpus_id,
                "passageCount": len(passages),
                "outputPath": str(out_path),
                "status": status,
                "sources": source_stats,
            }
        )
        print(f"[OK] {corpus_id}: {len(passages)} passages -> {out_path}")

    evidence_paths = [resolve_path(path) for path in (evidence_jsonl_paths or [])]
    evidence_paths.extend(configured_paths(source_payload, "sources", "globSources"))
    evidence_passages: list[dict[str, Any]] = []
    evidence_stats: list[dict[str, Any]] = []
    for evidence_path in evidence_paths:
        if not evidence_path.exists():
            evidence_stats.append({"path": str(evidence_path), "passageCount": 0, "status": "missing"})
            continue
        rows = dedupe_passages(list(index_evidence_jsonl(evidence_path, corpora, policy=policy, replacements=replacements)))
        evidence_passages.extend(rows)
        evidence_stats.append({"path": str(evidence_path), "passageCount": len(rows), "status": "ok"})
    if evidence_passages:
        evidence_passages = dedupe_passages(evidence_passages)
        out_path = out_root / "evidence-passages.jsonl"
        write_jsonl(out_path, evidence_passages)
        total_passages += len(evidence_passages)
        print(f"[OK] evidence-jsonl: {len(evidence_passages)} passages -> {out_path}")

    page_paths = [resolve_path(path) for path in (harvested_pages_jsonl_paths or [])]
    page_paths.extend(configured_paths(source_payload, "harvestedPageSources", "harvestedPageGlobSources"))
    summary_paths = configured_paths(source_payload, "harvestedPageSummarySources", "harvestedPageSummaryGlobSources")
    summary_page_paths, page_summary_stats = pages_jsonl_paths_from_summaries(summary_paths)
    page_paths.extend(summary_page_paths)
    page_paths = list(dict.fromkeys(path.resolve() for path in page_paths))
    page_passages: list[dict[str, Any]] = []
    page_stats: list[dict[str, Any]] = []
    for pages_path in page_paths:
        if not pages_path.exists():
            page_stats.append({"path": str(pages_path), "passageCount": 0, "status": "missing"})
            continue
        rows = dedupe_passages(
            list(
                index_harvested_pages_jsonl(
                    pages_path,
                    corpora,
                    source_policies,
                    alias_entries,
                    policy=policy,
                    replacements=replacements,
                    cleanup_markers=cleanup_markers,
                )
            )
        )
        page_passages.extend(rows)
        page_stats.append({"path": str(pages_path), "passageCount": len(rows), "status": "ok"})
    if page_passages:
        page_passages = dedupe_passages(page_passages)
        out_path = out_root / "harvested-page-passages.jsonl"
        write_jsonl(out_path, page_passages)
        total_passages += len(page_passages)
        print(f"[OK] harvested-pages: {len(page_passages)} passages -> {out_path}")

    residual_cleanup_summary = build_body_boundary_residual_cleanup_proposals(
        pages_paths=page_paths,
        source_payload=source_payload,
        output_root=out_root,
    )

    summary = {
        "schemaVersion": PASSAGE_INDEX_SCHEMA,
        "generatedAt": utc_now(),
        "totalPassages": total_passages,
        "corpora": stats,
        "evidenceJsonl": evidence_stats,
        "harvestedPages": page_stats,
        "harvestedPagePassageCount": len(page_passages),
        "harvestedPageSummaryInputs": page_summary_stats,
        "segmentationPolicy": policy,
        "sourcePolicyCount": len(source_policies),
        "aliasEntryCount": len(alias_entries),
        "pageTextCleanup": {
            "rulePath": cleanup_markers.get("path"),
            "ruleCount": cleanup_markers.get("ruleCount"),
            "tailTrimMarkerCount": len(cleanup_markers.get("tailTrimMarkers") or []),
            "noiseMarkerCount": len(cleanup_markers.get("noiseMarkers") or []),
        },
        "bodyBoundaryTelemetry": {
            "enabled": bool(policy.get("applyBodyBoundaryTelemetry", False)),
            "appliedPassageCount": sum(1 for row in page_passages if row.get("bodyBoundaryTelemetryApplied")),
            "configuredFileNames": string_list(policy.get("bodyBoundaryTelemetryFileNames")),
            "matchFields": string_list(policy.get("bodyBoundaryTelemetryMatchFields")),
        },
        "bodyBoundaryResidualProposals": residual_cleanup_summary,
    }
    write_json(out_root / "index-summary.json", summary)
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build anchor passage index from corpus directories.")
    parser.add_argument("--registry", help="Path to anchor-corpus-registry.json")
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    parser.add_argument("--sanguozhi-root", help="Root directory for sanguozhi text files")
    parser.add_argument("--houhanshu-root", help="Root directory for houhanshu text files")
    parser.add_argument("--zizhitongjian-root", help="Root directory for zizhitongjian text files")
    parser.add_argument("--sanguoyanyi-root", help="Root directory for sanguoyanyi text files")
    parser.add_argument("--source-config", default=str(DEFAULT_SOURCE_CONFIG_PATH), help="Anchor index build source config JSON")
    parser.add_argument("--evidence-jsonl", action="append", default=[], help="Evidence JSONL to convert into anchor passages")
    parser.add_argument("--harvested-pages-jsonl", action="append", default=[], help="Harvest pages JSONL to split into full-text anchor passages")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    corpus_roots: dict[str, str] = {}
    for corpus_id, attr in [
        ("sanguozhi", "sanguozhi_root"),
        ("houhanshu", "houhanshu_root"),
        ("zizhitongjian", "zizhitongjian_root"),
        ("sanguoyanyi", "sanguoyanyi_root"),
    ]:
        val = getattr(args, attr, None)
        if val:
            corpus_roots[corpus_id] = val

    summary = build_anchor_index(
        registry_path=args.registry,
        corpus_roots=corpus_roots,
        evidence_jsonl_paths=args.evidence_jsonl,
        harvested_pages_jsonl_paths=args.harvested_pages_jsonl,
        source_config=args.source_config,
        output_root=args.output_root,
    )
    print(f"Done. Total passages: {summary['totalPassages']}")


if __name__ == "__main__":
    main()
