"""One-shot evidence-backend smoke suite (SANGUO-RAGOPS post-0501).

Reads ``evidence-backend-smoke-commands.json`` and executes the commands
in declared order. Emits a single status report so CI can call one
script.

Default scope is the seven new evidence-backend smoke tests under
``pipelines/sanguo-rag/*_smoke_test.py``. The legacy governance group
(docker-exec entries) is skipped by default because it requires the
3klife-npc-brain-dev container; pass ``--include-group legacy-governance``
to include them.

Usage::

    python -B pipelines/sanguo-rag/run_evidence_backend_smoke_suite.py
    python -B pipelines/sanguo-rag/run_evidence_backend_smoke_suite.py \
        --output local/cutover-evidence/smoke-suite-report.json
"""

from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
DEFAULT_COMMANDS_PATH = ROOT / "evidence-backend-smoke-commands.json"
SUITE_SCHEMA_VERSION = "evidence-backend-smoke-suite-report.v0.1"

DEFAULT_INCLUDED_GROUPS = (
    "manifest",
    "repository",
    "backfill-parity",
    "vector",
    "rehearsal",
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the evidence-backend smoke suite (SANGUO-RAGOPS).")
    parser.add_argument(
        "--commands-path",
        default=str(DEFAULT_COMMANDS_PATH),
        help="evidence-backend-smoke-commands.json path",
    )
    parser.add_argument(
        "--include-group",
        action="append",
        default=[],
        help="extra group id to include (e.g. legacy-governance); repeatable",
    )
    parser.add_argument(
        "--exclude-group",
        action="append",
        default=[],
        help="group id to skip; repeatable",
    )
    parser.add_argument("--output", default="", help="optional output JSON path")
    parser.add_argument("--fail-fast", action="store_true", help="abort on first non-zero exit code")
    return parser.parse_args()


def _resolve_groups(commands: dict[str, Any], include: list[str], exclude: list[str]) -> list[dict[str, Any]]:
    groups: list[dict[str, Any]] = commands.get("groups") or []
    include_set = set(DEFAULT_INCLUDED_GROUPS) | set(include)
    exclude_set = set(exclude)
    return [g for g in groups if g["id"] in include_set and g["id"] not in exclude_set]


def _run_command(spec: dict[str, Any]) -> dict[str, Any]:
    cmd_text = str(spec["command"])
    expected = int(spec.get("expectExitCode", 0))
    started = time.monotonic()
    try:
        completed = subprocess.run(
            shlex.split(cmd_text),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            check=False,
        )
        exit_code = completed.returncode
        tail = "\n".join(completed.stdout.splitlines()[-8:]) if completed.stdout else ""
    except FileNotFoundError as exc:
        return {
            "id": spec["id"],
            "command": cmd_text,
            "expectExitCode": expected,
            "exitCode": -1,
            "ok": False,
            "elapsedSeconds": round(time.monotonic() - started, 3),
            "tail": f"FileNotFoundError: {exc}",
        }
    return {
        "id": spec["id"],
        "command": cmd_text,
        "expectExitCode": expected,
        "exitCode": exit_code,
        "ok": exit_code == expected,
        "elapsedSeconds": round(time.monotonic() - started, 3),
        "tail": tail,
    }


def main() -> int:
    args = parse_args()
    commands = json.loads(Path(args.commands_path).read_text(encoding="utf-8"))
    groups = _resolve_groups(commands, args.include_group, args.exclude_group)

    report: dict[str, Any] = {
        "schemaVersion": SUITE_SCHEMA_VERSION,
        "generatedAt": _utc_now(),
        "commandsPath": str(args.commands_path),
        "includedGroups": [g["id"] for g in groups],
        "results": [],
        "groupStatus": {},
        "okCount": 0,
        "failCount": 0,
    }

    overall_ok = True
    for group in groups:
        group_id = group["id"]
        group_results: list[dict[str, Any]] = []
        for spec in group.get("commands", []):
            result = _run_command(spec)
            result["group"] = group_id
            group_results.append(result)
            report["results"].append(result)
            if result["ok"]:
                report["okCount"] += 1
            else:
                report["failCount"] += 1
                overall_ok = False
                if args.fail_fast:
                    break
        report["groupStatus"][group_id] = {
            "ok": all(r["ok"] for r in group_results),
            "okCount": sum(1 for r in group_results if r["ok"]),
            "failCount": sum(1 for r in group_results if not r["ok"]),
        }
        if not overall_ok and args.fail_fast:
            break

    report["ok"] = overall_ok

    text = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
        print(f"[evidence-backend-smoke-suite] wrote {out}; ok={overall_ok} pass={report['okCount']} fail={report['failCount']}")
    else:
        print(text, end="")

    return 0 if overall_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
