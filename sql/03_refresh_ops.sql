-- ============================================================================
-- Demo freshness refresh — re-stamp time-sensitive operational rows to now()
-- so the intended Q1/Q10 edge cases stay crisp regardless of when you demo.
--
-- WHY: the seed writes absolute now() timestamps at load time. As wall-clock
-- advances, "fresh" monitors decay into "stale" and "at-risk" clocks breach.
-- In production NiFi keeps these live. With no live writer (and no pg_cron on
-- this Neon role), we instead give the HEALTHY signals a long interval / far
-- horizon so a single run keeps the demo correct for ~24h, while the
-- deliberately-broken signals (van-liveness silent, Werner/Kroger feeds
-- missing, walgreens-tl hung) stay broken.
--
-- Wrapped as refresh_demo_ops() so NiFi or pg_cron can drive it on a schedule
-- later for true minute-by-minute liveness. Idempotent.
-- ============================================================================

CREATE OR REPLACE FUNCTION refresh_demo_ops() RETURNS void AS $$
BEGIN
  -- Monitor heartbeats: van-liveness STALE (short interval, old run); the rest
  -- healthy with a long interval so they stay "reporting" between refreshes.
  UPDATE monitor_heartbeat SET last_run_at = now() - interval '18 min', expected_interval_sec = 300
    WHERE monitor_name = 'van-liveness';
  UPDATE monitor_heartbeat SET last_run_at = now(), expected_interval_sec = 86400
    WHERE monitor_name IN ('sftp-liveness','as2-liveness','api-liveness','mq-depth');

  -- Endpoint health: crocs down, rxo degraded, rest fresh + up. Certs by days.
  UPDATE endpoint_health SET last_ok_at=now()-interval '22 min', cert_expires_at=(now()+interval '5 day')::date  WHERE endpoint='crocs-edi';
  UPDATE endpoint_health SET last_ok_at=now()-interval '2 min',  cert_expires_at=(now()+interval '3 day')::date  WHERE endpoint='rxo-track';
  UPDATE endpoint_health SET last_ok_at=now()-interval '20 sec', cert_expires_at=(now()+interval '11 day')::date WHERE endpoint='maersk-as2';
  UPDATE endpoint_health SET last_ok_at=now()-interval '15 sec', cert_expires_at=(now()+interval '120 day')::date WHERE endpoint='target-sftp';
  UPDATE endpoint_health SET last_ok_at=now()-interval '30 sec', cert_expires_at=(now()+interval '90 day')::date  WHERE endpoint='crocs-edi-uat';

  -- Pipeline health: walgreens-tl HUNG (state+queue+rate, time-independent); others consuming.
  UPDATE pipeline_health SET last_consumed_at=now()-interval '40 min' WHERE pipeline='walgreens-tl';
  UPDATE pipeline_health SET last_consumed_at=now()                   WHERE pipeline IN ('air-main','ocean-main');

  -- Expected feeds: Werner 204 + Kroger 856 MISSING (past); Maersk 214 healthy (far future).
  UPDATE expected_feeds SET expected_next_at=now()-interval '35 min', last_seen_at=now()-interval '95 min'
    WHERE partner='Werner' AND doc_type='204';
  UPDATE expected_feeds SET expected_next_at=now()-interval '2 hour', last_seen_at=now()-interval '26 hour'
    WHERE partner='Kroger' AND doc_type='856';
  UPDATE expected_feeds SET expected_next_at=now()+interval '1 day', last_seen_at=now()-interval '20 min'
    WHERE partner='Maersk' AND doc_type='214';

  -- Stuck flow stays stuck (not terminal, aged > 20 min).
  UPDATE txn_current SET last_event_at=now()-interval '45 min', terminal=false, current_stage='transform'
    WHERE business_ref='LOAD-STUCK-001';

  -- At-risk 204: 25 min into a 30-min SLA -> Q10 'at_risk' (clock running, not breached).
  UPDATE txn_events SET event_time=now()-interval '25 min', sla_due_at=now()+interval '5 min'
    WHERE business_ref='LOAD-ATRISK-204';
  UPDATE txn_current SET last_event_at=now()-interval '25 min', sla_due_at=now()+interval '5 min'
    WHERE business_ref='LOAD-ATRISK-204';
END;
$$ LANGUAGE plpgsql;

SELECT refresh_demo_ops();

-- If pg_cron becomes available (created in the 'postgres' db on Neon), drive it live:
--   SELECT cron.schedule_in_database('cockpit-ops','* * * * *','SELECT refresh_demo_ops()','neondb');
--   SELECT cron.schedule_in_database('cockpit-rollup','*/2 * * * *','SELECT refresh_txn_rollup()','neondb');
