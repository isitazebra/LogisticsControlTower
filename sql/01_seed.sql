-- ============================================================================
-- Integration Cockpit — DEMO SEED  (stands in for NiFi; run AFTER 00_schema.sql)
-- Populates every table the dashboard reads, including the edge cases each
-- acceptance criterion tests. No NiFi required.
--   Bulk volume default ~300k rows; bump generate_series to 2-5M to load-test
--   the < 2s perf target.
-- ============================================================================

-- 0. clean (idempotent for re-runs)
TRUNCATE txn_events, txn_files, txn_current, txn_rollup_hourly,
         endpoint_health, expected_feeds, monitor_heartbeat, pipeline_health,
         sla_rules, diagnostic_rules, deploys, partner_penalty RESTART IDENTITY;

-- ============================================================================
-- 1. BULK VOLUME  (~300k rows: mostly healthy, small fail/reject/dup,
--    prod + uat, EDI ~74% / API ~26%, spread over 60 days)
-- ============================================================================
INSERT INTO txn_events (event_time, interchange_id, business_ref, environment, lob, partner,
  channel, protocol, direction, doc_type, stage, status, terminal, sla_due_at, value_usd, kchar, control_number)
SELECT
  now() - (random()*60||' days')::interval,
  'ICID'||(g/10),                      -- ~10 txns per parent file
  'REF'||g,
  CASE WHEN random()<0.12 THEN 'uat' ELSE 'prod' END,
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
FROM generate_series(1,300000) g;

-- ============================================================================
-- 2. FILE EDGE CASES (txn_files parent grain) — incoming & outgoing
-- ============================================================================
INSERT INTO txn_files (interchange_id, file_name, environment, partner, channel, protocol, direction,
  received_at, completed_at, status, reason_category, declared_txn_count, isa_control, gs_control, kchar) VALUES
 -- (a) declares 200, only 187 children seeded below -> 13 lost INSIDE the file
 ('ICID-KROGER-0815','KROGER_204_0815.edi','prod','Kroger','sftp','edi','in',
   now()-interval '40 min', now()-interval '38 min','parsed',NULL,200,'000012845','004521',62),
 -- (b) rejected at receipt, BEFORE parse (malformed) -> visible with no children
 ('ICID-AFNEWCO-1','AFNEWCO_INV.csv','prod','AF Newco','sftp','edi','in',
   now()-interval '44 min', now()-interval '44 min','rejected','bad_input_file',NULL,NULL,NULL,180),
 -- (c) clean outgoing
 ('ICID-MAERSK-856','MAERSK_856_OUT.x12','prod','Maersk','as2','edi','out',
   now()-interval '46 min', now()-interval '45 min','delivered',NULL,48,'000099120','007781',24),
 ('ICID-WERNER-990','WERNER_990_OUT.edi','prod','Werner','sftp','edi','out',
   now()-interval '48 min', now()-interval '47 min','delivered',NULL,12,'000077431','003310',8);

-- (a) children: 187 of the declared 200
INSERT INTO txn_events (event_time, interchange_id, business_ref, environment, lob, partner,
  channel, protocol, direction, doc_type, stage, status, reason_category, terminal, sla_due_at, kchar, control_number)
SELECT now()-interval '39 min','ICID-KROGER-0815','LOAD-882'||lpad(c::text,3,'0'),'prod','ground','Kroger',
  'sftp','edi','in','204','received',
  CASE WHEN c=33 THEN 'failed' ELSE 'ok' END,             -- one visible transaction failure too
  CASE WHEN c=33 THEN 'mapping_defect' END,
  CASE WHEN c=33 THEN false ELSE true END, now(),0.3,'CNK'||c
FROM generate_series(1,187) c;

