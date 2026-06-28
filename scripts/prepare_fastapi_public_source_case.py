from __future__ import annotations

import json
import subprocess
from pathlib import Path


UPSTREAM_URL = "https://github.com/fastapi/fastapi.git"
SNAPSHOT_TAG = "0.136.3"
BASELINE_ROOT = Path("local/public-source-snapshots/fastapi-0.136.3-baseline")
WORKING_ROOT = Path("local/public-source-snapshots/fastapi-0.136.3")
HELPER_SNIPPET = """


def _read_git_head(git_dir: _Path) -> str | None:
    head_path = git_dir / "HEAD"
    if not head_path.is_file():
        return None
    raw_head = head_path.read_text(encoding="utf-8").strip()
    if raw_head.startswith("ref: "):
        ref_path = git_dir / raw_head[5:]
        if ref_path.is_file():
            return ref_path.read_text(encoding="utf-8").strip() or None
        return raw_head[5:]
    return raw_head or None


def get_public_source_snapshot_metadata() -> dict[str, str | None]:
    module_path = _Path(__file__).resolve()
    repo_root = module_path.parents[1]
    git_dir = repo_root / ".git"
    return {
        "caseType": "public-source-snapshot",
        "package": "fastapi",
        "version": __version__,
        "modulePath": str(module_path),
        "repoRoot": str(repo_root),
        "repoHead": _read_git_head(git_dir),
        "helper": "get_public_source_snapshot_metadata",
    }
"""


def clone_if_missing(repo_root: Path, relative_path: Path) -> Path:
    target = repo_root / relative_path
    if target.exists():
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "clone", "--depth", "1", "--branch", SNAPSHOT_TAG, UPSTREAM_URL, str(target)],
        cwd=repo_root,
        check=True,
    )
    return target


def patch_working_snapshot(snapshot_root: Path) -> bool:
    init_path = snapshot_root / "fastapi" / "__init__.py"
    source = init_path.read_text(encoding="utf-8")
    if "get_public_source_snapshot_metadata" in source:
        return False

    source = source.replace(
        '"""FastAPI framework, high performance, easy to learn, fast to code, ready for production"""\n\n',
        '"""FastAPI framework, high performance, easy to learn, fast to code, ready for production"""\n\nfrom pathlib import Path as _Path\n\n',
        1,
    )
    source = source.rstrip() + HELPER_SNIPPET + "\n"
    init_path.write_text(source, encoding="utf-8")
    return True


def git_head(path: Path) -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=path,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout.strip()


def main() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    baseline_root = clone_if_missing(repo_root, BASELINE_ROOT)
    working_root = clone_if_missing(repo_root, WORKING_ROOT)
    patched = patch_working_snapshot(working_root)
    summary = {
        "upstreamUrl": UPSTREAM_URL,
        "tag": SNAPSHOT_TAG,
        "baselineRoot": str(baseline_root.resolve()),
        "workingRoot": str(working_root.resolve()),
        "baselineHead": git_head(baseline_root),
        "workingHead": git_head(working_root),
        "workingSnapshotPatched": patched or "get_public_source_snapshot_metadata already present",
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
