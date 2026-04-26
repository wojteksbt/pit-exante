# ADR-0001: PitFx jako empirical reference

**Status:** Accepted
**Date:** 2026-04-26

## Context

Polskie regulacje PIT dla zagranicznych dywidend i transakcji kapitałowych
(art. 30a, art. 30b, art. 11a) są często niejednoznaczne. Konkretne pytania
gdzie ustawa milczy lub interpretacje są rozbieżne:

- Czy DIV/TAX rozliczamy po dacie timestamp (execution) czy valueDate (payment)?
- Jaki kurs NBP użyć dla refundu cross-date — z dnia refundu czy z dnia
  oryginalnej dywidendy?
- Czy cap WHT z UPO (art. 30a ust. 9) liczyć per-event, per-country,
  czy aggregate?
- Jak rozliczać fractional cash z reverse splitu — koszt 0 czy proporcjonalny?

Dla każdego z tych pytań egzegeza ustawy daje 2-3 sensownych interpretacji.
Bez empirycznego punktu odniesienia, kalkulator drift'uje wraz z
interpretacjami i każdy reviewer dostaje inne wyniki.

## Decision

**PitFx (firma rozliczająca Exante) jest empirycznym referencyjnym punktem**
dla niejednoznacznych konwencji rozliczeniowych. Gdy interpretacja ustawy i
PitFx convention się rozjeżdżają, default = PitFx (chyba że PitFx ewidentnie
narusza ustawę — wtedy ADR z uzasadnieniem).

PitFx PDF (rozliczenia roczne 2020, 2022, 2023, 2024) służą jako
"oracle" — porównujemy nasze sumy z ich sumami; różnica > 1 grosz
po ceil do PLN = bug do zbadania.

## Considered alternatives

- **Pure egzegeza ustawy** — wybierana interpretacja literalna art. X.
  Odrzucone: każda interpretacja jest defensible, więc nie ma jednego
  "literalnego" wyniku, a każda ma inne sumy.
- **NSA + KIS jako reference** — wyroki sądowe jako ground truth.
  Problem: dla większości punktów nie ma wyroków bezpośrednio na temat;
  KIS interpretacje są często sprzeczne między sobą.
- **Inna firma rozliczeniowa** — np. inny biegły lub bot. Odrzucone:
  PitFx ma policzone konkretne lata Wojtka, więc można 1:1 porównać sumy.

## Empirical evidence

- `~/Documents/Finanse/PIT 2020/Exante/Trade2020.pdf` — capital gains
  per-event, REMX 2020 reverse split z fractional cash
- `~/Documents/Finanse/PIT 2020/Exante/Dywidendy.pdf` — 2020 dywidendy
  z per-country breakdown
- `~/Documents/Finanse/PIT 2022/Rozliczenie Exante.pdf` — NGE.ARCA
  case 2022-12-29 timestamp / 2023-01-03 valueDate booked into PIT-2022
- `~/Documents/Finanse/PIT 2023/Rozliczenie Exante.pdf`
- `~/Documents/Finanse/PIT 2024/Rozliczenie Exante.pdf`

Baseline 2024: nasz `dividends_tax_to_deduct_pln = 134.11` vs PitFx 134.10
= 1 grosz quantize artifact, identyczne po ceil do całych PLN.

## Consequences

- Pojedyncze ADR-y (0003, 0004, 0005) odwołują się do PitFx PDFs jako
  empirical anchor zamiast do ustawy.
- Gdy w przyszłości pojawi się PitFx PDF dla nowego roku z rozbieżnością
  > 1 PLN, traktujemy to jako bug do zbadania w kalkulatorze (nie jako
  PitFx error).
- Memory: `~/.claude/projects/-Users-Wojtek-projects-pit-exante/memory/feedback_pitfx_reference.md`
  zapisuje tę meta-policy dla przyszłych sesji.
