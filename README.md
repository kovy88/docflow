# Docflow

Vytěžování dat z firemních dokumentů lokálním LLM, s ověřením každé hodnoty proti PDF
a deterministickou kontrolou pravidel. Faktury, smlouvy, pojistky nebo jakýkoli jiný typ
dokumentu: obor určuje jen **šablona**, kterou lze vytvořit v UI nebo nechat navrhnout
modelem.

![Detail dokumentu: PDF se zvýrazněnou citací vlevo, vytěžená pole a nálezy vpravo](docs/workspace.png)

## Proč to není „chat s PDF“

| Problém LLM extrakce | Co s tím dělá Docflow |
|---|---|
| Model si hodnotu vymyslí | U každého pole musí vrátit stranu a doslovnou citaci; kód ověří, že citace v PDF opravdu je a hodnotu obsahuje. Neověřená pole UI označí. |
| Model nepočítá spolehlivě | Model nic nepočítá. Součty, kontrolní součet IČO a pořadí dat ověřuje deterministický kód podle pravidel šablony. |
| Čísla a data v různých formátech | Normalizace v kódu: `12 100,00 Kč`, `1.234,56`, `185 000,- Kč`, `3. 9. 2026`, `1. října 2026`, ISO. |
| Lokální model nepodporuje strukturovaný výstup | Vynucené volání nástroje, při selhání parseru Ollamy přechod na JSON v textu, validace Pydanticem a opakování pokusu s chybou jako zpětnou vazbou. |
| Nevíme, jak dobře to funguje | Evaluační sada 26 fiktivních dokumentů se známými hodnotami a vloženými chybami, měření přesnosti po polích, záchytu chyb i užitečnosti signálů v UI. |
| Člověk musí mít poslední slovo | Ruční kontrola v UI: opravy se uloží, pravidla se přepočítají; schválené extrakce lze použít jako další trénovací/evaluační data. |

## Výsledky evaluace

RESULTS_PLACEHOLDER

Celý report včetně chybně vytěžených polí je v [backend/evals/](backend/evals/) a na
stránce **Evaluace** v aplikaci.

## Architektura

```
PDF ─► text po stranách (pypdf)
    ─► LLM: pro každé pole šablony {hodnota, strana, citace, jistota}
         Ollama (qwen3.8:27b, lokálně) nebo OpenAI (strukturovaný výstup)
    ─► ověření citací v PDF ─► normalizace CZ čísel a dat ─► pravidla šablony
    ─► výsledek ke kontrole (needs_review / ok) ─► ruční oprava a schválení
```

- **backend/**: FastAPI, SQLModel (SQLite lokálně, Postgres/Supabase přes `DATABASE_URL`),
  extrakce běží na pozadí. [Podrobnosti](backend/README.md)
- **frontend/**: Next.js 16, react-pdf; PDF a pole vedle sebe, editor šablon, stránka
  evaluací. [Podrobnosti](frontend/README.md)
- **backend/dataset/**: generátor realistických fiktivních dokumentů s ground truth.

## Spuštění

```bash
ollama pull qwen3.8:27b
cd backend && uv sync && cp .env.example .env && uv run uvicorn app.main:app --reload
cd frontend && npm install && npm run dev      # http://localhost:3000
```

Testy: `cd backend && uv run pytest` (bez modelu, s falešným extraktorem),
`cd frontend && npm run lint && npx tsc --noEmit`.
Evaluace: `cd backend && uv run python scripts/evaluate.py [--model …] [--limit N]`.

Všechny dokumenty v repozitáři jsou fiktivní. Starší podklady k validaci trhu jsou ve
[validation/](validation/README.md).
