import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const spec = JSON.parse(readFileSync(new URL('./map.spec.json', import.meta.url), 'utf8'));
assert.equal(spec.schemaId, "atm.atomicMap");
assert.equal(spec.mapId, "ATM-MAP-0001");
assert.equal(spec.mapHash, "sha256:45c9cd97dd908d33e203d29f374eaa95b3ce9315cadda80fb8fb4998d29dd6bd");
assert.equal(spec.semanticFingerprint, "sf:sha256:c0dbe7bd328e40146c080b8a8bf3dd1116adfa6372d1feda9eb5b226d7126e1d");
assert.deepEqual(spec.entrypoints, ["ATM-NPCBRAIN-0001"]);
assert.deepEqual(spec.members, [{"atomId":"ATM-NPCBRAIN-0001","version":"0.1.0","role":"entry-adapter"},{"atomId":"ATM-NPCBRAIN-0002","version":"0.1.0","role":"domain-step"},{"atomId":"ATM-NPCBRAIN-0003","version":"0.1.0","role":"domain-step"},{"atomId":"ATM-NPCBRAIN-0004","version":"0.1.0","role":"domain-step"},{"atomId":"ATM-NPCBRAIN-0005","version":"0.1.0","role":"validator"},{"atomId":"ATM-NPCBRAIN-0006","version":"0.1.0","role":"side-effect"},{"atomId":"ATM-NPCBRAIN-0007","version":"0.1.0","role":"rollback-adapter"}]);
assert.deepEqual(spec.edges, [{"from":"ATM-NPCBRAIN-0001","to":"ATM-NPCBRAIN-0002","binding":"seed-pipeline","edgeKind":"control-flow"},{"from":"ATM-NPCBRAIN-0002","to":"ATM-NPCBRAIN-0003","binding":"external-summary","edgeKind":"data-flow"},{"from":"ATM-NPCBRAIN-0003","to":"ATM-NPCBRAIN-0004","binding":"precision-selection","edgeKind":"control-flow"},{"from":"ATM-NPCBRAIN-0004","to":"ATM-NPCBRAIN-0005","binding":"round-summary","edgeKind":"validation"},{"from":"ATM-NPCBRAIN-0005","to":"ATM-NPCBRAIN-0006","binding":"artifact-write","edgeKind":"side-effect"},{"from":"ATM-NPCBRAIN-0006","to":"ATM-NPCBRAIN-0007","binding":"rollback-snapshot","edgeKind":"rollback"}]);
assert.deepEqual(spec.qualityTargets, {"equivalenceFixtures":"full-roster-convergence","promoteGateRequired":true,"requiredChecks":2,"reviewAdvisoryRequired":true});
console.log("ATM-MAP-0001 map integration self-check ok");
