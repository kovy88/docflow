# docflow.logicflow.cz

Subdoména byla 19. 10. 2026 přidána k existujícímu projektu Vercel `docflow`
(`prj_KDdy0Il2BXPnxK6WdUZkv10LWUQI`). Vlastnictví je ověřeno. DNS zóna
`logicflow.cz` používá autoritativní nameservery FORPSI; změna DNS ve Vercelu by
veřejné směrování nezměnila.

V administraci FORPSI nastavit jediný záznam:

| Typ | Název v zóně logicflow.cz | Hodnota |
|---|---|---|
| CNAME | docflow | ec0899d6f768dc6e.vercel-dns-017.com. |

Cíl je konkrétní doporučení Vercelu pro tento projekt ze dne 19. 10. 2026.
Ponechat výchozí TTL. Neměnit nameservery, apex doménu, poštovní záznamy ani
ostatní subdomény. Při dokončení ověřit CNAME na autoritativním serveru,
konfiguraci domény ve Vercelu a vydání HTTPS certifikátu.

V repozitářovém `render.yaml` je nový origin přidaný do `CORS_ORIGINS`.
Pro živé API je nutné stejnou hodnotu skutečně aplikovat i v nastavení backendu;
samotná změna tohoto souboru nebo frontendové nasazení konfiguraci běžícího
backendu nemění. Veřejné statické demo backend nepoužívá.

Dokončeno 19. 10. 2026: uživatel přidal CNAME ve FORPSI. Správný záznam potvrzen
přímo na autoritativních DNS serverech FORPSI a přes veřejný resolver Cloudflare.
Vercel vrací `configured_correctly` a ověřené přiřazení k projektu `docflow`.
Produkční frontend byl úspěšně nasazen (`dpl_CwUf7bsYGY8c17cX6obg76hz2G4C`,
stav `READY`) s aliasem `docflow.logicflow.cz`.

HTTPS ověřeno TLS handshake s kontrolou důvěryhodnosti a názvu domény na obou
doporučených adresách Vercelu (216.198.79.1 a 64.29.17.1): TLS 1.3, certifikát pro
`docflow.logicflow.cz`, platný do 17. 1. 2027. Místní resolver/prohlížeč při ověřování
ještě držel předchozí negativní DNS odpověď; běžné rozšíření DNS cache může doběhnout
později než autoritativní DNS.
