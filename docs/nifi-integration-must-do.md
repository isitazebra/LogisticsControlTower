# NiFi Integration — Must-Do Guide

**Status:** the initial demo runs **without NiFi**. The cockpit reads Postgres tables populated by the **seed script**; that stands in for NiFi. This document is the contract for the **later** integration phase — what NiFi must do so the same tables fill from live flows. **When NiFi comes online, nothing in the dashboard changes** — same tables, same datasets, same charts; only the writer changes (seed → NiFi).

---

## 0. The integration boundary

NiFi is the **producer**; Postgres (Neon) is the **interface**; Superset/Preset is a read-only **consumer**. Superset never talks to NiFi. NiFi's only job is to keep a small set of Postgres tables current.

```
NiFi (later)                         Postgres (the contract)            Superset/Preset (demo today)
  data pipelines  ──inline writes──▶ txn_files / txn_events / txn_current ◀── datasets → charts
  reporting tasks ──signals───────▶ endpoint_health, pipeline_health,
  monitor flows                      expected_feeds, monitor_heartbeat
  (DEMO: seed script writes all of the above instead)
```

Two write paths:
- **A — Inline** (per file + per transaction-stage): the data pipelines write as they run.
- **B — Operational signals**: reporting tasks + small scheduled monitor flows write health/liveness.

---

## 1. Non-negotiable attributes — every pipeline MUST set these

If these are missing, specific cockpit views break. Set them as FlowFile attributes (via `UpdateAttribute`, `EvaluateJsonPath`/`ExtractText`/`EvaluateXPath`) before any DB write.

| Attribute | Required for | If missing |
|---|---|---|
| `business_ref` | transaction lookup (Q4), every per-txn view | transactions aren't findable; "where is it" breaks |
| `interchange_id` | file↔transaction link (Q4), reconciliation | files and transactions can't be tied together |
| `environment` (prod/uat) | every view's env filter | prod/UAT can't be separated |
| `partner`, `lob`, `doc_type`, `direction`, `protocol`, `channel` | all grouping/filtering | rollups and scorecards lose dimensions |
| `control_number` (ISA/GS/ST) | duplicate detection (Q11), ack matching (Q5) | dupes and acks can't be correlated |

`business_ref` and `interchange_id` are the two that matter most — treat them like required fields the build won't run without.

---

## 2. CRITICAL — write the file at receipt, before parse

