# ADR-0009: REIMBURSEMENT → PIT-36 inne źródła (art. 20)

**Status:** Accepted
**Date:** 2026-05-26
**Supersedes:** —
**Related:** ADR-0006 (cross-year dividend refund — manualne A/B), ADR-0002 (fail-fast policy)

## Context

Exante `operationType: REIMBURSEMENT` (zwrot środków za błędnie wykonane zlecenie brokerskie) cicho wpadał w `case _: return TaxCategory.SKIP` klasyfikatora. Skutek: pierwszy real-data event REIMBURSEMENT (zwrot brokera za duplikat zlecenia SELL, który wygenerował nielegalną krótką pozycję na rachunku non-margin) **nie pojawiał się w żadnym wyjściowym PIT-cie** → potencjalne underpayment.

Wymagane: jednoznaczna reguła routingu REIMBURSEMENT do odpowiedniej deklaracji PIT, niezależna od kwoty, konsystentna dla kolejnych zwrotów, audit-defensible.

Trzy defensible interpretacje:

- **(A) PIT-38 wiersz 1 (art. 17 pkt 6 + akcesoryjność)** — zwrot dzieli los źródła (WSA Gdańsk I SA/Gd 380/16). Linia mniejszościowa.
- **(B) PIT-36 inne źródła (art. 20 ust. 1)** — świadczenie peryferyjne, niemieszczące się w katalogu zamkniętym art. 17.
- **(C) Brak PIT (art. 21 ust. 1 pkt 3a — zwolnienie odszkodowania)** — odrzucone, voluntary broker correction ≠ statutory damages.

Dodatkowe odrzucone:

- **(D) Korekta PIT za rok pierwotnej transakcji** — PIT osób fizycznych = cash-basis, nie accrual. Admin koszt re-otwarcia złożonej deklaracji. Niemożliwe do utrzymania konsystentnie.
- **(E) Neutralność podatkowa (zwrot bezpodstawnego wzbogacenia)** — ma mocne wsparcie KIS (0113-KDIPT2-2.4011.427.2025.3.ACZ, 0114-KDIP3-1.4011.237.2022.2, 0112-KDIL2-1.4011.281.2025.1.KF) + NSA, ale w obecnym design tool'a wybrane jest aktywne księgowanie (B) z powodów audit-trail i symetrii z już-zaksięgowaną stratą z roku transakcji pierwotnej. Project owner używający tego tool'a w innym scenariuszu (np. intra-year refund bez wcześniejszego księgowania straty) może świadomie wybrać Opcję E.

Pełna analiza linii prawnej (jeśli posiadasz lokalnie): `docs/legal/reimbursement-art20.md`.

## Decision

**Każdy `operationType: REIMBURSEMENT` → `TaxCategory.REIMBURSEMENT` → `TaxEvent(kind=OTHER_SOURCES, event_type="reimbursement")` → osobna sekcja raportu "PIT-36 inne źródła (art. 20 ust. 1)".**

Implementacja:
- `src/pit_exante/models.py`: `TaxCategory.REIMBURSEMENT`, `InstrumentKind.OTHER_SOURCES`, pola `YearReport.pit36_inne_zrodla_*`.
- `src/pit_exante/classifier.py`: `case "REIMBURSEMENT" → TaxCategory.REIMBURSEMENT` przed default SKIP.
- `src/pit_exante/calculator.py`: `case TaxCategory.REIMBURSEMENT` generuje `TaxEvent` z NBP D-1 (przez `_effective_date(t)` = `valueDate`), `_classify_event_kind` rozpoznaje `event_type=="reimbursement" → OTHER_SOURCES`, `_aggregate_by_year` separuje OTHER_SOURCES od PIT-38.
- `src/pit_exante/report.py`: `_render_pit36_inne_zrodla()` drukuje osobną sekcję z instrukcją wypełnienia + ostrzeżeniem o kwocie wolnej od podatku (skala).
- CSV: REIMBURSEMENT pojawia się jako odrębny wiersz z `Typ="reimbursement"` (filtr na poziomie testu).

**Reguła "B-zawsze" — niezależna od kwoty zwrotu.** Świadomie wybrana ponad warianty zależne od kwoty (ad-hoc, audit risk).

## Considered alternatives

