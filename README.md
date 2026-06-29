# Integration Visibility Cockpit

A B2B integration visibility cockpit for an enterprise moving millions of EDI + API
transactions across multiple logistics lines of business. It makes **what didn't
happen** (absence) and **what's stuck** (stall) as visible as **what failed**, and
treats **rejected / duplicate / replayed** as first-class states.

Runs on **Preset/Superset over Neon Postgres**. **NiFi wires in later** — the demo
runs entirely on seed data.

> **Design principle: Postgres is the contract.** The dashboard reads tables; the
> seed fills them now; NiFi fills them later. Switching from seed to live changes
> nothing in the dashboard. See [`docs/`](docs/) for the full product brief, build
> pack, and NiFi integration contract.

## Status — all phases delivered & verified

| Phase | Scope | State |
|---|---|---|
| 0 | Schema (partitioned), rollup, reconciliation, seed (~300k + edge cases) | ✅ deployed to Neon |
| 1 | Q1 Arrival & Stuck, Q3 Exceptions, 5 gap-closing alerts | ✅ built + render-verified |
| 2 | Q2 EDI/API Flow summary, tabbed dashboard, filters, cross-filter, auto-refresh | ✅ built + render-verified |
| 3 | Q4/Q8/Q5 files·lookup·replay·acks | ✅ built + render-verified |
| 4 | Q6/Q7/Q9 partner SLA·activity·usage | ✅ built + render-verified |
| 5 | Q10/Q11 response-SLA (at-risk)·diagnostics·resolution KB + at-risk alert | ✅ built + render-verified |
| 6 | Q12 document-type / transaction-type command center (Cleo core) | ✅ built + render-verified |
| UX | Nested-tab IA · **All Transactions explorer** · drill-downs · visual pass | ✅ built + render-verified |
| R | **LOB Cockpit** (reference-dashboard baseline): per-LOB Overview·Details·Traffic·Exceptions + payload drill | ✅ built + render-verified |

**Dashboard:** `https://79309cb5.us1a.app.preset.io/superset/dashboard/integration-cockpit/`
— **6 nested sections** (Overview · **LOB Cockpit** · Operations · Flow · Transactions ·
SLA & Partners), **100 charts**, 8 native filters (incl. **LOB** scoping + **Business-ref
search**), cross-filtering, drill-to-detail, 60s auto-refresh. **6 alerts** (paused).

The **LOB Cockpit** section reproduces the reference per-LOB template
(`docs/reference-dashboards/`): the canonical 4-KPI Overview (Total received · Success ·
Failure · Last received) with status/type donuts, a partner bar, processing trend, and a
**doc-type × status stacked bar**; a **Details** master-detail (Incoming / Outgoing / Ack
payload panels driven by clicking a transaction); a **Traffic** tab (daily trend stacked
by doc type + **partner × day pivot crosstab**); and an **Exceptions** triage queue. Scope
it to one line of business with the LOB filter to get a Brokerage / MT / B2B-style view.

Visual pass: currency/percent/number formats + data bars + conditional color on key
tables, **big-number trendlines**, **treemap** (family→type), **gauge** (SLA %),
consistent status colors. The **All Transactions** tab is the every-transaction grid +
per-event drill target.

**Roadmap:** [`docs/cockpit-roadmap-cleo.md`](docs/cockpit-roadmap-cleo.md) — Cleo-informed
question list. Active next: Sprint 7 (Q13 choreography), Sprint 8 (Q15 predictive
anomaly), Sprint 9 (Q16 cases + Q17 partner 360), Sprint 10 (Q19 prescriptive).
Deferred: Q14/Q18/Q20/Q21.

## Layout

