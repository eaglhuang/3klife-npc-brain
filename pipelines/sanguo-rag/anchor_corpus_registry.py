"""
Anchor Corpus Registry — SANGUO-AUTO-0201
定義 anchor corpus 正式清單、layer、trustTier、sourceFamily 與 locator 規則。
anchor corpus 是穩定的離線文本（正史/演義），可產生穩定 textHash 與 locator，
作為外部 seed 的查證參照，但不能把外部 seed 直接洗成 A-history。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from repo_layout import resolve_repo_root

REPO_ROOT = resolve_repo_root(__file__)

ANCHOR_REGISTRY_SCHEMA_VERSION = "anchor.registry.v0.1"

DEFAULT_REGISTRY_PATH = REPO_ROOT / "pipelines/sanguo-rag/config/anchor-corpus-registry.json"


def load_anchor_corpus_registry(registry_path: str | Path | None = None) -> dict[str, Any]:
    path = Path(registry_path) if registry_path else DEFAULT_REGISTRY_PATH
    if not path.exists():
        raise FileNotFoundError(f"Anchor corpus registry not found: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schemaVersion") != ANCHOR_REGISTRY_SCHEMA_VERSION:
        raise ValueError(f"Unexpected registry schema: {payload.get('schemaVersion')}")
    return payload


def list_anchor_corpora(registry: dict[str, Any]) -> list[dict[str, Any]]:
    return registry.get("corpora", [])


def anchor_corpus_by_id(registry: dict[str, Any], corpus_id: str) -> dict[str, Any] | None:
    for corpus in list_anchor_corpora(registry):
        if corpus.get("corpusId") == corpus_id:
            return corpus
    return None


def anchor_locator_prefix(corpus: dict[str, Any]) -> str:
    return corpus.get("locatorPrefix", corpus["corpusId"])


def validate_anchor_locator(locator: str, registry: dict[str, Any]) -> bool:
    for corpus in list_anchor_corpora(registry):
        prefix = anchor_locator_prefix(corpus)
        if locator.startswith(prefix + "#"):
            return True
    return False


def corpora_by_layer(registry: dict[str, Any], layer: str) -> list[dict[str, Any]]:
    return [c for c in list_anchor_corpora(registry) if c.get("layer") == layer]


def history_corpora(registry: dict[str, Any]) -> list[dict[str, Any]]:
    return corpora_by_layer(registry, "history")


def romance_corpora(registry: dict[str, Any]) -> list[dict[str, Any]]:
    return corpora_by_layer(registry, "romance")


def same_source_family_sites(corpus: dict[str, Any]) -> list[str]:
    """同一部書的跨站抓取不算獨立 sourceFamily。"""
    return corpus.get("equivalentSites", [])
