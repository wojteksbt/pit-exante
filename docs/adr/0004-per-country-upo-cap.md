# ADR-0004: Per-country UPO cap z tolerance 0.1pp

**Status:** Accepted
**Date:** 2026-04-26

## Context

Art. 30a ust. 9 ustawy o PIT pozwala odliczyć od polskiego podatku
zagraniczny WHT, ale "do wysokości podatku obliczonego od tych przychodów"
— czyli max stawka UPO × dochód. Implementacja tego "do wysokości" wymaga
trzech decyzji:

1. **Granularność cap** — per-event (każda dywidenda osobno), per-country
   (suma dywidend z USA limitowana do 15% sumy dochodów z USA), czy
   aggregate (suma WHT limitowana do 15% sumy wszystkich zagranicznych
   dywidend)?
2. **Tolerancja stawki** — gdy efektywna stawka WHT (oryg.) wynosi
   15.001% przez quantize (np. 100.00 USD div, WHT 15.0001 USD), czy
   to jest "WHT pobrany na poziomie UPO" czy "WHT > UPO, cap apply'uje"?
3. **Stawki UPO dla nieznanych krajów** — fallback?

Dla (1) NSA II FSK 1171/22 (28.02.2023) interpretuje per-country.
Dla (2) ustawa milczy. Dla (3) fallback brak.

## Decision

- **Per-country cap** — `dividends_by_country` w `YearReport` agreguje
  dywidendy per ISO country code, cap apply'owany per-country.
  Zaimplementowane w `calculator._aggregate_by_year` (commit `858f14f`).
- **Tolerance 0.1pp** — gdy efektywna stawka WHT ≤ UPO + 0.1 percentage
  points, traktujemy jako "WHT pobrany na poziomie UPO" i odliczamy pełen
  WHT. Powyżej tej tolerancji apply'ujemy cap (UPO × dochód).
- **UPO dla nieznanych krajów = TAX_RATE (19% PL)** — fallback, ale w
  połączeniu z H8 fail-fast (ADR-0002) — DIVIDEND z nieznanego kraju
  raise'uje ValueError przed cap calculation, więc fallback rzadko triggeruje.

Per-country UPO rates (`country._COUNTRY_UPO_RATE`):
- US: 15% (UPO PL-USA art. 11, Dz.U. 1976 nr 31 poz. 178)
- CA: 15% (UPO PL-Kanada art. 10, Dz.U. 2013 poz. 1371)
- SE: 15% (UPO PL-Szwecja art. 10)

## Considered alternatives

- **Per-event cap** — każda dywidenda osobno. Odrzucone: niezgodne z
  NSA II FSK 1171/22 i PitFx convention.
- **Aggregate cap** — suma WHT limitowana do 19% × suma wszystkich
  dochodów. Odrzucone: zbyt liberalny, nie matches PitFx.
- **Tolerance 0.0pp** — strict 15% boundary. Odrzucone: quantize artifacts
  generują false positive cap apply (tax_to_deduct PLN różny od WHT
  o 1-2 grosze).
- **Tolerance 0.3pp (PitFx prawdopodobnie używa tego)** — Odrzucone:
  po ceil do PLN różnica zerowa we wszystkich latach Wojtka 2020-2024,
  więc 0.1pp też jest correct dla bieżących danych. 0.1pp daje mniejszy
  margines błędu dla future cases.

## Empirical evidence

- **NSA II FSK 1171/22 (28.02.2023)** — interpretacja per-country
- **PitFx PDFs 2020-2024** — per-country breakdown w sekcjach G
- **Bieżący baseline (po `858f14f`):**
  - 2024 `dividends_tax_to_deduct_pln` = 134.11 vs PitFx 134.10
    (1 grosz quantize, identyczne po ceil)
  - Lata 2020-2023: 0-grosz różnica vs PitFx
  - 2025-2026: zgodne z calculate'em (PitFx jeszcze niedostępne)

## Consequences

- Implementacja cap w `_aggregate_by_year` (calculator.py)
- Per-country `CountryDividend` dataclass w `models.py` z `tax_paid_pln`,
  `tax_due_pln`, `tax_to_deduct_pln`, `effective_wht_rate`
- Report `dividends_by_country` w PIT-38 sekcji G — per-country breakdown
- Gdy w przyszłości Wojtek doda inwestycje w EU/UK/CH, trzeba będzie
  rozszerzyć `_COUNTRY_UPO_RATE` (i `_EXCHANGE_COUNTRY` w `country.py`).
  H8 fail-fast wtedy zatrzyma kalkulator do dodania konkretnego kraju.
  Backlog: H6 w `pit-exante-backlog-2026-04-26.md`
