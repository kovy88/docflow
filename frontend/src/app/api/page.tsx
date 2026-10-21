import type { Metadata } from "next";
import Link from "next/link";

export const metadata: Metadata = {
  title: "Docflow API pro integrátory",
  description:
    "REST API pro vytěžení dat z PDF s citací zdroje, stranou, jistotou a kontrolou pravidel. Export do Excelu, JSON, POHODY a Money S3.",
};

// Server component: lib/api and PilotOffer pull in client-only code, so read the same env.
const BASE = `${process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000"}/v1`;
const CONTACT = process.env.NEXT_PUBLIC_PILOT_EMAIL ?? "logicflow88@gmail.com";
const KEY_REQUEST = `mailto:${CONTACT}?subject=${encodeURIComponent("Docflow API – žádost o klíč")}`;

const ENDPOINTS = [
  ["GET", "/v1/templates", "Typy dokumentů (template_id), jejich pole a pravidla"],
  ["POST", "/v1/documents", "Nahrání PDF (multipart, pole file); volitelně hlavička Idempotency-Key"],
  ["GET", "/v1/documents/{id}", "Detail dokumentu"],
  ["DELETE", "/v1/documents/{id}", "Smazání PDF, textu i všech extrakcí"],
  ["POST", "/v1/documents/{id}/extract", "Spuštění vytěžení podle šablony, odpověď 202 se stavem running"],
  ["GET", "/v1/extractions/{id}", "Stav a výsledek: pole s citací, stranou, jistotou, nálezy a pravidla"],
  ["PATCH", "/v1/extractions/{id}", "Lidská kontrola: opravy hodnot a schválení (status approved)"],
  ["GET", "/v1/extractions/{id}/export?format=…", "xlsx, json, pohoda nebo money"],
] as const;

const ERRORS = [
  ["401", "missing_key, invalid_key, revoked_key", "chybí, neplatný nebo odvolaný klíč"],
  ["404", "not_found", "dokument nebo extrakce neexistuje, nebo patří jinému klíči"],
  ["409", "conflict, idempotency_conflict, extraction_running, not_reviewable", "operace teď nejde (např. export do účetnictví bez schválení)"],
  ["413", "file_too_large", "soubor nad limit velikosti nebo počtu stran"],
  ["415", "unsupported_media_type", "soubor není PDF"],
  ["422", "invalid_request, unknown_template, unknown_field, no_text", "neplatný požadavek"],
  ["429", "quota_exceeded, limit_exceeded", "vyčerpaná denní kvóta klíče nebo celkový denní limit"],
] as const;

// Real response from a local run on the fictional transport order (fields shortened to two).
const SAMPLE = `{
  "id": "3GRKSx9XRhKxqpiu_XjtDQ",
  "document_id": "7VCKELG_m3yoFvtkNJcm4w",
  "template_id": "transport_order",
  "status": "ok",
  "model": "qwen/qwen3.6-35b-a3b",
  "created_at": "2026-10-21T14:48:55.223076Z",
  "reviewed_at": null,
  "latency_s": 11.4,
  "fields": [
    {
      "key": "loading_date",
      "label": "Datum nakládky",
      "type": "date",
      "value": "2026-11-04",
      "raw": "4. 11. 2026, 8:00",
      "page": 1,
      "quote": "Datum nakládky: 4. 11. 2026, 8:00",
      "confidence": 1.0,
      "grounded": true,
      "corrected": false
    },
    {
      "key": "weight_kg",
      "label": "Hmotnost (kg)",
      "type": "number",
      "value": 18400.0,
      "raw": "18 400 kg",
      "page": 1,
      "quote": "Hrubá hmotnost: 18 400 kg",
      "confidence": 1.0,
      "grounded": true,
      "corrected": false
    }
  ],
  "issues": [],
  "rules": [
    {
      "type": "date_order",
      "description": "Datum nakládky není po Datum vykládky",
      "fields": ["loading_date", "unloading_date"],
      "result": "passed"
    }
  ]
}`;

function Code({ children }: { children: string }) {
  return (
    <pre className="mt-3 overflow-x-auto rounded-lg border border-line bg-background p-4 font-mono text-sm leading-relaxed">
      <code>{children}</code>
    </pre>
  );
}

