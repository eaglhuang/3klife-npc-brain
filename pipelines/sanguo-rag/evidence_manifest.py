"""Evidence manifest read/write and resumable-scan helpers for Sanguo-RAG.

This module implements the manifest schema defined in
``pipelines/sanguo-rag/fixtures/evidence-manifest.schema.json`` and the
artifact-lake layout defined in
``data/sanguo/policies/policy-artifact-lake-layout.json``.

It is intentionally dependency-light: no external JSON-Schema library is
required at runtime because the validation surface is narrow (presence,
type, regex). Production code paths still verify sha256 and missing/extra
file detection.

The module is not wired into runtime by default; M1-0102 only delivers the
schema, helpers, and smoke fixture. Runtime integration is deferred to
M2-0202 (repository adapter).
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

SCHEMA_VERSION = "evidence-manifest.v0.1"
ARTIFACT_URI_PATTERN = re.compile(r"^atm://lake/[^/]+/sources/[^/]+/[^/]+/.+")
SHA256_PATTERN = re.compile(r"^[a-f0-9]{64}$")
ARTIFACT_TYPES = frozenset(
    {
        "raw-page",
        "harvested-page",
        "evidence-seed",
        "evidence-card",
        "anchor-passage",
        "proposal",
        "scoreboard",
        "telemetry",
        "manifest",
        "run",
    }
)
COMPRESSION_FORMATS = frozenset({"none", "zstd", "gzip"})
RETENTION_TIERS = frozenset({"hot", "cold", "archive", "expired"})
LIFECYCLE_ACTIONS = frozenset({"write", "compress", "archive", "rollback"})


class ManifestValidationError(ValueError):
    """Raised when a manifest fails structural validation."""


@dataclass
class ManifestEntry:
    artifact_type: str
    source_id: str
    artifact_uri: str
    path: str
    sha256: str
    size: int
    created_at: str
    shard_id: str | None = None
    round_id: str | None = None
    corpus_id: str | None = None
    layer_id: str | None = None
    compression: dict[str, Any] | None = None
    retention_tier: str | None = None
    body_start: int | None = None
    body_end: int | None = None
    linked_residual_proposal_id: str | None = None

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ManifestEntry":
        return cls(
            artifact_type=str(payload["artifactType"]),
            source_id=str(payload["sourceId"]),
            artifact_uri=str(payload["artifactUri"]),
            path=str(payload["path"]),
            sha256=str(payload["sha256"]),
            size=int(payload["size"]),
            created_at=str(payload["createdAt"]),
            shard_id=payload.get("shardId"),
            round_id=payload.get("roundId"),
            corpus_id=payload.get("corpusId"),
            layer_id=payload.get("layerId"),
            compression=payload.get("compression"),
            retention_tier=payload.get("retentionTier"),
            body_start=payload.get("bodyStart"),
            body_end=payload.get("bodyEnd"),
            linked_residual_proposal_id=payload.get("linkedResidualProposalId"),
        )


@dataclass
class EvidenceManifest:
    run_id: str
    generated_at: str
    updated_at: str
    canonical_writes: bool
    input_fingerprint: dict[str, Any]
    files: list[ManifestEntry]
    schema_version: str = SCHEMA_VERSION
    lane: str | None = None
    policy_refs: list[str] = field(default_factory=list)
    telemetry: dict[str, Any] = field(default_factory=dict)
    lifecycle: dict[str, Any] = field(default_factory=dict)
    summary: dict[str, Any] = field(default_factory=dict)

    @property
    def file_count(self) -> int:
        return len(self.files)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "EvidenceManifest":
        files = [ManifestEntry.from_dict(entry) for entry in payload.get("files", [])]
        return cls(
            run_id=str(payload["runId"]),
            generated_at=str(payload["generatedAt"]),
            updated_at=str(payload["updatedAt"]),
            canonical_writes=bool(payload["canonicalWrites"]),
            input_fingerprint=dict(payload["inputFingerprint"]),
            files=files,
            schema_version=str(payload.get("schemaVersion", SCHEMA_VERSION)),
            lane=payload.get("lane"),
            policy_refs=list(payload.get("policyRefs", [])),
            telemetry=dict(payload.get("telemetry", {})),
            lifecycle=dict(payload.get("lifecycle", {})),
            summary=dict(payload.get("summary", {})),
        )


def validate_manifest(payload: dict[str, Any]) -> None:
    """Structural validation. Raises ManifestValidationError on first failure.

    Verifies required fields, sha256 hex pattern, artifactUri scheme,
    artifactType / compression / retention enums, and fileCount alignment.
    """

    if not isinstance(payload, dict):
        raise ManifestValidationError("manifest payload must be an object")

    required_top = {
        "schemaVersion",
        "runId",
        "generatedAt",
        "updatedAt",
        "canonicalWrites",
        "inputFingerprint",
        "fileCount",
        "files",
    }
    missing = required_top - payload.keys()
    if missing:
        raise ManifestValidationError(f"missing required fields: {sorted(missing)}")

    if payload["schemaVersion"] != SCHEMA_VERSION:
        raise ManifestValidationError(
            f"schemaVersion must equal {SCHEMA_VERSION!r}, got {payload['schemaVersion']!r}"
        )

    fingerprint = payload["inputFingerprint"]
    for key in ("sha256", "fileCount", "files"):
        if key not in fingerprint:
            raise ManifestValidationError(f"inputFingerprint missing key: {key!r}")
    if not SHA256_PATTERN.match(str(fingerprint["sha256"])):
        raise ManifestValidationError("inputFingerprint.sha256 must be 64-hex lowercase")

    if not isinstance(payload["files"], list):
        raise ManifestValidationError("files must be a list")
    declared = int(payload["fileCount"])
    actual = len(payload["files"])
    if declared != actual:
        raise ManifestValidationError(
            f"fileCount={declared} disagrees with len(files)={actual}"
        )

    seen_uris: set[str] = set()
    for index, raw in enumerate(payload["files"]):
        if not isinstance(raw, dict):
            raise ManifestValidationError(f"files[{index}] must be an object")
        missing_entry = {"artifactType", "sourceId", "artifactUri", "path", "sha256", "size", "createdAt"} - raw.keys()
        if missing_entry:
            raise ManifestValidationError(
                f"files[{index}] missing keys: {sorted(missing_entry)}"
            )
        if raw["artifactType"] not in ARTIFACT_TYPES:
            raise ManifestValidationError(
                f"files[{index}].artifactType {raw['artifactType']!r} not in allowed set"
            )
        if not ARTIFACT_URI_PATTERN.match(str(raw["artifactUri"])):
            raise ManifestValidationError(
                f"files[{index}].artifactUri does not match lake scheme: {raw['artifactUri']!r}"
            )
        if not SHA256_PATTERN.match(str(raw["sha256"])):
            raise ManifestValidationError(f"files[{index}].sha256 must be 64-hex lowercase")
        if raw["artifactUri"] in seen_uris:
            raise ManifestValidationError(
                f"files[{index}].artifactUri duplicates earlier entry: {raw['artifactUri']!r}"
            )
        seen_uris.add(str(raw["artifactUri"]))
        compression = raw.get("compression")
        if compression is not None:
            fmt = compression.get("format")
            if fmt not in COMPRESSION_FORMATS:
                raise ManifestValidationError(
                    f"files[{index}].compression.format {fmt!r} not allowed"
                )
        tier = raw.get("retentionTier")
        if tier is not None and tier not in RETENTION_TIERS:
            raise ManifestValidationError(
                f"files[{index}].retentionTier {tier!r} not allowed"
            )

    lifecycle = payload.get("lifecycle") or {}
    last_action = lifecycle.get("lastAction")
    if last_action is not None and last_action not in LIFECYCLE_ACTIONS:
        raise ManifestValidationError(
            f"lifecycle.lastAction {last_action!r} not allowed"
        )


def sha256_file(path: Path, chunk_size: int = 1 << 20) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            chunk = fh.read(chunk_size)
            if not chunk:
                break
            hasher.update(chunk)
    return hasher.hexdigest()


@dataclass
class ResumeScanReport:
    run_id: str
    file_count: int
    missing: list[str] = field(default_factory=list)
    duplicates: list[str] = field(default_factory=list)
    hash_mismatch: list[dict[str, str]] = field(default_factory=list)
    unexpected: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not (self.missing or self.duplicates or self.hash_mismatch or self.unexpected)


def scan_manifest_for_resume(
    manifest: EvidenceManifest,
    lake_root: Path,
    *,
    verify_sha256: bool = False,
    extra_paths: Iterable[Path] | None = None,
) -> ResumeScanReport:
    """Walk only manifest-listed files to detect missing / mismatch / duplicates.

    ``extra_paths`` lets the caller declare additional concrete paths that
    are expected to exist (e.g. the manifest's own location). Anything not
    in either set is reported as ``unexpected`` so a divergent on-disk file
    can be spotted without scanning the entire tree.
    """

    report = ResumeScanReport(run_id=manifest.run_id, file_count=manifest.file_count)
    expected_paths: set[Path] = set()
    seen_uris: set[str] = set()

    for entry in manifest.files:
        if entry.artifact_uri in seen_uris:
            report.duplicates.append(entry.artifact_uri)
            continue
        seen_uris.add(entry.artifact_uri)
        candidate = (lake_root / entry.path).resolve() if not Path(entry.path).is_absolute() else Path(entry.path).resolve()
        expected_paths.add(candidate)
        if not candidate.exists():
            report.missing.append(str(candidate))
            continue
        if verify_sha256:
            digest = sha256_file(candidate)
            if digest != entry.sha256:
                report.hash_mismatch.append(
                    {"path": str(candidate), "expected": entry.sha256, "actual": digest}
                )

    for extra in extra_paths or []:
        expected_paths.add(extra.resolve())

    return report


def load_manifest(path: Path) -> EvidenceManifest:
    payload = json.loads(path.read_text(encoding="utf-8"))
    validate_manifest(payload)
    return EvidenceManifest.from_dict(payload)


def dump_manifest(manifest: EvidenceManifest, path: Path) -> None:
    payload = {
        "schemaVersion": manifest.schema_version,
        "runId": manifest.run_id,
        "lane": manifest.lane,
        "generatedAt": manifest.generated_at,
        "updatedAt": manifest.updated_at,
        "canonicalWrites": manifest.canonical_writes,
        "policyRefs": list(manifest.policy_refs),
        "inputFingerprint": dict(manifest.input_fingerprint),
        "fileCount": manifest.file_count,
        "files": [_entry_to_dict(entry) for entry in manifest.files],
        "telemetry": dict(manifest.telemetry),
        "lifecycle": dict(manifest.lifecycle),
        "summary": dict(manifest.summary),
    }
    validate_manifest(payload)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def _entry_to_dict(entry: ManifestEntry) -> dict[str, Any]:
    return {
        "artifactType": entry.artifact_type,
        "sourceId": entry.source_id,
        "shardId": entry.shard_id,
        "roundId": entry.round_id,
        "corpusId": entry.corpus_id,
        "layerId": entry.layer_id,
        "artifactUri": entry.artifact_uri,
        "path": entry.path,
        "sha256": entry.sha256,
        "size": entry.size,
        "createdAt": entry.created_at,
        "compression": entry.compression,
        "retentionTier": entry.retention_tier,
        "bodyStart": entry.body_start,
        "bodyEnd": entry.body_end,
        "linkedResidualProposalId": entry.linked_residual_proposal_id,
    }


__all__ = [
    "ARTIFACT_TYPES",
    "ARTIFACT_URI_PATTERN",
    "COMPRESSION_FORMATS",
    "EvidenceManifest",
    "LIFECYCLE_ACTIONS",
    "ManifestEntry",
    "ManifestValidationError",
    "RETENTION_TIERS",
    "ResumeScanReport",
    "SCHEMA_VERSION",
    "SHA256_PATTERN",
    "dump_manifest",
    "load_manifest",
    "scan_manifest_for_resume",
    "sha256_file",
    "validate_manifest",
]
