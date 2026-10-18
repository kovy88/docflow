# DocFlow

**Aplikace (portfolio):** obecná extrakce dat z dokumentů podle šablon, s citací zdroje u každé hodnoty, kontrolou pravidel a ruční kontrolou. Backend [backend/](backend/README.md) (FastAPI, lokální Qwen přes Ollamu), UI [frontend/](frontend/README.md) (Next.js). Níže je původní validace trhu pro makléře.

## Validace Document QA

První experiment: **kontrola kompletnosti a konzistence podkladů před obnovou firemního majetkového a odpovědnostního pojištění** u českých makléřů. Český trh uživatel potvrdil 17. 10. 2026. Konkrétní workflow a druh pojištění zůstávají pracovními hypotézami pro rozhovory.

Stav k 17. 10. 2026: uživatel chce nejdřív prohlédnout přesné zprávy a podobu produktu. Požadovaný odesílatel je **[e-mail odstraněn]**; účet je připojený jako **Freelance** a obsahuje 20 nových neodeslaných konceptů, ověřených zpětným načtením. Původních 20 konceptů ve starém účtu zůstává neodeslaných a nemá se použít. 17. 10. 2026 byly koncepty přepsány na verzi 2: místo hovoru žádají o krátkou odpověď e-mailem a nabízejí vyzkoušení BrokerScribe (stejný segment, už funkční nástroj). **Žádné uskutečněné rozhovory, získané zákaznické případy ani potvrzené platby.** Veřejné profily dokládají zaměření firem, nikoli potřebu produktu nebo ochotu platit.

## Klikací ukázka k prohlédnutí

[Otevřít lokální ukázku](http://127.0.0.1:8765/) — vysvětlení služby, report se smyšlenými dokumenty a přesné texty všech 20 zpráv. Zdroj a spuštění jsou v [prototype/README.md](prototype/README.md). Ukázka provádí tři deterministické kontroly nad připravenými hodnotami; nemá OCR, LLM ani zpracování skutečných PDF/XLSX.

## Začni zde

Aktuální provádění plánu: [stav a další krok](validation/execution.md). První vlna má [20 konkrétních personalizovaných zpráv](validation/outreach/review.md); 20 aktuálních neodeslaných konceptů je v účtu Freelance a jejich identifikátory v [evidenci](validation/outreach/gmail-drafts-freelance.json).

1. Vyber prvních pět firem z [prioritizovaného seznamu](validation/prospects.md). Upřednostni vlastní doporučení a osobní vazby, pokud existují.
2. Použij připravené [zprávy a scénář rozhovoru](validation/discovery.md). Zprávy jsou pouze návrhy; nic nebylo odesláno.
3. Každý rozhovor zaznamenej pomocí [šablony](validation/templates/interview.md). Kontaktované firmy aktualizuj v [CSV](validation/prospects.csv).
4. Rozhoduj podle [hypotéz a kritérií](validation/plan.md), ne podle počtu pozitivních reakcí.
5. Po doložení problému nabídni [konkrétní placený pilot](validation/pilot-offer.md). Výsledky měř podle [evaluačního protokolu](validation/evaluation.md).

## Připravené výstupy

| Soubor | Použití |
|---|---|
| [Plán na 30 dní](validation/plan.md) | Hypotézy, termíny, evidence a rozhodnutí pokračovat / změnit segment / skončit |
| [40 kandidátů](validation/prospects.md) | Dvě vlny po 20, veřejné kontakty, zdroje, známé vazby a neověřené předpoklady |
| [Discovery](validation/discovery.md) | Pět konkrétních úvodů, zprávy, nenavádějící rozhovor a žádost o data |
| [Nabídka pilotu](validation/pilot-offer.md) | Jeden workflow, 20 kontrol, 50 případů, návrh ceny 30 000 Kč |
| [Kontroly a ukázkový report](validation/checks-and-report.md) | Návrh pravidel a výslovně syntetická ukázka s důkazy a stavem „nelze vyhodnotit“ |
| [Vyhodnocení](validation/evaluation.md) | Recall, falešné nálezy, čas, náklady a ROI bez vymyšlených vstupů |
| [Kontext a zdroje](validation/research/context.md) | Co jsme ověřili, co ne, alternativy a omezení veřejného průzkumu |

Pracovní složka byla při zahájení prázdná a neobsahovala Git ani existující extraction pipeline. Lokální prototyp vznikl na následný požadavek prohlédnout podobu produktu; nejde o implementaci SaaS. Technické možnosti dřívějšího DocFlow je nutné ověřit na skutečných souborech.
