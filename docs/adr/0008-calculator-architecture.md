# ADR-0008: Architektura calculator.py — match/case + indeksy + helpers

**Status:** Accepted
**Date:** 2026-04-27

## Context

`calculator.py` jest główną orkiestracją: `parse → enrich → classify →
process per-transaction → aggregate per-year → kind classification`.
Centralna funkcja `calculate(transactions_path)` przed dekompozycją z
2026-04-27 miała ~540 LOC z głębokim `match/case` na `TaxCategory`,
gdzie sam case `TAX_WITHHELD` zajmował 160 LOC i miał 5+ poziomów
zagnieżdżonych warunków.

Pojawiły się trzy pytania architektoniczne:
1. Dlaczego `match/case` zamiast strategy pattern / dispatch table?
2. Po co cztery overlapping dividend indeksy (`dividend_by_uuid`,
   `dividend_by_symbol_date`, `dividend_txns_by_symbol`,
   `tax_to_dividend_map`)?
3. Czy `calculate()` powinno zostać monolitem?

Bez ADR ktoś (autor za pół roku, przyszły maintainer) mógłby usunąć
indeksy nieświadomy ich roli, albo "uprościć" `match/case` przez
naiwny dispatch table tracąc niezbędne ordering / fallback semantics.

## Decision

### 1. `match/case` jako struktura dispatch

Zachowujemy `match/case` na `TaxCategory` zamiast strategy pattern /
registry decoratora. Powody:

- **Wszystkie kategorie są znane statycznie** — nie ma plug-in modelu,
  nie ma extensibility wymaganej przez external plugins. Strategy
  pattern dodaje overhead bez korzyści.
- **Czytelność audytowa** — jedno miejsce z jawnym wyliczeniem
  wszystkich kategorii. Audytor podatkowy / ja-za-pół-roku widzi
  exhaustiveness w jednym pliku.
- **Type-checker friendly** — `match` z `case TaxCategory.X:`
  pozwala mypy wykryć brakujące kategorie po dodaniu nowej.
- **Performance jest second order** — koszty są w I/O (NBP) i FIFO
  scan, nie w dispatch.

### 2. Cztery dividend indeksy — różne use case'y

Każdy z indeksów obsługuje **inną strategię łączenia** TAX → DIVIDEND:

- `dividend_by_uuid: dict[str, DividendEvent]` — direct link via
  `parent_uuid` (Exante własny relacjny pointer). Najszybszy lookup,
  używany pierwszy.
- `dividend_by_symbol_date: dict[(symbol, iso_date), [DividendEvent]]`
  — fallback dla US TAX, gdzie `parent_uuid` nie istnieje, ale
  symbol+data są w komentarzu. Lookup `O(1)` po kluczu.
- `dividend_txns_by_symbol: dict[symbol, [(timestamp, DividendEvent)]]`
  — używany przez `_match_tax_by_timestamp` do dopasowania w oknie
  czasowym ±60s (lub ±120s dla US TAX). Zachowuje timestamp w ms
  do prefekstycji najbliższego eventu.
- `tax_to_dividend_map: dict[tax_uuid, DividendEvent]` — chain
  following. Gdy TAX jest rolled-back przez kolejny TAX o
  `parent_uuid = previous_tax.uuid`, drugi TAX musi znaleźć **ten
  sam** dividend, do którego linkował pierwszy. Bez tego refundy
  nie merge'ują się poprawnie.

Te role nie są substytutywalne. Unifikacja w jeden index zmusiłaby do
trzymania wszystkich krotek (uuid, symbol, date, timestamp, parent_tax)
w jednym wpisie, eksplodując pamięć i komplikując lookup.

### 3. Decompozycja `calculate()` — extract dwa hot spoty

Wyciągnęliśmy do funkcji modułowych:

- `_handle_tax_withheld(t, dividend_events, dividend_by_uuid,
  dividend_by_symbol_date, dividend_txns_by_symbol,
  tax_to_dividend_map, unlinked_tax_entries) -> None`  
  Zastąpiło 160-LOC case'a; refaktor na **early returns per
  strategy** (parent_uuid direct → rollback chain → timestamp match
  → US TAX comment) zamiast głębokiego `if/elif/else`.

