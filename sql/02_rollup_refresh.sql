-- ============================================================================
-- Incremental rollup refresh.
-- Run AFTER 00_schema.sql. gen_shipment_world.py builds the rollup once (full
-- rebuild via the same aggregation contract); this installs the repeatable
-- incremental refresh used in steady state.
-- Cardinal rule: recompute only the last 1-2 hours, never the whole history.
-- ============================================================================

-- Incremental refresh: delete-and-reinsert the last 2 hours of buckets.
CREATE OR REPLACE FUNCTION refresh_txn_rollup() RETURNS void AS $$
BEGIN
  DELETE FROM txn_rollup_hourly
   WHERE bucket >= date_trunc('hour', now()) - interval '2 hours';

  INSERT INTO txn_rollup_hourly
  SELECT date_trunc('hour', event_time), environment, lob, partner, channel, protocol, direction, doc_type, status,
    count(*), sum(value_usd), sum(kchar),
    count(*) FILTER (WHERE status='failed'),
    count(*) FILTER (WHERE status='rejected'),
    count(*) FILTER (WHERE status='duplicate'),
    count(*) FILTER (WHERE sla_due_at < now() AND NOT terminal)
  FROM txn_events
  WHERE event_time >= date_trunc('hour', now()) - interval '2 hours'
  GROUP BY 1,2,3,4,5,6,7,8,9;
END;
$$ LANGUAGE plpgsql;

-- Optional: schedule every 2 minutes via pg_cron (Neon supports it once enabled).
-- Uncomment after `CREATE EXTENSION IF NOT EXISTS pg_cron;` is permitted:
--   SELECT cron.schedule('refresh-txn-rollup', '*/2 * * * *', $$SELECT refresh_txn_rollup();$$);
