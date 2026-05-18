from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

from repo_layout import resolve_repo_root
from sanguo_governance_loader import load_python_hardcode_semantic_guard_policy


REPO_ROOT = resolve_repo_root(__file__)
DEFAULT_OUTPUT_ROOT = Path("artifacts/data-pipeline/sanguo-rag/python-hardcode-semantic-guard")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Scan Python files for suspicious inline domain data / semantic guard literals."
    )
    parser.add_argument("--governance-root", default=None)
    parser.add_argument("--policy", default=None, help="Override policy-python-hardcode-semantic-guard.json path")
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    parser.add_argument("--include-glob", action="append", default=[])
    parser.add_argument("--exclude-glob", action="append", default=[])
    parser.add_argument("--write-report", action="store_true")
    return parser.parse_args()


def repo_relative(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(REPO_ROOT.resolve())).replace("\\", "/")
    except ValueError:
        return str(path.resolve()).replace("\\", "/")


def resolve_output_root(path_text: str | Path) -> Path:
    path = Path(path_text)
    if path.is_absolute():
        return path.resolve()
    return (REPO_ROOT / path).resolve()


def compact_text(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def contains_cjk(text: str) -> bool:
    return bool(re.search(r"[\u4e00-\u9fff]", text))


def collection_string_values(node: ast.AST | None) -> list[str]:
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        values = [item.value for item in node.elts if isinstance(item, ast.Constant) and isinstance(item.value, str)]
        if len(values) == len(node.elts):
            return [str(item) for item in values]
        return []
    if isinstance(node, ast.Dict):
        values: list[str] = []
        for key, value in zip(node.keys, node.values):
            if key is not None and not (isinstance(key, ast.Constant) and isinstance(key.value, str)):
                return []
            if isinstance(value, (ast.List, ast.Tuple, ast.Set)):
                nested = collection_string_values(value)
                if not nested:
                    return []
                values.extend(nested)
                continue
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                values.append(str(value.value))
                continue
            return []
        return values
    return []


def membership_string_values(node: ast.AST | None) -> list[str]:
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        values = [item.value for item in node.elts if isinstance(item, ast.Constant) and isinstance(item.value, str)]
        if len(values) == len(node.elts):
            return [str(item) for item in values]
    return []


def regex_alternation_count(pattern: str) -> int:
    try:
        return len(re.findall(r"(?<!\\)\|", pattern)) + 1
    except re.error:
        return pattern.count("|") + 1


def normalized_source_segment(text: str, node: ast.AST | None, fallback: str = "") -> str:
    segment = ast.get_source_segment(text, node) if node is not None else None
    return compact_text(segment or fallback)


def build_signature(target_path: str, kind: str, symbol: str, payload: str) -> str:
    digest = hashlib.sha1(f"{target_path}|{kind}|{symbol}|{payload}".encode("utf-8")).hexdigest()
    return f"{kind}.{digest[:16]}"


def severity_for(kind: str, count: int, cjk_count: int) -> str:
    if kind == "top_level_collection":
        return "high" if count >= 12 or cjk_count >= 4 else "medium"
    if kind == "regex_alternation":
        return "high" if count >= 10 or cjk_count >= 1 else "medium"
    if kind == "numeric_threshold":
        return "medium"
    if kind == "inline_membership":
        return "high" if cjk_count >= 2 or count >= 6 else "medium"
    return "medium"


def approved_signature_index(policy: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows = policy.get("approvedFindings")
    if not isinstance(rows, list):
        return {}
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        signature = str(row.get("signature") or "").strip()
        if signature:
            result[signature] = row
    return result


def resolve_python_files(repo_root: Path, include_globs: list[str], exclude_globs: list[str]) -> list[Path]:
    excluded: set[Path] = set()
    for pattern in exclude_globs:
        excluded.update(path.resolve() for path in repo_root.glob(pattern))
    rows: set[Path] = set()
    for pattern in include_globs:
        rows.update(path.resolve() for path in repo_root.glob(pattern) if path.is_file())
    return sorted(
        path
        for path in rows
        if path.suffix == ".py"
        and "__pycache__" not in path.parts
        and path.resolve() not in excluded
    )


def detector_name_keywords(policy: dict[str, Any], key: str) -> list[str]:
    detector = policy.get("detectors", {}).get(key, {})
    values = detector.get("nameKeywords") if isinstance(detector, dict) else []
    if not isinstance(values, list):
        return []
    return [str(item).strip().upper() for item in values if str(item).strip()]


def scan_python_hardcode_semantics(
    *,
    repo_root: Path,
    policy: dict[str, Any],
    include_globs: list[str] | None = None,
    exclude_globs: list[str] | None = None,
) -> dict[str, Any]:
    include_patterns = include_globs or [str(item) for item in (policy.get("includeGlobs") or []) if str(item).strip()]
    exclude_patterns = exclude_globs or [str(item) for item in (policy.get("excludeGlobs") or []) if str(item).strip()]
    files = resolve_python_files(repo_root, include_patterns, exclude_patterns)
    approved = approved_signature_index(policy)

    detector_config = policy.get("detectors") if isinstance(policy.get("detectors"), dict) else {}
    top_level_config = detector_config.get("topLevelCollection") if isinstance(detector_config.get("topLevelCollection"), dict) else {}
    numeric_config = detector_config.get("numericThreshold") if isinstance(detector_config.get("numericThreshold"), dict) else {}
    regex_config = detector_config.get("regexAlternation") if isinstance(detector_config.get("regexAlternation"), dict) else {}
    membership_config = detector_config.get("inlineMembership") if isinstance(detector_config.get("inlineMembership"), dict) else {}

    top_level_keywords = detector_name_keywords(policy, "topLevelCollection")
    numeric_keywords = detector_name_keywords(policy, "numericThreshold")
    min_top_level_items = int(top_level_config.get("minItems") or 0)
    min_regex_alternations = int(regex_config.get("minAlternations") or 0)
    min_membership_items = int(membership_config.get("minItems") or 0)
    ignore_zero_numeric = bool(numeric_config.get("ignoreZero"))

    findings: list[dict[str, Any]] = []
    parse_error_paths: list[str] = []

    for path in files:
        rel_path = repo_relative(path)
        try:
            text = path.read_text(encoding="utf-8-sig")
            tree = ast.parse(text, filename=str(path))
        except SyntaxError:
            parse_error_paths.append(rel_path)
            continue

        for node in tree.body:
            if not isinstance(node, ast.Assign):
                continue
            for target in [item for item in node.targets if isinstance(item, ast.Name)]:
                symbol = target.id
                symbol_upper = symbol.upper()
                values = collection_string_values(node.value)
                if values and len(values) >= min_top_level_items and any(keyword in symbol_upper for keyword in top_level_keywords):
                    payload = " | ".join(values)
                    signature = build_signature(rel_path, "top_level_collection", symbol, payload)
                    finding = {
                        "signature": signature,
                        "targetPath": rel_path,
                        "line": node.lineno,
                        "kind": "top_level_collection",
                        "symbol": symbol,
                        "itemCount": len(values),
                        "cjkCount": sum(1 for item in values if contains_cjk(item)),
                        "preview": values[:8],
                        "sourceSnippet": normalized_source_segment(text, node.value, payload)[:220],
                    }
                    findings.append(finding)
                if isinstance(node.value, ast.Constant) and isinstance(node.value.value, (int, float)):
                    value = node.value.value
                    if ignore_zero_numeric and float(value) == 0.0:
                        continue
                    if any(keyword in symbol_upper for keyword in numeric_keywords):
                        signature = build_signature(rel_path, "numeric_threshold", symbol, str(value))
                        findings.append(
                            {
                                "signature": signature,
                                "targetPath": rel_path,
                                "line": node.lineno,
                                "kind": "numeric_threshold",
                                "symbol": symbol,
                                "itemCount": 1,
                                "cjkCount": 0,
                                "preview": [value],
                                "sourceSnippet": normalized_source_segment(text, node.value, str(value)),
                            }
                        )

        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                is_re_call = (
                    isinstance(func, ast.Attribute)
                    and isinstance(func.value, ast.Name)
                    and func.value.id == "re"
                    and func.attr in {"compile", "search", "match", "findall"}
                    and node.args
                )
                if is_re_call:
                    pattern_node = node.args[0]
                    if isinstance(pattern_node, ast.Constant) and isinstance(pattern_node.value, str):
                        pattern = str(pattern_node.value)
                        alternation_count = regex_alternation_count(pattern)
                        if alternation_count >= min_regex_alternations:
                            signature = build_signature(rel_path, "regex_alternation", func.attr, pattern)
                            findings.append(
                                {
                                    "signature": signature,
                                    "targetPath": rel_path,
                                    "line": node.lineno,
                                    "kind": "regex_alternation",
                                    "symbol": func.attr,
                                    "itemCount": alternation_count,
                                    "cjkCount": 1 if contains_cjk(pattern) else 0,
                                    "preview": [pattern[:120]],
                                    "sourceSnippet": normalized_source_segment(text, pattern_node, pattern)[:220],
                                }
                            )
            if isinstance(node, ast.Compare) and len(node.ops) == 1 and len(node.comparators) == 1:
                if not isinstance(node.ops[0], (ast.In, ast.NotIn)):
                    continue
                values = membership_string_values(node.comparators[0])
                if len(values) < min_membership_items:
                    continue
                cjk_count = sum(1 for item in values if contains_cjk(item))
                has_identifier_signal = any("_" in item or "-" in item for item in values)
                if not (cjk_count >= 1 or has_identifier_signal):
                    continue
                payload = " | ".join(values)
                signature = build_signature(rel_path, "inline_membership", "in-literal", payload)
                findings.append(
                    {
                        "signature": signature,
                        "targetPath": rel_path,
                        "line": node.lineno,
                        "kind": "inline_membership",
                        "symbol": "in-literal",
                        "itemCount": len(values),
                        "cjkCount": cjk_count,
                        "preview": values[:8],
                        "sourceSnippet": normalized_source_segment(text, node.comparators[0], payload)[:220],
                    }
                )

    for row in findings:
        row["severity"] = severity_for(row["kind"], int(row["itemCount"]), int(row["cjkCount"]))
        approved_row = approved.get(row["signature"])
        if approved_row:
            row["approvalStatus"] = str(approved_row.get("status") or "approved")
            row["approvalId"] = str(approved_row.get("id") or row["signature"])
            row["approvalReason"] = str(approved_row.get("reason") or approved_row.get("decision") or "").strip()
            row["approved"] = True
        else:
            row["approvalStatus"] = "unapproved"
            row["approvalId"] = ""
            row["approvalReason"] = ""
            row["approved"] = False

    findings.sort(key=lambda row: (row["targetPath"], int(row["line"]), row["kind"], row["symbol"]))
    unapproved = [row for row in findings if not row["approved"]]
    summary = {
        "scannedFileCount": len(files),
        "parseErrorCount": len(parse_error_paths),
        "findingCount": len(findings),
        "approvedFindingCount": len(findings) - len(unapproved),
        "unapprovedFindingCount": len(unapproved),
        "kindCounts": dict(Counter(row["kind"] for row in findings)),
        "severityCounts": dict(Counter(row["severity"] for row in findings)),
        "topFiles": [
            {"targetPath": target_path, "findingCount": count}
            for target_path, count in Counter(row["targetPath"] for row in findings).most_common(20)
        ],
    }
    return {
        "policyId": str(policy.get("id") or ""),
        "summary": summary,
        "findings": findings,
        "unapprovedFindings": unapproved,
        "parseErrors": parse_error_paths,
    }


def write_report(output_root: Path, payload: dict[str, Any]) -> None:
    output_root.mkdir(parents=True, exist_ok=True)
    json_path = output_root / "python-hardcode-semantic-guard.report.json"
    md_path = output_root / "python-hardcode-semantic-guard.report.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    lines = [
        "# Python Hardcode Semantic Guard",
        "",
        f"- policy: `{payload.get('policyId')}`",
        f"- scanned files: `{payload['summary']['scannedFileCount']}`",
        f"- findings: `{payload['summary']['findingCount']}`",
        f"- approved: `{payload['summary']['approvedFindingCount']}`",
        f"- unapproved: `{payload['summary']['unapprovedFindingCount']}`",
        "",
        "## Top Files",
        "",
    ]
    for row in payload["summary"]["topFiles"]:
        lines.append(f"- `{row['targetPath']}`: `{row['findingCount']}`")
    lines.extend(["", "## Unapproved Findings", ""])
    for row in payload["unapprovedFindings"][:120]:
        lines.append(
            f"- `{row['targetPath']}:{row['line']}` `{row['kind']}` `{row['symbol']}` x{row['itemCount']} ({row['severity']})"
        )
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    policy = load_python_hardcode_semantic_guard_policy(
        args.governance_root,
        python_hardcode_semantic_guard_policy=args.policy,
    )
    payload = scan_python_hardcode_semantics(
        repo_root=REPO_ROOT,
        policy=policy,
        include_globs=args.include_glob or None,
        exclude_globs=args.exclude_glob or None,
    )
    if args.write_report:
        write_report(resolve_output_root(args.output_root), payload)
    print(json.dumps(payload["summary"], ensure_ascii=False, indent=2))
    if bool(policy.get("failOnUnapprovedFindings")) and payload["summary"]["unapprovedFindingCount"] > 0:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
