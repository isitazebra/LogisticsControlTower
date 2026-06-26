# Integration Visibility Cockpit — Product Brief & Build Spec
### Hand-off document for Claude Code

---

> **⚠️ Implementation note (read first).** This is the *original* design brief. The
> shipped product is the **"Integration Command Center · Logistics"** dashboard
> (Superset dash 15), built by `scripts/build_value_sla.py` over a single data
> world in `public.txn_events`. Several design-time constructs below were
> collapsed or dropped during implementation — wherever this doc says otherwise,
> the **current** model wins:
>
> - **No `txn_current` table.** NiFi writes the append-only `txn_events` stream
>   *only* (one row per `business_ref`); the latest row **is** the current state.
>   "Open / current" is the filter `WHERE terminal=false`; per-shipment rollups
>   are the `vw_shipment*` views. Read every `txn_current` reference below as
>   "the latest `txn_events` row / a filter on `txn_events`."
> - **Dropped config tables:** `sla_rules`, `diagnostic_rules`, `deploys`,
>   `doc_type_catalog` were not built. Response SLA is expressed directly on
>   `txn_events` + the `vw_sla_pairs` view (204→990 / 204→214 / 204→210);
>   exception reasons live in `txn_events.reason_category`; the resolution-KB and
>   deploy-correlation features (Q11 extras) are out of scope.
> - **Seed/build path:** the bulk transaction world comes from
>   `scripts/gen_shipment_world.py`; static ops signals from `sql/01_seed.sql`;
>   the dashboard from `scripts/build_value_sla.py` (not the design-time
>   `build_cockpit.py` / `build_dashboard.py` split named below).
>
> The authoritative, current source of truth is the repo (`sql/` + `scripts/`).

---

## 1. The story — why this exists

An enterprise B2B integration platform moves **millions of EDI + API transactions** for a multi-LOB logistics business (brokerage, managed transportation, global freight forwarding, last/middle mile). Today's operational visibility is **error-record-centric**: it shows what *failed*, but it is blind to two failure modes that cause the worst incidents —

