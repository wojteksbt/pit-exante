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
    parser.add_argument(
        "--pit8c-config-dir",
        type=Path,
        default=None,
        help=(
            "Directory with PIT-8C configs ({year}.json). "
            "Default: <repo>/config/pit8c/. Required for wariant 18 (rok ≥ 2025) "
            "ścieżka B (z PIT-8C). Brak configu → ścieżka A z WARN."
        ),
    )
    parser.add_argument(
        "--no-stock-income-correction",
        action="store_true",
        help=(
            "D6 OPT-OUT (plan §5.3): pomiń dodawanie nadwyżki STOCK income "
            "(tool − PIT-8C poz. 35) do poz. 22 PIT-38 ścieżki B. Default: D6 "
            "default włączony — defensywne wykazanie. OPT-OUT odpowiada filed "
            "PIT-38 2025 (poz. 22 = CFD only)."
        ),
    )
    args = parser.parse_args(argv)

    transactions_path = Path(args.transactions)
    if not transactions_path.exists():
        print(f"Error: transactions file not found: {transactions_path}", file=sys.stderr)
        sys.exit(1)

    # Auto-discover PIT-8C config dir: repo's config/pit8c/ as default
    pit8c_config_dir = args.pit8c_config_dir
    if pit8c_config_dir is None:
        repo_root = Path(__file__).resolve().parent.parent.parent
        default_dir = repo_root / "config" / "pit8c"
        if default_dir.is_dir():
            pit8c_config_dir = default_dir

    print(f"Loading transactions from {transactions_path}...")
    if pit8c_config_dir is not None:
        print(f"PIT-8C config dir: {pit8c_config_dir}")
    reports, positions = calculate(transactions_path, pit8c_config_dir=pit8c_config_dir)

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

    written = write_reports(
        reports,
        positions,
        args.output,
        stock_income_correction=not args.no_stock_income_correction,
    )
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
