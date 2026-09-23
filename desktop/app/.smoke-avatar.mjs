import { existsSync, mkdirSync, mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";

import { createLocalApi } from "./main/index.mjs";

const root = mkdtempSync(path.join(tmpdir(), "magi-smoke-"));
const paths = {
  home: path.join(root, "home"),
  userData: path.join(root, "userData"),
  checkout: path.join(root, "checkout"),
};
for (const dir of [path.join(paths.home, ".magi"), paths.userData, paths.checkout]) {
  mkdirSync(dir, { recursive: true });
}
writeFileSync(path.join(paths.home, ".magi", "github-token"), "ghp_fake\n");

const PNG = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8DwHwAFAAH/q842iQAAAABJRU5ErkJggg==",
  "base64",
);
const fetched = [];
globalThis.fetch = async (url) => {
  fetched.push(String(url));
  if (String(url).startsWith("https://api.github.com/user")) {
    return new Response(
      JSON.stringify({
        login: "realTaki",
        name: "Taki Wang",
        id: 42,
        avatar_url: "https://avatars.githubusercontent.com/u/42?v=4",
      }),
      { status: 200, headers: { "content-type": "application/json" } },
    );
  }
  if (String(url).includes("avatars.githubusercontent.com")) {
    return new Response(PNG, { status: 200, headers: { "content-type": "image/png" } });
  }
  throw new Error(`unexpected fetch ${url}`);
};

const api = createLocalApi({
  paths,
  repository: "https://github.com/AgenticSocietyLab/MAGI.git",
  tools: { git: "git", env: process.env, python: "python" },
  emit: (event, payload) => console.log("emit", event, JSON.stringify(payload)),
  openExternal: async () => {},
  copy: () => {},
  managed: true,
});

const state = await api["github.state"]();
console.log("state:", JSON.stringify({ ...state, avatar: `${state.avatar.slice(0, 32)}…` }, null, 2));
console.log("avatar cached:", existsSync(path.join(paths.userData, "github-avatar")));
console.log("metadata:", readFileSync(path.join(paths.userData, "github.json"), "utf8").trim());

const before = fetched.length;
const again = await api["github.state"]();
console.log("second read:", again.avatar === state.avatar ? "same avatar" : "DIFFERENT");
console.log("fetches total:", fetched.length, "(reads:", before, "+", fetched.length - before, ")");
