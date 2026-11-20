import { spawnSync } from 'node:child_process';
import { existsSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import process from 'node:process';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const venv = path.join(root, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
const python = process.env.GTA_PYTHON || (existsSync(venv) ? venv : process.platform === 'win32' ? 'python' : 'python3');
let result = spawnSync(python, ['-m', 'pytest', 'tests', '-q'], { cwd: root, stdio: 'inherit' });
if (result.error) console.error(result.error.message);
if (result.status !== 0) process.exit(result.status ?? 1);
result = spawnSync(process.execPath, [path.join(root, 'frontend/node_modules/vitest/vitest.mjs'), 'run'], {
  cwd: path.join(root, 'frontend'), stdio: 'inherit',
});
if (result.error) console.error(result.error.message);
process.exitCode = result.status ?? 1;
