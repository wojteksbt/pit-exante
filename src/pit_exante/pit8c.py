"""PIT-8C config loader for PIT-38 wariant 18 (rok ≥ 2025).

User ręcznie transkrybuje poz. 35 i poz. 36 z PDF brokera do
`config/pit8c/{year}.json`. Loader walidacją chroni przed typowymi
błędami transkrypcji (negatywy, missing fields, niespójna metadata).

Plan: docs/internal/PLAN_PIT8C_2025.md sekcja 3 (Input model) + 6.1 (warningi).
"""

from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation
from pathlib import Path

from pit_exante.models import PitEightCInfo, YearReport


class Pit8CConfigError(ValueError):
    """Raised when PIT-8C config is malformed, invalid, or inconsistent."""


def load_pit8c(year: int, config_dir: Path) -> PitEightCInfo | None:
    """Load PIT-8C cz. D info from ``{config_dir}/{year}.json``.

    Returns ``None`` if the file does not exist (legacy path — wariant 17
    or rok ≥ 2025 bez PIT-8C). Raises :class:`Pit8CConfigError` on every
    malformation per plan §6.1 matrix.
    """
    path = config_dir / f"{year}.json"
    if not path.exists():
        return None

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise Pit8CConfigError(f"Malformed JSON in {path}: {e}") from e

    if not isinstance(data, dict):
        raise Pit8CConfigError(f"{path}: expected JSON object, got {type(data).__name__}")

    file_year = data.get("year")
    if file_year != year:
        raise Pit8CConfigError(f"{path}: year in file ({file_year!r}) ≠ requested year ({year})")

    if year < 2025:
        raise Pit8CConfigError(
            f"{path}: PIT-8C config dla roku {year} — wariant 17 nie obsługuje "
            f"wiersza 1 dla większości userów. Usuń config lub zmień rok."
        )

    for required in ("poz_35_income_pln", "poz_36_cost_pln"):
        if required not in data:
            raise Pit8CConfigError(f"{path}: niekompletny config — wymagane pole {required!r}")

    try:
        poz_35 = Decimal(str(data["poz_35_income_pln"]))
        poz_36 = Decimal(str(data["poz_36_cost_pln"]))
    except (InvalidOperation, TypeError) as e:
        raise Pit8CConfigError(f"{path}: poz_35/poz_36 not parseable as Decimal: {e}") from e

    if poz_35 < 0 or poz_36 < 0:
        raise Pit8CConfigError(f"{path}: PIT-8C nie może mieć ujemnych wartości — sprawdź PDF.")

    if poz_35 == 0 and poz_36 > 0:
        raise Pit8CConfigError(
            f"{path}: zerowy przychód ale niezerowe koszty — niemożliwe. " f"Sprawdź PDF/transkrypcję."
        )

    return PitEightCInfo(
        year=year,
        poz_35_income_pln=poz_35,
        poz_36_cost_pln=poz_36,
        issuer_name=data.get("issuer_name"),
        issuer_nip=data.get("issuer_nip"),
        notes=data.get("notes"),
    )


def hydrate_year_reports(reports: list[YearReport], config_dir: Path) -> None:
    """Populate ``report.pit8c`` for each report whose year has a config file.

    Mutates ``reports`` in place. Years without a matching ``{year}.json``
    are left with ``pit8c = None`` (legacy path). Raises
    :class:`Pit8CConfigError` if any matching config is malformed.
    """
    for report in reports:
        info = load_pit8c(report.year, config_dir)
        if info is not None:
            report.pit8c = info