-- ============================================================================
-- 3. TRANSACTION EDGE CASES (searchable fixed refs for the demo)
-- ============================================================================
-- multi-stage history for the lookup demo (HAWB failing at customs)
INSERT INTO txn_events (event_time, interchange_id, business_ref, environment, lob, partner, channel, protocol,
  direction, doc_type, stage, status, reason_category, terminal, value_usd, kchar, control_number) VALUES
 (now()-interval '2 hour','ICID-AIR-1','HAWB-12482907','prod','air','DHL','as2','edi','in','HAWB','received','ok',NULL,false,84000,12,'CNA1'),
 (now()-interval '118 min','ICID-AIR-1','HAWB-12482907','prod','air','DHL','as2','edi','in','HAWB','validated','ok',NULL,false,84000,12,'CNA1'),
 (now()-interval '116 min','ICID-AIR-1','HAWB-12482907','prod','air','DHL','as2','edi','in','HAWB','transformed','ok',NULL,false,84000,12,'CNA1'),
 (now()-interval '114 min','ICID-AIR-1','HAWB-12482907','prod','air','DHL','as2','edi','in','HAWB','customs','failed','bad_input_file',false,84000,12,'CNA1');

-- at-risk 204 with NO matching 990 (25 min in, 30-min SLA) -> Q10 at_risk + alert
INSERT INTO txn_events (event_time, interchange_id, business_ref, environment, lob, partner, channel, protocol,
  direction, doc_type, stage, status, terminal, sla_due_at, value_usd, kchar, control_number) VALUES
 (now()-interval '25 min','ICID-GRD-9','LOAD-ATRISK-204','prod','ground','Werner','sftp','edi','in','204','received','ok',false, now()+interval '5 min',12400,0.3,'CNR9');

-- ============================================================================
-- 4. CURRENT STATE  (derive from latest event per ref, then override edges)
-- ============================================================================
INSERT INTO txn_current (business_ref, environment, lob, partner, channel, protocol, doc_type,
  current_stage, current_status, first_event_at, last_event_at, terminal_at, sla_due_at, value_usd, terminal)
SELECT DISTINCT ON (business_ref) business_ref, environment, lob, partner, channel, protocol, doc_type,
  stage, status, min(event_time) OVER (PARTITION BY business_ref), event_time,
  CASE WHEN terminal THEN event_time END, sla_due_at, value_usd, terminal
FROM txn_events WHERE business_ref IS NOT NULL
ORDER BY business_ref, event_time DESC
ON CONFLICT (business_ref) DO NOTHING;

-- stuck flow (not terminal, aged) -> Q1 stuck
INSERT INTO txn_current (business_ref, environment, lob, partner, channel, protocol, doc_type,
  current_stage, current_status, first_event_at, last_event_at, sla_due_at, value_usd, terminal)
VALUES ('LOAD-STUCK-001','prod','ground','Werner','mq','edi','204','transform','ok',
  now()-interval '50 min', now()-interval '45 min', now()-interval '15 min', 12400, false)
ON CONFLICT (business_ref) DO UPDATE SET terminal=false, last_event_at=EXCLUDED.last_event_at, current_stage='transform';

-- replayed AND re-failed -> Q8 + Q11 re-failure
UPDATE txn_current SET replayed=true, replayed_at=now()-interval '20 min', replay_count=2,
  current_status='failed', terminal=false
WHERE business_ref='HAWB-12482907';

-- ============================================================================
-- 5. OPERATIONAL TABLES (Q1) — the active-sweep signals
-- ============================================================================
INSERT INTO endpoint_health (channel, endpoint, partner, environment, status, last_ok_at, cert_expires_at) VALUES
 ('sftp','crocs-edi','Crocs','prod','down',      now()-interval '22 min', (now()+interval '5 day')::date),   -- down + cert 5d
 ('api','rxo-track','RXO','prod','degraded',     now()-interval '2 min',  (now()+interval '3 day')::date),   -- 401s + token 3d
 ('as2','maersk-as2','Maersk','prod','up',       now()-interval '20 sec', (now()+interval '11 day')::date),
 ('sftp','target-sftp','Target','prod','up',     now()-interval '15 sec', (now()+interval '120 day')::date),
 ('sftp','crocs-edi-uat','Crocs','uat','up',     now()-interval '30 sec', (now()+interval '90 day')::date);

