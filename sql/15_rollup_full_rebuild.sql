-- 15_rollup_full_rebuild.sql
-- ===========================================================================
-- Full rebuild of txn_rollup_hourly from public.txn_events.
--
-- Why: the precomputed rollup (feeding Home / EDI / API) had drifted 1,059 rows
-- (0.35%) short of txn_events — isolated to doc types 214 (+629) and 850 (+430)
-- that were added to txn_events after the rollup was last seeded. The Consignment
-- and Transaction tabs read txn_events directly, so the rollup-backed tabs were
-- under-counting against them. This recomputes the WHOLE history once so every
-- tab reconciles to the same 300,263 transactions.
--
-- Uses the EXACT aggregation contract of refresh_txn_rollup() (sql/02), incl. the
-- now()-based SLA breach test, so steady-state incremental refreshes stay
-- consistent with this baseline. Idempotent: safe to re-run.
-- ===========================================================================
TRUNCATE txn_rollup_hourly;

INSERT INTO txn_rollup_hourly
SELECT date_trunc('hour', event_time), environment, lob, partner, channel, protocol, direction, doc_type, status,
    count(*), sum(value_usd), sum(kchar),
    count(*) FILTER (WHERE status='failed'),
    count(*) FILTER (WHERE status='rejected'),
    count(*) FILTER (WHERE status='duplicate'),
    count(*) FILTER (WHERE sla_due_at < now() AND NOT terminal)
FROM txn_events
GROUP BY 1,2,3,4,5,6,7,8,9;
