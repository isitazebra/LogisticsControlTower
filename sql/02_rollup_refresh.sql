-- ============================================================================
-- Incremental rollup refresh + helper views.
-- Run AFTER 00_schema.sql. The seed (01_seed.sql) already builds the rollup once;
-- this installs the repeatable incremental refresh used in steady state.
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

-- ----------------------------------------------------------------------------
-- Reconciliation view: transactions lost INSIDE a file (declared > actual).
-- A thing transaction-level views alone can't catch. Powers Q4 "files missing
-- transactions". Small result set -> safe as a view.
-- ----------------------------------------------------------------------------
CREATE OR REPLACE VIEW v_files_missing_txns AS
SELECT f.interchange_id, f.file_name, f.partner, f.direction, f.declared_txn_count,
       count(e.event_id) FILTER (WHERE e.stage='received') AS actual_txns,
       f.declared_txn_count - count(e.event_id) FILTER (WHERE e.stage='received') AS missing_inside_file
FROM txn_files f
LEFT JOIN txn_events e ON e.interchange_id = f.interchange_id
WHERE f.declared_txn_count IS NOT NULL
  AND f.direction = 'in'          -- reconcile what we received & parsed; outbound is generated, not declared-vs-actual
GROUP BY 1,2,3,4,5
HAVING f.declared_txn_count > count(e.event_id) FILTER (WHERE e.stage='received');
