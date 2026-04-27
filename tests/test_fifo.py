"""Tests for FIFO engine."""

import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from pit_exante.fifo import FifoEngine, _pln


class TestPln:
    """Test PLN conversion and rounding."""

    def test_basic_conversion(self):
        assert _pln(Decimal("100"), Decimal("4.0")) == Decimal("400.00")

    def test_rounds_to_grosze(self):
        # 100.555 * 1 = 100.555 → rounds to 100.56 (ROUND_HALF_UP)
        assert _pln(Decimal("100.555"), Decimal("1")) == Decimal("100.56")

    def test_small_amount(self):
        assert _pln(Decimal("0.01"), Decimal("4.0")) == Decimal("0.04")


class TestFifoBuySell:
    """Core FIFO buy/sell operations."""

    def test_simple_buy_sell_profit(self):
        fifo = FifoEngine()
        fifo.buy(
            "A", "STOCK", date(2020, 1, 1), Decimal("10"), Decimal("100"), "USD", Decimal("5"), Decimal("4.0")
        )

        event = fifo.sell(
            "A",
            "STOCK",
            date(2020, 6, 1),
            Decimal("-10"),
            Decimal("120"),
            "USD",
            Decimal("5"),
            Decimal("4.0"),
        )

        # Income: 10 * 120 * 4.0 = 4800.00
        assert event.income_pln == Decimal("4800.00")
        # Cost: buy cost (10 * 100 * 4.0 = 4000) + buy commission (10 * 0.5 * 4.0 = 20)
        #        + sell commission (5 * 4.0 = 20) = 4040.00
        assert event.cost_pln == Decimal("4040.00")
        assert event.event_type == "sell"

    def test_simple_buy_sell_loss(self):
        fifo = FifoEngine()
        fifo.buy(
            "A", "STOCK", date(2020, 1, 1), Decimal("10"), Decimal("100"), "USD", Decimal("0"), Decimal("4.0")
        )

        event = fifo.sell(
            "A", "STOCK", date(2020, 6, 1), Decimal("-10"), Decimal("80"), "USD", Decimal("0"), Decimal("4.0")
        )

        assert event.income_pln == Decimal("3200.00")
        assert event.cost_pln == Decimal("4000.00")

    def test_fifo_order_respected(self):
        """First lot bought is first lot sold."""
        fifo = FifoEngine()
        # Buy 10 @ 100
        fifo.buy(
            "A", "STOCK", date(2020, 1, 1), Decimal("10"), Decimal("100"), "USD", Decimal("0"), Decimal("4.0")
        )
        # Buy 10 @ 200
        fifo.buy(
            "A", "STOCK", date(2020, 2, 1), Decimal("10"), Decimal("200"), "USD", Decimal("0"), Decimal("4.0")
        )

        # Sell 10 — should consume first lot @ 100
        event = fifo.sell(
            "A",
            "STOCK",
            date(2020, 6, 1),
            Decimal("-10"),
            Decimal("150"),
            "USD",
            Decimal("0"),
            Decimal("4.0"),
        )

        # Cost should be 10 * 100 * 4.0 = 4000 (first lot)
        assert event.cost_pln == Decimal("4000.00")
        # Income: 10 * 150 * 4.0 = 6000
        assert event.income_pln == Decimal("6000.00")

    def test_partial_lot_consumption(self):
        """Selling less than a full lot leaves remainder."""
        fifo = FifoEngine()
        fifo.buy(
            "A", "STOCK", date(2020, 1, 1), Decimal("100"), Decimal("10"), "USD", Decimal("0"), Decimal("4.0")
        )

        # Sell only 30
        event = fifo.sell(
            "A", "STOCK", date(2020, 6, 1), Decimal("-30"), Decimal("15"), "USD", Decimal("0"), Decimal("4.0")
        )

        assert event.income_pln == Decimal("1800.00")  # 30 * 15 * 4
        assert event.cost_pln == Decimal("1200.00")  # 30 * 10 * 4

        # 70 should remain
        positions = fifo.get_positions()
        total_qty = sum(lot.quantity for lot in positions[("A", "STOCK")])
        assert total_qty == Decimal("70")

    def test_multi_lot_sell(self):
        """Sell spans two lots."""
        fifo = FifoEngine()
        fifo.buy(
            "A", "STOCK", date(2020, 1, 1), Decimal("5"), Decimal("10"), "USD", Decimal("0"), Decimal("4.0")
        )
        fifo.buy(
            "A", "STOCK", date(2020, 2, 1), Decimal("5"), Decimal("20"), "USD", Decimal("0"), Decimal("4.0")
        )

        # Sell 8 — consumes all 5 of first lot + 3 of second
        event = fifo.sell(
            "A", "STOCK", date(2020, 6, 1), Decimal("-8"), Decimal("30"), "USD", Decimal("0"), Decimal("4.0")
        )

        # Cost: 5*10*4 + 3*20*4 = 200 + 240 = 440
        assert event.cost_pln == Decimal("440.00")
        # Income: 8*30*4 = 960
        assert event.income_pln == Decimal("960.00")

    def test_different_nbp_rates_per_lot(self):
        """Each lot uses its own NBP rate from buy date."""
        fifo = FifoEngine()
        fifo.buy(
            "A", "STOCK", date(2020, 1, 1), Decimal("10"), Decimal("100"), "USD", Decimal("0"), Decimal("3.8")
        )
        fifo.buy(
            "A", "STOCK", date(2020, 6, 1), Decimal("10"), Decimal("100"), "USD", Decimal("0"), Decimal("4.2")
        )

        # Sell all 20 at sell rate 4.0
        event = fifo.sell(
            "A",
            "STOCK",
            date(2020, 12, 1),
            Decimal("-20"),
            Decimal("100"),
            "USD",
            Decimal("0"),
            Decimal("4.0"),
        )

        # Income: 20 * 100 * 4.0 = 8000
        assert event.income_pln == Decimal("8000.00")
        # Cost: 10*100*3.8 + 10*100*4.2 = 3800 + 4200 = 8000
        assert event.cost_pln == Decimal("8000.00")

    def test_separate_accounts(self):
        """FIFO queues are per (account, symbol)."""
        fifo = FifoEngine()
        fifo.buy(
            "A", "STOCK", date(2020, 1, 1), Decimal("10"), Decimal("100"), "USD", Decimal("0"), Decimal("4.0")
        )
        fifo.buy(
            "B", "STOCK", date(2020, 1, 1), Decimal("10"), Decimal("200"), "USD", Decimal("0"), Decimal("4.0")
        )

        # Sell from account A
        event = fifo.sell(
            "A",
            "STOCK",
            date(2020, 6, 1),
            Decimal("-10"),
            Decimal("150"),
            "USD",
            Decimal("0"),
            Decimal("4.0"),
        )

        # Should use A's lot @ 100, not B's @ 200
        assert event.cost_pln == Decimal("4000.00")

    def test_separate_symbols(self):
        """FIFO queues are per (account, symbol)."""
        fifo = FifoEngine()
        fifo.buy(
            "A", "X", date(2020, 1, 1), Decimal("10"), Decimal("100"), "USD", Decimal("0"), Decimal("4.0")
        )
        fifo.buy(
            "A", "Y", date(2020, 1, 1), Decimal("10"), Decimal("200"), "USD", Decimal("0"), Decimal("4.0")
        )

        event = fifo.sell(
            "A", "X", date(2020, 6, 1), Decimal("-10"), Decimal("150"), "USD", Decimal("0"), Decimal("4.0")
        )

        assert event.cost_pln == Decimal("4000.00")


