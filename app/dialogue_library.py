"""Shared dialogue line library (phase 1): exact-key SQLite cache.

Each request is reduced to a bucketed parameter set; the bucket set is hashed
into a cache key. A key holds at most MAX_LINES_PER_KEY active lines. Lines are
served in rotation (lowest servedCount first) once a key is full; below that
threshold the caller generates a new line with AI and stores it.

Vector similarity fallback is intentionally NOT implemented here. See
SimilarityFallback below for the interface that a future sqlite-vec backend
should satisfy.
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

BUCKET_VERSION = 2
MAX_LINES_PER_KEY = 5
DB_PATH_ENV = "NPC_DIALOGUE_DB_PATH"
LEGACY_DB_PATH_ENV = "NPC_DIALOGUE_LIBRARY_DB_PATH"
DEFAULT_DB_PATH = "local/dialogue-library.sqlite"
REPO_ROOT = Path(__file__).resolve().parents[1]

PERSONALITIES = ("Loyal", "Brave", "Wise", "Playful")
MOODS = ("joy", "anger", "sorrow", "fear", "pride", "calm")
WORLD_PHASES = ("unification", "tripartite", "crisis", "chaos")
SCENES = ("battlefield", "court", "camp", "lobby", "private")
INTERACTION_TYPES = (
    "greeting",
    "daily_chat",
    "battle_start",
    "battle_victory",
    "battle_defeat",
    "gift",
    "order",
    "encounter",
    "meeting",
    "memory_recall",
    "keyword_topic",
    "praised",
    "rejected",
    "request_reward",
    "gift_received",
    "farewell",
    "comfort",
    "other",
)
RELATION_BUCKET_MIN = 0
RELATION_BUCKET_MAX = 4

DEFAULT_MOOD = "calm"
DEFAULT_RELATION_BUCKET = 2
DEFAULT_WORLD_PHASE = "tripartite"
DEFAULT_INTERACTION_TYPE = "other"
DEFAULT_SCENE = "lobby"
UNSET_PERSONALITY = "unset"

INTERACTION_DESCRIPTIONS: dict[str, str] = {
    "greeting": "向玩家日常問候致意。",
    "daily_chat": "與玩家閒聊近況或軍中瑣事。",
    "battle_start": "出征迎敵之際，宣示戰意。",
    "battle_victory": "戰勝敵軍後，向玩家報捷或慶賀。",
    "battle_defeat": "戰局不利或敗退之際，檢討或重振士氣。",
    "gift": "向玩家呈遞禮物或寶物。",
    "order": "接獲玩家派下的調度軍令或政務委託。",
    "encounter": "在城中偶遇玩家，隨性寒暄。",
    "meeting": "參與軍政會議，與玩家商討大計。",
    "memory_recall": "回想昔日往事，與玩家感懷交談。",
    "keyword_topic": "針對特定人物或典故話題展開交談。",
    "praised": "受到玩家誇獎與讚許，謙遜或得意地回應，並以幽默自嘲收尾。",
    "rejected": "剛向玩家提了請求但被拒絕，得體且幽默自嘲地回應這次被拒絕。",
    "request_reward": "自認立下功勞，半開玩笑向玩家討賞，用輕鬆自嘲收尾。",
    "gift_received": "收到玩家送的賞賜或禮物，驚喜感謝並以小梗調侃收尾。",
    "farewell": "即將出發執行遠征或任務，向玩家道別。",
    "comfort": "安慰或開解受到挫折的對象，語帶溫暖與調侃。",
    "other": "日常交談與互動。",
}



DEFAULT_CONTEXT_TAG = "none"


@dataclass(frozen=True)
class DialogueBuckets:
    generalId: str
    personality: str
    mood: str
    relationBucket: int
    worldPhase: str
    interactionType: str
    scene: str
    contextTag: str = DEFAULT_CONTEXT_TAG

    def as_dict(self) -> dict[str, Any]:
        return {
            "bucketVersion": BUCKET_VERSION,
            "generalId": self.generalId,
            "personality": self.personality,
            "mood": self.mood,
            "relationBucket": self.relationBucket,
            "worldPhase": self.worldPhase,
            "interactionType": self.interactionType,
            "scene": self.scene,
            "contextTag": self.contextTag,
        }


@dataclass(frozen=True)
class DialogueLine:
    lineId: int
    cacheKey: str
    text: str
    model: str | None
    promptVersion: str | None
    servedCount: int


def normalize_buckets(
    *,
    general_id: str,
    personality: str | None,
    mood: str | None,
    relation_bucket: int | None,
    world_phase: str | None,
    interaction_type: str | None,
    scene: str | None,
    context_tag: str | None = None,
) -> DialogueBuckets:
    """Map raw request values onto the closed bucket sets. Unknown values fall back to defaults."""
    personality_value = personality if personality in PERSONALITIES else UNSET_PERSONALITY
    mood_value = mood if mood in MOODS else DEFAULT_MOOD
    if isinstance(relation_bucket, bool) or not isinstance(relation_bucket, int):
        relation_value = DEFAULT_RELATION_BUCKET
    else:
        relation_value = min(RELATION_BUCKET_MAX, max(RELATION_BUCKET_MIN, relation_bucket))
    phase_value = world_phase if world_phase in WORLD_PHASES else DEFAULT_WORLD_PHASE
    interaction_value = interaction_type if interaction_type in INTERACTION_TYPES else DEFAULT_INTERACTION_TYPE
    scene_value = scene if scene in SCENES else DEFAULT_SCENE
    tag_value = str(context_tag).strip() if context_tag else DEFAULT_CONTEXT_TAG
    return DialogueBuckets(
        generalId=str(general_id),
        personality=personality_value,
        mood=mood_value,
        relationBucket=relation_value,
        worldPhase=phase_value,
        interactionType=interaction_value,
        scene=scene_value,
        contextTag=tag_value,
    )


def compute_cache_key(buckets: DialogueBuckets) -> str:
    """SHA-256 over canonical JSON of the bucket fields. Locale and raw keyword text are excluded."""
    canonical = json.dumps(buckets.as_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]


class SimilarityFallback(Protocol):
    """Interface for the vector fallback. TODO(phase 2): implement with sqlite-vec.

    Must only match lines of the same generalId and return None below min_score.
    """

    def find_similar(self, general_id: str, descriptor: str, *, min_score: float = 0.85) -> DialogueLine | None: ...


class NullSimilarityFallback:
    """Phase 1 placeholder: always misses."""

    def find_similar(self, general_id: str, descriptor: str, *, min_score: float = 0.85) -> DialogueLine | None:
        return None


_SCHEMA = """
CREATE TABLE IF NOT EXISTS dialogue_line (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cache_key TEXT NOT NULL,
    general_id TEXT NOT NULL,
    bucket_json TEXT NOT NULL,
    text_zh_tw TEXT NOT NULL,
    text_en TEXT,
    text_ja TEXT,
    text_zh_cn TEXT,
    model TEXT,
    created_at TEXT NOT NULL,
    prompt_version TEXT,
    quality_score REAL,
    quality_flags_json TEXT,
    extra_json TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    served_count INTEGER NOT NULL DEFAULT 0,
    last_served_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_dialogue_line_key_status ON dialogue_line (cache_key, status);
"""


class DialogueLibrary:
    def __init__(self, db_path: str | Path | None = None, similarity: SimilarityFallback | None = None) -> None:
        raw = db_path or os.environ.get(DB_PATH_ENV) or os.environ.get(LEGACY_DB_PATH_ENV) or DEFAULT_DB_PATH
        path = Path(raw)
        self.db_path = path if path.is_absolute() else REPO_ROOT / path
        self.similarity: SimilarityFallback = similarity or NullSimilarityFallback()
        self._lock = threading.Lock()
        self._initialized = False

    def _connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(str(self.db_path), timeout=10, isolation_level=None)
        connection.row_factory = sqlite3.Row
        if not self._initialized:
            connection.executescript(_SCHEMA)
            self._initialized = True
        return connection

    @contextmanager
    def _session(self):
        with self._lock:
            connection = self._connect()
            try:
                yield connection
            finally:
                connection.close()

    def count_active(self, cache_key: str) -> int:
        with self._session() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM dialogue_line WHERE cache_key = ? AND status = 'active'",
                (cache_key,),
            ).fetchone()
        return int(row["n"])

    def pick(self, cache_key: str) -> DialogueLine | None:
        """Serve one active line: lowest servedCount, ties broken randomly. Increments servedCount."""
        with self._session() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                rows = conn.execute(
                    "SELECT id, cache_key, text_zh_tw, model, prompt_version, served_count "
                    "FROM dialogue_line WHERE cache_key = ? AND status = 'active'",
                    (cache_key,),
                ).fetchall()
                if not rows:
                    conn.execute("COMMIT")
                    return None
                lowest = min(int(row["served_count"]) for row in rows)
                row = random.choice([item for item in rows if int(item["served_count"]) == lowest])
                now = datetime.now(UTC).isoformat()
                conn.execute(
                    "UPDATE dialogue_line SET served_count = served_count + 1, last_served_at = ? WHERE id = ?",
                    (now, row["id"]),
                )
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
        return DialogueLine(
            lineId=int(row["id"]),
            cacheKey=cache_key,
            text=str(row["text_zh_tw"]),
            model=row["model"],
            promptVersion=row["prompt_version"],
            servedCount=lowest + 1,
        )

    def add(
        self,
        cache_key: str,
        buckets: DialogueBuckets,
        text_zh_tw: str,
        *,
        model: str | None,
        prompt_version: str | None,
        quality_score: float | None = None,
        quality_flags: list[str] | None = None,
        extra: dict[str, Any] | None = None,
    ) -> int | None:
        """Insert a line. When the key is full, replace the lowest-quality line only if the new one scores higher.

        Returns the new line id, or None when the key is full and the new line does not beat the existing ones.
        Lines with no qualityScore never displace existing lines.
        """
        text = str(text_zh_tw or "").strip()
        if not text:
            return None
        now = datetime.now(UTC).isoformat()
        with self._session() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                active = conn.execute(
                    "SELECT id, quality_score FROM dialogue_line WHERE cache_key = ? AND status = 'active'",
                    (cache_key,),
                ).fetchall()
                retire_id: int | None = None
                if len(active) >= MAX_LINES_PER_KEY:
                    if quality_score is None:
                        conn.execute("COMMIT")
                        return None
                    worst = min(active, key=lambda item: -1.0 if item["quality_score"] is None else float(item["quality_score"]))
                    worst_score = -1.0 if worst["quality_score"] is None else float(worst["quality_score"])
                    if float(quality_score) <= worst_score:
                        conn.execute("COMMIT")
                        return None
                    retire_id = int(worst["id"])
                    conn.execute("UPDATE dialogue_line SET status = 'retired' WHERE id = ?", (retire_id,))
                cursor = conn.execute(
                    """
                    INSERT INTO dialogue_line (
                        cache_key, general_id, bucket_json, text_zh_tw, model, created_at,
                        prompt_version, quality_score, quality_flags_json, extra_json, status, served_count
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', 0)
                    """,
                    (
                        cache_key,
                        buckets.generalId,
                        json.dumps(buckets.as_dict(), ensure_ascii=False, sort_keys=True),
                        text,
                        model,
                        now,
                        prompt_version,
                        quality_score,
                        json.dumps(quality_flags or [], ensure_ascii=False),
                        json.dumps(extra or {}, ensure_ascii=False, sort_keys=True),
                    ),
                )
                line_id = int(cursor.lastrowid)
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
        return line_id


EXPORT_FORMAT = "3klife-dialogue-library"
EXPORT_VERSION = 1
_VALID_STATUSES = ("active", "retired")


def _parse_timestamp(value: Any, field: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be an ISO-8601 string")
    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError as exc:
        raise ValueError(f"{field} is not a valid ISO-8601 timestamp: {value!r}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def _json_or_default(raw: str | None, default: Any) -> Any:
    if not raw:
        return default
    try:
        return json.loads(raw)
    except ValueError:
        return default


def _row_to_export(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "key": row["cache_key"],
        "generalId": row["general_id"],
        "bucket": _json_or_default(row["bucket_json"], {}),
        "textZhTw": row["text_zh_tw"],
        "textEn": row["text_en"],
        "textJa": row["text_ja"],
        "textZhCn": row["text_zh_cn"],
        "model": row["model"],
        "createdAt": row["created_at"],
        "promptVersion": row["prompt_version"],
        "qualityScore": row["quality_score"],
        "qualityFlags": _json_or_default(row["quality_flags_json"], []),
        "extra": _json_or_default(row["extra_json"], {}),
        "status": row["status"],
        "servedCount": int(row["served_count"] or 0),
        "lastServedAt": row["last_served_at"],
    }


def _validate_import_line(index: int, item: Any) -> dict[str, Any]:
    where = f"lines[{index}]"
    if not isinstance(item, dict):
        raise ValueError(f"{where} must be an object")
    key = item.get("key")
    if not isinstance(key, str) or not key.strip():
        raise ValueError(f"{where}.key must be a non-empty string")
    text = item.get("textZhTw")
    if not isinstance(text, str) or not text.strip():
        raise ValueError(f"{where}.textZhTw must be a non-empty string")
    status = item.get("status") or "active"
    if status not in _VALID_STATUSES:
        raise ValueError(f"{where}.status must be one of {_VALID_STATUSES}")
    served = item.get("servedCount") or 0
    if isinstance(served, bool) or not isinstance(served, int) or served < 0:
        raise ValueError(f"{where}.servedCount must be a non-negative integer")
    quality = item.get("qualityScore")
    if quality is not None and (isinstance(quality, bool) or not isinstance(quality, (int, float))):
        raise ValueError(f"{where}.qualityScore must be a number or null")
    bucket = item.get("bucket") or {}
    flags = item.get("qualityFlags") or []
    extra = item.get("extra") or {}
    if not isinstance(bucket, dict) or not isinstance(flags, list) or not isinstance(extra, dict):
        raise ValueError(f"{where}.bucket/extra must be objects and qualityFlags must be a list")
    created_at = _parse_timestamp(item.get("createdAt"), f"{where}.createdAt")
    return {
        "key": key.strip(),
        "generalId": str(item.get("generalId") or bucket.get("generalId") or ""),
        "bucket": bucket,
        "textZhTw": text.strip(),
        "textEn": item.get("textEn"),
        "textJa": item.get("textJa"),
        "textZhCn": item.get("textZhCn"),
        "model": item.get("model"),
        "createdAt": created_at.isoformat(),
        "createdAtDt": created_at,
        "promptVersion": item.get("promptVersion"),
        "qualityScore": None if quality is None else float(quality),
        "qualityFlags": flags,
        "extra": extra,
        "status": status,
        "servedCount": served,
        "lastServedAt": item.get("lastServedAt"),
    }


def validate_import_payload(payload: Any) -> list[dict[str, Any]]:
    """Validate a backup payload and return normalized lines. Raises ValueError on any malformed input."""
    if not isinstance(payload, dict):
        raise ValueError("payload must be a JSON object")
    if payload.get("format") not in (None, EXPORT_FORMAT):
        raise ValueError(f"unsupported format: {payload.get('format')!r}")
    if payload.get("version") not in (None, EXPORT_VERSION):
        raise ValueError(f"unsupported version: {payload.get('version')!r}")
    lines = payload.get("lines")
    if not isinstance(lines, list):
        raise ValueError("payload.lines must be a list")
    return [_validate_import_line(index, item) for index, item in enumerate(lines)]


def _count_active(conn: sqlite3.Connection, cache_key: str) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM dialogue_line WHERE cache_key = ? AND status = 'active'",
        (cache_key,),
    ).fetchone()
    return int(row["n"])


def _merge_line(conn: sqlite3.Connection, item: dict[str, Any]) -> tuple[str, bool]:
    """Merge one normalized line by (key, textZhTw). Returns (action, capped).

    Newer local rows are never overwritten. Active lines beyond MAX_LINES_PER_KEY are stored as retired.
    """
    existing = conn.execute(
        "SELECT id, status, created_at, served_count FROM dialogue_line "
        "WHERE cache_key = ? AND text_zh_tw = ? ORDER BY id LIMIT 1",
        (item["key"], item["textZhTw"]),
    ).fetchone()
    status = item["status"]
    capped = False
    if status == "active":
        others_active = _count_active(conn, item["key"])
        if existing is not None and existing["status"] == "active":
            others_active -= 1
        if others_active >= MAX_LINES_PER_KEY:
            status = "retired"
            capped = True
    bucket_json = json.dumps(item["bucket"], ensure_ascii=False, sort_keys=True)
    flags_json = json.dumps(item["qualityFlags"], ensure_ascii=False)
    extra_json = json.dumps(item["extra"], ensure_ascii=False, sort_keys=True)
    if existing is not None:
        local_created = _parse_timestamp(existing["created_at"], "created_at")
        if local_created >= item["createdAtDt"]:
            return "skipped", False
        conn.execute(
            """
            UPDATE dialogue_line SET general_id = ?, bucket_json = ?, text_en = ?, text_ja = ?, text_zh_cn = ?,
                model = ?, created_at = ?, prompt_version = ?, quality_score = ?, quality_flags_json = ?,
                extra_json = ?, status = ?, served_count = MAX(served_count, ?),
                last_served_at = COALESCE(?, last_served_at)
            WHERE id = ?
            """,
            (
                item["generalId"], bucket_json, item["textEn"], item["textJa"], item["textZhCn"],
                item["model"], item["createdAt"], item["promptVersion"], item["qualityScore"], flags_json,
                extra_json, status, item["servedCount"], item["lastServedAt"], existing["id"],
            ),
        )
        return "updated", capped
    conn.execute(
        """
        INSERT INTO dialogue_line (
            cache_key, general_id, bucket_json, text_en, text_ja, text_zh_cn, text_zh_tw,
            model, created_at, prompt_version, quality_score, quality_flags_json, extra_json,
            status, served_count, last_served_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            item["key"], item["generalId"], bucket_json, item["textEn"], item["textJa"], item["textZhCn"],
            item["textZhTw"], item["model"], item["createdAt"], item["promptVersion"], item["qualityScore"],
            flags_json, extra_json, status, item["servedCount"], item["lastServedAt"],
        ),
    )
    return "inserted", capped


def export_library_payload(library: DialogueLibrary) -> dict[str, Any]:
    """Full dump of every row (active and retired) as a portable JSON payload."""
    with library._session() as conn:
        rows = conn.execute("SELECT * FROM dialogue_line ORDER BY id").fetchall()
    lines = [_row_to_export(row) for row in rows]
    return {
        "format": EXPORT_FORMAT,
        "version": EXPORT_VERSION,
        "exportedAt": datetime.now(UTC).isoformat(),
        "count": len(lines),
        "lines": lines,
    }


def import_library_payload(library: DialogueLibrary, payload: Any, *, dry_run: bool = False) -> dict[str, Any]:
    """Merge a backup payload in one transaction. dry_run rolls back and only reports the counts."""
    items = validate_import_payload(payload)
    summary: dict[str, Any] = {
        "dryRun": bool(dry_run),
        "received": len(items),
        "inserted": 0,
        "updated": 0,
        "skipped": 0,
        "cappedToRetired": 0,
    }
    with library._session() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            for item in items:
                action, capped = _merge_line(conn, item)
                summary[action] += 1
                if capped:
                    summary["cappedToRetired"] += 1
            conn.execute("ROLLBACK" if dry_run else "COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    return summary
