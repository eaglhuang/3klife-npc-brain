from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from repo_layout import resolve_repo_root


REPO_ROOT = resolve_repo_root(__file__)
DEFAULT_JOBS_PATH = REPO_ROOT / "artifacts/data-pipeline/sanguo-rag/extracted/baihua-bootstrap/wave-001/top50-bootstrap-jobs.jsonl"
DEFAULT_PASSAGES_PATH = REPO_ROOT / "artifacts/data-pipeline/sanguo-rag/anchor-index/sanguoyanyi-baihua-zh-tw-passages.jsonl"
DEFAULT_OUTPUT_ROOT = REPO_ROOT / "artifacts/data-pipeline/sanguo-rag/extracted/baihua-bootstrap/wave-001"
LOCATOR_CHAPTER_PATTERN = re.compile(r"chapter-(\d+)", re.IGNORECASE)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build focusGeneralId -> baihua passage bundles for bootstrap jobs.")
    parser.add_argument("--jobs-path", default=str(DEFAULT_JOBS_PATH))
    parser.add_argument("--passages-path", default=str(DEFAULT_PASSAGES_PATH))
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    parser.add_argument("--bundle-jsonl-file-name", default="top50-passage-bundles.jsonl")
    parser.add_argument("--bundle-index-file-name", default="focus-general-to-bundle.json")
    parser.add_argument("--focus-dir-name", default="focus-bundles")
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


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


def chapter_ref(locator: str) -> str:
    match = LOCATOR_CHAPTER_PATTERN.search(locator or "")
    if not match:
        return ""
    chapter_no = int(match.group(1))
    return f"第{chapter_no:03d}回"


def passage_index(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for row in rows:
        locator = str(row.get("locator") or "").strip()
        if locator:
            index[locator] = row
    return index


def unique_strings(values: list[str]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        output.append(text)
    return output


def main() -> int:
    args = parse_args()
    jobs_path = Path(args.jobs_path).resolve()
    passages_path = Path(args.passages_path).resolve()
    output_root = Path(args.output_root).resolve()
    bundle_jsonl_path = output_root / args.bundle_jsonl_file_name
    bundle_index_path = output_root / args.bundle_index_file_name
    focus_root = output_root / args.focus_dir_name

    if not args.overwrite and (bundle_jsonl_path.exists() or bundle_index_path.exists()):
        raise FileExistsError(f"Output exists. Re-run with --overwrite: {bundle_jsonl_path}")

    jobs = read_jsonl(jobs_path)
    passages = read_jsonl(passages_path)
    by_locator = passage_index(passages)

    bundle_rows: list[dict[str, Any]] = []
    focus_index: dict[str, dict[str, Any]] = {}
    missing_refs: list[str] = []
    empty_focus: list[str] = []
    total_passage_count = 0
    counterpart_hit_counter: Counter[str] = Counter()

    for job in jobs:
        focus_id = str(job.get("focusGeneralId") or "").strip()
        if not focus_id:
            continue
        focus_name = str(job.get("focusNameZhTw") or focus_id).strip()
        job_id = str(job.get("jobId") or "").strip()
        source_corpus_id = str(job.get("sourceCorpusId") or "").strip()
        candidate_counterparts = unique_strings([str(item) for item in job.get("candidateCounterpartIds") or []])
        counterpart_set = set(candidate_counterparts)
        refs = unique_strings([str(item) for item in job.get("passageRefs") or []])

        selected_passages: list[dict[str, Any]] = []
        for locator in refs:
            source = by_locator.get(locator)
            if source is None:
                missing_refs.append(locator)
                continue
            person_ids = unique_strings([str(item) for item in source.get("personIds") or []])
            counterpart_hits = sorted(counterpart_set.intersection(person_ids))
            for hit in counterpart_hits:
                counterpart_hit_counter[hit] += 1
            selected_passages.append(
                {
                    "locator": locator,
                    "chapterRef": chapter_ref(locator),
                    "normalizedText": str(source.get("normalizedText") or "").strip(),
                    "charCount": int(source.get("charCount") or 0),
                    "sourcePath": str(source.get("sourcePath") or "").strip(),
                    "personIds": person_ids,
                    "counterpartHits": counterpart_hits,
                }
            )

        selected_passages.sort(key=lambda row: (row.get("chapterRef") or "", row.get("locator") or ""))
        if not selected_passages:
            empty_focus.append(focus_id)
        total_passage_count += len(selected_passages)
        chapter_values = sorted({str(row.get("chapterRef") or "") for row in selected_passages if str(row.get("chapterRef") or "").strip()})
        bundle_id = f"baihua-passage-bundle:{job.get('waveId') or 'wave'}:{focus_id}"
        focus_bundle_path = (focus_root / f"{focus_id}.bundle.json").resolve()

        bundle = {
            "bundleId": bundle_id,
            "jobId": job_id,
            "focusGeneralId": focus_id,
            "focusNameZhTw": focus_name,
            "waveId": str(job.get("waveId") or "").strip(),
            "sourceCorpusId": source_corpus_id,
            "candidateCounterpartIds": candidate_counterparts,
            "passageCount": len(selected_passages),
            "chapterRefs": chapter_values,
            "passages": selected_passages,
            "canonicalWrites": False,
        }
        write_json(focus_bundle_path, bundle)
        bundle_rows.append(
            {
                "bundleId": bundle_id,
                "jobId": job_id,
                "focusGeneralId": focus_id,
                "focusNameZhTw": focus_name,
                "sourceCorpusId": source_corpus_id,
                "bundlePath": str(focus_bundle_path),
                "passageCount": len(selected_passages),
                "chapterRefs": chapter_values,
                "canonicalWrites": False,
            }
        )
        focus_index[focus_id] = {
            "focusNameZhTw": focus_name,
            "bundlePath": str(focus_bundle_path),
            "passageCount": len(selected_passages),
        }

    write_jsonl(bundle_jsonl_path, bundle_rows)
    summary = {
        "mode": "baihua-passage-bundler",
        "generatedAt": utc_now(),
        "canonicalWrites": False,
        "inputs": {
            "jobsPath": str(jobs_path),
            "passagesPath": str(passages_path),
        },
        "outputs": {
            "bundleJsonlPath": str(bundle_jsonl_path),
            "bundleIndexPath": str(bundle_index_path),
            "focusBundleRoot": str(focus_root),
            "bundleCount": len(bundle_rows),
            "totalPassageCount": total_passage_count,
            "emptyFocusCount": len(empty_focus),
            "emptyFocusIds": sorted(empty_focus),
            "missingRefCount": len(missing_refs),
            "topCounterpartHitIds": [item[0] for item in counterpart_hit_counter.most_common(20)],
        },
        "focusGeneralToBundle": focus_index,
    }
    write_json(bundle_index_path, summary)
    print(f"[build_baihua_passage_bundles] wrote {bundle_jsonl_path}")
    print(f"[build_baihua_passage_bundles] wrote {bundle_index_path}")
    print(f"[build_baihua_passage_bundles] bundles={len(bundle_rows)} totalPassages={total_passage_count} emptyFocus={len(empty_focus)} canonicalWrites=false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
