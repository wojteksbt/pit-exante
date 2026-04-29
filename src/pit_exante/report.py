"""Report generation for PIT tax forms."""

from __future__ import annotations

import csv
import io
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from .models import TAX_RATE, FifoLot, TaxEvent, YearReport
from .pit8c import Pit8CReconciliationError

_RECONCILIATION_TOLERANCE = Decimal("0.05")  # plan §6.1 D9 — abort > 5%


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


def _check_pit8c_reconciliation(report: YearReport) -> None:
    """Plan §6.1 D9 — ABORT generacji gdy rozjazd > 5% przychodu/kosztu.

    Tool autorytatywny dla wartości (art. 11a ust. 2). PIT-8C autorytatywny
    dla istnienia obowiązku informacyjnego. Rozjazd > 5% = nie-metodologiczny —
    możliwy bug klasyfikatora STOCK/CFD lub brakujące transakcje.

    Edge case (review B-Step7-2): pit8c degenerate (oba 0) + tool ma dane →
    abort z komunikatem o niespójności (loader pozwala 0/0 jako technical
    valid input, ale nie ma sensu z transakcjami w tool).
    """
    pit8c = report.pit8c
    if pit8c is None:
        return
    income_corr = report.papiery_wart_income - pit8c.poz_35_income_pln
    cost_corr = report.papiery_wart_cost - pit8c.poz_36_cost_pln
    if pit8c.poz_35_income_pln == 0 and pit8c.poz_36_cost_pln == 0:
        if report.papiery_wart_income > 0 or report.papiery_wart_cost > 0:
            raise Pit8CReconciliationError(
                f"PIT-8C dla {report.year} ma zerowe poz. 35/36, ale tool widzi "
                f"transakcje (income={report.papiery_wart_income}, "
                f"cost={report.papiery_wart_cost}). Niespójność — sprawdź czy "
                f"PIT-8C dotyczy właściwego roku/brokera lub usuń config."
            )
        return  # both 0 + tool 0 = degenerate but consistent; no abort
    if pit8c.poz_35_income_pln > 0:
        ratio = abs(income_corr) / pit8c.poz_35_income_pln
        if ratio > _RECONCILIATION_TOLERANCE:
            raise Pit8CReconciliationError(
                f"Rozjazd przychodu PIT-8C dla {report.year}: tool="
                f"{report.papiery_wart_income} vs PIT-8C poz. 35={pit8c.poz_35_income_pln} "
                f"(różnica {income_corr}, {ratio * 100:.2f}% — limit 5%). Wymagana manualna "
                f"analiza per-symbol PRZED złożeniem PIT-38 (planowany audit-classifier {report.year})."
            )
    if pit8c.poz_36_cost_pln > 0:
        ratio = abs(cost_corr) / pit8c.poz_36_cost_pln
        if ratio > _RECONCILIATION_TOLERANCE:
            raise Pit8CReconciliationError(
                f"Rozjazd kosztów PIT-8C dla {report.year}: tool="
                f"{report.papiery_wart_cost} vs PIT-8C poz. 36={pit8c.poz_36_cost_pln} "
                f"(różnica {cost_corr}, {ratio * 100:.2f}% — limit 5%)."
            )


def _render_diagnostyka_pit8c(report: YearReport) -> list[str]:
    """Plan §5.3 D8 — DIAGNOSTYKA pokazuje obie liczby (tool vs PIT-8C) zawsze
    gdy pit8c present. Pełna transparentność audytu.
    """
    pit8c = report.pit8c
    if pit8c is None:
        return []
    income_corr = report.papiery_wart_income - pit8c.poz_35_income_pln
    cost_corr = report.papiery_wart_cost - pit8c.poz_36_cost_pln
    pct_inc = (income_corr * 100 / pit8c.poz_35_income_pln) if pit8c.poz_35_income_pln else Decimal("0")
    pct_cost = (cost_corr * 100 / pit8c.poz_36_cost_pln) if pit8c.poz_36_cost_pln else Decimal("0")
    lines: list[str] = []
    lines.append("══════════════════════════════════════════════════════════════════════")
    lines.append(" DIAGNOSTYKA — tool (art. 11a ust. 2) vs PIT-8C broker")
    lines.append("══════════════════════════════════════════════════════════════════════")
    lines.append("                          Tool        PIT-8C       Rozjazd       %")
    lines.append(
        f"  poz. 20 (przychód):  {_fmt(report.papiery_wart_income)} {_fmt(pit8c.poz_35_income_pln)}"
        f" {_fmt(income_corr)} ({pct_inc:+.2f}%)"
    )
    lines.append(
        f"  poz. 21 (koszty):    {_fmt(report.papiery_wart_cost)} {_fmt(pit8c.poz_36_cost_pln)}"
        f" {_fmt(cost_corr)} ({pct_cost:+.2f}%)"
    )
    lines.append("")
    lines.append("  Tool wpisuje SWOJĄ liczbę do poz. 20/21 per D7 (art. 11a ust. 2).")
    lines.append("  PIT-8C jest referencją informacyjną — broker zna kursy sprzedaży,")
    lines.append("  art. 11a ust. 2 wymaga kursu NBP dnia poprzedzającego transakcję.")
    lines.append("══════════════════════════════════════════════════════════════════════")
    lines.append("")
    return lines


