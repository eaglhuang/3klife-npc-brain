import { readFileSync } from 'node:fs';

function readStdin() {
  return new Promise((resolve, reject) => {
    const chunks = [];
    process.stdin.on('data', (chunk) => chunks.push(chunk));
    process.stdin.on('end', () => resolve(Buffer.concat(chunks).toString('utf8')));
    process.stdin.on('error', reject);
  });
}

function includesAny(text, patterns) {
  return patterns.some((pattern) => pattern.test(text));
}

function shouldRoute(prompt) {
  const text = String(prompt || '').trim();
  if (!text) {
    return false;
  }

  const pipelineScope = [
    /python/i,
    /pipeline/i,
    /pipelines\//i,
    /資料管線/i,
    /管線/i,
    /sanguo-rag/i,
    /腳本/i,
    /script/i,
    /repo/i
  ];

  const triageIntent = [
    /最亂/i,
    /messiest/i,
    /值得先整理/i,
    /先整理/i,
    /排一下優先/i,
    /priority/i,
    /prioriti[sz]e/i,
    /progress/i,
    /進度/i,
    /卡在/i,
    /refactor/i,
    /cleanup/i,
    /technical debt/i,
    /技術債/i,
    /split/i,
    /atomize/i,
    /infect/i,
    /compose/i
  ];

  const explicitAtm = [/\bATM\b/i, /atm\.mjs/i, /governance/i, /治理/i];

  return (includesAny(text, pipelineScope) && includesAny(text, triageIntent)) || includesAny(text, explicitAtm);
}

function buildAdditionalContext(prompt) {
  return [
    'ATM governance router matched this user prompt.',
    'Treat the original user prompt as the canonical goal. Preserve it exactly when passing --goal.',
    'Before broad repo exploration, run node atm.mjs next --json.',
    `Then run node atm.mjs guide --goal ${JSON.stringify(prompt)} --cwd . --json.`,
    `For Python pipeline ranking, refactor priority, cleanup ordering, or pipeline progress triage, run node atm.mjs candidates rank --include "pipelines/**/*.py" --goal ${JSON.stringify(prompt)} --json even if guide classification drifts.`,
    'Use ATM-generated evidence before ad hoc scanning.',
    'In the final answer, cite the candidate ranking report, source inventory report, police-family report, and guidance-drift-police report paths.'
  ].join('\n');
}

try {
  const raw = await readStdin();
  const payload = raw.trim() ? JSON.parse(raw) : {};
  const prompt = String(payload.prompt || '');

  if (!shouldRoute(prompt)) {
    process.exit(0);
  }

  const output = {
    hookSpecificOutput: {
      hookEventName: 'UserPromptSubmit',
      additionalContext: buildAdditionalContext(prompt)
    }
  };

  process.stdout.write(JSON.stringify(output));
} catch (error) {
  process.stderr.write(`ATM prompt router hook failed: ${error instanceof Error ? error.message : String(error)}`);
  process.exit(1);
}