class TestFifoUnderflow:
    def test_sell_more_than_owned_raises(self):
        fifo = FifoEngine()
        fifo.buy(
            "A", "STOCK", date(2020, 1, 1), Decimal("5"), Decimal("100"), "USD", Decimal("0"), Decimal("4.0")
        )

        with pytest.raises(ValueError, match="FIFO underflow"):
            fifo.sell(
                "A",
                "STOCK",
                date(2020, 6, 1),
                Decimal("-10"),
                Decimal("100"),
                "USD",
                Decimal("0"),
                Decimal("4.0"),
            )

    def test_sell_empty_queue_raises(self):
        fifo = FifoEngine()
        with pytest.raises(ValueError, match="FIFO underflow"):
            fifo.sell(
                "A",
                "STOCK",
                date(2020, 6, 1),
                Decimal("-1"),
                Decimal("100"),
                "USD",
                Decimal("0"),
                Decimal("4.0"),
            )


class TestStockSplit:
    def test_2_for_1_split(self):
        fifo = FifoEngine()
        fifo.buy(
            "A", "XLE", date(2020, 1, 1), Decimal("12"), Decimal("50"), "USD", Decimal("12"), Decimal("4.0")
        )

        fifo.apply_split("A", "XLE", new_for_old=2, old_shares=1)

        positions = fifo.get_positions()
        lots = positions[("A", "XLE")]
        total_qty = sum(lot.quantity for lot in lots)
        assert total_qty == Decimal("24")

        # Price halved
        assert lots[0].price_per_unit == Decimal("25")
        # Commission per unit halved
        assert lots[0].commission_per_unit == Decimal("0.5")

    def test_split_preserves_total_cost(self):
        fifo = FifoEngine()
        fifo.buy(
            "A", "XLE", date(2020, 1, 1), Decimal("12"), Decimal("50"), "USD", Decimal("12"), Decimal("4.0")
        )

        cost_before = sum(lot.total_cost for lot in fifo.get_positions()[("A", "XLE")])
        fifo.apply_split("A", "XLE", new_for_old=2, old_shares=1)
        cost_after = sum(lot.total_cost for lot in fifo.get_positions()[("A", "XLE")])

        assert cost_before == cost_after

    def test_parse_split_ratio(self):
        new, old = FifoEngine.parse_split_ratio("Stock split 2 for 1")
        assert new == 2
        assert old == 1

    def test_parse_reverse_split_ratio(self):
        new, old = FifoEngine.parse_split_ratio("Stock Split 1 for 3")
        assert new == 1
        assert old == 3

    def test_parse_split_invalid_raises(self):
        with pytest.raises(ValueError, match="Cannot parse"):
            FifoEngine.parse_split_ratio("no ratio here")


