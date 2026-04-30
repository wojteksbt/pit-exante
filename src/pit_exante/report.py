"""Report generation for PIT tax forms."""

from __future__ import annotations

import csv
import io
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from .models import TAX_RATE, FifoLot, TaxEvent, YearReport

_PIT38_W18_FIRST_YEAR = 2025  # MF revised PIT-38 layout (wariant 18) for tax year 2025


def _fmt(amount: Decimal, width: int = 12) -> str:
    """Format decimal as PLN amount with 2 decimal places (Polish locale).

    Separator dziesiętny: przecinek; separator tysięcy: NBSP ( ).
    NBSP a nie zwykła spacja — utrzymuje wyrównanie kolumn i nie myli
    parserów CSV / split() (które tnęły po zwykłej spacji).
    """
    s = f"{amount:>{width},.2f}"
    return s.replace(",", " ").replace(".", ",")


def _fmt_orig(amount: Decimal, currency: str, width: int = 12) -> str:
    """Format decimal in original currency (kropka — format giełdowy)."""
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
    if year >= _PIT38_W18_FIRST_YEAR:
        return 47, 48, 49
    return 45, 46, 47


def _pit38_total_to_pay_position(year: int) -> int:
    """PIT-38 'PODATEK DO ZAPŁATY' (suma sekcji G) — pozycja w formularzu.

    2020-2024 (wariant 17): poz. 49
    2025+ (wariant 18+): poz. 51 — przesunięcie +2 spójne z sekcją G dyw.
    Zweryfikowane na PIT-38(17) z 2024 i PIT-38(18) z 2025.
    """
    if year >= _PIT38_W18_FIRST_YEAR:
        return 51
    return 49


def _pit38_section_c_positions(year: int) -> dict[str, int]:
    """PIT-38 sekcja C — numeracja pól wg wariantu formularza.

    | Wariant | wiersz_1 (PIT-8C, pre-fill) | wiersz_2 (Inne) | wiersz_3 (Zwolnione) | razem |
    |---------|-----------------------------|-----------------|----------------------|-------|
    | 17 (≤2024) | n/a                      | 22, 23          | n/a                  | 24-27 |
    | 18 (≥2025) | 20, 21                   | 22, 23          | 24, 25               | 26-29 |

    Wariant 18 wiersz 1 (poz. 20-21) jest auto-pre-fillowany przez US z PIT-8C
    cz. D wystawionego przez brokera; tool nie wpisuje tam wartości — tylko
    porównuje je z własnym wyliczeniem (papiery_wart_*).
    """
    if year >= _PIT38_W18_FIRST_YEAR:
        return {
            "wiersz_1_inc": 20,
            "wiersz_1_cost": 21,
            "wiersz_2_inc": 22,
            "wiersz_2_cost": 23,
            "wiersz_3_inc": 24,
            "wiersz_3_cost": 25,
            "razem_inc": 26,
            "razem_cost": 27,
            "razem_dochod": 28,
            "razem_strata": 29,
        }
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
    base = 30 if year >= _PIT38_W18_FIRST_YEAR else 28
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
    if year >= _PIT38_W18_FIRST_YEAR:
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


def _papiery_header_destination(year: int) -> str:
    """Year-aware podtytuł sekcji 'Papiery wartościowe'.

    Wariant 17 (≤2024): brak PIT-8C → user wpisuje ręcznie do wiersza 2.
    Wariant 18 (≥2025): broker wystawia PIT-8C, KAS wstępnie wypełnia
    poz. 20-21 (sekcja C wiersz 1).
    """
    if year >= _PIT38_W18_FIRST_YEAR:
        return " → PIT-8C poz. 35/36 → PIT-38 sekcja C wiersz 1 (poz. 20-21, wstępnie wypełnione)"
    return " → PIT-38 sekcja C wiersz 2 'Inne przychody' (poz. 22-23)"


def _pochodne_header_destination(year: int) -> str:
    """Year-aware podtytuł sekcji 'Instrumenty pochodne' (CFD).

    Wariant 18: broker łączy CFD i akcje w PIT-8C poz. 35/36 (jeden tor pre-fill).
    """
    if year >= _PIT38_W18_FIRST_YEAR:
        return " → ujęte w PIT-8C poz. 35/36 razem z papierami (broker łączy CFD i akcje)"
    return " → PIT-38 sekcja C wiersz 2 'Inne przychody' (poz. 22-23, sumują z papierami)"


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


