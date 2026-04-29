"""Report generation for PIT tax forms."""

from __future__ import annotations

import csv
import io
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from .models import TAX_RATE, FifoLot, TaxEvent, YearReport


def _fmt(amount: Decimal, width: int = 12) -> str:
    """Format decimal as PLN amount with 2 decimal places."""
    return f"{amount:>{width},.2f}"


def _fmt_orig(amount: Decimal, currency: str, width: int = 12) -> str:
    """Format decimal in original currency."""
    return f"{amount:>{width},.2f} {currency}"


def _render_event_table(events: list[TaxEvent]) -> list[str]:
    """Render a chronological transaction table for a section."""
    lines: list[str] = []
    lines.append("─" * 100)
    lines.append(
        f"{'Data':<12}{'Typ':<18}{'Instrument':<16}"
        f"{'Przychód PLN':>14}{'Koszt PLN':>14}{'Zysk/Strata':>14}"
    )
    lines.append("─" * 100)
    for e in sorted(events, key=lambda x: x.date):
        profit = e.income_pln - e.cost_pln
        lines.append(
            f"{e.date.isoformat():<12}{e.event_type:<18}{e.symbol:<16}"
            f"{_fmt(e.income_pln, 14)}{_fmt(e.cost_pln, 14)}{_fmt(profit, 14)}"
        )
    lines.append("─" * 100)
    return lines


def _pit38_dividend_positions(year: int) -> tuple[int, int, int]:
    """PIT-38 sekcja G — numeracja pól (due, do_odliczenia, do_zapłaty).

    Numery pozycji zostały przesunięte o 2 w formularzu za 2025 r.
    """
    if year >= 2025:
        return 47, 48, 49
    return 45, 46, 47


def _pit38_total_to_pay_position(year: int) -> int:
    """PIT-38 'PODATEK DO ZAPŁATY' (suma sekcji G) — pozycja w formularzu.

    2020-2024 (wariant 17): poz. 49
    2025+ (wariant 18+): poz. 51 — przesunięcie +2 spójne z sekcją G dyw.
    Zweryfikowane na PIT-38(17) z 2024 i PIT-38(18) z 2025.
    """
    if year >= 2025:
        return 51
    return 49


def _pit38_section_c_positions(year: int, has_pit8c: bool = False) -> dict[str, int]:
    """PIT-38 sekcja C — numeracja pól wg wariantu i obecności PIT-8C.

    Returns dict z kluczami logicznymi → numery pozycji w formularzu.

    | Wariant | has_pit8c | wiersz_1 (PIT-8C) | wiersz_2 (Inne) | wiersz_3 (Zwolnione) | razem |
    |---------|-----------|-------------------|-----------------|----------------------|-------|
    | 17 (≤2024) | False | n/a              | 22, 23          | n/a                  | 24-27 |
    | 18 (≥2025) | False | n/a              | 22, 23          | 24, 25               | 26-29 |
    | 18 (≥2025) | True  | 20, 21           | 22, 23          | 24, 25               | 26-29 |

    Klucze 'razem_dochod', 'razem_strata' są w "zł, gr" (per formularz).

    Raises ValueError gdy `year < 2025 AND has_pit8c=True` (zabronione per
    plan §6.1: "wariant 17 nie obsługuje wiersza 1 dla większości userów").
    """
    if year >= 2025:
        positions = {
            "wiersz_2_inc": 22,
            "wiersz_2_cost": 23,
            "wiersz_3_inc": 24,
            "wiersz_3_cost": 25,
            "razem_inc": 26,
            "razem_cost": 27,
            "razem_dochod": 28,
            "razem_strata": 29,
        }
        if has_pit8c:
            positions["wiersz_1_inc"] = 20
            positions["wiersz_1_cost"] = 21
        return positions

    if has_pit8c:
        raise ValueError(
            f"has_pit8c=True nie jest obsługiwane dla wariantu 17 (rok {year}); "
            f"PIT-8C cz. D wymaga formularza ≥18 (rok ≥ 2025)"
        )
    return {
        "wiersz_2_inc": 22,
        "wiersz_2_cost": 23,
        "razem_inc": 24,
        "razem_cost": 25,
        "razem_dochod": 26,
        "razem_strata": 27,
    }