function H2({ id, children }: { id: string; children: React.ReactNode }) {
  return <h2 id={id} className="mt-10 text-xl font-bold tracking-tight">{children}</h2>;
}

const C = ({ children }: { children: string }) => <code className="font-mono">{children}</code>;

export default function ApiDocsPage() {
  return (
    <div className="mx-auto w-full max-w-4xl px-4 py-10">
      <p className="text-sm text-muted">Dokumentace pro integrátory · verze 1</p>
      <h1 className="mt-1 text-2xl font-bold tracking-tight">Docflow API</h1>
      <p className="mt-2 max-w-2xl text-muted">
        Pošlete PDF, vyberte šablonu a dostanete hodnoty polí. Ke každé hodnotě patří doslovná
        citace z dokumentu, číslo strany, jistota modelu a příznak, zda se citace v PDF opravdu
        našla. Pravidla šablony (součty, IČO, pořadí dat) počítá deterministický kód.
      </p>
      <p className="mt-3 text-sm">
        Strojově čitelná specifikace:{" "}
        <a href={`${BASE}/docs`} className="font-medium text-accent hover:underline">OpenAPI / Swagger</a>
        {" · "}
        <a href={`${BASE}/openapi.json`} className="font-medium text-accent hover:underline">openapi.json</a>
      </p>

      <H2 id="klic">Přístup</H2>
      <p className="mt-2 text-muted">
        Každý klient dostane vlastní API klíč (<C>dfk_…</C>) s denní kvótou extrakcí. Klíč se
        posílá v hlavičce <C>{"Authorization: Bearer <klíč>"}</C>. Klient vidí jen své dokumenty.
        Klíč lze kdykoli odvolat a vydat nový.{" "}
        <a href={KEY_REQUEST} className="text-accent hover:underline">Požádat o klíč</a>
      </p>

      <H2 id="postup">Postup v příkladech</H2>
      <p className="mt-2 text-muted">
        1. Nahrání PDF. Se stejným <C>Idempotency-Key</C> a stejným souborem vrátí API původní
        dokument (200), nevznikne duplikát.
      </p>
      <Code>{`curl -X POST ${BASE}/documents \\
  -H "Authorization: Bearer $DOCFLOW_KEY" \\
  -H "Idempotency-Key: objednavka-OP-2026-0815" \\
  -F file=@objednavka.pdf
# 201 {"id": "7VCKELG_m3yoFvtkNJcm4w", "filename": "objednavka.pdf", "page_count": 1, ...}`}</Code>

      <p className="mt-5 text-muted">2. Spuštění vytěžení. Běží na pozadí, odpověď přijde hned se stavem <C>running</C>.</p>
      <Code>{`curl -X POST ${BASE}/documents/7VCKELG_m3yoFvtkNJcm4w/extract \\
  -H "Authorization: Bearer $DOCFLOW_KEY" \\
  -H "Content-Type: application/json" \\
  -d '{"template_id": "transport_order"}'
# 202 {"id": "3GRKSx9XRhKxqpiu_XjtDQ", "status": "running", ...}`}</Code>

      <p className="mt-5 text-muted">
        3. Dotazování na výsledek, dokud <C>status</C> není <C>running</C> (stačí každé 2–3 s;
        jednostránkový dokument trvá obvykle 10–60 s).
      </p>
      <Code>{`curl ${BASE}/extractions/3GRKSx9XRhKxqpiu_XjtDQ \\
  -H "Authorization: Bearer $DOCFLOW_KEY"`}</Code>

      <p className="mt-5 text-muted">4. Export. <C>xlsx</C> a <C>json</C> pro každý dokončený výsledek.</p>
      <Code>{`curl -o objednavka.xlsx \\
  "${BASE}/extractions/3GRKSx9XRhKxqpiu_XjtDQ/export?format=xlsx" \\
  -H "Authorization: Bearer $DOCFLOW_KEY"`}</Code>

      <p className="mt-5 text-muted">
        5. Přijaté faktury do účetnictví. <C>pohoda</C> a <C>money</C> (XML import) fungují jen
        pro šablonu <C>invoice</C> po lidském schválení a bez chybových nálezů. Schválení
        s případnými opravami hodnot (klíče polí podle šablony):
      </p>
      <Code>{`curl -X PATCH ${BASE}/extractions/<id> \\
  -H "Authorization: Bearer $DOCFLOW_KEY" \\
  -H "Content-Type: application/json" \\
  -d '{"values": {"due_date": "17. 9. 2026"}}'

curl -o faktura-pohoda.xml \\
  "${BASE}/extractions/<id>/export?format=pohoda" \\
  -H "Authorization: Bearer $DOCFLOW_KEY"`}</Code>

      <H2 id="odpoved">Ukázka odpovědi</H2>
      <p className="mt-2 text-muted">
        Skutečná odpověď na fiktivní objednávku přepravy, zkrácená na dvě pole. <C>value</C> je
        normalizovaná hodnota (čísla jako čísla, data jako RRRR-MM-DD), <C>raw</C> zápis
        v dokumentu. <C>status</C>: <C>ok</C> bez nálezů, <C>needs_review</C> s nálezy ke
        kontrole, <C>approved</C> po schválení, <C>failed</C> při chybě. Pravidlo má výsledek{" "}
        <C>passed</C>, <C>failed</C> nebo <C>skipped</C> (chybí hodnota).
      </p>
      <Code>{SAMPLE}</Code>

      <H2 id="endpointy">Endpointy</H2>
      <div className="mt-3 overflow-x-auto rounded-lg border border-line">
        <table className="w-full text-sm">
          <thead className="bg-background text-left text-xs uppercase text-muted">
            <tr><th className="px-3 py-2">Metoda</th><th className="px-3 py-2">Cesta</th><th className="px-3 py-2">Účel</th></tr>
          </thead>
          <tbody className="divide-y divide-line bg-surface">
            {ENDPOINTS.map(([method, path, text]) => (
              <tr key={`${method} ${path}`} className="h-11">
                <td className="px-3 font-mono">{method}</td>
                <td className="whitespace-nowrap px-3 font-mono">{path}</td>
                <td className="px-3 text-muted">{text}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="mt-3 text-sm text-muted">
        Šablony: <C>invoice</C> (faktura), <C>transport_order</C> (objednávka přepravy / CMR),{" "}
        <C>lease</C> (nájemní smlouva), <C>contract</C> (smlouva), <C>insurance_policy</C>{" "}
        (pojistka). Šablonu na míru doplním po domluvě.
      </p>

      <H2 id="chyby">Chyby a limity</H2>
      <p className="mt-2 text-muted">Každá chyba má stejný tvar:</p>
      <Code>{`{"error": {"code": "quota_exceeded", "message": "Denní kvóta 100 extrakcí je vyčerpaná (UTC)."}}`}</Code>
      <div className="mt-3 overflow-x-auto rounded-lg border border-line">
        <table className="w-full text-sm">
          <thead className="bg-background text-left text-xs uppercase text-muted">
            <tr><th className="px-3 py-2">HTTP</th><th className="px-3 py-2">code</th><th className="px-3 py-2">Význam</th></tr>
          </thead>
          <tbody className="divide-y divide-line bg-surface">
            {ERRORS.map(([status, codes, text]) => (
              <tr key={status} className="h-11">
                <td className="px-3 font-mono tabular-nums">{status}</td>
                <td className="px-3 font-mono">{codes}</td>
                <td className="px-3 text-muted">{text}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <ul className="mt-4 list-disc space-y-1 pl-5 text-sm text-muted">
        <li>PDF nejvýš 20 MB a 50 stran; skeny se čtou OCR.</li>
        <li>Denní kvóta extrakcí je u každého klíče vlastní a počítá se podle dne v UTC.</li>
        <li>Dokumenty z API se nemažou automaticky, smažete je přes DELETE. Nepoužívají se k evaluaci ani tréninku.</li>
      </ul>

      <p className="mt-10 text-sm text-muted">
        Chcete si to nejdřív vyzkoušet bez klíče?{" "}
        <Link href="/vyzkouset" className="font-medium text-accent hover:underline">Zkušební stránka</Link>
      </p>
    </div>
  );
}