- **(A) PIT-38 wiersz 1** — odrzucone. Konsystencja > akcesoryjność dla zwrotów peryferyjnych. WSA Gdańsk I SA/Gd 380/16 jest linią mniejszościową.
- **Per-amount escalation: B dla małych, A dla dużych** — odrzucone. Ad-hoc reguła zależna od kwoty trudna do obronienia ("gdzie próg?", "dlaczego akurat tu?"). Linia konsystentna lepsza.
- **(D) Korekta PIT za rok pierwotnej transakcji** — odrzucone. Cash-basis PIT.
- **(E) Neutralność / zwrot bezpodstawnego wzbogacenia** — mocna linia prawna (KIS + NSA), świadomie odrzucona dla aktywnego księgowania jeśli istnieje wcześniej zaksięgowana strata z transakcji pierwotnej (asymetria byłaby nadmiernie favorable dla podatnika). **Future trigger:** dla projektów / użytkowników, gdzie wcześniejsza strata NIE jest zaksięgowana (intra-year refund, brak pierwotnej straty PIT-38), Opcja E może być właściwa — wymaga manualnej decyzji i ewentualnego wniosku o indywidualną interpretację.
- **(C) Art. 21 ust. 1 pkt 3a (zwolnienie)** — odrzucone. Audit bait, voluntary correction ≠ damages w rozumieniu statutu / wyroku / ugody.
- **Per-payload routing (`comment`, `parentUuid`)** — odrzucone. Real-world REIMBURSEMENT w danych Exante: `symbolId=null`, `comment=null`, `parentUuid=null`. Brak deterministycznego sygnału pozwalającego routować do PIT-38 (linking refund → original SELL). Nawet gdyby dane były bogatsze, akcesoryjność (A) i tak odrzucona.

## Empirical evidence

- **1 rzeczywisty REIMBURSEMENT** w danych testowych (USD denominacja, brak `symbolId`/`comment`/`parentUuid` w payload — minimalny shape).
- **Kontekst typowego case'u:** duplikat zlecenia SELL na rachunku non-margin → wygenerowanie nielegalnej krótkiej pozycji → buy-to-close po wyższej cenie generuje stratę PIT-38 → po skardze klienta broker wypłaca REIMBURSEMENT cofający ekonomicznie wynik błędu. Ten kontekst jest reprezentatywny dla case'u, dla którego ta reguła powstała.
- **Linia KIS dla Linii A (świadczenia peryferyjne, art. 20):** 0114-KDIP3-1.4011.373.2024.1.MG, 0114-KDIP3-1.4011.68.2025.1.AK.
- **Linia KIS + NSA dla Linii E (neutralność, art. 405 k.c. bezpodstawne wzbogacenie):** 0113-KDIPT2-2.4011.427.2025.3.ACZ, 0114-KDIP3-1.4011.237.2022.2, 0112-KDIL2-1.4011.281.2025.1.KF.
- **Praktyka rynkowa:** Saxo Bank Polska księguje analogiczne zwroty w PIT-36 inne źródła. Biura podatkowe (mainstream) idą tą samą drogą.
- **Advisor call** (`Inference.ts --mode advisor`, 2026-05-26) — sound, lock the decision; nie miał w polu widzenia pełnej linii Opcji E przy pierwotnej rekomendacji.

## Consequences

- **Zerowe ryzyko underpayment** dla REIMBURSEMENT — eksplicytny case w klasyfikatorze + obowiązkowa sekcja w raporcie.
- **Filing implication:** użytkownik, który dotąd składał tylko PIT-38 + jakąś formę dla działalności (PIT-28/PIT-36L), w roku z REIMBURSEMENT musi dodatkowo złożyć **PIT-36** (sekcja D wiersz "Inne źródła"). Nowy formularz: ~10 min w e-Urząd Skarbowy, dane osobowe auto-fill, wiersz inne źródła wpisuje samodzielnie. Numeracja pozycji zależy od wariantu formularza za rok deklarowany — instrukcja kalkulatora celowo nie wpisuje numerów, żeby raport nie zardzewiał przy nowych wariantach.
- **Tax impact:** podatek od inne źródła = `12%`/`32%` × przychód × (po pomniejszeniu kwotą wolną od podatku, jeśli to jedyne dochody na skali). Dla niskich kwot REIMBURSEMENT i braku innych dochodów na skali → realny podatek 0 PLN (kwota wolna pokrywa). Dla wyższych kwot lub posiadania innych dochodów na skali — sumują się.
- **PIT-38 za rok pierwotnej transakcji NIE jest modyfikowane** — strata z pierwotnej (błędnej) transakcji pozostaje zaksięgowana. Brak retroaktywnej korekty.
- **Reguła skaluje się** do kolejnych zwrotów: te same case'y w przyszłości pójdą tym samym torem.
- **Test coverage:** `tests/test_reimbursement.py`, `tests/test_classifier.py::test_reimbursement`.
- **ADR-0006 NIE jest superseded** — tamta dotyczy cross-year DIVIDEND refund (TAX_WITHHELD); ta dotyczy TRADE REIMBURSEMENT. Różne ścieżki kodu, różne podstawy prawne (art. 30a vs art. 20).
- **Future-defense:** Gdyby pojawił się event ekonomicznie podobny, ale z innym `operationType` (`ADJUSTMENT`, `CORRECTION`, `REBATE`, `COMPENSATION`), `classifier.py` go nie złapie. Decyzja per przypadek z aktualizacją tego ADR.

## Verification

- `pytest tests/test_classifier.py tests/test_reimbursement.py tests/test_report.py -q` → wszystkie zielone.
- Raport rocznika z REIMBURSEMENT zawiera sekcję "Inne źródła (art. 20 ust. 1)" z kwotą PLN po przeliczeniu NBP D-1.
- Raport rocznika pierwotnej transakcji pozostaje nieruszony (regression guard).
- `output/pit_all.csv` zawiera odpowiadające wiersze `Typ="reimbursement"`.
