"""Report generation for PIT tax forms."""

from __future__ import annotations

import csv
import io
from decimal import Decimal
from pathlib import Path

from .models import TAX_RATE, DividendEvent, FifoLot, TaxEvent, YearReport


def _fmt(amount: Decimal, width: int = 12) -> str:
    """Format decimal as PLN amount with 2 decimal places."""
    return f"{amount:>{width},.2f}"


def _fmt_orig(amount: Decimal, currency: str, width: int = 12) -> str:
    """Format decimal in original currency."""
    return f"{amount:>{width},.2f} {currency}"


def generate_year_report(report: YearReport) -> str:
    """Generate text report for a single tax year."""
    lines: list[str] = []

    lines.append("═" * 60)
    lines.append(f" PIT-38 — Rok podatkowy {report.year}")
    lines.append("═" * 60)
    lines.append("")

    # PIT-38 summary
    lines.append(f"PRZYCHÓD (ze sprzedaży papierów wartościowych): {_fmt(report.pit38_income)} PLN")
    lines.append(f"KOSZTY UZYSKANIA PRZYCHODU:                     {_fmt(report.pit38_cost)} PLN")

    sell_costs = sum(e.cost_pln for e in report.pit38_events if e.event_type == "sell")
    rollover_costs = sum(e.cost_pln for e in report.pit38_events if e.event_type == "rollover_cost")
    rollover_income = sum(e.income_pln for e in report.pit38_events if e.event_type == "rollover_income")
    fee_costs = sum(e.cost_pln for e in report.pit38_events if e.event_type == "fee")
    fractional_income = sum(e.income_pln for e in report.pit38_events if e.event_type == "fractional_cash")

    if rollover_costs > 0:
        lines.append(f"  w tym rollover (swap overnight):                {_fmt(rollover_costs)} PLN")
    if fee_costs > 0:
        lines.append(f"  w tym opłaty brokera:                           {_fmt(fee_costs)} PLN")
    if rollover_income > 0:
        lines.append(f"  przychód rollover:                              {_fmt(rollover_income)} PLN")

    lines.append(f"DOCHÓD / STRATA:                                {_fmt(report.pit38_profit_loss)} PLN")
    lines.append(f"PODATEK (19%):                                  {_fmt(report.pit38_tax)} PLN")
    lines.append("")

    # Transaction details
    if report.pit38_events:
        lines.append("Szczegóły transakcji:")
        lines.append("─" * 100)
        lines.append(
            f"{'Data':<12}{'Typ':<18}{'Instrument':<16}"
            f"{'Przychód PLN':>14}{'Koszt PLN':>14}{'Zysk/Strata':>14}"
        )
        lines.append("─" * 100)

        for e in sorted(report.pit38_events, key=lambda x: x.date):
            profit = e.income_pln - e.cost_pln
            lines.append(
                f"{e.date.isoformat():<12}{e.event_type:<18}{e.symbol:<16}"
                f"{_fmt(e.income_pln, 14)}{_fmt(e.cost_pln, 14)}{_fmt(profit, 14)}"
            )

        lines.append("─" * 100)
        total_profit = report.pit38_income - report.pit38_cost
        lines.append(
            f"{'RAZEM':<12}{'':<18}{'':<16}"
            f"{_fmt(report.pit38_income, 14)}{_fmt(report.pit38_cost, 14)}{_fmt(total_profit, 14)}"
        )
    lines.append("")

    # PIT-36 / PIT-ZG (dividends)
    lines.append("═" * 60)
    lines.append(f" PIT-36 / PIT-ZG — Dywidendy zagraniczne {report.year}")
    lines.append("═" * 60)
    lines.append("")

    lines.append(f"DYWIDENDY BRUTTO:                               {_fmt(report.dividends_income_pln)} PLN")
    lines.append(f"PODATEK POBRANY U ŹRÓDŁA:                       {_fmt(report.dividends_tax_paid_pln)} PLN")
    lines.append(f"PODATEK POLSKI (19%):                           {_fmt(report.dividends_tax_due_pln)} PLN")
    lines.append(f"DO ZAPŁATY W POLSCE:                            {_fmt(report.dividends_tax_to_pay_pln)} PLN")
    lines.append("")

    if report.dividend_events:
        # Group dividends by country for PIT/ZG
        country_names = {"US": "USA", "CA": "Kanada", "SE": "Szwecja"}
        by_country: dict[str, list[DividendEvent]] = {}
        for e in report.dividend_events:
            by_country.setdefault(e.country or "??", []).append(e)

        for country_code in sorted(by_country):
            events = by_country[country_code]
            country_name = country_names.get(country_code, country_code)

            c_gross = sum(e.gross_amount_pln for e in events)
            c_tax_paid = sum(e.tax_withheld_pln for e in events)
            c_tax_due = max(Decimal("0"), (c_gross * TAX_RATE).quantize(Decimal("0.01")))
            c_to_pay = max(Decimal("0"), c_tax_due - c_tax_paid)

            lines.append(f"Kraj: {country_name} ({country_code})")
            lines.append(f"  Brutto: {_fmt(c_gross)} PLN | Podatek źródło: {_fmt(c_tax_paid)} PLN | "
                         f"Podatek PL 19%: {_fmt(c_tax_due)} PLN | Do zapłaty: {_fmt(c_to_pay)} PLN")
            lines.append("")
            lines.append("─" * 110)
            lines.append(
                f"{'Data':<12}{'Instrument':<16}{'Brutto':>10} {'Wal':>4}"
                f"{'Brutto PLN':>14}{'Podatek źródło':>16}{'Podatek PL 19%':>16}{'Do zapłaty':>14}"
            )
            lines.append("─" * 110)

            for e in sorted(events, key=lambda x: x.date):
                tax_pl = max(Decimal("0"), (e.gross_amount_pln * TAX_RATE).quantize(Decimal("0.01")))
                to_pay = max(Decimal("0"), tax_pl - e.tax_withheld_pln)
                lines.append(
                    f"{e.date.isoformat():<12}{e.symbol:<16}"
                    f"{e.gross_amount:>10.2f} {e.currency:>4}"
                    f"{_fmt(e.gross_amount_pln, 14)}"
                    f"{_fmt(e.tax_withheld_pln, 16)}"
                    f"{_fmt(tax_pl, 16)}"
                    f"{_fmt(to_pay, 14)}"
                )

            lines.append("─" * 110)
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
    """Generate CSV with all tax events across all years."""
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
