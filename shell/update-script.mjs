export function shellQuote(value) {
  return `'${value.replaceAll("'", `'\\''`)}'`;
}

function waitForExit(pid) {
  return ["while kill -0 " + pid + " 2>/dev/null; do", "  sleep 0.3", "done"];
}

export function updateScript({ platform, pid, installer, mount, appBundle, stagedApp, nextBundle }) {
  if (!Number.isInteger(pid) || pid <= 0) {
    throw new Error("shell update needs the running process id");
  }
  if (platform === "darwin") {
    return [
      "#!/bin/sh",
      "set -eu",
      ...waitForExit(pid),
      "hdiutil detach " + shellQuote(mount) + " >/dev/null 2>&1 || true",
      "rm -rf " + shellQuote(nextBundle),
      "mkdir -p " + shellQuote(mount),
      "hdiutil attach -nobrowse -mountpoint " + shellQuote(mount) + " " + shellQuote(installer),
      "cp -R " + shellQuote(stagedApp) + " " + shellQuote(nextBundle),
      "hdiutil detach " + shellQuote(mount),
      "rm -rf " + shellQuote(appBundle),
      "mv " + shellQuote(nextBundle) + " " + shellQuote(appBundle),
      "open " + shellQuote(appBundle),
      "",
    ].join("\n");
  }
  if (platform === "win32") {
    return [
      "@echo off",
      ":wait",
      "tasklist /FI \"PID eq " + pid + "\" | find \"" + pid + "\" >nul",
      "if %ERRORLEVEL%==0 (",
      "  timeout /t 1 /nobreak >nul",
      "  goto wait",
      ")",
      "\"" + installer + "\" /S",
      "start \"\" \"" + appBundle + "\"",
      "",
    ].join("\n");
  }
  return [
    "#!/bin/sh",
    "set -eu",
    ...waitForExit(pid),
    "cp " + shellQuote(installer) + " " + shellQuote(appBundle),
    "chmod +x " + shellQuote(appBundle),
    shellQuote(appBundle),
    "",
  ].join("\n");
}
