from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import relationship_claim_pair_cues as pair_cues
from sanguo_governance_loader import SanguoGovernanceError, load_relationship_type_refinement_rules


COARSE_RELATIONSHIP_TYPES: set[str] = set()
NON_STABLE_COARSE_TYPES: set[str] = set()
STABLE_RELATIONSHIP_TYPES: set[str] = set()
KINSHIP_RELATIONSHIP_TYPES: set[str] = set()

RELATIONSHIP_TYPE_FAMILIES: dict[str, str] = {}

TYPE_LABELS: dict[str, str] = {}

BETRAYAL_TERMS: list[str] = []
MENTOR_TERMS: list[str] = []
PATRON_TERMS: list[str] = []
ALLIANCE_TERMS: list[str] = []
ENEMY_TERMS: list[str] = []
COMMAND_TERMS: list[str] = []
SPOUSE_TERMS: list[str] = []
SPOUSE_PARENT_CHILD_CONTEXT_TERMS: list[str] = []
SPOUSE_DIRECT_BINDING_TERMS: list[str] = []
PARENT_CHILD_TERMS: list[str] = []
ADOPTIVE_PARENT_CHILD_TERMS: list[str] = []
SIBLING_TERMS: list[str] = []
SWORN_SIBLING_TERMS: list[str] = []

SPOUSE_PLAN_CONTEXT_TERMS: tuple[str, ...] = (
    "許嫁",
    "許配",
    "欲嫁",
    "欲娶",
    "求婚",
    "婚配計",
    "婚配計策",
    "婚計",
    "婚策",
    "媒合",
    "媒妁",
    "說親",
    "議婚",
    "訂婚",
    "婚約",
    "轉嫁",
    "轉配",
    "獻與",
    "送與",
    "許婚",
)
SPOUSE_FAMILY_CONTEXT_TERMS: tuple[str, ...] = (
    "妻子兒女",
    "妻子",
    "妻兒",
    "妻小",
    "家眷",
    "家屬",
    "家人",
    "家口",
    "眷屬",
    "夫人",
    "婦人",
    "內人",
    "後妻",
    "前妻",
    "妾",
    "小妾",
    "側室",
    "正室",
    "女眷",
    "兒女",
    "子女",
    "家小",
    "其妻",
    "其夫",
)
SPOUSE_EXPLICIT_BINDING_EXTRAS: tuple[str, ...] = (
    "為妻",
    "為夫",
    "嫁為",
    "娶為",
    "納為",
    "配為",
    "結為夫妻",
    "結為夫婦",
    "成為夫妻",
    "成為夫婦",
    "作妻",
    "作夫",
)


_RELATIONSHIP_TYPE_REFINEMENT_RULES_LOADED = False


def _required_rule_value(by_name: dict[str, dict[str, Any]], constant_name: str) -> Any:
    row = by_name.get(constant_name)
    if row is None:
        raise SanguoGovernanceError(f"rule-relationship-type-refinement missing constantName: {constant_name}")
    return row.get("value")


def _optional_rule_value(by_name: dict[str, dict[str, Any]], constant_name: str, default: Any) -> Any:
    row = by_name.get(constant_name)
    if row is None:
        return default
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
    return {str(key): str(item) for key, item in value.items() if str(key) and str(item)}


