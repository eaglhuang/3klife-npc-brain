from __future__ import annotations

import argparse
import glob
import hashlib
import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from primary_canon_inputs import latest_primary_canon_run_root
import relationship_claim_pair_cues as pair_cues
import relationship_type_refinement as relationship_types
from relationship_type_refinement import refine_relationship_type, relationship_type_family
from repo_layout import pipeline_config_path, resolve_repo_root
from sanguo_governance_loader import default_governance_root, load_relationship_runtime_canon_policy


REPO_ROOT = resolve_repo_root(__file__)
DEFAULT_GENERALS_PATH = Path("assets/resources/data/generals.json")
DEFAULT_ALIAS_MAP_PATH = Path("artifacts/data-pipeline/sanguo-rag/extracted/alias-dictionary/formal-mention-map.json")
DEFAULT_STABLE_BOOTSTRAP_PATH = Path(
    "artifacts/data-pipeline/sanguo-rag/extracted/stable-knowledge-bootstrap/stable-knowledge-bootstrap.json"
)
DEFAULT_SOURCE_CONFIG_PATH = pipeline_config_path(REPO_ROOT, "external-evidence-sources.json")
DEFAULT_OUTPUT_ROOT = Path("artifacts/data-pipeline/sanguo-rag/extracted/relationship-claim-graph")
DEFAULT_GOVERNANCE_ROOT = default_governance_root()
DEFAULT_RELATIONSHIP_EDGE_PATTERNS = [
    "artifacts/data-pipeline/sanguo-rag/extracted/relationship-evidence/source-grounded-relationship-edges.jsonl",
    "artifacts/data-pipeline/sanguo-rag/extracted/core-person-progress/*staged-relationship-evidence.jsonl",
    "artifacts/data-pipeline/sanguo-rag/extracted/external-relationship-overlay/source-grounded-relationship-edges.external.jsonl",
    "artifacts/data-pipeline/sanguo-rag/extracted/primary-canon-relationship-backbone/*/relationship-overlay/source-grounded-relationship-edges.external.jsonl",
    "local/codex-smoke/knowledge-growth/external-relationship-overlay/source-grounded-relationship-edges.external.jsonl",
]
DEFAULT_EXTERNAL_EVIDENCE_CARD_PATTERNS = [
    "artifacts/data-pipeline/sanguo-rag/extracted/external-evidence/**/external-evidence-cards.jsonl",
    "artifacts/data-pipeline/sanguo-rag/extracted/primary-canon-relationship-backbone/*/cards/**/candidate-evidence-cards.jsonl",
    "local/codex-smoke/knowledge-growth/**/external-evidence/external-evidence-cards.jsonl",
]

HISTORY_SOURCE_FAMILIES = {"sanguozhi", "houhanshu", "zizhitongjian"}
ROMANCE_SOURCE_FAMILIES = {"sanguoyanyi", "romance-mao-hant"}
PROMOTABLE_HISTORY_TYPES = {
    "alliance_oath",
    "betrayal_surrender",
    "enemy_rival",
    "mentor_student",
    "parent_child",
    "patron_client",
    "ruler_subject",
    "sibling",
    "spouse",
    "sworn_sibling",
}
STABLE_BASELINE_LAYERS = {"stable-bootstrap-seed"}
PROFILE_BASELINE_LAYERS = {"stable-history-profile-baseline", "generals-parent-summary"}
INTERNAL_ROMANCE_LAYERS = {"mao-hant-observed-mentions"}
EXTERNAL_HISTORY_LAYERS = {"external-history"}
EXTERNAL_ROMANCE_LAYERS = {"external-romance"}
A_HISTORY_GRADES = {"A-history", "A-history-cross-source"}
A_ROMANCE_GRADES = {"A-romance"}
A_CANON_GRADES = A_HISTORY_GRADES | A_ROMANCE_GRADES
A_BASELINE_GRADES = {"A-baseline"}
GRADE_RANK = {
    "A-history": 90,
    "A-history-cross-source": 90,
    "A-romance": 90,
    "A-baseline": 80,
    "B-history": 60,
    "B-history-profile-baseline": 45,
    "B-romance": 40,
    "B-secondary": 30,
}
RELATIONSHIP_OUTPUT_FILES = {
    "all": "relationship-claims.jsonl",
    "aHistory": "a-history-relationship-claims.jsonl",
    "aRomance": "a-romance-relationship-claims.jsonl",
    "aCanon": "a-canon-relationship-claims.jsonl",
    "aBaseline": "a-baseline-relationship-claims.jsonl",
    "romance": "romance-relationship-claims.jsonl",
    "rejected": "rejected-relationship-claims.jsonl",
    "summary": "relationship-claim-summary.json",
    "audit": "relationship-claim-audit.md",
}
POLICY_TEXT = {
    "aHistoryRule": "A-history requires a primary history source family, direct pair signal, quote, locator, textHash, and cross/internal trust signal.",
    "aHistoryCrossSourceRule": "A-history-cross-source requires the same ordered pair and refined relationship type from at least two history source families.",
    "aRomanceRule": "A-romance is canonical A for project runtime when it comes from Romance of the Three Kingdoms / Mao-Hant romance with a concrete relationship type, quote, locator, and textHash; it is equal-rank runtime canon, but not labeled as historical fact.",
    "aCanonRule": "A-history, A-history-cross-source, and A-romance are equal-rank runtime canon; keep claimLayer/sourceFamily to separate history from romance.",
    "profileBaselineRule": "Profile-derived relationship baselines stay B-history-profile-baseline; they are not treated as final source truth.",
    "stableBootstrapRule": "Local curated hard relationships stay A-baseline until an external history claim with locator/hash confirms them.",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build relationship claim graph with source family, quote, locator, hash, and promotion audit."
    )
    parser.add_argument("--generals", default=str(DEFAULT_GENERALS_PATH))
    parser.add_argument("--alias-map", default=str(DEFAULT_ALIAS_MAP_PATH))
    parser.add_argument("--stable-bootstrap", default=str(DEFAULT_STABLE_BOOTSTRAP_PATH))
    parser.add_argument("--source-config", default=str(DEFAULT_SOURCE_CONFIG_PATH))
    parser.add_argument("--relationship-edge", action="append", default=[])
    parser.add_argument("--relationship-edge-pattern", action="append", default=[])
    parser.add_argument("--relationship-claim", action="append", default=[])
    parser.add_argument("--relationship-claim-pattern", action="append", default=[])
    parser.add_argument("--no-default-primary-canon-claims", action="store_true")
    parser.add_argument("--external-evidence-card", action="append", default=[])
    parser.add_argument("--external-evidence-card-pattern", action="append", default=[])
    parser.add_argument("--no-default-external-evidence-cards", action="store_true")
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    parser.add_argument("--governance-root", default=str(DEFAULT_GOVERNANCE_ROOT))
    parser.add_argument("--relationship-policy", default=None)
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def policy_set(policy: dict[str, Any], key: str, fallback: set[str]) -> set[str]:
    values = policy.get(key)
    if not isinstance(values, list):
        return set(fallback)
    return {str(item).strip() for item in values if str(item).strip()}


def apply_relationship_runtime_canon_policy(governance_root: str | Path | None, relationship_policy: str | Path | None = None) -> None:
    global HISTORY_SOURCE_FAMILIES
    global ROMANCE_SOURCE_FAMILIES
    global PROMOTABLE_HISTORY_TYPES
    global STABLE_BASELINE_LAYERS
    global PROFILE_BASELINE_LAYERS
    global INTERNAL_ROMANCE_LAYERS
    global EXTERNAL_HISTORY_LAYERS
    global EXTERNAL_ROMANCE_LAYERS
    global A_HISTORY_GRADES
    global A_ROMANCE_GRADES
    global A_CANON_GRADES
    global A_BASELINE_GRADES
    global GRADE_RANK
    global RELATIONSHIP_OUTPUT_FILES
    global POLICY_TEXT

    policy = load_relationship_runtime_canon_policy(governance_root, relationship_policy=relationship_policy)
    HISTORY_SOURCE_FAMILIES = policy_set(policy, "historySourceFamilies", HISTORY_SOURCE_FAMILIES)
    ROMANCE_SOURCE_FAMILIES = policy_set(policy, "romanceSourceFamilies", ROMANCE_SOURCE_FAMILIES)
    PROMOTABLE_HISTORY_TYPES = policy_set(policy, "promotableRelationshipTypes", PROMOTABLE_HISTORY_TYPES)
    STABLE_BASELINE_LAYERS = policy_set(policy, "stableBaselineSourceLayers", STABLE_BASELINE_LAYERS)
    PROFILE_BASELINE_LAYERS = policy_set(policy, "profileBaselineSourceLayers", PROFILE_BASELINE_LAYERS)
    INTERNAL_ROMANCE_LAYERS = policy_set(policy, "internalRomanceSourceLayers", INTERNAL_ROMANCE_LAYERS)
    EXTERNAL_HISTORY_LAYERS = policy_set(policy, "externalHistorySourceLayers", EXTERNAL_HISTORY_LAYERS)
    EXTERNAL_ROMANCE_LAYERS = policy_set(policy, "externalRomanceSourceLayers", EXTERNAL_ROMANCE_LAYERS)
    A_HISTORY_GRADES = policy_set(policy, "aHistoryGrades", A_HISTORY_GRADES)
    A_ROMANCE_GRADES = policy_set(policy, "aRomanceGrades", A_ROMANCE_GRADES)
    A_CANON_GRADES = policy_set(policy, "aCanonGrades", A_HISTORY_GRADES | A_ROMANCE_GRADES)
    A_BASELINE_GRADES = policy_set(policy, "aBaselineGrades", A_BASELINE_GRADES)
    rank_payload = policy.get("gradeRank")
    if isinstance(rank_payload, dict):
        GRADE_RANK = {str(key): int(value) for key, value in rank_payload.items()}
    output_payload = policy.get("relationshipClaimGraphOutputs")
    if isinstance(output_payload, dict):
        RELATIONSHIP_OUTPUT_FILES = {**RELATIONSHIP_OUTPUT_FILES, **{str(key): str(value) for key, value in output_payload.items()}}
    text_payload = policy.get("policyText")
    if isinstance(text_payload, dict):
        POLICY_TEXT = {**POLICY_TEXT, **{str(key): str(value) for key, value in text_payload.items()}}


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def resolve_path(path_text: str | Path) -> Path:
    path = Path(path_text)
    if path.is_absolute():
        return path
    return (REPO_ROOT / path).resolve()


