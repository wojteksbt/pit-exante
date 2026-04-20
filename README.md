# PIT Exante

Kalkulator polskiego podatku PIT dla inwestorów korzystających z brokera [Exante](https://exante.eu). Generuje dane do formularzy **PIT-38** (zyski kapitałowe, papiery wartościowe + instrumenty pochodne/CFD) oraz **PIT-36/PIT-ZG** (dywidendy zagraniczne) osobno dla każdego roku podatkowego.

## Co robi

- Pobiera całą historię transakcji z Exante (REST API) oraz metadane instrumentów
- Przelicza każdą transakcję na PLN po kursie NBP z ostatniego dnia roboczego poprzedzającego datę transakcji (art. 11a ust. 1-2 ustawy o PIT)
- Oblicza zyski/straty metodą **FIFO** osobno per instrument, z obsługą stock split, reverse split, corporate actions, rolloverów (swap overnight) i forexu
- Klasyfikuje instrumenty jako **papiery wartościowe** (akcje, ETF) vs **instrumenty pochodne** (CFD) na podstawie `symbolType` z API Exante
- Grupuje dywidendy po kraju źródła dla PIT-ZG z automatycznym rozliczeniem podatku pobranego u źródła
- Generuje raport tekstowy per rok + zbiorczy CSV

## Dlaczego ten projekt, skoro Exante wystawia PIT-8C?

Exante udostępnia raport podatkowy w formacie PIT-8C, ale w praktyce jest kilka miejsc, w których warto przeliczyć samodzielnie lub skonfrontować wyniki:

1. **Klasyfikacja papiery wartościowe vs instrumenty pochodne.** PIT-8C ma osobne pozycje 23-24 (papiery: akcje, ETF) i 27-28 (pochodne: CFD, futures), które w PIT-38 trafiają do osobnych wierszy (1 i 3) z osobnym zyskiem/stratą. Podział zależy od `symbolType` w bazie brokera — warto sprawdzić, czy każdy instrument z Twojego portfela został przypisany tam, gdzie trzeba.
2. **Kurs NBP.** Art. 11a ust. 1-2 ustawy o PIT wymaga średniego kursu NBP z **ostatniego dnia roboczego poprzedzającego** dzień transakcji, z uwzględnieniem polskich świąt i weekendów. Metoda przeliczenia stosowana przez brokera może się różnić — ten program ją jawnie implementuje i pokazuje, jakim kursem przeliczył każdą pozycję.
3. **Dywidendy zagraniczne per kraj (PIT-ZG).** Każdy kraj źródła dywidendy wymaga osobnego załącznika PIT-ZG z kwotą podatku pobranego u źródła. Program grupuje dywidendy po kraju (USA, Kanada, itd.) i liczy podatek do dopłaty w Polsce z uwzględnieniem art. 30a ust. 9 (odliczenie do wysokości polskiego podatku).
4. **Zaokrąglenia.** Ustawa wymaga kwot groszowych per rekord, dopiero potem sumowania — zaokrąglenie sumy "na końcu" może dać inny wynik niż per rekord. Różnice są małe (kilka groszy per rok), ale widoczne przy pełnej weryfikacji.
5. **FIFO łącznie dla subkont.** Art. 24 ust. 10 ustawy o PIT dotyczy rachunku papierów wartościowych — subkonta Exante (`.001`, `.002`) to jeden rachunek. Program normalizuje subkonta do konta głównego, żeby FIFO działało na wspólnej puli.
6. **Forex (wymiana EUR/USD u brokera).** Ręczna konwersja walut nie jest zdarzeniem podatkowym dla osoby fizycznej (art. 24c dotyczy działalności gospodarczej), ale bywa raportowana jako TRADE. Program pomija te pozycje i zalicza tylko prowizję jako koszt.
7. **Corporate actions.** Stock splity, reverse splity i fractional cash payments wymagają specjalnej obsługi FIFO (korekta quantity/price, wydzielenie sprzedaży ułamka). Program implementuje to jawnie i pokazuje przeliczenia.

**W skrócie:** ten program służy jako druga, niezależna kalkulacja, którą porównujesz z raportem brokera. Zgodne wyniki = pewność. Rozbieżności → zaglądasz w szczegóły i wiesz, gdzie zadać pytanie księgowej.

## Uwaga podatkowa

Ten program wspiera rozliczenie, ale **nie zastępuje doradztwa podatkowego**. Weryfikuj wyniki z księgową/doradcą.

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
- Testowane przypadki: akcje, ETF, CFD, wymiana EUR/USD. Opcje, futures, obligacje nie były weryfikowane.

## Licencja

MIT.
