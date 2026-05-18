from __future__ import annotations

from pathlib import Path
from typing import Any

from sanguo_governance_loader import SanguoGovernanceError, load_relationship_claim_pair_cue_rules


PAIR_CUE_MAX_SPAN = 0
PAIR_CUE_SENTENCE_MAX_SPAN = 0
PAIR_CUE_AFTER_ALIAS_LIMIT = 0
PAIR_CUE_SNIPPET_PAD = 0
PAIR_CUE_ENEMY_DIRECT_OBJECT_LIMIT = 0
PAIR_CUE_ENEMY_TAIL_LIMIT = 0
PAIR_CUE_LEGACY_TOKEN_POSITION_LIMIT = 0
PAIR_CUE_LEGACY_MAX_SPAN = 0
PAIR_CUE_LEGACY_WINDOW_BEFORE = 0
PAIR_CUE_LEGACY_WINDOW_AFTER = 0

PAIR_CUE_CLAUSE_BOUNDARIES: frozenset[str] = frozenset()
PAIR_CUE_SENTENCE_BOUNDARIES: frozenset[str] = frozenset()
PAIR_CUE_LIST_CONNECTORS: frozenset[str] = frozenset()
PAIR_CUE_LOOSE_LIST_CONNECTORS: frozenset[str] = frozenset()
PAIR_CUE_AFTER_PAIR_TYPES: set[str] = set()
PAIR_CUE_SWORN_SIBLING_SENTENCE_TERMS: set[str] = set()
PAIR_CUE_SIBLING_POSSESSIVE_MARKERS: set[str] = set()
PAIR_CUE_SIBLING_TITLE_TERMS: set[str] = set()
PAIR_CUE_SIBLING_RANK_TERMS: set[str] = set()
PAIR_CUE_ENEMY_DIRECT_OBJECT_TERMS: list[str] = []
PAIR_CUE_ENEMY_RECIPROCAL_TAIL_TERMS: list[str] = []
PAIR_CUE_ENEMY_ENCOUNTER_TERMS: list[str] = []
PAIR_CUE_ENEMY_PASSIVE_TAIL_TERMS: list[str] = []
PAIR_CUE_ENEMY_COMMAND_GUARDS: set[str] = set()
PAIR_CUE_WEAK_TERMS: dict[str, set[str]] = {}
PAIR_CUE_SINGLE_CHAR_ALLOW_RELATION_TYPES: set[str] = set()
PAIR_CUE_ENEMY_CONTEXT_GUARD_TYPES: set[str] = set()
PAIR_CUE_LEGACY_TYPE_PATTERNS: dict[str, str] = {}
PAIR_CUE_LEGACY_CONNECTORS: tuple[str, ...] = ()
PAIR_CUE_LEGACY_STRICT_CONNECTOR_TYPES: set[str] = set()
PAIR_CUE_LEGACY_KINSHIP_BETWEEN_ONLY_TYPES: set[str] = set()
PAIR_CUE_OVERRIDE_BROAD_TYPES: set[str] = set()


_PAIR_CUE_RULES_LOADED = False


def _required_rule_value(by_name: dict[str, dict[str, Any]], constant_name: str) -> Any:
    row = by_name.get(constant_name)
    if row is None:
        raise SanguoGovernanceError(f"rule-relationship-claim-pair-cues missing constantName: {constant_name}")
    return row.get("value")


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        text = str(item).strip()
        if not text or chr(0xFFFD) in text or set(text) == {"?"}:
            continue
        result.append(text)
    return result


def _string_set(value: Any) -> set[str]:
    return set(_string_list(value))


