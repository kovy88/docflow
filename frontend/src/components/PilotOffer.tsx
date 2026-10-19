import { REPO_URL } from "@/lib/api";

export const CONTACT = process.env.NEXT_PUBLIC_PILOT_EMAIL ?? "logicflow88@gmail.com";
export const contactHref = (subject: string) =>
  `mailto:${CONTACT}?subject=${encodeURIComponent(subject)}`;

const STEPS = [
  {
    title: "Nahrajete PDF",
    text: "Textové i skenované dokumenty do 20 MB.",
  },
  {
    title: "Docflow vytáhne údaje",
    text: "Podle šablony pro váš typ dokumentu. U každého údaje je citace z PDF a kontrola pravidly (IČO, součty, pořadí dat).",
  },
  {
    title: "Zkontrolujete a exportujete",
    text: "Opravíte, co nesedí, schválíte a stáhnete tabulku do Excelu.",
  },
];

/** Static preview of the signature pattern: value + source + check. Fictional data. */
function HeroPreview() {
  return (
    <figure className="rounded-lg border border-line bg-surface shadow-[var(--shadow-float)]">
      <div className="flex items-center justify-between border-b border-line px-5 py-3">
        <span className="text-sm font-medium">Nájemní smlouva.pdf</span>
        <span className="rounded-full bg-warn-bg px-2 py-0.5 text-xs font-medium text-warn">
          Ke kontrole
        </span>
      </div>
      <div className="grid gap-5 p-5">
        <div className="rounded-md bg-subtle p-4 text-sm leading-relaxed text-muted">
          … nájemce se zavazuje platit měsíční nájemné ve výši{" "}
          <mark className="rounded-sm bg-[var(--highlight)] px-0.5 text-ink">
            18 500 Kč
          </mark>{" "}
          vždy do 15. dne kalendářního měsíce …
        </div>
        <dl className="grid gap-3">
          <Field label="Měsíční nájemné" value="18 500 Kč" source="str. 2" ok />
          <Field label="Začátek nájmu" value="1. 3. 2026" source="str. 1" ok />
          <Field label="IČO pronajímatele" value="27596431" source="str. 1" warn="neplatný kontrolní součet" />
        </dl>
      </div>
      <figcaption className="border-t border-line px-5 py-2.5 text-xs text-muted">
        Fiktivní ukázka
      </figcaption>
    </figure>
  );
}

function Field({ label, value, source, ok, warn }: {
  label: string; value: string; source: string; ok?: boolean; warn?: string;
}) {
  return (
    <div className="flex items-baseline justify-between gap-4 border-b border-line pb-3 last:border-0 last:pb-0">
      <div>
        <dt className="text-xs text-muted">{label}</dt>
        <dd className="mt-0.5 font-medium tabular-nums">{value}</dd>
      </div>
      <div className="text-right text-xs">
        <span className="text-accent">Zdroj · {source} ↗</span>
        <p className={`mt-0.5 ${ok ? "text-ok" : "text-warn"}`}>
          {ok ? "✓ ověřeno" : `! ${warn}`}
        </p>
      </div>
    </div>
  );
}

export function Hero() {
  return (
    <section className="grid items-center gap-12 lg:grid-cols-[1.1fr_1fr]">
      <div>
        <p className="text-sm font-medium text-accent">Extrakce dat z PDF</p>
        <h1 className="mt-3 text-4xl font-bold tracking-[-0.02em] md:text-5xl">
          Z PDF do tabulky. U každého údaje vidíte, odkud je.
        </h1>
        <p className="mt-5 max-w-xl text-lg text-muted">
          Docflow vytáhne údaje z faktur, smluv, nájmů nebo pojistek, ke každému
          ukáže citaci v dokumentu a zkontroluje ho pravidly. Vám zbude jen
          kontrola a export.
        </p>
        <div className="mt-8 flex flex-wrap items-center gap-3">
          <a href="#demo"
            className="inline-flex h-10 items-center rounded-md bg-accent px-5 font-medium text-white transition hover:bg-accent-hover">
            Vyzkoušet demo
          </a>
          <a href={contactHref("Docflow – ukázka na vlastních dokumentech")}
            className="inline-flex h-10 items-center rounded-md border border-line-strong bg-surface px-5 font-medium transition hover:border-accent">
            Domluvit ukázku
          </a>
        </div>
        <p className="mt-3 text-sm text-muted">Demo bez registrace, na fiktivních dokumentech.</p>
      </div>
      <HeroPreview />
    </section>
  );
}