def _pit38_section_d_positions(year: int) -> dict[str, int]:
    """PIT-38 sekcja D (Obliczenie zobowiązania, art. 30b ust. 1) — numeracja.

    Wariant 17 (≤2024): poz. 28-33
    Wariant 18 (≥2025): poz. 30-35 (shift +2)

    Zaokrąglenia (te same w obu wariantach):
    - 'podstawa': pełne zł (po zaokrągleniu)
    - 'podatek_dochodu': zł, gr (= podstawa × stawka — bez zaokrąglenia)
    - 'podatek_za_granica': zł, gr
    - 'podatek_nalezny': pełne zł (po zaokrągleniu)
    - 'straty_lat': zł, gr
    """
    base = 30 if year >= 2025 else 28
    return {
        "straty_lat": base,
        "podstawa": base + 1,
        "stawka": base + 2,
        "podatek_dochodu": base + 3,
        "podatek_za_granica": base + 4,
        "podatek_nalezny": base + 5,
    }


def _pit38_pitzg_count_position(year: int) -> int:
    """PIT-38 sekcja L 'Informacje o załącznikach' — liczba PIT/ZG.

    2020-2024 (wariant 17): poz. 69
    2025+ (wariant 18+): poz. 72 (shift +3 — w18 dodał sekcje E i F + nadpłatę poz. 52)
    """
    if year >= 2025:
        return 72
    return 69


_COUNTRY_FULL_NAME = {
    "US": "STANY ZJEDNOCZONE AMERYKI",
    "CA": "KANADA",
    "SE": "SZWECJA",
}


def _country_full_name(code: str) -> str:
    """Pełna nazwa kraju do PIT/ZG poz. 6 (fallback to ISO code)."""
    return _COUNTRY_FULL_NAME.get(code, code)


def _papiery_country_breakdown(
    report: YearReport,
) -> dict[str, tuple[Decimal, Decimal]]:
    """Per-kraj agregacja przychód/koszty z papierów wartościowych.

    Tylko zdarzenia z income_pln > 0 (faktyczne sprzedaże). Opłaty (event_type=fee)
    nie mają country attribution w tym modelu — sumują się tylko w łącznych
    papiery_wart_cost. Stąd Σ per-kraj net != łączny net (różnica = opłaty).

    Country wyprowadzane z giełdy w symbolu (NGE.ARCA → US, LUN.TMX → CA).
    """
    from .country import derive_country

    breakdown: dict[str, list[Decimal]] = {}
    for e in report.papiery_wart_events:
        if e.income_pln <= 0:
            continue
        country = derive_country(e.symbol, e.currency)
        if country == "??":
            continue
        if country not in breakdown:
            breakdown[country] = [Decimal("0"), Decimal("0")]
        breakdown[country][0] += e.income_pln
        breakdown[country][1] += e.cost_pln
    return {k: (v[0], v[1]) for k, v in breakdown.items()}


