# Nájemní smlouvy → Excel: první placený pilot

Pracovní stav k 19. 10. 2026. Cíl: do čtyř týdnů jeden zaplacený pilot. Kapacita 15–25 hodin týdně. Poptávka, cena a úspora nejsou potvrzené; neexistuje zatím klientská tabulka ani sada reálných nájemních smluv.

Obchodní příprava: sestaveno 30 kandidátů; první vlna 15 zpráv odeslána 19. 10. 2026 z uživatelem potvrzeného účtu logicflow88@gmail.com s podpisem Matěj Koval | LogicFlow. Gmail u všech potvrdil SENT. Doručení a odpovědi nejsou potvrzené. Soukromá evidence, přesné texty a ID zpráv jsou v ignorované lokální složce `validation/lease-pilot/`; kontakty se nepublikují do repozitáře.

## Připravený produkt

- Šablona `lease`: role stran, adresa předmětu nájmu, data, nájemné, měna, období platby, služby a zálohy, celková platba, kauce a výpovědní lhůta. Pole i jejich pořadí lze nastavit zkopírováním vestavěné šablony v existujícím editoru.
- V přehledu dokumentů výběr a Excel export do 100 dokumentů. Použije nejnovější extrakci každého dokumentu; server odmítne celou dávku, pokud některý výsledek není schválený, nemá datum kontroly nebo obsahuje chybový nález. Šablona a sloupce musí odpovídat.
- Dávkové zpracování: víc PDF najednou, jedna šablona a extrakce pro všechny (nejvýše 100, celá dávka se odmítne, pokud nestačí denní limit). Seznam ukazuje stav každého dokumentu a odkaz „Další ke kontrole“.
- Stránka **Měření**: aplikace sama zaznamená čas kontroly (jen při otevřené kartě) a počet opravených polí; souhrn a CSV se sloupci [měřicího listu](measurement.md). Ruční základ se doplňuje ručně.
- Smazání odstraní PDF, OCR text i všechny extrakce dokumentu. Běžící extrakci je nutné nechat doběhnout; zálohy mají vlastní životní cyklus.
- Veřejná nabídka pilotu a kontakt přes `NEXT_PUBLIC_PILOT_EMAIL`. Bez kontaktního formuláře a bez automatického sběru smluv.
- [Provoz, oddělená instance a obnova](operations.md), [návrh podmínek a zpracování dat](terms.md), [scénář prodeje a dodání](sales.md), [měření přínosu](measurement.md).

## Čtyři týdny a rozhodovací body

| Období | Práce | Podmínka úspěchu |
|---|---|---|
| 19.–25. 10. | 30 kandidátů, první vlna 15 osobních oslovení, pět rozhovorů | Dva potvrzené problémy, dvě cílové tabulky a ideálně 2×3 anonymizované smlouvy |
| 26. 10.–1. 11. | Ukázka na vzorcích, uzamčení sloupců, nejvýše tři nabídky | Jeden konkrétní zájemce; nezačínat integrace bez poptávky |
| 2.–8. 11. | Připomenutí, objednávka, platba, založení odděleného pilotu | Zaplacený pilot a dohodnuté zpracování dat |
| 9.–15. 11. | Zpracování dávky, měření, předání, nabídka pokračování | ≥30 smluv, pokud dodány; ≥50% úspora; rozhodnutí o pokračování |

Termíny jsou cíle. Čtrnáctidenní pilot začíná až po platbě a dodání podkladů; při pozdějším prodeji se jeho konec posune.

Obchodní rozpočet: v prvním týdnu zhruba 2/3 času rozhovory a 1/3 ukázka. Technická práce před klientem maximálně 25–30 hodin celkem; evidovat skutečný čas. Žádné registrace, předplatné ani víceklientská platforma před ověřením poptávky.

Po deseti relevantních rozhovorech bez zájmu o vzorky či placený pilot zastavit rozšiřování a přehodnotit proces. Po prvních pěti osloveních bez odpovědi nejprve zkontrolovat kontakt a srozumitelnost nabídky. Pozitivní reakce není objednávka; zaplacení je samostatná metrika.

## Co ještě vyžaduje skutečný klient

Reakce na oslovení, rozhovory, anonymizované vzorky, uzamčené sloupce, smluvní strany a daňový režim nabídky, konkrétní modelový poskytovatel, klientské nasazení, produkční obnova, platba a reálné měření. Nic z toho se nepovažuje za splněné jen přidáním kódu či této dokumentace.
