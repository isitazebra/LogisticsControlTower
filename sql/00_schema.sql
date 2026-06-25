-- ============================================================================
-- Integration Visibility Cockpit — Phase 0 Schema (Neon Postgres)
-- "Postgres is the contract." NiFi (or the seed) is the producer; Superset is a
-- read-only consumer. Run this FIRST, then 01_seed.sql.
--
-- Design rules encoded here (see docs/cockpit-product-brief §6):
--   * txn_events is partitioned by event_time so drill queries hit one partition.
--   * Aggregate charts read txn_rollup_hourly / vw_shipment*, never raw events.
--   * Indexes target the access paths, not every column.
-- ============================================================================

-- ----------------------------------------------------------------------------
-- RAW EVENTS  (child grain: one row per transaction-stage; drill-only)
--   status taxonomy: ok | delivered | failed | rejected | duplicate
--   reason_category: bad_input_file | mapping_defect | connectivity |
--                    transform_error | delivery_error | ack_timeout |
--                    rejected_by_partner | duplicate | hung_pipeline | unknown
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS txn_events (
  event_id        bigint GENERATED ALWAYS AS IDENTITY,
  event_time      timestamptz NOT NULL,
  interchange_id  text,                         -- FK-by-convention to txn_files (parent file)
  business_ref    text,                         -- null until parsed (pre-parse receipt rows)
  environment     text,                         -- prod | uat
  lob             text,
  partner         text,
  channel         text,                         -- sftp | as2 | van | api | mft | mq
  protocol        text,                         -- edi | api
  direction       text,                         -- in | out
  doc_type        text,
  stage           text,                         -- received|validated|transformed|delivered|acked|customs...
  status          text,                         -- ok|delivered|failed|rejected|duplicate
  reason_category text,
  terminal        boolean DEFAULT false,
  sla_due_at      timestamptz,
  value_usd       numeric,
  kchar           numeric,                       -- fileSize / 1000 (EDI volume unit)
  error_code      text,
  replayed        boolean DEFAULT false,
  replayed_at     timestamptz,
  replay_count    int DEFAULT 0,
  control_number  text                           -- ISA/GS/ST control no. -> duplicate detection
) PARTITION BY RANGE (event_time);

-- Indexes (propagate to every partition automatically).
CREATE INDEX IF NOT EXISTS ix_evt_time ON txn_events USING brin (event_time);
CREATE INDEX IF NOT EXISTS ix_evt_dims ON txn_events (partner, doc_type, status, event_time);
CREATE INDEX IF NOT EXISTS ix_evt_ref  ON txn_events (business_ref);
CREATE INDEX IF NOT EXISTS ix_evt_ctrl ON txn_events (control_number);
CREATE INDEX IF NOT EXISTS ix_evt_file ON txn_events (interchange_id);

-- Monthly partitions spanning the seed window (now-3mo .. now+2mo) + a DEFAULT
-- catch-all so an out-of-range insert never errors. Drop/archive old partitions
-- instead of DELETE as history grows.
DO $$
DECLARE
  start_month date := date_trunc('month', now() - interval '3 months')::date;
  m           date;
  pname       text;
BEGIN
  FOR i IN 0..5 LOOP
    m := (start_month + (i || ' months')::interval)::date;
    pname := 'txn_events_' || to_char(m, 'YYYYMM');
    EXECUTE format(
      'CREATE TABLE IF NOT EXISTS %I PARTITION OF txn_events FOR VALUES FROM (%L) TO (%L)',
      pname, m, (m + interval '1 month')::date);
  END LOOP;
  EXECUTE 'CREATE TABLE IF NOT EXISTS txn_events_default PARTITION OF txn_events DEFAULT';
END $$;

-- ----------------------------------------------------------------------------
-- FILES / INTERCHANGES  (parent grain: one row per physical file, in or out)
--   Written at RECEIPT before parse, so malformed files are visible even if
--   they never become clean transactions.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS txn_files (
  interchange_id   text PRIMARY KEY,
  file_name        text,
  environment      text,
  partner          text,
  channel          text,
  protocol         text,
  direction        text,                          -- in | out
  received_at      timestamptz,
  completed_at     timestamptz,
  status           text,                          -- received|parsed|delivered|rejected
  reason_category  text,                          -- bad_input_file when de-envelope fails
  declared_txn_count int,
  isa_control      text,
  gs_control       text,
  value_usd        numeric,
  kchar            numeric
);
CREATE INDEX IF NOT EXISTS ix_files_recv    ON txn_files (received_at);
CREATE INDEX IF NOT EXISTS ix_files_partner ON txn_files (partner, direction, status);

-- ----------------------------------------------------------------------------
-- HOURLY ROLLUP  (powers Q2/Q3/Q7/Q9 — every aggregate chart reads this)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS txn_rollup_hourly (
  bucket          timestamptz,
  environment     text,
  lob             text,
  partner         text,
  channel         text,
  protocol        text,
  direction       text,
  doc_type        text,
  status          text,
  txn_count       bigint,
  value_sum       numeric,
  kchar_sum       numeric,
  failed_count    bigint,
  rejected_count  bigint,
  duplicate_count bigint,
  breached_count  bigint,
  PRIMARY KEY (bucket, environment, lob, partner, channel, protocol, direction, doc_type, status)
);

-- ----------------------------------------------------------------------------
-- CURRENT STATE  -- removed. In this model every transaction is a single
-- immutable row in txn_events (1 event per business_ref), so "current state"
-- IS the row. There is no event history to project, hence no separate
-- txn_current table. Anything that needs current state filters txn_events
-- directly (e.g. open == terminal=false); per-shipment rollups are the
-- vw_shipment* views. NiFi writes txn_events ONLY.
-- ----------------------------------------------------------------------------

-- ----------------------------------------------------------------------------
-- OPERATIONAL TABLES  (small; written by NiFi monitor jobs / seeded for demo)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ops_endpoint_health (
  channel text, endpoint text, partner text, environment text,
  status text, last_ok_at timestamptz, cert_expires_at date
);
CREATE TABLE IF NOT EXISTS ops_expected_feeds (
  partner text, doc_type text, channel text, environment text,
  expected_next_at timestamptz, grace_minutes int, last_seen_at timestamptz
);
CREATE TABLE IF NOT EXISTS ops_monitor_heartbeat (
  monitor_name text, channel text, environment text,
  last_run_at timestamptz, expected_interval_sec int
);
CREATE TABLE IF NOT EXISTS ops_pipeline_health (
  pipeline text, environment text, state text,
  queue_depth bigint, mq_depth bigint, consume_rate numeric, last_consumed_at timestamptz
);

-- ----------------------------------------------------------------------------
-- CONFIG TABLES  (SLA rules, resolution KB, deploys, penalties)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS sla_rules (
  rule_id           int GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  name              text,
  environment       text,
  partner           text,                    -- null = all partners
  trigger_doc_type  text,
  trigger_direction text,
  response_doc_type text,
  response_direction text,
  correlation_key   text DEFAULT 'business_ref',
  threshold_minutes int
);
CREATE TABLE IF NOT EXISTS diagnostic_rules (
  rule_id         int GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  partner         text,                       -- null = all; per-partner override wins
  reason_category text,
  error_code      text,
  likely_cause    text,
  suggested_action text,
  runbook_url     text
);
CREATE TABLE IF NOT EXISTS deploys (
  deployed_at timestamptz, component text, note text
);
CREATE TABLE IF NOT EXISTS ref_partner_penalty (
  partner text, doc_type text, penalty_usd numeric
);
