# Oddělený pilot a obnova

## Klient ve sdíleném nasazení

Výchozí cesta pro pilot: `scripts/workspaces.py create "Firma s.r.o." --quota 50` na produkčním backendu. Klient dostane vlastní kód a vidí jen svoje dokumenty, šablony a měření. Kód předat samostatným kanálem; vypsán je jen jednou. Ukončení: `revoke <id>` (data zůstávají, smazat je lze v aplikaci před ukončením). Před předáním ověřit, že kód jiného prostoru dokument nevidí (404).

## Nasazení jednoho klienta

Pro klienta, který požaduje oddělenou databázi nebo vlastní region. Připravený `render.pilot.yaml` vytváří nový backend, nikoli změnu veřejného dema. Zatím nebyl aplikován. Pro prvního klienta založit samostatnou databázi/projekt s novými přihlašovacími údaji a samostatný frontend projekt z `frontend/`. Nepoužívat pouze jiné schéma ve stávající sdílené databázi. Umístění, skutečné tarify a poskytovatele potvrdit podle dohody s klientem; bezplatný backend může mít studený start.

Backend: `PILOT_MODE=true`, nový náhodný `ACCESS_CODE`, klientské `DATABASE_URL`, `DB_SCHEMA=docflow`, `CORS_ORIGINS` pouze s adresou nového frontendu. Poskytovatele nastavit přes `LLM_PROVIDER`; pro OpenRouter vyplnit klíč a před předáním dat ověřit konkrétní dostupný endpoint, podmínky a region. Kód už požaduje `data_collection: deny`, ale to samo nedokládá region ani smluvní záruky. `PILOT_MODE` bez neprázdného kódu odmítne start.

Frontend: `NEXT_PUBLIC_API_URL` na nový backend, `NEXT_PUBLIC_DEMO=1`, `NEXT_PUBLIC_PILOT_EMAIL` na skutečný obchodní kontakt. Přístupový kód předat samostatným dohodnutým kanálem, nikdy jako `NEXT_PUBLIC_` proměnnou. Jeden kód odpovídá jednomu klientskému týmu; neumožňuje individuální audit jednotlivých uživatelů. Veřejný náhled čerpá pouze fiktivní statická data.

Databázové schéma aplikace nepřidávat mezi schémata veřejného Data API. Připojení backendu je serverové. Zásady pro schémata a dostupné zálohy ověřit podle aktuálního nastavení konkrétního projektu; viz [Supabase backup/restore](https://supabase.com/docs/guides/platform/migrating-within-supabase/backup-restore).

Před otevřením klientovi: bez kódu očekávat 401 pro dokumenty, export a klientské šablony; s kódem projít nahrání → extrakce → oprava → schválení → Excel → smazání. Ověřit, že druhá instance dokument nevidí. Uložit adresy a ID zdrojů bez hesel, verzi commitu a výsledek smoke testu do soukromého provozního zápisu. Samostatně vyplnit a podepsat podmínky.

## PostgreSQL: denní záloha a obnova do nového cíle

Použít `pg_dump` odpovídající verzi serveru nebo novější kompatibilní verzi. Přihlašovací údaje předat zabezpečenými proměnnými prostředí `PGHOST`, `PGPORT`, `PGUSER`, `PGPASSWORD`, `PGDATABASE` a `PGSSLMODE`; nevkládat je do dokumentace, historie příkazů ani logů. `PGDATABASE` zde obsahuje jen název databáze, ne URL. Pro zálohování pozastavit zápisy na dobu zaznamenání kontrolních počtů.

```sh
umask 077
pg_dump --format=custom --schema=docflow --no-owner --no-acl --file=pilot.dump
pg_restore --list pilot.dump > pilot-contents.txt
```

Zálohu uložit mimo aplikační disk do šifrovaného klientského úložiště s omezeným přístupem. Zahrnuje PDF, extrakce i šablony. Uchovávat posledních sedm denních záloh a před každou změnou nasazení jednu čerstvou, v mezích klientské retenční dohody. Automatické plánování v konkrétním účtu ještě není nastavené; během řízeného pilotu provádí a zapisuje zálohu provozovatel denně před zpracováním.

Pro zkoušku vytvořit úplně nový prázdný databázový cíl s odlišnými přihlašovacími údaji. V novém shellu bezpečně nastavit všech šest proměnných `PG*` pro tento cíl; nejprve ověřit jeho identitu a prázdnost. Obnova nesmí mířit na produkci.

```sh
pg_restore --no-owner --no-acl --single-transaction --exit-on-error --dbname="$PGDATABASE" pilot.dump
```

`PGDATABASE` je jméno nové databáze (např. `postgres` u nového projektu); host, uživatel, heslo a SSL se načtou z ostatních proměnných. Nikdy nevypisovat jejich hodnoty. [Dokumentace pg_dump](https://www.postgresql.org/docs/current/app-pgdump.html), [pg_restore](https://www.postgresql.org/docs/current/app-pgrestore.html).

Nad obnoveným cílem spustit backend se stejným `DB_SCHEMA` a provést kontrolu počtů všech pěti tabulek, stažení vybraného PDF a shody jeho SHA-256, ověření hodnot a data kontroly vybrané extrakce, XLSX export a smazání testovacího dokumentu. Teprve poté zapsat datum, čas obnovy a výsledek. Testovací cíl následně odstranit podle retenční dohody.

Reprodukovatelná lokální zkouška bez připojení k existující databázi:

```sh
cd backend
uv run python scripts/verify_postgres_restore.py
```

Skript vytvoří vlastní dočasný PostgreSQL cluster dostupný jen přes lokální socket, fiktivní záznamy, dump a druhou databázi. Porovná obsah všech tabulek, ověří chráněný export i smazání a cluster odstraní. Vyžaduje PostgreSQL utility na PATH. Lokální zkouška prošla 19. 10. 2026; obnova konkrétního hostovaného pilotu zatím neproběhla.

## SQLite pro lokální ukázku

```sh
cd backend
uv run python scripts/backup_sqlite.py docflow.db /bezpecne-uloziste/pilot.db
uv run python scripts/backup_sqlite.py /bezpecne-uloziste/pilot.db /docasny-cil/restored.db
```

Skript používá konzistentní SQLite backup API, soubor vytváří s oprávněním 0600, kontroluje integritu a cizí klíče a nikdy nepřepíše existující cíl. Databáze obsahuje i PDF. Jednotková zkouška ověřuje zachování PDF a kontroly po obnově.

## Provoz a ukončení

Každý pracovní den zkontrolovat dostupnost backendu, neúspěšné extrakce, denní limit volání, náklady a stáří poslední zálohy. Přerušené extrakce aplikace po restartu označí `failed`; opakovat je ručně, žádná trvalá fronta zatím není. Nezapisovat texty smluv do vlastních provozních poznámek.

Při ukončení předat výstup, získat instrukci o smazání, odstranit aktivní dokumenty a klientské vlastní šablony, zálohy i testovací cíle podle dohodnutých termínů. Pokud se ruší celý pilot, odstranit také databázi, instance a přístupové údaje. Mazání v UI neodstraňuje starší zálohy. Při obnově předchozí zálohy před otevřením provozu znovu uplatnit všechny následné požadavky na smazání. Zaznamenat co, kdy a kdo odstranil; neobnovovat klientské dokumenty do veřejného dema.
