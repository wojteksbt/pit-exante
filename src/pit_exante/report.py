"""Report generation for PIT tax forms."""

from __future__ import annotations

import csv
import io
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

from .models import TAX_RATE, DividendEvent, FifoLot, TaxEvent, YearReport


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


def generate_year_report(report: YearReport) -> str:
    """Generate text report for a single tax year — three PIT-38 sections."""
    lines: list[str] = []

    # ───────────────────────────────────────────────────────────────
    # SEKCJA 1: Papiery wartościowe → PIT-38 wiersz 1 (PIT-8C poz. 23-24)
    # ───────────────────────────────────────────────────────────────
    lines.append("═" * 70)
    lines.append(f" Papiery wartościowe — Rok {report.year}")
    lines.append(" → PIT-38 sekcja C wiersz 1 (PIT-8C poz. 23-24)")
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
    lines.append(" → PIT-38 sekcja C wiersz 3 (PIT-8C poz. 27-28)")
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

    lines.append(f"DYWIDENDY BRUTTO (do wyliczenia poz. {pos_due}):           {_fmt(report.dividends_income_pln)} PLN")
    lines.append(f"PODATEK POBRANY U ŹRÓDŁA (informacyjnie):       {_fmt(report.dividends_tax_paid_pln)} PLN")
    lines.append(f"PODATEK POLSKI 19%:                             {_fmt(report.dividends_tax_due_pln)} PLN  → poz. {pos_due}")
    lines.append(f"PODATEK DO ODLICZENIA (per-UPO cap):            {_fmt(report.dividends_tax_to_deduct_pln)} PLN  → poz. {pos_deduct}")
    lines.append(f"DO ZAPŁATY W POLSCE:                            {_fmt(report.dividends_tax_to_pay_pln)} PLN  → poz. {pos_to_pay}")
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

            from .country import is_below_upo_threshold, upo_rate as _upo
            # Per-row deduction musi replikować country branch — inaczej suma per-row
            # ≠ aggregate (USA 2025 case: 50.12 vs 50.24 PLN).
            no_cap_branch = is_below_upo_threshold(country_code, events)
            country_upo = _upo(country_code)

            for e in sorted(events, key=lambda x: x.date):
                # ROUND_HALF_UP explicit — must match calculator.py + models.to_pln,
                # otherwise per-row table values diverge from country aggregate
                # for amounts ending in exactly .005 (Python default is HALF_EVEN).
                tax_pl = max(Decimal("0"), (e.gross_amount_pln * TAX_RATE).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
                cap = (e.gross_amount_pln * country_upo).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                if no_cap_branch:
                    # No PLN cap clamping. Aggregate guarantees sum(WHT) ≤ c_due
                    # because effective rate ≤ UPO 15% < PL 19%. Show full WHT
                    # per row — sum exactly equals country aggregate. Don't clamp
                    # to per-row PL19 (would lose ≤0.01 PLN on rows where WHT
                    # slightly exceeds PL19 due to NBP rate spread vs annual avg).
                    deduct = e.tax_withheld_pln
                else:
                    # Cap clamping (np. CA z WHT 25%) — odlicz min(WHT, cap UPO)
                    deduct = min(e.tax_withheld_pln, cap)
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
        lines.append(
            f"{symbol:<16}{account_id:<16}{total_qty:>10.2f}{avg_cost:>12.4f}{currency:>8}"
        )

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
    writer.writerow([
        "Rok", "Data", "Typ", "Instrument", "Konto",
        "Przychód oryg.", "Koszt oryg.", "Waluta", "Kurs NBP",
        "Przychód PLN", "Koszt PLN", "Zysk/Strata PLN",
    ])

    for report in reports:
        for e in sorted(report.pit38_events, key=lambda x: x.date):
            profit = e.income_pln - e.cost_pln
            writer.writerow([
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
            ])

        for e in sorted(report.dividend_events, key=lambda x: x.date):
            writer.writerow([
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
            ])

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
