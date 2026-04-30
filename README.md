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

Raport Exante w formacie PIT-8C ma konkretne rozbieżności z polskimi przepisami. Ten program je adresuje:

1. **CFD w ogóle nie ma w raporcie Exante.** PIT-8C od Exante zawiera wyłącznie papiery wartościowe (akcje, ETF — pozycje 23-24, które w PIT-38 idą do wiersza 1). Instrumenty pochodne (CFD — pozycje 27-28, wiersz 3 PIT-38) są w raporcie pominięte, mimo że podatnik musi je rozliczyć. Program pobiera wszystkie transakcje CFD z API Exante (`symbolType: "CFD"`), liczy FIFO oraz rollovery (swap overnight) i pokazuje je osobno — jest to jedyne źródło danych do wiersza 3 PIT-38.
2. **Dywidendy zagraniczne.** PIT-8C to formularz dla zysków kapitałowych (art. 30b). Dywidendy (art. 30a) rozlicza się na PIT-36 z załącznikiem PIT-ZG per kraj źródła — w raporcie Exante ich nie ma w ogóle. Program pobiera z API transakcje DIVIDEND oraz TAX/US TAX (podatek u źródła), grupuje per kraj (USA, Kanada, itd.) i liczy podatek do dopłaty w Polsce z uwzględnieniem art. 30a ust. 9 (odliczenie do wysokości polskiego podatku).
3. **Forex (wymiana EUR/USD u brokera).** Ręczna konwersja walut nie jest zdarzeniem podatkowym dla osoby fizycznej (art. 24c dotyczy działalności gospodarczej), ale Exante raportuje ją jako TRADE. Program pomija te pozycje i zalicza wyłącznie prowizję jako koszt.
4. **Kurs NBP dla kosztu nabycia.** Exante przelicza koszt nabycia papieru po kursie NBP z dnia sprzedaży, nie z dnia zakupu. Ustawa (art. 11a ust. 2) wymaga kursu średniego NBP z ostatniego dnia roboczego poprzedzającego **dzień poniesienia kosztu** (czyli dzień zakupu), nie dzień powstania przychodu. Dla długo utrzymywanej pozycji różnica kursowa między tymi dwoma dniami przekłada się na istotną korektę w PIT-8C — zwykle zaniżone koszty. Program liczy każdy lot FIFO po kursie z D-1 przed datą jego zakupu i zachowuje ten kurs w kolejce aż do sprzedaży.

**W skrócie:** dla CFD i dywidend program jest jedynym źródłem danych (w raporcie Exante ich nie ma). Dla papierów wartościowych — druga, niezależna kalkulacja; rozbieżność vs raport Exante wynika głównie z innej metodologii kursowej (punkt 4).

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

### Zakres geograficzny i walutowy

- **Wspierane giełdy:** NYSE, NASDAQ, ARCA, BATS (USA), TMX (Kanada), SOMX (Szwecja). Inwestycja na innej giełdzie (XETRA, LSE, SWX, EURONEXT, ASX itd.) → fail-fast `UnknownCountryError` z prośbą o dodanie wpisu do `_EXCHANGE_COUNTRY` w `country.py` razem ze stawką UPO.
- **Wspierane waluty:** USD, EUR, CAD, SEK (+ PLN). Inna waluta → fail-fast w `nbp.py`. Dodanie nowej wymaga rozszerzenia `BARE_CURRENCIES` i `_VALID_NBP_CURRENCIES`.

### Metodologia podatkowa

- **Limit z art. 30a ust. 9 (dywidendy) liczony per-UPO 15% × brutto** ("interpretacja B", zgodna z linią KIS i praktyką biur typu PitFx). Alternatywna interpretacja A (cap = 19% × brutto) jest poparta wyrokiem NSA II FSK 1171/22 (28.02.2023) i niektórzy doradcy ją stosują — w obecnej implementacji nie jest dostępna jako tryb. Skala różnicy: dla 2024 r. rzędu ~6 PLN, dla portfeli z większą ekspozycją na kraje pobierające > 15% u źródła (Kanada bez NR301) może rosnąć liniowo.
- **Straty z lat ubiegłych (art. 9 ust. 3 / ust. 6 ustawy o PIT)** — Tool drukuje notę informacyjną w sekcji D PIT-38 z propozycją kwot na podstawie strat z lat widocznych w danych Exante (okno Y-5..Y-1). Tool NIE liczy automatycznie poz. 28/30 i NIE zna strat z innych brokerów / krypto / papierów PL ani historii ile straty już rozliczyłeś — to zostaje decyzją podatnika.
- **Autoconversion (EUR↔USD u brokera)** jest pomijana jako zdarzenie podatkowe — art. 24c dotyczy działalności gospodarczej. Komercyjne biura (np. PitFx) traktują ją jako zdarzenie kapitałowe. Skutek: nasz dochód kapitałowy może być nieco niższy/wyższy vs raport biura (rząd kilkudziesięciu PLN/rok dla typowej aktywności).
- **Rounding:** każda pozycja PLN kwantowana do 1 grosza (`ROUND_HALF_UP`) na poziomie zdarzenia. Tolerancja UPO cap: 0,1pp. Końcowe sumy w `pit_YYYY.txt` mogą różnić się od raportu biura o pojedyncze grosze.

### Niezaimplementowane scenariusze (fail-fast jeśli wystąpią)

- **Cross-year dividend refund** — zwrot podatku u źródła w innym roku niż wypłata dywidendy. Fail-fast `H2`. Wymaga decyzji A/B (przypisać do roku wypłaty czy roku otrzymania zwrotu) — odłożone do pierwszego rzeczywistego przypadku w danych.
- **CFD wypłacający dywidendę** — fail-fast w KROK 3 (manualna decyzja czy traktować jako derywat-z-przychodem-okresowym czy SECURITY).
- **Reverse split z konsekwencją FIFO** — obecna implementacja waży lots ilością (quantity); poprawne traktowanie wymagałoby ważenia kosztem PLN. REMX 2020 to jedyny case w danych, gdzie różnica jest pomijalna.

### Inne

- Program zakłada jedną walutę rozliczenia per instrument (derivowaną z exchange suffix lub `asset`). Egzotyczne instrumenty wielowalutowe nie są wspierane.
- Nieznane `symbolType` albo brak metadanych → `UnknownInstrumentError` / `UnknownTypeError`. Rozwiązanie: dodaj wpis do `config/symbol_overrides.json` i/lub rozszerz `EXANTE_TYPE_TO_KIND` w `symbol_metadata.py`.
- Testowane przypadki: akcje, ETF, CFD, wymiana EUR/USD, dywidendy USA/CA/SE. Opcje, futures, obligacje, dywidendy z innych krajów nie były weryfikowane.
- **Cache NBP (`data/nbp_cache.json`)** nie ma walidacji integralności ani obsługi retroaktywnych korekt tabel A. Jeśli NBP koryguje historyczny kurs (rzadkie) — usuń plik cache i uruchom ponownie. Cache jest persistowany na końcu udanego przebiegu kalkulatora.

## Licencja

MIT.