INSERT INTO expected_feeds (partner, doc_type, channel, environment, expected_next_at, grace_minutes, last_seen_at) VALUES
 ('Werner','204','sftp','prod', now()-interval '35 min', 15, now()-interval '95 min'),  -- MISSING
 ('Kroger','856','sftp','prod', now()-interval '2 hour', 30, now()-interval '26 hour'), -- MISSING
 ('Maersk','214','sftp','prod', now()+interval '40 min', 30, now()-interval '20 min');  -- healthy

INSERT INTO monitor_heartbeat (monitor_name, channel, environment, last_run_at, expected_interval_sec) VALUES
 ('van-liveness','van','prod', now()-interval '18 min', 300),   -- STALE -> sweep-integrity catch
 ('sftp-liveness','sftp','prod', now()-interval '20 sec', 300),
 ('as2-liveness','as2','prod', now()-interval '25 sec', 300),
 ('api-liveness','api','prod', now()-interval '15 sec', 300),
 ('mq-depth','mq','prod', now()-interval '10 sec', 120);

INSERT INTO pipeline_health (pipeline, environment, state, queue_depth, mq_depth, consume_rate, last_consumed_at) VALUES
 ('walgreens-tl','prod','running', 20, 20, 0,    now()-interval '40 min'),  -- HUNG (running, queue>0, rate 0)
 ('air-main','prod','running',     12, 0,  240,  now()),
 ('ocean-main','prod','running',    4, 0,  180,  now()),
 ('uat-ground','uat','running',     0, 0,  0,    now()-interval '3 hour');  -- uat idle

-- ============================================================================
-- 6. CONFIG  (SLA rules, resolution KB, deploys, penalties)
-- ============================================================================
INSERT INTO sla_rules(name,environment,partner,trigger_doc_type,trigger_direction,response_doc_type,response_direction,threshold_minutes) VALUES
 ('204->990 tender response','prod',NULL,'204','in','990','out',30),
 ('850->855 PO ack','prod',NULL,'850','in','855','out',60),
 ('inbound->997 func ack','prod',NULL,NULL,'in','997','out',15),
 ('204->214 pickup milestone','prod',NULL,'204','in','214','in',240);

INSERT INTO diagnostic_rules(partner, reason_category, error_code, likely_cause, suggested_action, runbook_url) VALUES
 (NULL,'bad_input_file',NULL,'Inbound file failed structural validation','Return to partner with the validation report; do not reprocess','https://runbook/bad-input'),
 (NULL,'mapping_defect',NULL,'A mapping rule did not match the received structure','Check the map version for this partner/doc; fix and reprocess from transform','https://runbook/mapping'),
 (NULL,'connectivity',NULL,'Endpoint unreachable or auth rejected','Verify endpoint, rotate credential if expired, then redeliver','https://runbook/connectivity'),
 ('Crocs','connectivity','AUTH','SFTP key rotated by partner','Import the new Crocs SFTP key and re-test','https://runbook/crocs-key');

INSERT INTO deploys(deployed_at, component, note) VALUES
 (now()-interval '42 min','walgreens-tl pipeline','weekend upgrade — correlate with hung pipeline');

INSERT INTO partner_penalty(partner, doc_type, penalty_usd) VALUES
 ('Werner','204',250),('Maersk','856',400),('Kroger','856',300);

-- ============================================================================
-- 7. BUILD THE ROLLUP from events (then schedule the incremental refresh)
-- ============================================================================
INSERT INTO txn_rollup_hourly
SELECT date_trunc('hour', event_time), environment, lob, partner, channel, protocol, direction, doc_type, status,
  count(*), sum(value_usd), sum(kchar),
  count(*) FILTER (WHERE status='failed'), count(*) FILTER (WHERE status='rejected'),
  count(*) FILTER (WHERE status='duplicate'),
  count(*) FILTER (WHERE sla_due_at < now() AND NOT terminal)
FROM txn_events
GROUP BY 1,2,3,4,5,6,7,8,9
ON CONFLICT DO NOTHING;

-- DONE. The dashboard now renders fully on this seed. Replace with NiFi later.
