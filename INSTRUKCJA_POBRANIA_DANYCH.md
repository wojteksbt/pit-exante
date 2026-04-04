# Instrukcja pobrania danych z Exante

## Jak pobrać

1. Zaloguj się: https://exante.eu
2. Przejdź do: **Reports** > **Add Custom Report**
3. **Account:** wybierz **ACC001 (All segregated)** — żeby objąć wszystkie subkonta
4. **Build report in:** zaznacz **CSV**
5. **Currency:** zostaw domyślną (przeliczamy na PLN po kursie NBP)

### Financial Transactions (WYMAGANE)

Zawiera: dywidendy, odsetki, opłaty, wpłaty/wypłaty, prowizje.

- **Od:** data otwarcia konta (najwcześniejsza możliwa)
- **Do:** dzisiaj
- Kliknij **+ Add**

### Trades (WYMAGANE)

Zawiera: wszystkie transakcje kupna i sprzedaży instrumentów.

- **Od:** data otwarcia konta (najwcześniejsza możliwa)
- **Do:** dzisiaj
- **Grouping:** No Grouping (każda transakcja osobno)
- Kliknij **+ Add**

### Pozostałe sekcje

Account Summary, Performance Report, Costs and Charges, Commissions, Overnights, Short allowance, Daily Position — **nie dodawaj**, zostaw puste.

### Zapisz

Kliknij **Save and Request** (prawy górny róg).

## Co rozliczamy

| Element | Źródło | Formularz PIT |
|---------|--------|---------------|
| Zyski/straty ze sprzedaży | Trades | PIT-38 (FIFO) |
| Prowizje transakcyjne | Trades / Financial Transactions | PIT-38 (koszty) |
| Dywidendy zagraniczne | Financial Transactions | PIT-36 + PIT/ZG |
| Odsetki | Financial Transactions | PIT-36 |
| Opłaty brokera | Financial Transactions | PIT-38 (koszty) |

## Po pobraniu

Wrzuć pobrane pliki CSV do katalogu `data/`:

```
pit-exante/
└── data/
    ├── financial_transactions.csv
    └── trades.csv
```

Daj znać "mam dane" — przeanalizuję format i wygeneruję rozliczenia osobno dla każdego roku podatkowego.

## Uwagi

- Pobierz **cały zakres** od początku konta — program sam podzieli na lata podatkowe
- Nie edytuj plików CSV ręcznie
- Jeśli masz kilka subkont/kont w Exante, pobierz raporty z każdego osobno
