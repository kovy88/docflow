const contact = process.env.NEXT_PUBLIC_PILOT_EMAIL ?? "logicflow88@gmail.com";

export function PilotOffer() {
  return (
    <section className="mb-10 rounded-xl border border-line bg-surface p-6" aria-labelledby="pilot-title">
      <p className="text-sm font-medium text-accent">Pro správce nájmů a realitní kanceláře</p>
      <h1 id="pilot-title" className="mt-2 text-3xl font-semibold tracking-tight">Z nájemních smluv do Excelu, s kontrolou každého údaje.</h1>
      <p className="mt-3 text-muted">Docflow připraví údaje ze smlouvy, ukáže jejich zdroj v PDF a umožní opravy. Po lidské kontrole dostanete přehlednou tabulku pro svou evidenci.</p>
      <p className="mt-4 font-medium">Pilot na 14 dní · do 50 smluv · 4 900 Kč</p>
      <p className="mt-2 text-sm text-muted">Jedna šablona podle vaší evidence, osobní nastavení a měření úspory času. Platba před zahájením. Navazující nabídka: 2 900 Kč měsíčně do 100 smluv; konečné podmínky a daňový režim potvrdíme v nabídce.</p>
      <a href={`mailto:${contact}?subject=${encodeURIComponent("Docflow – pilot nájemní smlouvy")}`}
        className="mt-5 inline-block rounded-md bg-accent px-4 py-2 font-medium text-white">Domluvit ukázku na vlastní evidenci</a>
      <p className="mt-2 text-sm text-muted">Kontakt: <a href={`mailto:${contact}`} className="underline">{contact}</a>. Pro první ukázku použijeme anonymizované vzorky.</p>
      <p className="mt-4 text-sm text-muted">Rozsah: samostatné české nájemní smlouvy v PDF. Dodatky, právní výklad a výpočty valorizací nejsou součástí pilotu. Úsporu i přesnost ověříme na vašich dokumentech.</p>
      <details className="mt-4 text-sm text-muted">
        <summary className="cursor-pointer font-medium">Jak pracujeme s dokumenty</summary>
        <p className="mt-2">Hostovaná varianta ukládá PDF a výsledky do databáze. Text z PDF nebo lokálního OCR posílá externímu poskytovateli modelu přes OpenRouter. Frontend běží na Vercelu, backend na Renderu a databáze v Supabase. Pilot má vlastní instanci, databázi a přístupový kód.</p>
        <p className="mt-2">Před předáním neanonymizovaných smluv dohodneme podmínky zpracování, konkrétního poskytovatele modelu a dobu uchování. Návrh je smazat pracovní data do 30 dnů po skončení pilotu. Veřejné demo níže používá fiktivní dokumenty a předpočítané výsledky.</p>
      </details>
    </section>
  );
}
