"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";
import { contactHref } from "@/components/PilotOffer";
import { StatusBadge } from "@/components/StatusBadge";
import type { ExtractedField } from "@/lib/api";
import {
  TRIAL_MAX_MB,
  TRIAL_MAX_PAGES,
  TRIAL_PER_IP,
  TRIAL_TYPES,
  trialApi,
  type RuleResult,
  type TrialResult,
} from "@/lib/trial";

const POLL_MS = 2000;

function formatValue(field: ExtractedField): string {
  if (field.value === null) return "";
  if (field.type === "number" && typeof field.value === "number")
    return field.value.toLocaleString("cs-CZ", { maximumFractionDigits: 2 });
  if (field.type === "date" && typeof field.value === "string") {
    const [y, m, d] = field.value.split("-").map(Number);
    return `${d}. ${m}. ${y}`;
  }
  return String(field.value);
}

const RULE_STYLE: Record<RuleResult["result"], { mark: string; label: string; className: string }> = {
  passed: { mark: "✓", label: "v pořádku", className: "text-ok" },
  failed: { mark: "✗", label: "nesedí", className: "text-err" },
  skipped: { mark: "–", label: "chybí hodnota", className: "text-muted" },
};

export function TrialPage() {
  const ref = useSearchParams().get("ref");
  const [templateId, setTemplateId] = useState<string>(TRIAL_TYPES[0].id);
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<TrialResult | null>(null);
  const [deleted, setDeleted] = useState(false);

  // Free hosting sleeps; wake the backend while the visitor picks a file.
  useEffect(() => {
    trialApi.wake();
  }, []);

  useEffect(() => {
    if (result?.status !== "running") return;
    const timer = setTimeout(() => {
      trialApi
        .get(result.id)
        .then(setResult)
        .catch((e: Error) => setError(e.message));
    }, POLL_MS);
    return () => clearTimeout(timer);
  }, [result]);

  function pick(f: File | null) {
    setError(null);
    if (f && f.size > TRIAL_MAX_MB * 1024 * 1024) {
      setError(`Soubor je větší než ${TRIAL_MAX_MB} MB.`);
      f = null;
    }
    setFile(f);
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!file) return;
    setBusy(true);
    setError(null);
    setDeleted(false);
    try {
      setResult(await trialApi.create(file, templateId, ref));
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    if (!result) return;
    try {
      await trialApi.remove(result.id);
      reset();
      setDeleted(true);
    } catch (err) {
      setError((err as Error).message);
    }
  }

  function reset() {
    setResult(null);
    setFile(null);
    setError(null);
    setDeleted(false);
  }

  return (
    <div className="mx-auto w-full max-w-4xl px-4 py-10">
      <h1 className="text-2xl font-bold tracking-tight">Vyzkoušejte Docflow na vlastním dokumentu</h1>
      <p className="mt-2 max-w-2xl text-muted">
        Nahrajte PDF a za chvíli uvidíte vytěžená pole. U každého je doslovná citace a strana,
        odkud hodnota pochází, a kontrola pravidel (součty, IČO, pořadí dat). Bez registrace.
      </p>

      <div role="note" className="mt-6 rounded-lg border border-line border-l-[3px] border-l-warn bg-warn-soft p-4 text-sm">
        <p className="font-medium text-foreground">Nahrávejte jen anonymizované dokumenty.</p>
        <p className="mt-1 text-foreground">
          Soubor, jeho text i výsledky se automaticky smažou do 24 hodin, nebo hned tlačítkem
          Smazat. Nepoužijí se k evaluaci ani k trénování modelu.{" "}
          <a href="#podminky" className="font-medium text-accent hover:underline">Podmínky</a>
        </p>
      </div>

      {!result && (
        <form onSubmit={submit} className="mt-6 rounded-lg border border-line bg-surface p-6">
          <fieldset>
            <legend className="text-sm font-medium">Typ dokumentu</legend>
            <div className="mt-2 grid gap-2 sm:grid-cols-3">
              {TRIAL_TYPES.map((type) => (
                <label key={type.id}
                  className={`cursor-pointer rounded-md border p-3 text-sm focus-within:ring-2 focus-within:ring-accent ${templateId === type.id ? "border-accent bg-accent-soft" : "border-line-strong hover:bg-background"}`}>
                  <input type="radio" name="template" value={type.id} checked={templateId === type.id}
                    onChange={() => setTemplateId(type.id)} className="sr-only" />
                  <span className="block font-medium">{type.label}</span>
                  <span className="mt-1 block text-xs text-muted">{type.hint}</span>
                </label>
              ))}
            </div>
          </fieldset>

          <label htmlFor="trial-file" className="mt-5 block text-sm font-medium">Soubor PDF</label>
          <input id="trial-file" type="file" accept="application/pdf,.pdf"
            onChange={(e) => pick(e.target.files?.[0] ?? null)}
            className="mt-2 block w-full rounded-md border border-line-strong bg-surface text-sm file:mr-3 file:h-9 file:border-0 file:bg-accent-soft file:px-3 file:font-medium file:text-accent focus:outline-none focus-visible:ring-2 focus-visible:ring-accent" />
          <p className="mt-2 text-xs text-muted">
            Nejvýš {TRIAL_MAX_MB} MB a {TRIAL_MAX_PAGES} stran, i skeny. Nemáte nic po ruce?
            Fiktivní vzorek:{" "}
            {TRIAL_TYPES.map((type, i) => (
              <span key={type.id}>
                {i > 0 && " · "}
                <a href={type.sample} download className="text-accent hover:underline">{type.label.toLowerCase()}</a>
              </span>
            ))}
          </p>

          {error && <p role="alert" className="mt-4 rounded-md border-l-[3px] border-l-err bg-err-soft px-3 py-2 text-sm text-err">{error}</p>}
          {deleted && <p role="status" className="mt-4 text-sm text-ok">Dokument i výsledky jsou smazané.</p>}

          <div className="mt-5 flex justify-end">
            <button type="submit" disabled={!file || busy}
              className="h-10 rounded-md bg-accent px-5 font-medium text-white hover:bg-accent-hover disabled:opacity-50">
              {busy ? "Nahrávám…" : "Vytěžit"}
            </button>
          </div>
        </form>
      )}

      {result && <TrialResultView result={result} error={error} onRemove={remove} onReset={reset} />}

      <section className="mt-10 grid gap-4 sm:grid-cols-2">
        <div className="rounded-lg border border-line bg-surface p-5">
          <h2 className="font-bold tracking-tight">Chcete to na vlastních dokumentech?</h2>
          <p className="mt-1 text-sm text-muted">
            Pilot na 14 dní: vaše šablona, kontrola v aplikaci, export do Excelu nebo účetnictví.
          </p>
          <a href={contactHref("Docflow – vyzkoušel jsem zkušební verzi")}
            className="mt-3 inline-block text-sm font-medium text-accent hover:underline">Napsat mi ↗</a>
        </div>
        <div className="rounded-lg border border-line bg-surface p-5">
          <h2 className="font-bold tracking-tight">Napojení na váš systém</h2>
          <p className="mt-1 text-sm text-muted">
            REST API s klíčem pro každého klienta: nahrání PDF, výsledek s citacemi, export.
          </p>
          <Link href="/api" className="mt-3 inline-block text-sm font-medium text-accent hover:underline">Dokumentace API →</Link>
        </div>
      </section>

      <TrialTerms />
    </div>
  );
}

