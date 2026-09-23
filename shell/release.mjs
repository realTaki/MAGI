/** Pick a GitHub Release installer for this shell and describe how to swap it in. */

export function githubRepo(repositoryUrl) {
  const match = /github\.com[:/]([^/]+)\/([^/#?\s]+)/.exec(repositoryUrl);
  if (match === null) {
    return null;
  }
  const name = match[2].replace(/\.git$/, "");
  if (match[1] === "" || name === "") {
    return null;
  }
  return { owner: match[1], name };
}

export function releaseVersion(tag) {
  return tag.replace(/^v/, "");
}

export function compareVersions(left, right) {
  const parsed = (value) => {
    const [core, pre = ""] = releaseVersion(value).split(/-(.*)/s);
    const numbers = core.split(".").map((part) => {
      const number = Number(part);
      return Number.isInteger(number) ? number : 0;
    });
    while (numbers.length < 3) {
      numbers.push(0);
    }
    return { numbers: numbers.slice(0, 3), pre };
  };
  const a = parsed(left);
  const b = parsed(right);
  for (let index = 0; index < 3; index += 1) {
    if (a.numbers[index] !== b.numbers[index]) {
      return a.numbers[index] < b.numbers[index] ? -1 : 1;
    }
  }
  if (a.pre === b.pre) {
    return 0;
  }
  if (a.pre === "") {
    return 1;
  }
  if (b.pre === "") {
    return -1;
  }
  return a.pre < b.pre ? -1 : 1;
}

export function releaseAsset(assets, platform, arch) {
  const extension = platform === "darwin" ? ".dmg" : platform === "win32" ? ".exe" : ".AppImage";
  const installers = assets.filter(
    (asset) => asset.name.endsWith(extension) && !asset.name.includes(".blockmap"),
  );
  return (
    installers.find((asset) => asset.name.includes(`-${arch}${extension}`)) ??
    (installers.length === 1 ? installers[0] : null)
  );
}

export function releaseDownloadUrl(repositoryUrl, assetUrl) {
  const repo = githubRepo(repositoryUrl);
  if (repo === null) {
    return false;
  }
  const prefix = `https://github.com/${repo.owner}/${repo.name}/releases/download/`;
  return assetUrl.startsWith(prefix);
}

export function shellQuote(value) {
  return `'${value.replaceAll("'", `'\\''`)}'`;
}

export function updateScript({ platform, pid, installer, mount, appBundle, stagedApp, nextBundle }) {
  if (!Number.isInteger(pid) || pid <= 0) {
    throw new Error("shell update needs the running process id");
  }
  if (platform === "darwin") {
    return `#!/bin/sh
set -eu
while kill -0 ${pid} 2>/dev/null; do
  sleep 0.3
done
hdiutil detach ${shellQuote(mount)} >/dev/null 2>&1 || true
rm -rf ${shellQuote(nextBundle)}
mkdir -p ${shellQuote(mount)}
hdiutil attach -nobrowse -mountpoint ${shellQuote(mount)} ${shellQuote(installer)}
cp -R ${shellQuote(stagedApp)} ${shellQuote(nextBundle)}
hdiutil detach ${shellQuote(mount)}
rm -rf ${shellQuote(appBundle)}
mv ${shellQuote(nextBundle)} ${shellQuote(appBundle)}
open ${shellQuote(appBundle)}
`;
  }
  if (platform === "win32") {
    return `@echo off
:wait
tasklist /FI "PID eq ${pid}" | find "${pid}" >nul
if %ERRORLEVEL%==0 (
  timeout /t 1 /nobreak >nul
  goto wait
)
"${installer}" /S
start "" "${appBundle}"
`;
  }
  return `#!/bin/sh
set -eu
while kill -0 ${pid} 2>/dev/null; do
  sleep 0.3
done
cp ${shellQuote(installer)} ${shellQuote(appBundle)}
chmod +x ${shellQuote(appBundle)}
${shellQuote(appBundle)}
`;
}
