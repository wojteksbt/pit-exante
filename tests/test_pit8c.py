"""Unit tests for src/pit_exante/pit8c.py loader (Step 2 of PLAN_PIT8C_2025.md)."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from pit_exante.calculator import _apply_pit8c_to_reports
from pit_exante.models import PitEightCInfo, YearReport
from pit_exante.pit8c import Pit8CConfigError, load_pit8c


def _write(config_dir: Path, year: int, data: dict) -> Path:
    config_dir.mkdir(parents=True, exist_ok=True)
    path = config_dir / f"{year}.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


class TestLoadPit8CHappyPaths:
    def test_missing_file_returns_none(self, tmp_path):
        assert load_pit8c(2025, tmp_path) is None

    def test_valid_minimal(self, tmp_path):
        _write(
            tmp_path,
            2025,
            {
                "year": 2025,
                "poz_35_income_pln": "70218.00",
                "poz_36_cost_pln": "73639.00",
            },
        )
        info = load_pit8c(2025, tmp_path)
        assert info == PitEightCInfo(
            year=2025,
            poz_35_income_pln=Decimal("70218.00"),
            poz_36_cost_pln=Decimal("73639.00"),
        )

    def test_valid_with_optional_fields(self, tmp_path):
        _write(
            tmp_path,
            2025,
            {
                "year": 2025,
                "issuer_name": "Ext Sp. z o.o. Oddział W Polsce",
                "issuer_nip": "1080028081",
                "poz_35_income_pln": "70218.00",
                "poz_36_cost_pln": "73639.00",
                "notes": "Wystawione 2026-02-XX",
            },
        )
        info = load_pit8c(2025, tmp_path)
        assert info is not None
        assert info.issuer_name == "Ext Sp. z o.o. Oddział W Polsce"
        assert info.issuer_nip == "1080028081"
        assert info.notes == "Wystawione 2026-02-XX"

    def test_zero_both_positions_accepted(self, tmp_path):
        # Edge case: rare but valid — broker wystawił PIT-8C bez transakcji
        _write(
            tmp_path,
            2025,
            {
                "year": 2025,
                "poz_35_income_pln": "0.00",
                "poz_36_cost_pln": "0.00",
            },
        )
        info = load_pit8c(2025, tmp_path)
        assert info is not None
        assert info.poz_35_income_pln == Decimal("0.00")
        assert info.poz_36_cost_pln == Decimal("0.00")

    def test_decimal_precision_preserved(self, tmp_path):
        # No float roundtrip — string input goes directly to Decimal
        _write(
            tmp_path,
            2025,
            {
                "year": 2025,
                "poz_35_income_pln": "70218.99",
                "poz_36_cost_pln": "73639.01",
            },
        )
        info = load_pit8c(2025, tmp_path)
        assert info is not None
        assert info.poz_35_income_pln == Decimal("70218.99")
        assert info.poz_36_cost_pln == Decimal("73639.01")
        # Verify no float artifact
        assert str(info.poz_35_income_pln) == "70218.99"


class TestLoadPit8CErrorPaths:
    def test_malformed_json_raises(self, tmp_path):
        path = tmp_path / "2025.json"
        path.write_text("{not valid json", encoding="utf-8")
        with pytest.raises(Pit8CConfigError, match="Malformed JSON"):
            load_pit8c(2025, tmp_path)

    def test_non_object_root_raises(self, tmp_path):
        path = tmp_path / "2025.json"
        path.write_text("[]", encoding="utf-8")
        with pytest.raises(Pit8CConfigError, match="expected JSON object"):
            load_pit8c(2025, tmp_path)

    def test_year_mismatch_raises(self, tmp_path):
        _write(
            tmp_path,
            2025,
            {
                "year": 2024,
                "poz_35_income_pln": "100.00",
                "poz_36_cost_pln": "50.00",
            },
        )
        with pytest.raises(Pit8CConfigError, match="year in file"):
            load_pit8c(2025, tmp_path)

    def test_year_below_2025_raises(self, tmp_path):
        _write(
            tmp_path,
            2024,
            {
                "year": 2024,
                "poz_35_income_pln": "100.00",
                "poz_36_cost_pln": "50.00",
            },
        )
        with pytest.raises(Pit8CConfigError, match="wariant 17"):
            load_pit8c(2024, tmp_path)

    def test_missing_poz_35_raises(self, tmp_path):
        _write(
            tmp_path,
            2025,
            {
                "year": 2025,
                "poz_36_cost_pln": "50.00",
            },
        )
        with pytest.raises(Pit8CConfigError, match="poz_35_income_pln"):
            load_pit8c(2025, tmp_path)

    def test_missing_poz_36_raises(self, tmp_path):
        _write(
            tmp_path,
            2025,
            {
                "year": 2025,
                "poz_35_income_pln": "100.00",
            },
        )
        with pytest.raises(Pit8CConfigError, match="poz_36_cost_pln"):
            load_pit8c(2025, tmp_path)

    def test_negative_poz_35_raises(self, tmp_path):
        _write(
            tmp_path,
            2025,
            {
                "year": 2025,
                "poz_35_income_pln": "-100.00",
                "poz_36_cost_pln": "50.00",
            },
        )
        with pytest.raises(Pit8CConfigError, match="ujemnych"):
            load_pit8c(2025, tmp_path)

    def test_negative_poz_36_raises(self, tmp_path):
        _write(
            tmp_path,
            2025,
            {
                "year": 2025,
                "poz_35_income_pln": "100.00",
                "poz_36_cost_pln": "-50.00",
            },
        )
        with pytest.raises(Pit8CConfigError, match="ujemnych"):
            load_pit8c(2025, tmp_path)

    def test_zero_income_nonzero_cost_raises(self, tmp_path):
        _write(
            tmp_path,
            2025,
            {
                "year": 2025,
                "poz_35_income_pln": "0.00",
                "poz_36_cost_pln": "50.00",
            },
        )
        with pytest.raises(Pit8CConfigError, match="niemożliwe"):
            load_pit8c(2025, tmp_path)

    def test_non_decimal_string_raises(self, tmp_path):
        _write(
            tmp_path,
            2025,
            {
                "year": 2025,
                "poz_35_income_pln": "abc",
                "poz_36_cost_pln": "50.00",
            },
        )
        with pytest.raises(Pit8CConfigError, match="not parseable"):
            load_pit8c(2025, tmp_path)

    def test_missing_year_field_raises(self, tmp_path):
        # year defaults to None, !=  arg → mismatch
        _write(
            tmp_path,
            2025,
            {
                "poz_35_income_pln": "100.00",
                "poz_36_cost_pln": "50.00",
            },
        )
        with pytest.raises(Pit8CConfigError, match="year in file"):
            load_pit8c(2025, tmp_path)

    # B1: schema mandates string-typed amounts (Decimal precision invariant).
    def test_json_float_rejected(self, tmp_path):
        _write(
            tmp_path,
            2025,
            {
                "year": 2025,
                "poz_35_income_pln": 70218.00,  # JSON number — not str
                "poz_36_cost_pln": "73639.00",
            },
        )
        with pytest.raises(Pit8CConfigError, match="musi być stringiem"):
            load_pit8c(2025, tmp_path)

    def test_json_int_rejected(self, tmp_path):
        _write(
            tmp_path,
            2025,
            {
                "year": 2025,
                "poz_35_income_pln": "100.00",
                "poz_36_cost_pln": 50,  # JSON int — not str
            },
        )
        with pytest.raises(Pit8CConfigError, match="musi być stringiem"):
            load_pit8c(2025, tmp_path)

    def test_json_null_rejected(self, tmp_path):
        # null is technically present but not a string — different from missing
        _write(
            tmp_path,
            2025,
            {
                "year": 2025,
                "poz_35_income_pln": None,
                "poz_36_cost_pln": "50.00",
            },
        )
        with pytest.raises(Pit8CConfigError, match="musi być stringiem"):
            load_pit8c(2025, tmp_path)


class TestApplyPit8cToReports:
    """Step 3 wiring + B3 cohesion fix: pit8c assignment lives in calculator."""

    def test_year_with_config_gets_pit8c(self, tmp_path):
        _write(
            tmp_path,
            2025,
            {
                "year": 2025,
                "poz_35_income_pln": "70218.00",
                "poz_36_cost_pln": "73639.00",
            },
        )
        reports = [YearReport(year=2025), YearReport(year=2024)]
        _apply_pit8c_to_reports(reports, tmp_path)
        assert reports[0].pit8c is not None
        assert reports[0].pit8c.poz_35_income_pln == Decimal("70218.00")
        assert reports[1].pit8c is None  # 2024 has no config, stays None

    def test_no_configs_leaves_all_none(self, tmp_path):
        reports = [YearReport(year=2024), YearReport(year=2025)]
        _apply_pit8c_to_reports(reports, tmp_path)
        assert all(r.pit8c is None for r in reports)

    def test_empty_reports_list(self, tmp_path):
        reports = []
        _apply_pit8c_to_reports(reports, tmp_path)  # no-op, no exception
        assert reports == []

    def test_malformed_config_bubbles_up(self, tmp_path):
        path = tmp_path / "2025.json"
        path.write_text("{not valid", encoding="utf-8")
        reports = [YearReport(year=2025)]
        with pytest.raises(Pit8CConfigError, match="Malformed JSON"):
            _apply_pit8c_to_reports(reports, tmp_path)

    def test_returns_none(self, tmp_path):
        # In-place mutation — explicit None return contract
        reports = [YearReport(year=2025)]
        result = _apply_pit8c_to_reports(reports, tmp_path)
        assert result is None

    def test_multiple_years_with_configs(self, tmp_path):
        # T2 from review: cover N≥2 reports both with configs
        _write(
            tmp_path,
            2025,
            {
                "year": 2025,
                "poz_35_income_pln": "100.00",
                "poz_36_cost_pln": "50.00",
            },
        )
        _write(
            tmp_path,
            2026,
            {
                "year": 2026,
                "poz_35_income_pln": "200.00",
                "poz_36_cost_pln": "150.00",
            },
        )
        reports = [YearReport(year=2025), YearReport(year=2026)]
        _apply_pit8c_to_reports(reports, tmp_path)
        assert reports[0].pit8c.poz_35_income_pln == Decimal("100.00")
        assert reports[1].pit8c.poz_35_income_pln == Decimal("200.00")


class TestCalculateAcceptsPit8CConfigDir:
    """Step 3 wiring: calculate() signature + default behaviour smoke tests.

    Heavy end-to-end coverage (real transactions + real config) is in
    tests/personal/ — Step 9 personal regression.
    """

    def test_signature_accepts_kwarg(self):
        import inspect

        from pit_exante import calculator

        sig = inspect.signature(calculator.calculate)
        assert "pit8c_config_dir" in sig.parameters
        assert sig.parameters["pit8c_config_dir"].default is None

    def test_legacy_positional_call_still_works(self):
        # The old single-arg signature must remain callable. We can't run a
        # full calculate() here without transaction fixtures — just verify
        # the parameter is optional.
        import inspect

        from pit_exante import calculator

        sig = inspect.signature(calculator.calculate)
        required = [name for name, p in sig.parameters.items() if p.default is inspect.Parameter.empty]
        assert required == ["transactions_path"]