- `_handle_corporate_action(t, ca_txns_by_symbol, fifo, tax_events,
  processed_uuids) -> None`  
  Zastąpiło 50-LOC case'a; reverse-split orchestration (removal +
  addition + opcjonalny fractional_cash).

Pozostałe case'y (BUY, SELL, DIVIDEND, SPLIT, ROLLOVER_*, FEE, SKIP)
są krótkie (10-50 LOC) i czytelne inline — extract'owanie ich
dałoby tylko parametr-sprawl bez korzyści.

`calculate()` zmalał z ~540 do 358 LOC.

## Considered alternatives

- **Strategy pattern z registry**:
  ```python
  HANDLERS: dict[TaxCategory, Callable] = {
      TaxCategory.BUY: _handle_buy,
      TaxCategory.SELL: _handle_sell,
      ...
  }
  HANDLERS[category](t, state)
  ```
  Odrzucone: wymaga uniform signature → albo bundle wszystkich
  zmiennych w `state` object (dodatkowa abstrakcja bez zysku), albo
  parametr-sprawl per handler. `match/case` jest natywnym
  dispatchem w Pythonie 3.10+ bez tego kosztu.

- **DividendRegistry class** unifikująca cztery indeksy:
  ```python
  class DividendRegistry:
      def get_by_uuid(uuid): ...
      def get_by_symbol_date(symbol, date): ...
      def get_by_timestamp_window(symbol, ts, max_delta): ...
      def get_chained_tax_target(tax_uuid): ...
  ```
  Odrzucone na ten moment: 22 mutation site'y w `_handle_tax_withheld`
  — refactor wymagałby ~150 LOC zmian z medium-high ryzykiem regresji.
  ROI niski przy obecnej skali (dane jednego usera, kilka lat).
  Jeśli kod kiedyś urośnie o 10× (multi-user / multi-broker), warto
  wprowadzić.

- **Pełna ekstrakcja każdego case'a** (BUY/SELL/DIV/...) do helpera:
  Odrzucone: średnie case'y (BUY/SELL ~50 LOC) miały 6+ params.
  Helpers mniejsze niż 80 LOC bez własnej domain-logic to zwykle
  parameter-sprawl, nie cohesion ↑.

- **Klasa `TaxCalculator` z metodami zamiast funkcji modułowych**:
  Odrzucone: stan jest mutable i wewnętrzny dla jednego wywołania
  `calculate()`. Klasa wymagałaby `__init__` przyjmującego
  transactions_path i drugiego pass'u przez `compute()`. Funkcja
  modułowa z lokalnymi zmiennymi jest prostsza.

## Empirical evidence

- **Pre-decomposition LOC** (commit `4b81b47`): `calculate()` ~540 LOC,
  `TAX_WITHHELD` case 160 LOC, `CORPORATE_ACTION` case 50 LOC.
- **Post-decomposition LOC** (commit `62ed694`): `calculate()` 358 LOC
  (-34%), TAX_WITHHELD case 9 LOC (-94%), CORPORATE_ACTION case 3 LOC
  (-94%).
- **Test suite stability** — wszystkie 308 testów (włącznie z
  `tests/personal/test_calculator_regression.py`) przeszły bez zmian
  po dekompozycji. Output `pit_2025.txt` byte-identical przed i po.
- **Latentny bug capped-branch display** (mixed-rate countries) —
  złapany przez per-row sum guardrail invariant, naprawiony przez
  alokację per-event w calculator (vs replikację branch logic w
  report). Patrz commit `4b81b47`.

## Consequences

- Nowi maintainerzy muszą zrozumieć role każdego z 4 indeksów przed
  zmianą `_handle_tax_withheld`. Docstringi w funkcji + ten ADR
  dokumentują ten kontrakt.
- `match/case` na `TaxCategory` jest **exhaustive** — dodanie nowej
  kategorii bez nowego case'a będzie złapane przez ruff/mypy.
- Helper extraction otwiera drogę do unit-testowania pojedynczych
  case'ów w izolacji (passing in fake state dicts), gdyby zaszła
  potrzeba.
- Następny logiczny refactor (jeśli skala wzrośnie): wprowadzenie
  `DividendRegistry` jako agregat indeksów. Decyzja **odroczona**
  do momentu gdy faktycznie będzie potrzebna.