def repo_relative(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(REPO_ROOT.resolve())).replace("\\", "/")
    except ValueError:
        return str(path.resolve())


def read_json(path: Path) -> Any:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8-sig"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), start=1):
        text = line.strip()
        if not text:
            continue
        value = json.loads(text)
        if isinstance(value, dict):
            value.setdefault("_sourceFile", repo_relative(path))
            value.setdefault("_sourceLine", line_no)
            rows.append(value)
    return rows


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    return len(rows)


def stable_hash(*parts: Any, length: int = 18) -> str:
    joined = "\n".join(str(part or "") for part in parts)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:length]


def compact_text(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or ""))


def relationship_pair_key(from_id: str, to_id: str) -> str:
    left = str(from_id or "").strip()
    right = str(to_id or "").strip()
    return "|".join(sorted((left, right))) if left and right else ""


def claim_key(row: dict[str, Any]) -> tuple[str, str, str, str]:
    refs = row.get("evidenceRefs") or []
    ref0 = str(refs[0] if refs else row.get("sourceEvidenceId") or row.get("sourceRef") or "")
    return (
        str(row.get("fromId") or ""),
        str(row.get("toId") or ""),
        str(row.get("type") or ""),
        ref0,
    )


def load_source_policy_index(path: Path) -> dict[str, dict[str, Any]]:
    payload = read_json(path)
    rows = payload.get("sources") if isinstance(payload, dict) else []
    index: dict[str, dict[str, Any]] = {}
    if isinstance(rows, list):
        for row in rows:
            if not isinstance(row, dict):
                continue
            source_id = str(row.get("sourceId") or "").strip()
            if source_id:
                index[source_id] = row
    return index


def build_alias_index(generals_path: Path, alias_map_path: Path) -> dict[str, list[str]]:
    aliases: dict[str, list[str]] = defaultdict(list)
    for row in read_json(generals_path) or []:
        if not isinstance(row, dict):
            continue
        general_id = str(row.get("id") or row.get("generalId") or "").strip()
        if not general_id:
            continue
        values = [row.get("name"), *(row.get("alias") or [])]
        for value in values:
            text = str(value or "").strip()
            if len(text) >= 2 and text not in aliases[general_id]:
                aliases[general_id].append(text)

    payload = read_json(alias_map_path)
    entries = payload.get("entries") if isinstance(payload, dict) else []
    if isinstance(entries, list):
        for row in entries:
            if not isinstance(row, dict):
                continue
            alias = str(row.get("alias") or "").strip()
            general_ids = row.get("generalIds") or []
            if len(alias) < 2 or not isinstance(general_ids, list) or len(general_ids) != 1:
                continue
            general_id = str(general_ids[0] or "").strip()
            if general_id and alias not in aliases[general_id]:
                aliases[general_id].append(alias)

    return {key: sorted(set(values), key=lambda item: (-len(item), item)) for key, values in aliases.items()}


def text_mentions_general(text: str, general_id: str, alias_index: dict[str, list[str]]) -> bool:
    compact = compact_text(text)
    if not compact:
        return False
    for alias in alias_index.get(general_id, []):
        if alias and alias in compact:
            return True
    return False


def edge_text(edge: dict[str, Any]) -> str:
    parts = [
        edge.get("sourceQuote"),
        edge.get("quote"),
        edge.get("evidenceText"),
        edge.get("summary"),
        *(edge.get("sourceQuotes") or []),
    ]
    result: list[str] = []
    seen: set[str] = set()
    for part in parts:
        text = str(part or "").strip()
        key = compact_text(text)
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(text)
    return "".join(result)


def edge_has_direct_pair_signal(edge: dict[str, Any], alias_index: dict[str, list[str]]) -> bool:
    from_id = str(edge.get("fromId") or "").strip()
    to_id = str(edge.get("toId") or "").strip()
    if not from_id or not to_id:
        return False
    text = edge_text(edge)
    return text_mentions_general(text, from_id, alias_index) and text_mentions_general(text, to_id, alias_index)


def edge_requires_pair_relation_cue(edge: dict[str, Any]) -> bool:
    return (
        str(edge.get("pattern") or "") == "external-relationship-card-gate"
        or str(edge.get("originalType") or "") == "relationship_external"
    )


def compact_aliases(general_id: str, alias_index: dict[str, list[str]]) -> list[str]:
    aliases = [compact_text(item) for item in alias_index.get(general_id, [])]
    return [item for item in aliases if len(item) >= 2]


def pair_relation_terms_for_type(rel_type: str) -> list[str]:
    relationship_types.ensure_relationship_type_refinement_rules_loaded()
    pair_cues.ensure_relationship_claim_pair_cue_rules_loaded()
    terms_by_type = {
        "alliance_oath": relationship_types.ALLIANCE_TERMS,
        "betrayal_surrender": relationship_types.BETRAYAL_TERMS,
        "enemy_rival": relationship_types.ENEMY_TERMS,
        "mentor_student": relationship_types.MENTOR_TERMS,
        "parent_child": relationship_types.PARENT_CHILD_TERMS,
        "patron_client": relationship_types.PATRON_TERMS,
        "ruler_subject": relationship_types.COMMAND_TERMS,
        "sibling": relationship_types.SIBLING_TERMS,
        "spouse": relationship_types.SPOUSE_TERMS,
        "sworn_sibling": relationship_types.SWORN_SIBLING_TERMS,
    }
    weak_terms = pair_cues.PAIR_CUE_WEAK_TERMS.get(rel_type, set())
    terms: list[str] = []
    for term in terms_by_type.get(rel_type, []):
        compacted = compact_text(term)
        if not compacted or compacted in weak_terms:
            continue
        if len(compacted) == 1 and rel_type not in pair_cues.PAIR_CUE_SINGLE_CHAR_ALLOW_RELATION_TYPES:
            continue
        terms.append(compacted)
    return sorted(set(terms), key=lambda item: (-len(item), item))


def token_positions(text: str, token: str, limit: int = 6) -> list[int]:
    positions: list[int] = []
    start = 0
    while len(positions) < limit:
        pos = text.find(token, start)
        if pos < 0:
            break
        positions.append(pos)
        start = pos + max(len(token), 1)
    return positions


def term_span(text: str, terms: list[str], *, offset: int = 0) -> tuple[str, int, int] | None:
    best: tuple[str, int, int] | None = None
    for term in terms:
        pos = text.find(term)
        if pos < 0:
            continue
        end = pos + len(term)
        current = (term, offset + pos, offset + end)
        if best is None or current[1] < best[1] or (current[1] == best[1] and len(current[0]) > len(best[0])):
            best = current
    return best


def has_clause_boundary(text: str) -> bool:
    return any(char in pair_cues.PAIR_CUE_CLAUSE_BOUNDARIES for char in text)


def interstitial_is_name_list(text: str) -> bool:
    return all(char in pair_cues.PAIR_CUE_LIST_CONNECTORS for char in text)


def interstitial_is_loose_name_list(text: str) -> bool:
    return all(char in pair_cues.PAIR_CUE_LOOSE_LIST_CONNECTORS for char in text)


def bounded_tail_before_boundary(text: str, start: int, limit: int) -> str:
    tail = text[start : min(len(text), start + limit)]
    boundary_positions = [tail.find(char) for char in pair_cues.PAIR_CUE_CLAUSE_BOUNDARIES if char in tail]
    if not boundary_positions:
        return tail
    return tail[: min(pos for pos in boundary_positions if pos >= 0)]


def tail_after_last_boundary(text: str) -> tuple[str, int]:
    boundary_positions = [text.rfind(char) for char in pair_cues.PAIR_CUE_CLAUSE_BOUNDARIES]
    last_boundary = max(boundary_positions)
    start = 0 if last_boundary < 0 else last_boundary + 1
    return text[start:], start


def sentence_span_for_pair(text: str, start: int, end: int) -> tuple[int, int]:
    left_candidates = [text.rfind(char, 0, start) for char in pair_cues.PAIR_CUE_SENTENCE_BOUNDARIES]
    left = max(left_candidates)
    sentence_start = 0 if left < 0 else left + 1
    right_candidates = [text.find(char, end) for char in pair_cues.PAIR_CUE_SENTENCE_BOUNDARIES]
    right = min((pos for pos in right_candidates if pos >= 0), default=len(text))
    return sentence_start, right


