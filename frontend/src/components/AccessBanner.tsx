"use client";

import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { getAccessCode, PUBLIC_DEPLOYMENT, setAccessCode, useLive } from "@/lib/access";
import { api, checkAccess, REPO_URL } from "@/lib/api";

/** On the public deployment: demo by default, live API after entering the access code. */
export function AccessBanner() {
  const live = useLive();
  const pathname = usePathname();
  const [open, setOpen] = useState(false);
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [backend, setBackend] = useState<{
    provider: string;
    model: string;
  } | null>(null);
  const [workspace, setWorkspace] = useState<string | null>(null);

  useEffect(() => {
    if (!live || !PUBLIC_DEPLOYMENT) return;
    api
      .health()
      .then(setBackend)
      .catch(() => setBackend(null));
    const stored = getAccessCode();
    if (stored)
      checkAccess(stored)
        .then((access) => setWorkspace(access.workspace))
        .catch(() => setWorkspace(null));
  }, [live]);

  // The trial page and API docs work without the code; the demo/live switch would confuse.
  if (!PUBLIC_DEPLOYMENT || pathname === "/vyzkouset" || pathname === "/api") return null;

  async function unlock(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await checkAccess(code.trim());
      setAccessCode(code.trim());
      // Full reload on purpose: every fetched list and id belongs to the other data source.
      // eslint-disable-next-line @next/next/no-location-assign-relative-destination
      window.location.assign("/");
    } catch (err) {
      const message = (err as Error).message;
      setError(
        message === "Failed to fetch"
          ? "Server se probouzí (free hosting), zkuste to za minutu znovu."
          : message,
      );
      setBusy(false);
    }
  }

  const bar =
    "flex shrink-0 flex-wrap items-center justify-center gap-x-3 gap-y-1 border-b border-line px-4 py-1.5 text-xs";

  if (live)
    return (
      <div className={`${bar} bg-ok-soft text-ok`}>
        <span>
          {workspace ? <strong className="font-medium">{workspace}: </strong> : "Živá verze: "}
          dokumenty zpracovává
          {backend ? ` ${backend.model} (${backend.provider})` : " model"} a
          data se ukládají jen v tomto pracovním prostoru.
        </span>
        <button
          type="button"
          className="font-medium underline"
          onClick={() => {
            setAccessCode(null);
            // eslint-disable-next-line @next/next/no-location-assign-relative-destination
            window.location.assign("/");
          }}
        >
          Odhlásit
        </button>
      </div>
    );

  return (
    <div className={`${bar} bg-accent-soft text-accent`}>
      <span>Veřejné demo s předpočítanými výsledky, jen pro čtení.</span>
      {open ? (
        <form onSubmit={unlock} className="flex flex-wrap items-center justify-center gap-2">
          <input
            type="password"
            autoFocus
            value={code}
            onChange={(e) => setCode(e.target.value)}
            placeholder="přístupový kód"
            aria-label="Přístupový kód"
            className="w-36 rounded border border-line bg-surface px-2 py-0.5 text-foreground"
          />
          <button
            type="submit"
            disabled={busy || !code.trim()}
            className="rounded bg-accent px-2 py-0.5 font-medium text-white disabled:opacity-50"
          >
            {busy ? "Ověřuji…" : "Odemknout"}
          </button>
          <button type="button" disabled={busy} className="underline" onClick={() => { setOpen(false); setCode(""); setError(null); }}>Zavřít</button>
          {error && <span role="alert" className="text-err">{error}</span>}
        </form>
      ) : (
        <>
          <button
            type="button"
            className="font-medium underline"
            onClick={() => setOpen(true)}
          >
            Mám přístupový kód
          </button>
          <a href={REPO_URL} className="underline">
            Zdrojový kód
          </a>
        </>
      )}
    </div>
  );
}
