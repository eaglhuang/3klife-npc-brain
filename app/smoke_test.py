from __future__ import annotations

import os
import json

from .npc_dialogue_service import DialogueRequest, NpcDialogueService, SceneDirectorRequest


def main() -> None:
    os.environ["NPC_LLM_PROVIDER_ORDER"] = "deterministic"
    service = NpcDialogueService()
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
    assert "關某" not in persona.safeFallbackLine, "zhang-fei fallback line must not use guan-yu self-name"
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
