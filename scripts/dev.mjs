// One launcher, no Electron/Tauri and no auto-downloading runtime dependencies.
import { spawn, spawnSync } from 'node:child_process';
import { existsSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import process from 'node:process';
import net from 'node:net';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const windows = process.platform === 'win32';
const venv = path.join(root, '.venv', windows ? 'Scripts/python.exe' : 'bin/python');
const python = process.env.GTA_PYTHON || (existsSync(venv) ? venv : windows ? 'python' : 'python3');
const production = process.argv.includes('--production');
const vite = path.join(root, 'frontend/node_modules/vite/bin/vite.js');
const check = spawnSync(python, ['-c', 'import fastapi, uvicorn, numpy, psutil, multipart, httpx, jwt; from jwt.algorithms import RSAAlgorithm'], { stdio: 'inherit', cwd: root });
if (check.status !== 0) {
  console.error('Install the Python dependencies first: python -m pip install -r requirements.txt');
  process.exit(1);
}
if (!production && !existsSync(vite)) {
  console.error('Install frontend dependencies first: npm install');
  process.exit(1);
}
if (production && !existsSync(path.join(root, 'frontend/dist/index.html'))) {
  console.error('Build the production interface first: npm run build');
  process.exit(1);
}
async function portAvailable(port) {
  return new Promise((resolve) => {
    const server = net.createServer();
    server.once('error', () => resolve(false));
    server.listen(port, '127.0.0.1', () => server.close(() => resolve(true)));
  });
}
for (const port of production ? [8000] : [8000, 5173]) {
  if (!await portAvailable(port)) {
    console.error(`Port ${port} is already in use. Stop the other server before launching.`);
    process.exit(1);
  }
}
const children = [];
let stopping = false;
function stop(code = 0) {
  if (stopping) return;
  stopping = true;
  // Windows has no portable Node signal for graceful console shutdown.
  // Killing the tree is recoverable: SQLite jobs requeue and atomic chunks
  // survive; backend-owned Job Objects prevent orphaned FFmpeg processes.
  for (const child of children) {
    if (!child.pid) continue;
    if (windows) {
      spawnSync('taskkill', ['/PID', String(child.pid), '/T', '/F'], { stdio: 'ignore', windowsHide: true });
    } else child.kill('SIGTERM');
  }
  process.exitCode = code;
}
function launch(executable, args, cwd) {
  const child = spawn(executable, args, { cwd, stdio: 'inherit', windowsHide: true });
  children.push(child);
  child.on('error', (error) => { console.error(error.message); stop(1); });
  child.on('exit', (code) => { if (!stopping) stop(code ?? 1); });
}
launch(python, ['-m', 'uvicorn', 'backend.main:app', '--host', '127.0.0.1', '--port', '8000', '--workers', '1'], root);
if (!production) launch(process.execPath, [vite, '--host', '127.0.0.1', '--port', '5173', '--strictPort'], path.join(root, 'frontend'));
console.log(`\nGTA Clip Studio → http://127.0.0.1:${production ? '8000' : '5173'}\nHeavy processing is serialized; completed scan chunks survive restarts.\n`);
process.on('SIGINT', () => stop());
process.on('SIGTERM', () => stop());
