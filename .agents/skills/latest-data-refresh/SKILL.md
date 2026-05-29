---
name: latest-data-refresh
description: Refresh or verify the newest deployed data by checking deployment commit, snapshot timestamps, and explicit version markers; invalidate cached results when freshness cannot be proven.
---

# Latest Data Refresh

Use this skill when the user wants the newest available data, wants to know whether a result is stale, or wants cache/version checks before trusting API or artifact output.

## Core rule

Do not assume data is fresh just because a page or API returned successfully. Prove freshness with version evidence.

## Freshness order

1. **Deployment version**
   - Read `/healthz`.
   - Prefer `deployment.renderGitCommit`.
   - Also inspect `deployment.githubSha`, `deployment.buildSha`, and `deployment.deployedAt` when available.
2. **Snapshot version**
   - Inspect `runtimeSnapshots.*.mtime` and `sizeBytes`.
   - Treat newer `mtime` as stronger evidence than browser-visible output.
3. **Payload version**
   - Prefer explicit version fields in the payload, such as:
     - `version`
     - `schemaVersion`
     - `datasetVersion`
     - `snapshotVersion`
     - `promptVersion`
     - `cacheSchemaVersion`
4. **Fallback fingerprint**
   - If no version field exists, use a fallback fingerprint from:
     - source path
     - file `mtime`
     - file size
     - content hash if needed

## What to do

- If a newer deployment commit exists, treat older cached API output as stale until reloaded.
- If a payload version changes, invalidate any cache keyed by the old version.
- If only the browser page changed but API/data versions did not, the front end is stale or reading an old asset.
- If the API changed but the page still looks old, check browser cache, static asset cache, and GitHub Pages / CDN refresh.

## Recommended workflow

1. Check `/healthz`.
2. Compare deployment commit/version.
3. Compare runtime snapshot `mtime` values.
4. Compare payload version markers.
5. If freshness cannot be proven, refresh from source instead of reusing cache.

## Repository-specific notes

- For `npc-brain`, use the health fields added in `app/npc_dialogue_service.py`.
- Canonical `dataVersion` for scheduled redeploys is the repo `git commit SHA`.
- `artifactVersion` is a supplemental content fingerprint emitted by the refresh script.
- When a remote health check exposes `dataVersion`, compare it before reusing any cache.
- For runtime profile and event data, prefer the freshest `mtime` and the newest materialized artifact under `artifacts/data-pipeline/sanguo-rag/extracted/`.
- For remote deployment, assume Render may still be serving an older build until `renderGitCommit` proves otherwise.

## Output format

When you apply this skill, report:

- freshness verdict: `fresh`, `stale-deployment`, `stale-snapshot`, or `stale-cache`
- evidence used: commit, version, `mtime`, size, or hash
- next action: reload, redeploy, refresh artifacts, or clear cache