def _render_section_c_path_a(
    *,
    inne_inc: Decimal,
    inne_cost: Decimal,
    year: int,
    pos_c: dict[str, int],
    razem_wiersz_num: int,
) -> tuple[list[str], Decimal]:
    """Renderuje sekcję C ścieżki A (bez PIT-8C). Zwraca (lines, razem_pl).

    Wariant 17: wiersz 2 (poz. 22-23) + wiersz 3 razem (24-27).
    Wariant 18 fallback: wiersz 2 (22-23) + wiersz 3 zwolnione (24-25 = 0,00) +
    wiersz 4 razem (26-29).
    """
    razem_pl = inne_inc - inne_cost  # zwolnione = 0
    lines: list[str] = []
    lines.append("▌ SEKCJA C — Dochody/straty z papierów wartościowych (art. 30b ust. 1)")
    lines.append("    Wiersz 2 'Inne przychody':")
    lines.append(f"      poz. {pos_c['wiersz_2_inc']} (Przychód):       {_fmt(inne_inc)} PLN")
    lines.append(f"      poz. {pos_c['wiersz_2_cost']} (Koszty):         {_fmt(inne_cost)} PLN")
    if year >= 2025:
        lines.append("    Wiersz 3 'Zwolnione art. 21 ust. 1 pkt 105a':")
        lines.append(f"      poz. {pos_c['wiersz_3_inc']} (Przychód):              0,00 PLN")
        lines.append(f"      poz. {pos_c['wiersz_3_cost']} (Koszty):                0,00 PLN")
    lines.append(f"    Wiersz {razem_wiersz_num} 'Razem':")
    lines.append(f"      poz. {pos_c['razem_inc']} (Suma przychód):  {_fmt(inne_inc)} PLN")
    lines.append(f"      poz. {pos_c['razem_cost']} (Suma koszty):    {_fmt(inne_cost)} PLN")
    if razem_pl > 0:
        lines.append(f"      poz. {pos_c['razem_dochod']} (Dochód):         {_fmt(razem_pl)} PLN")
        lines.append(f"      poz. {pos_c['razem_strata']} (Strata):         puste")
    elif razem_pl < 0:
        lines.append(f"      poz. {pos_c['razem_dochod']} (Dochód):         puste")
        lines.append(f"      poz. {pos_c['razem_strata']} (Strata):         {_fmt(-razem_pl)} PLN")
    else:
        lines.append(f"      poz. {pos_c['razem_dochod']}-{pos_c['razem_strata']}: 0 (break-even)")
    lines.append("")
    return lines, razem_pl


