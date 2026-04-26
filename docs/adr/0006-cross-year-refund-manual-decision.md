# ADR-0006: Cross-year refund wymaga manual decision

**Status:** Accepted
**Date:** 2026-04-26

## Context

US TAX recalculation może być wystawiony przez brokera w roku N+1 dla
dywidendy otrzymanej w roku N (np. refund WHT z marca 2024 dotyczy
dywidendy z grudnia 2023). Polski PIT nie ma jednoznacznej procedury
dla tego przypadku.

Dwie defensible interpretacje:

- **(A) Korekta PIT roku N** — refund "należy do" roku N (rok dywidendy);
  zmniejsz `tax_paid` w PIT-N, korekta + ewentualny czynny żal jeśli
  PIT-N już złożony.
- **(B) Negatywny WHT w roku N+1** — refund "powstał" w roku N+1; zaksięguj
  jako negatywny WHT w PIT-(N+1) bez ruszania PIT-N.

KIS interpretacje są sprzeczne między sobą. Realna praktyka: doradca
podatkowy konkretnego klienta podejmuje decyzję A lub B w oparciu o
wartość refundu, status PIT-N, i stosunek kosztu korekty do profitu.

## Decision

**Kalkulator nie podejmuje decyzji A/B automatycznie — `raise ValueError`
z opisem obu opcji i hintem do skonsultowania doradcy.** Zaimplementowane
w `calculator.py` (refund→parent merge path):

```python
if parent_div is not None and parent_div.date.year < tx_date.year:
    raise ValueError(
        f"Refund cross-year detected: ... \n"
        f"  (A) Korekta PIT-{parent_div.date.year}: ... \n"
        f"  (B) Negatywny WHT w PIT-{tx_date.year}: ... \n"
        f"Skonsultuj doradcę podatkowego. Po decyzji: "
        f"zmodyfikuj data/transactions.json lub dodaj override do code path."
    )
```

Po decyzji użytkownik:
- Dla (A): modyfikuje `data/transactions.json` aby refund był datowany
  do roku N (zmiana timestamp/valueDate), lub usuwa refund i ręcznie
  koryguje PIT-N
- Dla (B): modyfikuje refund tak żeby był zaksięgowany jako standalone
  negative WHT w roku N+1 (np. zmienia parent_uuid lub usuwa parent_div
  match — wymaga override w code path)

## Considered alternatives

- **Default = (A)** — automatic korekta poprzedniego roku. Odrzucone:
  użytkownik mógł już złożyć PIT-N i automatic korekta bez warning'a
  spowoduje rozbieżność z urzędem.
- **Default = (B)** — automatic negative WHT w bieżącym roku.
  Odrzucone: PitFx convention używa (A) dla małych wartości,
  ale (B) dla dużych — nie ma jednolitej reguły którą można zaimplementować.
- **Per-period parsing z komentarza Exante** — backlog H5 — gdy w
  comment jest "for tax period 2024-Q4", automatycznie księguj do roku
  z period. Odrzucone na razie: brak rzeczywistych refundów cross-year
  w danych Wojtka, więc format komentarza nieznany.

## Empirical evidence

- **Brak rzeczywistych cross-year refundów w danych Wojtka 2020-2026** —
  guard jest forward-defensive
- **PitFx PDFs 2020-2024** — żaden case cross-year refund w analizowanych
  latach
- **Backlog H5** w `pit-exante-backlog-2026-04-26.md` — kiedy pojawi się
  pierwszy refund cross-year, format komentarza będzie zbadany i ADR
  potencjalnie superseded

## Consequences

- ADR-0002 (fail-fast policy) konkretizowany dla tego case'u
- Test `tests/test_calculator_failfast.py::TestH2RefundCrossYear` covers
  scenariusz
- Gdy pojawi się rzeczywisty refund cross-year, użytkownik musi podjąć
  manual decision (A/B) i edytować `transactions.json` lub code path
- Jeśli okaże się że Exante używa konsistentnego formatu komentarza
  ("for tax period YYYY-QQ"), parsowanie tego można zautomatyzować
  w future ADR (i superseduje 0006)
