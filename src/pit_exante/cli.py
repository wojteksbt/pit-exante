#!/usr/bin/env python3
"""CLI entry point for PIT Exante calculator."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .calculator import calculate
from .report import write_reports


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="PIT Exante — Polish tax calculator for Exante broker transactions",
    )
    parser.add_argument(
        "--transactions",
        default="data/transactions.json",
        help="Path to transactions JSON file (default: data/transactions.json)",
    )
    parser.add_argument(
        "--output",
        default="output",
        help="Output directory for reports (default: output)",
    )
    parser.add_argument(
        "--year",
        type=int,
        help="Generate report for a specific year only",
    )
    args = parser.parse_args(argv)

    transactions_path = Path(args.transactions)
    if not transactions_path.exists():
        print(f"Error: transactions file not found: {transactions_path}", file=sys.stderr)
        sys.exit(1)

    print(f"Loading transactions from {transactions_path}...")
    reports, positions = calculate(transactions_path)

    if args.year:
        reports = [r for r in reports if r.year == args.year]
        if not reports:
            print(f"No data for year {args.year}", file=sys.stderr)
            sys.exit(1)

    print(f"\nGenerated reports for {len(reports)} year(s):")
    for report in reports:
        print(f"\n  Year {report.year}:")
        print(f"    PIT-38 income:     {report.pit38_income:>12,.2f} PLN")
        print(f"    PIT-38 cost:       {report.pit38_cost:>12,.2f} PLN")
        print(f"    PIT-38 profit:     {report.pit38_profit_loss:>12,.2f} PLN")
        print(f"    PIT-38 tax (19%):  {report.pit38_tax:>12,.2f} PLN")
        print(f"    Dividends gross:   {report.dividends_income_pln:>12,.2f} PLN")
        print(f"    Div tax withheld:  {report.dividends_tax_paid_pln:>12,.2f} PLN")
        print(f"    Div tax to pay:    {report.dividends_tax_to_pay_pln:>12,.2f} PLN")

    written = write_reports(reports, positions, args.output)
    print(f"\nWritten {len(written)} files to {args.output}/:")
    for p in written:
        print(f"  {p}")

    # Summary of open positions
    if positions:
        print(f"\nOpen positions ({len(positions)} instruments):")
        for (account_id, symbol), lots in sorted(positions.items()):
            total_qty = sum(lot.quantity for lot in lots)
            if total_qty > 0:
                print(f"  {symbol:<16} {account_id:<16} qty={total_qty}")


if __name__ == "__main__":
    main()