def _render_section_c_path_b(
    report: YearReport,
    *,
    pos_c: dict[str, int],
    razem_wiersz_num: int,
    stock_income_correction: bool,
) -> tuple[list[str], Decimal]:
    """Renderuje sekcję C ścieżki B (z PIT-8C). Zwraca (lines, razem_pl).

    D7: tool wins poz. 20/21. D6 default = wykazanie korekty STOCK w poz. 22.
    Cytat broszury MF gated on cost_corr > 0. WARN dla negative corrections
    per plan §6.1 (broker zawyżył przychód lub koszt — możliwy bug klasyfikatora).
    """
    pit8c = report.pit8c
    assert pit8c is not None  # caller's invariant
    income_corr = report.papiery_wart_income - pit8c.poz_35_income_pln
    cost_corr = report.papiery_wart_cost - pit8c.poz_36_cost_pln
    wiersz_1_inc = report.papiery_wart_income  # tool wins per D7
    wiersz_1_cost = report.papiery_wart_cost
    if stock_income_correction and income_corr > 0:
        wiersz_2_inc = report.pochodne_income + income_corr  # D6 default
    else:
        wiersz_2_inc = report.pochodne_income  # D6 OPT-OUT (matches filed 2025)
    wiersz_2_cost = report.pochodne_cost
    razem_inc = wiersz_1_inc + wiersz_2_inc  # zwolnione = 0
    razem_cost = wiersz_1_cost + wiersz_2_cost
    razem_pl = razem_inc - razem_cost

    issuer = pit8c.issuer_name or "broker"
    lines: list[str] = []
    lines.append("▌ SEKCJA C — Dochody/straty z papierów wartościowych (art. 30b ust. 1)")
    lines.append(f"    Wiersz 1 (PIT-8C cz. D od {issuer}):")
    lines.append(
        f"      poz. {pos_c['wiersz_1_inc']} (Przychód):       {_fmt(wiersz_1_inc)} PLN  "
        f"← tool wins per D7 (art. 11a ust. 2)"
    )
    lines.append(f"        z tego: PIT-8C poz. 35:                   {_fmt(pit8c.poz_35_income_pln)} PLN")
    if income_corr != 0:
        lines.append(f"                + różnica (tool − PIT-8C):        {_fmt(income_corr)} PLN")
    lines.append("        Podstawa: art. 11a ust. 2 — kurs NBP dnia poprzedzającego transakcję.")
    if income_corr < 0:
        lines.append("        ⚠ Tool niższy niż PIT-8C — broker zawyżył przychód. Tool wpisuje")
        lines.append("          swoją wartość per D7; przygotuj uzasadnienie metodologiczne")
        lines.append("          (art. 11a ust. 2) gdyby audytor pytał.")
    lines.append("")
    lines.append(
        f"      poz. {pos_c['wiersz_1_cost']} (Koszty):         {_fmt(wiersz_1_cost)} PLN  "
        f"← tool wins per D7"
    )
    lines.append(f"        z tego: PIT-8C poz. 36:                   {_fmt(pit8c.poz_36_cost_pln)} PLN")
    if cost_corr != 0:
        lines.append(f"                + różnica (tool − PIT-8C):        {_fmt(cost_corr)} PLN")
    if cost_corr > 0:
        lines.append("        Broszura MF do PIT-38, str. 4: 'w poz. 21 należy wykazać")
        lines.append("        sumę kwot z poz. 36 PIT-8C oraz innych kosztów związanych")
        lines.append("        z przychodami z poz. 35, niewykazanych przez podmiot")
        lines.append("        sporządzający informację'.")
    elif cost_corr < 0:
        lines.append("        ⚠ Tool niższy o powyższą kwotę — możliwy bug klasyfikatora")
        lines.append("          STOCK→CFD lub brakujące transakcje. Sprawdź per-symbol")
        lines.append("          przed wysłaniem (planowany audit-classifier sub-command).")
    lines.append("")
    lines.append("    Wiersz 2 'Inne przychody — poza PIT-8C':")
    lines.append(f"      poz. {pos_c['wiersz_2_inc']} (Przychód):       {_fmt(wiersz_2_inc)} PLN")
    if report.pochodne_income > 0 or wiersz_2_inc > 0:
        lines.append(f"        z tego: CFD/derywatywy:                   {_fmt(report.pochodne_income)} PLN")
        if stock_income_correction and income_corr > 0:
            lines.append(f"                + korekta STOCK (D6 default):     {_fmt(income_corr)} PLN")
            lines.append("                  (gdy `--no-stock-income-correction`: 0)")
    lines.append(f"      poz. {pos_c['wiersz_2_cost']} (Koszty):         {_fmt(wiersz_2_cost)} PLN")
    if report.pochodne_cost > 0:
        lines.append(f"        z tego: CFD/derywatywy + rollovery:       {_fmt(report.pochodne_cost)} PLN")
    lines.append("")
    lines.append("    Wiersz 3 'Zwolnione art. 21 ust. 1 pkt 105a':")
    lines.append(f"      poz. {pos_c['wiersz_3_inc']} (Przychód):              0,00 PLN")
    lines.append(f"      poz. {pos_c['wiersz_3_cost']} (Koszty):                0,00 PLN")
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
        lines.append(f"      poz. {pos_c['razem_dochod']}-{pos_c['razem_strata']}: 0 (break-even)")
    lines.append("")
    return lines, razem_pl


