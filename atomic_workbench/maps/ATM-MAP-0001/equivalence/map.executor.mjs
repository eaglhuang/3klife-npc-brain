import { readFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const specPath = path.resolve(here, '../map.spec.json');

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
  const spec = JSON.parse(readFileSync(specPath, 'utf8'));
  const output = buildOutput(input);
  if (!output.legacyUri) {
    throw new Error('legacyUri is required.');
  }
  if (!output.atomId) {
    throw new Error('expectedAtomId is required.');
  }
  const legacyUriPresent = Array.isArray(spec?.replacement?.legacyUris) && spec.replacement.legacyUris.includes(output.legacyUri);
  if (!legacyUriPresent) {
    throw new Error(`map spec is missing legacyUri ${output.legacyUri}`);
  }
  const member = Array.isArray(spec?.members)
    ? spec.members.find((entry) => String(entry?.atomId || '').trim() === output.atomId)
    : null;
  if (!member) {
    throw new Error(`map spec is missing member ${output.atomId}`);
  }
  return output;
}