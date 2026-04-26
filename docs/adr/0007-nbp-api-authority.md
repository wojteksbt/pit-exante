# ADR-0007: NBP API jako autorytet (drop kalendarz świąt)

**Status:** Accepted
**Date:** 2026-04-26

## Context

Art. 11a ust. 1-2 ustawy o PIT wymaga "średniego kursu NBP z ostatniego
dnia roboczego poprzedzającego dzień transakcji". "Dzień roboczy" w
kontekście NBP = dzień gdy NBP publikuje tabelę A (poniedziałek-piątek
za wyjątkiem polskich świąt państwowych).

Pre-2026-04 implementacja używała hand-maintained kalendarza polskich
świąt:
- `_polish_holidays(year)` — set świąt stałych + ruchomych (Wielkanoc,
  Boże Ciało liczone Anonymous Gregorian algorithm)
- `_is_business_day(d)` — sprawdzenie czy dzień jest roboczy
- `_previous_business_day(d)` — znajdź poprzedzający dzień roboczy

Problem 1: w 2025 Sejm uchwalił że 24.12 jest świętem (od 2025).
Hand-maintained kalendarz wymagał update'u — nie było automatycznego
sygnału że kod jest stale.

Problem 2: każda przyszła zmiana ustawy o świętach wymaga pull request'u
do projektu.

## Decision

**NBP API jest autorytetem dla "dnia roboczego".** Drop kalendarz świąt
(`_easter`, `_polish_holidays`, `_is_business_day`, `_previous_business_day`,
`_holidays_cache`).

Nowa logika `get_rate(currency, transaction_date)`:
1. Jeśli currency == "PLN" → zwróć 1
2. Validacja currency vs `_VALID_NBP_CURRENCIES` (USD, EUR, CAD, SEK)
3. Sprawdź pre-2002 archive guard
4. Iteracyjnie: spróbuj `transaction_date - 1`, jeśli 404 (NBP nie
   publikował tego dnia) → dekrementuj o 1 dzień, retry max 7 dni
5. Cache key = "transaction_date - 1 calendar day" (deterministyczny,
   future runs hit cache immediately)

`_fetch_from_api` validuje response (effectiveDate + currency code matchują
request) — defensywa przed ewentualną zmianą NBP API zwracającą "nearest
available" zamiast 404.

## Considered alternatives

- **Status quo + auto-update kalendarza** — pobierać polskie święta z
  zewnętrznego API. Odrzucone: dodanie network dependency, gorsze niż
  używać NBP API bezpośrednio.
- **Drop kalendarz, ale bez 7-dniowego retry** — uznać że > 3 dni
  zamknięty znaczy bug. Odrzucone: defensywa przeciwko hipotetycznym
  6+-dniowym sekwencjom (Wielkanoc + dodatkowe świata), 7 dni daje margines.
- **`BARE_CURRENCIES` import z `models.py`** — reuse istniejącej stałej.
  Odrzucone: `BARE_CURRENCIES` zawiera PLN i jest semantycznie inną
  listą (waluty asset/cash leg, nie waluty dla NBP API). Lokalne
  `_VALID_NBP_CURRENCIES = {USD, EUR, CAD, SEK}` jest jaśniejsze
  i mniej coupling z parsem.

## Empirical evidence

- **2025 rok 24.12** — pre-L5 kod cache'ował rate dla 2025-12-24 (klucz
  `USD_2025-12-24`) bo old kalendarz nie miał Wigilii jako święto.
  Po L5: NBP 404 dla 24.12, retry do 23.12, cache key 23.12.
- **Test `test_holiday_fallback_walks_back`** — symuluje BN+Wigilia
  weekend, weryfikuje 3 retry (25, 24, 23) i sukces na 23.
- **Baseline 2020-2026** — zachowany po refactorze (verified).
  Cache file niezmieniony po pierwszym uruchomieniu (legacy entries
  prevailing przez step-key fallback).

## Consequences

- 22 testy z `test_nbp.py` (TestEaster, TestPolishHolidays,
  TestIsBusinessDay, TestPreviousBusinessDay) usunięte
- 6 nowych testów w `TestL5Refactor` (currency validation, pre-archive,
  Wigilia fallback, max-fallback, frozenset contract)
- W przyszłości gdy Sejm uchwali kolejne święto, kalkulator nie wymaga
  zmian — NBP API automatycznie zwróci 404 i logika retry zadziała
- Cache key migration: legacy entries pod starymi kluczami (np.
  `USD_2024-04-05` jako lookup_date dla transakcji Mon 2024-04-08)
  są podpinane przez step-key fallback przy pierwszym lookup'ie i
  back-fill'owane do nowych kluczy (`USD_2024-04-07`). Old entries
  pozostają nietknięte.
