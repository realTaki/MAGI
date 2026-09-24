export function shellQuote(value) {
  return `'${value.replaceAll("'", `'\\''`)}'`;
}

export function updateScript({ platform, pid, installer, mount, appBundle, stagedApp, nextBundle }) {
  if (!Number.isInteger(pid) || pid <= 0) throw new Error("shell update needs the running process id");
  if (platform === "darwin") return `#!/bin/sh\nset -eu\nwhile kill -0 ${pid} 2>/dev/null; do\n  sleep 0.3\ndone\nhdiutil detach ${shellQuote(mount)} >/dev/null 2>&1 || true\nrm -rf ${shellQuote(nextBundle)}\nmkdir -p ${shellQuote(mount)}\nhdiutil attach -nobrowse -mountpoint ${shellQuote(mount)} ${shellQuote(installer)}\ncp -R ${shellQuote(stagedApp)} ${shellQuote(nextBundle)}\nhdiutil detach ${shellQuote(mount)}\nrm -rf ${shellQuote(appBundle)}\nmv ${shellQuote(nextBundle)} ${shellQuote(appBundle)}\nopen ${shellQuote(appBundle)}\n`;
  if (platform === "win32") return `@echo off\n:wait\ntasklist /FI "PID eq ${pid}" | find "${pid}" >nul\nif %ERRORLEVEL%==0 (\n  timeout /t 1 /nobreak >nul\n  goto wait\n)\n"${installer}" /S\nstart "" "${appBundle}"\n`;
  return `#!/bin/sh\nset -eu\nwhile kill -0 ${pid} 2>/dev/null; do\n  sleep 0.3\ndone\ncp ${shellQuote(installer)} ${shellQuote(appBundle)}\nchmod +x ${shellQuote(appBundle)}\n${shellQuote(appBundle)}\n`;
}
