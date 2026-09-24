import { createWriteStream } from "node:fs";
import path from "node:path";
import { Readable } from "node:stream";
import { pipeline } from "node:stream/promises";

export function githubRepo(repositoryUrl) {
  const match = /github\.com[:/]([^/]+)\/([^/#?\s]+)/.exec(repositoryUrl);
  if (match === null) return null;
  const name = match[2].replace(/\.git$/, "");
  return match[1] === "" || name === "" ? null : { owner: match[1], name };
}

export function releaseVersion(tag) {
  return tag.replace(/^v/, "");
}

export function compareVersions(left, right) {
  const parsed = (value) => {
    const [core, pre = ""] = releaseVersion(value).split(/-(.*)/s);
    const numbers = core.split(".").map((part) => Number.isInteger(Number(part)) ? Number(part) : 0);
    while (numbers.length < 3) numbers.push(0);
    return { numbers: numbers.slice(0, 3), pre };
  };
  const a = parsed(left);
  const b = parsed(right);
  for (let index = 0; index < 3; index += 1) {
    if (a.numbers[index] !== b.numbers[index]) return a.numbers[index] < b.numbers[index] ? -1 : 1;
  }
  if (a.pre === b.pre) return 0;
  if (a.pre === "") return 1;
  if (b.pre === "") return -1;
  return a.pre < b.pre ? -1 : 1;
}

export function releaseAsset(assets, platform, arch) {
  const extension = platform === "darwin" ? ".dmg" : platform === "win32" ? ".exe" : ".AppImage";
  const installers = assets.filter((asset) => asset.name.endsWith(extension) && !asset.name.includes(".blockmap"));
  return installers.find((asset) => asset.name.includes(`-${arch}${extension}`)) ?? (installers.length === 1 ? installers[0] : null);
}

export async function shellRelease({ repository, currentVersion, packaged, platform, arch, fetchImpl = fetch }) {
  const blank = { packaged, currentVersion, latestVersion: "", latestTag: "", updateAvailable: false, assetName: "", assetUrl: "", releaseUrl: "", error: "", reason: "" };
  const repo = githubRepo(repository);
  if (repo === null) return { ...blank, reason: "unavailable", error: "The configured source is not a GitHub repository." };
  let payload;
  try {
    const response = await fetchImpl(`https://api.github.com/repos/${repo.owner}/${repo.name}/releases/latest`, { headers: { Accept: "application/vnd.github+json", "User-Agent": "MAGI" } });
    if (response.status === 404) return { ...blank, reason: "no-release" };
    if (!response.ok) return { ...blank, reason: "unavailable", error: `GitHub releases answered ${response.status}.` };
    payload = await response.json();
  } catch (error) {
    return { ...blank, reason: "unavailable", error: error instanceof Error ? error.message : String(error) };
  }
  const tag = typeof payload.tag_name === "string" ? payload.tag_name : "";
  const assets = Array.isArray(payload.assets)
    ? payload.assets.flatMap((asset) => typeof asset?.name === "string" && typeof asset?.browser_download_url === "string" ? [{ name: asset.name, browser_download_url: asset.browser_download_url }] : [])
    : [];
  const asset = releaseAsset(assets, platform, arch);
  const latestVersion = releaseVersion(tag);
  const newer = latestVersion !== "" && compareVersions(currentVersion, latestVersion) < 0;
  return { ...blank, latestVersion, latestTag: tag, updateAvailable: newer && asset !== null, assetName: asset?.name ?? "", assetUrl: asset?.browser_download_url ?? "", releaseUrl: typeof payload.html_url === "string" ? payload.html_url : "", reason: newer && asset === null ? "no-asset" : "" };
}

export async function downloadShellInstaller(url, destination, fetchImpl = fetch) {
  const response = await fetchImpl(url, { headers: { Accept: "application/octet-stream", "User-Agent": "MAGI" }, redirect: "follow" });
  if (!response.ok || response.body === null) throw new Error(`Could not download the client installer (${response.status}).`);
  await pipeline(Readable.fromWeb(response.body), createWriteStream(destination));
  return destination;
}

export function installerPath(directory, name) {
  return path.join(directory, path.basename(name));
}
