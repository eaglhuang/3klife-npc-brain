"""
Anchor Passage Index Builder — SANGUO-AUTO-0202
將 anchor corpus 切成可重建的 passage，產生穩定 locator 與 textHash。
每個 passage 有 corpusId、sourceFamily、layer、locator、textHash、normalizedText。
相同輸入產生相同 hash（確定性）。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from anchor_corpus_registry import load_anchor_corpus_registry
from repo_layout import resolve_repo_root

REPO_ROOT = resolve_repo_root(__file__)

DEFAULT_REGISTRY_PATH = REPO_ROOT / "pipelines/sanguo-rag/config/anchor-corpus-registry.json"
DEFAULT_OUTPUT_ROOT = Path("artifacts/data-pipeline/sanguo-rag/anchor-index")
PASSAGE_INDEX_SCHEMA = "anchor.passage.index.v0.1"
MIN_PASSAGE_CHARS = 20
MAX_PASSAGE_CHARS = 2000


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def resolve_path(p: str | Path) -> Path:
    path = Path(p)
    return path if path.is_absolute() else (REPO_ROOT / path).resolve()


def normalize_text(raw: str) -> str:
    text = re.sub(r"\s+", " ", raw.strip())
    text = text.replace("　", " ")
    return text


def stable_text_hash(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def split_into_passages(text: str, max_chars: int = MAX_PASSAGE_CHARS) -> list[str]:
    """以段落為單位切分，段落太長時再以句號切分。"""
    paragraphs = [p.strip() for p in re.split(r"\n{2,}", text) if p.strip()]
    passages: list[str] = []
    for para in paragraphs:
        if len(para) <= max_chars:
            passages.append(para)
        else:
            sentences = re.split(r"(?<=[。！？])", para)
            chunk = ""
            for sent in sentences:
                if len(chunk) + len(sent) > max_chars and chunk:
                    passages.append(chunk.strip())
                    chunk = sent
                else:
                    chunk += sent
            if chunk.strip():
                passages.append(chunk.strip())
    return [p for p in passages if len(p) >= MIN_PASSAGE_CHARS]


def build_passage_record(
    corpus: dict[str, Any],
    chapter_id: str,
    para_idx: int,
    raw_text: str,
) -> dict[str, Any]:
    normalized = normalize_text(raw_text)
    locator = f"{corpus['locatorPrefix']}#{chapter_id}#p{para_idx}"
    return {
        "corpusId": corpus["corpusId"],
        "sourceFamily": corpus["sourceFamily"],
        "layer": corpus["layer"],
        "trustTier": corpus["trustTier"],
        "locator": locator,
        "textHash": stable_text_hash(normalized),
        "normalizedText": normalized,
        "charCount": len(normalized),
    }


def index_corpus_directory(
    corpus: dict[str, Any],
    corpus_dir: Path,
) -> Iterator[dict[str, Any]]:
    """讀取 corpus 目錄下的文本文件，產生 passage records。"""
    if not corpus_dir.exists():
        return
    for text_file in sorted(corpus_dir.glob("*.txt")):
        chapter_id = text_file.stem
        raw_text = text_file.read_text(encoding="utf-8")
        passages = split_into_passages(raw_text)
        for para_idx, passage in enumerate(passages):
            yield build_passage_record(corpus, chapter_id, para_idx, passage)


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def build_anchor_index(
    registry_path: str | Path | None = None,
    corpus_roots: dict[str, str] | None = None,
    output_root: str | Path = DEFAULT_OUTPUT_ROOT,
) -> dict[str, Any]:
    registry = load_anchor_corpus_registry(registry_path)
    out_root = resolve_path(output_root)
    corpus_roots = corpus_roots or {}

    stats: list[dict[str, Any]] = []
    total_passages = 0

    for corpus in registry.get("corpora", []):
        corpus_id = corpus["corpusId"]
        corpus_dir_str = corpus_roots.get(corpus_id)
        if not corpus_dir_str:
            print(f"[SKIP] {corpus_id}: no corpus_root provided, skipping.")
            stats.append({"corpusId": corpus_id, "passageCount": 0, "status": "skipped"})
            continue

        corpus_dir = resolve_path(corpus_dir_str)
        passages = list(index_corpus_directory(corpus, corpus_dir))
        out_path = out_root / f"{corpus_id}-passages.jsonl"
        write_jsonl(out_path, passages)
        total_passages += len(passages)
        stats.append({"corpusId": corpus_id, "passageCount": len(passages), "outputPath": str(out_path), "status": "ok"})
        print(f"[OK] {corpus_id}: {len(passages)} passages -> {out_path}")

    summary = {
        "schemaVersion": PASSAGE_INDEX_SCHEMA,
        "generatedAt": utc_now(),
        "totalPassages": total_passages,
        "corpora": stats,
    }
    write_json(out_root / "index-summary.json", summary)
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build anchor passage index from corpus directories.")
    parser.add_argument("--registry", help="Path to anchor-corpus-registry.json")
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    parser.add_argument("--sanguozhi-root", help="Root directory for sanguozhi text files")
    parser.add_argument("--houhanshu-root", help="Root directory for houhanshu text files")
    parser.add_argument("--zizhitongjian-root", help="Root directory for zizhitongjian text files")
    parser.add_argument("--sanguoyanyi-root", help="Root directory for sanguoyanyi text files")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    corpus_roots: dict[str, str] = {}
    for corpus_id, attr in [
        ("sanguozhi", "sanguozhi_root"),
        ("houhanshu", "houhanshu_root"),
        ("zizhitongjian", "zizhitongjian_root"),
        ("sanguoyanyi", "sanguoyanyi_root"),
    ]:
        val = getattr(args, attr, None)
        if val:
            corpus_roots[corpus_id] = val

    summary = build_anchor_index(
        registry_path=args.registry,
        corpus_roots=corpus_roots,
        output_root=args.output_root,
    )
    print(f"Done. Total passages: {summary['totalPassages']}")


if __name__ == "__main__":
    main()