function TrialResultView({ result, error, onRemove, onReset }: {
  result: TrialResult;
  error: string | null;
  onRemove: () => void;
  onReset: () => void;
}) {
  const running = result.status === "running";
  const finished = !running && result.status !== "failed";
  // Per-field warnings are shown next to the field; the rest (rule errors) below the rules.
  const general = result.issues.filter((i) => i.severity === "error" || i.fields.length !== 1);
  const expires = new Date(result.expires_at).toLocaleString("cs-CZ", { dateStyle: "short", timeStyle: "short" });
  return (
    <section aria-live="polite" className="mt-6 rounded-lg border border-line bg-surface">
      <div className="flex flex-wrap items-center gap-3 border-b border-line p-4">
        <StatusBadge status={result.status} />
        <span className="min-w-0 truncate font-medium">{result.filename}</span>
        <span className="text-sm text-muted">{result.page_count} str.</span>
        <span className="ml-auto text-xs text-muted">Smaže se {expires}</span>
      </div>

      {running && (
        <p className="p-6 text-sm text-muted">
          Model čte dokument a ověřuje citace. Obvykle to trvá 20–60 sekund, stránku nezavírejte.
        </p>
      )}
      {result.status === "failed" && (
        <p className="p-6 text-sm text-err">{result.issues[0]?.message ?? "Extrakce selhala."}</p>
      )}
      {error && <p role="alert" className="mx-4 mt-4 rounded-md border-l-[3px] border-l-err bg-err-soft px-3 py-2 text-sm text-err">{error}</p>}

      {finished && (
        <>
          <ul className="divide-y divide-line">
            {result.fields.map((field) => {
              const value = formatValue(field) || field.raw;
              const warning = result.issues.find(
                (i) => i.severity === "warning" && i.fields.length === 1 && i.fields[0] === field.key,
              );
              return (
                <li key={field.key} className="px-4 py-3">
                  <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
                    <span className="w-44 shrink-0 text-sm text-muted">{field.label}</span>
                    <span className={`min-w-0 flex-1 font-medium ${value ? "" : "text-muted"}`}>{value || "nenalezeno"}</span>
                    {field.raw !== null && (
                      <span className={`text-xs ${field.grounded ? "text-ok" : "text-warn"}`}
                        title={field.grounded ? "Citace nalezena v PDF" : "Citace v PDF nenalezena"}>
                        {field.grounded ? "✓ ověřeno" : "⚠ neověřeno"} · {Math.round(field.confidence * 100)} %
                      </span>
                    )}
                    {field.page && <span className="text-xs font-medium text-accent">Zdroj · str. {field.page}</span>}
                  </div>
                  {field.quote && (
                    <p className="mt-2 border-l-2 border-line-strong pl-3 text-sm text-muted"><q>{field.quote}</q></p>
                  )}
                  {warning && <p className="mt-1 text-xs text-warn">{warning.message}</p>}
                </li>
              );
            })}
          </ul>

          <div className="border-t border-line p-4">
            <h2 className="text-xs font-medium uppercase tracking-wide text-muted">Kontrola pravidel</h2>
            {result.rules.length === 0 ? (
              <p className="mt-2 text-sm text-muted">Šablona nemá pravidla.</p>
            ) : (
              <ul className="mt-2 space-y-1 text-sm">
                {result.rules.map((rule) => (
                  <li key={rule.description} className="flex gap-2">
                    <span className={RULE_STYLE[rule.result].className} aria-hidden>{RULE_STYLE[rule.result].mark}</span>
                    <span>{rule.description}</span>
                    <span className={`ml-auto text-xs ${RULE_STYLE[rule.result].className}`}>{RULE_STYLE[rule.result].label}</span>
                  </li>
                ))}
              </ul>
            )}
            {general.length > 0 && (
              <ul className="mt-3 space-y-1 text-sm">
                {general.map((issue, i) => (
                  <li key={i} className={issue.severity === "error" ? "text-err" : "text-warn"}>{issue.message}</li>
                ))}
              </ul>
            )}
          </div>
        </>
      )}

      <div className="flex flex-wrap items-center gap-3 border-t border-line p-4">
        <button type="button" onClick={onRemove} disabled={running}
          className="h-9 rounded-md px-3 text-sm font-medium text-err hover:bg-err-soft disabled:opacity-50">
          Smazat hned
        </button>
        <button type="button" onClick={onReset}
          className="h-9 rounded-md border border-line-strong px-3 text-sm font-medium hover:bg-background">
          Nahrát další
        </button>
        {finished && (
          <span className="ml-auto flex gap-2">
            {(["xlsx", "json"] as const).map((format) => (
              <a key={format} href={trialApi.exportUrl(result.id, format)}
                className="inline-flex h-9 items-center rounded-md border border-line-strong px-3 text-sm font-medium hover:bg-background">
                Stáhnout {format.toUpperCase()}
              </a>
            ))}
          </span>
        )}
      </div>
    </section>
  );
}