def apply_relationship_type_refinement_rules(
    governance_root: str | Path | None = None,
    relationship_type_refinement_rules: str | Path | None = None,
) -> None:
    global _RELATIONSHIP_TYPE_REFINEMENT_RULES_LOADED
    global COARSE_RELATIONSHIP_TYPES, NON_STABLE_COARSE_TYPES, STABLE_RELATIONSHIP_TYPES, KINSHIP_RELATIONSHIP_TYPES
    global RELATIONSHIP_TYPE_FAMILIES, TYPE_LABELS
    global BETRAYAL_TERMS, MENTOR_TERMS, PATRON_TERMS, ALLIANCE_TERMS, ENEMY_TERMS, COMMAND_TERMS
    global SPOUSE_TERMS, SPOUSE_PARENT_CHILD_CONTEXT_TERMS, SPOUSE_DIRECT_BINDING_TERMS
    global PARENT_CHILD_TERMS, ADOPTIVE_PARENT_CHILD_TERMS, SIBLING_TERMS, SWORN_SIBLING_TERMS

    rows = load_relationship_type_refinement_rules(
        governance_root,
        relationship_type_refinement_rules=relationship_type_refinement_rules,
    )
    by_name = {str(row.get("constantName") or ""): row for row in rows}
    COARSE_RELATIONSHIP_TYPES = _string_set(_required_rule_value(by_name, "COARSE_RELATIONSHIP_TYPES"))
    NON_STABLE_COARSE_TYPES = _string_set(_optional_rule_value(by_name, "NON_STABLE_COARSE_TYPES", []))
    STABLE_RELATIONSHIP_TYPES = _string_set(_required_rule_value(by_name, "STABLE_RELATIONSHIP_TYPES"))
    KINSHIP_RELATIONSHIP_TYPES = _string_set(_required_rule_value(by_name, "KINSHIP_RELATIONSHIP_TYPES"))
    RELATIONSHIP_TYPE_FAMILIES = _string_mapping(_required_rule_value(by_name, "RELATIONSHIP_TYPE_FAMILIES"))
    TYPE_LABELS = _string_mapping(_required_rule_value(by_name, "TYPE_LABELS"))
    BETRAYAL_TERMS = _string_list(_required_rule_value(by_name, "BETRAYAL_TERMS"))
    MENTOR_TERMS = _string_list(_required_rule_value(by_name, "MENTOR_TERMS"))
    PATRON_TERMS = _string_list(_required_rule_value(by_name, "PATRON_TERMS"))
    ALLIANCE_TERMS = _string_list(_required_rule_value(by_name, "ALLIANCE_TERMS"))
    ENEMY_TERMS = _string_list(_required_rule_value(by_name, "ENEMY_TERMS"))
    COMMAND_TERMS = _string_list(_required_rule_value(by_name, "COMMAND_TERMS"))
    SPOUSE_TERMS = _string_list(_required_rule_value(by_name, "SPOUSE_TERMS"))
    SPOUSE_PARENT_CHILD_CONTEXT_TERMS = _string_list(
        _optional_rule_value(by_name, "SPOUSE_PARENT_CHILD_CONTEXT_TERMS", [])
    )
    SPOUSE_DIRECT_BINDING_TERMS = _string_list(_optional_rule_value(by_name, "SPOUSE_DIRECT_BINDING_TERMS", []))
    PARENT_CHILD_TERMS = _string_list(_required_rule_value(by_name, "PARENT_CHILD_TERMS"))
    ADOPTIVE_PARENT_CHILD_TERMS = _string_list(_optional_rule_value(by_name, "ADOPTIVE_PARENT_CHILD_TERMS", []))
    SIBLING_TERMS = _string_list(_required_rule_value(by_name, "SIBLING_TERMS"))
    SWORN_SIBLING_TERMS = _string_list(_required_rule_value(by_name, "SWORN_SIBLING_TERMS"))
    _RELATIONSHIP_TYPE_REFINEMENT_RULES_LOADED = True


def ensure_relationship_type_refinement_rules_loaded() -> None:
    if not _RELATIONSHIP_TYPE_REFINEMENT_RULES_LOADED:
        apply_relationship_type_refinement_rules()


def compact_text(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "").strip())


def edge_text(edge: dict[str, Any], fallback_text: str = "") -> str:
    parts = [
        edge.get("sourceQuote"),
        edge.get("evidenceText"),
        edge.get("summary"),
        fallback_text,
    ]
    return compact_text("".join(str(part or "") for part in parts))


def contains_any(text: str, terms: list[str]) -> bool:
    return any(term in text for term in terms)


def spouse_binding_terms() -> list[str]:
    ensure_relationship_type_refinement_rules_loaded()
    blocked_terms = {"婚", "嫁", "娶", "納", "配", "以女妻", "女妻之"}
    terms = [term for term in SPOUSE_DIRECT_BINDING_TERMS if len(term) > 1 and term not in blocked_terms]
    terms.extend(SPOUSE_EXPLICIT_BINDING_EXTRAS)
    return sorted(set(terms), key=lambda item: (-len(item), item))


def spouse_supports_pair_binding(text: str) -> bool:
    compact = compact_text(text)
    if not compact:
        return False
    if contains_any(compact, list(SPOUSE_PLAN_CONTEXT_TERMS)):
        return False
    if contains_any(compact, spouse_binding_terms()):
        return True
    if contains_any(compact, list(SPOUSE_FAMILY_CONTEXT_TERMS)):
        return False
    return False


