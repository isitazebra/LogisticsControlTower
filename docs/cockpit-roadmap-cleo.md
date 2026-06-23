# Cockpit Roadmap — Cleo-Informed Question List & Sprint Pack

Extends the original brief (Q1–Q11, all shipped) with capabilities learned from
**Cleo Integration Cloud (CIC) Cockpit** — the reference product in this exact
domain. Same format as the brief: a prioritized question list, then a phased
sprint build pack with testable acceptance criteria. Same architecture holds:
**Postgres is the contract; Superset reads; NiFi (or seed) writes.**

> Sources: Cleo CIC Cockpit (end-to-end EDI+API visibility, document flow by
> business transaction type, partner scorecards, centralized issue lifecycle),
> and Cleo's 2025–26 AI-native release (predictive anomaly detection,
> prescriptive action plans, chargeback prevention, carrier performance).

---

## What Cleo does that pushes us further

| Cleo strength | Our current state | The gap → new question |
|---|---|---|
| **Visibility by message type, grouped by business transaction type** (orders, invoices, ASNs, acks…) | Q2 has one volumetric grid | **Q12** doc-type command center + families |
| **Document-flow choreography** across a process | per-txn lookup only | **Q13** business-process flow |
| **Revenue / "business outcome" visibility** | `value_usd` only | **Q14** money in motion / revenue-at-risk |
| **Predictive anomaly detection, early warnings** | reactive at-risk SLA | **Q15** silent-partner / volume anomaly |
| **Centralized issue lifecycle, tickets, collaboration** | read-only exception list | **Q16** exception case management + MTTR |
| **Partner scorecards shared with ecosystem (RLS)** | per-partner SLA chart | **Q17** partner 360 / network scorecard |
| **Chargeback prevention / compliance** | penalties table only | **Q18** chargeback & compliance risk |
| **Prescriptive action plans** | static resolution KB | **Q19** prescriptive playbooks |
| **Carrier performance management** | — | **Q20** carrier scorecard |
| **Persona dashboards (business vs IT), scheduled** | one dashboard | **Q21** persona & shareable views |

---

## The questions (Q12–Q21)

Priority: **P0** = next sprint; **P1** = soon; **P2/P3** = later. Acceptance is testable.

