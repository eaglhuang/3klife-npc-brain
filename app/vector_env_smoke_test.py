from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys

PIPELINE_HELPER_ROOT = Path(__file__).resolve().parents[1] / "pipelines" / "sanguo-rag"
if str(PIPELINE_HELPER_ROOT) not in sys.path:
    sys.path.insert(0, str(PIPELINE_HELPER_ROOT))
from repo_layout import resolve_repo_root
from urllib import error, request

from .llm_dialogue_renderer import load_local_env
from .vector_config import load_vector_runtime_config


def _package_installed(module_name: str) -> bool:
    return importlib.util.find_spec(module_name) is not None


def _ping_qdrant(base_url: str) -> dict:
    url = f"{base_url.rstrip('/')}/collections"
    try:
        with request.urlopen(url, timeout=2.5) as response:
            payload = json.loads(response.read().decode("utf-8"))
        collections = ((payload or {}).get("result") or {}).get("collections") or []
        return {
            "reachable": True,
            "url": url,
            "collectionCount": len(collections),
        }
    except error.URLError as exc:
        return {
            "reachable": False,
            "url": url,
            "error": str(exc.reason),
        }
    except Exception as exc:  # pragma: no cover - smoke helper only
        return {
            "reachable": False,
            "url": url,
            "error": str(exc),
        }


def main() -> None:
    repo_root = resolve_repo_root(__file__)
    load_local_env(repo_root)
    config = load_vector_runtime_config()
    payload = {
        "vector": config.as_health(),
        "packages": {
            "pinecone": _package_installed("pinecone"),
            "qdrant_client": _package_installed("qdrant_client"),
        },
        "qdrantPing": _ping_qdrant(config.qdrant_url),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()