def pair_cue_snippet(text: str, start: int, end: int) -> str:
    left = max(0, start - pair_cues.PAIR_CUE_SNIPPET_PAD)
    right = min(len(text), end + pair_cues.PAIR_CUE_SNIPPET_PAD)
    return text[left:right]


def pair_relation_cue_payload(
    *,
    rel_type: str,
    binding: str,
    from_alias: str,
    to_alias: str,
    from_span: tuple[int, int],
    to_span: tuple[int, int],
    cue: tuple[str, int, int],
    text: str,
    source_claim_type: Any,
    source_claim_scopes: Any,
) -> dict[str, Any]:
    cue_start, cue_end = cue[1], cue[2]
    return {
        "evaluator": "subject-bound-pair-cue-v1",
        "relationshipType": rel_type,
        "binding": binding,
        "cueTerm": cue[0],
        "cueSpan": [cue_start, cue_end],
        "fromAlias": from_alias,
        "fromAliasSpan": [from_span[0], from_span[1]],
        "toAlias": to_alias,
        "toAliasSpan": [to_span[0], to_span[1]],
        "snippet": pair_cue_snippet(text, min(from_span[0], to_span[0], cue_start), max(from_span[1], to_span[1], cue_end))[:120],
        "sourceClaimType": source_claim_type,
        "sourceClaimScopes": list(source_claim_scopes or []),
    }


def ending_term_span(text: str, terms: tuple[str, ...] | list[str], *, offset: int = 0) -> tuple[str, int, int] | None:
    for term in sorted(set(terms), key=lambda item: (-len(item), item)):
        if text.endswith(term):
            start = len(text) - len(term)
            return term, offset + start, offset + len(text)
    return None


def sworn_sibling_sentence_cue(
    *,
    left: str,
    right: str,
    left_span: tuple[int, int],
    right_span: tuple[int, int],
    edge: dict[str, Any],
    text: str,
) -> dict[str, Any] | None:
    pair_start = min(left_span[0], right_span[0])
    pair_end = max(left_span[1], right_span[1])
    sentence_start, sentence_end = sentence_span_for_pair(text, pair_start, pair_end)
    if sentence_end - sentence_start > pair_cues.PAIR_CUE_SENTENCE_MAX_SPAN:
        return None
    terms = pair_relation_terms_for_type("sworn_sibling") + sorted(pair_cues.PAIR_CUE_SWORN_SIBLING_SENTENCE_TERMS)
    cue = term_span(text[sentence_start:sentence_end], terms, offset=sentence_start)
    if cue is None:
        return None
    return pair_relation_cue_payload(
        rel_type="sworn_sibling",
        binding="sworn-sibling-sentence-cue",
        from_alias=left,
        to_alias=right,
        from_span=left_span,
        to_span=right_span,
        cue=cue,
        text=text,
        source_claim_type=edge.get("sourceClaimType"),
        source_claim_scopes=edge.get("sourceClaimScopes"),
    )


def enemy_ordered_direct_object_cue(
    *,
    rel_left: str,
    rel_right: str,
    left_span: tuple[int, int],
    right_span: tuple[int, int],
    actor_alias: str,
    actor_span: tuple[int, int],
    target_alias: str,
    target_span: tuple[int, int],
    edge: dict[str, Any],
    text: str,
) -> dict[str, Any] | None:
    if actor_span[0] >= target_span[0]:
        return None
    sentence_start, sentence_end = sentence_span_for_pair(text, actor_span[0], target_span[1])
    if sentence_end - sentence_start > pair_cues.PAIR_CUE_SENTENCE_MAX_SPAN:
        return None
    between = text[actor_span[1] : target_span[0]]
    if any(char in pair_cues.PAIR_CUE_SENTENCE_BOUNDARIES for char in between):
        return None
    tail, tail_offset = tail_after_last_boundary(between)
    if not tail or len(tail) > pair_cues.PAIR_CUE_ENEMY_DIRECT_OBJECT_LIMIT:
        return None
    if any(marker in tail for marker in pair_cues.PAIR_CUE_ENEMY_COMMAND_GUARDS):
        return None
    cue = ending_term_span(tail, pair_cues.PAIR_CUE_ENEMY_DIRECT_OBJECT_TERMS, offset=actor_span[1] + tail_offset)
    if cue is None:
        return None
    return pair_relation_cue_payload(
        rel_type="enemy_rival",
        binding="enemy-direct-object",
        from_alias=rel_left,
        to_alias=rel_right,
        from_span=left_span,
        to_span=right_span,
        cue=cue,
        text=text,
        source_claim_type=edge.get("sourceClaimType"),
        source_claim_scopes=edge.get("sourceClaimScopes"),
    )


def enemy_ordered_reciprocal_battle_cue(
    *,
    rel_left: str,
    rel_right: str,
    left_span: tuple[int, int],
    right_span: tuple[int, int],
    actor_alias: str,
    actor_span: tuple[int, int],
    target_alias: str,
    target_span: tuple[int, int],
    edge: dict[str, Any],
    text: str,
) -> dict[str, Any] | None:
    if actor_span[0] >= target_span[0]:
        return None
    sentence_start, sentence_end = sentence_span_for_pair(text, actor_span[0], target_span[1])
    if sentence_end - sentence_start > pair_cues.PAIR_CUE_SENTENCE_MAX_SPAN:
        return None
    between = text[actor_span[1] : target_span[0]]
    if any(char in pair_cues.PAIR_CUE_SENTENCE_BOUNDARIES for char in between):
        return None
    between_tail, _ = tail_after_last_boundary(between)
    if "\u8207" not in between_tail and "\u548c" not in between_tail and "\u540c" not in between_tail:
        return None
    sentence_tail = text[target_span[1] : min(sentence_end, target_span[1] + pair_cues.PAIR_CUE_ENEMY_TAIL_LIMIT)]
    cue = term_span(sentence_tail, list(pair_cues.PAIR_CUE_ENEMY_RECIPROCAL_TAIL_TERMS), offset=target_span[1])
    if cue is None:
        return None
    return pair_relation_cue_payload(
        rel_type="enemy_rival",
        binding="enemy-reciprocal-battle",
        from_alias=rel_left,
        to_alias=rel_right,
        from_span=left_span,
        to_span=right_span,
        cue=cue,
        text=text,
        source_claim_type=edge.get("sourceClaimType"),
        source_claim_scopes=edge.get("sourceClaimScopes"),
    )


def enemy_ordered_encounter_battle_cue(
    *,
    rel_left: str,
    rel_right: str,
    left_span: tuple[int, int],
    right_span: tuple[int, int],
    actor_alias: str,
    actor_span: tuple[int, int],
    target_alias: str,
    target_span: tuple[int, int],
    edge: dict[str, Any],
    text: str,
) -> dict[str, Any] | None:
    if actor_span[0] >= target_span[0]:
        return None
    sentence_start, sentence_end = sentence_span_for_pair(text, actor_span[0], target_span[1])
    if sentence_end - sentence_start > pair_cues.PAIR_CUE_SENTENCE_MAX_SPAN:
        return None
    between = text[actor_span[1] : target_span[0]]
    if any(char in pair_cues.PAIR_CUE_SENTENCE_BOUNDARIES for char in between):
        return None
    between_tail, tail_offset = tail_after_last_boundary(between)
    encounter = ending_term_span(between_tail, pair_cues.PAIR_CUE_ENEMY_ENCOUNTER_TERMS, offset=actor_span[1] + tail_offset)
    if encounter is None:
        return None
    sentence_tail = text[target_span[1] : min(sentence_end, target_span[1] + pair_cues.PAIR_CUE_ENEMY_TAIL_LIMIT)]
    cue = term_span(sentence_tail, list(pair_cues.PAIR_CUE_ENEMY_RECIPROCAL_TAIL_TERMS), offset=target_span[1])
    if cue is None:
        return None
    return pair_relation_cue_payload(
        rel_type="enemy_rival",
        binding="enemy-encounter-battle",
        from_alias=rel_left,
        to_alias=rel_right,
        from_span=left_span,
        to_span=right_span,
        cue=cue,
        text=text,
        source_claim_type=edge.get("sourceClaimType"),
        source_claim_scopes=edge.get("sourceClaimScopes"),
    )


def enemy_ordered_passive_kill_cue(
    *,
    rel_left: str,
    rel_right: str,
    left_span: tuple[int, int],
    right_span: tuple[int, int],
    actor_alias: str,
    actor_span: tuple[int, int],
    target_alias: str,
    target_span: tuple[int, int],
    edge: dict[str, Any],
    text: str,
) -> dict[str, Any] | None:
    if target_span[0] >= actor_span[0]:
        return None
    sentence_start, sentence_end = sentence_span_for_pair(text, target_span[0], actor_span[1])
    if sentence_end - sentence_start > pair_cues.PAIR_CUE_SENTENCE_MAX_SPAN:
        return None
    between = text[target_span[1] : actor_span[0]]
    if any(char in pair_cues.PAIR_CUE_SENTENCE_BOUNDARIES for char in between):
        return None
    between_tail, tail_offset = tail_after_last_boundary(between)
    if not between_tail.endswith("\u70ba") and not between_tail.endswith("\u88ab"):
        return None
    sentence_tail = text[actor_span[1] : min(sentence_end, actor_span[1] + 8)]
    cue = term_span(sentence_tail, list(pair_cues.PAIR_CUE_ENEMY_PASSIVE_TAIL_TERMS), offset=actor_span[1])
    if cue is None:
        return None
    return pair_relation_cue_payload(
        rel_type="enemy_rival",
        binding="enemy-passive-kill",
        from_alias=rel_left,
        to_alias=rel_right,
        from_span=left_span,
        to_span=right_span,
        cue=cue,
        text=text,
        source_claim_type=edge.get("sourceClaimType"),
        source_claim_scopes=edge.get("sourceClaimScopes"),
    )


