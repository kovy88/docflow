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
| Lokální model nepodporuje strukturovaný výstup | Vynucené volání nástroje; když parser Ollamy selže, záložní raw dokončení mimo parser; validace Pydanticem a opakování s chybou jako zpětnou vazbou. |
| Nevíme, jak dobře to funguje | Evaluační sada 37 fiktivních dokumentů včetně těžkých případů, skenů a vložených chyb; přesnost po polích, záchyt chyb, užitečnost signálů v UI, verze promptu. |
| Člověk musí mít poslední slovo | Ruční kontrola v UI: opravy se uloží, pravidla se přepočítají; schválené extrakce lze použít jako další trénovací/evaluační data. |

## Výsledky evaluace

33 fiktivních dokumentů (12 + 4 faktury, 10 smluv, 7 pojistek; 7 z nich jsou „těžké
případy“), 339 polí, 11 vložených chyb. Mac s 32 GB RAM, Ollama.

| Model | Prompt | Přesnost polí | Standardní / těžké | Dokumenty bez chyby | Vložené chyby zachyceny | Medián času |
|---|---|---|---|---|---|---|
| qwen3.8:27b | v1 | 99,1 % | 100 % / 95,9 % | 31 / 33 | 11 / 11 | 102 s |
| qwen3.6:35b-a3b (MoE) | v1 | 99,1 % | 99,6 % / 97,3 % | 30 / 33 | 11 / 11 | 24 s |
| qwen3.8:27b | **v2** | 99,7 % | 100 % / 98,6 % | 32 / 33 | 11 / 11 | 69 s |
| qwen3.6:35b-a3b (MoE) | **v2** | **99,7 %** | 99,6 % / **100 %** | 32 / 33 | 11 / 11 | **23 s** |

Co z evaluace vzešlo:

- **Parser Ollamy selhal u 2 z 26 dokumentů** (model vygeneroval rozbité XML volání
  nástroje, HTTP 500). Oprava: záložní raw dokončení přes `/api/generate`, které parser
  obchází. Ověřeno vynuceným selháním.
- **Těžké případy odhalily dva typy chyb:** model doplnil datum splatnosti u „splatné
  ihned“ (vzal datum vystavení) a dopočítal konec smlouvy z „12 měsíců“. Prompt v2
  zakazuje přebírat hodnotu jiného pole i odvozovat chybějící hodnoty. U qwen3.6 to chyby
  odstranilo, qwen3.8 dál doplňuje splatnost.
- **Chyba normalizace, ne modelu:** u pojistky model správně opsal „1. 10. 2026, 0:00 hod.“,
  ale parser data čas nezvládl. Opraveno v kódu.
- **Pravidla hlídají i model:** jediná zbývající chyba qwen3.6 (základ daně zaměněný za
  celkovou částku) má platnou citaci, takže ji ověření citací nezachytí. Zachytí ji ale
  pravidlo součtu a dokument jde ke kontrole.
- **Jistota modelu sama nestačí:** ze tří chyb qwen3.6 (v1) měla nízkou jistotu jen jedna
  (dopočítaný konec smlouvy, který zároveň neprošel ověřením citace); další dvě měly jistotu
  0,9 a 1,0. Proto Docflow kombinuje tři nezávislé kontroly: citaci, jistotu a pravidla.
- **Menší MoE model je pro tuto úlohu lepší volba:** stejná přesnost, 3× rychlejší.

**Skeny (OCR).** Strany bez textové vrstvy čte lokálně Tesseract (`ces`). Sada má navíc
4 „skenované“ dokumenty (obrazové PDF, mírné natočení, šum; 41 polí, 1 vložená chyba).
qwen3.6:35b-a3b na nich má **95,1 %** přesnost polí (39 / 41), 3 / 4 dokumenty bez chyby
a vloženou chybu zachytil. Obě chybná pole (prohozený základ a celková částka u faktury,
kde OCR oddělí popisky od hodnot) zůstala označená jako neověřená, ověřená pole byla
správně všechna. Co k tomu bylo potřeba:

- **Mediánový filtr před OCR:** tečky šumu Tesseract čte jako interpunkci v číslech;
  filtr 3×3 zvedl doslovně přečtené hodnoty z 19 na 36 ze 39.
- **Volnější ověření citací jen na OCR stranách:** nejlepší okno textu s podobností
  ≥ 0,85 místo nejdelšího společného úseku ≥ 0,9 a citace s „…“ po částech. Práh je
  změřený: správné hodnoty mají podobnost ≥ 0,88, hodnoty z jiných dokumentů projdou
  v 3,7 % případů (při 0,8 v 10,6 %).
- **Normalizace:** OCR přečte „3 000 000 Kč“ jako „3.000 000 Kč“; parser to teď bere jako
  oddělovače tisíců.

Omezení: výsledky na skenech stojí na 4 generovaných dokumentech; skutečné skeny (horší
kvalita, razítka, rukopis) budou horší. Sada také nemá skutečně neuspořádaná PDF. Další
krok je sada reálných anonymizovaných dokumentů.

Celý report včetně chybně vytěžených polí je v [backend/evals/](backend/evals/) a na
stránce **Evaluace** v aplikaci.

## Architektura

```
PDF ─► text po stranách (pypdf; skeny lokální OCR Tesseract)
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
