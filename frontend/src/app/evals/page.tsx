"use client";

import { useEffect, useState } from "react";
import { api, parseTimestamp, type EvalRun } from "@/lib/api";

const pct = (x: number | null | undefined) =>
  x == null ? "–" : `${(x * 100).toFixed(1)} %`;
const TEMPLATE_NAMES: Record<string, string> = {
  invoice: "Faktury",
  contract: "Smlouvy",
  insurance_policy: "Pojistky",
};

export default function EvalsPage() {
  const [runs, setRuns] = useState<EvalRun[] | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .evals()
      .then((r) => {
        setRuns(r);
        setSelected(r[0]?.id ?? null);
      })
      .catch((e: Error) => setError(e.message));
  }, []);

  const run = runs?.find((r) => r.id === selected);

  return (
    <div className="mx-auto w-full max-w-5xl px-4 py-10">
      <h1 className="text-2xl font-semibold tracking-tight">Evaluace</h1>
      <p className="mt-1 max-w-3xl text-muted">
        Přesnost měřená na 26 fiktivních dokumentech se známými správnými
        hodnotami. Deset z nich obsahuje schválně vložené chyby (neplatné IČO,
        nesedící součet, prohozená data). Spuštění:{" "}
        <code className="font-mono text-sm">
          uv run python scripts/evaluate.py
        </code>
      </p>
      {error && (
        <p className="mt-4 rounded-md bg-err-soft px-3 py-2 text-err">
          {error}
        </p>
      )}
      {runs?.length === 0 && (
        <p className="mt-6 text-muted">Zatím žádný dokončený běh.</p>
      )}

      {runs && runs.length > 0 && (
        <div className="mt-6 overflow-x-auto rounded-lg border border-line bg-surface">
          <table className="w-full text-sm">
            <thead className="bg-background text-left text-muted">
              <tr>
                {[
                  "Model",
                  "Přesnost polí",
                  "Bez chyby",
                  "Chyby zachyceny",
                  "Falešné poplachy",
                  "Medián času",
                  "Datum",
                ].map((h) => (
                  <th key={h} className="px-3 py-2 font-medium">
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-line">
              {runs.map((r) => {
                const s = r.summary;
                return (
                  <tr
                    key={r.id}
                    onClick={() => setSelected(r.id)}
                    className={`cursor-pointer ${r.id === selected ? "bg-accent-soft" : "hover:bg-background"}`}
                  >
                    <td className="px-3 py-2 font-medium">
                      {r.model}{" "}
                      <span className="text-muted">({r.provider})</span>
                    </td>
                    <td className="px-3 py-2 tabular-nums">
                      {pct(s.field_accuracy)}
                    </td>
                    <td className="px-3 py-2 tabular-nums">
                      {s.documents_fully_correct} /{" "}
                      {s.documents - s.failed_documents}
                    </td>
                    <td className="px-3 py-2 tabular-nums">
                      {s.rule_detection.tp} /{" "}
                      {s.rule_detection.tp + s.rule_detection.fn}
                    </td>
                    <td className="px-3 py-2 tabular-nums">
                      {s.rule_detection.fp}
                    </td>
                    <td className="px-3 py-2 tabular-nums">
                      {s.latency_s.median ?? "–"} s
                    </td>
                    <td className="px-3 py-2 text-muted">
                      {parseTimestamp(r.started).toLocaleDateString("cs-CZ")}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {run && (
        <div className="mt-8 grid gap-6 lg:grid-cols-2">
          <section className="rounded-lg border border-line bg-surface p-4">
            <h2 className="font-semibold">Přesnost podle polí</h2>
            <ul className="mt-3 space-y-1.5">
              {Object.entries(run.summary.per_field)
                .sort(([, a], [, b]) => (a ?? 0) - (b ?? 0))
                .map(([key, value]) => (
                  <li
                    key={key}
                    className="grid grid-cols-[150px_1fr_56px] items-center gap-2 text-sm"
                  >
                    <span className="truncate font-mono text-xs">{key}</span>
                    <span className="h-2 overflow-hidden rounded-full bg-background">
                      <span
                        className={`block h-full rounded-full ${(value ?? 0) >= 0.95 ? "bg-ok" : (value ?? 0) >= 0.8 ? "bg-warn" : "bg-err"}`}
                        style={{ width: `${(value ?? 0) * 100}%` }}
                      />
                    </span>
                    <span className="text-right tabular-nums">
                      {pct(value)}
                    </span>
                  </li>
                ))}
            </ul>
          </section>

          <section className="space-y-4">
            <div className="rounded-lg border border-line bg-surface p-4">
              <h2 className="font-semibold">Podle typu dokumentu</h2>
              <dl className="mt-2 grid grid-cols-3 gap-2 text-sm">
                {Object.entries(run.summary.per_template).map(([t, v]) => (
                  <div key={t} className="rounded-md bg-background p-2">
                    <dt className="text-muted">{TEMPLATE_NAMES[t] ?? t}</dt>
                    <dd className="text-lg font-semibold tabular-nums">
                      {pct(v)}
                    </dd>
                  </div>
                ))}
              </dl>
            </div>
            <div className="rounded-lg border border-line bg-surface p-4">
              <h2 className="font-semibold">Fungují signály v UI?</h2>
              <p className="mt-1 text-sm text-muted">
                Přesnost polí podle toho, jestli byla citace nalezena v PDF a
                jak si byl model jistý. Velký rozdíl znamená, že signál pomáhá
                najít chyby.
              </p>
              <dl className="mt-3 grid grid-cols-2 gap-2 text-sm">
                {[
                  ["Citace ověřena", run.summary.signals.accuracy_grounded],
                  ["Citace neověřena", run.summary.signals.accuracy_ungrounded],
                  [
                    "Jistota ≥ 90 %",
                    run.summary.signals.accuracy_high_confidence,
                  ],
                  [
                    "Jistota < 90 %",
                    run.summary.signals.accuracy_low_confidence,
                  ],
                ].map(([label, v]) => (
                  <div
                    key={label as string}
                    className="rounded-md bg-background p-2"
                  >
                    <dt className="text-muted">{label}</dt>
                    <dd className="font-semibold tabular-nums">
                      {pct(v as number | null)}
                    </dd>
                  </div>
                ))}
              </dl>
            </div>
          </section>

          <section className="rounded-lg border border-line bg-surface p-4 lg:col-span-2">
            <h2 className="font-semibold">
              Chybně vytěžená pole ({run.summary.errors.length})
            </h2>
            {run.summary.errors.length === 0 ? (
              <p className="mt-2 text-sm text-muted">Žádné.</p>
            ) : (
              <div className="mt-3 overflow-x-auto">
                <table className="w-full text-sm">
                  <thead className="text-left text-muted">
                    <tr>
                      <th className="py-1 pr-3 font-medium">Dokument</th>
                      <th className="py-1 pr-3 font-medium">Pole</th>
                      <th className="py-1 pr-3 font-medium">Očekáváno</th>
                      <th className="py-1 font-medium">Model</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-line">
                    {run.summary.errors.map((e, i) => (
                      <tr key={i}>
                        <td className="py-1.5 pr-3">{e.file}</td>
                        <td className="py-1.5 pr-3 font-mono text-xs">
                          {e.key}
                        </td>
                        <td className="py-1.5 pr-3">
                          {String(e.expected ?? "–")}
                        </td>
                        <td className="py-1.5 text-err">
                          {e.raw ?? String(e.predicted ?? "–")}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>
        </div>
      )}
    </div>
  );
}