def _render_pit38_filling_instructions(report: YearReport) -> list[str]:
    """Konkretne instrukcje wypełnienia PIT-38: pole-po-polu, z numeracją 2024.

    Numeracja sekcji G obsługuje shift +2 dla 2025+ (przez _pit38_dividend_positions).
    Sekcje C/D używają numeracji 2024 — dla 2025+ pojawia się jawne ostrzeżenie.
    """
    pos_due, pos_deduct, pos_to_pay = _pit38_dividend_positions(report.year)
    lines: list[str] = []

    lines.append("")
    lines.append("▌ SEKCJA A — Cel złożenia")
    lines.append("    poz. 6 (Cel):            1 = pierwsze złożenie  |  2 = korekta")
    lines.append("    poz. 7 (Rodzaj korekty): 1 = art. 81 OP (zwykła) — TYLKO jeśli korekta")
    lines.append("")

    if report.papiery_wart_events:
        papiery_pl = report.papiery_wart_income - report.papiery_wart_cost
        lines.append("▌ SEKCJA C — Dochody/straty z papierów wartościowych (art. 30b ust. 1)")
        lines.append("    Wiersz 2 'Inne przychody':")
        lines.append(f"      poz. 22 (Przychód):       {_fmt(report.papiery_wart_income)} PLN")
        lines.append(f"      poz. 23 (Koszty):         {_fmt(report.papiery_wart_cost)} PLN")
        lines.append("    Wiersz 3 'Razem':")
        lines.append(f"      poz. 24 (Suma przychód):  {_fmt(report.papiery_wart_income)} PLN")
        lines.append(f"      poz. 25 (Suma koszty):    {_fmt(report.papiery_wart_cost)} PLN")
        if papiery_pl > 0:
            lines.append(f"      poz. 26 (Dochód):         {_fmt(papiery_pl)} PLN")
            lines.append("      poz. 27 (Strata):         puste")
        elif papiery_pl < 0:
            lines.append("      poz. 26 (Dochód):         puste")
            lines.append(f"      poz. 27 (Strata):         {_fmt(-papiery_pl)} PLN")
        else:
            lines.append("      poz. 26-27: 0 (break-even)")
        lines.append("")

        lines.append("▌ SEKCJA D — Obliczenie zobowiązania (art. 30b ust. 1)")
        if papiery_pl > 0:
            podstawa = papiery_pl.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
            podatek_pre = papiery_pl * TAX_RATE
            podatek = podatek_pre.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
            lines.append(f"      poz. 29 (Podstawa, do pełnych zł):  {podstawa} PLN")
            lines.append("      poz. 30 (Stawka):                    19%")
            lines.append(f"      poz. 31 (Podatek 19%):              {_fmt(podatek_pre)} PLN")
            lines.append("      poz. 32 (Podatek za granicą art. 30b ust. 5a/5b): 0,00 PLN")
            lines.append(f"      poz. 33 (Podatek należny, do pełnych zł):        {podatek} PLN")
        else:
            lines.append("      poz. 29 (Podstawa):       0  (strata, brak podatku)")
            lines.append("      poz. 30-33:               0 / puste")
        lines.append("")

    if report.dividends_income_pln > 0:
        lines.append("▌ SEKCJA G — Zryczałtowany podatek od dywidend zagr. (art. 30a)")
        lines.append(
            f"      poz. {pos_due} (Zryczałt. podatek 19%):      {_fmt(report.dividends_tax_due_pln)} PLN"
        )
        lines.append(
            f"      poz. {pos_deduct} (Podatek za granicą do odl.): "
            f"{_fmt(report.dividends_tax_to_deduct_pln)} PLN"
        )
        lines.append("        UWAGA: kwota PO LIMICIE per-kraj UPO,")
        lines.append(f"               NIE faktyczny WHT pobrany ({_fmt(report.dividends_tax_paid_pln)} PLN)")
        lines.append(
            f"      poz. {pos_to_pay} (Różnica do zapłaty):         {_fmt(report.dividends_tax_to_pay_pln)} PLN"
        )
        lines.append("        Zaokrąglić DO PEŁNYCH GROSZY w górę")
        lines.append("        (wyjątek z art. 63 § 1a OP dla art. 30a — NIE do pełnych zł)")
        lines.append("")

    papiery_pl = report.papiery_wart_income - report.papiery_wart_cost
    _papiery_tax_pre = max(Decimal("0"), papiery_pl * TAX_RATE)
    podatek_papiery = _papiery_tax_pre.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    podatek_dyw = report.dividends_tax_to_pay_pln
    razem = podatek_papiery + podatek_dyw
    pos_total = _pit38_total_to_pay_position(report.year)
    lines.append(f"▌ PODATEK DO ZAPŁATY (poz. {pos_total} PIT-38)")
    lines.append(f"      = poz. 33 ({podatek_papiery} zł) + poz. {pos_to_pay} ({_fmt(podatek_dyw)} zł)")
    lines.append(f"      = {_fmt(razem)} PLN")
    lines.append("")

    pitzg_count = len(_papiery_country_breakdown(report))
    lines.append("▌ SEKCJA L — Załączniki")
    lines.append(f"      poz. 69 (Liczba załączników PIT/ZG): {pitzg_count}")
    lines.append("")

    if report.year >= 2025:
        lines.append("⚠ UWAGA 2025+: pozycje sekcji G przesunięte o +2 (45→47, 46→48, 47→49,")
        lines.append("  total 49→51). Sekcje C i D prawdopodobnie bez zmian. Zweryfikuj na")
        lines.append("  aktualnym formularzu PIT-38 (wariant ≥18).")
        lines.append("")

    return lines


