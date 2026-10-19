import type {
  Api,
  DemoDocument,
  DocumentInfo,
  EvalRun,
  Extraction,
  Template,
} from "@/lib/api";

/** Read-only implementation of the API for the public demo. Data are real results of the
 *  local model on the fictional dataset, precomputed by backend/scripts/export_demo.py. */
type DemoData = {
  documents: (DocumentInfo & { ocr_pages: number[] })[];
  extractions: Record<string, Extraction[]>;
  templates: Template[];
  demo: DemoDocument[];
  evals: EvalRun[];
  model: string;
};

export const DEMO_MESSAGE =
  "Veřejné demo je jen pro čtení. Nahrávání, extrakci a úpravy si vyzkoušíš po spuštění Docflow lokálně.";

let cache: Promise<DemoData> | null = null;
const data = () =>
  (cache ??= fetch("/demo/data.json").then((r) => {
    if (!r.ok) throw new Error("Demo data se nepodařilo načíst.");
    return r.json() as Promise<DemoData>;
  }));

const readOnly = async (): Promise<never> => {
  throw new Error(DEMO_MESSAGE);
};

async function documentById(id: number) {
  const doc = (await data()).documents.find((d) => d.id === id);
  if (!doc) throw new Error("Dokument nenalezen.");
  return doc;
}

export const demoApi: Api = {
  health: async () => ({ provider: "ollama", model: (await data()).model }),
  templates: async () => (await data()).templates,
  template: async (id) => {
    const t = (await data()).templates.find((x) => x.id === id);
    if (!t) throw new Error("Šablona nenalezena.");
    return t;
  },
  createTemplate: readOnly,
  updateTemplate: readOnly,
  deleteTemplate: readOnly,
  suggestTemplate: readOnly,
  documents: async () => [],
  document: documentById,
  upload: readOnly,
  extractions: async (documentId) =>
    (await data()).extractions[String(documentId)] ?? [],
  extract: async (documentId, templateId) => {
    const found = (await data()).extractions[String(documentId)]?.find(
      (e) => e.template_id === templateId,
    );
    if (!found)
      throw new Error(
        "V demu je pro tento dokument předpočítaná jen jeho výchozí šablona.",
      );
    return found;
  },
  extraction: async (id) => {
    const all = Object.values((await data()).extractions).flat();
    const found = all.find((e) => e.id === id);
    if (!found) throw new Error("Extrakce nenalezena.");
    return found;
  },
  review: readOnly,
  fileSource: (documentId) => `/demo/pdfs/${documentId}.pdf`,
  download: readOnly,
  checkAccess: readOnly,
  demo: async () => (await data()).demo,
  createDemo: async (file) => {
    const doc = (await data()).documents.find((d) => d.filename === file);
    if (!doc) throw new Error("Dokument nenalezen.");
    return doc;
  },
  evals: async () => (await data()).evals,
};
