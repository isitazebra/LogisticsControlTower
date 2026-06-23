# Superset / Preset Build Pack — Full Sequence (Q1–Q9)

Authoritative build doc. Work the phases in order; each is self-contained (schema dependency → datasets → charts → alerts → acceptance). Supersedes the Q1+Q2 pack.

**Stack:** Neon Postgres · NiFi (producer) · Preset/Superset 4.x–5.x with `DRILL_TO_DETAIL`, `DRILL_BY`, `DASHBOARD_CROSS_FILTERS`, `ALERTS_REPORTS` on.
**Perf target:** every panel < 2s at 5M+ txns/month. Aggregate-first, raw drill-only. See §Performance.

## Build order at a glance
| Phase | Delivers | Questions | Pri |
|---|---|---|---|
| 0 | Schema, rollup, perf, seed | — | gate |
| 1 | Arrival & Stuck + Exceptions + gap alerts | Q1, Q3 | P0 |
| 2 | EDI/API Flow summary + dashboard assembly | Q2 | P0 |
| 3 | Lookup + Replay + Acknowledgments | Q4, Q8, Q5 | P1 |
| 4 | Partner SLA + Partner activity + Usage | Q6, Q7, Q9 | P2 |
| 5 | Response-SLA compliance + Diagnostics + Resolution KB | Q10, Q11 | P1/P2 |

---

# Phase 0 — Foundation (gate)

### 0.1 Tables
```sql
-- RAW EVENTS (drill-only; partitioned)  — child grain: one row per transaction-stage
CREATE TABLE txn_events (
  event_id bigserial, event_time timestamptz NOT NULL,
  interchange_id text,                                      -- FK to txn_files (the parent file)
  business_ref text,                                        -- null until parsed (pre-parse receipt rows)
  environment text,                                         -- prod|uat
  lob text, partner text, channel text,                    -- sftp|as2|van|api|mft|mq
  protocol text, direction text, doc_type text,            -- protocol: edi|api
  stage text, status text, reason_category text,           -- status: ok|failed|rejected|duplicate
  terminal boolean DEFAULT false, sla_due_at timestamptz,
  value_usd numeric, kchar numeric,                        -- kchar = fileSize/1000
  error_code text,
  replayed boolean DEFAULT false, replayed_at timestamptz, replay_count int DEFAULT 0,
  control_number text                                       -- ISA/GS/ST ctrl no.
) PARTITION BY RANGE (event_time);
CREATE INDEX ix_evt_time ON txn_events USING brin (event_time);
CREATE INDEX ix_evt_dims ON txn_events (partner, doc_type, status, event_time);
CREATE INDEX ix_evt_ref  ON txn_events (business_ref);
CREATE INDEX ix_evt_ctrl ON txn_events (control_number);
CREATE INDEX ix_evt_file ON txn_events (interchange_id);

-- FILES / INTERCHANGES (parent grain: one row per physical file, in or out)
CREATE TABLE txn_files (
  interchange_id text PRIMARY KEY, file_name text, environment text,
  partner text, channel text, protocol text, direction text,   -- in | out
  received_at timestamptz, completed_at timestamptz,
  status text,                                                 -- received|parsed|delivered|rejected
  reason_category text,                                        -- bad_input_file when de-envelope fails
  declared_txn_count int, isa_control text, gs_control text, value_usd numeric, kchar numeric
);
CREATE INDEX ix_files_recv ON txn_files (received_at);
CREATE INDEX ix_files_partner ON txn_files (partner, direction, status);

-- HOURLY ROLLUP (powers Q2, Q3, Q7, Q9)
CREATE TABLE txn_rollup_hourly (
  bucket timestamptz, environment text, lob text, partner text, channel text,
  protocol text, direction text, doc_type text, status text,
  txn_count bigint, value_sum numeric, kchar_sum numeric,
  failed_count bigint, rejected_count bigint, duplicate_count bigint, breached_count bigint,
  PRIMARY KEY (bucket, environment, lob, partner, channel, protocol, direction, doc_type, status)
);

-- CURRENT STATE (one row per live ref; powers Q1 stuck, Q4 lookup, Q6 completion)
CREATE TABLE txn_current (
  business_ref text PRIMARY KEY, environment text, lob text, partner text,
  channel text, protocol text, doc_type text,
  current_stage text, current_status text,
  first_event_at timestamptz, last_event_at timestamptz, terminal_at timestamptz,
  sla_due_at timestamptz, value_usd numeric, terminal boolean DEFAULT false,
  replayed boolean DEFAULT false, replayed_at timestamptz, replay_count int DEFAULT 0
);
CREATE INDEX ix_cur_open ON txn_current (terminal, last_event_at);
CREATE INDEX ix_cur_partner ON txn_current (partner, terminal);

-- OPERATIONAL TABLES (small; NiFi monitor jobs)
CREATE TABLE endpoint_health (channel text, endpoint text, partner text, environment text,
  status text, last_ok_at timestamptz, cert_expires_at date);
CREATE TABLE expected_feeds (partner text, doc_type text, channel text, environment text,
  expected_next_at timestamptz, grace_minutes int, last_seen_at timestamptz);
CREATE TABLE monitor_heartbeat (monitor_name text, channel text, environment text,
  last_run_at timestamptz, expected_interval_sec int);
CREATE TABLE pipeline_health (pipeline text, environment text, state text,
  queue_depth bigint, mq_depth bigint, consume_rate numeric, last_consumed_at timestamptz);

-- OPTIONAL CONFIG (Q6 $ impact)
CREATE TABLE partner_penalty (partner text, doc_type text, penalty_usd numeric);
```

