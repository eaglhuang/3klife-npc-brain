from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Callable

SeedRowMerger = Callable[[list[Path]], list[dict[str, Any]]]
AllowlistBuilder = Callable[..., tuple[list[str], str]]
RepoRelativeFn = Callable[[Path], str]
CommandRunner = Callable[..., dict[str, Any]]
JsonlWriter = Callable[[Path, list[dict[str, Any]]], int]


def run_global_seed_pipeline_atom(
    *,
    round_root: Path,
    round_id: str,
    scoreboard_path: Path | None,
    seed_paths: list[Path],
    seed_to_card_priority_limit: int,
    seed_to_card_priority_extra_ids: list[str] | None,
    seed_to_card_min_score: float,
    dry_run: bool,
    overwrite: bool,
    repo_root: Path,
    pipeline_root: Path,
    merge_seed_rows_fn: SeedRowMerger,
    build_seed_to_card_priority_allowlist_fn: AllowlistBuilder,
    repo_relative_fn: RepoRelativeFn,
    run_command_fn: CommandRunner,
    write_jsonl_fn: JsonlWriter,
) -> dict[str, Any]:
    _ = round_id
    pipeline_run_root = round_root / "external-evidence" / "global-standard-pipeline"
    merged_seed_path = pipeline_run_root / "merged-manual-evidence-seeds.jsonl"
    harvested_seed_path = pipeline_run_root / "external-evidence-seeds.jsonl"
    ranking_path = pipeline_run_root / "external-evidence-seed-ranking.json"
    candidate_cards_path = pipeline_run_root / "candidate-evidence-cards.jsonl"
    candidate_summary_path = pipeline_run_root / "candidate-evidence-card-summary.json"
    allowlist_path = pipeline_run_root / "seed-to-card-priority-person-allowlist.json"

    if not scoreboard_path or not scoreboard_path.exists():
        return _disabled_pipeline_result(
            reason="missing-scoreboard-json",
            seed_paths=seed_paths,
            seed_input_count=len(seed_paths),
            seed_to_card_priority_limit=seed_to_card_priority_limit,
            seed_to_card_min_score=seed_to_card_min_score,
        )

    seed_rows = merge_seed_rows_fn(seed_paths)
    if not seed_rows:
        return _disabled_pipeline_result(
            reason="no-seed-input",
            seed_paths=seed_paths,
            seed_input_count=0,
            seed_to_card_priority_limit=seed_to_card_priority_limit,
            seed_to_card_min_score=seed_to_card_min_score,
        )

    write_jsonl_fn(merged_seed_path, seed_rows)

    allowlist_ids, allowlist_reason = build_seed_to_card_priority_allowlist_fn(
        scoreboard_path=scoreboard_path,
        limit=int(seed_to_card_priority_limit),
        output_path=allowlist_path,
        extra_person_ids=seed_to_card_priority_extra_ids,
    )

    harvest_command = [
        sys.executable,
        str((repo_root / pipeline_root / "harvest_external_evidence_seeds.py").resolve()),
        "--no-default-external-evidence-cards",
        "--manual-seeds-jsonl",
        repo_relative_fn(merged_seed_path),
        "--scoreboard-json",
        repo_relative_fn(scoreboard_path),
        "--output-root",
        repo_relative_fn(pipeline_run_root),
    ]
    if overwrite:
        harvest_command.append("--overwrite")
    harvest_result = run_command_fn(harvest_command, dry_run=dry_run)

    score_command = [
        sys.executable,
        str((repo_root / pipeline_root / "score_external_evidence_seeds.py").resolve()),
        "--seeds-jsonl",
        repo_relative_fn(harvested_seed_path),
        "--output-root",
        repo_relative_fn(pipeline_run_root),
    ]
    if overwrite:
        score_command.append("--overwrite")
    score_result = run_command_fn(score_command, dry_run=dry_run)

    promote_command = [
        sys.executable,
        str((repo_root / pipeline_root / "promote_seed_to_evidence_card.py").resolve()),
        "--ranking-json",
        repo_relative_fn(ranking_path),
        "--output-root",
        repo_relative_fn(pipeline_run_root),
        "--min-score",
        str(float(seed_to_card_min_score)),
    ]
    if allowlist_ids:
        promote_command.extend(["--person-allowlist-json", repo_relative_fn(allowlist_path)])
    if overwrite:
        promote_command.append("--overwrite")
    promote_result = run_command_fn(promote_command, dry_run=dry_run)

    return {
        "enabled": True,
        "reason": None,
        "seedInputCount": len(seed_rows),
        "seedInputPaths": [repo_relative_fn(path) for path in seed_paths],
        "mergedSeedPath": repo_relative_fn(merged_seed_path),
        "harvestedSeedPath": repo_relative_fn(harvested_seed_path),
        "rankingPath": repo_relative_fn(ranking_path),
        "candidateCardsPath": repo_relative_fn(candidate_cards_path),
        "candidateSummaryPath": repo_relative_fn(candidate_summary_path),
        "harvestCommand": harvest_result,
        "scoreCommand": score_result,
        "promoteCommand": promote_result,
        "seedToCardPriorityLimit": int(seed_to_card_priority_limit),
        "seedToCardMinScore": float(seed_to_card_min_score),
        "seedToCardPrioritySelectedCount": len(allowlist_ids),
        "seedToCardPriorityAllowlistPath": repo_relative_fn(allowlist_path) if allowlist_ids else None,
        "seedToCardPriorityReason": allowlist_reason,
    }


def _disabled_pipeline_result(
    *,
    reason: str,
    seed_paths: list[Path],
    seed_input_count: int,
    seed_to_card_priority_limit: int,
    seed_to_card_min_score: float,
) -> dict[str, Any]:
    return {
        "enabled": False,
        "reason": reason,
        "seedInputCount": seed_input_count,
        "seedInputPaths": [str(path).replace("\\", "/") for path in seed_paths],
        "mergedSeedPath": None,
        "harvestedSeedPath": None,
        "rankingPath": None,
        "candidateCardsPath": None,
        "candidateSummaryPath": None,
        "harvestCommand": None,
        "scoreCommand": None,
        "promoteCommand": None,
        "seedToCardPriorityLimit": int(seed_to_card_priority_limit),
        "seedToCardMinScore": float(seed_to_card_min_score),
        "seedToCardPrioritySelectedCount": 0,
        "seedToCardPriorityAllowlistPath": None,
        "seedToCardPriorityReason": reason,
    }
