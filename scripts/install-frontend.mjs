// Run npm in the actual frontend directory. A relative --prefix from a root
// lifecycle can install the root as a local package and recurse on Windows.
import { spawnSync } from 'node:child_process';
import { existsSync } from 'node:fs';
import path from 'node:path';
import process from 'node:process';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const npmCli = process.env.npm_execpath;
if (!npmCli || !existsSync(npmCli)) {
  console.error('Run this installer through npm install from the project root.');
  process.exit(1);
}
if (process.env.GTA_INSTALLING_FRONTEND === '1') {
  console.error('Recursive frontend installation detected. Check frontend/package.json for a dependency on the root project.');
  process.exit(1);
}
const environment = { ...process.env, GTA_INSTALLING_FRONTEND: '1' };
// npm lifecycle metadata describes the parent package, not the next install.
for (const name of ['npm_config_local_prefix', 'npm_package_json', 'npm_package_name', 'npm_package_version']) {
  delete environment[name];
}
const result = spawnSync(process.execPath, [npmCli, 'install', '--no-audit', '--no-fund'], {
  cwd: path.join(root, 'frontend'), stdio: 'inherit', env: environment,
});
if (result.error) console.error(result.error.message);
process.exitCode = result.status ?? 1;
