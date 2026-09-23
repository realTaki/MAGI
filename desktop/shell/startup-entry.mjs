import { existsSync } from "node:fs";
import path from "node:path";

const STARTUP_ASSETS = ["index.html", "styles.css", "app.js"];

export function resolveStartupEntry({ checkout, fallback }) {
  const projectBoot = path.join(checkout, "desktop", "shell", "boot");
  if (
    existsSync(path.join(checkout, ".git")) &&
    STARTUP_ASSETS.every((file) => existsSync(path.join(projectBoot, file)))
  ) {
    return path.join(projectBoot, "index.html");
  }
  return fallback;
}
