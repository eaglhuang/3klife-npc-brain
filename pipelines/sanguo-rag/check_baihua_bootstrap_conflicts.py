from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from repo_layout import resolve_repo_root


REPO_ROOT = resolve_repo_root(__file__)
DEFAULT_INPUT_PATH = REPO_ROOT / "artifacts/data-pipeline/sanguo-rag/extracted/baihua-bootstrap/wave-001/merged-bootstrap-candidates.jsonl"
DEFAULT_POLICY_PATH = REPO_ROOT / "data/sanguo/policies/policy-baihua-bootstrap-lane.json"
DEFAULT_OUTPUT_ROOT = REPO_ROOT / "artifacts/data-pipeline/sanguo-rag/extracted/baihua-bootstrap/wave-001"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check baihua bootstrap candidate conflicts and emit conflict report.")
    parser.add_argument("--input-path", default=str(DEFAULT_INPUT_PATH))
    parser.add_argument("--policy-path", default=str(DEFAULT_POLICY_PATH))
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    parser.add_argument("--output-file-name", default="merged-bootstrap-candidates-conflict-checked.jsonl")
    parser.add_argument("--report-file-name", default="bootstrap-conflict-report.json")
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return payload


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8-sig") as handle:
        for line_no, line in enumerate(handle, 1):
            text = line.strip()
            if not text:
                continue
            payload = json.loads(text)
            if not isinstance(payload, dict):
                raise ValueError(f"JSONL row must be object: {path}:{line_no}")
            rows.append(payload)
    return rows


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def pair_id(from_id: str, to_id: str) -> str:
    left, right = sorted([from_id.strip(), to_id.strip()])
    return f"{left}|{right}"


