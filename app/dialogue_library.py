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

BUCKET_VERSION = 1
MAX_LINES_PER_KEY = 5
DB_PATH_ENV = "NPC_DIALOGUE_LIBRARY_DB_PATH"
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


@dataclass(frozen=True)
class DialogueBuckets:
    generalId: str
    personality: str
    mood: str
    relationBucket: int
    worldPhase: str
    interactionType: str
    scene: str

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
    return DialogueBuckets(
        generalId=str(general_id),
        personality=personality_value,
        mood=mood_value,
        relationBucket=relation_value,
        worldPhase=phase_value,
        interactionType=interaction_value,
        scene=scene_value,
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
        raw = db_path or os.environ.get(DB_PATH_ENV) or DEFAULT_DB_PATH
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
