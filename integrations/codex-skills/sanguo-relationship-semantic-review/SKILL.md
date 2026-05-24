---
name: sanguo-relationship-semantic-review
description: Use when reviewing Sanguo relationship semantic review packets exported by run_codex_relationship_semantic_review_bridge.py. Produces source-grounded JSONL cache rows for relationship trust-zone evidence when local models are too weak or unavailable.
---

# Sanguo Relationship Semantic Review

Use this skill only for exported relationship semantic review packets. It is a temporary Codex-side reviewer that can be replaced later by a stronger local or remote sentence-level model.

## Contract

- Input is a JSON packet exported by `pipelines/sanguo-rag/run_codex_relationship_semantic_review_bridge.py --mode export`.
- Output must be JSONL, one reviewed cache row per input unit.
- Keep `canonicalWrites=false`.
- Do not invent facts from memory. Judge only whether the preserved `sourceSentence` explicitly supports one of the candidate pair-key relationships.
- Deterministic extractor output is only a narrowing signal. The source sentence is the truth surface.
- If the sentence is ambiguous, uses pronouns that cannot be resolved from `allowedEntities`, or only mentions two names without a relationship cue, return `not_enough_context`.

## Review Steps

1. Read `sourceSentence` first.
2. Identify explicit people and aliases using only `allowedEntities`.
3. Extract relationship facts from the sentence, if any.
4. Map extracted facts back to candidate `trustKey` values.
5. For each candidate, emit one relationship decision.

## Output Row Shape

Each JSONL row must preserve the input unit fields and add review fields:

```json
{
  "semanticReviewUnitId": "string",
  "promptVersion": "string",
  "sentenceHash": "string",
  "sourceSentence": "string",
  "sourceRefs": [],
  "allowedEntities": [],
  "candidates": [],
  "reviewedAt": "ISO-8601 UTC",
  "reviewer": {
    "provider": "codex-skill",
    "preset": "sanguo-relationship-semantic-review",
    "model": "codex",
    "apiUrl": null
  },
  "reviewMode": "sentence-relation-extraction",
  "semanticReviewPerformed": true,
  "reviewedCandidateKeys": ["trustKey"],
  "relationships": [
    {
      "trustKey": "string",
      "relationshipType": "ruler_subject|spouse|parent_child|sibling|sworn_sibling|enemy_rival|...",
      "fromId": "string",
      "toId": "string",
      "verdict": "supported|contradicted|uncertain|not_enough_context",
      "semanticTrustScore": 0,
      "confidence": 0,
      "typeMatched": false,
      "directionMatched": false,
      "matchedFromNameZhTw": "",
      "matchedToNameZhTw": "",
      "fromEvidenceSpanZhTw": "",
      "toEvidenceSpanZhTw": "",
      "relationshipCueSpanZhTw": "",
      "cueCategory": "",
      "polarity": "",
      "mismatchReasonZhTw": "",
      "evidenceSentence": "source sentence excerpt",
      "rationaleZhTw": "繁體中文簡短理由",
      "normalizedClaimZhTw": "",
      "stableRelation": false,
      "reviewMode": "sentence-relation-extraction",
      "canonicalWrites": false
    }
  ],
  "extractedRelationships": [],
  "rawReviewerSummary": {
    "reviewer": "codex-skill",
    "notesZhTw": "繁體中文摘要"
  },
  "canonicalWrites": false
}
```

## Scoring

- `90-100`: sentence directly states the exact pair and relationship type; direction is clear.
- `80-89`: sentence supports the relationship, but wording is slightly indirect.
- `50-79`: some related signal exists, but not enough for stable trust.
- `0-49`: unsupported, contradicted, or not enough context.

Only `supported` with `typeMatched=true`, `directionMatched=true`, high confidence, and explicit sentence evidence may later enter skill review. Human review still controls final 100-point lock.
