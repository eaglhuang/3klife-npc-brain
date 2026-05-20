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
DEFAULT_SOURCE_CONFIG_PATH = REPO_ROOT / "pipelines/sanguo-rag/config/anchor-index-build-sources.json"
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


def read_json(path: Path) -> Any:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8-sig"))


def read_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        text = line.strip()
        if not text:
            continue
        row = json.loads(text)
        if isinstance(row, dict):
            yield row


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


def normalize_layer(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text.endswith("-history") or text == "history":
        return "history"
    if text.endswith("-romance") or text == "romance":
        return "romance"
    return text


def corpus_for_evidence_record(
    record: dict[str, Any],
    corpora: list[dict[str, Any]],
) -> dict[str, Any] | None:
    family = str(record.get("sourceFamily") or record.get("sourcePolicyId") or record.get("sourceId") or "").strip()
    layer = normalize_layer(record.get("claimLayer") or record.get("sourceLayerRaw") or record.get("sourceLayer"))
    for corpus in corpora:
        if family and family == str(corpus.get("sourceFamily") or ""):
            return corpus
    for corpus in corpora:
        if layer and layer == str(corpus.get("layer") or ""):
            return corpus
    return None


def evidence_text(record: dict[str, Any]) -> str:
    for key in ("quote", "sourceQuote", "evidenceText", "seedText", "translatedTraditionalText"):
        value = str(record.get(key) or "").strip()
        if value:
            return value
    return ""


def evidence_locator(record: dict[str, Any], corpus: dict[str, Any], ordinal: int) -> str:
    prefix = str(corpus.get("locatorPrefix") or corpus.get("corpusId") or "anchor")
    raw_locator = str(record.get("locator") or "").strip()
    if not raw_locator:
        refs = [str(ref or "").strip() for ref in (record.get("evidenceRefs") or []) if str(ref or "").strip()]
        raw_locator = refs[0] if refs else str(record.get("sourceEvidenceId") or record.get("claimId") or record.get("seedId") or ordinal)
    if raw_locator.startswith(prefix + "#"):
        return raw_locator
    return f"{prefix}#{raw_locator}"


def build_passage_record_from_evidence(
    record: dict[str, Any],
    corpus: dict[str, Any],
    ordinal: int,
) -> dict[str, Any] | None:
    text = evidence_text(record)
    if len(normalize_text(text)) < MIN_PASSAGE_CHARS:
        return None
    normalized = normalize_text(text)
    text_hash = str(record.get("textHash") or "").strip() or stable_text_hash(normalized)
    person_ids = []
    for key in ("generalId", "fromId", "toId"):
        value = str(record.get(key) or "").strip()
        if value:
            person_ids.append(value)
    for value in record.get("generalIds") or []:
        token = str(value or "").strip()
        if token:
            person_ids.append(token)
    return {
        "corpusId": corpus["corpusId"],
        "sourceFamily": corpus["sourceFamily"],
        "layer": corpus["layer"],
        "trustTier": record.get("trustTier") or corpus["trustTier"],
        "locator": evidence_locator(record, corpus, ordinal),
        "textHash": text_hash,
        "normalizedText": normalized,
        "charCount": len(normalized),
        "personIds": sorted(set(person_ids)),
        "sourceRecordId": record.get("evidenceId") or record.get("sourceEvidenceId") or record.get("claimId") or record.get("seedId"),
    }


def index_evidence_jsonl(
    evidence_path: Path,
    corpora: list[dict[str, Any]],
) -> Iterator[dict[str, Any]]:
    for ordinal, record in enumerate(read_jsonl(evidence_path), 1):
        corpus = corpus_for_evidence_record(record, corpora)
        if not corpus:
            continue
        passage = build_passage_record_from_evidence(record, corpus, ordinal)
        if passage:
            passage["sourcePath"] = str(evidence_path)
            yield passage


def dedupe_passages(passages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for passage in passages:
        key = (
            str(passage.get("corpusId") or ""),
            str(passage.get("locator") or ""),
            str(passage.get("textHash") or ""),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(passage)
    return deduped


def source_config_paths(source_config: str | Path | None) -> list[Path]:
    if not source_config:
        return []
    path = resolve_path(source_config)
    payload = read_json(path)
    paths: list[Path] = []
    for row in payload.get("sources") or []:
        if not isinstance(row, dict) or row.get("enabled") is False:
            continue
        raw_path = str(row.get("path") or "").strip()
        if raw_path:
            paths.append(resolve_path(raw_path))
    for row in payload.get("globSources") or []:
        if not isinstance(row, dict) or row.get("enabled") is False:
            continue
        root_text = str(row.get("root") or "").strip()
        pattern = str(row.get("pattern") or "").strip()
        if not root_text or not pattern:
            continue
        root = resolve_path(root_text)
        if not root.exists():
            continue
        iterator = root.rglob(pattern) if row.get("recursive", True) else root.glob(pattern)
        paths.extend(sorted(path for path in iterator if path.is_file()))
    seen: set[Path] = set()
    unique: list[Path] = []
    for candidate in paths:
        resolved = candidate.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        unique.append(resolved)
    return unique


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
    evidence_jsonl_paths: list[str | Path] | None = None,
    source_config: str | Path | None = DEFAULT_SOURCE_CONFIG_PATH,
    output_root: str | Path = DEFAULT_OUTPUT_ROOT,
) -> dict[str, Any]:
    registry = load_anchor_corpus_registry(registry_path)
    out_root = resolve_path(output_root)
    corpus_roots = corpus_roots or {}
    corpora = registry.get("corpora", [])

    stats: list[dict[str, Any]] = []
    total_passages = 0

    for corpus in corpora:
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

    evidence_paths = [resolve_path(path) for path in (evidence_jsonl_paths or [])]
    evidence_paths.extend(source_config_paths(source_config))
    evidence_passages: list[dict[str, Any]] = []
    evidence_stats: list[dict[str, Any]] = []
    for evidence_path in evidence_paths:
        if not evidence_path.exists():
            evidence_stats.append({"path": str(evidence_path), "passageCount": 0, "status": "missing"})
            continue
        rows = dedupe_passages(list(index_evidence_jsonl(evidence_path, corpora)))
        evidence_passages.extend(rows)
        evidence_stats.append({"path": str(evidence_path), "passageCount": len(rows), "status": "ok"})
    if evidence_passages:
        evidence_passages = dedupe_passages(evidence_passages)
        out_path = out_root / "evidence-passages.jsonl"
        write_jsonl(out_path, evidence_passages)
        total_passages += len(evidence_passages)
        print(f"[OK] evidence-jsonl: {len(evidence_passages)} passages -> {out_path}")

    summary = {
        "schemaVersion": PASSAGE_INDEX_SCHEMA,
        "generatedAt": utc_now(),
        "totalPassages": total_passages,
        "corpora": stats,
        "evidenceJsonl": evidence_stats,
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
    parser.add_argument("--source-config", default=str(DEFAULT_SOURCE_CONFIG_PATH), help="Anchor index build source config JSON")
    parser.add_argument("--evidence-jsonl", action="append", default=[], help="Evidence JSONL to convert into anchor passages")
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
        evidence_jsonl_paths=args.evidence_jsonl,
        source_config=args.source_config,
        output_root=args.output_root,
    )
    print(f"Done. Total passages: {summary['totalPassages']}")


if __name__ == "__main__":
    main()