def _render_pitzg_attachments(report: YearReport) -> list[str]:
    """Per-kraj rekomendacje załączników PIT/ZG z konkretnymi liczbami.

    Reguła prawna: PIT/ZG wymagany dla art. 27 ust. 8/9/9a, art. 30b ust. 5a/5b/5e/5f,
    art. 30c ust. 4/5, art. 30e ust. 8/9. NIE wymagany dla art. 30a (dywidendy).
    """
    lines: list[str] = []
    lines.append("")

    breakdown = _papiery_country_breakdown(report)
    dividend_countries = set(report.dividends_by_country.keys())

    if not breakdown and not dividend_countries:
        lines.append("  Brak dochodów zagranicznych — żaden PIT/ZG nie jest wymagany.")
        lines.append("")
        return lines

    for pitzg_idx, country in enumerate(sorted(breakdown), start=1):
        country_name = _country_full_name(country)
        income, cost = breakdown[country]
        net = income - cost
        dochod_pitzg = max(net, Decimal("0"))

        lines.append(f"▌ PIT/ZG #{pitzg_idx} — {country_name} ({country}) — WYMAGANY")
        lines.append("   Powód: papiery wartościowe sprzedane w tym kraju")
        lines.append("          → art. 30b ust. 5a/5b ustawy o PIT")
        lines.append("")
        lines.append("   Sekcja A — Dane identyfikacyjne podatnika:")
        lines.append("     Imię, nazwisko, data urodzenia, NIP/PESEL — Twoje dane")
        lines.append("")
        lines.append("   Sekcja B — Państwo:")
        lines.append(f"     poz. 6 (Państwo): {country_name}")
        lines.append(f"     poz. 7 (Kod):     {country}")
        lines.append("")
        lines.append("   Sekcja C.3 (DOCHODY ROZLICZANE W PIT-38):")
        lines.append(f"     poz. 29 (Dochód art. 30b ust. 5a/5b):  {_fmt(dochod_pitzg)} PLN")
        if net < 0:
            lines.append(f"        (kraj na minusie {_fmt(-net)} PLN; przychód był → wpisujemy 0,00)")
        elif net == 0:
            lines.append("        (break-even)")
        lines.append("     poz. 30 (Podatek za granicą):           0,00 PLN")
        lines.append("        (typowo brak WHT od kapitałowych dla nierezydentów PL)")
        lines.append("     poz. 31 (Dochód art. 30b ust. 5e/5f, waluty wirtualne): puste")
        lines.append("     poz. 32 (Podatek za granicą od poz. 31):                puste")
        lines.append("")
        lines.append("   Sekcje C.1, C.2, C.4 (PIT-36/L/39): puste")
        lines.append("")

    only_dyw = dividend_countries - set(breakdown)
    for country in sorted(only_dyw):
        country_name = _country_full_name(country)
        cd = report.dividends_by_country[country]
        c_to_pay = max(Decimal("0"), cd.tax_due_pln - cd.tax_to_deduct_pln)

        lines.append(f"▌ {country_name} ({country}) — PIT/ZG NIE WYMAGANY")
        lines.append(f"   Powód: tylko dywidendy z {country_name} (art. 30a ust. 1 pkt 1)")
        lines.append("          PIT/ZG dotyczy art. 27, 30b, 30c, 30e — NIE art. 30a")
        lines.append("   Dane już ujęte zbiorczo w PIT-38 sekcji G powyżej.")
        lines.append("")
        lines.append(
            f"   (Per-kraj: brutto {_fmt(cd.income_pln)} PLN | "
            f"WHT {_fmt(cd.tax_paid_pln)} PLN | "
            f"do odliczenia {_fmt(cd.tax_to_deduct_pln)} PLN | "
            f"do zapłaty {_fmt(c_to_pay)} PLN)"
        )
        lines.append("")

    fee_costs = sum(
        (e.cost_pln for e in report.papiery_wart_events if e.event_type == "fee"),
        Decimal("0"),
    )
    if fee_costs > 0:
        lines.append(f"ℹ Opłaty brokera ({_fmt(fee_costs)} PLN) wliczone w łączną poz. 23 PIT-38,")
        lines.append("  ale NIE atrybuowane do kraju w tym narzędziu (broker = Cypr formalnie).")
        lines.append("  Stąd Σ poz. 29 PIT/ZG < |poz. 27 PIT-38| o tę kwotę.")
        lines.append("")

    return lines


