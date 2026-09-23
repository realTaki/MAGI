import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { test } from "node:test";

import { createLocalApi } from "../main/index.mjs";

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

  git(root, ["clone", "--quiet", origin, checkout]);
  writeFileSync(path.join(origin, "README"), "remote\n");
  git(origin, ["commit", "-am", "remote"]);

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

  const api = createLocalApi({
    paths: { home: root, userData: path.join(root, "userData"), checkout },
    repository: "https://github.com/AgenticSocietyLab/MAGI.git",
    tools: { git: "git", env: gitEnv, python: "python" },
    emit: () => {},
    openExternal: async () => {},
    copy: () => {},
  });
  t.after(() => api.dispose());

  const status = await api["source.status"]();
  assert.equal(status.available, true);
  assert.equal(status.branch, "main");
  assert.equal(status.commit, commit);
  assert.equal(status.forkPoint, forkPoint);
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
    tools: { git: "git", env: gitEnv, python: "python" },
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
