-- ============================================================================
-- Phase 3-5 targeted seed — clean, fixed demo states for lookup/replay/acks
-- (Q4/Q8/Q5), response-SLA (Q10), and diagnostics + resolution KB (Q11).
-- Idempotent: removes its own refs first, then re-inserts relative to now().
-- Run AFTER 01_seed.sql. Rebuilds the rollup at the end.
-- ============================================================================

-- 0. clean this file's demo refs (so re-runs don't duplicate)
DELETE FROM txn_events WHERE business_ref LIKE 'ACK-%'
   OR business_ref LIKE 'CROCS-FAIL%' OR business_ref LIKE 'DUP-T%'
   OR business_ref IN ('LOAD-MET-1','LOAD-MISS-1');

-- ============================================================================
-- 1. ACKNOWLEDGMENTS (Q5) — matched / missing / rejected 997s
-- ============================================================================
-- received: 810 out -> 997 in, same partner+control
INSERT INTO txn_events (event_time, business_ref, environment, lob, partner, channel, protocol,
  direction, doc_type, stage, status, terminal, sla_due_at, control_number) VALUES
 (now()-interval '50 min','ACK-RCV-1','prod','po','Maersk','as2','edi','out','810','delivered','delivered',true, now()-interval '35 min','CTRL-ACK-1'),
 (now()-interval '45 min','ACK-RCV-1','prod','po','Maersk','as2','edi','in','997','acked','ok',true, now()-interval '35 min','CTRL-ACK-1'),
-- missing: 850 out, no 997, past its window
 (now()-interval '90 min','ACK-MISS-1','prod','po','Werner','sftp','edi','out','850','delivered','delivered',true, now()-interval '30 min','CTRL-ACK-2'),
-- rejected: 856 out -> 997 in with status rejected
 (now()-interval '40 min','ACK-REJ-1','prod','wh','DHL','as2','edi','out','856','delivered','delivered',true, now()-interval '25 min','CTRL-ACK-3'),
 (now()-interval '35 min','ACK-REJ-1','prod','wh','DHL','as2','edi','in','997','acked','rejected',true, now()-interval '25 min','CTRL-ACK-3');

-- ============================================================================
-- 2. RESPONSE-SLA pairs (Q10) — met / missed (at_risk already seeded as LOAD-ATRISK-204)
-- ============================================================================
INSERT INTO txn_events (event_time, business_ref, environment, lob, partner, channel, protocol,
  direction, doc_type, stage, status, terminal, sla_due_at, value_usd, control_number) VALUES
 -- met: 204 in at -60m, 990 out at -40m (elapsed 20m < 30m threshold)
 (now()-interval '60 min','LOAD-MET-1','prod','ground','Werner','sftp','edi','in','204','received','ok',false, now()-interval '30 min',9800,'CTRL-MET-1'),
 (now()-interval '40 min','LOAD-MET-1','prod','ground','Werner','sftp','edi','out','990','delivered','delivered',true, now()-interval '30 min',9800,'CTRL-MET-1'),
 -- missed: 204 in at -90m, no 990 (well past 30m)
 (now()-interval '90 min','LOAD-MISS-1','prod','ground','Kroger','sftp','edi','in','204','received','ok',false, now()-interval '60 min',15200,'CTRL-MISS-1');

-- ============================================================================
-- 3. DIAGNOSTICS (Q11) — a failure-signature cluster + duplicate source
-- ============================================================================
-- 60 failures sharing a signature (Crocs / connectivity / AUTH / delivered),
-- onset spread over the last 3h -> top cluster, matches the Crocs KB rule.
INSERT INTO txn_events (event_time, business_ref, environment, lob, partner, channel, protocol,
  direction, doc_type, stage, status, reason_category, error_code, terminal, value_usd, control_number)
SELECT now() - (random()*180||' min')::interval,
  'CROCS-FAIL'||g, 'prod','wh','Crocs','sftp','edi','out','856','delivered','failed',
  'connectivity','AUTH', false, (random()*3000)::int, 'CN-CROCS-'||g
FROM generate_series(1,60) g;

-- duplicate source: Target sends the same control number 3x
INSERT INTO txn_events (event_time, business_ref, environment, lob, partner, channel, protocol,
  direction, doc_type, stage, status, reason_category, terminal, control_number) VALUES
 (now()-interval '30 min','DUP-T1a','prod','wh','Target','van','edi','in','856','received','duplicate','duplicate',true,'DUP-1'),
 (now()-interval '29 min','DUP-T1b','prod','wh','Target','van','edi','in','856','received','duplicate','duplicate',true,'DUP-1'),
 (now()-interval '28 min','DUP-T1c','prod','wh','Target','van','edi','in','856','received','duplicate','duplicate',true,'DUP-1');

-- ============================================================================
-- 4. rebuild the rollup so Q2/Q3/Q7/Q9 totals include the new rows
-- ============================================================================
TRUNCATE txn_rollup_hourly;
INSERT INTO txn_rollup_hourly
SELECT date_trunc('hour', event_time), environment, lob, partner, channel, protocol, direction, doc_type, status,
  count(*), sum(value_usd), sum(kchar),
  count(*) FILTER (WHERE status='failed'), count(*) FILTER (WHERE status='rejected'),
  count(*) FILTER (WHERE status='duplicate'),
  count(*) FILTER (WHERE sla_due_at < now() AND NOT terminal)
FROM txn_events
GROUP BY 1,2,3,4,5,6,7,8,9
ON CONFLICT DO NOTHING;