def _render_razem_block(
    *,
    pos_c: dict[str, int],
    razem_wiersz_num: int,
    razem_inc: Decimal,
    razem_cost: Decimal,
    razem_pl: Decimal,
) -> list[str]:
    lines: list[str] = []
    lines.append(f"    Wiersz {razem_wiersz_num} 'Razem':")
    lines.append(f"      poz. {pos_c['razem_inc']} (Suma przychód):  {_fmt(razem_inc)} PLN")
    lines.append(f"      poz. {pos_c['razem_cost']} (Suma koszty):    {_fmt(razem_cost)} PLN")
    if razem_pl > 0:
        lines.append(f"      poz. {pos_c['razem_dochod']} (Dochód):         {_fmt(razem_pl)} PLN")
        lines.append(f"      poz. {pos_c['razem_strata']} (Strata):         puste")
    elif razem_pl < 0:
        lines.append(f"      poz. {pos_c['razem_dochod']} (Dochód):         puste")
        lines.append(f"      poz. {pos_c['razem_strata']} (Strata):         {_fmt(-razem_pl)} PLN")
    else:
        lines.append(f"      poz. {pos_c['razem_dochod']}-{pos_c['razem_strata']}: 0 (bilans zerowy)")
    return lines


def _fee_costs(report: YearReport) -> Decimal:
    return sum(
        (e.cost_pln for e in report.papiery_wart_events if e.event_type == "fee"),
        Decimal("0"),
    )


def _render_section_c_w17(
    report: YearReport,
    *,
    pos_c: dict[str, int],
) -> tuple[list[str], Decimal]:
    """Sekcja C wariant 17 (rok ≤ 2024): wszystkie zagraniczne dochody w wierszu 2.

    Razem na poz. 24-27. Pochodne sumują się z papierami w jednej linii (PIT-8C
    od zagranicznego brokera nie istnieje, więc nie ma wiersza 1).
    """
    inne_inc = report.papiery_wart_income + report.pochodne_income
    inne_cost = report.papiery_wart_cost + report.pochodne_cost
    razem_pl = inne_inc - inne_cost
    lines: list[str] = []
    lines.append("▌ SEKCJA C — Dochody/straty z papierów wartościowych (art. 30b ust. 1)")
    lines.append("    Wiersz 2 'Inne przychody':")
    lines.append(f"      poz. {pos_c['wiersz_2_inc']} (Przychód):       {_fmt(inne_inc)} PLN")
    lines.append(f"      poz. {pos_c['wiersz_2_cost']} (Koszty):         {_fmt(inne_cost)} PLN")
    lines.extend(
        _render_razem_block(
            pos_c=pos_c,
            razem_wiersz_num=3,
            razem_inc=inne_inc,
            razem_cost=inne_cost,
            razem_pl=razem_pl,
        )
    )
    lines.append("")
    return lines, razem_pl


