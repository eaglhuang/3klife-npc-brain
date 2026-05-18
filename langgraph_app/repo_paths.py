from __future__ import annotations

from pathlib import Path


def resolve_repo_root(anchor: Path | None = None) -> Path:
    start = (anchor or Path(__file__)).resolve()
    candidates = [start, *start.parents]
    for candidate in candidates:
        if (candidate / "app").is_dir() and (candidate / "langgraph_app").is_dir() and (candidate / "requirements.txt").exists():
            return candidate
        nested = candidate / "server" / "npc-brain"
        if (nested / "app").is_dir() and (nested / "langgraph_app").is_dir():
            return nested
    return Path(__file__).resolve().parents[1]


REPO_ROOT = resolve_repo_root()
PIPELINE_ROOT = Path("pipelines/sanguo-rag")
