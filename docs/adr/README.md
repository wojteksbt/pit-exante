# Architecture Decision Records

Lightweight ADRs for non-obvious decisions in pit-exante. Each ADR captures:
*why* a decision was made, *what* alternatives were considered, and what
empirical evidence supports it.

The Polish PIT regulations are often ambiguous (art. 30a, art. 11a etc.
have multiple defensible interpretations). When code makes a judgement call
that diverges from a literal reading of the law — or, conversely, that
follows the law over an obvious-but-wrong heuristic — we write an ADR.

## Format

```markdown
# ADR-NNNN: <title>

**Status:** Accepted | Superseded by ADR-NNNN
**Date:** YYYY-MM-DD

## Context
<the problem, the relevant Polish PIT articles, why a default isn't obvious>

## Decision
<what we chose>

## Considered alternatives
<what else we looked at, why we didn't pick it>

## Empirical evidence
<PitFx PDFs, NSA judgments, KIS interpretations, real-world data>

## Consequences
<what this means for code, for users, for future tax years>
```

ADRs are append-only. To change a decision, write a new ADR that supersedes
the old one (mark the old one's Status as `Superseded by ADR-NNNN`).

## Index

- [ADR-0001 — PitFx jako empirical reference](0001-pitfx-empirical-reference.md)
- [ADR-0002 — Fail-fast > silent warning dla anomalii podatkowych](0002-fail-fast-tax-anomalies.md)
- [ADR-0003 — Year boundary: timestamp dla DIV/TAX](0003-year-boundary-timestamp.md)
- [ADR-0004 — Per-country UPO cap z tolerance 0.1pp](0004-per-country-upo-cap.md)
- [ADR-0005 — Cost=0 dla fractional cash z reverse split](0005-fractional-cash-cost-zero.md)
- [ADR-0006 — Cross-year refund wymaga manual decision](0006-cross-year-refund-manual-decision.md)
- [ADR-0007 — NBP API jako autorytet (drop kalendarz świąt)](0007-nbp-api-authority.md)
- [ADR-0008 — Architektura calculator.py (match/case + indeksy + helpers)](0008-calculator-architecture.md)
