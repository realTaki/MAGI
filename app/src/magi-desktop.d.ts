/**
 * The bridge the Electron preload exposes to whatever page the window loads.
 *
 * The startup controls are the shell's own; ``invokeLocal`` / ``onLocalEvent``
 * forward to the app backend the shell loaded from the checkout. Every member
 * is optional: the same interface also runs in a plain browser (dev server,
 * remote ASP), where no bridge exists at all.
 */

import type { LocalEvent } from "./github-connect";

export type ShellRelease = {
  packaged: boolean;
  currentVersion: string;
  latestVersion: string;
  latestTag: string;
  updateAvailable: boolean;
  assetName: string;
  assetUrl: string;
  releaseUrl: string;
  error: string;
  reason: "" | "no-release" | "no-asset" | "unavailable";
};

export {};

declare global {
  interface Window {
    magiDesktop?: {
      retryStartup?: () => Promise<void>;
      copyText?: (text: string) => Promise<void>;
      shellRelease?: () => Promise<ShellRelease>;
      installShellRelease?: () => Promise<{ quitting: boolean }>;
      onStartupProgress?: (
        listener: (progress: { step: string; message: string }) => void,
      ) => void;
      onStartupError?: (listener: (message: string) => void) => void;
      invokeLocal?: (method: string, payload?: unknown) => Promise<unknown>;
      onLocalEvent?: (listener: (message: LocalEvent) => void) => void;
    };
  }
}