| ID | Pri | Question | Must show | Acceptance criteria | Powered by |
|---|---|---|---|---|---|
| **Q12** | **P0** | What's happening **by document type**, grouped by business transaction type? | Per-doc-type metrics (volume, %ok, failed, rejected, duplicate, $ value, EDI/API, trend, top partners); types rolled into **business families** (Order-to-Cash, Procure-to-Pay, Transportation, Warehouse, Functional) | Every `doc_type` present in data has a card/row with its metrics; each type maps to a family; a family subtotal = Σ its types; unmapped types fall into **Other**; selecting a type cross-filters the tab; EDI vs API shown per type | `txn_rollup_hourly` + `doc_type_catalog` |
| **Q13** | P1 | Where in the **document chain** did a business process stall? | Per process (O2C `850→855→856→810→820`; Transportation `204→990→214→210`), instances that completed the full chain vs stalled at step N; broken-chain worklist; cycle time per hop | A process instance missing a downstream document is flagged at the step it stalled; completion % per process; per-hop cycle time; a stalled chain links to its transactions | `txn_events` + `process_def` |
| **Q14** | P1 | How much **money** is flowing, and how much is **at risk**? | $ in motion (period); **$ at risk** = stuck + failed + SLA-missed value; $-at-risk by partner/LOB/doc-type; top-$ exposure worklist; value trend | $ at risk = Σ(value of non-terminal stuck) + Σ(value of failed/rejected) + Σ(value of SLA-missed); sliceable by partner/LOB/type; top exposures sorted by $ | `txn_current` + `txn_rollup_hourly` + `txn_events` |
| **Q15** | P1 | Which partner/feed is **behaving abnormally** (before it's an incident)? | Per-partner rolling **volume baseline** (mean/σ over trailing N days); current vs baseline **z-score** + drop %; **silent/abnormal partner** list; projected SLA breaches | A partner whose current volume is ≥2σ below baseline (or dropped >X%) is listed as abnormal; a partner gone fully silent vs an active baseline is flagged; deterministic (no ML) thresholds | `txn_rollup_hourly` (baseline view) |
| **Q16** | P2 | Can we **manage and prove** exception resolution? | Cases with status/owner/severity; open-by-status, **aging worklist**, **MTTR**, resolution-SLA; opened/resolved trend | A failed/rejected txn can become a case; case carries status (open→investigating→waiting_partner→resolved), owner, timestamps; MTTR computed; aging cases surfaced; **Superset read-only, writes via `cases.py`/NiFi** | `exception_cases` (new) |
| **Q17** | P2 | What's each **partner's full health** (one scorecard, shareable)? | One row/partner: volume, exception rate, ack health, SLA %, $-at-risk, last-seen, onboarding status & tier, anomaly flag | Scorecard sortable across all partners; onboarding status & tier shown; **RLS rule on `partner`** so a partner sees only their row; anomaly flag from Q15 | `partner_profile` (new) + rollup + current |
| **Q18** | P2 | Where are we **exposed to chargebacks / compliance** penalties? | Compliance breaches that trigger chargebacks (late ASN, late/missing 997, label/PO compliance); **$ penalty exposure**; by partner/retailer | A late/missing ASN or ack with a configured penalty is listed with its $ exposure; total exposure by partner; trend | acks/SLA views + `partner_penalty` |
| **Q19** | P2 | What's the **prescribed fix** for this exception, step by step? | Deepened KB: ordered remediation steps, owner, expected fix time, deep-link to NiFi action; tie to cases | Each exception signature maps to an ordered playbook with expected-fix-time; surfaced inline on the exception and on its case | `diagnostic_rules` (extended) + `playbook_steps` (new) |
| **Q20** | P3 | How are our **carriers** performing? | Carrier scorecard: on-time milestone %, tender-accept %, status-update latency, exception rate | Per-carrier on-time % and tender acceptance; sortable; period-over-period | `txn_events` (204/990/214) + `partner_profile` (carrier flag) |
| **Q21** | P3 | Can each **persona** get their own view, on a schedule? | Exec summary, ops worklist, partner-scoped scorecard; scheduled email/Slack delivery | Separate dashboards/tabs per persona; partner view RLS-scoped; scheduled report delivers on cadence | Superset dashboards + RLS + Reports |

**Cross-cutting:** every new view keeps the `environment` (prod/UAT) filter and reads
aggregates first (rollup/current), raw only on drill — same NFRs as the brief (<2s).

---

# Sprint build pack

Work sprints in order; each is self-contained (schema Δ → datasets → charts →
alerts → done-when). Sprint 6 is the P0 commit.

## Sprint 6 — Document-type / transaction-type command center (Q12) · P0

**Goal:** Cleo's core — see everything *by message type*, grouped by *business
transaction type*.

**Schema Δ**
```sql
CREATE TABLE doc_type_catalog (
  doc_type text PRIMARY KEY, label text, business_family text,
  typical_direction text, sla_minutes int
);
-- seed: 850 PO / 855 PO-ack / 860 PO-change → Order-to-Cash; 810 invoice / 820 pay
-- → Procure-to-Pay; 204 tender / 990 response / 214 status / 210 invoice →
-- Transportation; 940/945 → Warehouse; 997/CONTRL → Functional; HAWB → Air.
```
**Datasets** (read rollup, join catalog)
```sql
-- per-type metrics with family + EDI/API
SELECT coalesce(c.business_family,'Other') AS family,
       coalesce(c.label, r.doc_type) AS doc_label, r.doc_type, r.protocol,
       sum(r.txn_count) txns, sum(r.failed_count) failed, sum(r.rejected_count) rejected,
       sum(r.duplicate_count) dupes, sum(r.value_sum) value_usd,
       round(100.0*(1-sum(r.failed_count+r.rejected_count)::numeric/nullif(sum(r.txn_count),0)),1) ok_pct
FROM txn_rollup_hourly r LEFT JOIN doc_type_catalog c USING (doc_type)
GROUP BY 1,2,3,4;
-- family subtotal: GROUP BY family only.
```
**Charts — tab "Transaction Types"**: family volume bar (breakdown protocol);
per-family big numbers; **doc-type grid** (label, family, EDI/API, volume, ok%,
failed, rejected, dup, $); top partners per type; throughput-by-family trend.
**Alert:** none (visibility tab).
**Done when:** every data doc_type appears with metrics; family subtotals equal
Σ types; selecting a type cross-filters; an unmapped type lands in **Other**.

## Sprint 7 — Process choreography (Q13) + Money in motion (Q14) · P1
**Schema Δ:** `process_def(process, step_no, doc_type, direction, optional)`.
**Datasets:** chain-completion per `business_ref`/interchange; `$-at-risk` =
stuck-value ∪ failed-value ∪ SLA-missed-value.
**Charts:** process completion funnel, broken-chain worklist, cycle-time per hop;
$-in-motion, $-at-risk big numbers, $-at-risk by partner/LOB/type, top exposures.
**Alert:** $-at-risk over threshold.
**Done when:** a chain missing a downstream doc flags at its stall step; $-at-risk
matches Σ of its three components.

## Sprint 8 — Predictive anomaly / silent partner (Q15) · P1
**Schema Δ:** `v_partner_baseline` view (trailing mean/σ per partner·doc_type).
**Datasets:** current vs baseline z-score, drop %, silent flag; projected breaches.
**Charts:** abnormal-partner worklist, volume-vs-baseline, anomaly count.
**Alert:** silent/abnormal partner (≥2σ drop) — proactive.
**Done when:** a seeded partner whose recent volume drops to ~0 vs an active
baseline is listed as abnormal and alerts.

## Sprint 9 — Exception cases + MTTR (Q16) + Partner 360 (Q17) · P2
**Schema Δ:** `exception_cases(...)`, `partner_profile(...)`.
**Write-path:** **seed + a tiny `cases.py` CLI** (open/update/resolve) standing in
for NiFi/an app — Superset stays read-only. *(Chosen approach.)*
**Charts:** open-by-status, aging worklist, MTTR, resolution-SLA; partner-360
scorecard (volume, exceptions, acks, SLA %, $-at-risk, onboarding, anomaly).
**RLS:** rule on `partner` for partner-scoped sharing.
**Alert:** aging open case (> N h unresolved).
**Done when:** open→resolve via CLI moves the case and updates MTTR; a partner
RLS user sees only their scorecard row.

## Sprint 10 — Chargeback risk (Q18) + Prescriptive playbooks (Q19) + Carrier (Q20) · P2
**Schema Δ:** extend `diagnostic_rules` (severity, expected_fix_minutes),
`playbook_steps(signature, step_no, action, owner, eta_min, nifi_link)`,
`partner_profile.is_carrier`.
**Charts:** chargeback exposure worklist ($), prescriptive playbook per signature,
carrier scorecard (on-time %, tender-accept %, status latency).
**Alert:** chargeback exposure over threshold.
**Done when:** a late/missing ASN with a penalty shows $ exposure; each signature
shows an ordered playbook; carrier on-time % computes.

## Sprint 11 — Persona & shareable dashboards (Q21) · P3
Exec summary, ops worklist, partner-scoped scorecard; scheduled email/Slack;
RLS-scoped partner view. **Done when:** each persona has a view and a scheduled
delivery fires.

---

## Schema additions summary (so future writers/NiFi know the contract)
`doc_type_catalog` · `process_def` · `partner_profile` · `exception_cases` ·
`playbook_steps`; views `v_partner_baseline`, `v_revenue_at_risk`,
`v_partner_360`, `v_chargeback_risk`. All small/config or aggregate — no change to
the hot path. NiFi continues to fill `txn_events`/`txn_files`/ops tables unchanged.
