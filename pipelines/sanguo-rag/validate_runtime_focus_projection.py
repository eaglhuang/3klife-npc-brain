from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


DEFAULT_PROFILE_ROOT = Path("artifacts/data-pipeline/sanguo-rag/extracted/runtime-general-profiles")
INSUFFICIENT_STATUS = "insufficient_source_data"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate runtime focus projection gates for generated Sanguo profiles.")
    parser.add_argument("--profile-root", default=str(DEFAULT_PROFILE_ROOT))
    parser.add_argument("--general-id", action="append", default=[])
    parser.add_argument("--json", action="store_true")
    return parser.parse_args()


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def persona_paths(profile_root: Path, general_ids: list[str]) -> list[Path]:
    if general_ids:
        return [profile_root / general_id / f"{general_id}.persona.json" for general_id in general_ids]
    return sorted(profile_root.glob("*/*.persona.json"))


def iter_runtime_sources(persona: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    sources: list[tuple[str, dict[str, Any]]] = []
    for beat in persona.get("storyBeats") or []:
        if isinstance(beat, dict):
            sources.append(("storyBeat", beat))
    for highlight in persona.get("sourceHighlights") or []:
        if isinstance(highlight, dict):
            sources.append(("sourceHighlight", highlight))
    return sources


def validate_persona(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"path": str(path), "ok": False, "errors": ["persona file not found"], "warnings": []}
    persona = load_json(path)
    errors: list[str] = []
    warnings: list[str] = []
    projection_count = 0
    scene_eligible_count = 0
    feedback_count = 0
    for source_type, source in iter_runtime_sources(persona):
        source_ref = source.get("sourceRef") or next(iter(source.get("sourceRefs") or []), None)
        for projection in source.get("targetProjections") or []:
            if not isinstance(projection, dict):
                continue
            projection_count += 1
            target_id = str(projection.get("targetId") or "").strip()
            trace_sources = {str(item).strip() for item in (projection.get("traceSources") or []) if str(item).strip()}
            scene_eligible = bool(projection.get("sceneEligible"))
            if scene_eligible:
                scene_eligible_count += 1
            if projection.get("upstreamFeedback"):
                feedback_count += 1
            if trace_sources and trace_sources <= {"aliasMatch"} and scene_eligible:
                errors.append(f"{source_type}:{source_ref}:{target_id}: alias-only projection cannot be sceneEligible")
            if projection.get("sourceDataStatus") == INSUFFICIENT_STATUS:
                if scene_eligible:
                    errors.append(f"{source_type}:{source_ref}:{target_id}: insufficient source data cannot be sceneEligible")
                if not projection.get("upstreamFeedback"):
                    errors.append(f"{source_type}:{source_ref}:{target_id}: insufficient source data must emit upstreamFeedback")

    projection_gate_active = projection_count > 0 or bool((persona.get("targetLinking") or {}).get("focusProjectionVersion"))
    if projection_gate_active:
        link_keys: set[tuple[str, str, str]] = set()
        duplicate_links: list[str] = []
        for link in persona.get("angleTargetLinks") or []:
            if not isinstance(link, dict):
                continue
            key = (
                str(link.get("targetId") or ""),
                str(link.get("sourceRef") or link.get("sourceId") or ""),
                str(link.get("sourceType") or ""),
            )
            if key in link_keys:
                duplicate_links.append("|".join(key))
            link_keys.add(key)
            if link.get("sourceDataStatus") == INSUFFICIENT_STATUS and link.get("sceneEligible"):
                errors.append(f"angleTargetLinks:{'|'.join(key)}: insufficient source data cannot be sceneEligible")
        if duplicate_links:
            errors.append(f"angleTargetLinks duplicate target/source identities: {sorted(set(duplicate_links))[:12]}")
    if projection_count == 0:
        warnings.append("profile has no targetProjections yet; regenerate with updated exporter to activate downstream projection gates")
    return {
        "path": str(path),
        "generalId": persona.get("generalId"),
        "ok": not errors,
        "projectionCount": projection_count,
        "sceneEligibleProjectionCount": scene_eligible_count,
        "upstreamFeedbackProjectionCount": feedback_count,
        "errors": errors,
        "warnings": warnings,
    }


def main() -> int:
    args = parse_args()
    profile_root = Path(args.profile_root)
    reports = [validate_persona(path) for path in persona_paths(profile_root, args.general_id)]
    payload = {
        "ok": all(report.get("ok") for report in reports),
        "profileRoot": str(profile_root),
        "checkedCount": len(reports),
        "failedCount": len([report for report in reports if not report.get("ok")]),
        "reports": reports,
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        status = "PASS" if payload["ok"] else "FAIL"
        print(f"runtime focus projection validation: {status} checked={payload['checkedCount']} failed={payload['failedCount']}")
        for report in reports:
            for error in report.get("errors") or []:
                print(f"ERROR {report.get('generalId') or report.get('path')}: {error}")
            for warning in report.get("warnings") or []:
                print(f"WARN {report.get('generalId') or report.get('path')}: {warning}")
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())