from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from repo_layout import resolve_repo_root


REPO_ROOT = resolve_repo_root(__file__)
DEFAULT_ALIAS_RECORDS = Path("artifacts/data-pipeline/sanguo-rag/extracted/alias-dictionary/general-alias-records.json")
DEFAULT_FORMAL_MAP = Path("artifacts/data-pipeline/sanguo-rag/extracted/alias-dictionary/formal-mention-map.json")


def resolve_path(path_text: str | Path) -> Path:
    path = Path(path_text)
    if path.is_absolute():
        return path
    return (REPO_ROOT / path).resolve()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def compact_text(value: Any) -> str:
    return str(value or "").strip()


def string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value] if value.strip() else []
    if not isinstance(value, list):
        return []
    return [str(item or "").strip() for item in value if str(item or "").strip()]


def normalize_label(value: Any) -> str:
    return "".join(str(value or "").strip().split()).lower()


def alias_rows(record: dict[str, Any]) -> list[dict[str, Any]]:
    return [row for row in record.get("aliases") or [] if isinstance(row, dict)]


def is_courtesy_alias(alias_row: dict[str, Any]) -> bool:
    if compact_text(alias_row.get("aliasSource")) == "wiki-courtesy-alias":
        return True
    sources = {compact_text(item).lower() for item in alias_row.get("sources") or []}
    return "courtesy" in sources


def record_view(record: dict[str, Any]) -> dict[str, Any]:
    name = compact_text(record.get("name"))
    accepted = [
        compact_text(alias.get("label"))
        for alias in alias_rows(record)
        if compact_text(alias.get("reviewStatus")).lower() == "accepted" and compact_text(alias.get("label"))
    ]
    collisions = [
        compact_text(alias.get("label"))
        for alias in alias_rows(record)
        if compact_text(alias.get("reviewStatus")).lower() == "collision" and compact_text(alias.get("label"))
    ]
    accepted_courtesy = [
        compact_text(alias.get("label"))
        for alias in alias_rows(record)
        if compact_text(alias.get("reviewStatus")).lower() == "accepted" and is_courtesy_alias(alias)
    ]
    collision_courtesy = [
        compact_text(alias.get("label"))
        for alias in alias_rows(record)
        if compact_text(alias.get("reviewStatus")).lower() == "collision" and is_courtesy_alias(alias)
    ]
    accepted_extra = [alias for alias in accepted if normalize_label(alias) != normalize_label(name)]
    return {
        "generalId": compact_text(record.get("generalId")),
        "name": name,
        "reviewStatus": compact_text(record.get("reviewStatus")),
        "acceptedAliasesZhTw": list(dict.fromkeys(accepted)),
        "acceptedExtraAliasesZhTw": list(dict.fromkeys(accepted_extra)),
        "ambiguousAliasesZhTw": list(dict.fromkeys(collisions)),
        "scopedAliasesZhTw": string_list(record.get("scopedAliasesZhTw")),
        "blockedAliasesZhTw": string_list(record.get("blockedAliasesZhTw")),
        "acceptedCourtesyAliasesZhTw": list(dict.fromkeys(accepted_courtesy)),
        "ambiguousCourtesyAliasesZhTw": list(dict.fromkeys(collision_courtesy)),
    }