function TrialTerms() {
  return (
    <details id="podminky" className="mt-10 rounded-lg border border-line bg-surface p-5 text-sm">
      <summary className="cursor-pointer font-medium">Podmínky zkušební verze</summary>
      <ul className="mt-3 list-disc space-y-2 pl-5 text-muted">
        <li>Zkušební verze slouží jen k jednorázovému vyzkoušení. Nahrávejte anonymizované dokumenty bez osobních údajů.</li>
        <li>Uloží se PDF, jeho text a výsledek vytěžení, odděleně od dat zákazníků. Vše se automaticky smaže do 24 hodin od nahrání, nebo hned tlačítkem Smazat hned. Dokumenty se nepoužijí k evaluaci, k trénování modelu ani se nikomu nepředávají.</li>
        <li>Text dokumentu zpracuje jazykový model Qwen přes OpenRouter, jen u poskytovatelů, kteří vstupy neukládají ani na nich netrénují. Stránka běží na Vercelu, server na Renderu a data v databázi Supabase.</li>
        <li>Kvůli limitu {TRIAL_PER_IP} dokumentů za 24 hodin se ukládá jen otisk (hash) IP adresy, smaže se spolu s dokumentem. Parametr ref z odkazu v e-mailu se uloží k nahrání (bez názvu a obsahu dokumentu). Stránka nepoužívá sledovací cookies.</li>
        <li>Výsledky jsou strojové a mohou obsahovat chyby, proto je u každé hodnoty zdroj a výsledek kontrol.</li>
        <li>Provozovatel: Logic flow, <a href="https://logicflow.cz" className="text-accent hover:underline">logicflow.cz</a>. Dotazy a žádosti o smazání: <a href={contactHref("Docflow – zkušební verze")} className="text-accent hover:underline">napište mi</a>.</li>
      </ul>
    </details>
  );
}