class TestShortPosition:
    def test_sell_short_then_buy_to_close(self):
        fifo = FifoEngine()
        fifo.sell_short(
            "A", "FX", date(2020, 1, 1), Decimal("100"), Decimal("1.10"), "USD", Decimal("2"), Decimal("4.0")
        )

        assert fifo.has_short_position("A", "FX") is True

        event = fifo.buy_to_close(
            "A", "FX", date(2020, 2, 1), Decimal("100"), Decimal("1.08"), "USD", Decimal("2"), Decimal("4.0")
        )

        # Income from short sale: 100 * 1.10 * 4.0 = 440
        assert event.income_pln == Decimal("440.00")
        # Cost of closing: 100 * 1.08 * 4.0 + 2 * 4.0 = 432 + 8 = 440
        assert event.cost_pln == Decimal("440.00")

    def test_short_underflow_raises(self):
        fifo = FifoEngine()
        fifo.sell_short(
            "A", "FX", date(2020, 1, 1), Decimal("5"), Decimal("1.10"), "USD", Decimal("0"), Decimal("4.0")
        )

        with pytest.raises(ValueError, match="Short FIFO underflow"):
            fifo.buy_to_close(
                "A",
                "FX",
                date(2020, 2, 1),
                Decimal("10"),
                Decimal("1.08"),
                "USD",
                Decimal("0"),
                Decimal("4.0"),
            )


class TestReverseSplit:
    def test_reverse_split_1_for_3(self):
        fifo = FifoEngine()
        # Buy 5 shares @ 9.9
        fifo.buy(
            "A", "REMX", date(2020, 1, 1), Decimal("5"), Decimal("9.9"), "USD", Decimal("0"), Decimal("4.0")
        )

        events = fifo.apply_reverse_split(
            account_id="A",
            symbol="REMX",
            reverse_date=date(2020, 4, 15),
            old_quantity=Decimal("5"),
            new_quantity=Decimal("1"),
            fractional_cash=Decimal("19.89"),
            currency="USD",
            nbp_rate=Decimal("4.15"),
        )

        # Should have 1 share remaining
        positions = fifo.get_positions()
        total_qty = sum(lot.quantity for lot in positions[("A", "REMX")])
        assert total_qty == Decimal("1")

        # Fractional cash event
        assert len(events) == 1
        assert events[0].event_type == "fractional_cash"
        assert events[0].income_original == Decimal("19.89")
        assert events[0].cost_original == Decimal("0")  # zero cost per Exante convention

    def test_reverse_split_no_fractional(self):
        fifo = FifoEngine()
        fifo.buy(
            "A", "TEST", date(2020, 1, 1), Decimal("6"), Decimal("10"), "USD", Decimal("0"), Decimal("4.0")
        )

        events = fifo.apply_reverse_split(
            account_id="A",
            symbol="TEST",
            reverse_date=date(2020, 4, 15),
            old_quantity=Decimal("6"),
            new_quantity=Decimal("2"),
            fractional_cash=None,
            currency="USD",
            nbp_rate=Decimal("4.0"),
        )

        assert len(events) == 0
        positions = fifo.get_positions()
        total_qty = sum(lot.quantity for lot in positions[("A", "TEST")])
        assert total_qty == Decimal("2")
