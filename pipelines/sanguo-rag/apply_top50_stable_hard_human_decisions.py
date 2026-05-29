from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from repo_layout import resolve_repo_root
from versioning import build_version_metadata


REPO_ROOT = resolve_repo_root(__file__)
DEFAULT_INPUT_PATH = (
    REPO_ROOT
    / "artifacts/data-pipeline/sanguo-rag/extracted/baihua-bootstrap/wave-001-max400/top50-stable-hard-human-decisions.json"
)
DEFAULT_OUTPUT_PATH = (
    REPO_ROOT
    / "artifacts/data-pipeline/sanguo-rag/extracted/baihua-bootstrap/wave-001-max400/top50-stable-hard-human-decisions.applied.json"
)
DEFAULT_SUMMARY_PATH = (
    REPO_ROOT
    / "artifacts/data-pipeline/sanguo-rag/extracted/baihua-bootstrap/wave-001-max400/top50-stable-hard-human-decisions.applied.summary.json"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Apply Top50 stable hard human decisions and emit trust-zone compatible human-decisions input."
    )
    parser.add_argument("--input-path", default=str(DEFAULT_INPUT_PATH))
    parser.add_argument("--output-path", default=str(DEFAULT_OUTPUT_PATH))
    parser.add_argument("--summary-path", default=str(DEFAULT_SUMMARY_PATH))
    parser.add_argument("--reviewer", default="human")
    parser.add_argument(
        "--generated-notes-prefix",
        default="top50-stable-hard-human-review",
        help="Prefix used when generating command notes from approved/rejected decisions.",
    )
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value] if value.strip() else []
    if not isinstance(value, list):
        return []
    return [str(item or "").strip() for item in value if str(item or "").strip()]


def normalized_set(items: list[str], fallbacks: list[str]) -> set[str]:
    values = {item.strip().casefold() for item in items if item.strip()}
    if values:
        return values
    return {item.casefold() for item in fallbacks}


def repo_relative(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(REPO_ROOT.resolve())).replace("\\", "/")
    except ValueError:
        return str(path.resolve())


def first_nonempty(items: list[str], fallback: str) -> str:
    for item in items:
        if item.strip():
            return item.strip()
    return fallback


def preferred_action(items: list[str], fallback: str, canonical: str) -> str:
    for item in items:
        if item.strip() == canonical:
            return canonical
    return first_nonempty(items, fallback)


def valid_command_rows(rows: Any) -> list[dict[str, Any]]:
    if not isinstance(rows, list):
        return []
    cleaned: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        trust_key = str(row.get("trustKey") or "").strip()
        action = str(row.get("action") or "").strip()
        if not trust_key or not action:
            continue
        cleaned.append(row)
    return cleaned