### 0.2 `txn_current` upsert (NiFi PutDatabaseRecord per event)
```sql
INSERT INTO txn_current AS c (business_ref, environment, lob, partner, channel, protocol, doc_type,
  current_stage, current_status, first_event_at, last_event_at, terminal_at, sla_due_at, value_usd,
  terminal, replayed, replayed_at, replay_count)
VALUES (:ref,:env,:lob,:partner,:channel,:protocol,:doc,:stage,:status,:ts,:ts,
        CASE WHEN :terminal THEN :ts END,:sla,:val,:terminal,:replayed,:replayed_at,:replay_count)
ON CONFLICT (business_ref) DO UPDATE SET
  current_stage=:stage, current_status=:status, last_event_at=:ts,
  terminal_at=CASE WHEN :terminal THEN :ts ELSE c.terminal_at END,
  terminal=:terminal, replayed=c.replayed OR :replayed,
  replayed_at=COALESCE(:replayed_at,c.replayed_at), replay_count=GREATEST(c.replay_count,:replay_count);
-- nightly: move terminal rows older than N days to txn_current_history
```

### 0.2b File visibility — write-on-receipt + reconciliation (every incoming/outgoing file)
**Rule: write the file parent the instant it lands, BEFORE parse** — so a malformed file that never becomes a clean transaction is still visible (the AF-Newco "invalid structure / 2,000 empty rows" case). NiFi writes one `txn_files` row at receipt, then upserts `declared_txn_count`/`status` after de-envelope, and links each child `txn_events` row via `interchange_id`.
```sql
-- 1) at receipt (before parse), keyed on interchange_id, business_ref still null:
INSERT INTO txn_files (interchange_id, file_name, environment, partner, channel, protocol, direction,
  received_at, status, kchar)
VALUES (:icid,:fname,:env,:partner,:channel,:protocol,:dir, now(), 'received', :kchar)
ON CONFLICT (interchange_id) DO NOTHING;
-- 2) after de-envelope: set declared count + control numbers, or reject:
UPDATE txn_files SET status=:status, declared_txn_count=:n, isa_control=:isa, gs_control=:gs,
  reason_category=:reason, completed_at=CASE WHEN :status IN ('delivered','rejected') THEN now() END
WHERE interchange_id=:icid;
```
**Reconciliation dataset — transactions lost *inside* a file** (a thing transaction-level views alone can't catch):
```sql
SELECT f.interchange_id, f.file_name, f.partner, f.direction, f.declared_txn_count,
       count(e.event_id) FILTER (WHERE e.stage='received') AS actual_txns,
       f.declared_txn_count - count(e.event_id) FILTER (WHERE e.stage='received') AS missing_inside_file
FROM txn_files f
LEFT JOIN txn_events e ON e.interchange_id=f.interchange_id
WHERE f.declared_txn_count IS NOT NULL
GROUP BY 1,2,3,4,5
HAVING f.declared_txn_count > count(e.event_id) FILTER (WHERE e.stage='received');
```


### 0.3 Incremental rollup (every 2 min via pg_cron or NiFi timer)
```sql
DELETE FROM txn_rollup_hourly WHERE bucket >= date_trunc('hour', now()) - interval '2 hours';
INSERT INTO txn_rollup_hourly
SELECT date_trunc('hour', event_time), environment, lob, partner, channel, protocol, direction, doc_type, status,
  count(*), sum(value_usd), sum(kchar),
  count(*) FILTER (WHERE status='failed'), count(*) FILTER (WHERE status='rejected'),
  count(*) FILTER (WHERE status='duplicate'),
  count(*) FILTER (WHERE sla_due_at < now() AND NOT terminal)
FROM txn_events WHERE event_time >= date_trunc('hour', now()) - interval '2 hours'
GROUP BY 1,2,3,4,5,6,7,8,9;
```

### 0.4 Seed data (so the dashboard renders before NiFi is wired)
```sql
-- bulk volume for perf realism (~2M rows): randomized dims/status across 60 days
INSERT INTO txn_events (event_time, business_ref, environment, lob, partner, channel, protocol, direction,
  doc_type, stage, status, terminal, sla_due_at, value_usd, kchar, control_number)
SELECT now() - (random()*60||' days')::interval,
  'REF'||g, 'prod',
  (ARRAY['air','ocean','ground','customs','wh','home','po'])[1+floor(random()*7)],
  (ARRAY['Maersk','Werner','DHL','Target','Kroger','Hapag','Flextronics'])[1+floor(random()*7)],
  (ARRAY['sftp','as2','van','api','mq'])[1+floor(random()*5)],
  CASE WHEN random()<0.26 THEN 'api' ELSE 'edi' END,
  (ARRAY['in','out'])[1+floor(random()*2)],
  (ARRAY['214','204','997','810','856','990','850'])[1+floor(random()*7)],
  'acked',
  CASE WHEN random()<0.004 THEN 'failed' WHEN random()<0.006 THEN 'rejected'
       WHEN random()<0.008 THEN 'duplicate' ELSE 'ok' END,
  true, now(), (random()*5000)::int, (random()*40)::int, 'CN'||g
FROM generate_series(1,2000000) g;
-- then refresh the rollup (0.3 over a wider window once)
-- hand-seed the edge states the acceptance criteria test:
INSERT INTO expected_feeds VALUES
 ('Werner','204','sftp','prod', now()-interval '35 min', 15, now()-interval '90 min'),
 ('Kroger','856','sftp','prod', now()-interval '2 hour', 30, now()-interval '26 hour');
INSERT INTO pipeline_health VALUES
 ('walgreens-tl','prod','running', 20, 20, 0, now()-interval '40 min'),  -- hung
 ('air-main','prod','running', 12, 0, 240, now());
INSERT INTO endpoint_health VALUES
 ('sftp','crocs-edi','Crocs','prod','down', now()-interval '22 min', now()+5),   -- cert in 5d
 ('api','rxo-track','RXO','prod','degraded', now()-interval '2 min', NULL);
INSERT INTO monitor_heartbeat VALUES
 ('van-liveness','van','prod', now()-interval '18 min', 300),                    -- stale
 ('sftp-liveness','sftp','prod', now()-interval '20 sec', 300);
```
**Acceptance (Phase 0):** every dataset query in later phases returns rows; rollup count ≈ raw count; a partner filter on any rollup chart returns < 2s.

---

# Phase 1 — Arrival & Stuck (Q1) + Exceptions (Q3) + gap-closing alerts  · P0

### Datasets (virtual SQL)
```sql
-- missing feeds (the missing-204 detector)
SELECT partner, doc_type, channel, environment, expected_next_at, last_seen_at,
       round(extract(epoch FROM (now()-expected_next_at))/60) AS mins_overdue
FROM expected_feeds
WHERE now() > expected_next_at + make_interval(mins => grace_minutes)
  AND (last_seen_at IS NULL OR last_seen_at < expected_next_at);

-- hung pipeline (the retailer-204 signature)
SELECT pipeline, environment, queue_depth, mq_depth, consume_rate, last_consumed_at
FROM pipeline_health
WHERE state='running' AND (queue_depth>0 OR mq_depth>0) AND consume_rate=0;

-- sweep integrity (monitor-the-monitors)
SELECT monitor_name, channel, environment, last_run_at,
       (now()-last_run_at) > make_interval(secs => expected_interval_sec) AS is_stale
FROM monitor_heartbeat;

-- stuck / aging  (and listed-not-fetched = stage 'received')
SELECT business_ref, lob, partner, channel, doc_type, current_stage, environment,
       last_event_at, round(extract(epoch FROM (now()-last_event_at))/60) AS age_min, value_usd
FROM txn_current
WHERE NOT terminal AND now()-last_event_at > interval '20 minutes';

-- exceptions (Q3) — failed/rejected/duplicate distinct, from rollup; drill to raw
-- (chart reads txn_rollup_hourly filtered status IN ('failed','rejected'); duplicates counted, not alerted)
```
Dead/degraded: `SELECT * FROM endpoint_health WHERE status<>'up'`  ·  Cert expiry: `WHERE cert_expires_at < now()+interval '14 days'`

### Charts — tab "Arrival & Channel Health" (Q1)
| Chart | Type | Dataset | Notes |
|---|---|---|---|
| Monitors reporting | Big Number | sweep integrity | not-stale/total |
| Stale monitors | Table V2 | sweep integrity (is_stale) | should be empty |
| Channel health | Table V2 | endpoint_health grouped | cross-filter `channel` |
| Hung pipelines | Table V2 | hung pipeline | **drives Alert: hung** |
| Missing expected feeds | Table V2 | missing feeds | **drives Alert: missing** |
| Dead / degraded conns | Table V2 | endpoint down | **drives Alert: channel** |
| Landed not picked up | Table V2 | stuck (stage='received') | |
| Stuck / aging | Table V2 | stuck flows | sort by age |
| Cert / key expiry | Table V2 | cert expiry | color by days |

### Charts — tab "Exceptions" (Q3)
| Chart | Type | Dataset | Notes |
|---|---|---|---|
| Failed (period) | Big Number | rollup | `SUM(failed_count)` |
| Rejected (period) | Big Number | rollup | `SUM(rejected_count)` — **its own number** |
| Duplicates suppressed | Big Number | rollup | `SUM(duplicate_count)` |
| Exceptions by reason | Bar | rollup-by-reason* | reason_category |
| Exception queue | Table V2 | rollup status IN (failed,rejected) → drill to raw | partner, doc_type, reason, count |
\* add reason_category to the rollup GROUP BY, or build a small reason rollup.

### Alerts (Professional/trial)
| Alert | Basis | Cadence | Fire |
|---|---|---|---|
| Hung pipeline | hung dataset | 5 min | rows > 0 |
| Missing feed | missing feeds | 15 min | rows > 0 |
| Channel down | endpoint down | 5 min | rows > 0 |
| Rejected message | rollup rejected last hr | 15 min | count > 0 (**closes today's gap**) |
| Cert expiring | cert < 7d | daily | rows > 0 |

**Acceptance:** seeded hung pipeline fires the hung alert and shows on Q1; rejected shows as its own number/alert separate from failed; the stale VAN monitor shows as "silent" (not counted healthy); the missing Werner 204 appears within its window.

---

# Phase 2 — EDI/API Flow summary (Q2) + dashboard assembly · P0

### Charts — tab "Flow — EDI & API summary" (all read rollup)
| Chart | Type | Metric | Dim |
|---|---|---|---|
| Total transactions | Big Number w/ trend | `SUM(txn_count)` | time |
| EDI / API | 2× Big Number | `SUM(txn_count) FILTER protocol=…` | — |
| Auto-processed % | Big Number | `1-SUM(failed+rejected)/SUM(txn_count)` | — |
| Data volume (kchar) | Big Number | `SUM(kchar_sum)` | — |
| EDI vs API split | Pie | `SUM(txn_count)` | protocol |
| Volume by message type | Bar | `SUM(txn_count)` | doc_type, breakdown protocol |
| Message-type volumetric grid | Table V2 (AG Grid) | count, %, failed, rejected + mini-bar | doc_type, protocol, direction |
| Throughput over time | Time-series bar | `SUM(txn_count)` | bucket, breakdown protocol |
| Inbound vs outbound | Bar | `SUM(txn_count)` | direction |
| Volume by partner (top 20) | Bar | `SUM(txn_count)` | partner |
| Volume by LOB | Bar | `SUM(txn_count)` | lob |

### Assembly
- **Native filters (all tabs):** Time · Environment · LOB · Partner · Protocol · Channel · Doc type.
- **Cross-filtering** on bars/pie; **Drill-to-detail** to row-limited `txn_events`.
- **Caching:** per-chart TTL 120s; dashboard auto-refresh 1–2 min; async on.

**Acceptance:** partner filter re-scopes all charts < 2s; protocol pie + message-type grid match totals; drill opens filtered raw rows.

---

# Phase 3 — Lookup (Q4) + Replay (Q8) + Acknowledgments (Q5) · P1

# Phase 3 — Files + Transaction lookup (Q4) + Replay (Q8) + Acknowledgments (Q5) · P1

Two grains in the UI: **files** (parent) and **transactions** (child), linked by `interchange_id`. A user can start from either and pivot to the other.

### Datasets
```sql
-- Q4 FILE lookup: txn_files with a file_name / interchange_id native filter (in & out)
SELECT interchange_id, file_name, direction, partner, channel, protocol, received_at, completed_at,
       status, reason_category, declared_txn_count, kchar FROM txn_files;

-- Q4 FILE → child transactions (drill: filter txn_events by the selected interchange_id)
SELECT interchange_id, business_ref, doc_type, direction, current_stage:=stage, status,
       reason_category, event_time FROM txn_events WHERE interchange_id = :interchange_id;

-- Q4 incoming vs outgoing file feed (live file activity)
SELECT direction, status, count(*) AS files, sum(declared_txn_count) AS txns, sum(kchar) AS kchar
FROM txn_files WHERE received_at >= now()-interval '24 hours' GROUP BY 1,2;

-- reconciliation: txns lost inside a file  (see §0.2b) -> drives a "files missing transactions" table

-- Q4 transaction lookup: txn_current with a business_ref native filter (one ref's state + replay + its parent interchange_id)
-- Q4 step history: txn_events for that ref, ordered by event_time

-- Q8 replayed:
SELECT business_ref, partner, doc_type, replayed_at, replay_count, current_status
FROM txn_current WHERE replayed = true;

-- Q5 acknowledgments: match outbound interchanges to their 997/CONTRL acks
SELECT o.business_ref, o.partner, o.doc_type, o.event_time AS sent_at,
       a.event_time AS ack_at, a.status AS ack_status,
       CASE WHEN a.event_time IS NULL AND now() > o.sla_due_at THEN 'missing'
            WHEN a.status='rejected' THEN 'rejected'
            WHEN a.event_time IS NOT NULL THEN 'received'
            ELSE 'pending' END AS fa_state
FROM txn_events o
LEFT JOIN txn_events a
  ON a.control_number=o.control_number AND a.doc_type IN ('997','CONTRL') AND a.partner=o.partner
WHERE o.direction='out' AND o.doc_type NOT IN ('997','CONTRL');
```

### Charts
| Tab | Chart | Type | Dataset |
|---|---|---|---|
| **Files** | Incoming vs outgoing files (24h) | Bar | file feed (direction × status) |
| **Files** | File explorer | Table V2 | txn_files + file_name/interchange filter (status, declared count) |
| **Files** | File → transactions | Table V2 | child txns by `interchange_id` (drill-by from File explorer) |
| **Files** | Files missing transactions | Table V2 | reconciliation (declared > actual) — **lost-inside-file** |
| **Files** | Rejected at receipt (pre-parse) | Table V2 | txn_files status='rejected' (malformed before they became txns) |
| Lookup | Transaction status | Table V2 | txn_current + business_ref filter (replayed badge, parent file link) |
| Lookup | Step history | Table V2 | txn_events by ref, time-ordered |
| Lookup | Replays today | Big Number | replayed |
| Lookup | Replayed messages | Table V2 | replayed (deep-link col → NiFi) |
| Acks | Missing / rejected acks | Big Number ×2 | acks (fa_state) |
| Acks | FA tracking | Table V2 | acks, filter fa_state |

UI behavior: **File explorer → drill-by `interchange_id` → File → transactions** (parent to children); the transaction-status row carries `interchange_id` as a clickable cross-filter back to its parent file. Native filters: direction (in/out), file status, partner, time.

**Alert:** missing/rejected acks, 30 min; **files rejected at receipt**, 10 min; **files missing transactions** (declared>actual), 15 min.
**Acceptance:** an incoming file appears the instant it lands (before parse), even if malformed; selecting a file shows its child transactions; a file declaring 200 txns with 187 children flags 13 missing; searching a transaction ref shows its parent file; replay + ack criteria as before.

---

# Phase 4 — Partner SLA (Q6) + Partner activity (Q7) + Usage (Q9) · P2

### Datasets
```sql
-- Q6 partner SLA scorecard (over terminal rows in txn_current — fast)
SELECT c.partner,
  count(*) AS total,
  count(*) FILTER (WHERE terminal_at <= sla_due_at) AS met,
  count(*) FILTER (WHERE terminal_at > sla_due_at OR (terminal_at IS NULL AND now()>sla_due_at)) AS missed,
  round(avg(extract(epoch FROM (terminal_at-first_event_at))/60)) AS avg_min,
  round(min(extract(epoch FROM (terminal_at-first_event_at))/60)) AS min_min,
  round(max(extract(epoch FROM (terminal_at-first_event_at))/60)) AS max_min,
  coalesce(sum(p.penalty_usd) FILTER (WHERE terminal_at > sla_due_at),0) AS penalty_usd
FROM txn_current c LEFT JOIN partner_penalty p ON p.partner=c.partner
GROUP BY c.partner;

-- Q7 partner activity, period over period
WITH cur AS (SELECT partner, sum(txn_count) v, sum(failed_count+rejected_count) e
             FROM txn_rollup_hourly WHERE bucket >= now()-interval '7 days' GROUP BY partner),
     prv AS (SELECT partner, sum(txn_count) v FROM txn_rollup_hourly
             WHERE bucket >= now()-interval '14 days' AND bucket < now()-interval '7 days' GROUP BY partner)
SELECT cur.partner, cur.v AS volume, cur.e AS exceptions,
       round(100.0*(cur.v-prv.v)/nullif(prv.v,0)) AS pct_change
FROM cur LEFT JOIN prv USING (partner) ORDER BY cur.v DESC;

-- Q9 usage (billing volumetrics)
SELECT date_trunc('month',bucket) AS month, partner, protocol, doc_type, channel,
       sum(txn_count) AS txns, sum(kchar_sum) AS kchar
FROM txn_rollup_hourly GROUP BY 1,2,3,4,5;
```

### Charts
| Tab | Chart | Type | Dataset |
|---|---|---|---|
| Scorecard | Partner SLA | Table V2 | scorecard (%met, %missed, avg/min/max, penalty) |
| Scorecard | %Met by partner | Bar | scorecard |
| Partner activity | Top partners by volume | Bar | activity |
| Partner activity | Top by exceptions | Bar | activity |
| Partner activity | Change vs prior | Table V2 | activity (pct_change) |
| Usage | Monthly volume | Table V2 (export) | usage |
| Usage | Volume trend | Time-series | usage |

**Acceptance:** scorecard sortable per partner with met/missed + completion stats; partner activity shows top-N + period-over-period; usage exports monthly totals by partner/protocol/doc_type.

---

# Phase 5 — Response-SLA compliance (Q10) + Diagnostics (Q11) · P1/P2

Two capabilities here, plus the user-driven layer adapted from Fabrik's Operator Agent. Boundary first: Superset **diagnoses and decides**; autonomous resolution, self-healing, and the learning flywheel stay in Fabrik/NiFi. Everything below is read-only over data you already capture.

### 5A · Response-SLA compliance — paired trigger→response (Q10)
The class current SLA fields miss: "990 within 30 min of the 204," "997 within 15 min," "855 within the hour." A rules table + a graded pairing.
```sql
CREATE TABLE sla_rules(
  rule_id serial PRIMARY KEY, name text, environment text, partner text,   -- partner null = all
  trigger_doc_type text, trigger_direction text,
  response_doc_type text, response_direction text,
  correlation_key text DEFAULT 'business_ref',
  threshold_minutes int
);
INSERT INTO sla_rules(name,environment,partner,trigger_doc_type,trigger_direction,response_doc_type,response_direction,threshold_minutes) VALUES
 ('204→990 tender response','prod',NULL,'204','in','990','out',30),
 ('850→855 PO ack',         'prod',NULL,'850','in','855','out',60),
 ('inbound→997 func ack',   'prod',NULL, NULL,'in','997','out',15),
 ('204→214 pickup milestone','prod',NULL,'204','in','214','in',240);
```
Grading query (materialize as `sla_pairs` when the response lands, or scheduled — don't scan live):
```sql
SELECT r.rule_id, r.name, t.partner, t.business_ref, t.event_time AS trigger_at,
  resp.event_time AS response_at,
  round(extract(epoch FROM (resp.event_time - t.event_time))/60) AS elapsed_min, r.threshold_minutes,
  CASE
    WHEN resp.event_time IS NOT NULL
         AND extract(epoch FROM (resp.event_time-t.event_time))/60 <= r.threshold_minutes THEN 'met'
    WHEN resp.event_time IS NOT NULL THEN 'missed'
    WHEN now()-t.event_time > make_interval(mins=>r.threshold_minutes)     THEN 'missed'
    WHEN now()-t.event_time > make_interval(mins=>r.threshold_minutes*0.8) THEN 'at_risk'  -- clock running
    ELSE 'pending' END AS sla_state
FROM sla_rules r
JOIN txn_events t ON t.doc_type=r.trigger_doc_type AND t.direction=r.trigger_direction
  AND (r.partner IS NULL OR t.partner=r.partner) AND t.environment=r.environment
LEFT JOIN LATERAL (
  SELECT event_time FROM txn_events x
  WHERE x.doc_type=r.response_doc_type AND x.direction=r.response_direction
    AND x.business_ref=t.business_ref AND x.event_time >= t.event_time
  ORDER BY x.event_time LIMIT 1) resp ON true;
```
Charts — tab "SLA Compliance":
| Chart | Type | Metric |
|---|---|---|
| Compliance % by rule | Bar / Big Number | met / (met+missed) |
| Breaches | Table V2 | sla_state='missed' |
| Responses due soon | Table V2 | sla_state='at_risk' (**proactive worklist**) |
| Elapsed-time distribution | Histogram | elapsed_min per rule |

**Alert — at-risk response:** `sla_state='at_risk'`, 5 min → fires *before* the breach (the response-SLA equivalent of catching a missing feed).

### 5B · Diagnostic views (Q11)
```sql
-- Failure signature clustering: is this 100 incidents or one root cause?
SELECT reason_category, error_code, stage, partner,
  count(*) AS occurrences, min(event_time) AS onset, max(event_time) AS latest,
  count(DISTINCT business_ref) AS refs, sum(value_usd) AS value_exposed
FROM txn_events WHERE status IN ('failed','rejected')
GROUP BY 1,2,3,4 ORDER BY occurrences DESC;

-- Replay outcome: a reprocessed message that re-failed (more urgent than first-time fail)
SELECT business_ref, partner, doc_type, replay_count, current_status
FROM txn_current WHERE replay_count>0 AND current_status IN ('failed','rejected');

-- Duplicate source: which partner/interchange sends dupes
SELECT partner, control_number, count(*) FROM txn_events
GROUP BY 1,2 HAVING count(*)>1 ORDER BY 3 DESC;

-- Deploy correlation: overlay as a Superset annotation layer on the failure-onset time-series
CREATE TABLE deploys(deployed_at timestamptz, component text, note text);
```
Charts — tab "Diagnostics": signature table (clusters incidents → root causes with onset), **partner-vs-platform attribution** donut (`reason_category` ours/theirs), re-failures table, recurrence time-series with `deploys` annotation layer (the "suspected weekend upgrade" check).

### 5C · Resolution knowledge base (from Fabrik's Operator Agent)
Fabrik's "error-resolution from knowledge base," translated to a deterministic KB lookup — no AI, surfaced inline on every exception.
```sql
CREATE TABLE diagnostic_rules(
  rule_id serial PRIMARY KEY, partner text,            -- null = all; per-partner override wins
  reason_category text, error_code text,
  likely_cause text, suggested_action text, runbook_url text
);
-- exception queue joins to this → each failure shows likely cause + suggested fix + runbook link
SELECT e.business_ref, e.partner, e.reason_category, e.error_code,
       coalesce(dp.likely_cause, dg.likely_cause)     AS likely_cause,
       coalesce(dp.suggested_action, dg.suggested_action) AS suggested_action,
       coalesce(dp.runbook_url, dg.runbook_url)       AS runbook_url
FROM txn_events e
LEFT JOIN diagnostic_rules dp ON dp.partner=e.partner
  AND dp.reason_category=e.reason_category AND dp.error_code=e.error_code
LEFT JOIN diagnostic_rules dg ON dg.partner IS NULL
  AND dg.reason_category=e.reason_category AND dg.error_code=e.error_code
WHERE e.status IN ('failed','rejected');
```

### 5D · User-driven diagnostics (adapted from Fabrik Operator)
- **Natural-language query:** Preset Chatbot / Preset MCP — plain-English → chart against your datasets, respecting RLS. This is Fabrik's Operator NL-query capability, delivered natively by Preset.
- **Self-service:** drill-by + cross-filter + saved/shared investigations (parameterized queries users keep).
- **Stays in Fabrik/NiFi (not Superset):** autonomous resolution, self-healing pipelines, SLA *prediction*, and the learning flywheel. The cockpit hands off to them via deep-link.

**Acceptance (Phase 5):** a 204 with no 990 at 25 min shows `at_risk` and alerts before the 30-min breach; an exception row shows its KB likely-cause + suggested action + runbook; a re-failed replay is flagged; failure signatures cluster many incidents into N root causes with onset times; a deploy annotation lines up against an error spike.

---

- **RLS:** add a row-level security rule on `partner` for partner-scoped dashboards (Q6 external sharing later).
- **preset-cli:** keep all datasets/charts/dashboards as YAML; `preset-cli superset sync native ./assets` to import; re-runnable, version-controlled.
- **Environments:** the `environment` filter defaults to prod; switch to uat to run the same Q1 liveness against UAT pipelines.
- **NiFi dependency:** see brief §7a — every field maps to a NiFi mechanism; verify the 3 gating facts (REST reachable, provenance retained, permission to add reporting tasks) before Phase 1.

# Out of scope
No write-back/actions in Superset (reprocess lives in NiFi, deep-linked) · no ML/anomaly/baseline intel · no custom HTML UI · semantic layer not required.
