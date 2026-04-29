# config/pit8c/ — PIT-8C cz. D config (rok ≥ 2025)

Per-year JSON config dla PIT-38 wariant 18, dostarczany ręcznie przez usera
po otrzymaniu PIT-8C od brokera.

**Plan techniczny:** `docs/internal/PLAN_PIT8C_2025.md`.

---

## Schema

Każdy plik `{year}.json`:

```json
{
  "year": 2025,
  "issuer_name": "Ext Sp. z o.o. Oddział W Polsce",
  "issuer_nip": "1080028081",
  "poz_35_income_pln": "70218.00",
  "poz_36_cost_pln": "73639.00",
  "notes": "Wystawione 2026-02-XX, doręczone elektronicznie."
}
```

| Pole | Typ | Required | Walidacja |
|---|---|---|---|
| `year` | int | ✅ | musi == liczba w nazwie pliku, musi ≥ 2025 |
| `poz_35_income_pln` | **string** | ✅ | Decimal-parseable, ≥ 0 (zalecany format z PDF: `"70218.00"`) |
| `poz_36_cost_pln` | **string** | ✅ | Decimal-parseable, ≥ 0 (zalecany format z PDF: `"73639.00"`) |
| `issuer_name` | string | optional | display only |
| `issuer_nip` | string | optional | display only |
| `notes` | string | optional | wolny tekst |

Edge case: `poz_35==0 AND poz_36>0` jest odrzucany (niemożliwy PIT-8C).
Wartości MUSZĄ być stringami (loader explicit type-checks `isinstance(val, str)`)
— chroni precyzję Decimal (brak float roundtrip). JSON numbers, ints, null są
odrzucane z `Pit8CConfigError("musi być stringiem")`. Loader nie enforce'uje
sztywnego regexu `\d+\.\d{2}` — `"100"`, `"100.5"`, `"100.123"` też przejdą,
ale dla audytowalności zachowaj format z PDF (2 grosze).

---

## User flow

1. Otrzymujesz PIT-8C od brokera (zwykle styczeń-luty, np. `~/Documents/Finanse/PIT 2025/PIT-8C_*.pdf`).
2. Otwierasz PDF, w części D odczytujesz:
   - **poz. 35** (Przychód)
   - **poz. 36** (Koszty uzyskania)
3. Kopiujesz `2025.json.example` → `2025.json` i wpisujesz wartości jako stringi.
4. Uruchamiasz `pit-exante report 2025` — tool auto-discoveruje plik z tego katalogu.

Czas: ~30 sekund. Zero PDF parsingu (decyzja NG1 planu).

---

## Bezpieczeństwo

`*.json` jest **gitignored** (per `config/pit8c/.gitignore`). Realne PIT-8C
NIE trafia do publicznego repo. Tylko `*.json.example` (placeholder data,
np. `00000.00`) jest tracked.

Pre-commit hook `no-personal-amounts` + `gitleaks` chronią dodatkowo —
jeśli przypadkowo zacommitujesz realny config, hook złapie to przed pushem.

Custom path: `pit-exante report --pit8c-config-dir /absolute/path 2025`.

---

## Multi-broker scenario (out of scope)

Plan §6.1 odrzuca >1 PIT-8C dla jednego roku jako ABORT (decyzja D4).
Jeśli masz 2+ brokerów wystawiających PIT-8C dla tego samego roku —
ręcznie zsumuj poz. 35/36 do jednego configu, opisz scenariusz w `notes`,
i zachowaj oryginały dla audytu.