def main() -> int:
    args = parse_args()
    input_path = Path(args.input_path).resolve()
    policy_path = Path(args.policy_path).resolve()
    output_root = Path(args.output_root).resolve()
    output_path = output_root / args.output_file_name
    report_path = output_root / args.report_file_name

    if not args.overwrite and (output_path.exists() or report_path.exists()):
        raise FileExistsError(f"Output exists. Re-run with --overwrite: {output_path}")

    policy = read_json(policy_path)
    rules = policy.get("conflictRules")
    if not isinstance(rules, list):
        rules = []
    candidates = read_jsonl(input_path)

    rows_with_index: list[tuple[int, dict[str, Any]]] = []
    pair_type_to_indexes: dict[tuple[str, str], list[int]] = defaultdict(list)
    type_pair_counter: Counter[str] = Counter()

    for index, row in enumerate(candidates):
        relationship_type = str(row.get("relationshipType") or "").strip()
        from_id = str(row.get("fromId") or "").strip()
        to_id = str(row.get("toId") or "").strip()
        if not relationship_type or not from_id or not to_id:
            rows_with_index.append((index, row))
            continue
        normalized_pair = pair_id(from_id, to_id)
        pair_type_to_indexes[(normalized_pair, relationship_type)].append(index)
        type_pair_counter[f"{relationship_type}|{normalized_pair}"] += 1
        rows_with_index.append((index, row))

    flagged: dict[int, list[str]] = defaultdict(list)
    conflicts: list[dict[str, Any]] = []
    rule_counter: Counter[str] = Counter()

    for rule in rules:
        if not isinstance(rule, dict):
            continue
        rule_id = str(rule.get("ruleId") or "").strip() or "anonymous-rule"
        left_type = str(rule.get("leftType") or "").strip()
        right_type = str(rule.get("rightType") or "").strip()
        if not left_type or not right_type:
            continue

        all_pairs = {
            pair
            for (pair, relationship_type) in pair_type_to_indexes.keys()
            if relationship_type in {left_type, right_type}
        }
        for normalized_pair in sorted(all_pairs):
            left_indexes = pair_type_to_indexes.get((normalized_pair, left_type)) or []
            right_indexes = pair_type_to_indexes.get((normalized_pair, right_type)) or []
            if not left_indexes or not right_indexes:
                continue
            conflict_id = f"{rule_id}|{normalized_pair}|{left_type}|{right_type}"
            rule_counter[rule_id] += 1
            related_indexes = sorted(set([*left_indexes, *right_indexes]))
            for item_index in related_indexes:
                flagged[item_index].append(rule_id)
            conflicts.append(
                {
                    "conflictId": conflict_id,
                    "ruleId": rule_id,
                    "pairId": normalized_pair,
                    "leftType": left_type,
                    "rightType": right_type,
                    "relatedTrustKeys": sorted(
                        {
                            str(candidates[item_index].get("trustKey") or "")
                            for item_index in related_indexes
                            if str(candidates[item_index].get("trustKey") or "")
                        }
                    ),
                }
            )

    # Additional guard: opposite parent-child directions on the same pair.
    parent_pair_rows: dict[str, list[tuple[int, dict[str, Any]]]] = defaultdict(list)
    for index, row in rows_with_index:
        if str(row.get("relationshipType") or "").strip() != "parent_child":
            continue
        from_id = str(row.get("fromId") or "").strip()
        to_id = str(row.get("toId") or "").strip()
        if not from_id or not to_id:
            continue
        parent_pair_rows[pair_id(from_id, to_id)].append((index, row))

    for normalized_pair, pair_rows in sorted(parent_pair_rows.items()):
        directions = {(str(row.get("fromId") or ""), str(row.get("toId") or "")) for (_index, row) in pair_rows}
        if len(directions) < 2:
            continue
        rule_id = "parent-child-direction-inverse"
        rule_counter[rule_id] += 1
        related_indexes = [index for (index, _row) in pair_rows]
        for item_index in related_indexes:
            flagged[item_index].append(rule_id)
        conflicts.append(
            {
                "conflictId": f"{rule_id}|{normalized_pair}",
                "ruleId": rule_id,
                "pairId": normalized_pair,
                "leftType": "parent_child",
                "rightType": "parent_child",
                "relatedTrustKeys": sorted(
                    {
                        str(candidates[item_index].get("trustKey") or "")
                        for item_index in related_indexes
                        if str(candidates[item_index].get("trustKey") or "")
                    }
                ),
            }
        )

    output_rows: list[dict[str, Any]] = []
    flagged_counter = 0
    for index, row in enumerate(candidates):
        cloned = dict(row)
        existing_flags = [str(item).strip() for item in (cloned.get("conflictFlags") or []) if str(item or "").strip()]
        new_flags = sorted(set([*existing_flags, *flagged.get(index, [])]))
        cloned["conflictFlags"] = new_flags
        if new_flags:
            cloned["bootstrapStage"] = "conflicted"
            flagged_counter += 1
        output_rows.append(cloned)

    output_rows.sort(key=lambda row: (str(row.get("relationshipType")), str(row.get("fromId")), str(row.get("toId"))))
    write_jsonl(output_path, output_rows)

    report = {
        "mode": "baihua-bootstrap-conflict-check",
        "generatedAt": utc_now(),
        "canonicalWrites": False,
        "inputs": {
            "candidatePath": str(input_path),
            "policyPath": str(policy_path),
            "conflictRuleCount": len(rules),
        },
        "outputs": {
            "conflictCheckedCandidatePath": str(output_path),
            "reportPath": str(report_path),
            "inputCount": len(candidates),
            "flaggedCandidateCount": flagged_counter,
            "conflictCount": len(conflicts),
            "conflictRuleHitCounts": dict(sorted(rule_counter.items())),
        },
        "conflicts": conflicts,
    }
    write_json(report_path, report)
    print(f"[check_baihua_bootstrap_conflicts] wrote {output_path}")
    print(f"[check_baihua_bootstrap_conflicts] wrote {report_path}")
    print(
        "[check_baihua_bootstrap_conflicts] "
        f"candidates={len(candidates)} flagged={flagged_counter} conflicts={len(conflicts)} canonicalWrites=false"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
