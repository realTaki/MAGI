/**
 * The bridge the Electron preload exposes to whatever page the window loads.
 *
 * Every member is optional: the same UI also runs in a plain browser (dev
 * server, remote ASP), where no bridge exists at all.
 */

import type { GitHubEvent, GitHubState } from "./github-connect";

export {};

declare global {
  interface Window {
    magiDesktop?: {
      retryStartup?: () => Promise<void>;
      onStartupProgress?: (
        listener: (progress: { step: string; message: string }) => void,
      ) => void;
      onStartupError?: (listener: (message: string) => void) => void;
      githubState?: () => Promise<GitHubState>;
      startGitHubSignIn?: () => Promise<{ login: string }>;
      connectGitHub?: () => Promise<GitHubState>;
      onGitHubEvent?: (listener: (event: GitHubEvent) => void) => void;
    };
  }
}