```
sql/
  00_schema.sql          partitioned txn_events + files + rollup + ops tables + partner penalty
  01_seed.sql            static operational signals (ops_*), file edge cases, partner penalty
  02_rollup_refresh.sql  incremental rollup refresh function (steady-state)
  03_refresh_ops.sql     refresh_demo_ops() — re-crisp time-sensitive Q1/Q10 edge cases
  06_payloads.sql        per-event message body for the payload drill (Sprint R)
  09_predictive_anomaly.sql  feed-anomaly + vw_partner_anomaly views
  10_partner_profile.sql ref_partner_profile + vw_partner_360 scorecard
  13_exception_reason_backfill.sql  spread NULL reason_category across realistic codes
  14_consignment_views.sql  vw_shipment + vw_shipment_detail (the single shipment world)
  16_sla_pairs.sql       vw_sla_pairs (204->990 / 204->214 / 204->210 response SLA)
  (07/08 augment the separate edi_anomaly_dashboard_dataset reference schema)
scripts/
  db.py                  tiny psql substitute (runs .sql / queries against Neon)
  preset_client.py       authenticated Superset client (reused by all build scripts)
  01_register_db.py      register Neon as a Preset database
  gen_shipment_world.py  regenerate public.txn_events as the order world + rebuild rollup
  cockpit_spec.py        shared spec library (retired cockpit) imported by value_spec
  value_spec.py / value_spec_sla.py  dash15 spec (datasets + charts + layout)
  build_cockpit.py       the engine: create datasets/charts, render-verify each chart
  build_value_sla.py     build dash15 (datasets+charts+dashboard+verify); `all` or `verify`
  build_alerts.py        Phase-1 alerts (paused by default)
  deploy.sh / refresh.sh one-command deploy / pre-demo refresh
superset/assets/         version-controlled native YAML (db password masked)
docs/                    the 4 spec documents (brief, build pack, NiFi contract, handoff)
prototypes/              the 3 HTML design references (demo only — not the build)
```

## Setup

1. Python env (already created): `.venv311/` holds `preset-cli` + `psycopg2` (Python 3.11).
2. Secrets: copy `.env.example` → `.env`, fill in Neon URL + Preset workspace/API keys.
   `.env` is gitignored.

## Deploy / rebuild

```bash
scripts/deploy.sh        # full idempotent pipeline (schema -> ... -> verify)
scripts/refresh.sh       # re-crisp the demo edge cases before a presentation
```

Individual stages:
```bash
.venv311/bin/python scripts/db.py run sql/00_schema.sql        # schema
.venv311/bin/python scripts/db.py run sql/01_seed.sql          # static ops signals
cd scripts && ../.venv311/bin/python gen_shipment_world.py     # bulk txn world + rollup
../.venv311/bin/python build_value_sla.py all                  # datasets+charts+dashboard+verify
../.venv311/bin/python build_value_sla.py verify               # just re-render every chart
../.venv311/bin/python build_alerts.py [--activate]            # alerts (paused unless --activate)
```

## Performance

Aggregate-first: every Q2/Q3 chart reads `txn_rollup_hourly`; live/stuck reads
filter `txn_events` directly (`WHERE NOT terminal` — current state *is* the latest
row, there is no separate current table); raw `txn_events` is partitioned and
reached only by drill. Measured partner-filter aggregate: **~64 ms** at 300k rows
(target < 2s; bump `gen_shipment_world.py`'s order count to load-test).

## Demo freshness

`gen_shipment_world.py` stamps `txn_events` **`now()`-relative** at seed time: the
newest messages land "now", in-flight orders sit in the recent window with their
SLA either still ahead (on-time in-flight) or just elapsed (Stuck). Re-run the
seeder to refresh — over a day or two the on-time in-flight items naturally decay
to Stuck as their SLA window passes.
`refresh_demo_ops()` (in `sql/03_refresh_ops.sql`) re-stamps the small ops tables:
broken signals stay broken (van-liveness silent, Werner/Kroger feeds missing,
walgreens-tl hung), healthy ones get a long horizon so one run stays crisp ~24h.
`pg_cron` isn't available on this Neon role; if it becomes available, schedule
`refresh_demo_ops()` + `refresh_txn_rollup()` (commented at the bottom of the SQL).

## Alerts

5 alerts built (Hung pipeline, Missing feed, Channel down, Rejected message, Cert
expiring) — SQL + `> 0` validator against Neon, attached to their Q1 charts.
**Created paused** (`active=false`) because seed conditions are persistently true
and would email on every cadence. Flip on in Preset, or `build_alerts.py --activate`,
once you've set the intended recipient (currently the workspace owner).

## NiFi cutover (later)

See [`docs/nifi-integration-must-do.md`](docs/nifi-integration-must-do.md). NiFi
writes the same tables (inline per-event + monitor reporting tasks); stop the seed,
point NiFi at the tables — **no dashboard changes**.