def main() -> int:
    args = parse_args()
    input_path = Path(args.input_path).resolve()
    output_path = Path(args.output_path).resolve()
    summary_path = Path(args.summary_path).resolve()

    if not input_path.exists():
        raise FileNotFoundError(f"Input decisions file not found: {input_path}")
    if not args.overwrite and (output_path.exists() or summary_path.exists()):
        raise FileExistsError("Output exists. Re-run with --overwrite.")

    payload = read_json(input_path)
    if not isinstance(payload, dict):
        raise ValueError("Decision file must be a JSON object.")

    command_field = str(payload.get("commandField") or "action")
    decision_field = str(payload.get("decisionField") or "decision")
    approved_statuses = normalized_set(
        string_list(payload.get("approvedStatuses")),
        ["通過", "approved", "human-approved", "accept", "accepted"],
    )
    rejected_statuses = normalized_set(
        string_list(payload.get("rejectedStatuses")),
        ["打叉", "rejected", "reject"],
    )

    available_commands = payload.get("availableCommands") if isinstance(payload.get("availableCommands"), dict) else {}
    whitelist_actions = string_list(available_commands.get("forceWhitelistActions"))
    blacklist_actions = string_list(available_commands.get("forceBlacklistActions"))
    remove_actions = string_list(available_commands.get("removeFromIndexActions"))
    whitelist_action = preferred_action(whitelist_actions, "force-whitelist", "force-whitelist")
    blacklist_action = preferred_action(blacklist_actions, "force-blacklist", "force-blacklist")
    remove_action = preferred_action(remove_actions, "remove-from-index", "remove-from-index")

    explicit_commands = valid_command_rows(payload.get("commands"))
    command_by_trust_key: dict[str, dict[str, Any]] = {
        str(row.get("trustKey") or "").strip(): row for row in explicit_commands
    }

    output_commands: list[dict[str, Any]] = []
    source_decisions = payload.get("decisions")
    if not isinstance(source_decisions, list):
        source_decisions = []

    counters: Counter[str] = Counter()
    skipped_pending: list[str] = []
    skipped_unknown: list[str] = []

    for decision_row in source_decisions:
        if not isinstance(decision_row, dict):
            continue
        trust_key = str(decision_row.get("trustKey") or "").strip()
        if not trust_key:
            continue

        if trust_key in command_by_trust_key:
            explicit = command_by_trust_key[trust_key]
            action = str(explicit.get(command_field) or explicit.get("action") or "").strip()
            if not action:
                continue
            output_commands.append(
                {
                    "action": action,
                    "trustKey": trust_key,
                    "reviewer": explicit.get("reviewer") or decision_row.get("reviewer") or args.reviewer,
                    "notes": explicit.get("notes") or decision_row.get("notes") or "",
                    "canonicalWrites": False,
                }
            )
            counters[f"explicit:{action}"] += 1
            continue

        status = str(decision_row.get(decision_field) or "").strip()
        status_key = status.casefold()
        if not status:
            skipped_pending.append(trust_key)
            continue

        action = ""
        if status_key in approved_statuses:
            action = whitelist_action
            counters["approved"] += 1
        elif status_key in rejected_statuses:
            action = blacklist_action
            counters["rejected"] += 1
        elif status_key == "pending":
            skipped_pending.append(trust_key)
            continue
        else:
            skipped_unknown.append(trust_key)
            continue

        relation = str(decision_row.get("relationshipType") or "").strip()
        pair = f"{decision_row.get('fromId') or ''}->{decision_row.get('toId') or ''}".strip("->")
        notes = str(decision_row.get("notes") or "").strip()
        if not notes:
            notes = f"{args.generated_notes_prefix}: {status} {relation} {pair}".strip()

        output_commands.append(
            {
                "action": action,
                "trustKey": trust_key,
                "reviewer": decision_row.get("reviewer") or args.reviewer,
                "notes": notes,
                "canonicalWrites": False,
            }
        )

    version_metadata = build_version_metadata(
        schema_version="top50-stable-hard-human-decisions.v1",
        artifact_paths=[input_path],
        repo_root=REPO_ROOT,
    )
    output_payload = {
        "version": "1.0.0",
        "mode": "relationship-trust-zone-human-decisions",
        "reviewer": args.reviewer,
        "reviewedAt": utc_now(),
        "canonicalWrites": False,
        **version_metadata,
        "sourceDecisionPath": repo_relative(input_path),
        "commands": output_commands,
    }
    write_json(output_path, output_payload)

    summary_payload = {
        "mode": "top50-stable-hard-human-decisions-applier",
        "generatedAt": utc_now(),
        **version_metadata,
        "canonicalWrites": False,
        "inputPath": repo_relative(input_path),
        "outputPath": repo_relative(output_path),
        "reviewer": args.reviewer,
        "counts": {
            "decisionRows": len(source_decisions),
            "outputCommands": len(output_commands),
            "forceWhitelist": sum(1 for row in output_commands if row.get("action") == whitelist_action),
            "forceBlacklist": sum(1 for row in output_commands if row.get("action") == blacklist_action),
            "removeFromIndex": sum(1 for row in output_commands if row.get("action") == remove_action),
            "skippedPending": len(skipped_pending),
            "skippedUnknown": len(skipped_unknown),
        },
        "statusBuckets": dict(sorted(counters.items())),
        "skippedPendingTrustKeys": skipped_pending,
        "skippedUnknownTrustKeys": skipped_unknown,
        "actionVocabulary": {
            "forceWhitelist": whitelist_action,
            "forceBlacklist": blacklist_action,
            "removeFromIndex": remove_action,
        },
    }
    write_json(summary_path, summary_payload)

    print(f"[apply_top50_stable_hard_human_decisions] wrote {output_path}")
    print(f"[apply_top50_stable_hard_human_decisions] wrote {summary_path}")
    print(
        "[apply_top50_stable_hard_human_decisions] "
        f"commands={len(output_commands)} whitelist={summary_payload['counts']['forceWhitelist']} "
        f"blacklist={summary_payload['counts']['forceBlacklist']} pending={summary_payload['counts']['skippedPending']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
