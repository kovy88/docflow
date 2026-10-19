# Návrh podmínek pilotu a zpracování dat

Pracovní smluvní podklad, nikoli již uzavřená smlouva. Před použitím doplnit strany, přesné služby, přílohy a skutečné poskytovatele; ověřit soulad konečného znění s konkrétním provozem. Role stran a obsah zpracovatelské dohody vycházejí z [článku 28 GDPR](https://eur-lex.europa.eu/legal-content/CS/TXT/?uri=celex%3A32016R0679); tato šablona sama neprokazuje splnění povinností.

## Objednávka

Dodavatel/zpracovatel: [firma/jméno, IČO, sídlo, kontaktní osoba a e-mail]. Odběratel: [firma, IČO, sídlo a oprávněná osoba]. Role odběratele při správě dokumentů: [správce / zpracovatel jednající s povolením svého správce; připojit oprávnění]. Kontakty pro incidenty: [obě strany].

Předmět, termín, cena včetně daňového režimu, akceptace, opravy a případné vrácení platby jsou v odsouhlasené nabídce podle `sales.md`. Pilot trvá 14 dní, maximálně 50 samostatných českých nájemních smluv, jedna odsouhlasená tabulka. Bez automatického prodloužení. Změny rozsahu vyžadují písemnou dohodu.

## Pokyny a účel zpracování

Výhradní účel: na doložený pokyn odběratele převést smlouvy do tabulkové evidence, ověřit hodnoty člověkem, předat výstup a změřit přínos. Operace: příjem PDF, uložení, OCR, extrakce pomocí modelu, kontrola, oprava, export, záloha a výmaz. Dokumenty se nepoužijí pro trénink, veřejné demo, další klienty ani evaluace bez nového samostatného oprávnění.

Subjekty údajů: smluvní strany, jejich zástupci a případní další lidé uvedení ve smlouvách. Údaje: jména, adresy, kontakty a další obsah dodaného PDF včetně identifikátorů, účtů nebo podpisů, pokud je obsahuje; extrakce je omezena na dohodnuté sloupce. Dodavatel vyžaduje minimalizované dokumenty; nepotřebné přílohy se nepředávají. Zvláštní kategorie údajů a údaje o trestních věcech nejsou zamýšleným vstupem; při jejich zjištění zpracování pozastavit a vyžádat pokyn.

Odběratel potvrzuje oprávnění dokumenty předat a zajišťuje své povinnosti vůči dotčeným osobám. Zpracovatel jedná jen na doložené pokyny; pokud má za to, že pokyn odporuje předpisům, upozorní odběratele a dotčené zpracování pozastaví. Případné zákonem vyžadované předání oznámí předem, dovoluje-li to právo.

## Přístup a ochrana

Přístup mají pouze jmenované pověřené osoby: [seznam/role], vázané mlčenlivostí. Pilot má vlastní backend a databázi, HTTPS, vlastní přístupový kód, omezený přístup k dashboardům a chráněné zálohy. Kód sdílí jeden klientský tým; nejde o individuální uživatelské účty. Přesný rozsah ochrany, regiony a obnova jsou v přiloženém provozním zápisu. Odběratel odpovídá za ochranu svého zařízení, přístupového kódu a stažených výstupů.

## Další zpracovatelé — doplnit před zahájením

| Služba | Úloha a předávaná data | Údaje k ověření a schválení |
|---|---|---|
| Vercel | Frontend a technické požadavky prohlížeče | Smluvní entita, logování, regiony, podmínky a případné přenosy |
| Render | Backend, zpracování PDF, OCR, přístup k obsahu | Smluvní entita, zvolený region a nastavení logů |
| Supabase | Databáze s PDF, textem a extrakcemi | Smluvní entita, projekt/region, zálohy a jejich doba uchování |
| OpenRouter | Směrování požadavku s textem a šablonou | Smluvní entita, podmínky a přenosy |
| [konkrétní endpoint modelu] | Extrakce z textu | Název společnosti, země, model, uchování, zákaz tréninku a právní podklad přenosu |
| [úložiště záloh] | Zašifrovaná kopie databáze | Poskytovatel, region, oprávnění a retenční lhůta |

Odběratel tyto konkrétně vyplněné další zpracovatele písemně schválí. Změna vyžaduje předchozí oznámení a možnost vznést námitku před předáním dat novému poskytovateli. Dodavatel uloží dalším zpracovatelům odpovídající povinnosti a odpovídá za jejich plnění podle použitelného práva. Žádná neověřená garance, že data zůstávají v EU; případné přenosy a jejich právní mechanismus doložit před zahájením.

## Součinnost, incidenty a uchování

Dodavatel bez zbytečného odkladu oznámí zjištěné porušení zabezpečení, předá dostupné informace o rozsahu, dopadech a opatřeních a průběžně je doplní. Poskytne přiměřenou součinnost pro práva dotčených osob, zabezpečení, oznámení incidentů a posouzení vlivu. Poskytne informace potřebné k doložení plnění dohody a umožní přiměřeně dohodnuté audity či inspekce oprávněné strany.

Aktivní pracovní data smazat nebo vrátit dle písemné volby odběratele nejpozději do 30 dnů od konce pilotu, pokud není dohodnuta navazující služba. Zálohy rotovat nejvýše sedm dní; poslední zálohu s klientským obsahem odstranit rovněž nejpozději do stejného třicetidenního termínu. Podmínky uchování u všech poskytovatelů musí tento slib umožňovat; jinak smluvní termín upravit před přijetím dokumentů. Výjimky vyžadované právem konkrétně zdokumentovat.

Po obnově zálohy znovu uplatnit požadavky na výmaz, než bude provoz zpřístupněn. Potvrdit dokončení smazání včetně testovacích kopií a záloh. Odběratel uchovává vlastní originály a převzatý Excel. Provozní záznam o provedení služby a fakturační údaje mají odděleně určený účel a dobu uchování [doplnit].

Datum a přijetí nabídky i dohody oprávněnými osobami: [doplnit].