def _render_section_c_w18_compare(
    report: YearReport,
    *,
    pos_c: dict[str, int],
) -> tuple[list[str], Decimal]:
    """Sekcja C wariant 18 (rok ≥ 2025) — compare-with-prefill.

    KAS wstępnie wypełnia poz. 20-21 z PIT-8C brokera (Razem 35/36 obejmuje
    akcje + CFD net). Kalkulator drukuje swoje wyliczenia jako referencję;
    razem_pl informacyjny dla SEKCJI D.
    """
    papiery_inc = report.papiery_wart_income
    papiery_cost = report.papiery_wart_cost
    papiery_pl = papiery_inc - papiery_cost
    pochodne_pl = report.pochodne_income - report.pochodne_cost
    razem_pl = papiery_pl + pochodne_pl

    w1 = f"{pos_c['wiersz_1_inc']}-{pos_c['wiersz_1_cost']}"

    lines: list[str] = []
    lines.append("▌ SEKCJA C — Dochody/straty z papierów wartościowych (art. 30b ust. 1)")
    lines.append(f"    Wiersz 1 'Z PIT-8C cz. D' (poz. {w1}):")
    lines.append("")
    lines.append("      Wyliczenie kalkulatora (papiery wartościowe):")
    lines.append(f"        Przychód:  {_fmt(papiery_inc)} PLN")
    lines.append(f"        Koszt:     {_fmt(papiery_cost)} PLN")
    if papiery_pl > 0:
        lines.append(f"        Dochód:    {_fmt(papiery_pl)} PLN")
    elif papiery_pl < 0:
        lines.append(f"        Strata:    {_fmt(-papiery_pl)} PLN")
    else:
        lines.append("        Bilans zerowy.")
    lines.append("")
    lines.append("      → Te wartości NIE idą do PIT-38 manualnie.")
    lines.append(f"      → KAS wstępnie wypełnia poz. {w1} danymi z PIT-8C od brokera")
    lines.append("        (usługa Twój e-PIT na podatki.gov.pl).")
    lines.append("      → Otwórz PIT-38 w usłudze Twój e-PIT i sprawdź, czy wstępnie")
    lines.append(f"        wypełnione poz. {w1} ≈ wartości powyżej. Jeśli daleko (>~1%)")
    lines.append("        — możliwy rozjazd klasyfikatora STOCK/CFD lub broker pominął")
    lines.append("        transakcje.")
    lines.append("")
    lines.append(f"    Wiersz 2 'Inne przychody' (poz. {pos_c['wiersz_2_inc']}-{pos_c['wiersz_2_cost']}):")
    lines.append("")
    lines.append("      Zostaw puste — broker ujął CFD/derywatywy w wierszu 3 PIT-8C")
    lines.append(f"      (uwzględnione w Razem poz. 35/36 → trafiają do PIT-38 poz. {w1}")
    lines.append("      przez wstępne wypełnienie).")
    if report.pochodne_events:
        lines.append("")
        lines.append(f"      Wynik CFD netto wg kalkulatora (informacyjnie): {_fmt(pochodne_pl)} PLN")
    lines.append("")
    lines.append(
        f"    Wiersz 3 'Zwolnione art. 21 ust. 1 pkt 105a' (poz. {pos_c['wiersz_3_inc']}-{pos_c['wiersz_3_cost']}):"
    )
    lines.append("")
    lines.append("      0,00 / 0,00 (retail = 0)")
    lines.append("")
    lines.append(f"    Wiersz 4 'Razem' (poz. {pos_c['razem_inc']}-{pos_c['razem_strata']}):")
    lines.append("")
    lines.append("      Pole wstępnie wypełnione = wiersz 1; podatnik niczego nie wpisuje.")
    lines.append("")
    return lines, razem_pl