def formal_alias_map(payload: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    mapping: dict[str, list[dict[str, Any]]] = {}
    for entry in payload.get("entries") or []:
        if not isinstance(entry, dict):
            continue
        alias = compact_text(entry.get("alias"))
        if alias:
            mapping.setdefault(alias, []).append(entry)
    return mapping


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit accepted vs ambiguous alias coverage for Sanguo records.")
    parser.add_argument("--alias-records", default=str(DEFAULT_ALIAS_RECORDS))
    parser.add_argument("--formal-map", default=str(DEFAULT_FORMAL_MAP))
    parser.add_argument("--focus-general-ids-file", default="", help="Optional UTF-8 text file with one generalId per line.")
    parser.add_argument("--output-json", default="")
    parser.add_argument("--output-md", default="")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    alias_records_path = resolve_path(args.alias_records)
    formal_map_path = resolve_path(args.formal_map)
    alias_payload = read_json(alias_records_path)
    formal_payload = read_json(formal_map_path)
    rows = alias_payload.get("data") or []
    views = [record_view(row) for row in rows if isinstance(row, dict)]
    by_id = {row["generalId"]: row for row in views if row.get("generalId")}
    focus_ids = []
    if compact_text(args.focus_general_ids_file):
        focus_path = resolve_path(args.focus_general_ids_file)
        focus_ids = [line.strip() for line in focus_path.read_text(encoding="utf-8-sig").splitlines() if line.strip()]

    tier_counts = Counter()
    focus_missing_extra: list[dict[str, Any]] = []
    focus_collision_only: list[dict[str, Any]] = []
    for row in views:
        if row["acceptedCourtesyAliasesZhTw"]:
            tier = "courtesy-covered"
        elif row["acceptedExtraAliasesZhTw"]:
            tier = "accepted-extra"
        elif row["scopedAliasesZhTw"]:
            tier = "scoped-only"
        elif row["blockedAliasesZhTw"]:
            tier = "reviewed-no-global-alias"
        elif row["ambiguousAliasesZhTw"]:
            tier = "collision-only"
        else:
            tier = "canonical-only"
        row["aliasCoverageTier"] = tier
        tier_counts[tier] += 1

    for general_id in focus_ids:
        row = by_id.get(general_id)
        if not row:
            focus_missing_extra.append({"generalId": general_id, "reason": "missing-record"})
            continue
        if not row["acceptedExtraAliasesZhTw"] and not row["scopedAliasesZhTw"] and not row["blockedAliasesZhTw"]:
            focus_missing_extra.append(
                {
                    "generalId": general_id,
                    "name": row["name"],
                    "reviewStatus": row["reviewStatus"],
                    "ambiguousAliasesZhTw": row["ambiguousAliasesZhTw"],
                }
            )
        if row["ambiguousAliasesZhTw"] and not row["acceptedExtraAliasesZhTw"]:
            focus_collision_only.append(
                {
                    "generalId": general_id,
                    "name": row["name"],
                    "ambiguousAliasesZhTw": row["ambiguousAliasesZhTw"],
                    "ambiguousCourtesyAliasesZhTw": row["ambiguousCourtesyAliasesZhTw"],
                }
            )

    famous_alias_checks = {}
    formal_by_alias = formal_alias_map(formal_payload)
    for alias in ["孔明", "玄德", "孟德", "子龍", "雲長", "子敬", "安國"]:
        famous_alias_checks[alias] = formal_by_alias.get(alias, [])

    report = {
        "canonicalWrites": False,
        "aliasRecordsPath": str(alias_records_path),
        "formalMapPath": str(formal_map_path),
        "totalGenerals": len(views),
        "aliasCoverageTierCounts": dict(sorted(tier_counts.items())),
        "acceptedExtraAliasCount": sum(1 for row in views if row["acceptedExtraAliasesZhTw"]),
        "acceptedCourtesyAliasCount": sum(1 for row in views if row["acceptedCourtesyAliasesZhTw"]),
        "collisionOnlyAliasCount": sum(1 for row in views if row["aliasCoverageTier"] == "collision-only"),
        "focusGeneralIdCount": len(focus_ids),
        "focusMissingAcceptedExtraAliasCount": len(focus_missing_extra),
        "focusCollisionOnlyCount": len(focus_collision_only),
        "focusAliasSourceResolvedCount": len(focus_ids) - len(focus_missing_extra),
        "focusMissingAcceptedExtraAliases": focus_missing_extra,
        "focusCollisionOnlyAliases": focus_collision_only,
        "famousAliasChecks": famous_alias_checks,
    }

    md_lines = [
        "# Alias Baseline Audit",
        "",
        f"- 總人物數：`{report['totalGenerals']}`",
        f"- 有已接受額外別名的人物數：`{report['acceptedExtraAliasCount']}`",
        f"- 有已接受字號別名的人物數：`{report['acceptedCourtesyAliasCount']}`",
        f"- 只有碰撞別名的人物數：`{report['collisionOnlyAliasCount']}`",
        f"- focus 人物數：`{report['focusGeneralIdCount']}`",
        f"- focus 缺少已接受額外別名的人物數：`{report['focusMissingAcceptedExtraAliasCount']}`",
        f"- focus 已由來源審核覆蓋的人物數：`{report['focusAliasSourceResolvedCount']}`",
        f"- focus 只有碰撞別名的人物數：`{report['focusCollisionOnlyCount']}`",
        "",
        "## Coverage",
        "",
        json.dumps(report["aliasCoverageTierCounts"], ensure_ascii=False, indent=2),
        "",
    ]
    if focus_missing_extra:
        md_lines.extend(["## Focus 缺口", "", json.dumps(focus_missing_extra, ensure_ascii=False, indent=2), ""])
    if focus_collision_only:
        md_lines.extend(["## Focus 只有碰撞別名", "", json.dumps(focus_collision_only, ensure_ascii=False, indent=2), ""])
    md_lines.extend(["## 常見字號檢查", "", json.dumps(famous_alias_checks, ensure_ascii=False, indent=2), ""])

    if compact_text(args.output_json):
        output_json = resolve_path(args.output_json)
        output_json.parent.mkdir(parents=True, exist_ok=True)
        output_json.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if compact_text(args.output_md):
        output_md = resolve_path(args.output_md)
        output_md.parent.mkdir(parents=True, exist_ok=True)
        output_md.write_text("\n".join(md_lines), encoding="utf-8")

    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
