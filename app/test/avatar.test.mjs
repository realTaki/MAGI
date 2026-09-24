/**
 * The local backend's GitHub account state: the name and picture the interface
 * shows, and the fact that both are cached on this machine instead of being
 * fetched again on every read.
 */

import assert from "node:assert/strict";
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { test } from "node:test";

import { createLocalApi } from "../main/index.mjs";

// A 1x1 transparent PNG: enough to prove bytes travel from GitHub to the cache.
const PNG = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8DwHwAFAAH/q842iQAAAABJRU5ErkJggg==",
  "base64",
);
const AVATAR_URL = "https://avatars.githubusercontent.com/u/42?v=4";

/** A backend on throwaway paths, with GitHub replaced by a local fake. */
function backend({ viewer = {}, viewerStatus = 200, avatarStatus = 200, metadata = null } = {}) {
  const root = mkdtempSync(path.join(tmpdir(), "magi-app-test-"));
  const paths = {
    home: path.join(root, "home"),
    userData: path.join(root, "userData"),
    checkout: path.join(root, "checkout"),
  };
  const appData = path.join(paths.home, ".magi", "app");
  mkdirSync(appData, { recursive: true });
  mkdirSync(paths.checkout, { recursive: true });
  writeFileSync(path.join(appData, "github-token"), "ghp_test\n");
  if (metadata !== null) {
    writeFileSync(path.join(appData, "github.json"), JSON.stringify(metadata));
  }

  const originalFetch = globalThis.fetch;
  const requests = [];
  globalThis.fetch = async (url) => {
    requests.push(String(url));
    if (String(url).startsWith("https://api.github.com/user")) {
      return new Response(
        JSON.stringify({
          id: 42,
          login: "realTaki",
          name: "Taki Wang",
          avatar_url: AVATAR_URL,
          ...viewer,
        }),
        { status: viewerStatus, headers: { "content-type": "application/json" } },
      );
    }
    if (String(url) === AVATAR_URL) {
      return new Response(avatarStatus === 200 ? PNG : "", {
        status: avatarStatus,
        headers: { "content-type": "image/png" },
      });
    }
    throw new Error(`unexpected request: ${url}`);
  };

  const api = createLocalApi({
    paths,
    repository: "https://github.com/AgenticSocietyLab/MAGI.git",
    tools: { git: "git", env: process.env },
    emit: () => {},
    openExternal: async () => {},
    copy: () => {},
    managed: true,
  });

  return {
    api,
    requests,
    avatarFile: () => path.join(paths.home, ".magi", "app", "github-avatar"),
    tokenFile: () => path.join(paths.home, ".magi", "app", "github-token"),
    metadata: () => JSON.parse(readFileSync(path.join(paths.home, ".magi", "app", "github.json"), "utf8")),
    downloads: () => requests.filter((url) => url === AVATAR_URL).length,
    dispose: () => {
      globalThis.fetch = originalFetch;
      rmSync(root, { recursive: true, force: true });
    },
  };
}

test("state reports the GitHub account and caches the picture", async (t) => {
  const github = backend();
  t.after(github.dispose);

  const state = await github.api["github.state"]();
  assert.equal(state.avatar, `data:image/png;base64,${PNG.toString("base64")}`);
  assert.deepEqual(
    { ...state, avatar: "checked above" },
    {
      available: true,
      upstream: "AgenticSocietyLab/MAGI",
      login: "realTaki",
      name: "Taki Wang",
      avatar: "checked above",
      fork: "",
      signedIn: true,
      verified: true,
      connected: false,
    },
  );
  assert.deepEqual(github.metadata(), {
    login: "realTaki",
    name: "Taki Wang",
    avatarType: "image/png",
  });
  assert.ok(existsSync(github.avatarFile()), "the picture lands in this machine's state");
  assert.ok(existsSync(github.tokenFile()), "the token remains in app state");
});

test("existing app account metadata is read from app state", (t) => {
  const old = { login: "old-user", fork: "old-user/MAGI" };
  const github = backend({ metadata: old });
  t.after(github.dispose);
  assert.deepEqual(github.metadata(), old);
});

test("an invalid app token stays removed", async (t) => {
  const github = backend({ viewerStatus: 401 });
  t.after(github.dispose);

  assert.equal((await github.api["github.state"]()).signedIn, false);
  assert.equal(existsSync(github.tokenFile()), false);
  assert.equal((await github.api["github.state"]()).signedIn, false);
  assert.equal(github.requests.filter((url) => url.startsWith("https://api.github.com/user")).length, 1);
});

test("a later read reuses the cached picture instead of downloading it again", async (t) => {
  const github = backend();
  t.after(github.dispose);

  const first = await github.api["github.state"]();
  assert.equal(github.downloads(), 1);
  const second = await github.api["github.state"]();
  assert.equal(second.avatar, first.avatar);
  assert.equal(github.downloads(), 1, "no second download");
});

test("a profile without a name falls back to the login", async (t) => {
  const github = backend({ viewer: { name: null } });
  t.after(github.dispose);

  const state = await github.api["github.state"]();
  assert.equal(state.name, "realTaki");
});

test("a failed download leaves the account usable without a picture", async (t) => {
  const github = backend({ avatarStatus: 500 });
  t.after(github.dispose);

  const state = await github.api["github.state"]();
  assert.equal(state.avatar, "");
  assert.equal(state.name, "Taki Wang");
  assert.equal(state.verified, true);
  assert.equal(existsSync(github.avatarFile()), false);
});
