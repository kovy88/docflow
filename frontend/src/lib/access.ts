import { useSyncExternalStore } from "react";

/** Access code for the live API on the public deployment. Without it the public site
 *  serves the precomputed read-only demo (no backend, no cold start, no API cost). */
const KEY = "docflow:access-code";
const listeners = new Set<() => void>();

/** NEXT_PUBLIC_DEMO=1 marks the public deployment: live API only after unlocking. */
export const PUBLIC_DEPLOYMENT = process.env.NEXT_PUBLIC_DEMO === "1";

export function getAccessCode(): string | null {
  try {
    return localStorage.getItem(KEY);
  } catch {
    return null;
  }
}

export function setAccessCode(code: string | null) {
  try {
    if (code) localStorage.setItem(KEY, code);
    else localStorage.removeItem(KEY);
  } catch {
    /* storage unavailable: the code lasts only for this page */
  }
  listeners.forEach((l) => l());
}

/** True when requests go to the live backend. */
export function isLive(): boolean {
  return !PUBLIC_DEPLOYMENT || Boolean(getAccessCode());
}

const subscribe = (listener: () => void) => {
  listeners.add(listener);
  window.addEventListener("storage", listener);
  return () => {
    listeners.delete(listener);
    window.removeEventListener("storage", listener);
  };
};

/** Hydration-safe: the server always renders the public (demo) variant. */
export function useLive(): boolean {
  return useSyncExternalStore(subscribe, isLive, () => !PUBLIC_DEPLOYMENT);
}
