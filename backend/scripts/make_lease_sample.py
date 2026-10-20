"""Generate the fictional residential lease for the pilot walkthrough (not model evaluation).

    uv run python scripts/make_lease_sample.py

Reads like a real Czech lease: parties are people, the landlord lives elsewhere than the flat,
the signing date precedes the start, and the total monthly payment is printed next to rent and
advances, so the template's "do not compute / do not confuse" rules are actually exercised."""

from make_dataset import Doc
from make_samples import OUT

TITLE = "SMLOUVA O NÁJMU BYTU"
LAW = "uzavřená podle § 2235 a násl. zákona č. 89/2012 Sb., občanský zákoník, ve znění pozdějších předpisů"

PARTIES = [
    (
        "Ing. Pavel Hrubý",
        "nar. 14. 3. 1971\nbydliště: Kotlářská 418/27, 602 00 Brno\n"
        "bankovní spojení: 2384710593/0800",
        "pronajímatel",
    ),
    (
        "Mgr. Kristýna Málková",
        "nar. 2. 9. 1994\ndosavadní bydliště: Husova 1120, 586 01 Jihlava\n"
        "tel.: 734 218 906, e-mail: k.malkova@email.cz",
        "nájemce",
    ),
]

ADVANCES = [
    ("Teplo a ohřev teplé vody", "1 650 Kč"),
    ("Studená voda a stočné", "540 Kč"),
    ("Osvětlení a úklid společných prostor", "210 Kč"),
    ("Provoz výtahu", "150 Kč"),
    ("Odvoz odpadu", "100 Kč"),
]

ARTICLES = [
    (
        "Předmět nájmu",
        [
            "Pronajímatel prohlašuje, že je výlučným vlastníkem bytové jednotky č. 1426/12 "
            "v budově č. p. 1426 v části obce Královo Pole, která je součástí pozemku parc. "
            "č. 2187/4, vše zapsané na LV č. 6253 pro katastrální území Královo Pole, obec Brno, "
            "u Katastrálního úřadu pro Jihomoravský kraj.",
            "Pronajímatel přenechává nájemci do užívání byt č. 12 o velikosti 2+kk a celkové "
            "podlahové ploše 54,6 m², nacházející se ve 4. nadzemním podlaží domu na adrese "
            "Štefánikova 1426/38, 612 00 Brno (dále jen „byt“). Součástí nájmu je sklepní kóje "
            "č. 12 v 1. podzemním podlaží.",
            "Byt je vybaven kuchyňskou linkou s elektrickou varnou deskou, troubou, lednicí "
            "s mrazákem a myčkou nádobí, vestavěnou skříní v předsíni a sprchovým koutem. "
            "Podrobný soupis vybavení a stav měřidel uvádí předávací protokol, který je přílohou "
            "č. 1 této smlouvy.",
            "Nájemce bude byt užívat k bydlení pro sebe; jiné osoby smí v bytě trvale bydlet "
            "jen s předchozím písemným souhlasem pronajímatele.",
        ],
    ),
    (
        "Doba nájmu",
        [
            "Nájem se sjednává na dobu určitou od 1. listopadu 2026 do 31. října 2027.",
            "Nezvolí-li kterákoli ze stran jinak, má se za to, že nájem byl obnoven za stejných "
            "podmínek na další rok, v souladu s § 2285 občanského zákoníku.",
        ],
    ),
    (
        "Nájemné a úhrada za služby",
        [
            "Nájemné za užívání bytu včetně vybavení a sklepní kóje činí 16 500 Kč měsíčně "
            "(slovy: šestnáct tisíc pět set korun českých).",
            "Vedle nájemného hradí nájemce měsíční zálohy na služby spojené s užíváním bytu "
            "v tomto rozpisu:",
        ],
    ),
    (
        "Jistota",
        [
            "Nájemce složí pronajímateli nejpozději v den podpisu této smlouvy peněžitou jistotu "
            "ve výši 33 000 Kč (§ 2254 občanského zákoníku).",
            "Pronajímatel je oprávněn jistotu použít na úhradu dlužného nájemného, záloh "
            "a případných škod na bytě. Po skončení nájmu vrátí pronajímatel jistotu, sníženou "
            "o tyto pohledávky, nejpozději do 30 dnů od předání bytu.",
        ],
    ),
    (
        "Práva a povinnosti smluvních stran",
        [
            "Pronajímatel předá nájemci byt ve stavu způsobilém k řádnému užívání dne "
            "1. listopadu 2026 spolu se dvěma sadami klíčů od bytu, domu a schránky.",
            "Nájemce provádí a hradí běžnou údržbu a drobné opravy bytu v rozsahu nařízení vlády "
            "č. 308/2015 Sb. Ostatní opravy zajišťuje pronajímatel; nájemce mu potřebu takové "
            "opravy oznámí bez zbytečného odkladu.",
            "Nájemce nesmí v bytě provádět stavební úpravy ani jej dát do podnájmu bez "
            "předchozího písemného souhlasu pronajímatele. Chov drobného domácího zvířete "
            "pronajímatel povoluje.",
            "Pronajímatel je oprávněn po předchozím ohlášení, nejméně 3 dny předem, vstoupit "
            "do bytu za účelem kontroly jeho stavu nebo provedení nutné opravy.",
        ],
    ),
    (
        "Skončení nájmu",
        [
            "Nájemce může nájem vypovědět z důvodů uvedených v § 2287 občanského zákoníku, "
            "pronajímatel z důvodů uvedených v § 2288 občanského zákoníku.",
            "Výpovědní doba činí tři měsíce a počíná běžet prvním dnem kalendářního měsíce "
            "následujícího po doručení výpovědi druhé straně. Výpověď musí mít písemnou formu.",
            "Při skončení nájmu nájemce byt vyklidí a předá pronajímateli ve stavu, v jakém jej "
            "převzal, s přihlédnutím k obvyklému opotřebení.",
        ],
    ),
    (
        "Závěrečná ustanovení",
        [
            "Tuto smlouvu lze měnit pouze písemnými dodatky podepsanými oběma stranami.",
            "Smlouva je vyhotovena ve dvou stejnopisech, z nichž každá strana obdrží jeden.",
            "Smluvní strany prohlašují, že si smlouvu přečetly, s jejím obsahem souhlasí a na "
            "důkaz toho připojují své podpisy.",
            "Přílohy: č. 1 – Předávací protokol, č. 2 – Kopie průkazu energetické náročnosti "
            "budovy.",
        ],
    ),
]