def generate_year_report(report: YearReport) -> str:
    """Generate text report for a single tax year — three PIT-38 sections."""
    lines: list[str] = []

    # ───────────────────────────────────────────────────────────────
    # SEKCJA 1: Papiery wartościowe → PIT-38 wiersz 1 (PIT-8C poz. 23-24)
    # ───────────────────────────────────────────────────────────────
    lines.append("═" * 70)
    lines.append(f" Papiery wartościowe — Rok {report.year}")
    lines.append(" → PIT-38 sekcja C wiersz 2 'Inne przychody' (poz. 22-23)")
    lines.append("═" * 70)
    lines.append("")
    lines.append(f"PRZYCHÓD:                                      {_fmt(report.papiery_wart_income)} PLN")
    lines.append(f"KOSZTY UZYSKANIA PRZYCHODU:                    {_fmt(report.papiery_wart_cost)} PLN")
    papiery_pl = report.papiery_wart_income - report.papiery_wart_cost
    lines.append(f"DOCHÓD / STRATA:                               {_fmt(papiery_pl)} PLN")
    fee_costs = sum(
        (e.cost_pln for e in report.papiery_wart_events if e.event_type == "fee"),
        Decimal("0"),
    )
    if fee_costs > 0:
        lines.append(f"  w tym opłaty brokera:                          {_fmt(fee_costs)} PLN")
    lines.append("")
    if report.papiery_wart_events:
        lines.extend(_render_event_table(report.papiery_wart_events))
        lines.append(
            f"{'RAZEM':<12}{'':<18}{'':<16}"
            f"{_fmt(report.papiery_wart_income, 14)}{_fmt(report.papiery_wart_cost, 14)}"
            f"{_fmt(papiery_pl, 14)}"
        )
    lines.append("")

    # ───────────────────────────────────────────────────────────────
    # SEKCJA 2: Instrumenty pochodne → PIT-38 wiersz 3 (PIT-8C poz. 27-28)
    # ───────────────────────────────────────────────────────────────
    lines.append("═" * 70)
    lines.append(f" Instrumenty pochodne — Rok {report.year}")
    lines.append(" → PIT-38 sekcja C wiersz 2 'Inne przychody' (poz. 22-23, sumują z papierami)")
    lines.append("═" * 70)
    lines.append("")
    if report.pochodne_events:
        lines.append(f"PRZYCHÓD:                                      {_fmt(report.pochodne_income)} PLN")
        lines.append(f"KOSZTY UZYSKANIA PRZYCHODU:                    {_fmt(report.pochodne_cost)} PLN")
        pochodne_pl = report.pochodne_income - report.pochodne_cost
        lines.append(f"DOCHÓD / STRATA:                               {_fmt(pochodne_pl)} PLN")
        rollover_costs = sum(
            (e.cost_pln for e in report.pochodne_events if e.event_type == "rollover_cost"),
            Decimal("0"),
        )
        rollover_income = sum(
            (e.income_pln for e in report.pochodne_events if e.event_type == "rollover_income"),
            Decimal("0"),
        )
        if rollover_costs > 0:
            lines.append(f"  w tym rollover (swap overnight):               {_fmt(rollover_costs)} PLN")
        if rollover_income > 0:
            lines.append(f"  przychód rollover:                             {_fmt(rollover_income)} PLN")
        lines.append("")
        lines.extend(_render_event_table(report.pochodne_events))
        lines.append(
            f"{'RAZEM':<12}{'':<18}{'':<16}"
            f"{_fmt(report.pochodne_income, 14)}{_fmt(report.pochodne_cost, 14)}"
            f"{_fmt(pochodne_pl, 14)}"
        )
    else:
        lines.append("(brak transakcji w tym roku)")
    lines.append("")

    # ───────────────────────────────────────────────────────────────
    # SUMA PIT-38 (kontrola)
    # ───────────────────────────────────────────────────────────────
    lines.append("═" * 70)
    lines.append(f" SUMA PIT-38 (kontrola) — Rok {report.year}")
    lines.append("═" * 70)
    lines.append(f"PRZYCHÓD ŁĄCZNIE (wiersz 1 + 3):               {_fmt(report.pit38_income)} PLN")
    lines.append(f"KOSZTY ŁĄCZNIE:                                {_fmt(report.pit38_cost)} PLN")
    lines.append(f"DOCHÓD / STRATA:                               {_fmt(report.pit38_profit_loss)} PLN")
    lines.append(f"PODATEK (19% od dochodu):                      {_fmt(report.pit38_tax)} PLN")
    lines.append("")

    # ───────────────────────────────────────────────────────────────
    # SEKCJA 3: Dywidendy zagraniczne → PIT-38 sekcja G + PIT/ZG
    # ───────────────────────────────────────────────────────────────
    # Numeracja pól w sekcji G zmieniła się od formularza za 2025 r. — przesunięcie o 2.
    # 2020-2024: poz. 45 (due) / 46 (do odliczenia) / 47 (do zapłaty)
    # 2025+:     poz. 47 / 48 / 49
    pos_due, pos_deduct, pos_to_pay = _pit38_dividend_positions(report.year)

    lines.append("═" * 70)
    lines.append(f" Dywidendy zagraniczne — Rok {report.year}")
    lines.append(" → PIT-38 sekcja G + załącznik PIT/ZG")
    lines.append("═" * 70)
    lines.append("")

    lines.append(
        f"DYWIDENDY BRUTTO (do wyliczenia poz. {pos_due}):           {_fmt(report.dividends_income_pln)} PLN"
    )
    lines.append(f"PODATEK POBRANY U ŹRÓDŁA (informacyjnie):       {_fmt(report.dividends_tax_paid_pln)} PLN")
    lines.append(
        f"PODATEK POLSKI 19%:                             {_fmt(report.dividends_tax_due_pln)} PLN  → poz. {pos_due}"
    )
    lines.append(
        f"PODATEK DO ODLICZENIA (per-UPO cap):            {_fmt(report.dividends_tax_to_deduct_pln)} PLN  → poz. {pos_deduct}"
    )
    lines.append(
        f"DO ZAPŁATY W POLSCE:                            {_fmt(report.dividends_tax_to_pay_pln)} PLN  → poz. {pos_to_pay}"
    )
    lines.append("")

    if report.dividends_by_country:
        country_names = {"US": "USA", "CA": "Kanada", "SE": "Szwecja"}

        for country_code in sorted(report.dividends_by_country):
            cd = report.dividends_by_country[country_code]
            country_name = country_names.get(country_code, country_code)
            events = cd.events
            c_to_pay = max(Decimal("0"), cd.tax_due_pln - cd.tax_to_deduct_pln)

            lines.append(f"Kraj: {country_name} ({country_code})")
            lines.append(
                f"  Brutto: {_fmt(cd.income_pln)} PLN | Podatek źródło: {_fmt(cd.tax_paid_pln)} PLN | "
                f"Podatek PL 19%: {_fmt(cd.tax_due_pln)} PLN | "
                f"Do odliczenia: {_fmt(cd.tax_to_deduct_pln)} PLN | "
                f"Do zapłaty: {_fmt(c_to_pay)} PLN"
            )
            lines.append("")
            lines.append("─" * 126)
            lines.append(
                f"{'Data':<12}{'Instrument':<16}{'Brutto':>10} {'Wal':>4}"
                f"{'Brutto PLN':>14}{'Podatek źródło':>16}{'Podatek PL 19%':>16}"
                f"{'Cap UPO':>14}{'Do odliczenia':>16}"
            )
            lines.append("─" * 126)

            from .country import upo_rate as _upo

            country_upo = _upo(country_code)

            for e in sorted(events, key=lambda x: x.date):
                # ROUND_HALF_UP explicit — must match calculator.py + models.to_pln,
                # otherwise per-row table values diverge from country aggregate
                # for amounts ending in exactly .005 (Python default is HALF_EVEN).
                tax_pl = max(
                    Decimal("0"),
                    (e.gross_amount_pln * TAX_RATE).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
                )
                cap = (e.gross_amount_pln * country_upo).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                # deduct_pln allocated by calculator.py greedy-by-date. Σ over
                # events == cd.tax_to_deduct_pln by construction — no branch
                # logic here, no per-row recomputation. assert non-None to
                # surface contract violation early (calculator must allocate).
                assert e.deduct_pln is not None, "calculator did not allocate deduct_pln"
                deduct = e.deduct_pln
                lines.append(
                    f"{e.date.isoformat():<12}{e.symbol:<16}"
                    f"{e.gross_amount:>10.2f} {e.currency:>4}"
                    f"{_fmt(e.gross_amount_pln, 14)}"
                    f"{_fmt(e.tax_withheld_pln, 16)}"
                    f"{_fmt(tax_pl, 16)}"
                    f"{_fmt(cap, 14)}"
                    f"{_fmt(deduct, 16)}"
                )

            lines.append("─" * 126)
            lines.append("")
    lines.append("")

    # ───────────────────────────────────────────────────────────────
    # INSTRUKCJA WYPEŁNIENIA PIT-38 — co wpisać w jaką komórkę
    # ───────────────────────────────────────────────────────────────
    lines.append("═" * 70)
    lines.append(f" INSTRUKCJA WYPEŁNIENIA PIT-38 — Rok {report.year}")
    lines.append("═" * 70)
    lines.extend(_render_pit38_filling_instructions(report))

    # ───────────────────────────────────────────────────────────────
    # ZAŁĄCZNIKI PIT/ZG — które kraje, z jakimi liczbami, dlaczego
    # ───────────────────────────────────────────────────────────────
    lines.append("═" * 70)
    lines.append(f" ZAŁĄCZNIKI PIT/ZG — Rok {report.year}")
    lines.append("═" * 70)
    lines.extend(_render_pitzg_attachments(report))

    return "\n".join(lines)