def _render_pit38_filling_instructions(
    report: YearReport, *, stock_income_correction: bool = True
) -> list[str]:
    """Konkretne instrukcje wypełnienia PIT-38, year-aware + path-aware.

    Wariant 17 (rok ≤ 2024): sekcja C 22-27, sekcja D 29-33, sekcja L poz. 69.
    Wariant 18 (rok ≥ 2025) ścieżka A (bez PIT-8C): sekcja C 22-29, sekcja D
    31-35, sekcja L poz. 72.
    Wariant 18 ścieżka B (z PIT-8C): wiersz 1 (poz. 20-21) = tool wins per D7,
    z breakdown PIT-8C poz. 35 + różnica + cytat broszury MF dla cost_corr.
    DIAGNOSTYKA tabela na końcu (D8) — zawsze dla has_pit8c.

    Raises ``Pit8CReconciliationError`` gdy `abs(corr) / pit8c_*` > 5% (D9).

    ``stock_income_correction`` (D6 default = True): gdy `income_corr > 0`,
    nadwyżkę dodajemy do poz. 22 (defensywne wykazanie). OPT-OUT (False)
    pomija — odpowiada filed PIT-38 2025 user'a.
    """
    has_pit8c = report.pit8c is not None
    if has_pit8c:
        _check_pit8c_reconciliation(report)  # raises if > 5%

    pos_due, pos_deduct, pos_to_pay = _pit38_dividend_positions(report.year)
    pos_c = _pit38_section_c_positions(report.year, has_pit8c=has_pit8c)
    pos_d = _pit38_section_d_positions(report.year)
    pos_pitzg = _pit38_pitzg_count_position(report.year)
    pos_total = _pit38_total_to_pay_position(report.year)
    razem_wiersz_num = 4 if report.year >= 2025 else 3

    lines: list[str] = []

    lines.append("")
    lines.append("▌ SEKCJA A — Cel złożenia")
    lines.append("    poz. 6 (Cel):            1 = pierwsze złożenie  |  2 = korekta")
    lines.append("    poz. 7 (Rodzaj korekty): 1 = art. 81 OP (zwykła) — TYLKO jeśli korekta")
    lines.append("")

    # SEKCJA C — dispatch ścieżka A (bez PIT-8C) vs B (z PIT-8C).
    # Dla wariantu 17 pochodne u tego usera historycznie = 0, więc combined wartości
    # w wierszu 2 są bytewise identyczne ze snapshotem (pre-Step 5 behavior).
    inne_inc = report.papiery_wart_income + report.pochodne_income
    inne_cost = report.papiery_wart_cost + report.pochodne_cost

    if has_pit8c:
        sec_c_lines, razem_pl = _render_section_c_path_b(
            report,
            pos_c=pos_c,
            razem_wiersz_num=razem_wiersz_num,
            stock_income_correction=stock_income_correction,
        )
        lines.extend(sec_c_lines)
    elif report.papiery_wart_events or report.pochodne_events:
        sec_c_lines, razem_pl = _render_section_c_path_a(
            inne_inc=inne_inc,
            inne_cost=inne_cost,
            year=report.year,
            pos_c=pos_c,
            razem_wiersz_num=razem_wiersz_num,
        )
        lines.extend(sec_c_lines)
    else:
        razem_pl = Decimal("0")  # no sekcja C → no dochód/strata to compute

    sekcja_c_rendered = bool(report.papiery_wart_events or report.pochodne_events) or has_pit8c
    if sekcja_c_rendered:
        lines.append("▌ SEKCJA D — Obliczenie zobowiązania (art. 30b ust. 1)")
        if razem_pl > 0:
            podstawa = razem_pl.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
            podatek_pre = razem_pl * TAX_RATE  # poz. podatek_dochodu — zł, gr (NOT rounded)
            podatek = podatek_pre.quantize(Decimal("1"), rounding=ROUND_HALF_UP)  # poz. nalezny — pełne zł
            lines.append(f"      poz. {pos_d['podstawa']} (Podstawa, do pełnych zł):  {podstawa} PLN")
            lines.append(f"      poz. {pos_d['stawka']} (Stawka):                    19%")
            lines.append(
                f"      poz. {pos_d['podatek_dochodu']} (Podatek 19%):              {_fmt(podatek_pre)} PLN"
            )
            lines.append(
                f"      poz. {pos_d['podatek_za_granica']} (Podatek za granicą art. 30b ust. 5a/5b): 0,00 PLN"
            )
            lines.append(
                f"      poz. {pos_d['podatek_nalezny']} (Podatek należny, do pełnych zł):        {podatek} PLN"
            )
        else:
            lines.append(f"      poz. {pos_d['podstawa']} (Podstawa):       0  (strata, brak podatku)")
            lines.append(f"      poz. {pos_d['stawka']}-{pos_d['podatek_nalezny']}:               0 / puste")
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

    _papiery_tax_pre = max(Decimal("0"), razem_pl * TAX_RATE)
    podatek_papiery = _papiery_tax_pre.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    podatek_dyw = report.dividends_tax_to_pay_pln
    razem = podatek_papiery + podatek_dyw
    lines.append(f"▌ PODATEK DO ZAPŁATY (poz. {pos_total} PIT-38)")
    lines.append(
        f"      = poz. {pos_d['podatek_nalezny']} ({podatek_papiery} zł) + poz. {pos_to_pay} ({_fmt(podatek_dyw)} zł)"
    )
    lines.append(f"      = {_fmt(razem)} PLN")
    lines.append("")

    pitzg_count = len(_papiery_country_breakdown(report))
    lines.append("▌ SEKCJA L — Załączniki")
    lines.append(f"      poz. {pos_pitzg} (Liczba załączników PIT/ZG): {pitzg_count}")
    lines.append("")

    if report.year >= 2025 and not has_pit8c and sekcja_c_rendered:
        lines.append(f"⚠ UWAGA wariant 18: brak config/pit8c/{report.year}.json — generuję bez wiersza 1.")
        lines.append("  Jeśli broker wystawił PIT-8C — utwórz config i regeneruj raport.")
        lines.append("  Ścieżka A: cały obrót w wierszu drugim + zwolnione w wierszu trzecim (zerowe).")
        lines.append("")

    if has_pit8c:
        lines.extend(_render_diagnostyka_pit8c(report))

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
        # Year/path-aware: fees w wierszu 2 (path A, poz. 23) lub wierszu 1 (path B, poz. 21).
        # Strata position: w17 → poz. 27, w18 → poz. 29.
        has_pit8c = report.pit8c is not None
        pos_c = _pit38_section_c_positions(report.year, has_pit8c=has_pit8c)
        fees_pos = pos_c["wiersz_1_cost"] if has_pit8c else pos_c["wiersz_2_cost"]
        strata_pos = pos_c["razem_strata"]
        lines.append(f"ℹ Opłaty brokera ({_fmt(fee_costs)} PLN) wliczone w łączną poz. {fees_pos} PIT-38,")
        lines.append("  ale NIE atrybuowane do kraju w tym narzędziu (broker = Cypr formalnie).")
        lines.append(f"  Stąd Σ poz. 29 PIT/ZG < |poz. {strata_pos} PIT-38| o tę kwotę.")
        lines.append("")

    return lines


