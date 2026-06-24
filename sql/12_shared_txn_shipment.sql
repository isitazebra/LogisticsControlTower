-- Shared transaction <-> shipment contract (schema public, cockpit world).
--
-- WHY (Integration Command Center, dash 14, task 4 — view consistency):
-- The Shipment view and the Transaction view must be fed by the SAME underlying
-- data and present aligned columns, with the Shipment view a GROUPING/ROLLUP of
-- the SAME transaction rows the Transaction view shows.
--
-- The rich multi-message EDI choreography lives in mp_demo
-- (edi_anomaly_dashboard_dataset), already ported + partner-remapped onto the
-- cockpit partner set by sql/11 (public.vw_shipment_messages). Each message row
-- is keyed by transaction_id and linked to a shipment by shipment_id. That
-- message stream IS the transaction stream: one transaction = one EDI message
-- (204 load tender / 990 response / 214 status / 210 invoice / 997 ack), and a
-- shipment is simply the set of transactions sharing a shipment_id.
--
-- So we publish ONE shared detail view and derive the shipment worklist from it:
--   vw_txn_detail    -- one row per transaction (the shared source of truth)
--   vw_txn_shipment  -- one row per shipment = rollup of vw_txn_detail
--
-- Both the Transaction view and the Shipment view read these, guaranteeing a
-- single source and aligned columns. Partner names stay real (DHL/Maersk/Hapag/
-- Werner/Kroger/Target/Flextronics/Crocs) via the sql/11 cockpit_partner_map.

-- idempotent (drop dependent rollup first)
DROP VIEW IF EXISTS public.vw_txn_shipment;
DROP VIEW IF EXISTS public.vw_txn_detail;

-- 1) SHARED transaction detail — one row per EDI message/transaction. ----------
-- This is the single source feeding BOTH views. Column names are aligned to the
-- Transaction view's LOB vocabulary (business_ref / doc_type / partner / status)
-- while retaining the shipment grouping key (shipment_id) for consolidation.
CREATE VIEW public.vw_txn_detail AS
SELECT
  d.shipment_id,                              -- consolidation key (groups txns)
  d.transaction_id        AS business_ref,    -- the transaction reference
  d.partner,
  d.protocol,
  d.transport_mode,
  d.doc_type,
  d.direction,
  d.transaction_timestamp AS event_time,
  d.processing_status     AS status,
  d.ack_required,
  d.ack_received,
  d.ack_timestamp,
  d.control_number,
  d.error_code,
  d.error_description
FROM public.vw_shipment_messages d;

-- 2) SHIPMENT consolidation — one row per shipment, rolled up from the SAME
-- transaction rows above. Columns mirror the transaction detail (partner /
-- protocol / transport_mode / doc-type rollups / status / ack / error counts)
-- so the two views read as aligned grouping levels of one dataset. ------------
CREATE VIEW public.vw_txn_shipment AS
SELECT
  t.shipment_id,
  max(t.partner)                                              AS partner,
  max(t.protocol)                                             AS protocol,
  max(t.transport_mode)                                       AS transport_mode,
  count(*)                                                    AS total_messages,
  count(DISTINCT t.doc_type)                                  AS distinct_doc_types,
  count(*) FILTER (WHERE t.doc_type='204')                    AS cnt_204,
  count(*) FILTER (WHERE t.doc_type='990')                    AS cnt_990,
  count(*) FILTER (WHERE t.doc_type='214')                    AS cnt_214,
  count(*) FILTER (WHERE t.doc_type='210')                    AS cnt_210,
  count(*) FILTER (WHERE t.doc_type='997')                    AS cnt_997,
  -- choreography: a complete flow has the load tender (204) and a response (990)
  (bool_or(t.doc_type='204') AND bool_or(t.doc_type='990'))   AS choreography_complete,
  CASE WHEN bool_or(t.doc_type='204') AND bool_or(t.doc_type='990')
       THEN 'complete' ELSE 'incomplete' END                 AS choreography_status,
  count(*) FILTER (WHERE t.ack_required)                      AS ack_required_cnt,
  count(*) FILTER (WHERE t.ack_received)                      AS ack_received_cnt,
  count(*) FILTER (WHERE t.ack_required AND NOT t.ack_received) AS ack_pending,
  count(*) FILTER (WHERE t.error_code IS NOT NULL AND t.error_code <> '') AS error_cnt,
  count(*) FILTER (WHERE t.status='rejected')                 AS rejected_cnt,
  count(*) FILTER (WHERE t.status='duplicate')                AS duplicate_cnt,
  max(CASE WHEN t.status IN ('rejected','duplicate') OR
               (t.error_code IS NOT NULL AND t.error_code <> '') THEN 1 ELSE 0 END)
                                                              AS has_exception,
  min(t.event_time)                                           AS first_msg_ts,
  max(t.event_time)                                           AS last_msg_ts
FROM public.vw_txn_detail t
GROUP BY t.shipment_id;