ROMAN = ["I", "II", "III", "IV", "V", "VI", "VII"]


def main() -> None:
    OUT.mkdir(exist_ok=True)
    pdf = Doc()
    pdf.set_font("A", "B", 15)
    pdf.cell(0, 8, TITLE, align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("A", "I", 8.5)
    pdf.multi_cell(0, 4.5, LAW, align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.gap(6)
    pdf.para("Smluvní strany", style="B", size=10)
    pdf.gap(2)
    for i, (name, details, role) in enumerate(PARTIES):
        pdf.para(name, style="B", size=10)
        pdf.para(details, size=9.5)
        pdf.para(f"(dále jen „{role}“)", size=9.5, style="I")
        if i == 0:
            pdf.gap(2)
            pdf.para("a", size=9.5)
            pdf.gap(2)
    pdf.gap(3)
    pdf.para("uzavírají níže uvedeného dne, měsíce a roku tuto smlouvu o nájmu bytu:", size=9.5)

    for i, (title, paras) in enumerate(ARTICLES, 1):
        pdf.gap(3)
        pdf.para(f"{ROMAN[i - 1]}.\n{title}", style="B", size=10, align="C")
        pdf.gap(1)
        for j, text in enumerate(paras, 1):
            pdf.para(f"{i}.{j}  {text}", size=9.5, align="J")
            pdf.gap(1)
        if title == "Nájemné a úhrada za služby":
            x, w = pdf.l_margin + 8, 110
            for k, (service, amount) in enumerate(
                [*ADVANCES, ("Zálohy na služby celkem", "2 650 Kč")]
            ):
                last = k == len(ADVANCES)
                if last:
                    pdf.set_draw_color(160, 165, 172)
                    pdf.line(x, pdf.get_y() + 0.5, x + w, pdf.get_y() + 0.5)
                    pdf.ln(1)
                pdf.set_x(x)
                pdf.set_font("A", "B" if last else "", 9.5)
                pdf.cell(w - 30, 5, service)
                pdf.cell(30, 5, amount, align="R", new_x="LMARGIN", new_y="NEXT")
            pdf.gap(2)
            for j, text in enumerate(
                [
                    "Celková měsíční platba (nájemné a zálohy na služby) tedy činí 19 150 Kč.",
                    "Nájemné i zálohy jsou splatné měsíčně předem, vždy nejpozději do 5. dne "
                    "kalendářního měsíce, na účet pronajímatele č. 2384710593/0800 pod variabilním "
                    "symbolem 142612.",
                    "Zálohy vyúčtuje pronajímatel jednou ročně, nejpozději do 30. dubna "
                    "následujícího roku; přeplatek nebo nedoplatek je splatný do 30 dnů od "
                    "doručení vyúčtování.",
                    "Pronajímatel může nájemné zvyšovat nejvýše jednou ročně podle § 2249 "
                    "občanského zákoníku.",
                ],
                len(paras) + 1,
            ):
                pdf.para(f"{i}.{j}  {text}", size=9.5, align="J")
                pdf.gap(1)

    if pdf.get_y() > pdf.h - 70:  # keep place, date and both signatures on one page
        pdf.add_page()
    pdf.gap(8)
    pdf.para("V Brně dne 24. října 2026", size=9.5)
    pdf.gap(16)
    y, half = pdf.get_y(), pdf.content_w / 2
    for col, (name, _, role) in enumerate(PARTIES):
        pdf.set_xy(pdf.l_margin + col * half, y)
        pdf.set_font("A", "", 9)
        pdf.cell(half - 10, 5, "……………………………………", new_x="LEFT", new_y="NEXT")
        pdf.cell(half - 10, 5, f"{name}, {role}", new_x="LEFT", new_y="NEXT")
    pdf.output(OUT / "najem_pilot.pdf")


if __name__ == "__main__":
    main()
