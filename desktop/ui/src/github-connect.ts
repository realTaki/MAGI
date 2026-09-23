/**
 * Local GitHub connection — a desktop-app capability, not an ASP one.
 *
 * The desktop app owns this machine's checkout and the operator's GitHub
 * token; ASP only relays messages and MAGI keeps its own store, so both may
 * live on a remote server while the working copy stays local. When the same UI
 * is opened outside the desktop app the bridge is simply absent, which is why
 * every caller feature-detects with :func:`localGitHubAvailable`.
 */

export type GitHubState = {
  /** False when this build has no local checkout, or the upstream is not on GitHub. */
  available: boolean;
  /** `owner/name` of the repository the app clones from. */
  upstream: string;
  /** GitHub account, empty until a sign-in succeeded once. */
  login: string;
  /** `owner/name` of the operator's fork, empty until the checkout is wired. */
  fork: string;
  signedIn: boolean;
  /** The stored token still works. */
  verified: boolean;
  /** origin points at the fork and the token is valid. */
  connected: boolean;
};

export type GitHubEvent = {
  step: "waiting" | "signed-in" | "forking" | "forked" | "remote" | "connected";
  userCode?: string;
  expiresInMinutes?: number;
  login?: string;
  fork?: string;
  remote?: string;
};

type GitHubBridge = {
  githubState: () => Promise<GitHubState>;
  startGitHubSignIn: () => Promise<{ login: string }>;
  connectGitHub: () => Promise<GitHubState>;
  onGitHubEvent: (listener: (event: GitHubEvent) => void) => void;
};

function bridge(): GitHubBridge | null {
  const api = (window as unknown as { magiDesktop?: Partial<GitHubBridge> }).magiDesktop;
  if (api && typeof api.githubState === "function" && typeof api.connectGitHub === "function") {
    return api as GitHubBridge;
  }
  return null;
}

/** True when this UI runs inside the desktop app. */
export function localGitHubAvailable(): boolean {
  return bridge() !== null;
}

export function getGitHubBridge(): GitHubBridge {
  const api = bridge();
  if (api === null) {
    throw new Error("The desktop app is not available in this window.");
  }
  return api;
}

// The overlay opens from two places — the first run and the account menu — so
// its open flag lives in a small store instead of in one component's state.
let open = false;
const listeners = new Set<() => void>();

function publish(): void {
  for (const listener of listeners) {
    listener();
  }
}

export function openGitHubConnect(): void {
  if (open) {
    return;
  }
  open = true;
  publish();
}

export function closeGitHubConnect(): void {
  if (!open) {
    return;
  }
  open = false;
  publish();
}

export function subscribeGitHubConnect(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

export function isGitHubConnectOpen(): boolean {
  return open;
}
