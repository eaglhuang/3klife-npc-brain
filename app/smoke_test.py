from __future__ import annotations

import os
import json

from pathlib import Path

from .npc_dialogue_service import DialogueRequest, NpcDialogueService, SceneDirectorRequest


def main() -> None:
    os.environ["NPC_LLM_PROVIDER_ORDER"] = "deterministic"
    workspace_root = Path(__file__).resolve().parents[1]
    workspace_runtime_root = workspace_root / "artifacts/data-pipeline/sanguo-rag/extracted/runtime-general-profiles"
    service_kwargs = {"runtime_profile_root": workspace_runtime_root} if workspace_runtime_root.exists() else {}
    service = NpcDialogueService(**service_kwargs)
    contexts = service.get_context_options("zhang-fei")
    keywords = service.get_keyword_options("zhang-fei")
    persona = service.get_persona_card("zhang-fei")
    selected_keyword_keys = [
        option.keywordKey
        for category in ["person", "item", "event"]
        for option in (keywords.categories.get(category) or [])[:1]
    ]
    response = service.build_dialogue(
        DialogueRequest(
            generalId="zhang-fei",
            contextKey=contexts.options[0].contextKey if contexts.options else None,
            selectedKeywordKeys=selected_keyword_keys + ["unknown-key"],
            locale="zh-TW",
            speechContextMode="inner_monologue",
            llmModelPreset="fallback_chain",
            maxChars=90,
        )
    )
    label_fallback_service = NpcDialogueService()
    label_fallback_service._roster_name_for = lambda target_id, roster_index: target_id  # type: ignore[method-assign]
    fallback_targets = label_fallback_service._build_interaction_targets(
        "liu-bei",
        label_fallback_service.store.read_runtime_persona("liu-bei") or {},
        label_fallback_service.store.read_runtime_keywords("liu-bei") or {},
        label_fallback_service.store.read_runtime_relationships("liu-bei") or {},
        {},
    )
    fallback_target_labels = {target.targetId: target.label for target in fallback_targets}
    resolved_scene = service.build_scene_director(
        SceneDirectorRequest(
            generalId="liu-bei",
            angle="people",
            targetId="zhang-fei",
            llmModelPreset="fallback_chain",
            renderMode="data_first",
        )
    )
    invalid_scene = service.build_scene_director(
        SceneDirectorRequest(
            generalId="liu-bei",
            angle="emotion",
            targetId="not-real",
            llmModelPreset="fallback_chain",
            renderMode="data_first",
        )
    )
    if workspace_runtime_root.exists():
        liu_bei_profile = service.get_narrative_profile("liu-bei")
        liu_bei_targets = {target.targetId: target for target in liu_bei_profile.interactionTargets}
        liu_bei_scene_matrix = {
            ("sun-shang-xiang", "emotion"): service.build_scene_director(
                SceneDirectorRequest(
                    generalId="liu-bei",
                    angle="emotion",
                    targetId="sun-shang-xiang",
                    llmModelPreset="fallback_chain",
                    renderMode="data_first",
                )
            ),
            ("guan-yu", "people"): service.build_scene_director(
                SceneDirectorRequest(
                    generalId="liu-bei",
                    angle="people",
                    targetId="guan-yu",
                    llmModelPreset="fallback_chain",
                    renderMode="data_first",
                )
            ),
            ("liu-shan", "people"): service.build_scene_director(
                SceneDirectorRequest(
                    generalId="liu-bei",
                    angle="people",
                    targetId="liu-shan",
                    llmModelPreset="fallback_chain",
                    renderMode="data_first",
                )
            ),
            ("zhao-yun", "relationship"): service.build_scene_director(
                SceneDirectorRequest(
                    generalId="liu-bei",
                    angle="relationship",
                    targetId="zhao-yun",
                    llmModelPreset="fallback_chain",
                    renderMode="data_first",
                )
            ),
            ("sun-qian", "people"): service.build_scene_director(
                SceneDirectorRequest(
                    generalId="liu-bei",
                    angle="people",
                    targetId="sun-qian",
                    llmModelPreset="fallback_chain",
                    renderMode="data_first",
                )
            ),
            ("wei-yan", "people"): service.build_scene_director(
                SceneDirectorRequest(
                    generalId="liu-bei",
                    angle="people",
                    targetId="wei-yan",
                    llmModelPreset="fallback_chain",
                    renderMode="data_first",
                )
            ),
        }
        # Canonical output must keep one card per (angle, relatedTargetId) pair and only merge sourceRefs.
        pair_keys = [
            (card.angle, target_id)
            for card in liu_bei_profile.evidenceCards
            for target_id in card.relatedTargetIds
        ]
        assert len(pair_keys) == len(set(pair_keys)), "liu-bei narrative profile should not duplicate angle/target pairs"
        assert len({card.evidenceId for card in liu_bei_profile.evidenceCards}) == len(
            liu_bei_profile.evidenceCards
        ), "narrative profile evidence IDs should remain unique"
        sun_shang_xiang = liu_bei_targets.get("sun-shang-xiang")
        assert sun_shang_xiang is not None, "liu-bei narrative profile should keep sun-shang-xiang as an interaction target"
        assert sun_shang_xiang.sourceType == "relationship-edge", "sun-shang-xiang should be relationship-backed, not mention-only"
        assert sun_shang_xiang.relationshipType == "spouse", "sun-shang-xiang should retain the stable spouse anchor"
        assert sun_shang_xiang.sceneEligible, "sun-shang-xiang should remain scene-eligible"
        assert sun_shang_xiang.sourceDataStatus == "ready", "sun-shang-xiang should stay ready via stable relationship"
        assert liu_bei_targets.get("liu-shan") is not None, "liu-shan should remain in liu-bei interaction targets"
        assert liu_bei_targets["liu-shan"].sceneEligible, "liu-shan should remain scene-eligible via parent-child anchor"
        assert liu_bei_targets["liu-shan"].relationshipType == "parent_child", "liu-shan should keep the parent-child relationship type"
        assert liu_bei_targets.get("zhang-fei") is not None, "zhang-fei should remain in liu-bei interaction targets"
        assert liu_bei_targets["zhang-fei"].sceneEligible, "zhang-fei should remain scene-eligible"
        assert liu_bei_targets["zhang-fei"].relationshipType == "sworn_sibling", "zhang-fei should resolve to sworn_sibling"
        assert liu_bei_targets.get("sun-qian") is not None, "sun-qian should still surface when backed by non-edge scene evidence"
        assert liu_bei_targets["sun-qian"].sceneEligible, "sun-qian should stay scene-eligible only when scene evidence exists"
        assert liu_bei_targets["sun-qian"].sourceType == "pipeline-angle-target-link", "sun-qian should no longer be ready from relationship-edge alone"
        assert liu_bei_targets.get("zhao-yun") is not None, "zhao-yun should still be visible in target projections for diagnostics"
        assert not liu_bei_targets["zhao-yun"].sceneEligible, "zhao-yun should not be scene-eligible from relationship-edge alone"
        assert liu_bei_targets["zhao-yun"].sourceDataStatus == "insufficient_source_data", "zhao-yun should be downgraded when no playable card exists"
        assert liu_bei_targets.get("wei-yan") is not None, "wei-yan should still be visible in target projections for diagnostics"
        assert not liu_bei_targets["wei-yan"].sceneEligible, "wei-yan should not remain scene-eligible without playable evidence cards"
        assert liu_bei_targets["wei-yan"].sourceDataStatus == "insufficient_source_data", "wei-yan should be downgraded when no playable card exists"
        assert "guan-yu" not in liu_bei_targets, "guan-yu should not be exposed when the runtime profile provides no playable target pair"
        assert all(
            target.sourceType != "source-text-mention" for target in liu_bei_profile.interactionTargets
        ), "top interaction targets should not be filled by incidental source text mentions"
        assert liu_bei_scene_matrix[("sun-shang-xiang", "emotion")].dataStatus == "direct", "sun-shang-xiang emotion scene should resolve directly"
        assert not liu_bei_scene_matrix[("sun-shang-xiang", "emotion")].isEmpty, "sun-shang-xiang emotion scene should not be empty"
        assert liu_bei_scene_matrix[("liu-shan", "people")].dataStatus == "direct", "liu-shan people scene should resolve directly"
        assert not liu_bei_scene_matrix[("liu-shan", "people")].isEmpty, "liu-shan people scene should not be empty"
        assert liu_bei_scene_matrix[("sun-qian", "people")].dataStatus == "direct", "sun-qian should stay playable only when a direct card exists"
        assert not liu_bei_scene_matrix[("sun-qian", "people")].isEmpty, "sun-qian should not be empty when it is kept by scene evidence"
        assert liu_bei_scene_matrix[("zhao-yun", "relationship")].dataStatus == "invalid_request", "zhao-yun should be rejected once the target is demoted"
        assert liu_bei_scene_matrix[("zhao-yun", "relationship")].isEmpty, "zhao-yun demotion should produce an empty scene response"
        assert liu_bei_scene_matrix[("wei-yan", "people")].dataStatus == "invalid_request", "wei-yan should be rejected once the target is demoted"
        assert liu_bei_scene_matrix[("wei-yan", "people")].isEmpty, "wei-yan demotion should produce an empty scene response"
        assert liu_bei_scene_matrix[("guan-yu", "people")].dataStatus == "invalid_request", "guan-yu should be rejected when the pair is absent from the runtime profile"
        assert liu_bei_scene_matrix[("guan-yu", "people")].isEmpty, "guan-yu should not synthesize a scene when no target exists"

    assert contexts.options, "context options should not be empty"
    assert keywords.categories.get("person"), "person keyword options should not be empty"
    assert persona is not None, "zhang-fei persona card should be available before LLM integration"
    assert response.evidenceRefs, "dialogue response should include evidence refs"
    assert response.usedEvidenceRefs, "dialogue response should resolve evidence refs"
    assert response.generationMode.endswith("persona-card"), "dialogue response should report persona-card mode"
    assert response.locale == "zh-TW", "dialogue response should echo locale"
    assert response.speechContextMode == "inner_monologue", "dialogue response should echo speech context mode"
    assert response.llmModelPreset == "fallback_chain", "dialogue response should echo model preset"
    assert response.rejectedKeywordKeys == ["unknown-key"], "unknown keyword should be rejected explicitly"
    assert fallback_target_labels.get("zhang-fei") == "張飛", "target labels should preserve human-readable names even when roster names are slugs"
    assert "??" not in persona.safeFallbackLine, "zhang-fei fallback line must not use guan-yu self-name"
    assert resolved_scene.dataStatus in {"direct", "angle_empty_filled", "target_empty_filled"}, "liu-bei/zhang-fei scene should resolve to a non-empty scene"
    assert not resolved_scene.isEmpty, "liu-bei/zhang-fei scene should produce non-empty director beats"
    assert resolved_scene.beats.sceneText or resolved_scene.storyText, "resolved scene should include at least one grounded narrative field"
    assert invalid_scene.dataStatus == "invalid_request", "invalid target should be rejected explicitly"
    assert invalid_scene.isEmpty, "invalid target should not produce scene content"
    assert invalid_scene.beats.sceneText == "", "invalid target should not invent scene text"
    assert invalid_scene.storyText == "", "invalid target should not call story LLM"
    print("[npc-brain-smoke] PASS")
    print(f"[npc-brain-smoke] contexts={len(contexts.options)} categories={len(keywords.categories)} evidenceRefs={len(response.evidenceRefs)}")
    print("[npc-brain-smoke] text=" + json.dumps(response.text, ensure_ascii=True))


if __name__ == "__main__":
    main()