def enemy_subject_bound_pair_cue(
    *,
    left: str,
    right: str,
    left_span: tuple[int, int],
    right_span: tuple[int, int],
    edge: dict[str, Any],
    text: str,
) -> dict[str, Any] | None:
    ordered_pairs = (
        (left, left_span, right, right_span),
        (right, right_span, left, left_span),
    )
    evaluators = (
        enemy_ordered_direct_object_cue,
        enemy_ordered_reciprocal_battle_cue,
        enemy_ordered_encounter_battle_cue,
        enemy_ordered_passive_kill_cue,
    )
    for actor_alias, actor_span, target_alias, target_span in ordered_pairs:
        for evaluator in evaluators:
            cue = evaluator(
                rel_left=left,
                rel_right=right,
                left_span=left_span,
                right_span=right_span,
                actor_alias=actor_alias,
                actor_span=actor_span,
                target_alias=target_alias,
                target_span=target_span,
                edge=edge,
                text=text,
            )
            if cue is not None:
                return cue
    return None


def sibling_possessive_cue(
    *,
    left: str,
    right: str,
    left_span: tuple[int, int],
    right_span: tuple[int, int],
    edge: dict[str, Any],
    text: str,
) -> dict[str, Any] | None:
    pair_start = min(left_span[0], right_span[0])
    pair_end = max(left_span[1], right_span[1])
    if pair_end - pair_start > pair_cues.PAIR_CUE_MAX_SPAN:
        return None
    ordered = [(left, left_span, right, right_span), (right, right_span, left, left_span)]
    for _first_alias, first_span, _second_alias, second_span in ordered:
        if first_span[0] > second_span[0]:
            continue
        bridge = text[first_span[1] : second_span[0]]
        if bridge not in pair_cues.PAIR_CUE_SIBLING_POSSESSIVE_MARKERS:
            continue
        cue = (bridge, first_span[1], second_span[0])
        return pair_relation_cue_payload(
            rel_type="sibling",
            binding="sibling-possessive-between-aliases",
            from_alias=left,
            to_alias=right,
            from_span=left_span,
            to_span=right_span,
            cue=cue,
            text=text,
            source_claim_type=edge.get("sourceClaimType"),
            source_claim_scopes=edge.get("sourceClaimScopes"),
        )
    return None


def sibling_group_title_cue(
    *,
    left: str,
    right: str,
    left_span: tuple[int, int],
    right_span: tuple[int, int],
    edge: dict[str, Any],
    text: str,
) -> dict[str, Any] | None:
    pair_start = min(left_span[0], right_span[0])
    pair_end = max(left_span[1], right_span[1])
    if pair_end - pair_start > pair_cues.PAIR_CUE_MAX_SPAN:
        return None
    if left_span[0] <= right_span[0]:
        interstitial = text[left_span[1] : right_span[0]]
    else:
        interstitial = text[right_span[1] : left_span[0]]
    if not interstitial_is_loose_name_list(interstitial):
        return None
    sentence_start, sentence_end = sentence_span_for_pair(text, pair_start, pair_end)
    if sentence_end - sentence_start > pair_cues.PAIR_CUE_SENTENCE_MAX_SPAN:
        return None
    sentence = text[sentence_start:sentence_end]
    cue = term_span(sentence, sorted(pair_cues.PAIR_CUE_SIBLING_TITLE_TERMS), offset=sentence_start)
    if cue is None:
        return None
    if not (cue[2] <= pair_start and pair_start - cue[2] <= 16):
        return None
    return pair_relation_cue_payload(
        rel_type="sibling",
        binding="sibling-title-before-listed-pair",
        from_alias=left,
        to_alias=right,
        from_span=left_span,
        to_span=right_span,
        cue=cue,
        text=text,
        source_claim_type=edge.get("sourceClaimType"),
        source_claim_scopes=edge.get("sourceClaimScopes"),
    )


def sibling_rank_sentence_cue(
    *,
    left: str,
    right: str,
    left_span: tuple[int, int],
    right_span: tuple[int, int],
    edge: dict[str, Any],
    text: str,
) -> dict[str, Any] | None:
    pair_start = min(left_span[0], right_span[0])
    pair_end = max(left_span[1], right_span[1])
    sentence_start, sentence_end = sentence_span_for_pair(text, pair_start, pair_end)
    if sentence_end - sentence_start > pair_cues.PAIR_CUE_SENTENCE_MAX_SPAN:
        return None
    sentence = text[sentence_start:sentence_end]
    has_rank_pair = "\u70ba\u5144" in sentence and "\u70ba\u5f1f" in sentence
    if not has_rank_pair:
        return None
    cue = term_span(sentence, sorted(pair_cues.PAIR_CUE_SIBLING_RANK_TERMS), offset=sentence_start)
    if cue is None:
        return None
    return pair_relation_cue_payload(
        rel_type="sibling",
        binding="sibling-rank-sentence-cue",
        from_alias=left,
        to_alias=right,
        from_span=left_span,
        to_span=right_span,
        cue=cue,
        text=text,
        source_claim_type=edge.get("sourceClaimType"),
        source_claim_scopes=edge.get("sourceClaimScopes"),
    )


def kinship_subject_bound_pair_cue(
    *,
    rel_type: str,
    left: str,
    right: str,
    left_span: tuple[int, int],
    right_span: tuple[int, int],
    edge: dict[str, Any],
    text: str,
) -> dict[str, Any] | None:
    if rel_type == "sworn_sibling":
        return sworn_sibling_sentence_cue(
            left=left,
            right=right,
            left_span=left_span,
            right_span=right_span,
            edge=edge,
            text=text,
        )
    if rel_type != "sibling":
        return None
    for evaluator in (sibling_possessive_cue, sibling_group_title_cue, sibling_rank_sentence_cue):
        cue = evaluator(
            left=left,
            right=right,
            left_span=left_span,
            right_span=right_span,
            edge=edge,
            text=text,
        )
        if cue is not None:
            return cue
    return None


def relationship_subject_bound_pair_cue(
    *,
    rel_type: str,
    left: str,
    right: str,
    left_span: tuple[int, int],
    right_span: tuple[int, int],
    edge: dict[str, Any],
    text: str,
) -> dict[str, Any] | None:
    if rel_type == "enemy_rival":
        return enemy_subject_bound_pair_cue(
            left=left,
            right=right,
            left_span=left_span,
            right_span=right_span,
            edge=edge,
            text=text,
        )
    return kinship_subject_bound_pair_cue(
        rel_type=rel_type,
        left=left,
        right=right,
        left_span=left_span,
        right_span=right_span,
        edge=edge,
        text=text,
    )


def edge_pair_relation_cue_evidence(
    edge: dict[str, Any],
    alias_index: dict[str, list[str]],
    rel_type: str,
) -> dict[str, Any] | None:
    pair_cues.ensure_relationship_claim_pair_cue_rules_loaded()
    from_id = str(edge.get("fromId") or "").strip()
    to_id = str(edge.get("toId") or "").strip()
    text = compact_text(edge_text(edge))
    if not from_id or not to_id or not text:
        return None
    from_aliases = compact_aliases(from_id, alias_index)
    to_aliases = compact_aliases(to_id, alias_index)
    type_terms = pair_relation_terms_for_type(rel_type)
    if not from_aliases or not to_aliases or not type_terms:
        return None

    for left in from_aliases:
        left_positions = token_positions(text, left)
        if not left_positions:
            continue
        for right in to_aliases:
            right_positions = token_positions(text, right)
            if not right_positions:
                continue
            for left_pos in left_positions:
                for right_pos in right_positions:
                    left_span = (left_pos, left_pos + len(left))
                    right_span = (right_pos, right_pos + len(right))
                    pair_start = min(left_pos, right_pos)
                    pair_end = max(left_span[1], right_span[1])
                    relation_cue = relationship_subject_bound_pair_cue(
                        rel_type=rel_type,
                        left=left,
                        right=right,
                        left_span=left_span,
                        right_span=right_span,
                        edge=edge,
                        text=text,
                    )
                    if relation_cue is not None:
                        return relation_cue
                    if pair_end - pair_start > pair_cues.PAIR_CUE_MAX_SPAN:
                        continue
                    between = text[pair_start:pair_end]
                    if has_clause_boundary(between):
                        continue
                    cue = term_span(between, type_terms, offset=pair_start)
                    if cue is not None:
                        return pair_relation_cue_payload(
                            rel_type=rel_type,
                            binding="cue-between-aliases",
                            from_alias=left,
                            to_alias=right,
                            from_span=left_span,
                            to_span=right_span,
                            cue=cue,
                            text=text,
                            source_claim_type=edge.get("sourceClaimType"),
                            source_claim_scopes=edge.get("sourceClaimScopes"),
                        )
                    if rel_type not in pair_cues.PAIR_CUE_AFTER_PAIR_TYPES:
                        continue
                    if left_pos <= right_pos:
                        interstitial = text[left_span[1] : right_pos]
                    else:
                        interstitial = text[right_span[1] : left_pos]
                    if not interstitial_is_name_list(interstitial):
                        continue
                    cue = term_span(
                        bounded_tail_before_boundary(text, pair_end, pair_cues.PAIR_CUE_AFTER_ALIAS_LIMIT),
                        type_terms,
                        offset=pair_end,
                    )
                    if cue is not None:
                        return pair_relation_cue_payload(
                            rel_type=rel_type,
                            binding="cue-after-listed-pair",
                            from_alias=left,
                            to_alias=right,
                            from_span=left_span,
                            to_span=right_span,
                            cue=cue,
                            text=text,
                            source_claim_type=edge.get("sourceClaimType"),
                            source_claim_scopes=edge.get("sourceClaimScopes"),
                        )
    return None


