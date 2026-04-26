# ADR-0003: Year boundary — timestamp dla DIV/TAX, exec_date/value_date dla SELL/BUY

**Status:** Accepted
**Date:** 2026-04-26

## Context

Każda transakcja Exante ma trzy pola dat:

- `timestamp` — moment zapisania w systemie (execution recording)
- `valueDate` — data rozliczenia gotówkowego (T+2 dla US stocks)
- COMMISSION `valueDate` — execution date (T+0)

Polski PIT wymaga przypisania transakcji do roku podatkowego i znalezienia
kursu NBP "z dnia poprzedzającego transakcję". Pytanie: która z trzech
dat jest "datą transakcji"?

Art. 11a ust. 1-2 ustawy o PIT mówi o "ostatnim dniu roboczym poprzedzającym
dzień uzyskania przychodu/poniesienia kosztu". Dla dywidend KIS interpretacje
są niejednoznaczne: niektóre mówią o dniu wypłaty (valueDate), inne o dniu
postawienia do dyspozycji (timestamp).

## Decision

Status quo (już zaimplementowane przed ADR):

- **DIVIDEND, TAX, US TAX** → `_timestamp_date(t)` (timestamp w strefie polskiej CET)
- **TRADE (sell, buy)** → `value_date` (valueDate, T+2 settlement)
- **COMMISSION** → `value_date` (T+0 execution date) jako proxy dla execution
- **STOCK SPLIT, CORPORATE ACTION** → `_effective_date()` (valueDate jeśli set,
  inaczej timestamp)

Ta konwencja jest *świadomym wyborem* zgodnym z PitFx, nie default'em
inherytowanym z parsera.

## Considered alternatives

- **Wszystko valueDate** — najbardziej "literal" reading art. 11a
  (data otrzymania środków). Odrzucone: PitFx 2022 NGE.ARCA case
  (timestamp 2022-12-29, valueDate 2023-01-03) został zaksięgowany
  w PIT-2022, nie PIT-2023. Switching to valueDate spowodowałby
  niezgodność z PitFx.
- **Wszystko timestamp** — execution date dla wszystkiego.
  Odrzucone: dla TRADE klasyczna konwencja "data transakcji" w PIT
  to settlement date, i PitFx tak liczy.
- **Per-transaction-type configuration** — pozwolić użytkownikowi
  konfigurować. Over-engineering.

## Empirical evidence

- **PitFx PDF 2022 (Rozliczenie Exante 2022.pdf)** — NGE.ARCA dividend
  z timestamp 2022-12-29 23:55:xx (CET), valueDate 2023-01-03,
  zaksięgowane w PIT-2022 sekcja G. Test empiryczny — przełączyliśmy
  do valueDate i nasz wynik dla 2022 rozjechał się z PitFx o tę dywidendę
  (podlegała wtedy PIT-2023).
- **PitFx PDFs 2020-2024** — wszystkie inwoke timestamp dla DIV/TAX
  i valueDate dla TRADE. Nasza implementacja matches all 5 lat.

## Consequences

- Komentarz w `calculator.py:_timestamp_date` referuje NGE.ARCA case jako
  empirical anchor (Batch #4)
- Użytkownik z transakcjami stycznia/grudnia powinien mieć świadomość
  że timestamp/valueDate mogą rozjeżdżać się o dni i kalkulator
  preferuje timestamp dla dywidend
- Jeśli kiedyś PitFx zmieni konwencję (lub KIS wyda interpretację
  general'ną dla valueDate), ADR będzie superseded i komentarz w kodzie
  zaktualizowany

## Related

- ADR-0001: PitFx jako empirical reference (meta-policy uzasadniający tę decyzję)
- Memory: `feedback_pitfx_reference.md` (ścieżki do PDFs)
