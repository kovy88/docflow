"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { api, type Rule, type Template, type TemplateField } from "@/lib/api";

type Mode = "new" | "edit" | "view";

const TYPES: { id: TemplateField["type"]; label: string }[] = [
  { id: "string", label: "Text" },
  { id: "number", label: "Číslo" },
  { id: "date", label: "Datum" },
];

const RULE_LABELS: Record<Rule["type"], string> = {
  ico: "Kontrolní součet IČO",
  sum: "Součet polí",
  date_order: "Pořadí dat",
};

export const slug = (text: string) =>
  text
    .normalize("NFKD")
    .replace(/[̀-ͯ]/g, "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "")
    .replace(/^(\d)/, "t_$1")
    .slice(0, 48);

const input =
  "w-full rounded-md border border-line bg-surface px-2 py-1.5 text-sm focus:border-accent focus:outline-none disabled:bg-background";

export function TemplateEditor({
  initial,
  mode,
}: {
  initial: Template;
  mode: Mode;
}) {
  const router = useRouter();
  const [t, setT] = useState<Template>(initial);
  const [idTouched, setIdTouched] = useState(mode !== "new");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const readOnly = mode === "view";

  const keysOf = (type: TemplateField["type"]) =>
    t.fields.filter((f) => f.type === type && f.key);

  function setField(i: number, patch: Partial<TemplateField>) {
    setT({
      ...t,
      fields: t.fields.map((f, j) => (j === i ? { ...f, ...patch } : f)),
    });
  }

  function setRule(i: number, rule: Rule) {
    setT({ ...t, rules: t.rules.map((r, j) => (j === i ? rule : r)) });
  }

  function addRule(type: Rule["type"]) {
    const first = (kind: TemplateField["type"], n = 0) =>
      keysOf(kind)[n]?.key ?? "";
    const rule: Rule =
      type === "ico"
        ? { type, field: first("string") }
        : type === "sum"
          ? {
              type,
              fields: [first("number"), first("number", 1)],
              equals: first("number", 2),
            }
          : { type, earlier: first("date"), later: first("date", 1) };
    setT({ ...t, rules: [...t.rules, rule] });
  }

  async function save() {
    setSaving(true);
    setError(null);
    try {
      const saved =
        mode === "new"
          ? await api.createTemplate(t)
          : await api.updateTemplate(t);
      router.push(`/templates/${saved.id}`);
      router.refresh();
      setSaving(false);
    } catch (e) {
      setError((e as Error).message);
      setSaving(false);
    }
  }

  async function remove() {
    if (
      !window.confirm(
        `Smazat šablonu „${t.name}“? Dřívější extrakce zůstanou zachované.`,
      )
    )
      return;
    try {
      await api.deleteTemplate(t.id);
      router.push("/templates");
    } catch (e) {
      setError((e as Error).message);
    }
  }

  function duplicate() {
    try {
      sessionStorage.setItem(
        "docflow:template-draft",
        JSON.stringify({
          ...t,
          id: `${t.id}_kopie`,
          name: `${t.name} (kopie)`,
          builtin: undefined,
        }),
      );
    } catch {
      /* storage unavailable: open an empty editor */
    }
    router.push("/templates/new");
  }

  const fieldSelect = (
    value: string,
    kind: TemplateField["type"],
    onChange: (v: string) => void,
    label: string,
  ) => (
    <select
      value={value}
      onChange={(e) => onChange(e.target.value)}
      disabled={readOnly}
      aria-label={label}
      className={`${input} w-auto min-w-36`}
    >
      <option value="">— pole —</option>
      {keysOf(kind).map((f) => (
        <option key={f.key} value={f.key}>
          {f.label || f.key}
        </option>
      ))}
    </select>
  );

  return (
    <div className="space-y-6">
      <section className="grid gap-3 rounded-lg border border-line bg-surface p-4 sm:grid-cols-2">
        <label className="text-sm">
          <span className="text-muted">Název</span>
          <input
            className={input}
            value={t.name}
            disabled={readOnly}
            onChange={(e) =>
              setT({
                ...t,
                name: e.target.value,
                id: idTouched ? t.id : slug(e.target.value),
              })
            }
          />
        </label>
        <label className="text-sm">
          <span className="text-muted">Identifikátor</span>
          <input
            className={`${input} font-mono`}
            value={t.id}
            disabled={mode !== "new"}
            onChange={(e) => {
              setIdTouched(true);
              setT({ ...t, id: e.target.value });
            }}
          />
        </label>
        <label className="text-sm sm:col-span-2">
          <span className="text-muted">
            Popis typu dokumentu (pomáhá modelu)
          </span>
          <input
            className={input}
            value={t.description}
            disabled={readOnly}
            onChange={(e) => setT({ ...t, description: e.target.value })}
          />
        </label>
      </section>

      <section className="rounded-lg border border-line bg-surface">
        <h2 className="border-b border-line px-4 py-3 font-semibold">
          Pole ({t.fields.length})
        </h2>
        <div className="divide-y divide-line">
          {t.fields.map((f, i) => (
            <div
              key={i}
              className="grid gap-2 px-4 py-3 sm:grid-cols-[1fr_1fr_110px_auto_auto]"
            >
              <input
                className={input}
                placeholder="Popisek, např. Číslo faktury"
                value={f.label}
                disabled={readOnly}
                onChange={(e) =>
                  setField(i, {
                    label: e.target.value,
                    ...(f.key === "" || f.key === slug(f.label)
                      ? { key: slug(e.target.value) }
                      : {}),
                  })
                }
                aria-label="Popisek pole"
              />
              <input
                className={`${input} font-mono`}
                placeholder="klic_pole"
                value={f.key}
                disabled={readOnly}
                onChange={(e) => setField(i, { key: e.target.value })}
                aria-label="Klíč pole"
              />
              <select
                className={input}
                value={f.type}
                disabled={readOnly}
                onChange={(e) =>
                  setField(i, { type: e.target.value as TemplateField["type"] })
                }
                aria-label="Typ pole"
              >
                {TYPES.map((x) => (
                  <option key={x.id} value={x.id}>
                    {x.label}
                  </option>
                ))}
              </select>
              <label className="flex items-center gap-1.5 text-sm">
                <input
                  type="checkbox"
                  checked={f.required}
                  disabled={readOnly}
                  onChange={(e) => setField(i, { required: e.target.checked })}
                />
                povinné
              </label>
              {!readOnly && (
                <button
                  type="button"
                  onClick={() =>
                    setT({ ...t, fields: t.fields.filter((_, j) => j !== i) })
                  }
                  className="text-sm text-err hover:underline"
                >
                  Odebrat
                </button>
              )}
              <input
                className={`${input} sm:col-span-5`}
                placeholder="Nápověda pro model, např. „Celková částka včetně DPH.“"
                value={f.description}
                disabled={readOnly}
                onChange={(e) => setField(i, { description: e.target.value })}
                aria-label="Nápověda pro model"
              />
            </div>
          ))}
        </div>
        {!readOnly && (
          <button
            type="button"
            onClick={() =>
              setT({
                ...t,
                fields: [
                  ...t.fields,
                  {
                    key: "",
                    label: "",
                    type: "string",
                    description: "",
                    required: true,
                  },
                ],
              })
            }
            className="m-4 text-sm font-medium text-accent hover:underline"
          >
            + Přidat pole
          </button>
        )}
      </section>

      <section className="rounded-lg border border-line bg-surface">
        <h2 className="border-b border-line px-4 py-3 font-semibold">
          Pravidla kontroly ({t.rules.length})
        </h2>
        <div className="divide-y divide-line">
          {t.rules.length === 0 && (
            <p className="px-4 py-3 text-sm text-muted">
              Pravidla se ověřují deterministicky v kódu, ne modelem.
            </p>
          )}
          {t.rules.map((r, i) => (
            <div
              key={i}
              className="flex flex-wrap items-center gap-2 px-4 py-3 text-sm"
            >
              <span className="w-40 font-medium">{RULE_LABELS[r.type]}</span>
              {r.type === "ico" &&
                fieldSelect(
                  r.field,
                  "string",
                  (v) => setRule(i, { ...r, field: v }),
                  "Pole s IČO",
                )}
              {r.type === "sum" && (
                <>
                  {r.fields.map((k, n) => (
                    <span key={n} className="flex items-center gap-2">
                      {n > 0 && "+"}
                      {fieldSelect(
                        k,
                        "number",
                        (v) =>
                          setRule(i, {
                            ...r,
                            fields: r.fields.map((x, m) => (m === n ? v : x)),
                          }),
                        `Sčítanec ${n + 1}`,
                      )}
                    </span>
                  ))}
                  {!readOnly && (
                    <button
                      type="button"
                      className="text-accent"
                      onClick={() =>
                        setRule(i, { ...r, fields: [...r.fields, ""] })
                      }
                    >
                      + pole
                    </button>
                  )}
                  =
                  {fieldSelect(
                    r.equals,
                    "number",
                    (v) => setRule(i, { ...r, equals: v }),
                    "Výsledek",
                  )}
                </>
              )}
              {r.type === "date_order" && (
                <>
                  {fieldSelect(
                    r.earlier,
                    "date",
                    (v) => setRule(i, { ...r, earlier: v }),
                    "Dřívější datum",
                  )}
                  nejpozději
                  {fieldSelect(
                    r.later,
                    "date",
                    (v) => setRule(i, { ...r, later: v }),
                    "Pozdější datum",
                  )}
                </>
              )}
              {!readOnly && (
                <button
                  type="button"
                  onClick={() =>
                    setT({ ...t, rules: t.rules.filter((_, j) => j !== i) })
                  }
                  className="ml-auto text-err hover:underline"
                >
                  Odebrat
                </button>
              )}
            </div>
          ))}
        </div>
        {!readOnly && (
          <div className="flex flex-wrap gap-3 p-4 text-sm">
            {(Object.keys(RULE_LABELS) as Rule["type"][]).map((type) => (
              <button
                key={type}
                type="button"
                onClick={() => addRule(type)}
                className="font-medium text-accent hover:underline"
              >
                + {RULE_LABELS[type]}
              </button>
            ))}
          </div>
        )}
      </section>

      {error && (
        <p className="rounded-md bg-err-soft px-3 py-2 text-sm text-err">
          {error}
        </p>
      )}

      <div className="flex flex-wrap items-center gap-3">
        {readOnly ? (
          <>
            <span className="text-sm text-muted">
              Vestavěná šablona je jen pro čtení.
            </span>
            <button
              type="button"
              onClick={duplicate}
              className="rounded-md bg-foreground px-3 py-1.5 font-medium text-white hover:opacity-90"
            >
              Vytvořit kopii
            </button>
          </>
        ) : (
          <>
            <button
              type="button"
              onClick={save}
              disabled={saving}
              className="rounded-md bg-accent px-3 py-1.5 font-medium text-white hover:opacity-90 disabled:opacity-50"
            >
              {saving
                ? "Ukládám…"
                : mode === "new"
                  ? "Vytvořit šablonu"
                  : "Uložit změny"}
            </button>
            {mode === "edit" && (
              <button
                type="button"
                onClick={remove}
                className="text-sm text-err hover:underline"
              >
                Smazat šablonu
              </button>
            )}
          </>
        )}
      </div>
    </div>
  );
}