def _string_mapping(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {str(key): str(item) for key, item in value.items() if str(key).strip() and str(item).strip()}


def _mapping_terms(value: Any) -> dict[str, set[str]]:
    if not isinstance(value, dict):
        return {}
    result: dict[str, set[str]] = {}
    for key, item in value.items():
        normalized_key = str(key).strip()
        normalized_terms = set(_string_list(item))
        if not normalized_key or not normalized_terms:
            continue
        result[normalized_key] = normalized_terms
    return result


def _int_value(value: Any) -> int:
    if isinstance(value, bool):
        raise SanguoGovernanceError(f"rule-relationship-claim-pair-cues integer value cannot be bool: {value}")
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise SanguoGovernanceError(f"rule-relationship-claim-pair-cues integer parse failed: {value}") from exc


def apply_relationship_claim_pair_cue_rules(
    governance_root: str | Path | None = None,
    relationship_claim_pair_cue_rules: str | Path | None = None,
) -> None:
    global _PAIR_CUE_RULES_LOADED
    global PAIR_CUE_MAX_SPAN
    global PAIR_CUE_SENTENCE_MAX_SPAN
    global PAIR_CUE_AFTER_ALIAS_LIMIT
    global PAIR_CUE_SNIPPET_PAD
    global PAIR_CUE_ENEMY_DIRECT_OBJECT_LIMIT
    global PAIR_CUE_ENEMY_TAIL_LIMIT
    global PAIR_CUE_LEGACY_TOKEN_POSITION_LIMIT
    global PAIR_CUE_LEGACY_MAX_SPAN
    global PAIR_CUE_LEGACY_WINDOW_BEFORE
    global PAIR_CUE_LEGACY_WINDOW_AFTER
    global PAIR_CUE_CLAUSE_BOUNDARIES
    global PAIR_CUE_SENTENCE_BOUNDARIES
    global PAIR_CUE_LIST_CONNECTORS
    global PAIR_CUE_LOOSE_LIST_CONNECTORS
    global PAIR_CUE_AFTER_PAIR_TYPES
    global PAIR_CUE_SWORN_SIBLING_SENTENCE_TERMS
    global PAIR_CUE_SIBLING_POSSESSIVE_MARKERS
    global PAIR_CUE_SIBLING_TITLE_TERMS
    global PAIR_CUE_SIBLING_RANK_TERMS
    global PAIR_CUE_ENEMY_DIRECT_OBJECT_TERMS
    global PAIR_CUE_ENEMY_RECIPROCAL_TAIL_TERMS
    global PAIR_CUE_ENEMY_ENCOUNTER_TERMS
    global PAIR_CUE_ENEMY_PASSIVE_TAIL_TERMS
    global PAIR_CUE_ENEMY_COMMAND_GUARDS
    global PAIR_CUE_WEAK_TERMS
    global PAIR_CUE_SINGLE_CHAR_ALLOW_RELATION_TYPES
    global PAIR_CUE_ENEMY_CONTEXT_GUARD_TYPES
    global PAIR_CUE_LEGACY_TYPE_PATTERNS
    global PAIR_CUE_LEGACY_CONNECTORS
    global PAIR_CUE_LEGACY_STRICT_CONNECTOR_TYPES
    global PAIR_CUE_LEGACY_KINSHIP_BETWEEN_ONLY_TYPES
    global PAIR_CUE_OVERRIDE_BROAD_TYPES

    rows = load_relationship_claim_pair_cue_rules(
        governance_root,
        relationship_claim_pair_cue_rules=relationship_claim_pair_cue_rules,
    )
    by_name = {str(row.get("constantName") or ""): row for row in rows}

    PAIR_CUE_MAX_SPAN = _int_value(_required_rule_value(by_name, "PAIR_CUE_MAX_SPAN"))
    PAIR_CUE_SENTENCE_MAX_SPAN = _int_value(_required_rule_value(by_name, "PAIR_CUE_SENTENCE_MAX_SPAN"))
    PAIR_CUE_AFTER_ALIAS_LIMIT = _int_value(_required_rule_value(by_name, "PAIR_CUE_AFTER_ALIAS_LIMIT"))
    PAIR_CUE_SNIPPET_PAD = _int_value(_required_rule_value(by_name, "PAIR_CUE_SNIPPET_PAD"))
    PAIR_CUE_ENEMY_DIRECT_OBJECT_LIMIT = _int_value(_required_rule_value(by_name, "PAIR_CUE_ENEMY_DIRECT_OBJECT_LIMIT"))
    PAIR_CUE_ENEMY_TAIL_LIMIT = _int_value(_required_rule_value(by_name, "PAIR_CUE_ENEMY_TAIL_LIMIT"))
    PAIR_CUE_LEGACY_TOKEN_POSITION_LIMIT = _int_value(_required_rule_value(by_name, "PAIR_CUE_LEGACY_TOKEN_POSITION_LIMIT"))
    PAIR_CUE_LEGACY_MAX_SPAN = _int_value(_required_rule_value(by_name, "PAIR_CUE_LEGACY_MAX_SPAN"))
    PAIR_CUE_LEGACY_WINDOW_BEFORE = _int_value(_required_rule_value(by_name, "PAIR_CUE_LEGACY_WINDOW_BEFORE"))
    PAIR_CUE_LEGACY_WINDOW_AFTER = _int_value(_required_rule_value(by_name, "PAIR_CUE_LEGACY_WINDOW_AFTER"))

    PAIR_CUE_CLAUSE_BOUNDARIES = frozenset(_string_list(_required_rule_value(by_name, "PAIR_CUE_CLAUSE_BOUNDARIES")))
    PAIR_CUE_SENTENCE_BOUNDARIES = frozenset(_string_list(_required_rule_value(by_name, "PAIR_CUE_SENTENCE_BOUNDARIES")))
    PAIR_CUE_LIST_CONNECTORS = frozenset(_string_list(_required_rule_value(by_name, "PAIR_CUE_LIST_CONNECTORS")))
    PAIR_CUE_LOOSE_LIST_CONNECTORS = frozenset(_string_list(_required_rule_value(by_name, "PAIR_CUE_LOOSE_LIST_CONNECTORS")))
    PAIR_CUE_AFTER_PAIR_TYPES = _string_set(_required_rule_value(by_name, "PAIR_CUE_AFTER_PAIR_TYPES"))
    PAIR_CUE_SWORN_SIBLING_SENTENCE_TERMS = _string_set(
        _required_rule_value(by_name, "PAIR_CUE_SWORN_SIBLING_SENTENCE_TERMS")
    )
    PAIR_CUE_SIBLING_POSSESSIVE_MARKERS = _string_set(
        _required_rule_value(by_name, "PAIR_CUE_SIBLING_POSSESSIVE_MARKERS")
    )
    PAIR_CUE_SIBLING_TITLE_TERMS = _string_set(_required_rule_value(by_name, "PAIR_CUE_SIBLING_TITLE_TERMS"))
    PAIR_CUE_SIBLING_RANK_TERMS = _string_set(_required_rule_value(by_name, "PAIR_CUE_SIBLING_RANK_TERMS"))
    PAIR_CUE_ENEMY_DIRECT_OBJECT_TERMS = _string_list(
        _required_rule_value(by_name, "PAIR_CUE_ENEMY_DIRECT_OBJECT_TERMS")
    )
    PAIR_CUE_ENEMY_RECIPROCAL_TAIL_TERMS = _string_list(
        _required_rule_value(by_name, "PAIR_CUE_ENEMY_RECIPROCAL_TAIL_TERMS")
    )
    PAIR_CUE_ENEMY_ENCOUNTER_TERMS = _string_list(
        _required_rule_value(by_name, "PAIR_CUE_ENEMY_ENCOUNTER_TERMS")
    )
    PAIR_CUE_ENEMY_PASSIVE_TAIL_TERMS = _string_list(
        _required_rule_value(by_name, "PAIR_CUE_ENEMY_PASSIVE_TAIL_TERMS")
    )
    PAIR_CUE_ENEMY_COMMAND_GUARDS = _string_set(_required_rule_value(by_name, "PAIR_CUE_ENEMY_COMMAND_GUARDS"))
    PAIR_CUE_WEAK_TERMS = _mapping_terms(_required_rule_value(by_name, "PAIR_CUE_WEAK_TERMS"))
    PAIR_CUE_SINGLE_CHAR_ALLOW_RELATION_TYPES = _string_set(
        _required_rule_value(by_name, "PAIR_CUE_SINGLE_CHAR_ALLOW_RELATION_TYPES")
    )
    PAIR_CUE_ENEMY_CONTEXT_GUARD_TYPES = _string_set(
        _required_rule_value(by_name, "PAIR_CUE_ENEMY_CONTEXT_GUARD_TYPES")
    )
    PAIR_CUE_LEGACY_TYPE_PATTERNS = _string_mapping(_required_rule_value(by_name, "PAIR_CUE_LEGACY_TYPE_PATTERNS"))
    PAIR_CUE_LEGACY_CONNECTORS = tuple(_string_list(_required_rule_value(by_name, "PAIR_CUE_LEGACY_CONNECTORS")))
    PAIR_CUE_LEGACY_STRICT_CONNECTOR_TYPES = _string_set(
        _required_rule_value(by_name, "PAIR_CUE_LEGACY_STRICT_CONNECTOR_TYPES")
    )
    PAIR_CUE_LEGACY_KINSHIP_BETWEEN_ONLY_TYPES = _string_set(
        _required_rule_value(by_name, "PAIR_CUE_LEGACY_KINSHIP_BETWEEN_ONLY_TYPES")
    )
    PAIR_CUE_OVERRIDE_BROAD_TYPES = _string_set(_required_rule_value(by_name, "PAIR_CUE_OVERRIDE_BROAD_TYPES"))

    _PAIR_CUE_RULES_LOADED = True


def ensure_relationship_claim_pair_cue_rules_loaded() -> None:
    if not _PAIR_CUE_RULES_LOADED:
        apply_relationship_claim_pair_cue_rules()
