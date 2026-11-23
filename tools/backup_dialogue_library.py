"""Back up and restore the NPC brain dialogue library through the admin API.

Usage (PowerShell or bash; secrets come from environment variables only):

  # back up the Render service to a local directory (default C:/Users/User/3klife-backups/dialogue-library/)
  python tools/backup_dialogue_library.py backup [--out-dir DIR]

  # restore / move a backup file into any service (dry-run first recommended)
  python tools/backup_dialogue_library.py restore --file PATH [--url URL] [--token-env NAME] [--dry-run]

Environment:
  NPC_ADMIN_TOKEN   admin bearer token of the service being read (backup) or written (restore)
  NPC_BRAIN_URL     service base URL (default https://threeklife-npc-brain.onrender.com)

The token is only sent in the Authorization header and is never written to files or printed.
Plain http is refused unless the host is localhost / 127.0.0.1 / ::1.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

DEFAULT_URL = "https://threeklife-npc-brain.onrender.com"
DEFAULT_OUT_DIR = Path("C:/Users/User/3klife-backups/dialogue-library")
URL_ENV = "NPC_BRAIN_URL"
TOKEN_ENV = "NPC_ADMIN_TOKEN"
EXPORT_PATH = "/v1/admin/dialogue-library/export"
IMPORT_PATH = "/v1/admin/dialogue-library/import"
TIMEOUT_SECONDS = 120
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}

Opener = Callable[..., Any]


class AdminRequestError(RuntimeError):
    pass


def resolve_base_url(explicit: str | None) -> str:
    base = (explicit or os.environ.get(URL_ENV) or DEFAULT_URL).strip().rstrip("/")
    parsed = urllib.parse.urlparse(base)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise AdminRequestError(f"invalid service URL: {base}")
    return base


def require_token(env_name: str) -> str:
    token = (os.environ.get(env_name) or "").strip()
    if not token:
        raise AdminRequestError(f"environment variable {env_name} is not set")
    return token


def _check_transport(base_url: str) -> None:
    parsed = urllib.parse.urlparse(base_url)
    if parsed.scheme == "http" and (parsed.hostname or "") not in LOCAL_HOSTS:
        raise AdminRequestError("refusing to send the admin token over plain http to a non-local host")


def _request(method: str, url: str, token: str, body: bytes | None, opener: Opener) -> Any:
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with opener(request, timeout=TIMEOUT_SECONDS) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", errors="replace")[:300]
        except Exception:  # noqa: BLE001 - best effort, the status code is what matters
            pass
        raise AdminRequestError(f"HTTP {exc.code} from {urllib.parse.urlparse(url).path}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise AdminRequestError(f"cannot reach {urllib.parse.urlparse(url).hostname}: {exc.reason}") from exc
    try:
        return json.loads(raw.decode("utf-8"))
    except ValueError as exc:
        raise AdminRequestError("service returned non-JSON response") from exc


def fetch_export(base_url: str, token: str, opener: Opener = urllib.request.urlopen) -> dict[str, Any]:
    _check_transport(base_url)
    return _request("GET", base_url + EXPORT_PATH, token, None, opener)


def post_import(
    base_url: str,
    token: str,
    payload: dict[str, Any],
    *,
    dry_run: bool = False,
    opener: Opener = urllib.request.urlopen,
) -> dict[str, Any]:
    _check_transport(base_url)
    query = "?dryRun=true" if dry_run else ""
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    return _request("POST", base_url + IMPORT_PATH + query, token, body, opener)


def backup(out_dir: Path, base_url: str, token: str, opener: Opener = urllib.request.urlopen) -> tuple[Path, int]:
    payload = fetch_export(base_url, token, opener)
    count = int(payload.get("count", len(payload.get("lines", []))))
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    target = out_dir / f"dialogue-library-{stamp}.json"
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return target, count


def restore(
    file_path: Path,
    base_url: str,
    token: str,
    *,
    dry_run: bool = False,
    opener: Opener = urllib.request.urlopen,
) -> dict[str, Any]:
    payload = json.loads(file_path.read_text(encoding="utf-8"))
    return post_import(base_url, token, payload, dry_run=dry_run, opener=opener)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    backup_parser = sub.add_parser("backup", help="download the full library to a timestamped JSON file")
    backup_parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    backup_parser.add_argument("--url", default=None, help=f"service base URL (env {URL_ENV})")

    restore_parser = sub.add_parser("restore", help="merge a backup JSON file into any service")
    restore_parser.add_argument("--file", type=Path, required=True)
    restore_parser.add_argument("--url", default=None, help=f"target service base URL (env {URL_ENV})")
    restore_parser.add_argument("--token-env", default=TOKEN_ENV, help="env var holding the target admin token")
    restore_parser.add_argument("--dry-run", action="store_true", help="report merge counts without writing")

    args = parser.parse_args(argv)
    try:
        base_url = resolve_base_url(args.url)
        if args.command == "backup":
            token = require_token(TOKEN_ENV)
            target, count = backup(args.out_dir, base_url, token)
            print(f"backup ok: {count} lines -> {target}")
        else:
            token = require_token(args.token_env)
            summary = restore(args.file, base_url, token, dry_run=args.dry_run)
            print(json.dumps(summary, ensure_ascii=False, indent=2))
    except (AdminRequestError, OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
