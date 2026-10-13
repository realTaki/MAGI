import { posix } from "node:path";

/** The installed artifact differs from Electron's executable inside an AppImage. */
export function shellArtifactPath({ platform, execPath, appImage }) {
  if (platform === "darwin") return posix.resolve(posix.dirname(execPath), "..", "..");
  if (platform === "linux") {
    if (typeof appImage !== "string" || !posix.isAbsolute(appImage) || posix.resolve(appImage) === posix.resolve(execPath)) {
      throw new Error("Linux shell updates require the outer APPIMAGE path; launch the AppImage rather than its extracted executable.");
    }
    return appImage;
  }
  return execPath;
}
