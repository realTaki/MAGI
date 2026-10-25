// Runs under the same Electron-in-Node-mode executable as the desktop Host.
// No second Electron application and no dependency on a system Node or dsh CLI.
import { pathToFileURL } from 'node:url';

const [cli, runtimeDir, supportDir, ...args] = process.argv.slice(2);
if (!cli || !runtimeDir || !supportDir || !process.send) throw new Error('eva runner requires a parent IPC channel and bundled runtime paths.');
process.once('disconnect', () => {
  // A crashed/killed controller must not leave an unmanaged Host behind.
  const killGroup = (signal) => {
    if (process.platform !== 'win32') {
      try { process.kill(-process.pid, signal); return; } catch {}
    }
    process.kill(process.pid, signal);
  };
  // Complete tree cleanup even if the Host exits gracefully before its descendants.
  if (process.platform !== 'win32') process.once('exit', () => { killGroup('SIGKILL'); });
  setTimeout(() => killGroup('SIGKILL'), 6000).unref();
  if (process.listenerCount('SIGTERM') > 0) killGroup('SIGTERM');
  else process.exit(1);
});
process.argv = [process.execPath, cli, ...args];
const { runDesktopCli } = await import(pathToFileURL(cli).href);
await runDesktopCli(runtimeDir, supportDir);
