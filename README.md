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

**Dashboard:** `https://79309cb5.us1a.app.preset.io/superset/dashboard/integration-cockpit/`
— **11 tabs, 60 charts** (all render-verified), 7 native filters, cross-filtering, 60s
auto-refresh. **6 alerts** (paused).

**Roadmap:** [`docs/cockpit-roadmap-cleo.md`](docs/cockpit-roadmap-cleo.md) — Cleo-informed
question list (Q12–Q21) + sprint pack. Sprint 6 (Q12) shipped; Sprints 7–11 (money,
predictive anomalies, exception cases, partner 360, chargeback, prescriptive, carrier,
personas) are planned next.

## Layout

```
sql/
  00_schema.sql          partitioned txn_events + files + rollup + current + ops/config tables
  01_seed.sql            ~300k bulk rows + every acceptance-criteria edge case
  02_rollup_refresh.sql  incremental rollup function + files-missing-txns reconciliation view
  03_refresh_ops.sql     refresh_demo_ops() — re-crisp time-sensitive Q1/Q10 edge cases
scripts/
  db.py                  tiny psql substitute (runs .sql / queries against Neon)
  preset_client.py       authenticated Superset client (reused by all build scripts)
  01_register_db.py      register Neon as a Preset database
  cockpit_spec.py        declarative datasets + charts + dashboard spec
  build_cockpit.py       create datasets/charts, render-verify each chart
  build_dashboard.py     assemble tabbed dashboard + native filters + cross-filter
  build_alerts.py        5 Phase-1 alerts (paused by default)
  export_assets.py       export dashboard bundle -> superset/assets/*.yaml
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
.venv311/bin/python scripts/db.py run sql/00_schema.sql
cd scripts && ../.venv311/bin/python build_cockpit.py all      # datasets+charts+verify
../.venv311/bin/python build_dashboard.py                       # dashboard
../.venv311/bin/python build_alerts.py [--activate]             # alerts (paused unless --activate)
../.venv311/bin/python export_assets.py                         # refresh YAML bundle
```

## Performance

Aggregate-first: every Q2/Q3 chart reads `txn_rollup_hourly`; live/stuck reads
`txn_current`; raw `txn_events` is partitioned and reached only by drill. Measured
partner-filter aggregate: **~64 ms** at 300k rows (target < 2s; bump the seed's
`generate_series` to 2–5M to load-test).

## Demo freshness

The seed stamps absolute `now()` timestamps, so "fresh" signals decay over time.
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
