# Měření přínosu bez odhadovaných výsledků

Před laděním šablony vybrat s klientem deset reprezentativních smluv. Pět použít při nastavení a pět ponechat stranou až do závěrečného měření; zahrnout různé pořadí stran, dobu neurčitou, chybějící údaje, rozdílné zálohy a sken. Ground truth odsouhlasí klient. Fiktivní sada se vykazuje odděleně.

Každou smlouvu zpracovat ručně i s Docflow, ideálně v prohozeném pořadí po polovinách nebo různými pracovníky, aby výsledek nebyl jen důsledkem zapamatování. Měřit stejný rozsah od otevření PDF do uložení správného řádku evidence. Zaznamenat i čekání, během kterého pracovník nemůže dělat jinou práci. Dávkový export měřit jednou za dávku, ne násobit celkový čas každým dokumentem.

CSV evidence, jeden řádek na smlouvu:

```csv
case_id,split,manual_seconds,upload_seconds,blocked_wait_seconds,review_and_fix_seconds,export_share_seconds,fields_total,fields_wrong_before_review,key_errors_after_review,excluded_reason
```

Neznámé hodnoty zůstávají prázdné; nula znamená skutečně změřenou nulu. Celkový čas Docflow = upload + blokující čekání + kontrola/opravy + podíl exportu. Úspora = `1 - součet Docflow času / součet ručního času`, pouze pro stejné dokončené případy. Nulový či chybějící ruční základ znamená „nelze vyhodnotit“. Nezařazené smlouvy a čas jejich ručního dořešení vykázat zvlášť a zahrnout do celkového provozního přínosu.

Samostatná evidence provozovatele:

```csv
date,activity,minutes,model_cost_czk,hosting_share_czk,note
```

Oddělit jednorázové nastavení, podporu, lidskou kontrolu dodávanou jako služba a provozní náklady. Přínos pro zákazníka i udržitelnost služby musí být viditelné; přesunutí ruční práce na dodavatele není automatizace.

Před každým předáním zkontrolovat všechny exportované klíčové hodnoty, uložit seznam ID extrakcí a datum kontroly. Nevyhodnocovat správnost pole jen podle `grounded=true`. Uvést chybovost před lidskou kontrolou a známé chyby po ní zvlášť.

Závěrečný zápis: zaplaceno ano/ne a skutečná částka, dodané/zpracované/předané smlouvy, počet vyřazených, součet obou časů, úspora, chyby, náklady služby, rozhodnutí klienta a další datum. Cíle: zaplacený pilot, ≥30 smluv při dodání klientem, ≥50% úspora, nula známých neopravených klíčových chyb a výslovné rozhodnutí o pokračování.
