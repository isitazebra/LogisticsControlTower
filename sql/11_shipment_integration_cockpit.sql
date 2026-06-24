-- Shipment Integration 360 — UNIFIED onto the cockpit world (schema public).
--
-- Why: the Integration Command Center is a single-world dashboard (db3/public)
-- so partner names + native filters stay consistent across every tab. The rich
-- shipment choreography model lives only in the mp_demo schema
-- (edi_anomaly_dashboard_dataset), and it uses a DIFFERENT partner universe
-- (Amazon/CVS/Lowes…). These views port that vetted choreography/ACK/response-SLA
-- logic into the public schema and REMAP the partner onto the cockpit partner set
-- (DHL/Maersk/Hapag/Werner/Kroger/Target/Flextronics/Crocs), so the dashboard's
-- single Partner filter cross-filters the Shipment view like every other tab.
--
-- SCOPE GUARDRAIL (unchanged): integration lens only — choreography completeness,
-- 204->990 response latency vs partner expectation, ACK coverage, flow anomalies.
-- Physical fields (promised/actual delivery) are read-only CONTEXT; we never
-- compute on-time-delivery / transit / dwell, which belong to the TMS.

-- Deterministic mp_demo partner_id -> cockpit partner remap. Covers all 8 cockpit
-- partners; preserves the one real overlap (P007 Kroger -> Kroger).
CREATE OR REPLACE VIEW public.cockpit_partner_map AS
SELECT * FROM (VALUES
  ('P001','Maersk'), ('P002','Target'),  ('P003','Hapag'),
  ('P004','Werner'), ('P005','Flextronics'), ('P006','DHL'),
  ('P007','Kroger'), ('P008','Crocs'),  ('P009','DHL'),
  ('P010','Maersk')
) AS m(partner_id, partner);

-- idempotent
DROP VIEW IF EXISTS public.vw_shipment_integration;
DROP VIEW IF EXISTS public.vw_shipment_messages;
DROP VIEW IF EXISTS public.vw_shipment_journey;

-- 1) One row per shipment: the integration scorecard (cockpit-partnered) -------
CREATE VIEW public.vw_shipment_integration AS
SELECT
  COALESCE(m.partner, 'Unmapped')      AS partner,
  'prod'::text                         AS environment,
  'edi'::text                          AS protocol,
  s.shipment_id, s.transport_mode, s.priority, s.shipment_status,
  s.shipment_date, s.promised_delivery_date, s.actual_delivery_date,  -- context
  s.total_messages, s.cnt_204, s.cnt_990, s.cnt_214, s.cnt_210, s.cnt_997,
  s.has_204, s.has_990, s.has_214, s.has_997,
  s.missing_990, s.missing_214, s.ack_gap,
  s.choreography_status, s.choreography_complete,
  s.response_minutes, s.expected_204_990_minutes, s.response_sla_met,
  s.ack_required_cnt, s.ack_received_cnt, s.ack_pending,
  s.error_cnt, s.duplicate_cnt,
  s.anomaly_count, s.critical_anomaly_count, s.business_impact_amount,
  s.first_msg_ts, s.last_msg_ts
FROM edi_anomaly_dashboard_dataset.vw_shipment_integration_summary s
LEFT JOIN public.cockpit_partner_map m ON m.partner_id = s.partner_id;

-- 2) One row per EDI message — per-shipment message-set drill ------------------
CREATE VIEW public.vw_shipment_messages AS
SELECT
  COALESCE(m.partner, 'Unmapped')      AS partner,
  'edi'::text                          AS protocol,
  d.shipment_id, d.transaction_id,
  d.transaction_type                   AS doc_type,
  d.transaction_direction              AS direction,
  d.transaction_timestamp,
  d.processing_status, d.ack_required, d.ack_received, d.ack_timestamp,
  d.error_code, d.error_description, d.control_number, d.transport_mode
FROM edi_anomaly_dashboard_dataset.vw_shipment_message_detail d
LEFT JOIN public.cockpit_partner_map m ON m.partner_id = d.partner_id;

-- 3) One row per status step — per-shipment status journey --------------------
CREATE VIEW public.vw_shipment_journey AS
SELECT
  COALESCE(m.partner, 'Unmapped')      AS partner,
  j.shipment_id, j.status_code, j.status_timestamp, j.city, j.status_sequence
FROM edi_anomaly_dashboard_dataset.vw_shipment_journey_timeline j
LEFT JOIN public.cockpit_partner_map m ON m.partner_id = j.partner_id;