export function Steps() {
  return (
    <section aria-labelledby="steps-title">
      <h2 id="steps-title" className="text-2xl font-bold tracking-[-0.01em]">Jak to funguje</h2>
      <ol className="mt-6 grid gap-4 md:grid-cols-3">
        {STEPS.map((s, i) => (
          <li key={s.title} className="rounded-lg border border-line bg-surface p-6">
            <span className="grid h-7 w-7 place-items-center rounded-full bg-accent-soft text-sm font-bold text-accent">
              {i + 1}
            </span>
            <h3 className="mt-4 font-bold">{s.title}</h3>
            <p className="mt-1.5 text-muted">{s.text}</p>
          </li>
        ))}
      </ol>
    </section>
  );
}

export function PilotOffer() {
  return (
    <section aria-labelledby="pilot-title">
      <h2 id="pilot-title" className="text-2xl font-bold tracking-[-0.01em]">Nasazení na vaše dokumenty</h2>
      <div className="mt-6 grid gap-4 md:grid-cols-2">
        <div className="flex flex-col rounded-lg border border-accent bg-surface p-6">
          <p className="text-sm font-medium text-accent">Pilot · nájemní smlouvy</p>
          <p className="mt-2 text-2xl font-bold tabular-nums">4 900 Kč</p>
          <p className="text-muted">14 dní · do 50 smluv</p>
          <ul className="mt-4 grid gap-1.5">
            <li>✓ Šablona podle vaší evidence</li>
            <li>✓ Osobní nastavení a export do Excelu</li>
            <li>✓ Měření úspory času na vašich smlouvách</li>
          </ul>
          <p className="mt-4 text-sm text-muted">
            Poté 2 900 Kč měsíčně do 100 smluv. Platba před zahájením, konečné
            podmínky a daňový režim potvrdíme v nabídce. Mimo rozsah: dodatky,
            právní výklad a výpočty valorizací.
          </p>
          <a href={contactHref("Docflow – pilot nájemní smlouvy")}
            className="mt-6 inline-flex h-10 items-center justify-center self-start rounded-md bg-accent px-5 font-medium text-white transition hover:bg-accent-hover">
            Domluvit pilot
          </a>
        </div>
        <div className="flex flex-col rounded-lg border border-line bg-surface p-6">
          <p className="text-sm font-medium text-muted">Jiné dokumenty</p>
          <p className="mt-2 text-2xl font-bold">Faktury, smlouvy, pojistky…</p>
          <p className="mt-4 text-muted">
            Šablonu připravíme podle toho, jaké údaje z dokumentů potřebujete.
            První ukázku uděláme na anonymizovaných vzorcích, cenu domluvíme podle
            objemu.
          </p>
          <a href={contactHref("Docflow – vlastní typ dokumentu")}
            className="mt-auto inline-flex h-10 items-center justify-center self-start rounded-md border border-line-strong px-5 font-medium transition hover:border-accent">
            Napsat na {CONTACT}
          </a>
        </div>
      </div>
    </section>
  );
}

export function DataAndTech() {
  return (
    <section className="grid gap-6 border-t border-line pt-10 text-sm text-muted md:grid-cols-2">
      <details>
        <summary className="cursor-pointer font-medium text-ink">Jak pracujeme s dokumenty</summary>
        <p className="mt-2">Hostovaná varianta ukládá PDF a výsledky do databáze. Text z PDF nebo lokálního OCR posílá externímu poskytovateli modelu přes OpenRouter. Frontend běží na Vercelu, backend na Renderu a databáze v Supabase. Pilot má vlastní instanci, databázi a přístupový kód.</p>
        <p className="mt-2">Před předáním neanonymizovaných dokumentů dohodneme podmínky zpracování, konkrétního poskytovatele modelu a dobu uchování. Návrh je smazat pracovní data do 30 dnů po skončení pilotu.</p>
      </details>
      <div>
        <p className="font-medium text-ink">Pro vývojáře</p>
        <p className="mt-2">
          Výsledky v demu jsou skutečný výstup lokálního modelu přes Ollamu,
          předpočítaný na fiktivní evaluační sadě.{" "}
          <a href={REPO_URL} className="font-medium text-accent hover:underline">
            Zdrojový kód a evaluace na GitHubu →
          </a>
        </p>
      </div>
    </section>
  );
}
