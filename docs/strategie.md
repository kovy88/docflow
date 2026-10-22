# Strategie: od pilotu k produktu

Stav k 20. 10. 2026. Ceny, obory i čísla jsou hypotézy; ověřuje je jen zaplacený pilot. Pravidla z [pilot/README.md](pilot/README.md) platí dál: žádné registrace, předplatné ani víceklientská platforma před ověřením poptávky.

## Komu a co

Produkt „pro každého“ si nekoupí nikdo. Docflow se prodává jednomu oboru s jednou bolestí a rozšiřuje se přes šablony.

**Zákazník:** česká firma, kde někdo ručně přepisuje údaje z PDF do Excelu nebo systému – desítky až stovky dokumentů měsíčně, chyba v částce nebo datu něco stojí.

**Slib:** PDF → ověřená tabulka. Každá hodnota má zdroj v dokumentu, pravidla hlídají součty a data, nic neodejde bez schválení člověkem.

**Proč ne konkurence:**

| Alternativa | Slabina pro tohoto zákazníka | Docflow |
|---|---|---|
| Rossum, Digitoo a další vytěžovače faktur | Zaměřené na faktury a účetní systémy, drahé nastavení | Libovolný typ dokumentu, šablona za 10 minut, výstup do Excelu |
| Chat s PDF (ChatGPT apod.) | Nedoloží zdroj, nepočítá spolehlivě, data mimo kontrolu | Citace + strana, deterministická pravidla, lidská kontrola, export blokovaný při chybě |
| Ruční přepis / brigádník | Pomalé, chyby se nehledají | Měřená úspora a chybovost na vlastních dokumentech zákazníka |

Bonus pro opatrné firmy: model u poskytovatelů bez ukládání dat, nebo lokálně (data neopustí firmu).

## Pořadí oborů

1. **Správci nájmů** – nájemní smlouvy → evidence (pilot běží).
2. **Realitní a facility firmy** – předávací protokoly, dodatky, revize.
3. **Pojišťovací makléři** – pojistky a obnovy (sdílí trh s BrokerScribe).
4. **Logistika** – CMR, dodací listy, objednávky.
5. **Účetní kanceláře** – export přijatých faktur do POHODY a Money S3 (XML) je hotový; oslovovat až po ověřeném importu v reálné Pohodě/Money. U faktur je největší konkurence.

Další obor až po zaplaceném pilotu v předchozím. Šablona a ukázkový dokument pro nový obor = zhruba den práce.

## Ceník (hypotéza)

| Položka | Cena |
|---|---|
| Pilot: 14 dní, ≤50 dokumentů, jedna šablona, měření | 4 900 Kč |
| Provoz do 100 dokumentů / měsíc | 2 900 Kč / měsíc |
| Provoz do 300 dokumentů / měsíc | 5 900 Kč / měsíc |
| Nad limit | 8 Kč / dokument |
| Šablona na míru | 3 000–5 000 Kč jednorázově |
| Lokální nasazení (data neopustí firmu) | 40 000–80 000 Kč + podpora |

Náklad modelu je kolem 0,03 Kč za dokument; cena stojí na ušetřeném čase, ne na nákladech.

## Jak se dostat k firmám

1. **Osobní oslovení** (běží): krátký e-mail, pak ukázka na třech anonymizovaných dokumentech zákazníka.
2. **Case study z pilotu:** naměřená úspora a chybovost (stránka Měření + CSV). Bez ní další prodej nezačínat.
3. **Partneři:** dodavatelé správcovského a účetního softwaru, IT firmy dělající implementace pro malé firmy, profesní asociace (realitní, správci nemovitostí, makléři). Nabídka: provize nebo white-label.
4. **Obsah:** LinkedIn příspěvky s čísly z evaluace a pilotu („co model plete a jak to chytáme“), vstup přes logicflow.cz a živé demo.

## Roadmapa s branami

| Brána | Produkt |
|---|---|
| Teď, před první platbou | Dávkové nahrání a extrakce, „Další ke kontrole“, automatické měření času kontroly a oprav (hotovo) |
| Po 1. zaplaceném pilotu | Evaluace na reálných anonymizovaných dokumentech (příjem dokumentů e-mailem hotový 22. 10. 2026, export do Pohody/Money 20. 10. 2026) |
| Po 3 platících zákaznících | Organizace a účty, fakturace, limity podle tarifu, API a webhooky pro integrátory |

Pracovní prostory pro firmy (vlastní kód, data, šablony, kvóta, měsíční přehled využití) vznikly 22. 10. 2026, aby jedna instance unesla víc pilotů najednou; registrace, uživatelské účty a fakturace dál čekají. API pro integrátory (klíče, kvóty, exporty) a veřejná zkušební stránka vznikly 21. 10. 2026 vědomě dřív než po 3 platících zákaznících, protože se na API ptá potenciální zákazník (spedice); webhooky a API klíče firem (jejich šablony a kvóta) přibyly 22. 10. 2026; registrace, uživatelské účty a fakturace dál čekají na tuto bránu.

## Co sledovat každý týden

Obchod: oslovení → odpovědi → rozhovory → vzorky → nabídky → zaplacené piloty.
Produkt: přesnost před kontrolou, medián času kontroly, úspora proti ručnímu přepisu, vyřazené dokumenty.
