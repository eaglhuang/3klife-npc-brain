from __future__ import annotations

from pathlib import Path

from repo_layout import resolve_npc_brain_root, resolve_repo_root


REPO_ROOT = resolve_repo_root(__file__)
NPC_BRAIN_ROOT = resolve_npc_brain_root(REPO_ROOT)
DEFAULT_GOVERNANCE_ROOT = NPC_BRAIN_ROOT / "data/sanguo"


def default_governance_root() -> Path:
    return DEFAULT_GOVERNANCE_ROOT