def edge_has_pair_relation_cue(
    edge: dict[str, Any],
    alias_index: dict[str, list[str]],
    rel_type: str,
) -> bool:
    if edge_pair_relation_cue_evidence(edge, alias_index, rel_type) is not None:
        return True
    if rel_type == "enemy_rival":
        return False
    return edge_has_legacy_pair_relation_cue(edge, alias_index, rel_type)


def edge_has_legacy_pair_relation_cue(
    edge: dict[str, Any],
    alias_index: dict[str, list[str]],
    rel_type: str,
) -> bool:
    pair_cues.ensure_relationship_claim_pair_cue_rules_loaded()

    from_id = str(edge.get("fromId") or "").strip()
    to_id = str(edge.get("toId") or "").strip()
    text = compact_text(edge_text(edge))
    if not from_id or not to_id or not text:
        return False
    from_aliases = compact_aliases(from_id, alias_index)
    to_aliases = compact_aliases(to_id, alias_index)
    if not from_aliases or not to_aliases:
        return False

    cue = re.compile(
        pair_cues.PAIR_CUE_LEGACY_TYPE_PATTERNS.get(
            rel_type,
            pair_cues.PAIR_CUE_LEGACY_TYPE_PATTERNS.get("default", ""),
        )
    )
    connectors = pair_cues.PAIR_CUE_LEGACY_CONNECTORS

    def positions(token: str) -> list[int]:
        result: list[int] = []
        start = 0
        while len(result) < pair_cues.PAIR_CUE_LEGACY_TOKEN_POSITION_LIMIT:
            pos = text.find(token, start)
            if pos < 0:
                break
            result.append(pos)
            start = pos + max(len(token), 1)
        return result

    for left in from_aliases:
        left_positions = positions(left)
        if not left_positions:
            continue
        for right in to_aliases:
            right_positions = positions(right)
            if not right_positions:
                continue
            for left_pos in left_positions:
                for right_pos in right_positions:
                    pair_start = min(left_pos, right_pos)
                    pair_end = max(left_pos + len(left), right_pos + len(right))
                    if pair_end - pair_start > pair_cues.PAIR_CUE_LEGACY_MAX_SPAN:
                        continue
                    window = text[
                        max(0, pair_start - pair_cues.PAIR_CUE_LEGACY_WINDOW_BEFORE) :
                        min(len(text), pair_end + pair_cues.PAIR_CUE_LEGACY_WINDOW_AFTER)
                    ]
                    between = text[pair_start:pair_end]
                    has_connector = any(connector in between for connector in connectors)
                    cue_in_window = bool(cue.search(window))
                    cue_in_between = bool(cue.search(between))
                    if rel_type in pair_cues.PAIR_CUE_LEGACY_STRICT_CONNECTOR_TYPES:
                        if has_connector and cue_in_between:
                            return True
                        continue
                    if has_connector and cue_in_window:
                        return True
                    if rel_type == "enemy_rival" and cue_in_between:
                        return True
                    if rel_type in pair_cues.PAIR_CUE_LEGACY_KINSHIP_BETWEEN_ONLY_TYPES and cue_in_between:
                        return True
    return False


def override_broad_type_from_pair_cue(
    edge: dict[str, Any],
    alias_index: dict[str, list[str]],
    refined_type: str,
) -> tuple[str, list[str]]:
    pair_cues.ensure_relationship_claim_pair_cue_rules_loaded()
    if refined_type not in pair_cues.PAIR_CUE_OVERRIDE_BROAD_TYPES:
        return refined_type, []
    if edge_has_pair_relation_cue(edge, alias_index, "enemy_rival"):
        return "enemy_rival", ["pair_relation_war_cue_override"]
    return refined_type, []


def has_quote_locator_hash(row: dict[str, Any]) -> bool:
    quote = str(row.get("quote") or row.get("sourceQuote") or row.get("evidenceText") or "").strip()
    return len(quote) >= 8 and bool(row.get("locator")) and bool(row.get("textHash"))


def claim_layer_for_grade(grade: str) -> str:
    if grade in A_HISTORY_GRADES or "history" in grade:
        return "history"
    if grade in A_ROMANCE_GRADES or "romance" in grade:
        return "romance"
    return "relationship"


def source_profile(edge: dict[str, Any], source_policy_index: dict[str, dict[str, Any]]) -> dict[str, str]:
    source_policy_id = str(edge.get("sourcePolicyId") or edge.get("sourceId") or "").strip()
    policy = source_policy_index.get(source_policy_id, {})
    raw_layer = str(edge.get("sourceLayerRaw") or policy.get("sourceLayer") or "").strip()
    source_layer = str(edge.get("sourceLayer") or "").strip()
    source_family = str(edge.get("sourceFamily") or policy.get("sourceFamily") or "").strip()
    trust_tier = str(edge.get("trustTier") or policy.get("trustTier") or "").strip()
    source_class = str(edge.get("sourceClass") or policy.get("sourceClass") or "").strip()

    if source_layer in STABLE_BASELINE_LAYERS:
        source_family = source_family or "stable-bootstrap"
        raw_layer = raw_layer or "baseline"
        trust_tier = trust_tier or "local-curated"
        source_class = source_class or "curated-baseline"
    elif source_layer in PROFILE_BASELINE_LAYERS:
        source_family = source_family or "structured-profile"
        raw_layer = raw_layer or "profile"
        trust_tier = trust_tier or "local-profile"
        source_class = source_class or "structured-profile"
    elif source_layer in INTERNAL_ROMANCE_LAYERS:
        source_family = source_family or "romance-mao-hant"
        raw_layer = raw_layer or "romance"
        trust_tier = trust_tier or "primary-text-transcription"
        source_class = source_class or "internal-primary-text"
    elif source_layer.startswith("external-") and raw_layer:
        source_family = source_family or str(policy.get("sourceFamily") or raw_layer)

    return {
        "sourcePolicyId": source_policy_id,
        "sourceLayer": source_layer,
        "sourceLayerRaw": raw_layer,
        "sourceFamily": source_family,
        "trustTier": trust_tier,
        "sourceClass": source_class,
    }


def grade_claim(
    edge: dict[str, Any],
    profile: dict[str, str],
    direct_pair: bool,
    pair_relation: bool,
    pair_relation_required: bool,
    enemy_context_guard: bool,
) -> tuple[str, list[str]]:
    trace: list[str] = []
    source_layer = profile["sourceLayer"]
    raw_layer = profile["sourceLayerRaw"]
    source_family = profile["sourceFamily"]
    refined_type = str(edge.get("type") or "")
    confidence_signals = {str(item) for item in (edge.get("confidenceSignals") or [])}
    cross_families = {str(item) for item in (edge.get("crossSiteSourceFamilies") or []) if str(item).strip()}
    quote_locator_hash = has_quote_locator_hash(edge)

    if source_layer in STABLE_BASELINE_LAYERS:
        trace.append("local-curated-stable-baseline")
        return "A-baseline", trace
    if source_layer in PROFILE_BASELINE_LAYERS:
        trace.append("structured-profile-only")
        return "B-history-profile-baseline", trace

    is_primary_history = source_family in HISTORY_SOURCE_FAMILIES
    is_history = raw_layer == "history" or is_primary_history
    is_romance = raw_layer == "romance" or source_family in ROMANCE_SOURCE_FAMILIES or source_layer in INTERNAL_ROMANCE_LAYERS
    if not direct_pair:
        trace.append("missing-direct-pair-signal")
        if is_history:
            return "C-history-needs-review", trace
        if is_romance:
            return "C-romance-needs-review", trace
        return "C-needs-review", trace

    trace.append("direct-pair-signal")
    if pair_relation_required:
        if pair_relation:
            trace.append("pair-relation-cue")
        else:
            trace.append("missing-pair-relation-cue")
    if enemy_context_guard:
        trace.append("enemy-context-without-directed-binding")
    if is_history:
        if quote_locator_hash:
            trace.append("quote-locator-hash")
        if "cross-source" in confidence_signals or len(cross_families) >= 2:
            trace.append("cross-family-history")
        if enemy_context_guard:
            return "B-history", trace
        if is_primary_history and refined_type in PROMOTABLE_HISTORY_TYPES and quote_locator_hash and (
            "cross-source" in confidence_signals or "internal-external" in confidence_signals or len(cross_families) >= 2
        ) and (not pair_relation_required or pair_relation):
            return "A-history", trace
        return "B-history", trace

    if is_romance:
        if quote_locator_hash:
            trace.append("quote-locator-hash")
            if enemy_context_guard:
                return "B-romance", trace
            if refined_type in PROMOTABLE_HISTORY_TYPES and (not pair_relation_required or pair_relation):
                return "A-romance", trace
            if refined_type not in PROMOTABLE_HISTORY_TYPES:
                trace.append("non-promotable-romance-type")
            if pair_relation_required and not pair_relation:
                trace.append("missing-required-pair-relation")
        return "B-romance", trace

    return "B-secondary", trace