def generate_positions_report(
    positions: dict[tuple[str, str], list[FifoLot]],
    year: int,
) -> str:
    """Generate open positions report as of year-end."""
    lines: list[str] = []
    lines.append("═" * 60)
    lines.append(f" Pozycje otwarte na 31.12.{year} (przeniesione na {year + 1})")
    lines.append("═" * 60)
    lines.append("")
    lines.append(f"{'Instrument':<16}{'Konto':<16}{'Ilość':>10}{'Śr. koszt':>12}{'Waluta':>8}")
    lines.append("─" * 62)

    for (account_id, symbol), lots in sorted(positions.items()):
        total_qty = sum(lot.quantity for lot in lots)
        if total_qty == 0:
            continue
        total_cost = sum(lot.quantity * lot.price_per_unit for lot in lots)
        avg_cost = total_cost / total_qty if total_qty else Decimal("0")
        currency = lots[0].currency if lots else ""
        lines.append(f"{symbol:<16}{account_id:<16}{total_qty:>10.2f}{avg_cost:>12.4f}{currency:>8}")

    lines.append("")
    return "\n".join(lines)


def generate_csv(
    reports: list[YearReport],
    output_path: str | Path,
) -> None:
    """Generate CSV with all tax events across all years.

    Schema overload: dividend rows reuse trade columns. For Typ="dividend",
    "Przychód oryg./PLN" = gross dividend, "Koszt oryg./PLN" = withholding
    tax (NOT a cost). Filter by Typ before summing cost columns.
    """
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(
        [
            "Rok",
            "Data",
            "Typ",
            "Instrument",
            "Konto",
            "Przychód oryg.",
            "Koszt oryg.",
            "Waluta",
            "Kurs NBP",
            "Przychód PLN",
            "Koszt PLN",
            "Zysk/Strata PLN",
        ]
    )

    for report in reports:
        for e in sorted(report.pit38_events, key=lambda x: x.date):
            profit = e.income_pln - e.cost_pln
            writer.writerow(
                [
                    report.year,
                    e.date.isoformat(),
                    e.event_type,
                    e.symbol,
                    e.account_id,
                    f"{e.income_original:.2f}",
                    f"{e.cost_original:.2f}",
                    e.currency,
                    f"{e.nbp_rate:.4f}",
                    f"{e.income_pln:.2f}",
                    f"{e.cost_pln:.2f}",
                    f"{profit:.2f}",
                ]
            )

        for e in sorted(report.dividend_events, key=lambda x: x.date):
            writer.writerow(
                [
                    report.year,
                    e.date.isoformat(),
                    "dividend",
                    e.symbol,
                    e.account_id,
                    f"{e.gross_amount:.2f}",
                    f"{e.tax_withheld:.2f}",
                    e.currency,
                    f"{e.nbp_rate:.4f}",
                    f"{e.gross_amount_pln:.2f}",
                    f"{e.tax_withheld_pln:.2f}",
                    f"{(e.gross_amount_pln - e.tax_withheld_pln):.2f}",
                ]
            )

    Path(output_path).write_text(output.getvalue(), encoding="utf-8")


def write_reports(
    reports: list[YearReport],
    positions: dict[tuple[str, str], list[FifoLot]],
    output_dir: str | Path,
) -> list[Path]:
    """Write all reports to output directory."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    written: list[Path] = []

    for report in reports:
        # Year report
        text = generate_year_report(report)

        # Add positions for years that have them
        text += generate_positions_report(positions, report.year)

        path = output_dir / f"pit_{report.year}.txt"
        path.write_text(text, encoding="utf-8")
        written.append(path)

    # CSV
    csv_path = output_dir / "pit_all.csv"
    generate_csv(reports, csv_path)
    written.append(csv_path)

    return written