def generate_year_report(report: YearReport, *, stock_income_correction: bool = True) -> str:
    """Generate text report for a single tax year — three PIT-38 sections.

    ``stock_income_correction`` (D6 default = True) — gdy ścieżka B (z PIT-8C)
    i tool's STOCK income > PIT-8C poz. 35, dodaj nadwyżkę do poz. 22 PIT-38.
    Wire'd przez Step 8 CLI flag ``--no-stock-income-correction``.
    """
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
    variant_suffix = " (wariant 18)" if report.year >= 2025 else ""
    lines.append(f" INSTRUKCJA WYPEŁNIENIA PIT-38 — Rok {report.year}{variant_suffix}")
    lines.append("═" * 70)
    lines.extend(_render_pit38_filling_instructions(report, stock_income_correction=stock_income_correction))

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
    *,
    stock_income_correction: bool = True,
) -> list[Path]:
    """Write all reports to output directory.

    ``stock_income_correction`` (D6 default = True) plumbed to
    ``generate_year_report`` for ścieżki B (z PIT-8C). CLI flag
    ``--no-stock-income-correction`` (Step 8) wstrzykuje False.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    written: list[Path] = []

    for report in reports:
        # Year report
        text = generate_year_report(report, stock_income_correction=stock_income_correction)

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
