"""Generate a clearly fictional lease for the pilot walkthrough (not model evaluation)."""

from make_samples import OUT, write_pdf


def main():
    OUT.mkdir(exist_ok=True)
    write_pdf(
        "najem_pilot.pdf",
        "FIKTIVNÍ UKÁZKA – nájemní smlouva",
        [
            "Tento dokument je syntetický testovací podklad, nikoli vzor pro právní použití.",
            "Nájemce: Ukázkový Nájemce s.r.o., sídlo Fiktivní 99, Brno.",
            "Pronajímatel: Ukázkový Pronajímatel s.r.o., sídlo Vzorová 10, Praha.",
            "Předmět nájmu: byt č. 5, Ukázková 123, 602 00 Brno.",
            "Začátek nájmu: 1. 11. 2026. Nájem se sjednává na dobu neurčitou.",
            "Datum podpisu smlouvy: 19. 10. 2026.",
            "Nájemné: 15 000 Kč měsíčně. Měna: CZK.",
            "Zálohy na služby: 3 000 Kč měsíčně. Celková měsíční platba: 18 000 Kč.",
            "Kauce: 30 000 Kč. Výpovědní lhůta: 3 měsíce.",
            "Údaje o společnostech i adrese jsou smyšlené. Dokument neobsahuje podpisy.",
        ],
    )


if __name__ == "__main__":
    main()
