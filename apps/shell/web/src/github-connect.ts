/**
 * GitHub connection — one method of this app's local backend.
 *
 * The backend lives in ``app/main/`` and is loaded by the Electron
 * shell from the checkout, so this file (like the rest of the app) evolves with
 * the source tree. Calls travel through the shell's generic bridge; when the
 * same interface is opened outside the desktop app the bridge is absent, which
 * is why every caller feature-detects with :func:`localAppAvailable`.
 */

import { useEffect, useState } from "react";

export type GitHubState = {
  /** False when this build has no local checkout, or the upstream is not on GitHub. */
  available: boolean;
  /** `owner/name` of the repository the app clones from. */
  upstream: string;
  /** GitHub login, empty until a sign-in succeeded once. */
  login: string;
  /** Display name from the GitHub profile, falling back to the login. */
  name: string;
  /** The account's avatar as a data URL, cached locally; empty when unknown. */
  avatar: string;
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

/** Avatar letters for a GitHub login: ``realTaki`` → ``RE``, ``taki-wang`` → ``TW``. */
export function initialsFromLogin(login: string): string {
  const parts = login.split(/[-_.\s]+/).filter(Boolean);
  if (parts.length === 0) {
    return "";
  }
  if (parts.length === 1) {
    return parts[0].slice(0, 2).toUpperCase();
  }
  return (parts[0][0] + parts[1][0]).toUpperCase();
}

/** The account behind the checkout: GitHub's login, name and picture. */
export type GitHubAccount = {
  login: string;
  name: string;
  avatar: string;
};

const EMPTY_ACCOUNT: GitHubAccount = { login: "", name: "", avatar: "" };

// Opening a settings overlay remounts this hook; the last read is kept so the
// picture does not blink through the fallback while the new one arrives.
let cachedAccount: GitHubAccount = EMPTY_ACCOUNT;
let accountRead: Promise<GitHubAccount> | null = null;

/**
 * Read the account from the local backend, one round-trip at a time: mounts
 * that happen together share the answer, and the last one is kept for the next
 * mount to paint with.
 */
export function readGitHubAccount(): Promise<GitHubAccount> {
  accountRead ??= githubState()
    .then((state) => {
      cachedAccount = { login: state.login, name: state.name, avatar: state.avatar };
      return cachedAccount;
    })
    .finally(() => {
      accountRead = null;
    });
  return accountRead;
}

/**
 * The GitHub account this machine is signed in with — what the interface shows
 * as "the operator". The last known value paints immediately; empty until a
 * sign-in succeeded (or outside the desktop app); callers keep their own local
 * name in that case.
 */
export function useGitHubAccount(): GitHubAccount {
  const [account, setAccount] = useState<GitHubAccount>(cachedAccount);
  useEffect(() => {
    if (!localAppAvailable()) {
      return;
    }
    let cancelled = false;
    const read = () => {
      void readGitHubAccount()
        .then((next) => {
          if (!cancelled) {
            setAccount(next);
          }
        })
        .catch(() => {});
    };
    read();
    onGitHubEvent((message) => {
      if (message.event === "github.signed-in" || message.event === "github.connected") {
        read();
      }
    });
    return () => {
      cancelled = true;
    };
  }, []);
  return account;
}