def _render_loss_carryforward_note(
    report: YearReport,
    all_reports: list[YearReport],
    razem_pl: Decimal,
) -> list[str]:
    """Nuta informacyjna o stratach z lat ubiegłych (art. 9 ust. 3 / ust. 6).

    Branch A (razem_pl < 0): bieżąca strata zwiększa pulę na lata Y+1..Y+5.
    Branch B (razem_pl > 0): listujemy straty widoczne w oknie Y-5..Y-1 z
    propozycją 50% × strata/rok i hedgem o tym czego kalkulator nie wie.
    Bilans zerowy (razem_pl == 0): pomijamy.

    Kalkulator nie liczy automatycznie poz. 28/30 — kwota do wpisania
    zostaje decyzją podatnika (pula może obejmować straty spoza Exante:
    krypto, inny broker, papiery PL).
    """
    if razem_pl == 0:
        return []

    lines: list[str] = []
    pos_d = _pit38_section_d_positions(report.year)
    Y = report.year

    if razem_pl < 0:
        loss = -razem_pl
        half = (loss / Decimal("2")).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )  # 50% cap, art. 9 ust. 3
        next_pos = _pit38_section_d_positions(Y + 1)["straty_lat"]
        lines.append("▌ STRATY Z LAT UBIEGŁYCH (art. 9 ust. 3 ustawy o PIT)")
        lines.append(f"    Bieżący rok ({Y}) kończy się stratą {_fmt(loss).strip()} PLN — brak dochodu")
        lines.append("    do pomniejszenia w tym roku.")
        lines.append(f"    Ta strata zwiększa pulę dostępną w PIT-38 lat {Y + 1}–{Y + 5}.")
        lines.append("    Limity (z ustawy):")
        lines.append(f"      · klasyczny tryb: max 50% × {_fmt(loss).strip()} = {_fmt(half).strip()} PLN/rok")
        lines.append("      · od 2019 (wybór podatnika): jednorazowo do 5 000 000 PLN, reszta klasycznie")
        lines.append(f"    Jeśli następny rok ({Y + 1}) zakończy się dochodem, wpisz odpowiednią")
        lines.append(f"    kwotę w poz. {next_pos} PIT-38 ({Y + 1}).")
        lines.append("")
        return lines

    # razem_pl > 0
    income = razem_pl
    pos_strat = pos_d["straty_lat"]
    by_year = {r.year: r for r in all_reports}
    window_years = list(range(Y - 5, Y))

    lines.append(f"▌ STRATY Z LAT UBIEGŁYCH (art. 9 ust. 3 ustawy o PIT) — poz. {pos_strat}")
    lines.append("    Dochód PIT-38 z art. 30b można pomniejszyć o stratę z 5 poprzednich")
    lines.append("    lat podatkowych (z TEGO SAMEGO źródła — kapitały pieniężne).")
    lines.append("")
    lines.append(f"    W oknie {Y - 5}–{Y - 1} kalkulator widzi:")

    proposals: list[tuple[int, Decimal, Decimal]] = []
    for wy in window_years:
        if wy in by_year:
            pl = by_year[wy].pit38_profit_loss
            if pl < 0:
                strata = -pl
                half = (strata / Decimal("2")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                lines.append(
                    f"      · {wy}: strata {_fmt(strata).strip()} PLN  → max 50% = {_fmt(half).strip()} PLN"
                )
                proposals.append((wy, strata, half))
            elif pl > 0:
                lines.append(f"      · {wy}: zysk {_fmt(pl).strip()} PLN (brak straty)")
            else:
                lines.append(f"      · {wy}: bilans zerowy (brak straty)")
        else:
            lines.append(f"      · {wy}: brak danych w kalkulatorze")

    if proposals:
        sum_half = sum((p[2] for p in proposals), Decimal("0"))
        lines.append("")
        lines.append(f"    Suma propozycji (50%/rok klasycznie): {_fmt(sum_half).strip()} PLN")
        lines.append(f"    Wartość poz. {pos_strat} ≤ dochód za ten rok ({_fmt(income).strip()} PLN)")
        lines.append("    (odliczenie nie może wytworzyć nowej straty).")
        lines.append("")
        lines.append("    Tryb 5 mln (od 2019): jednorazowo do 5 000 000 PLN ze straty")
        lines.append("    jednego roku — dla Twoich kwot praktycznie = pełna strata danego roku.")
    else:
        lines.append("")
        lines.append("    Brak strat w widzianym oknie — kalkulator nie sugeruje konkretnej kwoty.")

    lines.append("")
    lines.append("    UWAGA — czego kalkulator nie wie:")
    earliest = min(by_year) if by_year else Y
    lines.append(f"      · strat z lat przed {earliest} (np. krypto 2017, inne źródła kapitałowe)")
    lines.append("      · strat z innych brokerów / papierów PL")
    lines.append("      · ile straty już rozliczyłeś w poprzednich latach")
    lines.append(f"    Wartość poz. {pos_strat} jest decyzją podatnika.")
    lines.append("")
    return lines


def _render_pit38_filling_instructions(
    report: YearReport,
    all_reports: list[YearReport],
) -> list[str]:
    """Konkretne instrukcje wypełnienia PIT-38, year-aware.

    Wariant 17 (rok ≤ 2024): sekcja C wiersz 2 (poz. 22-23) + razem 24-27,
    sekcja D 29-33, sekcja L poz. 69.
    Wariant 18 (rok ≥ 2025): compare-with-prefill — kalkulator drukuje
    wartości referencyjne dla poz. 20-21 (KAS wstępnie wypełnia z PIT-8C
    brokera), instruuje zostawić wiersz 2 puste, sekcja D 31-35, sekcja L
    poz. 72.

    `all_reports` daje kontekst cross-year — używany tylko w
    `_render_loss_carryforward_note` do skanowania okna Y-5..Y-1.
    """
    pos_due, pos_deduct, pos_to_pay = _pit38_dividend_positions(report.year)
    pos_c = _pit38_section_c_positions(report.year)
    pos_d = _pit38_section_d_positions(report.year)
    pos_pitzg = _pit38_pitzg_count_position(report.year)
    pos_total = _pit38_total_to_pay_position(report.year)

    lines: list[str] = []

    lines.append("")
    lines.append("▌ SEKCJA A — Cel złożenia")
    lines.append("    poz. 6 (Cel):            1 = pierwsze złożenie  |  2 = korekta")
    lines.append("    poz. 7 (Rodzaj korekty): 1 = art. 81 OP (zwykła) — TYLKO jeśli korekta")
    lines.append("")

    has_events = bool(report.papiery_wart_events or report.pochodne_events)

    razem_pl = Decimal("0")
    if has_events:
        renderer = (
            _render_section_c_w18_compare if report.year >= _PIT38_W18_FIRST_YEAR else _render_section_c_w17
        )
        sec_c_lines, razem_pl = renderer(report, pos_c=pos_c)
        lines.extend(sec_c_lines)

    if has_events:
        lines.append("▌ SEKCJA D — Obliczenie zobowiązania (art. 30b ust. 1)")
        if razem_pl > 0:
            podstawa = razem_pl.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
            podatek_pre = razem_pl * TAX_RATE  # poz. podatek_dochodu — zł, gr (NOT rounded)
            podatek = podatek_pre.quantize(Decimal("1"), rounding=ROUND_HALF_UP)  # poz. nalezny — pełne zł
            lines.append(f"    poz. {pos_d['straty_lat']} (Straty z lat ubiegłych): puste — patrz uwaga ↓")
            lines.append(f"    poz. {pos_d['podstawa']} (Podstawa, do pełnych zł):  {podstawa} PLN")
            lines.append(f"    poz. {pos_d['stawka']} (Stawka):                    19%")
            lines.append(
                f"    poz. {pos_d['podatek_dochodu']} (Podatek 19%):              {_fmt(podatek_pre)} PLN"
            )
            lines.append(
                f"    poz. {pos_d['podatek_za_granica']} (Podatek za granicą art. 30b ust. 5a/5b): 0,00 PLN"
            )
            lines.append(
                f"    poz. {pos_d['podatek_nalezny']} (Podatek należny, do pełnych zł):        {podatek} PLN"
            )
        else:
            lines.append(f"    poz. {pos_d['podstawa']} (Podstawa):       0  (strata, brak podatku)")
            lines.append(f"    poz. {pos_d['stawka']}-{pos_d['podatek_nalezny']}:               0 / puste")
        lines.append("")
        lines.extend(_render_loss_carryforward_note(report, all_reports, razem_pl))

    if report.dividends_income_pln > 0:
        lines.append("▌ SEKCJA G — Zryczałtowany podatek od dywidend zagr. (art. 30a)")
        lines.append(
            f"    poz. {pos_due} (Zryczałt. podatek 19%):      {_fmt(report.dividends_tax_due_pln)} PLN"
        )
        lines.append(
            f"    poz. {pos_deduct} (Podatek za granicą do odl.): "
            f"{_fmt(report.dividends_tax_to_deduct_pln)} PLN"
        )
        lines.append("      UWAGA: kwota po limicie wynikającym z UPO (liczona per kraj),")
        lines.append(
            f"             NIE faktyczny podatek u źródła pobrany ({_fmt(report.dividends_tax_paid_pln)} PLN)"
        )
        lines.append(
            f"    poz. {pos_to_pay} (Różnica do zapłaty):         {_fmt(report.dividends_tax_to_pay_pln)} PLN"
        )
        lines.append("      Zaokrąglić DO PEŁNYCH GROSZY w górę")
        lines.append("      (wyjątek z art. 63 § 1a OP dla art. 30a — NIE do pełnych zł)")
        lines.append("")

    _papiery_tax_pre = max(Decimal("0"), razem_pl * TAX_RATE)
    podatek_papiery = _papiery_tax_pre.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    podatek_dyw = report.dividends_tax_to_pay_pln
    razem = podatek_papiery + podatek_dyw
    lines.append(f"▌ POZYCJA {pos_total} — PODATEK DO ZAPŁATY")
    lines.append(
        f"    = poz. {pos_d['podatek_nalezny']} ({podatek_papiery} zł) + poz. {pos_to_pay} ({_fmt(podatek_dyw)} zł)"
    )
    lines.append(f"    = {_fmt(razem)} PLN")
    lines.append("")

    pitzg_count = len(_papiery_country_breakdown(report))
    lines.append("▌ SEKCJA L — Załączniki")
    lines.append(f"    poz. {pos_pitzg} (Liczba załączników PIT/ZG): {pitzg_count}")
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
            lines.append(f"        (kraj zakończył rok stratą {_fmt(-net)} PLN — w PIT/ZG")
            lines.append("        deklarujemy DOCHÓD; straty zagranicznej nie wykazujemy → 0,00)")
        elif net == 0:
            lines.append("        (bilans zerowy)")
        lines.append("     poz. 30 (Podatek za granicą):           0,00 PLN")
        lines.append("        (typowo brak podatku u źródła od kapitałowych dla nierezydentów PL)")
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
        lines.append("   Per kraj (informacyjnie):")
        lines.append(f"     · brutto:             {_fmt(cd.income_pln)} PLN")
        lines.append(f"     · podatek u źródła:   {_fmt(cd.tax_paid_pln)} PLN")
        lines.append(f"     · do odliczenia:      {_fmt(cd.tax_to_deduct_pln)} PLN")
        lines.append(f"     · do zapłaty:         {_fmt(c_to_pay)} PLN")
        lines.append("")

    fee_costs = _fee_costs(report)
    if fee_costs > 0:
        pos_c = _pit38_section_c_positions(report.year)
        if report.year >= _PIT38_W18_FIRST_YEAR:
            fees_pos = pos_c["wiersz_1_cost"]
            fees_note = f"wliczone we wstępnie wypełnioną poz. {fees_pos} PIT-38 " "(PIT-8C brokera, poz. 36)"
        else:
            fees_pos = pos_c["wiersz_2_cost"]
            fees_note = f"wliczone w łączną poz. {fees_pos} PIT-38"
        strata_pos = pos_c["razem_strata"]
        lines.append(f"ℹ Opłaty brokera ({_fmt(fee_costs)} PLN) {fees_note},")
        lines.append("  ale NIE atrybuowane do kraju w kalkulatorze (broker = Cypr formalnie).")
        lines.append(f"  Stąd Σ poz. 29 PIT/ZG < |poz. {strata_pos} PIT-38| o tę kwotę.")
        lines.append("")

    return lines


def generate_year_report(
    report: YearReport,
    all_reports: list[YearReport] | None = None,
) -> str:
    """Generate text report for a single tax year — three PIT-38 sections.

    `all_reports` daje cross-year kontekst dla loss carryforward (art. 9 ust. 3)
    — okno Y-5..Y-1. None → fallback do [report] (tylko własny rok).
    """
    if all_reports is None:
        all_reports = [report]
    lines: list[str] = []

    # ───────────────────────────────────────────────────────────────
    # SEKCJA 1: Papiery wartościowe → PIT-38 wiersz 1 (PIT-8C poz. 23-24)
    # ───────────────────────────────────────────────────────────────
    lines.append("═" * 70)
    lines.append(f" Papiery wartościowe — Rok {report.year}")
    lines.append(_papiery_header_destination(report.year))
    lines.append("═" * 70)
    lines.append("")
    lines.append(f"PRZYCHÓD:                                      {_fmt(report.papiery_wart_income)} PLN")
    lines.append(f"KOSZTY UZYSKANIA PRZYCHODU:                    {_fmt(report.papiery_wart_cost)} PLN")
    papiery_pl = report.papiery_wart_income - report.papiery_wart_cost
    lines.append(f"DOCHÓD / STRATA:                               {_fmt(papiery_pl)} PLN")
    fee_costs = _fee_costs(report)
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
    lines.append(_pochodne_header_destination(report.year))
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
    variant_suffix = " (wariant 18)" if report.year >= _PIT38_W18_FIRST_YEAR else ""
    lines.append(f" INSTRUKCJA WYPEŁNIENIA PIT-38 — Rok {report.year}{variant_suffix}")
    lines.append("═" * 70)
    lines.extend(_render_pit38_filling_instructions(report, all_reports))

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
    lines: list[str] = [""]
    lines.append("═" * 70)
    lines.append(f" Pozycje otwarte na 31.12.{year} (przeniesione na {year + 1})")
    lines.append("═" * 70)
    lines.append("")
    lines.append(f"{'Instrument':<16}{'Konto':<16}{'Ilość':>10}{'Śr. koszt':>12}{'Waluta':>8}")
    lines.append("─" * 70)

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
        text = generate_year_report(report, all_reports=reports)

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
