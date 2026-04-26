# ADR-0005: Cost=0 dla fractional cash z reverse splitu

**Status:** Accepted
**Date:** 2026-04-26

## Context

Reverse split z fractional cash payment generuje gotówkę za niemożliwą
do utrzymania ułamkową ilość akcji. Przykład: REMX 2020 reverse split
1 for 3 — użytkownik miał 100 akcji, dostał 33 nowe akcje + ~$X cash
za 0.33 ułamka.

Pytanie: jak rozliczyć ten cash?

1. **Cost=0** — całość fractional cash to przychód PIT-38 (sekcja C
   wiersz 1), 0 kosztu
2. **Proportional cost** — koszt nabycia z FIFO podzielony na
   "zachowane akcje" + "fractional cash" według proporcji 0.33/100,
   zatem fractional cash ma proportional cost basis
3. **Allocate to retained shares** — fractional cash jest "phantom",
   cały koszt zostaje przy nowych akcjach, fractional cash to czysty
   profit z 0 cost

(2) jest najbardziej "literalny" art. 30b — koszt to to co
zapłacono za to co teraz sprzedajemy. (1) i (3) są praktyczne ale
mniej rigorous.

## Decision

**Cost=0** dla fractional cash. Zaimplementowane w `fifo.apply_reverse_split`
(linia ~287-302):

```python
if fractional_cash and fractional_cash > 0:
    fractional_income_pln = to_pln(fractional_cash, nbp_rate)
    events.append(TaxEvent(
        ...
        income_original=fractional_cash,
        cost_original=Decimal("0"),
        ...
    ))
```

## Considered alternatives

- **Proportional cost** — pure literal art. 30b. Odrzucone:
  niezgodne z PitFx PDF 2020 Trade2020.pdf (REMX case księguje
  fractional cash z cost=0).
- **Allocate to retained shares** — efektywnie cost basis adjustment
  na nowych akcjach. Odrzucone: skomplikowane bookeeping w FIFO,
  i niezgodne z PitFx.
- **Skip fractional cash** — nie rozliczać. Odrzucone: to jest
  realny przychód PIT-38, nie można pominąć.

## Empirical evidence

- **PitFx PDF 2020 Trade2020.pdf** — REMX 2020 reverse split case
  zaksięgowany jako 1 event z `income = fractional cash`, `cost = 0`.
  Dokument służy jako empirical anchor.
- **W obecnych danych Wojtka** REMX 2020 jest jedynym reverse splitem
  z fractional cash. Backlog M3 dla future reverse splits z bardziej
  skomplikowanym cost basis.

## Consequences

- Komentarz w `fifo.py:287` referuje PitFx Trade2020.pdf jako empirical
  anchor (Batch #4)
- Test `tests/test_fifo.py::TestApplyReverseSplit` covers fractional
  cash scenario
- Dla future reverse splits gdzie cost basis matters (M3 w backlogu),
  ten ADR będzie potencjalnie superseded
