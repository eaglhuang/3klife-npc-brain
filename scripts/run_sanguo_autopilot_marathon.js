#!/usr/bin/env node

const fs = require('fs');
const path = require('path');
const { spawnSync } = require('child_process');

const repoRoot = path.resolve(__dirname, '..');
const controllerPath = path.join(repoRoot, 'pipelines', 'sanguo-rag', 'run_sanguo_autopilot_marathon.py');
const defaultMaxRounds = '5';

function pickPythonExecutable() {
  const candidates = [
    process.env.PYTHON && process.env.PYTHON.trim(),
    path.join(repoRoot, '.venv', 'Scripts', 'python.exe'),
    path.join(repoRoot, '.venv', 'Scripts', 'python'),
    'python',
  ].filter(Boolean);

  for (const candidate of candidates) {
    if (path.isAbsolute(candidate)) {
      if (fs.existsSync(candidate)) {
        return candidate;
      }
      continue;
    }
    return candidate;
  }

  return 'python';
}

function main(argv) {
  const pythonExecutable = pickPythonExecutable();
  const helpRequested = argv.includes('--help') || argv.includes('-h');
  if (helpRequested) {
    const helpResult = spawnSync(pythonExecutable, [controllerPath, '--help'], {
      cwd: repoRoot,
      stdio: 'inherit',
      windowsHide: true,
    });
    return typeof helpResult.status === 'number' ? helpResult.status : 1;
  }

  const allowApply = !argv.includes('--no-allow-apply');
  const passthroughArgs = argv.filter((arg) => arg !== '--no-allow-apply');
  const defaultArgs = [
    '--resume',
    '--overwrite',
    '--advance-source-ref-window',
    '--max-rounds', defaultMaxRounds,
  ];

  if (allowApply) {
    defaultArgs.push('--allow-apply', '--apply-bucket', 'propose-lane');
  }

  const result = spawnSync(pythonExecutable, [controllerPath, ...defaultArgs, ...passthroughArgs], {
    cwd: repoRoot,
    stdio: 'inherit',
    windowsHide: true,
  });

  if (result.error) {
    console.error(`[run_sanguo_autopilot_marathon] failed to launch: ${result.error.message}`);
    return 1;
  }

  return typeof result.status === 'number' ? result.status : 1;
}

process.exit(main(process.argv.slice(2)));