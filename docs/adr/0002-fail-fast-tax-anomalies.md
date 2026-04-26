# ADR-0002: Fail-fast > silent warning dla anomalii podatkowych

**Status:** Accepted
**Date:** 2026-04-26

## Context

Kalkulator pit-exante to single-user tool uruchamiany ~raz/rok przed
złożeniem PIT. Gdy w danych pojawia się anomalia — np. refund WHT większy
niż oryginalny WHT, dywidenda na CFD, dywidenda PLN z nierozpoznanej
giełdy — istnieją trzy możliwe reakcje:

1. Cicho pominąć (continue) — tradycyjne dla parserów
2. Logger.warning — kontynuować z ostrzeżeniem w stderr
3. raise ValueError — zatrzymać kalkulator do podjęcia decyzji

Pre-2026-04 kod używał miks (1) i (2). Konsekwencja: kalkulator producował
"prawie poprawny" PIT w przypadku anomalii, a użytkownik składał błędny PIT
nie zauważając warning'u.

## Decision

**Wszystkie anomalie podatkowe → `raise ValueError` z czytelnym hintem co
sprawdzić i jak zdecydować dalej.** Aktualnie 5 guardów:

| ID | Anomaly | Lokalizacja |
|---|---|---|
| H4 | Over-refund: refund > original WHT | `calculator.py` (refund→parent merge) |
| H8 | DIVIDEND PLN bez rozpoznanego kraju | `calculator.py` (DIVIDEND case) |
| H2 | Refund cross-year (parent.year < refund.year) | `calculator.py` (refund→parent merge) |
| — | Dywidenda na CFD/derivative | `calculator.py` (post-loop kind classification) |
| — | Unknown instrument bez metadata/override | `calculator.py` (post-loop kind classification) |

Każdy `ValueError` zawiera:
- Identyfikator (uuid, symbol, data)
- Wyjaśnienie *dlaczego* to jest problem
- Konkretny next step (dodaj do `symbol_overrides.json`, skonsultuj doradcę,
  zbadaj dane Exante)

## Considered alternatives

- **logger.warning + continue** — status quo dla CFD-dyw (linia 730)
  i unknown-instrument (727-728). Odrzucone: warningi w stderr są
  niewidoczne dla użytkownika składającego PIT przez UI.
- **Manual verification raport** — kalkulator generuje listę "issues to
  review" zamiast zatrzymywać się. Odrzucone: dodatkowy etap procesu,
  i tak wymaga zatrzymania, mniej przejrzysty.
- **Per-anomaly konfiguracja (allow/warn/error)** — feature flag.
  Odrzucone: over-engineering dla single-user tool, default=error i
  tak byłby właściwy.

## Empirical evidence

- 2024-04-26 audyt zidentyfikował 5 cichych anomaly paths które potencjalnie
  generują błędny PIT
- W obecnych danych Wojtka żaden z 5 guardów nie triggeruje (verified po
  Batch #3) — guardy są forward-defensive
- Zasada: "single-user tool, raz/rok, cichy bug = błędny PIT" (memory
  `feedback_legal_certainty.md`)

## Consequences

- Gdy w przyszłości pojawi się anomalia, kalkulator zatrzyma się i wymusi
  decyzję — zamiast cicho generować zły PIT
- Niektóre guardy (H8 PLN-unknown, CFD-dyw) wymagają od użytkownika
  modyfikacji `symbol_overrides.json` lub `country.py` zanim kalkulator
  zacznie znowu działać — to feature, nie bug
- ADR-0006 (cross-year refund) opisuje konkretny case H2 z opcjami A/B
  do podjęcia decyzji
