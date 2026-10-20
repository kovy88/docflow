"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { useLive } from "@/lib/access";
import { api, parseTimestamp, plural, type ReviewReport, type Template } from "@/lib/api";

const pct = (x: number | null) => (x == null ? "–" : `${(x * 100).toFixed(1)} %`);
const secs = (x: number | null) =>
  x == null ? "–" : x < 60 ? `${Math.round(x)} s` : `${Math.floor(x / 60)} min ${Math.round(x % 60)} s`;

export default function ReportPage() {
  const live = useLive();
  const [templates, setTemplates] = useState<Template[]>([]);
  const [templateId, setTemplateId] = useState("");
  const [report, setReport] = useState<ReviewReport | null>(null);
  const [manual, setManual] = useState("");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!live) return;
    api.templates().then(setTemplates).catch(() => {});
  }, [live]);

  useEffect(() => {
    if (!live) return;
    api
      .reviewReport(templateId || undefined)
      .then(setReport)
      .catch((e: Error) => setError(e.message));
  }, [live, templateId]);

  const s = report?.summary;
  const manualSeconds = Number(manual.replace(",", "."));
  const saving =
    s?.review_s_median != null && manualSeconds > 0
      ? 1 - s.review_s_median / manualSeconds
      : null;

  return (
    <div className="mx-auto w-full max-w-5xl px-4 py-10">
      <h1 className="text-2xl font-semibold tracking-tight">Měření</h1>
      <p className="mt-1 max-w-3xl text-muted">
        Jak Docflow funguje na vašich dokumentech: kolik polí musel člověk při kontrole
        opravit a kolik času kontrola zabrala. Počítá se nejnovější schválená extrakce
        každého dokumentu; čas měří aplikace jen při otevřené kartě.
      </p>

      {!live ? (
        <p className="mt-6 rounded-lg border border-line bg-surface p-5 text-muted">
          Měření pracuje s vlastními nahranými dokumenty, a proto potřebuje přístupový kód.
          Výsledky na fiktivní sadě najdeš na stránce{" "}
          <Link href="/evals" className="text-accent hover:underline">Evaluace</Link>.
        </p>
      ) : (
        <>
          <div className="mt-6 flex flex-wrap items-center gap-2">
            <select
              value={templateId}
              onChange={(e) => setTemplateId(e.target.value)}
              aria-label="Šablona"
              className="h-9 rounded-md border border-line-strong bg-surface px-2"
            >
              <option value="">Všechny šablony</option>
              {templates.map((t) => (
                <option key={t.id} value={t.id}>{t.name}</option>
              ))}
            </select>
            <button
              type="button"
              disabled={!report?.rows.length}
              onClick={() =>
                api.downloadReviewCsv(templateId || undefined).catch((e: Error) => setError(e.message))
              }
              className="ml-auto h-9 rounded-md border border-line-strong px-3 hover:bg-subtle disabled:opacity-50"
            >
              Exportovat CSV
            </button>
          </div>
          {error && (
            <p role="alert" className="mt-4 rounded-md bg-err-soft px-3 py-2 text-err">{error}</p>
          )}

          {s && (
            <div className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
              <Tile label="Schválené dokumenty" value={String(s.documents)} />
              <Tile
                label="Přesnost před kontrolou"
                value={pct(s.accuracy_before_review)}
                note={`${plural(s.fields_corrected, "opravené pole", "opravená pole", "opravených polí")} z ${s.fields}`}
              />
              <Tile
                label="Medián kontroly"
                value={secs(s.review_s_median)}
                note={`změřeno u ${s.documents_timed} dokumentů`}
              />
              <div className="rounded-lg border border-line bg-surface p-4">
                <label htmlFor="manual" className="text-xs uppercase text-muted">
                  Ruční přepis, s / dokument
                </label>
                <input
                  id="manual"
                  inputMode="decimal"
                  value={manual}
                  onChange={(e) => setManual(e.target.value)}
                  placeholder="změřte a zadejte"
                  className="mt-1 h-9 w-full rounded-md border border-line-strong px-2 tabular-nums"
                />
                <p className="mt-1 text-xs text-muted">
                  {saving == null
                    ? "Úspora se spočítá ze zadaného ručního času."
                    : `Úspora času kontroly oproti zadanému: ${pct(saving)}`}
                </p>
              </div>
            </div>
          )}

          {report && report.rows.length === 0 && (
            <p className="mt-6 text-muted">
              Zatím žádný schválený dokument. Nahraj dokumenty, spusť extrakci a výsledky
              zkontroluj a schval.
            </p>
          )}

          {report && report.rows.length > 0 && (
            <div className="mt-6 overflow-x-auto rounded-lg border border-line bg-surface">
              <table className="w-full text-sm">
                <thead className="text-left text-xs uppercase text-muted">
                  <tr className="border-b border-line">
                    <th className="px-4 py-3 font-medium">Dokument</th>
                    <th className="px-4 py-3 font-medium">Šablona</th>
                    <th className="px-4 py-3 text-right font-medium">Opraveno</th>
                    <th className="px-4 py-3 text-right font-medium">Kontrola</th>
                    <th className="px-4 py-3 text-right font-medium">Extrakce</th>
                    <th className="px-4 py-3 font-medium">Schváleno</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-line">
                  {report.rows.map((r) => (
                    <tr key={r.extraction_id} className="h-11">
                      <td className="max-w-64 truncate px-4">
                        <Link href={`/documents/${r.document_id}`} className="hover:text-accent">
                          {r.filename}
                        </Link>
                        {r.ocr && <span className="ml-2 text-xs text-warn">OCR</span>}
                      </td>
                      <td className="px-4 text-muted">
                        {templates.find((t) => t.id === r.template_id)?.name ?? r.template_id}
                      </td>
                      <td className="px-4 text-right tabular-nums">
                        {r.fields_corrected} / {r.fields_total}
                      </td>
                      <td className="px-4 text-right tabular-nums">{secs(r.review_s)}</td>
                      <td className="px-4 text-right tabular-nums">{secs(r.latency_s)}</td>
                      <td className="px-4 text-muted">
                        {parseTimestamp(r.reviewed_at).toLocaleString("cs-CZ")}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          <p className="mt-4 max-w-3xl text-xs text-muted">
            CSV má sloupce měřicího listu pilotu; ruční čas a další údaje doplňte ručně.
            Čas extrakce běží na serveru a nevyžaduje práci člověka.
          </p>
        </>
      )}
    </div>
  );
}

function Tile({ label, value, note }: { label: string; value: string; note?: string }) {
  return (
    <div className="rounded-lg border border-line bg-surface p-4">
      <p className="text-xs uppercase text-muted">{label}</p>
      <p className="mt-1 text-xl font-semibold tabular-nums">{value}</p>
      {note && <p className="mt-1 text-xs text-muted">{note}</p>}
    </div>
  );
}
