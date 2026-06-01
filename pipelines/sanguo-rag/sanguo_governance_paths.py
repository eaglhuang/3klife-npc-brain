from __future__ import annotations

from pathlib import Path


def _path(root: Path, section: str, filename: str) -> Path:
    return (root / section / filename).resolve()
