import { readFile } from 'node:fs/promises';
import { spawn } from 'node:child_process';
import { once } from 'node:events';

export async function runDesktopCli() {
  const args = process.argv.slice(2);
  const patch = JSON.parse(await readFile(args[args.indexOf('--patch') + 1], 'utf8'));
  const config = patch[0].insert[0].config;
  if (config.profile === 'timeout') return;
  if (config.profile === 'failure') { console.error('Simulated startup failure'); process.exitCode = 1; process.disconnect(); return; }
  if (config.profile === 'slow') await new Promise((done) => setTimeout(done, 150));
  let leafPid;
  if (config.profile === 'tree') {
    const leaf = spawn(process.execPath, ['-e', 'process.on("SIGTERM",()=>{}); process.send("ready"); setInterval(()=>{},1000)'], { stdio: ['ignore', 'inherit', 'inherit', 'ipc'] });
    await once(leaf, 'message');
    leafPid = leaf.pid;
  }
  console.log('dsh web: http://127.0.0.1:12345/?token=do-not-leak#secret');
  process.on('SIGTERM', () => process.exit(0));
  process.send({
    type: 'eva-ready', pid: process.pid, profile: config.profile,
    workspace: config.workspace, home: process.env.DSH_HOME,
    cwd: config.profile === 'wrong-cwd' ? `${config.workspace}/workspace` : config.profile === 'missing-cwd' ? undefined : process.cwd(),
    defaultAgentPreset: config.profile === 'missing-preset' ? undefined : config.profile === 'user-standard' ? 'standard' : patch.find((row) => row.id === 'agent-preset-registry')?.config.default,
    profileDir: `${process.env.DSH_HOME}/profiles/${config.profile}`, origin: 'http://127.0.0.1:12345', workspaceId: 'fake-workspace', leafPid,
    mainSessionId: config.profile === 'nav-missing-main' ? undefined : 'fake-main-session',
    launchUrl: ({
      'nav-origin-mismatch': 'http://127.0.0.1:54321/?token=do-not-leak#eva-main',
      'nav-bad-hash': 'http://127.0.0.1:12345/?token=do-not-leak#wrong',
      'nav-credentials': 'http://user:do-not-leak@127.0.0.1:12345/?token=do-not-leak#eva-main',
      'nav-external-protocol': 'https://127.0.0.1:12345/?token=do-not-leak#eva-main',
      'nav-external-host': 'http://example.com:12345/?token=do-not-leak#eva-main',
      'nav-path': 'http://127.0.0.1:12345/private?token=do-not-leak#eva-main',
    })[config.profile] ?? 'http://127.0.0.1:12345/?token=do-not-leak#eva-main',
  });
}