def normalize_edge_to_claim(
    edge: dict[str, Any],
    *,
    alias_index: dict[str, list[str]],
    source_policy_index: dict[str, dict[str, Any]],
    external_card_index: dict[tuple[str, str], dict[str, Any]],
    source_file: str,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    from_id = str(edge.get("fromId") or "").strip()
    to_id = str(edge.get("toId") or "").strip()
    if not from_id or not to_id or from_id == to_id:
        return None, {"reason": "invalid-endpoints", "edge": edge}

    normalized = enrich_edge_from_external_card(edge, external_card_index)
    refined_type, refinement_reasons = refine_relationship_type(normalized, edge_text(normalized))
    refined_type, override_reasons = override_broad_type_from_pair_cue(normalized, alias_index, refined_type)
    normalized["type"] = refined_type
    normalized["refinementReasons"] = list(
        dict.fromkeys([*(edge.get("refinementReasons") or []), *refinement_reasons, *override_reasons])
    )
    profile = source_profile(normalized, source_policy_index)
    direct_pair = edge_has_direct_pair_signal(normalized, alias_index)
    pair_relation_required = edge_requires_pair_relation_cue(normalized)
    pair_relation = True
    pair_relation_cue: dict[str, Any] | None = None
    enemy_context_guard = False
    if pair_relation_required:
        pair_relation_cue = edge_pair_relation_cue_evidence(normalized, alias_index, refined_type)
        pair_relation = pair_relation_cue is not None
        if not pair_relation:
            pair_relation = edge_has_pair_relation_cue(normalized, alias_index, refined_type)
        if refined_type in pair_cues.PAIR_CUE_ENEMY_CONTEXT_GUARD_TYPES:
            enemy_context_guard = edge_has_legacy_pair_relation_cue(normalized, alias_index, "enemy_rival")
            enemy_context_guard = enemy_context_guard and edge_pair_relation_cue_evidence(
                normalized,
                alias_index,
                "enemy_rival",
            ) is None
    if profile["sourceLayer"] in STABLE_BASELINE_LAYERS:
        direct_pair = True
        pair_relation = True
        pair_relation_required = False
        enemy_context_guard = False
    claim_grade, promotion_trace = grade_claim(
        normalized,
        profile,
        direct_pair,
        pair_relation,
        pair_relation_required,
        enemy_context_guard,
    )

    if claim_grade.startswith("C-"):
        return None, {
            "reason": claim_grade,
            "fromId": from_id,
            "toId": to_id,
            "type": refined_type,
            "sourceFile": source_file,
            "evidenceRefs": list(normalized.get("evidenceRefs") or []),
            "sourceLayer": profile["sourceLayer"],
            "sourceFamily": profile["sourceFamily"],
            "promotionTrace": promotion_trace,
            "directPairSignal": direct_pair,
            "pairRelationSignal": pair_relation,
            "pairRelationRequired": pair_relation_required,
            "pairRelationCue": pair_relation_cue,
            "enemyContextGuard": enemy_context_guard,
        }

    quote = str(normalized.get("quote") or normalized.get("sourceQuote") or normalized.get("evidenceText") or "").strip()
    evidence_refs = [str(item) for item in (normalized.get("evidenceRefs") or []) if str(item).strip()]
    claim_id = "relclaim." + stable_hash(
        from_id,
        to_id,
        refined_type,
        "|".join(evidence_refs),
        profile["sourcePolicyId"],
        normalized.get("sourceEvidenceId"),
    )
    claim = {
        "claimId": claim_id,
        "fromId": from_id,
        "toId": to_id,
        "type": refined_type,
        "typeFamily": relationship_type_family(refined_type),
        "claimGrade": claim_grade,
        "claimLayer": claim_layer_for_grade(claim_grade),
        "directPairSignal": direct_pair,
        "pairRelationSignal": pair_relation,
        "pairRelationRequired": pair_relation_required,
        "pairRelationCue": pair_relation_cue,
        "enemyContextGuard": enemy_context_guard,
        "sourcePolicyId": profile["sourcePolicyId"],
        "sourceEvidenceId": normalized.get("sourceEvidenceId") or normalized.get("evidenceId"),
        "sourceFamily": profile["sourceFamily"],
        "sourceLayer": profile["sourceLayer"],
        "sourceLayerRaw": profile["sourceLayerRaw"],
        "sourceClass": profile["sourceClass"],
        "trustTier": profile["trustTier"],
        "sourceClaimType": normalized.get("sourceClaimType"),
        "sourceClaimScopes": list(normalized.get("sourceClaimScopes") or []),
        "quote": quote[:260],
        "locator": normalized.get("locator"),
        "textHash": normalized.get("textHash"),
        "evidenceRefs": evidence_refs,
        "chapterNo": normalized.get("chapterNo"),
        "edgeConfidence": normalized.get("edgeConfidence") or normalized.get("confidence") or 0.0,
        "edgeStrength": normalized.get("edgeStrength"),
        "confidenceSignals": list(normalized.get("confidenceSignals") or []),
        "promotionTrace": promotion_trace,
        "refinementReasons": normalized["refinementReasons"],
        "sourceFile": source_file,
        "canonicalWrites": False,
    }
    return claim, None


def dedupe_claims(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_key: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for row in rows:
        key = claim_key(row)
        existing = by_key.get(key)
        if existing is None:
            by_key[key] = row
            continue
        current_rank = GRADE_RANK.get(str(row.get("claimGrade") or ""), 0)
        existing_rank = GRADE_RANK.get(str(existing.get("claimGrade") or ""), 0)
        if (current_rank, float(row.get("edgeConfidence") or 0.0)) > (
            existing_rank,
            float(existing.get("edgeConfidence") or 0.0),
        ):
            by_key[key] = row
    rows = list(by_key.values())
    rows.sort(
        key=lambda row: (
            str(row.get("fromId") or ""),
            str(row.get("toId") or ""),
            str(row.get("type") or ""),
            str(row.get("claimGrade") or ""),
            str(row.get("claimId") or ""),
        )
    )
    return rows


def detect_conflicts(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    conflicts: list[dict[str, Any]] = []
    by_pair: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        key = relationship_pair_key(str(row.get("fromId") or ""), str(row.get("toId") or ""))
        if key:
            by_pair[key].append(row)

    for pair_key, pair_rows in by_pair.items():
        strong_rows = [
            row
            for row in pair_rows
            if str(row.get("claimGrade") or "") in A_CANON_GRADES or str(row.get("claimGrade") or "") in A_BASELINE_GRADES
        ]
        if not strong_rows:
            continue
        strong_families = {str(row.get("typeFamily") or "") for row in strong_rows}
        for row in pair_rows:
            if row in strong_rows:
                continue
            family = str(row.get("typeFamily") or "")
            if family and strong_families and family not in strong_families:
                conflicts.append(
                    {
                        "pairKey": pair_key,
                        "strongClaimIds": [item.get("claimId") for item in strong_rows],
                        "conflictingClaimId": row.get("claimId"),
                        "conflictingGrade": row.get("claimGrade"),
                        "conflictingType": row.get("type"),
                        "reason": "type-family-conflicts-with-strong-claim",
                    }
                )
    return conflicts


def default_relationship_edge_paths(extra_patterns: list[str], extra_paths: list[str]) -> list[Path]:
    paths: list[Path] = []
    for pattern in [*DEFAULT_RELATIONSHIP_EDGE_PATTERNS, *extra_patterns]:
        absolute_pattern = str(resolve_path(pattern))
        for match in glob.glob(absolute_pattern):
            path = Path(match)
            if path.is_file():
                paths.append(path)
    for path_text in extra_paths:
        path = resolve_path(path_text)
        if path.is_file():
            paths.append(path)
    deduped: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        key = str(path.resolve()).lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(path)
    return deduped


def default_relationship_claim_paths(
    extra_patterns: list[str],
    extra_paths: list[str],
    *,
    include_defaults: bool,
) -> list[Path]:
    paths: list[Path] = []
    patterns = list(extra_patterns)
    if include_defaults:
        latest_run_root = latest_primary_canon_run_root()
        if latest_run_root is not None:
            latest_claims = latest_run_root / "relationship-claim-graph-after" / RELATIONSHIP_OUTPUT_FILES["all"]
            if latest_claims.is_file():
                paths.append(latest_claims)
    for pattern in patterns:
        absolute_pattern = str(resolve_path(pattern))
        for match in glob.glob(absolute_pattern):
            path = Path(match)
            if path.is_file():
                paths.append(path)
    for path_text in extra_paths:
        path = resolve_path(path_text)
        if path.is_file():
            paths.append(path)
    deduped: list[Path] = []
    seen: set[str] = set()
    for path in sorted(paths, key=lambda item: str(item).lower()):
        key = str(path.resolve()).lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(path)
    return deduped


def default_external_evidence_card_paths(
    extra_patterns: list[str],
    extra_paths: list[str],
    *,
    include_defaults: bool,
) -> list[Path]:
    paths: list[Path] = []
    patterns = [*DEFAULT_EXTERNAL_EVIDENCE_CARD_PATTERNS, *extra_patterns] if include_defaults else list(extra_patterns)
    for pattern in patterns:
        absolute_pattern = str(resolve_path(pattern))
        for match in glob.glob(absolute_pattern, recursive=True):
            path = Path(match)
            if path.is_file():
                paths.append(path)
    for path_text in extra_paths:
        path = resolve_path(path_text)
        if path.is_file():
            paths.append(path)
    deduped: list[Path] = []
    seen: set[str] = set()
    for path in sorted(paths, key=lambda item: str(item).lower()):
        key = str(path.resolve()).lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(path)
    return deduped


def external_card_key(source_policy_id: Any, evidence_id: Any) -> tuple[str, str] | None:
    source_id = str(source_policy_id or "").strip()
    evidence = str(evidence_id or "").strip()
    if not source_id or not evidence:
        return None
    return source_id, evidence


def external_ref_key(ref: Any) -> tuple[str, str] | None:
    text = str(ref or "").strip()
    if not text.startswith("ext-card:"):
        return None
    parts = text.split(":", 2)
    if len(parts) != 3:
        return None
    return external_card_key(parts[1], parts[2])


def card_quality_score(card: dict[str, Any]) -> tuple[int, int, str]:
    quote = str(card.get("quote") or card.get("translatedTraditionalText") or "").strip()
    score = 0
    if card.get("locator"):
        score += 30
    if card.get("textHash"):
        score += 30
    if len(quote) >= 8:
        score += 20
    if card.get("url"):
        score += 10
    if card.get("pageTitle"):
        score += 5
    if card.get("sourceFamily"):
        score += 5
    return score, min(len(quote), 500), str(card.get("_sourceFile") or "")


def load_external_evidence_card_index(paths: list[Path]) -> dict[tuple[str, str], dict[str, Any]]:
    index: dict[tuple[str, str], dict[str, Any]] = {}
    for path in paths:
        for card in read_jsonl(path):
            if not isinstance(card, dict):
                continue
            evidence_id = str(card.get("evidenceId") or "").strip()
            if not evidence_id:
                continue
            source_ids = [
                str(card.get("sourcePolicyId") or "").strip(),
                str(card.get("sourceId") or "").strip(),
            ]
            for source_id in source_ids:
                key = external_card_key(source_id, evidence_id)
                if not key:
                    continue
                existing = index.get(key)
                if existing is None or card_quality_score(card) > card_quality_score(existing):
                    index[key] = card
    return index


def lookup_external_card(edge: dict[str, Any], index: dict[tuple[str, str], dict[str, Any]]) -> dict[str, Any] | None:
    source_id = str(edge.get("sourcePolicyId") or edge.get("sourceId") or "").strip()
    evidence_id = str(edge.get("sourceEvidenceId") or edge.get("evidenceId") or "").strip()
    key = external_card_key(source_id, evidence_id)
    if key and key in index:
        return index[key]
    for ref in edge.get("evidenceRefs") or []:
        key = external_ref_key(ref)
        if key and key in index:
            return index[key]
    return None


def enrich_edge_from_external_card(
    edge: dict[str, Any],
    card_index: dict[tuple[str, str], dict[str, Any]],
) -> dict[str, Any]:
    card = lookup_external_card(edge, card_index)
    if not card:
        return edge
    enriched = dict(edge)
    field_map = {
        "locator": "locator",
        "textHash": "textHash",
        "url": "url",
        "pageTitle": "pageTitle",
        "sourceFamily": "sourceFamily",
        "sourceClass": "sourceClass",
        "trustTier": "trustTier",
        "sourcePolicyId": "sourcePolicyId",
        "sourceEvidenceId": "evidenceId",
    }
    for edge_field, card_field in field_map.items():
        if not enriched.get(edge_field) and card.get(card_field):
            enriched[edge_field] = card.get(card_field)
    if not enriched.get("sourceLayerRaw") and card.get("sourceLayer"):
        enriched["sourceLayerRaw"] = card.get("sourceLayer")
    if card.get("claimType"):
        enriched.setdefault("sourceClaimType", card.get("claimType"))
    if card.get("claimScopes"):
        enriched.setdefault("sourceClaimScopes", list(card.get("claimScopes") or []))
    if not enriched.get("quote") and not enriched.get("sourceQuote"):
        quote = str(card.get("quote") or card.get("translatedTraditionalText") or "").strip()
        if quote:
            enriched["sourceQuote"] = quote
    if card.get("_sourceFile"):
        enriched["sourceCardFile"] = card.get("_sourceFile")
    return enriched


def can_promote_cross_source(row: dict[str, Any]) -> bool:
    grade = str(row.get("claimGrade") or "")
    source_family = str(row.get("sourceFamily") or "")
    rel_type = str(row.get("type") or "")
    return (
        grade == "B-history"
        and source_family in HISTORY_SOURCE_FAMILIES
        and rel_type in PROMOTABLE_HISTORY_TYPES
        and bool(row.get("directPairSignal"))
        and (not row.get("pairRelationRequired") or bool(row.get("pairRelationSignal")))
        and has_quote_locator_hash(row)
    )


def promote_cross_source_history_claims(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    by_group: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if not can_promote_cross_source(row):
            continue
        key = (
            str(row.get("fromId") or ""),
            str(row.get("toId") or ""),
            str(row.get("type") or ""),
        )
        by_group[key].append(row)

    promoted_ids: set[str] = set()
    promotions: list[dict[str, Any]] = []
    for key, group_rows in by_group.items():
        families = sorted({str(row.get("sourceFamily") or "") for row in group_rows if row.get("sourceFamily")})
        if len(families) < 2:
            continue
        claim_ids = [str(row.get("claimId") or "") for row in group_rows if row.get("claimId")]
        for row in group_rows:
            promoted_ids.add(str(row.get("claimId") or ""))
        promotions.append(
            {
                "groupKey": "|".join(key),
                "sourceFamilies": families,
                "claimIds": claim_ids,
            }
        )

    if not promoted_ids:
        return rows, promotions

    result: list[dict[str, Any]] = []
    for row in rows:
        claim_id = str(row.get("claimId") or "")
        if claim_id not in promoted_ids:
            result.append(row)
            continue
        updated = dict(row)
        group_key = (
            str(row.get("fromId") or ""),
            str(row.get("toId") or ""),
            str(row.get("type") or ""),
        )
        peers = by_group.get(group_key, [])
        families = sorted({str(peer.get("sourceFamily") or "") for peer in peers if peer.get("sourceFamily")})
        updated["claimGrade"] = "A-history-cross-source"
        updated["claimLayer"] = "history"
        updated["crossSourceFamilies"] = families
        updated["promotionPeerClaimIds"] = [
            str(peer.get("claimId") or "") for peer in peers if str(peer.get("claimId") or "") != claim_id
        ]
        trace = list(updated.get("promotionTrace") or [])
        trace.append("cross-source-history-promotion:" + ",".join(families))
        updated["promotionTrace"] = list(dict.fromkeys(trace))
        updated["edgeConfidence"] = round(max(float(updated.get("edgeConfidence") or 0.0), 0.9), 2)
        result.append(updated)
    return result, promotions


def stable_relationship_edges(path: Path) -> list[dict[str, Any]]:
    payload = read_json(path)
    rows = payload.get("relationshipEdges") if isinstance(payload, dict) else []
    if not isinstance(rows, list):
        return []
    result: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        if str(row.get("sourceLayer") or "") not in STABLE_BASELINE_LAYERS:
            continue
        item = dict(row)
        item.setdefault("_sourceFile", repo_relative(path))
        result.append(item)
    return result


def render_markdown(summary: dict[str, Any], conflicts: list[dict[str, Any]]) -> str:
    lines = [
        "# Relationship Claim Graph Audit",
        "",
        f"- Generated At: `{summary['generatedAt']}`",
        f"- Claim Count: `{summary['metrics']['claimCount']}`",
        f"- A-History Claim Count: `{summary['metrics']['aHistoryClaimCount']}`",
        f"- A-Romance Claim Count: `{summary['metrics']['aRomanceClaimCount']}`",
        f"- A-Canon Claim Count: `{summary['metrics']['aCanonClaimCount']}`",
        f"- A-Baseline Claim Count: `{summary['metrics']['aBaselineClaimCount']}`",
        f"- Cross-Source Promotion Count: `{summary['metrics']['crossSourcePromotionCount']}`",
        f"- Rejected Count: `{summary['metrics']['rejectedCount']}`",
        f"- Conflict Count: `{summary['metrics']['conflictCount']}`",
        "",
        "## Grade Counts",
        "",
    ]
    for key, value in summary["metrics"]["claimGradeCounts"].items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "## Source Families", ""])
    for key, value in summary["metrics"]["sourceFamilyCounts"].items():
        lines.append(f"- `{key or 'unknown'}`: `{value}`")
    lines.extend(["", "## Inputs", ""])
    for path in summary["inputs"]["relationshipEdgePaths"]:
        lines.append(f"- `{path}`")
    if summary["inputs"].get("relationshipClaimPaths"):
        lines.extend(["", "## Supplemental Relationship Claims", ""])
        lines.append(f"- File Count: `{summary['metrics']['supplementalClaimFileCount']}`")
        for path in summary["inputs"]["relationshipClaimPaths"][:40]:
            lines.append(f"- `{path}`")
    if summary["inputs"].get("externalEvidenceCardPaths"):
        lines.extend(["", "## External Evidence Cards", ""])
        lines.append(f"- File Count: `{summary['metrics']['externalEvidenceCardFileCount']}`")
        lines.append(f"- Indexed Key Count: `{summary['metrics']['externalEvidenceCardKeyCount']}`")
        for path in summary["inputs"]["externalEvidenceCardPaths"][:40]:
            lines.append(f"- `{path}`")
        remaining = len(summary["inputs"]["externalEvidenceCardPaths"]) - 40
        if remaining > 0:
            lines.append(f"- `... {remaining} more`")
    lines.extend(["", "## Policy", ""])
    policy_lines = summary.get("policy") if isinstance(summary.get("policy"), dict) else {}
    for key in [
        "aHistoryRule",
        "aHistoryCrossSourceRule",
        "aRomanceRule",
        "aCanonRule",
        "profileBaselineRule",
        "stableBootstrapRule",
    ]:
        text = str(policy_lines.get(key) or "").strip()
        if text:
            lines.append(f"- {text}")
    if conflicts:
        lines.extend(["", "## Conflicts", ""])
        for item in conflicts[:80]:
            lines.append(
                f"- `{item['pairKey']}`: `{item['conflictingClaimId']}` conflicts with `{', '.join(item['strongClaimIds'])}`"
            )
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    args = parse_args()
    relationship_types.apply_relationship_type_refinement_rules(args.governance_root)
    pair_cues.apply_relationship_claim_pair_cue_rules(args.governance_root)
    apply_relationship_runtime_canon_policy(args.governance_root, args.relationship_policy)
    output_root = resolve_path(args.output_root)
    if output_root.exists() and any(output_root.iterdir()) and not args.overwrite:
        raise FileExistsError(f"output already exists: {repo_relative(output_root)}")
    output_root.mkdir(parents=True, exist_ok=True)

    alias_index = build_alias_index(resolve_path(args.generals), resolve_path(args.alias_map))
    source_policy_index = load_source_policy_index(resolve_path(args.source_config))
    relationship_paths = default_relationship_edge_paths(args.relationship_edge_pattern, args.relationship_edge)
    relationship_claim_paths = default_relationship_claim_paths(
        args.relationship_claim_pattern,
        args.relationship_claim,
        include_defaults=not args.no_default_primary_canon_claims,
    )
    external_card_paths = default_external_evidence_card_paths(
        args.external_evidence_card_pattern,
        args.external_evidence_card,
        include_defaults=not args.no_default_external_evidence_cards,
    )
    external_card_index = load_external_evidence_card_index(external_card_paths)

    raw_edges: list[dict[str, Any]] = []
    stable_path = resolve_path(args.stable_bootstrap)
    raw_edges.extend(stable_relationship_edges(stable_path))
    for path in relationship_paths:
        raw_edges.extend(read_jsonl(path))

    claims: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for edge in raw_edges:
        source_file = str(edge.get("_sourceFile") or "unknown")
        claim, reject = normalize_edge_to_claim(
            edge,
            alias_index=alias_index,
            source_policy_index=source_policy_index,
            external_card_index=external_card_index,
            source_file=source_file,
        )
        if claim:
            claims.append(claim)
        if reject:
            rejected.append(reject)
    for path in relationship_claim_paths:
        claims.extend(read_jsonl(path))

    claims = dedupe_claims(claims)
    claims, cross_source_promotions = promote_cross_source_history_claims(claims)
    conflicts = detect_conflicts(claims)
    a_history = [row for row in claims if str(row.get("claimGrade") or "") in A_HISTORY_GRADES]
    a_romance = [row for row in claims if str(row.get("claimGrade") or "") in A_ROMANCE_GRADES]
    a_canon = [row for row in claims if str(row.get("claimGrade") or "") in A_CANON_GRADES]
    a_baseline = [row for row in claims if row.get("claimGrade") == "A-baseline"]
    romance = [row for row in claims if "romance" in str(row.get("claimGrade") or "")]

    claim_grade_counts = Counter(str(row.get("claimGrade") or "unknown") for row in claims)
    source_family_counts = Counter(str(row.get("sourceFamily") or "") for row in claims)
    source_layer_counts = Counter(str(row.get("sourceLayer") or "") for row in claims)
    type_counts = Counter(str(row.get("type") or "") for row in claims)
    summary = {
        "version": "1.0.0",
        "generatedAt": utc_now(),
        "mode": "relationship-claim-graph",
        "canonicalWrites": False,
        "inputs": {
            "generalsPath": repo_relative(resolve_path(args.generals)),
            "aliasMapPath": repo_relative(resolve_path(args.alias_map)),
            "stableBootstrapPath": repo_relative(stable_path),
            "sourceConfigPath": repo_relative(resolve_path(args.source_config)),
            "relationshipEdgePaths": [repo_relative(path) for path in relationship_paths],
            "relationshipClaimPaths": [repo_relative(path) for path in relationship_claim_paths],
            "externalEvidenceCardPaths": [repo_relative(path) for path in external_card_paths],
        },
        "outputs": {
            "relationshipClaims": repo_relative(output_root / RELATIONSHIP_OUTPUT_FILES["all"]),
            "aHistoryRelationshipClaims": repo_relative(output_root / RELATIONSHIP_OUTPUT_FILES["aHistory"]),
            "aRomanceRelationshipClaims": repo_relative(output_root / RELATIONSHIP_OUTPUT_FILES["aRomance"]),
            "aCanonRelationshipClaims": repo_relative(output_root / RELATIONSHIP_OUTPUT_FILES["aCanon"]),
            "aBaselineRelationshipClaims": repo_relative(output_root / RELATIONSHIP_OUTPUT_FILES["aBaseline"]),
            "romanceRelationshipClaims": repo_relative(output_root / RELATIONSHIP_OUTPUT_FILES["romance"]),
            "rejectedRelationshipClaims": repo_relative(output_root / RELATIONSHIP_OUTPUT_FILES["rejected"]),
            "summary": repo_relative(output_root / RELATIONSHIP_OUTPUT_FILES["summary"]),
            "audit": repo_relative(output_root / RELATIONSHIP_OUTPUT_FILES["audit"]),
        },
        "metrics": {
            "rawEdgeCount": len(raw_edges),
            "supplementalClaimFileCount": len(relationship_claim_paths),
            "externalEvidenceCardFileCount": len(external_card_paths),
            "externalEvidenceCardKeyCount": len(external_card_index),
            "claimCount": len(claims),
            "aHistoryClaimCount": len(a_history),
            "aRomanceClaimCount": len(a_romance),
            "aCanonClaimCount": len(a_canon),
            "aBaselineClaimCount": len(a_baseline),
            "romanceClaimCount": len(romance),
            "rejectedCount": len(rejected),
            "conflictCount": len(conflicts),
            "crossSourcePromotionCount": len(cross_source_promotions),
            "claimGradeCounts": dict(sorted(claim_grade_counts.items())),
            "sourceFamilyCounts": dict(sorted(source_family_counts.items())),
            "sourceLayerCounts": dict(sorted(source_layer_counts.items())),
            "relationshipTypeCounts": dict(sorted(type_counts.items())),
        },
        "policy": POLICY_TEXT,
        "crossSourcePromotions": cross_source_promotions[:250],
        "conflicts": conflicts[:250],
    }

    write_jsonl(output_root / RELATIONSHIP_OUTPUT_FILES["all"], claims)
    write_jsonl(output_root / RELATIONSHIP_OUTPUT_FILES["aHistory"], a_history)
    write_jsonl(output_root / RELATIONSHIP_OUTPUT_FILES["aRomance"], a_romance)
    write_jsonl(output_root / RELATIONSHIP_OUTPUT_FILES["aCanon"], a_canon)
    write_jsonl(output_root / RELATIONSHIP_OUTPUT_FILES["aBaseline"], a_baseline)
    write_jsonl(output_root / RELATIONSHIP_OUTPUT_FILES["romance"], romance)
    write_jsonl(output_root / RELATIONSHIP_OUTPUT_FILES["rejected"], rejected)
    write_json(output_root / RELATIONSHIP_OUTPUT_FILES["summary"], summary)
    (output_root / RELATIONSHIP_OUTPUT_FILES["audit"]).write_text(render_markdown(summary, conflicts), encoding="utf-8")

    print(f"[build_relationship_claim_graph] wrote {output_root / RELATIONSHIP_OUTPUT_FILES['all']}")
    print(f"[build_relationship_claim_graph] wrote {output_root / RELATIONSHIP_OUTPUT_FILES['aHistory']}")
    print(
        "[build_relationship_claim_graph] "
        f"claims={len(claims)} aHistory={len(a_history)} aRomance={len(a_romance)} "
        f"aCanon={len(a_canon)} aBaseline={len(a_baseline)} rejected={len(rejected)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
