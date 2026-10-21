"use client";

import { useRouter } from "next/navigation";
import { useRef, useState } from "react";
import { api, type Rule, type Template, type TemplateField } from "@/lib/api";
import { useLive } from "@/lib/access";

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
  "min-h-9 w-full rounded-md border border-line-strong bg-surface px-2 py-1.5 text-sm focus:border-accent disabled:bg-background";

export function TemplateEditor({
  initial,
  mode,
}: {
  initial: Template;
  mode: Mode;
}) {
  const router = useRouter();
  const live = useLive();
  const keyBeforeEdit = useRef("");
  const [t, setT] = useState<Template>(initial);
  const [idTouched, setIdTouched] = useState(mode !== "new");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const readOnly = mode === "view";

  const keysOf = (type: TemplateField["type"]) =>
    t.fields.filter((f) => f.type === type && f.key);

  function setField(i: number, patch: Partial<TemplateField>) {
    setT((prev) => ({
      ...prev,
      fields: prev.fields.map((f, j) => (j === i ? { ...f, ...patch } : f)),
      rules: patch.key && prev.fields[i].key ? renameReferences(prev.rules, prev.fields[i].key, patch.key) : prev.rules,
    }));
  }

  function renameReferences(rules: Rule[], from: string, to: string): Rule[] {
    return rules.map((rule) => {
      const rename = (key: string) => key === from ? to : key;
      if (rule.type === "ico") return { ...rule, field: rename(rule.field) };
      if (rule.type === "date_order") return { ...rule, earlier: rename(rule.earlier), later: rename(rule.later) };
      return { ...rule, fields: rule.fields.map(rename), equals: rename(rule.equals) };
    });
  }

  function removeField(i: number) {
    const key = t.fields[i].key;
    const used = key && t.rules.some((r) => r.type === "ico" ? r.field === key : r.type === "date_order" ? r.earlier === key || r.later === key : r.equals === key || r.fields.includes(key));
    if (used) {
      setError(`Pole „${t.fields[i].label || key}“ používá pravidlo kontroly. Nejdřív uprav nebo odeber toto pravidlo.`);
      return;
    }
    setError(null);
    setT({ ...t, fields: t.fields.filter((_, j) => j !== i) });
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
    if (!t.name.trim()) { setError("Vyplň název šablony."); return; }
    const validKey = /^[a-z][a-z0-9_]*$/;
    if (!validKey.test(t.id)) { setError("Identifikátor musí začínat malým písmenem a obsahovat jen malá písmena bez diakritiky, číslice a podtržítka."); return; }
    if (t.fields.length === 0) { setError("Přidej alespoň jedno pole."); return; }
    if (t.fields.some((field) => !field.label.trim() || !validKey.test(field.key))) {
      setError("Každé pole musí mít popisek a klíč začínající malým písmenem, bez diakritiky a mezer."); return;
    }
    if (new Set(t.fields.map((field) => field.key)).size !== t.fields.length) { setError("Každé pole musí mít jiný klíč."); return; }
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
    router.push(`/templates/new?draft=${crypto.randomUUID()}`);
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
          <textarea
            rows={3}
            className={`${input} resize-y`}
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
              className="grid gap-2 px-4 py-3 sm:grid-cols-[minmax(0,1fr)_110px_auto_auto]"
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
                <div className="flex items-center gap-2">
                {([-1, 1] as const).map((direction) => (
                  <button type="button" key={direction}
                    aria-label={`${direction === -1 ? "Posunout nahoru" : "Posunout dolů"}: ${f.label}`}
                    disabled={i + direction < 0 || i + direction >= t.fields.length}
                    className="text-sm text-accent disabled:opacity-30"
                    onClick={() => setT((prev) => {
                      const fields = [...prev.fields];
                      [fields[i], fields[i + direction]] = [fields[i + direction], fields[i]];
                      return { ...prev, fields };
                    })}>{direction === -1 ? "↑" : "↓"}</button>
                ))}
                <button
                  type="button"
                  onClick={() => removeField(i)}
                  className="text-sm text-err hover:underline"
                >
                  Odebrat
                </button>
                </div>
              )}
              <details className="sm:col-span-4">
                <summary className="cursor-pointer text-sm text-accent" aria-label={`Pokročilé nastavení: ${f.label || `Pole ${i + 1}`}`}>
                  Pokročilé nastavení
                </summary>
                <div className="mt-3 grid gap-3 rounded-md bg-background p-3">
                  <label className="text-sm">
                    <span className="text-muted">Klíč pole</span>
                    <input
                      className={`${input} font-mono`}
                      placeholder="klic_pole"
                      value={f.key}
                      disabled={readOnly}
                      onFocus={() => { keyBeforeEdit.current = f.key; }}
                      onChange={(e) => setT((prev) => ({ ...prev, fields: prev.fields.map((field, j) => j === i ? { ...field, key: e.target.value } : field) }))}
                      onBlur={(e) => {
                        const from = keyBeforeEdit.current;
                        const to = e.target.value;
                        if (from && to && from !== to) setT((prev) => ({ ...prev, rules: renameReferences(prev.rules, from, to) }));
                      }}
                      aria-label="Klíč pole"
                    />
                  </label>
                  <label className="text-sm">
                    <span className="text-muted">Pokyny pro model</span>
                    <textarea
                      rows={3}
                      className={`${input} resize-y`}
                      placeholder="Např. Celková částka včetně DPH."
                      value={f.description}
                      disabled={readOnly}
                      onChange={(e) => setField(i, { description: e.target.value })}
                      aria-label="Nápověda pro model"
                    />
                  </label>
                </div>
              </details>
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
        <p role="alert" className="rounded-md bg-err-soft px-3 py-2 text-sm text-err">
          {error}
        </p>
      )}

      <div className="flex flex-wrap items-center gap-3">
        {readOnly ? (
          <>
            <span className="text-sm text-muted">
              {live ? "Vestavěná šablona je jen pro čtení." : "Ukázková šablona je jen pro čtení."}
            </span>
            {live && <button
              type="button"
              onClick={duplicate}
              className="rounded-md border border-line-strong bg-surface px-3 py-1.5 font-medium text-foreground hover:bg-subtle"
            >
              Vytvořit kopii
            </button>}
          </>
        ) : (
          <>
            <button
              type="button"
              onClick={save}
              disabled={saving}
              className="rounded-md bg-accent px-3 py-1.5 font-medium text-white hover:bg-accent-hover disabled:opacity-50"
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
