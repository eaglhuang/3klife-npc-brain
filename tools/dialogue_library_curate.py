"""Curate the NPC brain dialogue library: list, flag, delete (soft) and regenerate lines.

Secrets: NPC_ADMIN_TOKEN comes from the environment, or from the repo-root .env as a fallback.
It is only sent in the Authorization header and is never printed or written to a file.

Usage:
  python tools/dialogue_library_curate.py list [--status active] [--general ID] [--key K] [--limit 50] [--full]

  # preview lines matching rules (rules are OR-ed, scope filters are AND-ed); writes a review file
  python tools/dialogue_library_curate.py flag --text-contains 本王 --too-short 6 --no-end-punct \
      --key-mismatch --duplicate-similar 0.85 [--general ID] [--key K]

  # delete = soft delete (status -> retired). Always previews first with --dry-run.
  python tools/dialogue_library_curate.py delete --text-contains 本王 --dry-run
  python tools/dialogue_library_curate.py delete --text-contains 本王 --yes     # backs up first, then deletes

  # refill buckets that lost lines by calling the online /v1/npc/dialogue (max N calls, default 5)
  python tools/dialogue_library_curate.py regenerate --from-deleted DELETED.json [--max 5] [--dry-run]
  python tools/dialogue_library_curate.py regenerate --key K --max 2

Environment:
  NPC_BRAIN_URL     service base URL (default https://threeklife-npc-brain.onrender.com)
  NPC_ADMIN_TOKEN   admin bearer token (list / flag / delete)
"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import backup_dialogue_library as backup_tool  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
DELETE_PATH = "/v1/admin/dialogue-library/delete"
DIALOGUE_PATH = "/v1/npc/dialogue"
DEFAULT_MAX_REGEN = 5
MAX_LINES_PER_KEY = 5  # keep in sync with app/dialogue_library.MAX_LINES_PER_KEY
END_PUNCT = "。！？!?…」』）)”\"~～"
AdminRequestError = backup_tool.AdminRequestError


def read_env_file_value(name: str, env_file: Path | None = None) -> str | None:
    path = env_file or REPO_ROOT / ".env"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        key, sep, value = line.partition("=")
        if sep and key.strip() == name:
            return value.strip().strip("'\"") or None
    return None


def resolve_token(env_file: Path | None = None) -> str:
    token = (os.environ.get(backup_tool.TOKEN_ENV) or "").strip() or read_env_file_value(backup_tool.TOKEN_ENV, env_file)
    if not token:
        raise AdminRequestError(f"{backup_tool.TOKEN_ENV} is not set in the environment or in the repo .env")
    return token


# ---------------------------------------------------------------- selection


def cache_key_of(bucket: dict[str, Any]) -> str:
    """Same recipe as app.dialogue_library.compute_cache_key, kept local so the tool has no app import."""
    canonical = json.dumps(bucket, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]


def in_scope(line: dict[str, Any], args: argparse.Namespace) -> bool:
    status = getattr(args, "status", "active")
    if status != "all" and line.get("status") != status:
        return False
    keys = getattr(args, "key", None)
    if keys and line.get("key") not in keys:
        return False
    generals = getattr(args, "general", None)
    if generals and line.get("generalId") not in generals:
        return False
    return True


def has_rules(args: argparse.Namespace) -> bool:
    return bool(
        getattr(args, "text_contains", None)
        or getattr(args, "too_short", None)
        or getattr(args, "no_end_punct", False)
        or getattr(args, "key_mismatch", False)
        or getattr(args, "duplicate_similar", None)
    )


def find_duplicates(lines: list[dict[str, Any]], threshold: float) -> dict[int, str]:
    """Within one key, flag the weaker of two similar lines. Keeps higher qualityScore, then the earlier createdAt."""
    flagged: dict[int, str] = {}
    by_key: dict[str, list[int]] = {}
    for index, line in enumerate(lines):
        by_key.setdefault(str(line.get("key")), []).append(index)

    def rank(i: int) -> tuple[float, str]:
        score = lines[i].get("qualityScore")
        return (-1.0 if score is None else float(score), "~" + str(lines[i].get("createdAt") or ""))

    for indexes in by_key.values():
        ordered = sorted(indexes, key=rank, reverse=True)  # best first
        kept: list[int] = []
        for i in ordered:
            text = str(lines[i].get("textZhTw") or "")
            twin = next(
                (j for j in kept if difflib.SequenceMatcher(None, text, str(lines[j].get("textZhTw") or "")).ratio() >= threshold),
                None,
            )
            if twin is None:
                kept.append(i)
            else:
                flagged[i] = f"similar>={threshold:g} to another line in the same key"
    return flagged


def select_lines(lines: list[dict[str, Any]], args: argparse.Namespace) -> list[tuple[dict[str, Any], list[str]]]:
    """Return (line, reasons). Scope filters AND together; rules OR together. No rules means the whole scope."""
    scoped = [line for line in lines if in_scope(line, args)]
    if not has_rules(args):
        return [(line, ["in scope"]) for line in scoped]
    duplicates = (
        find_duplicates(scoped, args.duplicate_similar) if getattr(args, "duplicate_similar", None) else {}
    )
    picked: list[tuple[dict[str, Any], list[str]]] = []
    for index, line in enumerate(scoped):
        text = str(line.get("textZhTw") or "")
        reasons: list[str] = []
        for word in getattr(args, "text_contains", None) or []:
            if word and word in text:
                reasons.append(f"contains {word!r}")
        too_short = getattr(args, "too_short", None)
        if too_short and len(text.strip()) < too_short:
            reasons.append(f"shorter than {too_short} chars")
        if getattr(args, "no_end_punct", False) and text.strip() and text.strip()[-1] not in END_PUNCT:
            reasons.append("no ending punctuation")
        if getattr(args, "key_mismatch", False):
            bucket = line.get("bucket") or {}
            if not bucket or cache_key_of(bucket) != line.get("key"):
                reasons.append("key does not match its bucket")
        if index in duplicates:
            reasons.append(duplicates[index])
        if reasons:
            picked.append((line, reasons))
    return picked


# ---------------------------------------------------------------- remote calls


def post_delete(
    base_url: str,
    token: str,
    targets: list[dict[str, str]],
    *,
    dry_run: bool = False,
    opener: backup_tool.Opener = urllib.request.urlopen,
) -> dict[str, Any]:
    backup_tool._check_transport(base_url)
    body = json.dumps({"targets": targets, "dryRun": bool(dry_run)}, ensure_ascii=False).encode("utf-8")
    return backup_tool._request("POST", base_url + DELETE_PATH, token, body, opener)


def request_dialogue(
    base_url: str, payload: dict[str, Any], opener: backup_tool.Opener = urllib.request.urlopen
) -> dict[str, Any]:
    backup_tool._check_transport(base_url)
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        base_url + DIALOGUE_PATH,
        data=body,
        method="POST",
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    try:
        with opener(request, timeout=backup_tool.TIMEOUT_SECONDS) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise AdminRequestError(f"HTTP {exc.code} from {DIALOGUE_PATH}") from exc
    except urllib.error.URLError as exc:
        raise AdminRequestError(f"cannot reach service: {exc.reason}") from exc
    except ValueError as exc:
        raise AdminRequestError("service returned non-JSON response") from exc


def bucket_to_request(bucket: dict[str, Any], extra: dict[str, Any] | None, max_chars: int) -> dict[str, Any]:
    """Rebuild a v2 dialogue request that lands in the same cache key as `bucket`."""
    personality = bucket.get("personality")
    payload: dict[str, Any] = {
        "generalId": bucket.get("generalId"),
        "schemaVersion": 2,
        "locale": "zh-TW",
        "maxChars": max_chars,
        "personality": None if personality in (None, "unset") else personality,
        "mood": bucket.get("mood"),
        "relationBucket": bucket.get("relationBucket"),
        "worldPhase": bucket.get("worldPhase"),
        "interaction": {"type": bucket.get("interactionType")},
        "scene": bucket.get("scene"),
        "extra": extra or {},
    }
    context_key = (extra or {}).get("contextKey")
    if isinstance(context_key, str) and context_key:
        payload["contextKey"] = context_key
    return payload


def plan_regeneration(
    entries: list[dict[str, Any]], current_lines: list[dict[str, Any]], max_calls: int
) -> list[dict[str, Any]]:
    """One planned call per missing slot, per key, until max_calls is used. entries: {key,bucket,extra,count}."""
    active: dict[str, int] = {}
    for line in current_lines:
        if line.get("status") == "active":
            active[str(line.get("key"))] = active.get(str(line.get("key")), 0) + 1
    plan: list[dict[str, Any]] = []
    for entry in entries:
        need = min(int(entry.get("count", 1)), MAX_LINES_PER_KEY - active.get(entry["key"], 0))
        for _ in range(max(need, 0)):
            if len(plan) >= max_calls:
                return plan
            plan.append(entry)
    return plan


# ---------------------------------------------------------------- output helpers


def clip(text: str, width: int) -> str:
    text = text.replace("\n", " ")
    return text if len(text) <= width else text[: width - 1] + "…"


def print_lines(rows: list[tuple[dict[str, Any], list[str]]], *, full: bool, limit: int) -> None:
    width = 10_000 if full else 36
    for line, reasons in rows[: max(limit, 0)]:
        bucket = line.get("bucket") or {}
        context = "/".join(str(bucket.get(name, "?")) for name in ("interactionType", "scene", "mood"))
        score = line.get("qualityScore")
        print(
            f"{str(line.get('key'))[:10]}  {clip(str(line.get('generalId')), 14):14}  {context:28}  "
            f"{clip(str(line.get('textZhTw')), width)}  q={'-' if score is None else score}  {line.get('status')}"
            + (f"  <- {'; '.join(reasons)}" if reasons and reasons != ["in scope"] else "")
        )
    if len(rows) > limit:
        print(f"... {len(rows) - limit} more (raise --limit)")


def stamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%d-%H%M%S")


def write_json(path: Path, data: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


# ---------------------------------------------------------------- commands


def cmd_list(args: argparse.Namespace, base_url: str, token: str, opener) -> int:
    payload = backup_tool.fetch_export(base_url, token, opener)
    lines = payload.get("lines", [])
    rows = [(line, []) for line in lines if in_scope(line, args)]
    by_status: dict[str, int] = {}
    for line in lines:
        by_status[line.get("status", "?")] = by_status.get(line.get("status", "?"), 0) + 1
    print(f"library: {len(lines)} lines {by_status}; shown scope: {len(rows)}")
    print_lines(rows, full=args.full, limit=args.limit)
    return 0


def cmd_flag(args: argparse.Namespace, base_url: str, token: str, opener) -> int:
    if not has_rules(args):
        raise AdminRequestError("flag needs at least one rule (--text-contains, --too-short, ...)")
    payload = backup_tool.fetch_export(base_url, token, opener)
    picked = select_lines(payload.get("lines", []), args)
    print(f"flagged {len(picked)} of {len(payload.get('lines', []))} lines")
    print_lines(picked, full=args.full, limit=args.limit)
    if picked:
        report = write_json(
            args.out_dir / f"flagged-{stamp()}.json",
            {"createdAt": datetime.now(UTC).isoformat(), "lines": [dict(line, reasons=reasons) for line, reasons in picked]},
        )
        print(f"review file: {report}  (use: delete --from-flagged {report})")
    return 0


def _selected_for_delete(args: argparse.Namespace, lines: list[dict[str, Any]]) -> list[tuple[dict[str, Any], list[str]]]:
    if args.from_flagged:
        flagged = json.loads(Path(args.from_flagged).read_text(encoding="utf-8")).get("lines", [])
        wanted = {(item["key"], item["textZhTw"]) for item in flagged}
        return [
            (line, ["from flagged file"])
            for line in lines
            if line.get("status") == "active" and (line.get("key"), line.get("textZhTw")) in wanted
        ]
    if not has_rules(args) and not (args.key or args.general):
        raise AdminRequestError("refusing to delete without a rule, --key, --general or --from-flagged")
    return select_lines(lines, args)


def cmd_delete(args: argparse.Namespace, base_url: str, token: str, opener) -> int:
    payload = backup_tool.fetch_export(base_url, token, opener)
    picked = _selected_for_delete(args, payload.get("lines", []))
    print(f"{'would delete' if args.dry_run else 'selected'} {len(picked)} active lines (soft delete -> retired)")
    print_lines(picked, full=args.full, limit=args.limit)
    if args.dry_run or not picked:
        if args.dry_run:
            print("dry-run: nothing was backed up or deleted")
        return 0
    if not args.yes:
        raise AdminRequestError("real deletion needs --yes (use --dry-run to preview)")
    target = args.out_dir / f"dialogue-library-{stamp()}.json"
    write_json(target, payload)
    print(f"backup ok: {len(payload.get('lines', []))} lines -> {target}")
    targets = [{"key": line["key"], "textZhTw": line["textZhTw"]} for line, _ in picked]
    result = post_delete(base_url, token, targets, opener=opener)
    deleted_file = write_json(
        args.out_dir / f"deleted-{stamp()}.json",
        {
            "createdAt": datetime.now(UTC).isoformat(),
            "backupFile": str(target),
            "lines": [
                {"key": line["key"], "bucket": line.get("bucket"), "extra": line.get("extra"), "textZhTw": line["textZhTw"]}
                for line, _ in picked
            ],
        },
    )
    print(f"deleted: {result.get('retired')} lines; refill with: regenerate --from-deleted {deleted_file}")
    return 0


def _entries_from_deleted(path: Path) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for line in json.loads(path.read_text(encoding="utf-8")).get("lines", []):
        entry = grouped.setdefault(
            line["key"], {"key": line["key"], "bucket": line.get("bucket") or {}, "extra": line.get("extra") or {}, "count": 0}
        )
        entry["count"] += 1
    return list(grouped.values())


def cmd_regenerate(args: argparse.Namespace, base_url: str, token: str, opener) -> int:
    payload = backup_tool.fetch_export(base_url, token, opener)
    lines = payload.get("lines", [])
    if args.from_deleted:
        entries = _entries_from_deleted(Path(args.from_deleted))
    elif args.key:
        entries = []
        for key in args.key:
            sample = next((line for line in lines if line.get("key") == key), None)
            if sample is None:
                raise AdminRequestError(f"key {key} not found in the library")
            entries.append({"key": key, "bucket": sample.get("bucket") or {}, "extra": sample.get("extra") or {}, "count": MAX_LINES_PER_KEY})
    else:
        raise AdminRequestError("regenerate needs --from-deleted or --key")
    plan = plan_regeneration(entries, lines, args.max)
    print(f"plan: {len(plan)} generation call(s) (cap --max {args.max}) across {len({e['key'] for e in plan})} bucket(s)")
    if args.dry_run:
        for entry in plan:
            print(f"  {entry['key'][:10]}  {entry['bucket'].get('generalId')}  {entry['bucket'].get('interactionType')}/{entry['bucket'].get('scene')}/{entry['bucket'].get('mood')}")
        print("dry-run: no generation calls made")
        return 0
    new_lines = 0
    for entry in plan:
        request = bucket_to_request(entry["bucket"], entry["extra"], args.max_chars)
        reply = request_dialogue(base_url, request, opener)
        stored = reply.get("provider") == "anthropic" and not reply.get("fallbackUsed") and not reply.get("qualityWarnings")
        new_lines += 1 if stored else 0
        note = "stored" if stored else f"NOT stored (provider={reply.get('provider')}, warnings={reply.get('qualityWarnings')})"
        print(f"  {entry['key'][:10]}  {reply.get('text')}  [{note}]")
    print(f"done: {new_lines}/{len(plan)} new line(s) stored")
    return 0


# ---------------------------------------------------------------- cli


def add_scope_args(parser: argparse.ArgumentParser, *, status_default: str) -> None:
    parser.add_argument("--status", choices=["active", "retired", "all"], default=status_default)
    parser.add_argument("--key", action="append", help="cache key (repeatable)")
    parser.add_argument("--general", action="append", help="generalId (repeatable)")
    parser.add_argument("--limit", type=int, default=50, help="max rows printed")
    parser.add_argument("--full", action="store_true", help="do not truncate text")


def add_rule_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--text-contains", action="append", metavar="WORD", help="text contains WORD (repeatable)")
    parser.add_argument("--too-short", type=int, metavar="N", help="text shorter than N characters")
    parser.add_argument("--no-end-punct", action="store_true", help="text lacks ending punctuation")
    parser.add_argument("--key-mismatch", action="store_true", help="key does not equal the hash of its own bucket")
    parser.add_argument("--duplicate-similar", type=float, metavar="RATIO", help="near-duplicate within a key, e.g. 0.85")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", default=None, help=f"service base URL (env {backup_tool.URL_ENV})")
    parser.add_argument("--out-dir", type=Path, default=backup_tool.DEFAULT_OUT_DIR)
    sub = parser.add_subparsers(dest="command", required=True)

    add_scope_args(sub.add_parser("list", help="print a summary of the library"), status_default="active")

    flag = sub.add_parser("flag", help="preview lines matching rules and write a review file")
    add_scope_args(flag, status_default="active")
    add_rule_args(flag)

    delete = sub.add_parser("delete", help="soft-delete matching lines (backup first)")
    add_scope_args(delete, status_default="active")
    add_rule_args(delete)
    delete.add_argument("--from-flagged", help="flagged-*.json produced by the flag command")
    delete.add_argument("--dry-run", action="store_true", help="preview only: no backup, no deletion")
    delete.add_argument("--yes", action="store_true", help="confirm the real deletion")

    regen = sub.add_parser("regenerate", help="refill buckets through the online dialogue endpoint")
    regen.add_argument("--from-deleted", help="deleted-*.json produced by the delete command")
    regen.add_argument("--key", action="append", help="refill this cache key up to 5 lines (repeatable)")
    regen.add_argument("--max", type=int, default=DEFAULT_MAX_REGEN, help="max generation calls (default 5)")
    regen.add_argument("--max-chars", type=int, default=80)
    regen.add_argument("--dry-run", action="store_true", help="show the plan only")
    return parser


def main(argv: list[str] | None = None, opener: backup_tool.Opener = urllib.request.urlopen) -> int:
    args = build_parser().parse_args(argv)
    try:
        base_url = backup_tool.resolve_base_url(args.url)
        token = resolve_token()
        handler = {"list": cmd_list, "flag": cmd_flag, "delete": cmd_delete, "regenerate": cmd_regenerate}[args.command]
        return handler(args, base_url, token, opener)
    except (AdminRequestError, OSError, ValueError, KeyError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
