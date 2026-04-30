"""Tests for CLI argument parsing."""

from __future__ import annotations

import inspect

import pytest

from pit_exante import cli


class TestCli:
    def test_main_signature_includes_argv(self):
        sig = inspect.signature(cli.main)
        assert "argv" in sig.parameters

    def test_help_runs(self, capsys):
        with pytest.raises(SystemExit):
            cli.main(["--help"])
        captured = capsys.readouterr()
        assert "--transactions" in captured.out
        assert "--year" in captured.out
