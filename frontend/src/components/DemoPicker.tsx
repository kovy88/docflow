"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { api, type DemoDocument } from "@/lib/api";

const GROUPS: { id: string; label: string }[] = [
  { id: "invoice", label: "Faktury" },
  { id: "contract", label: "Smlouvy" },
  { id: "insurance_policy", label: "Pojistky" },
  { id: "lease", label: "Nájmy" },
];

const PLANTED: Record<string, string> = {
  sum: "nesedí součet",
  ico: "neplatné IČO",
  dates: "data v nesprávném pořadí",
};

type Card = {
  title: string;
  detail: string;
  badge: { text: string; tone: "err" | "warn" } | null;
};

/** Turns the evaluation-set description ("layout A, 4 pol., …, vložené chyby: sum") into
 *  something a visitor can read. */
function describe(d: DemoDocument): Card {
  if (d.template_id === "lease") return {
    title: "Nájemní smlouva → evidence",
    detail: "Nájem bytu 2+kk · doba určitá · mimo měřenou evaluaci",
    badge: null,
  };
  const parts = d.description.split(", ");
  const number = d.file.match(/_(\d+)/)?.[1] ?? "";
  let badge: Card["badge"] = null;
  let hard: string | null = null;
  for (const p of parts) {
    if (p.startsWith("vložené chyby: "))
      badge = {
        text: `Chyba: ${p
          .slice(15)
          .split(/[+ ]+/)
          .map((c) => PLANTED[c] ?? c)
          .join(", ")}`,
        tone: "err",
      };
    else if (p.startsWith("těžký případ: ")) {
      hard = p.slice(14);
      badge = { text: "Těžký případ", tone: "warn" };
    } else if (p === "sken bez textové vrstvy")
      badge = { text: "Sken, čte se OCR", tone: "warn" };
  }

  if (d.template_id === "invoice") {
    const items = Number(d.description.match(/(\d+) pol\./)?.[1]);
    const vat = d.description
      .match(/sazby DPH \[([^\]]*)\]/)?.[1]
      .replaceAll(", ", " + ");
    return {
      title: `Faktura ${number}`,
      detail:
        hard ??
        [
          items && `${items} ${items >= 2 && items <= 4 ? "položky" : "položek"}`,
          vat && `DPH ${vat} %`,
        ]
          .filter(Boolean)
          .join(" · "),
      badge,
    };
  }
  const kind = parts[0].charAt(0).toUpperCase() + parts[0].slice(1);
  const detail =
    d.template_id === "insurance_policy"
      ? parts.filter((p) => p.startsWith("doba") || p.startsWith("splátky"))
      : [];
  return {
    title: kind,
    detail: hard ?? [`č. ${number}`, ...detail].join(" · "),
    badge,
  };
}

const LIMIT = 6;

/** Fictional documents from the evaluation set, so the app can be tried without own PDFs. */
export function DemoPicker() {
  const router = useRouter();
  const [demo, setDemo] = useState<DemoDocument[]>([]);
  const [group, setGroup] = useState(GROUPS[0].id);
  const [all, setAll] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .demo()
      .then(setDemo)
      .catch(() => setDemo([]));
  }, []);

  // cacheComponents keeps this page alive while a document is open; without the reset the
  // buttons would still be disabled after navigating back.
  useEffect(() => () => setBusy(null), []);

  async function open(file: string) {
    setBusy(file);
    setError(null);
    try {
      const doc = await api.createDemo(file);
      router.push(`/documents/${doc.id}`);
    } catch (e) {
      setError((e as Error).message);
      setBusy(null);
    }
  }

  if (demo.length === 0) return null;
  const shown = demo.filter((d) => d.template_id === group);

  return (
    <section>
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 className="text-2xl font-bold tracking-[-0.01em]">
            Vyzkoušejte na ukázce
          </h2>
          <p className="mt-1 max-w-xl text-muted">
            Vyberte dokument a klikněte na libovolný údaj: v PDF se zvýrazní, odkud
            pochází. Červeně označené ukázky obsahují schválně vloženou chybu, kterou
            má Docflow najít. Všechny dokumenty jsou fiktivní.
          </p>
        </div>
        <div
          role="tablist"
          className="flex rounded-lg border border-line bg-surface p-0.5 text-sm"
        >
          {GROUPS.map((g) => {
            const count = demo.filter((d) => d.template_id === g.id).length;
            const active = g.id === group;
            return (
              <button
                key={g.id}
                type="button"
                role="tab"
                aria-selected={active}
                onClick={() => {
                  setGroup(g.id);
                  setAll(false);
                }}
                className={`rounded-md px-3 py-1 transition ${
                  active
                    ? "bg-accent-soft font-medium text-accent"
                    : "text-muted hover:text-foreground"
                }`}
              >
                {g.label} <span className="text-xs opacity-70">{count}</span>
              </button>
            );
          })}
        </div>
      </div>

      {error && (
        <p className="mt-3 rounded-md bg-err-soft px-3 py-2 text-sm text-err">
          {error}
        </p>
      )}

      <ul className="mt-4 grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
        {shown
          .slice(0, all ? undefined : LIMIT)
          .map((d) => {
            const card = describe(d);
            const opening = busy === d.file;
            return (
              <li key={d.file}>
                <button
                  type="button"
                  onClick={() => open(d.file)}
                  disabled={busy !== null}
                  className={`flex h-full w-full flex-col items-start gap-1 rounded-lg border bg-surface px-3 py-2.5 text-left transition hover:border-accent hover:shadow-sm disabled:cursor-wait ${
                    opening ? "border-accent" : "border-line"
                  } ${busy !== null && !opening ? "opacity-60" : ""}`}
                >
                  <span className="text-sm font-medium">
                    {opening ? "Otevírám…" : card.title}
                  </span>
                  <span className="text-xs text-muted">{card.detail}</span>
                  {card.badge && (
                    <span
                      className={`mt-0.5 rounded px-1.5 py-0.5 text-xs ${
                        card.badge.tone === "err"
                          ? "bg-err-soft text-err"
                          : "bg-warn-soft text-warn"
                      }`}
                    >
                      {card.badge.text}
                    </span>
                  )}
                </button>
              </li>
            );
          })}
      </ul>
      {!all && shown.length > LIMIT && (
        <button
          type="button"
          onClick={() => setAll(true)}
          className="mt-3 text-sm font-medium text-accent hover:underline"
        >
          Zobrazit všech {shown.length}
        </button>
      )}
    </section>
  );
}
