import { useEffect, useState } from "react";

export const SETTINGS_HASH = "#/settings";

export function hashPath(): string {
  const raw = window.location.hash.replace(/^#/, "");
  return raw || "/";
}

export function openSettingsRoute(): void {
  window.location.hash = "/settings";
}

export function openChatsRoute(): void {
  window.location.hash = "";
}

export function useHashPath(): string {
  const [path, setPath] = useState(hashPath);
  useEffect(() => {
    function sync() {
      setPath(hashPath());
    }
    window.addEventListener("hashchange", sync);
    return () => window.removeEventListener("hashchange", sync);
  }, []);
  return path;
}
