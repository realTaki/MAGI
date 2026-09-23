/**
 * GitHub connection — one method of this app's local backend.
 *
 * The backend lives in ``desktop/app/main/`` and is loaded by the Electron
 * shell from the checkout, so this file (like the rest of the app) evolves with
 * the source tree. Calls travel through the shell's generic bridge; when the
 * same interface is opened outside the desktop app the bridge is absent, which
 * is why every caller feature-detects with :func:`localAppAvailable`.
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

/** One message from the backend, e.g. ``github.waiting``. */
export type LocalEvent = {
  event: string;
  payload: Record<string, unknown>;
};

type LocalBridge = {
  invokeLocal: (method: string, payload?: unknown) => Promise<unknown>;
  onLocalEvent: (listener: (message: LocalEvent) => void) => void;
};

function bridge(): LocalBridge | null {
  const api = (window as unknown as { magiDesktop?: Partial<LocalBridge> }).magiDesktop;
  if (api && typeof api.invokeLocal === "function") {
    return api as LocalBridge;
  }
  return null;
}

/** True when this interface runs inside the desktop app. */
export function localAppAvailable(): boolean {
  return bridge() !== null;
}

function invoke(method: string, payload?: unknown): Promise<unknown> {
  const api = bridge();
  if (api === null) {
    return Promise.reject(new Error("The desktop app is not available in this window."));
  }
  return api.invokeLocal(method, payload);
}

export async function githubState(): Promise<GitHubState> {
  return (await invoke("github.state")) as GitHubState;
}

export async function signInWithGitHub(): Promise<{ login: string }> {
  return (await invoke("github.signIn")) as { login: string };
}

export async function connectGitHub(): Promise<GitHubState> {
  return (await invoke("github.connect")) as GitHubState;
}

export function onGitHubEvent(listener: (message: LocalEvent) => void): void {
  bridge()?.onLocalEvent(listener);
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