The riskiest file is the malformed one that fails *before* it becomes a transaction (invalid structure, empty rows, won't de-envelope). If you only write a row after successful parse, that file is invisible at the exact moment it's broken.

**Rule:** the inbound flow's *first* action after landing a file is to write a `txn_files` parent row (`status='received'`), keyed on `interchange_id`, with `business_ref` still null. Then update it after de-envelope.

```sql
-- 1) at receipt, before parse (PutDatabaseRecord):
INSERT INTO txn_files (interchange_id, file_name, environment, partner, channel, protocol, direction,
  received_at, status, kchar)
VALUES (:icid, :fname, :env, :partner, :channel, :protocol, :dir, now(), 'received', :kchar)
ON CONFLICT (interchange_id) DO NOTHING;

-- 2) after de-envelope (set declared count + control numbers, or reject):
UPDATE txn_files SET status=:status, declared_txn_count=:n, isa_control=:isa, gs_control=:gs,
  reason_category=:reason,
  completed_at=CASE WHEN :status IN ('delivered','rejected') THEN now() END
WHERE interchange_id=:icid;
```
`interchange_id` comes from the ISA identifier at receipt; `declared_txn_count` from the GS/ST counts at de-envelope. This is what makes "every incoming and outgoing file is visible" literally true.

---

## 3. Per-transaction writes (`txn_events` + `txn_current`)

Write **one `txn_events` row per stage transition** (received → validated → translated → transformed → delivered → acked) and **upsert `txn_current`** on every event. Use `PutDatabaseRecord` with a `DBCPConnectionPool` to Neon.

Status & reason mapping (set on the FlowFile by relationship):
| NiFi outcome | `status` | `reason_category` |
|---|---|---|
| success relationship | `ok` / `delivered` | — |
| failure relationship (validation) | `failed` | `bad_input_file` |
| failure (mapping/transform) | `failed` | `mapping_defect` / `transform_error` |
| failure (connection/delivery) | `failed` | `connectivity` / `delivery_error` |
| partner negative ack parsed | `rejected` | `rejected_by_partner` |
| `DetectDuplicate` matched | `duplicate` | `duplicate` |

Other inline fields: `kchar` = `${fileSize}` / 1000; `value_usd` parsed from the document if present; `sla_due_at` = event time + the partner/doc SLA (parameter or lookup); `terminal` = true on acked/delivered/failed-final; the **replay/reprocess flow** sets `replayed=true`, `replayed_at`, and increments `replay_count`.

(`txn_current` upsert SQL is in the build pack §0.2; `txn_files` link via `interchange_id` on each child row.)

---

## 4. Operational monitors — path B (the active sweep)

These power Q1 (arrival & stuck). Three reporting tasks plus a few small scheduled flows. **All of these are seeded statically in the demo; they go live only in the NiFi phase.**

| Table / signal | NiFi mechanism |
|---|---|
| `pipeline_health` (state, queue_depth, consume_rate, last_consumed_at) | `SiteToSiteStatusReportingTask` → input port → `PutDatabaseRecord`; **or** a scheduled `InvokeHTTP` to the NiFi REST status API. Gives queue depth + processing rate = the **hung-pipeline** signal (running + queue>0 + rate=0) |
| `pipeline_health.mq_depth` | `ConsumeJMS`/MQ metrics, or a small flow querying IBM MQ depth |
| `endpoint_health.status / last_ok_at` | `SiteToSiteBulletinReportingTask` (connection/auth bulletins) + a scheduled **liveness flow per endpoint** (`ListSFTP`/AS2 test/`InvokeHTTP` health) that writes `last_ok_at` on success, `status='down'` on bulletin |
| `endpoint_health.cert_expires_at` | scheduled flow reading cert/key expiry per endpoint (or from a config registry) |
| `expected_feeds.last_seen_at` | the inbound pipeline updates it on each arrival; `MonitorActivity` emits when a continuous feed goes silent |
| `expected_feeds.expected_next_at`, `grace_minutes` | **config table** (cadence per partner/doc) — the one human-maintained artifact |
| listed-not-fetched (stuck at pickup) | `SiteToSiteProvenanceReportingTask` → correlate `RECEIVE`/list vs `FETCH`; a listed file with no fetch in N min |
| `monitor_heartbeat.last_run_at` | **every monitor flow writes its own heartbeat each run** → powers sweep integrity (the watcher's watcher) |
| `duplicate` flag | `DetectDuplicate` on `control_number` via a `DistributedMapCache` |

**Discipline:** parameterize monitors — one process-group template per monitor type (SFTP liveness, landing-zone reconcile…), instantiated per endpoint via **Parameter Contexts**. Don't hand-build a job per partner. Keep all monitors in their own process group so a monitor failure can't touch a data flow.

---

## 5. Verify these 3 facts before starting NiFi work

These gate which signals are achievable:
1. **NiFi REST / Site-to-Site reachable** from where the writes land. (Everything in §4 depends on it.)
2. **Provenance retained ≥ a few hours.** (Listed-not-fetched correlation needs it.)
3. **Permission to add** the three reporting tasks + `MonitorActivity` + `DetectDuplicate` processors.

If any is "no," note which signals degrade — mostly listed-not-fetched (#2) and duplicate (#3) — and proceed with the rest.

---

## 6. Cutover — seed → live (when NiFi is ready)

1. Stand up the NiFi writes per §1–4 into a **staging** copy of the tables; confirm rows look right.
2. Validate parity: `txn_rollup_hourly` totals ≈ `txn_events` counts; a known file shows its children; a seeded-style hung pipeline appears.
3. Stop the seed job; point the NiFi writers at the live tables; truncate seed rows (or let them age out).
4. **No dashboard changes** — datasets, charts, alerts, and filters are unchanged. The cockpit doesn't know or care that the writer changed.

---

## 7. What runs on seed only until NiFi is live

For the demo, these are static seed rows, not live signals — fine to demo, but call out as illustrative:
- Live arrival/stuck, channel liveness, hung-pipeline, cert expiry (Q1)
- Provenance-based listed-not-fetched
- Real MQ/queue depth
- Real per-transaction and per-file event streams (seed generates a realistic volume + the edge cases)

Everything the cockpit *displays* works in the demo on seed data; only the *freshness/liveness* becomes real when NiFi is wired. That's the whole point of the Postgres-as-contract design: build and demo now, integrate later, zero rework.
