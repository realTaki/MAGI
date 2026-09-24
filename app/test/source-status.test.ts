import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { test } from "node:test";

import { createLocalApi } from "../main/index.ts";

const gitEnv = {
  ...process.env,
  GIT_AUTHOR_NAME: "Magi",
  GIT_AUTHOR_EMAIL: "magi@example.com",
  GIT_COMMITTER_NAME: "Magi",
  GIT_COMMITTER_EMAIL: "magi@example.com",
};

function git(cwd, args) {
  return execFileSync("git", args, { cwd, env: gitEnv, encoding: "utf8" }).trim();
}

test("about reports the running commit and where it forked from AgenticSociety", async (t) => {
  const root = mkdtempSync(path.join(tmpdir(), "magi-source-"));
  const origin = path.join(root, "origin");
  const checkout = path.join(root, "checkout");
  t.after(() => rmSync(root, { recursive: true, force: true }));

  mkdirSync(origin);
  git(origin, ["init", "-b", "main"]);
  writeFileSync(path.join(origin, "README"), "base\n");
  git(origin, ["add", "README"]);
  git(origin, ["commit", "-m", "base"]);
  const forkPoint = git(origin, ["rev-parse", "HEAD"]);
  git(origin, ["tag", "v0.0.7-experiment", forkPoint]);

  git(root, ["clone", "--quiet", origin, checkout]);
  git(checkout, ["remote", "set-url", "origin", "https://github.com/realTaki/MAGI.git"]);
  writeFileSync(path.join(origin, "README"), "remote\n");
  git(origin, ["commit", "-am", "remote"]);
  const latestCommit = git(origin, ["rev-parse", "HEAD"]);

  writeFileSync(path.join(checkout, "local"), "ours\n");
  git(checkout, ["add", "local"]);
  git(checkout, ["commit", "-m", "local"]);
  const commit = git(checkout, ["rev-parse", "HEAD"]);
  // The checkout's origin is a local path. Rewrite the AgenticSociety URL to
  // that path so the check does not touch the network.
  git(checkout, [
    "config",
    "--local",
    `url.${origin}.insteadOf`,
    "https://github.com/AgenticSocietyLab/MAGI.git",
  ]);
  git(checkout, [
    "config",
    "--local",
    "--add",
    `url.${origin}.insteadOf`,
    "https://github.com/realTaki/MAGI.git",
  ]);

  const api = createLocalApi({
    paths: { home: root, userData: path.join(root, "userData"), checkout },
    repository: "https://github.com/AgenticSocietyLab/MAGI.git",
    tools: { git: "git", env: gitEnv },
    emit: () => {},
    openExternal: async () => {},
    copy: () => {},
  });
  t.after(() => api.dispose());

  const status = await api["source.status"]();
  assert.equal(status.available, true);
  assert.equal(status.branch, "main");
  assert.equal(status.commit, commit);
  assert.equal(status.latestCommit, latestCommit);
  assert.equal(status.tag, "v0.0.7-experiment");
  assert.equal(status.repository, "realTaki/MAGI");
  assert.equal(status.upstreamRepository, "AgenticSocietyLab/MAGI");
  assert.equal(status.commitUrl, `https://github.com/realTaki/MAGI/commit/${latestCommit}`);
  assert.equal(
    status.tagUrl,
    "https://github.com/AgenticSocietyLab/MAGI/releases/tag/v0.0.7-experiment",
  );
  assert.equal(status.forkPoint, forkPoint);
  assert.equal(
    status.forkPointUrl,
    `https://github.com/AgenticSocietyLab/MAGI/commit/${forkPoint}`,
  );
  assert.equal(status.remoteAhead, true);
  assert.equal(status.remoteChecked, true);
  assert.equal(status.remote, "https://github.com/AgenticSocietyLab/MAGI.git");
});

test("about stays local when the checkout is not a Git repository", async (t) => {
  const root = mkdtempSync(path.join(tmpdir(), "magi-source-empty-"));
  const checkout = path.join(root, "checkout");
  mkdirSync(checkout);
  t.after(() => rmSync(root, { recursive: true, force: true }));

  const api = createLocalApi({
    paths: { home: root, userData: path.join(root, "userData"), checkout },
    repository: "https://github.com/AgenticSocietyLab/MAGI.git",
    tools: { git: "git", env: gitEnv },
    emit: () => {},
    openExternal: async () => {},
    copy: () => {},
  });
  t.after(() => api.dispose());

  const status = await api["source.status"]();
  assert.equal(status.available, false);
  assert.equal(status.commit, "");
  assert.equal(status.remoteAhead, false);
});
