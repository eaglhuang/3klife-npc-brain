import { existsSync, readFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const repositoryRoot = path.resolve(here, '../../../..');
const planPath = path.join(repositoryRoot, '.atm/history/reports/decomposition-plan.full-roster-convergence-v1.json');

function buildOutput(input) {
  return {
    stage: String(input?.stage || '').trim(),
    legacyUri: String(input?.legacyUri || '').trim(),
    atomId: String(input?.expectedAtomId || '').trim(),
    role: String(input?.expectedRole || '').trim(),
    legacyPath: String(input?.legacyUri || '').trim().replace(/^legacy:\/\//, '').split('#')[0],
    replacementCoverage: 'declared',
    contractMode: 'architecture-level'
  };
}

export async function run(input) {
  const plan = JSON.parse(readFileSync(planPath, 'utf8'));
  const output = buildOutput(input);
  if (!output.legacyUri) {
    throw new Error('legacyUri is required.');
  }
  if (!output.atomId) {
    throw new Error('expectedAtomId is required.');
  }
  const legacyUriPresent = Array.isArray(plan?.legacyUris) && plan.legacyUris.includes(output.legacyUri);
  if (!legacyUriPresent) {
    throw new Error(`decomposition plan is missing legacyUri ${output.legacyUri}`);
  }
  const member = Array.isArray(plan?.proposedMembers)
    ? plan.proposedMembers.find((entry) => String(entry?.atomId || '').trim() === output.atomId)
    : null;
  if (!member) {
    throw new Error(`decomposition plan is missing member ${output.atomId}`);
  }
  const absoluteLegacyPath = path.join(repositoryRoot, output.legacyPath.replace(/\//g, path.sep));
  if (!existsSync(absoluteLegacyPath)) {
    throw new Error(`legacy file not found: ${output.legacyPath}`);
  }
  return output;
}