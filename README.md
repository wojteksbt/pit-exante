# PIT Exante

Kalkulator polskiego podatku PIT dla inwestorów korzystających z brokera [Exante](https://exante.eu). Generuje dane do formularzy **PIT-38** (zyski kapitałowe, papiery wartościowe + instrumenty pochodne/CFD) oraz **PIT-36/PIT-ZG** (dywidendy zagraniczne) osobno dla każdego roku podatkowego.

## Co robi

- Pobiera całą historię transakcji z Exante (REST API) oraz metadane instrumentów
- Przelicza każdą transakcję na PLN po kursie NBP z ostatniego dnia roboczego poprzedzającego datę transakcji (art. 11a ust. 1-2 ustawy o PIT)
- Oblicza zyski/straty metodą **FIFO** osobno per instrument, z obsługą stock split, reverse split, corporate actions, rolloverów (swap overnight) i forexu
- Klasyfikuje instrumenty jako **papiery wartościowe** (akcje, ETF) vs **instrumenty pochodne** (CFD) na podstawie `symbolType` z API Exante
- Grupuje dywidendy po kraju źródła dla PIT-ZG z automatycznym rozliczeniem podatku pobranego u źródła
- Generuje raport tekstowy per rok + zbiorczy CSV

## Uwaga podatkowa

Ten program wspiera rozliczenie, ale **nie zastępuje doradztwa podatkowego**. Weryfikuj wyniki z księgową/doradcą. Autorzy nie biorą odpowiedzialności za błędne zeznania.

## Wymagania

- Python 3.10+
- Konto Exante z dostępem do API (wygeneruj w panelu: Account > API Access)
- VPN do Polski jeśli API blokuje Twój region

## Setup

```bash
git clone <this-repo>
cd pit-exante
cp .env.example .env
```

Uzupełnij `.env`:

```
EXANTE_API_KEY=...
EXANTE_SECRET=...
EXANTE_CLIENT_ID=...
EXANTE_ACCOUNT=ABC1234           # Twoje główne konto
EXANTE_SUBACCOUNTS=ABC1234.001,ABC1234.002  # opcjonalnie, jeśli masz subkonta
```

## Użycie

### 1. Pobierz dane

```bash
python download_transactions.py
```

Zapisuje `data/transactions.json` (wszystkie transakcje) i `data/symbols.json` (metadane instrumentów). Symbole delisted/przemianowane (404 z API) trafiają do `data/symbols_missing.json` — w takim przypadku dodaj ręcznie wpis do `config/symbol_overrides.json` z właściwym `symbolType`.

Alternatywnie — pobranie przez web UI Exante i wrzucenie CSV do `data/`: szczegóły w [INSTRUKCJA_POBRANIA_DANYCH.md](INSTRUKCJA_POBRANIA_DANYCH.md).

### 2. Wygeneruj rozliczenie

```bash
python -m pit_exante.cli
```

Opcjonalne flagi:
- `--year 2024` — tylko konkretny rok
- `--transactions data/transactions.json` — ścieżka do pliku (default jak wyżej)
- `--output output` — katalog docelowy (default `output/`)

### 3. Sprawdź wyniki

Raport per rok: `output/pit_YYYY.txt` (sformatowane sekcje PIT-38 + PIT-36/PIT-ZG).
Zbiorczy CSV: `output/pit_all.csv`.

## Testy

```bash
pytest
```

Pokrywa parser, klasyfikator, NBP API, silnik FIFO, generator raportów. Testy nie wymagają danych z `data/` — wszystkie to testy jednostkowe z mockami i fixturami.

## Architektura

```
src/pit_exante/
├── models.py          # Dataclasses: Transaction, FifoLot, TaxEvent, YearReport
├── parser.py          # JSON → Transaction (+derivacja waluty z exchange suffix)
├── classifier.py      # operationType → TaxCategory
├── nbp.py             # API NBP + cache + obsługa świąt polskich
├── symbol_metadata.py # STOCK vs CFD per symbolType (+ manual overrides)
├── country.py         # derivacja kraju dywidendy z exchange + waluty
├── fifo.py            # Silnik FIFO z obsługą splitów i corporate actions
├── calculator.py      # Orkiestrator: parse → classify → FIFO → aggregate per year
├── report.py          # Tekstowy raport per rok + CSV
└── cli.py             # Entry point
```

## Obsługiwane scenariusze

| Typ operacji Exante | Interpretacja | Formularz PIT |
|---|---|---|
| TRADE (BUY/SELL) | Zdarzenie FIFO | PIT-38 (papiery lub pochodne wg symbolType) |
| COMMISSION | Koszt uzyskania przychodu | PIT-38 |
| DIVIDEND | Przychód | PIT-36 + PIT-ZG (per kraj) |
| TAX / US TAX | Podatek u źródła | odliczenie w PIT-ZG |
| STOCK SPLIT | Modyfikacja FIFO (quantity × ratio, price / ratio) | neutralne podatkowo |
| CORPORATE ACTION | Reverse split + fractional cash payment | FIFO + sprzedaż ułamka |
| ROLLOVER | Swap overnight (CFD) | koszt lub przychód (instrumenty pochodne) |
| SPECIAL FEE / EXCESS MARGIN FEE | Koszt uzyskania przychodu | PIT-38 |
| AUTOCONVERSION | Pomijane (art. 24c nie dotyczy os. fizycznych) | — |
| FUNDING / WITHDRAWAL | Pomijane | — |
| SUBACCOUNT TRANSFER | Pomijane (transfer wewnętrzny) | — |

FIFO działa osobno per `(rachunek, instrument)` z normalizacją subkont do konta głównego (bo art. 24 ust. 10 ustawy o PIT dotyczy rachunku papierów wartościowych, nie subkonta brokerskiego).

## Znane ograniczenia

- Program zakłada jedną walutę rozliczenia per instrument (derivowaną z exchange suffix lub `asset`). Egzotyczne instrumenty wielowalutowe nie są wspierane.
- Nieznane `symbolType` albo brak metadanych → `UnknownInstrumentError` / `UnknownTypeError`. Rozwiązanie: dodaj wpis do `config/symbol_overrides.json` i/lub rozszerz `EXANTE_TYPE_TO_KIND` w `symbol_metadata.py`.
- Weryfikowano na realnych danych 2020-2026 (akcje, ETF, jeden CFD, wymiana EUR/USD). Inne przypadki (opcje, futures, obligacje) nie były testowane.

## Licencja

MIT. Używaj, modyfikuj, dziel się. Informuj mnie, jeśli znajdziesz błąd w logice podatkowej — wspólne zainteresowanie.
