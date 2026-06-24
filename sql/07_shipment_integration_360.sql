-- Shipment Integration 360 — integration-scoped views over the mp_demo reference
-- data (schema edi_anomaly_dashboard_dataset). These power a shipment-centric
-- drill on dashboard 12 (Shipment Anomaly Control Tower).
--
-- SCOPE GUARDRAIL: we are a *transient integration layer*, not a system of
-- record. These views describe the MESSAGE SET we processed per shipment and the
-- INTEGRATION health of that flow (choreography completeness, 204->990 response
-- latency vs partner expectation, ACK coverage, flow anomalies). Physical
-- fields (promised/actual delivery) are carried as read-only CONTEXT only — we
-- never compute on-time-delivery / transit / dwell KPIs, which belong to the TMS.

-- idempotent: column sets change between revisions, so drop before recreate.
DROP VIEW IF EXISTS edi_anomaly_dashboard_dataset.vw_shipment_integration_summary;
DROP VIEW IF EXISTS edi_anomaly_dashboard_dataset.vw_shipment_message_detail;

-- 1) One row per shipment: the integration scorecard --------------------------
CREATE OR REPLACE VIEW edi_anomaly_dashboard_dataset.vw_shipment_integration_summary AS
WITH tx AS (
  SELECT shipment_id,
    COUNT(*)                                                   AS total_messages,
    COUNT(*) FILTER (WHERE transaction_type='204')             AS cnt_204,
    COUNT(*) FILTER (WHERE transaction_type='990')             AS cnt_990,
    COUNT(*) FILTER (WHERE transaction_type='214')             AS cnt_214,
    COUNT(*) FILTER (WHERE transaction_type='210')             AS cnt_210,
    COUNT(*) FILTER (WHERE transaction_type='997')             AS cnt_997,
    COUNT(*) FILTER (WHERE ack_required)                       AS ack_required_cnt,
    COUNT(*) FILTER (WHERE ack_required AND ack_received)      AS ack_received_cnt,
    COUNT(*) FILTER (WHERE processing_status='rejected'
                        OR error_code IS NOT NULL)             AS error_cnt,
    COUNT(*) FILTER (WHERE processing_status='duplicate')      AS duplicate_cnt,
    MIN(transaction_timestamp)                                 AS first_msg_ts,
    MAX(transaction_timestamp)                                 AS last_msg_ts,
    MIN(transaction_timestamp) FILTER (WHERE transaction_type='204') AS ts_204,
    MIN(transaction_timestamp) FILTER (WHERE transaction_type='990') AS ts_990
  FROM edi_anomaly_dashboard_dataset.edi_transactions
  GROUP BY shipment_id
),
anom AS (
  SELECT shipment_id,
    COUNT(*)                                                   AS anomaly_count,
    COUNT(*) FILTER (WHERE severity='Critical')                AS critical_anomaly_count,
    COUNT(*) FILTER (WHERE anomaly_type='MISSING_990')         AS miss_990_anom,
    COUNT(*) FILTER (WHERE anomaly_type='MISSING_214')         AS miss_214_anom,
    COUNT(*) FILTER (WHERE anomaly_type='MISSING_997')         AS miss_997_anom,
    COALESCE(SUM(business_impact_amount),0)                    AS business_impact_amount
  FROM edi_anomaly_dashboard_dataset.anomaly_registry
  GROUP BY shipment_id
)
SELECT
  s.shipment_id, s.partner_id, p.partner_name, s.transport_mode, s.priority,
  s.shipment_status, s.shipment_date,
  s.promised_delivery_date, s.actual_delivery_date,           -- context only
  COALESCE(tx.total_messages,0) AS total_messages,
  COALESCE(tx.cnt_204,0) AS cnt_204, COALESCE(tx.cnt_990,0) AS cnt_990,
  COALESCE(tx.cnt_214,0) AS cnt_214, COALESCE(tx.cnt_210,0) AS cnt_210,
  COALESCE(tx.cnt_997,0) AS cnt_997,
  -- choreography completeness (integration ownership) ------------------------
  (COALESCE(tx.cnt_204,0)>0) AS has_204,
  (COALESCE(tx.cnt_990,0)>0) AS has_990,
  (COALESCE(tx.cnt_214,0)>0) AS has_214,
  (COALESCE(tx.cnt_997,0)>0) AS has_997,
  -- gap signals: the system's own MISSING_* detections, backed by raw presence
  -- and ACK flags (a discrete 997 message is NOT required when ACKs are flagged
  -- received - only 305 shipments have a true ACK gap vs 22k lacking a 997 row).
  CASE WHEN COALESCE(a.miss_990_anom,0)>0 OR (COALESCE(tx.cnt_204,0)>0 AND COALESCE(tx.cnt_990,0)=0) THEN 1 ELSE 0 END AS missing_990,
  CASE WHEN COALESCE(a.miss_214_anom,0)>0 OR COALESCE(tx.cnt_214,0)=0 THEN 1 ELSE 0 END AS missing_214,
  CASE WHEN COALESCE(a.miss_997_anom,0)>0 OR GREATEST(COALESCE(tx.ack_required_cnt,0)-COALESCE(tx.ack_received_cnt,0),0)>0 THEN 1 ELSE 0 END AS ack_gap,
  CASE
    WHEN COALESCE(tx.total_messages,0)=0 THEN 'No messages'
    WHEN COALESCE(a.miss_990_anom,0)>0 OR (COALESCE(tx.cnt_204,0)>0 AND COALESCE(tx.cnt_990,0)=0) THEN 'Missing 990'
    WHEN COALESCE(a.miss_214_anom,0)>0 OR COALESCE(tx.cnt_214,0)=0 THEN 'Missing 214'
    WHEN COALESCE(a.miss_997_anom,0)>0 OR GREATEST(COALESCE(tx.ack_required_cnt,0)-COALESCE(tx.ack_received_cnt,0),0)>0 THEN 'ACK gap'
    ELSE 'Complete'
  END AS choreography_status,
  CASE WHEN COALESCE(tx.total_messages,0)>0
            AND COALESCE(a.miss_990_anom,0)=0 AND NOT (COALESCE(tx.cnt_204,0)>0 AND COALESCE(tx.cnt_990,0)=0)
            AND COALESCE(a.miss_214_anom,0)=0 AND COALESCE(tx.cnt_214,0)>0
            AND COALESCE(a.miss_997_anom,0)=0 AND GREATEST(COALESCE(tx.ack_required_cnt,0)-COALESCE(tx.ack_received_cnt,0),0)=0
       THEN 1 ELSE 0 END AS choreography_complete,
  -- 204 -> 990 response latency vs partner expectation (integration SLA) ------
  ROUND((EXTRACT(EPOCH FROM (tx.ts_990 - tx.ts_204))/60.0)::numeric, 1) AS response_minutes,
  p.expected_204_990_minutes,
  CASE WHEN tx.ts_204 IS NOT NULL AND tx.ts_990 IS NOT NULL
            AND EXTRACT(EPOCH FROM (tx.ts_990 - tx.ts_204))/60.0
                <= COALESCE(p.expected_204_990_minutes, 1e9)
       THEN 1 ELSE 0 END AS response_sla_met,
  -- acknowledgements (integration) -------------------------------------------
  COALESCE(tx.ack_required_cnt,0) AS ack_required_cnt,
  COALESCE(tx.ack_received_cnt,0) AS ack_received_cnt,
  GREATEST(COALESCE(tx.ack_required_cnt,0)-COALESCE(tx.ack_received_cnt,0),0) AS ack_pending,
  COALESCE(tx.error_cnt,0)        AS error_cnt,
  COALESCE(tx.duplicate_cnt,0)    AS duplicate_cnt,
  COALESCE(a.anomaly_count,0)            AS anomaly_count,
  COALESCE(a.critical_anomaly_count,0)   AS critical_anomaly_count,
  COALESCE(a.business_impact_amount,0)   AS business_impact_amount,
  tx.first_msg_ts, tx.last_msg_ts
FROM edi_anomaly_dashboard_dataset.shipment_header s
LEFT JOIN edi_anomaly_dashboard_dataset.trading_partner_master p ON p.partner_id = s.partner_id
LEFT JOIN tx   ON tx.shipment_id   = s.shipment_id
LEFT JOIN anom a ON a.shipment_id  = s.shipment_id;

-- 2) One row per EDI message, for the per-shipment message-set drill ----------
CREATE OR REPLACE VIEW edi_anomaly_dashboard_dataset.vw_shipment_message_detail AS
SELECT
  t.shipment_id, t.transaction_id, t.transaction_type, t.transaction_direction,
  t.transaction_timestamp, t.processing_status,
  t.ack_required, t.ack_received, t.ack_timestamp,
  t.error_code, t.error_description, t.control_number,
  t.partner_id, p.partner_name, s.transport_mode
FROM edi_anomaly_dashboard_dataset.edi_transactions t
LEFT JOIN edi_anomaly_dashboard_dataset.trading_partner_master p ON p.partner_id = t.partner_id
LEFT JOIN edi_anomaly_dashboard_dataset.shipment_header s        ON s.shipment_id = t.shipment_id;