- **Absence:** a partner sends files, nothing appears, no error is raised. (Real pattern: a major retailer's 204 load tenders were received but no loads built; messages sat in an MQ behind a pipeline that was "running" but consuming nothing. Caught only when humans noticed ~20 missing loads.)
- **Stall:** thousands of messages backlogged or stuck mid-pipeline, not failed, not moving. (Real pattern: 4,000+ status messages awaiting reprocessing during a major incident.)

It is also missing operational signals the team has explicitly asked for: **rejected messages don't alert** (only hard failures do), **replays are invisible** (no one can tell if a message was already reprocessed), and **duplicates surface as false failures** and spam alerts.

**Product thesis:** make *what didn't happen* and *what's stuck* as visible as *what failed* — and treat **rejected, duplicate, and replayed** as first-class states, not afterthoughts.

**Vision (one line):** One cockpit where an ops engineer can answer, in seconds, "is everything flowing, what's stuck or missing, what's broken and why, and where is this specific transaction" — at platform scale.

---

## 2. Users

| Persona | Primary need | Lands on |
|---|---|---|
| Ops / hypercare engineer (primary) | Catch stuck/missing before customers do; triage exceptions | Arrival & Stuck, Exceptions |
| Support / CSR | Look up one transaction's status & history | Transaction lookup |
| Account / exec | Health at a glance across LOBs | Flow overview |
| Trading partner (later) | Their own SLA scorecard | Scoped scorecard |

---

## 3. The questions the cockpit must answer

Priority: **P0** = build in the 2-day window; **P1** = next; **P2** = later. Acceptance criteria are testable.

| ID | Pri | Question | Must show | Acceptance criteria | Powered by |
|---|---|---|---|---|---|
| **Q1** | P0 | Did it even arrive, and is anything stuck? | Channel liveness; missing expected feeds; listed-not-fetched; stuck/aging; **hung pipeline** (running but consuming nothing); MQ/queue depth; cert expiry; **sweep integrity** | A silent "feed expected but absent" appears within its grace window; a pipeline with queue depth > 0 and consume-rate 0 is flagged as hung; if a monitor itself stops running, the UI says so (no false "all clear") | ops tables + `txn_events` (`WHERE terminal=false`) |
| **Q3** | P0 | What's broken right now? | Exception queue distinguishing **failed vs rejected vs duplicate**, with reason category, partner, value, age | Rejected and failed are separate, filterable states; duplicates are counted but not shown as failures; each row has a reason_category | `txn_rollup_hourly` + drill to `txn_events` |
| **Q2** | P0 | Is everything flowing? (overall summary) | Headline volumetrics; **EDI vs API split** (primary); **volume by message type** (the volumetric grid: type × protocol × direction, count, %, failed, rejected, trend); volume over time stacked by protocol; top partners; auto-processed % | Every chart reads a rollup (sub-second at millions of rows); EDI/API split is a first-class dimension; the message-type grid shows per-type volume + fail/reject; filters re-scope all charts | `txn_rollup_hourly` |
| **Q4** | P1 | Where is this file / transaction? | **File explorer** (incoming & outgoing, parent grain) → drill to its child transactions; transaction lookup by business_ref → status + step history + **replay status**; files-missing-transactions reconciliation; files rejected at receipt | An incoming file appears the instant it lands (before parse), even if malformed; selecting a file shows its child transactions; a file declaring 200 txns with 187 children flags 13 missing; a transaction links back to its parent file; replay status visible | `txn_files` + `txn_events` |
| **Q8** | P1 | Can I fix it and prove it was fixed? | Replay/reprocess visibility: replayed badge, timestamp, count; link out to NiFi for the action | A replayed message is visibly marked as replayed (closes a known gap); the "fix" action deep-links to NiFi (Superset is read-only) | `txn_events` replay fields (`replayed`, `replayed_at`, `replay_count`) |
| **Q5** | P1 | Did the partner acknowledge? | FA tracking: late / rejected / missing 997s & CONTRLs | A missing ack past its window is listed; a rejected ack is distinguished from a missing one | `txn_events` ack linkage |
| **Q6** | P2 | Are we hitting SLA per partner? | Partner scorecard: %met/%missed, completion-time stats, financial impact | Sortable per-partner SLA; drill to that partner's history | rollup + `vw_partner_360` |
| **Q7** | P2 | Which partners/flows are worst? | Volume + error/reject leaders, change vs prior period | Top-N partners by volume and by exceptions, period-over-period | rollup |
| **Q9** | P2 | How much volume flowed? | Usage/billing view: counts by partner, doc type, channel | Monthly totals exportable | rollup |
| **Q10** | P1 | Are we hitting response SLAs? | Paired trigger→response compliance (e.g. 990 within X min of 204; 997 within 15 min); compliance % per rule; **at-risk worklist** (clock running, not yet breached) | A 204 with no 990 past threshold is "missed"; one approaching threshold is "at_risk" and alerts before breach; compliance % sliceable per rule/partner | `vw_sla_pairs` + `txn_events` |
| **Q11** | P2 | What's the root cause, and how do I fix it? | Failure-signature clustering with onset; partner-vs-platform attribution; replay re-failures; deploy correlation; **resolution KB** (likely cause + suggested action + runbook per exception) | Many incidents cluster into N signatures with onset; each exception shows a KB suggested action; a re-failed replay is flagged | `txn_events` (`reason_category`) |

**Cross-cutting requirement:** every view supports an `environment` filter (**prod / UAT**) — the same liveness/stuck checks must run against UAT pipelines, which go dark frequently.

---

## 4. Data model — the contract

Postgres (Neon). NiFi is the producer; Superset/Preset is a read-only consumer.

### 4.1 Status taxonomy (enum)
`received · validated · translated · transformed · delivered · acked · failed · rejected · duplicate`
- **failed** ≠ **rejected** ≠ **duplicate** — three distinct states, distinct reasons, distinct alerts.

### 4.2 Reason taxonomy (`reason_category`)
`bad_input_file · mapping_defect · connectivity · transform_error · delivery_error · ack_timeout · rejected_by_partner · duplicate · hung_pipeline · unknown`
- `bad_input_file` is attributed to the **partner** (reframes "your platform broke" → "this partner's file was malformed").

### 4.3 Tables
```sql
-- raw events (drill-only; partition by event_time; index dims)
txn_events(
  event_id, event_time, interchange_id, business_ref, environment,  -- interchange_id = parent file; business_ref null pre-parse
  lob, partner, channel, protocol, direction, doc_type,    -- protocol: edi|api
  stage, status, reason_category, terminal,
  sla_due_at, value_usd, kchar,                            -- kchar: kilochars (EDI volume unit)
  error_code,
  replayed boolean, replayed_at, replay_count,             -- replay visibility
  control_number                                            -- for duplicate detection
)

-- file/interchange parent grain: one row per physical file (incoming & outgoing)
-- written at RECEIPT, before parse, so malformed files are visible even if they never become transactions
txn_files(
  interchange_id PK, file_name, environment, partner, channel, protocol, direction,  -- in|out
  received_at, completed_at, status,                       -- received|parsed|delivered|rejected
  reason_category, declared_txn_count, isa_control, gs_control, value_usd, kchar
)
-- reconciliation: declared_txn_count vs actual child rows -> "transactions lost inside a file"
-- protocol is explicit (not derived): a partner can run both EDI and API.
-- Default rule if unknown: channel='api' -> 'api', else 'edi'.

-- hourly rollup (powers all aggregate charts; MV or upserted table)
txn_rollup_hourly(
  bucket, environment, lob, partner, channel, protocol, direction, doc_type, status,
  txn_count, value_sum, kchar_sum,
  failed_count, rejected_count, duplicate_count, breached_count
)

-- one row per live reference (lookup + stuck; never scan history)
txn_current(
  business_ref PK, environment, lob, partner, channel, doc_type,
  current_stage, current_status, last_event_at, sla_due_at, value_usd, terminal,
  replayed boolean, replayed_at, replay_count
)

-- operational monitor tables (small; written by NiFi monitor jobs)
endpoint_health(channel, endpoint, partner, environment, status, last_ok_at, cert_expires_at)
expected_feeds(partner, doc_type, channel, environment, expected_next_at, grace_minutes, last_seen_at)
monitor_heartbeat(monitor_name, channel, environment, last_run_at, expected_interval_sec)
pipeline_health(pipeline, environment, state, queue_depth, mq_depth, consume_rate, last_consumed_at)
```

---

## 5. Detection rules (must be implemented exactly)

- **Missing feed:** `now() > expected_next_at + grace_minutes` AND no arrival since `expected_next_at`.
- **Hung pipeline:** `state='running' AND (queue_depth>0 OR mq_depth>0) AND consume_rate=0` for ≥ N minutes. *(This is the retailer-204 incident signature — highest priority.)*
- **Stuck/aging:** `txn_current.terminal=false AND now()-last_event_at > stage_threshold`.
- **Listed-not-fetched:** source-listed file with no flow start within N min (NiFi provenance List-vs-Fetch).
- **Channel down:** `endpoint_health.status <> 'up'`.
- **Sweep integrity:** any `monitor_heartbeat` with `now()-last_run_at > expected_interval_sec` → surface as "monitor silent," never count it as healthy.
- **Rejected:** `status='rejected'` → its own queue + **its own alert** (today's explicit gap).
- **Duplicate:** repeated `control_number` per partner/doc_type → mark `duplicate`, count it, **suppress** from failure alerts.
- **Cert expiry:** `cert_expires_at < now()+14d` (warn), `<7d` (critical).

---

## 6. Non-functional requirements — performance is a feature

Target: **every dashboard panel renders in < 2s at 5M+ transactions/month**, and filter changes re-scope in < 2s. Hard rules:

**Aggregate-first (the cardinal rule).** No chart ever scans `txn_events` on load. Every tile/chart reads `txn_rollup_hourly` (tens of thousands of rows max) or `txn_current` (one row per live ref). Raw rows are reached only via drill-to-detail, which Superset auto-LIMITs.

**Partition the raw table.** `txn_events` is `PARTITION BY RANGE (event_time)`, daily or monthly. Drill queries hit one partition, not the whole history. Drop/archive old partitions instead of `DELETE`.

**Index for the access paths, not everything.**
- `txn_events`: BRIN index on `event_time` (cheap on append-only time data) + a composite btree on the drill dimensions `(partner, doc_type, status, event_time)`; btree on `business_ref`.
- `txn_rollup_hourly`: unique btree on the full dimension key `(bucket, environment, lob, partner, channel, protocol, direction, doc_type, status)`.
- `txn_current`: PK on `business_ref`; partial index `WHERE terminal=false` for the stuck scan.

**Incremental rollup, not full recompute.** Refresh only the last 1–2 hours each cycle: delete-and-reinsert the affected `bucket`s (or `INSERT … ON CONFLICT … DO UPDATE`), driven by NiFi or `pg_cron`. A full `REFRESH MATERIALIZED VIEW` is acceptable on day 1 but must be replaced before volume grows — recomputing the whole history every 2 min will not hold.

**Keep `txn_current` lean.** Archive terminal rows older than N days out to a history table so the live table stays small and the stuck-scan stays fast.

**Pre-aggregate the heavy grids.** The message-type volumetric grid and partner top-N read the rollup, never raw. If a chart needs a metric the rollup doesn't carry, add the column to the rollup — don't compute it in Superset.

**Lean on Preset caching + async.** Enable results caching (Preset manages the cache layer); set per-chart cache TTL to match the rollup cadence (e.g. 120s). Use async queries so the dashboard streams panels as they return rather than blocking on the slowest.

**Bound every query.** Row limits on all table charts; time-range default on every dataset (e.g. last 7 days) so nobody accidentally queries all history; `SELECT *` is banned in datasets.

**Other NFRs.**
- **Freshness:** rollup every 2–5 min; dashboard auto-refresh 1–2 min. Near-real-time, not streaming.
- **Read-only:** Superset/Preset does not act; reprocess/replay/re-sweep live in NiFi and are deep-linked.
- **Environments:** prod and UAT both monitored.
- **Access:** row-level security ready for partner-scoped views.

---

## 7. Architecture & platform

```
NiFi (producer)                 Neon Postgres (store)            Preset / Superset (consumer)
  ├ emits txn events  ───────▶  txn_events / txn_current  ◀────  datasets → charts → dashboard
  └ monitor jobs      ───────▶  ops + pipeline tables     ◀────  Alerts & Reports (email/Slack)
```
Preset specifics Claude Code should account for:
- **Alerts & Reports require the Professional plan** — start the 14-day Pro trial for the build; on free Starter, substitute auto-refreshing table charts.
- Preset is cloud-hosted → **allowlist Preset's egress IPs in Neon** + SSL on the connection.
- Use **preset-cli** (`superset sync native`) to import datasets/charts/dashboard as version-controlled YAML rather than hand-building, and to run DDL.

---

## 7a. NiFi data sourcing — every field is obtainable from NiFi

Nothing in the schema requires data NiFi doesn't already have. Every column maps to a native NiFi mechanism. Two write paths: **(A) per-transaction events** written inline by the data pipelines, and **(B) operational signals** pushed by NiFi's reporting tasks and small monitor flows.

### A. `txn_events` / `txn_current` — written inline by each pipeline
A pipeline sets these as FlowFile attributes (via `UpdateAttribute` / `EvaluateJsonPath` / `ExtractText`) and writes one row per status transition with `PutDatabaseRecord`.

| Field | Where NiFi gets it |
|---|---|
| `business_ref` | extracted from the payload (PO/Load/AWB/BOL) — set as attribute during parse; **make it a required attribute every pipeline sets** |
| `event_time` | `${now()}` at the processor |
| `environment` | parameter context per environment (prod/uat) |
| `lob, partner, doc_type` | routing attributes the pipeline already sets to choose the flow |
| `channel` | the inbound processor type (ListSFTP→sftp, AS2→as2, ConsumeJMS/MQ→mq, HandleHttpRequest→api…) |
| `protocol` | set `api` on API-ingest flows, `edi` on X12/EDIFACT parse flows — known at design time |
| `direction` | inbound vs outbound flow — known by pipeline |
| `stage` | the processor group the FlowFile is leaving (received→validated→transformed→delivered→acked) |
| `status` | success relationship → `ok`/`delivered`; failure rel → `failed`; partner negative-ack parse → `rejected`; dedupe processor → `duplicate` |
| `reason_category` | mapped from the failing processor / error bulletin (validation→`bad_input_file`, map error→`mapping_defect`, connection→`connectivity`…) |
| `error_code` | from the processor's error attribute / exception |
| `value_usd` | parsed from the document if present (invoice/PO amount) |
| `kchar` | `${fileSize}` / 1000 — FlowFile size is always available |
| `control_number` | the X12 ISA/GS/ST control number (or API idempotency key) — extracted during parse; **drives duplicate detection** (`DetectDuplicate` processor can also flag it) |
| `interchange_id` | the ISA/file identifier set at receipt; links every child transaction to its parent file. NiFi writes the `txn_files` parent row **at receipt before parse** (so malformed files are visible), then sets `declared_txn_count` from the GS/ST counts after de-envelope |
| `sla_due_at` | computed from `event_time` + the partner/doc SLA (parameter or lookup) |
| `replayed, replayed_at, replay_count` | the **replay/reprocess flow** sets `replayed=true` and increments — closes the "was this replayed?" gap |
| `terminal` | true on `acked`/`delivered`/`failed`-final stages |

### B. Operational tables — pushed by NiFi reporting tasks + monitor flows
| Table | NiFi source |
|---|---|
| `pipeline_health` (state, queue_depth, consume_rate, last_consumed_at) | `SiteToSiteStatusReportingTask` emits component + connection status, including **queued counts and processing rates** per processor/connection — this is the hung-pipeline signal natively |
| `pipeline_health.mq_depth` | `ConsumeJMS`/MQ processor metrics, or a small flow that queries IBM MQ depth |
| `endpoint_health` (status, last_ok_at) | `SiteToSiteBulletinReportingTask` (connection/auth bulletins) + a scheduled liveness flow per endpoint (ListSFTP/AS2 test/InvokeHTTP health) writing last_ok_at |
| `endpoint_health.cert_expires_at` | a scheduled flow reading cert/key expiry per endpoint (or config registry) |
| `expected_feeds.last_seen_at` | updated by the inbound pipeline on each arrival; `MonitorActivity` emits when a continuous feed goes silent |
| `expected_feeds.expected_next_at, grace_minutes` | config table (cadence per partner/doc) — the one human-maintained artifact |
| `monitor_heartbeat.last_run_at` | every monitor flow writes its own heartbeat each run → powers sweep integrity |
| `txn_events` provenance correlation (listed-not-fetched) | `SiteToSiteProvenanceReportingTask` emits List vs Fetch events; a query finds listed-with-no-fetch |

**Verify-first (3 facts that gate the build):** NiFi REST/Site-to-Site reachable from the store; provenance retained ≥ a few hours; permission to add the three reporting tasks + `MonitorActivity`/`DetectDuplicate` processors. If any is no, note which signals degrade (mostly listed-not-fetched and duplicate) and proceed with the rest.

**Phase 0 — Foundation (½ day)**
- Create schema (§4), the rollup MV/refresh, the `txn_current` upsert, the ops + `pipeline_health` tables.
- Load seed data: a few hundred rows spanning healthy / missing / stuck / hung-pipeline / failed / rejected / duplicate / replayed, in prod and UAT.
- *Done when:* every dataset query returns believable rows.

**Phase 1 — Q1 + Q3 + the gap-closing alerts (P0, ~1 day)**
- Q1 "Arrival & Stuck" tab: channel health, missing feeds, hung-pipeline, stuck, sweep integrity, cert expiry.
- Q3 exception queue with failed/rejected/duplicate distinction + reason_category.
- Alerts: **rejected-message**, **channel-down**, **hung-pipeline**, missing-feed, cert-expiry.
- *Done when:* a seeded hung-pipeline row triggers the hung alert and shows on Q1; a rejected row alerts and is separate from failed; a silenced monitor shows as "silent."

**Phase 2 — Q2 + assembly (P0, ~½ day)**
- Q2 "Flow" tab off the rollup; assemble both tabs; native filters (time, LOB, partner, channel, doc_type, environment) + cross-filtering + drill-to-detail; caching + auto-refresh.
- *Done when:* filtering by partner re-scopes all charts sub-second; drill goes to filtered raw rows.

**Phase 3 — Q4 / Q8 / Q5 (P1, stretch)**
- Transaction lookup with replay badge; ack tracking.
- *Done when:* searching a ref shows status + replay history; a missing ack is listed.

---

## 9. Out of scope (do not build)

- No write-back / action buttons in Superset (reprocess lives in NiFi).
- No ML anomaly detection or revenue-baseline intelligence (deliberately descoped).
- No custom HTML/React UI — Superset chart library + grid only.
- Q6/Q7/Q9 are P2 — stub the datasets, don't build the views yet.

---

## 10. Hand-off checklist — give Claude Code

1. **This brief.**
2. **The SQL build pack** (DDL + rollup + virtual-dataset queries) and a **seed script** (request both if not yet generated).
3. **Connection details:** Neon connection string (SSL), Preset workspace URL + API token/secret for preset-cli, Preset egress IPs to allowlist.
4. **Three NiFi facts to verify first:** REST API reachable from the cockpit; provenance retained ≥ a few hours; permission to add reporting tasks + MonitorActivity processors.
5. **Decision flagged:** Pro trial (alerts on) vs Starter (refresh-schedule workaround).

**First instruction to Claude Code:** "Build Phase 0 and Phase 1 against the attached schema and seed; stop at the Phase 1 acceptance criteria and show me the Q1 dashboard before continuing."