def kinship_pair_binding_supported(relation_type: str, text: str) -> bool:
    ensure_relationship_type_refinement_rules_loaded()
    compact = compact_text(text)
    if not compact:
        return False
    normalized = str(relation_type or "").strip()
    if normalized == "spouse":
        return spouse_supports_pair_binding(compact)
    if normalized == "parent_child":
        terms = [term for term in PARENT_CHILD_TERMS if len(term) > 1]
        return contains_any(compact, terms)
    if normalized == "adoptive_parent_child":
        terms = [term for term in ADOPTIVE_PARENT_CHILD_TERMS if len(term) > 1]
        return contains_any(compact, terms)
    if normalized == "sibling":
        return contains_any(compact, SIBLING_TERMS)
    if normalized == "sworn_sibling":
        return contains_any(compact, SWORN_SIBLING_TERMS)
    return False


def ruler_subject_authority_terms() -> list[str]:
    pair_cues.ensure_relationship_claim_pair_cue_rules_loaded()
    return list(pair_cues.PAIR_CUE_AUTHORITY_DIRECT_TERMS)


def input_type_is_pre_refined(edge: dict[str, Any], original_type: str) -> bool:
    source_original_type = str(edge.get("originalType") or "").strip()
    if source_original_type and source_original_type != original_type:
        return True
    refinement_reasons = {str(reason) for reason in edge.get("refinementReasons") or []}
    return any(reason.endswith("_terms") or reason.endswith("_cue_override") for reason in refinement_reasons)


def relationship_type_family(relation_type: str) -> str:
    ensure_relationship_type_refinement_rules_loaded()
    normalized = str(relation_type or "").strip()
    return RELATIONSHIP_TYPE_FAMILIES.get(normalized, "relationship")


def refine_relationship_type(edge: dict[str, Any], fallback_text: str = "") -> tuple[str, list[str]]:
    ensure_relationship_type_refinement_rules_loaded()
    original_type = str(edge.get("type") or "").strip()
    source_original_type = str(edge.get("originalType") or "").strip()
    pre_refined_input = input_type_is_pre_refined(edge, original_type)
    text = edge_text(edge, fallback_text)
    reasons: list[str] = []

    if original_type in STABLE_RELATIONSHIP_TYPES and not pre_refined_input:
        return original_type, ["stable_relationship_type"]
    if original_type == "allies":
        return "alliance_oath", ["original_allies"]
    if original_type in {"confronts", "killing"}:
        return "enemy_rival", [f"original_{original_type}"]
    if original_type in NON_STABLE_COARSE_TYPES:
        reasons.append("non_stable_coarse_type")
        return original_type or "relationship", reasons

    if contains_any(text, SWORN_SIBLING_TERMS):
        reasons.append("sworn_sibling_terms")
        return "sworn_sibling", reasons
    if contains_any(text, SPOUSE_TERMS):
        if spouse_supports_pair_binding(text):
            reasons.append("spouse_terms")
            return "spouse", reasons
        reasons.append("spouse_context_rejected")
        if original_type == "spouse":
            return "relationship", reasons
    if contains_any(text, ADOPTIVE_PARENT_CHILD_TERMS):
        reasons.append("adoptive_parent_child_terms")
        return "adoptive_parent_child", reasons
    if contains_any(text, PARENT_CHILD_TERMS):
        reasons.append("parent_child_terms")
        return "parent_child", reasons
    if contains_any(text, SIBLING_TERMS):
        reasons.append("sibling_terms")
        return "sibling", reasons

    if contains_any(text, BETRAYAL_TERMS):
        reasons.append("betrayal_or_surrender_terms")
        return "betrayal_surrender", reasons
    if contains_any(text, MENTOR_TERMS):
        reasons.append("mentor_or_instruction_terms")
        return "mentor_student", reasons
    if contains_any(text, PATRON_TERMS):
        reasons.append("patronage_or_client_terms")
        return "patron_client", reasons
    if contains_any(text, ALLIANCE_TERMS):
        reasons.append("alliance_or_oath_terms")
        return "alliance_oath", reasons

    if contains_any(text, ruler_subject_authority_terms()):
        reasons.append("authority_hierarchy_terms")
        return "ruler_subject", reasons
    if contains_any(text, ENEMY_TERMS):
        reasons.append("enemy_or_rival_terms")
        return "enemy_rival", reasons

    if original_type in COARSE_RELATIONSHIP_TYPES:
        reasons.append("coarse_type_defaulted_to_ruler_subject")
        return "ruler_subject", reasons
    if pre_refined_input:
        return source_original_type or "relationship", ["unchanged_relationship_type"]
    return original_type or "relationship", ["unchanged_relationship_type"]
