-- 14_consignment_views.sql
-- ===========================================================================
-- SHIPMENT = ORDER world.  One transaction population for every tab.
--
-- Per the data contract (gen_shipment_world.py): each ORDER (shipment_id =
-- ORD-NNNNNN, one interchange_id) has a clean lifecycle and only four
-- message types:
--   204  order / load tender   (exactly one, opens the order)
--   990  order confirmation    (exactly one)
--   214  order update          (one or more)
--   210  invoice               (exactly one; absent while the order is open)
--
-- All dimensions are constant within an order and value_usd lives on the 204
-- order, so totals/splits reconcile: sum(value_usd) == total order value,
-- total messages == #204 + #990 + #214 + #210, and #orders == #204 == #990.
--
-- Completeness contract (lifecycle-based):
--   complete     := invoice (210) issued AND zero failed/rejected messages
--   in progress  := no invoice yet AND zero failed/rejected
--   exceptions   := one or more failed/rejected messages
--
-- SLA: sla_breached := the order has a message that is overdue and not terminal
-- (sla_due_at < now() AND NOT terminal) -- the same test the rollup aggregates
-- into breached_count, so the Shipment view and the SLA tab agree.
-- ===========================================================================

DROP VIEW IF EXISTS public.vw_consignment;
DROP VIEW IF EXISTS public.vw_consignment_detail;
DROP VIEW IF EXISTS public.vw_shipment CASCADE;
DROP VIEW IF EXISTS public.vw_shipment_detail CASCADE;

-- One row per ORDER (shipment) ---------------------------------------------
CREATE OR REPLACE VIEW public.vw_shipment AS
SELECT
    interchange_id                                          AS shipment_id,
    mode() WITHIN GROUP (ORDER BY partner)                  AS partner,
    mode() WITHIN GROUP (ORDER BY protocol)                 AS protocol,
    mode() WITHIN GROUP (ORDER BY lob)                      AS lob,
    mode() WITHIN GROUP (ORDER BY channel)                  AS channel,
    mode() WITHIN GROUP (ORDER BY environment)              AS environment,
    min(event_time)                                         AS first_msg_ts,
    max(event_time)                                         AS last_msg_ts,
    count(*)                                                AS total_messages,
    count(*) FILTER (WHERE doc_type = '214')                AS update_count,
    bool_or(doc_type = '204')                               AS has_order,
    bool_or(doc_type = '990')                               AS has_confirmation,
    bool_or(doc_type = '210')                               AS has_invoice,
    bool_or(sla_due_at < now() AND NOT terminal)            AS sla_breached,
    count(*) FILTER (WHERE status IN ('failed','rejected')) AS exception_cnt,
    count(*) FILTER (WHERE status = 'duplicate')            AS duplicate_cnt,
    count(*) FILTER (WHERE status = 'ok')                   AS ok_cnt,
    -- Per-leg signal (Transaction view, reference shape): one transaction has an
    -- inbound side (received by the platform) and an outbound side (emitted).
    -- Counts + the worst-of status per leg (failed > rejected > duplicate > ok)
    -- + last activity per leg, so the master grid shows both legs on one row.
    count(*) FILTER (WHERE direction = 'in')                AS inbound_count,
    count(*) FILTER (WHERE direction = 'out')               AS outbound_count,
    CASE max(CASE status WHEN 'failed' THEN 4 WHEN 'rejected' THEN 3
                         WHEN 'duplicate' THEN 2 WHEN 'ok' THEN 1 ELSE 0 END)
              FILTER (WHERE direction = 'in')
         WHEN 4 THEN 'failed' WHEN 3 THEN 'rejected'
         WHEN 2 THEN 'duplicate' WHEN 1 THEN 'ok' ELSE NULL
    END                                                     AS inbound_status,
    CASE max(CASE status WHEN 'failed' THEN 4 WHEN 'rejected' THEN 3
                         WHEN 'duplicate' THEN 2 WHEN 'ok' THEN 1 ELSE 0 END)
              FILTER (WHERE direction = 'out')
         WHEN 4 THEN 'failed' WHEN 3 THEN 'rejected'
         WHEN 2 THEN 'duplicate' WHEN 1 THEN 'ok' ELSE NULL
    END                                                     AS outbound_status,
    max(event_time) FILTER (WHERE direction = 'in')         AS last_in_ts,
    max(event_time) FILTER (WHERE direction = 'out')        AS last_out_ts,
    sum(value_usd)                                          AS value_usd,
    (bool_or(doc_type = '210')
       AND count(*) FILTER (WHERE status IN ('failed','rejected')) = 0)
                                                            AS complete,
    CASE
        WHEN count(*) FILTER (WHERE status IN ('failed','rejected')) > 0
            THEN 'exceptions'
        WHEN NOT bool_or(doc_type = '210')
            THEN 'in progress'
        ELSE 'complete'
    END                                                     AS completeness_status
FROM public.txn_events
WHERE interchange_id IS NOT NULL
GROUP BY interchange_id;

-- Message-level detail (the SAME rows the rollup consolidates) --------------
-- Feeds BOTH the Shipment drill-down and the Transaction view, so the two tabs
-- are a rollup / detail pair on one source.
--
-- TRANSLATE-AND-FORWARD model: every message has TWO sides — an incoming file +
-- payload (received) and an outgoing file + payload (emitted, translated). The
-- EDI/X12 side always faces the PARTNER; the JSON side always faces the internal
-- SYSTEM. Which is incoming vs outgoing flips with direction:
--   direction='in'  (partner -> platform): incoming = EDI, outgoing = JSON
--   direction='out' (platform -> partner): incoming = JSON, outgoing = EDI
-- Files mirror the reference shape: EDI = <doc_type>_<partnercode>_<ref>_<ts>.edi,
-- JSON = <syscode>_<doc_type>_<ref>_<ts>.json. payload bodies are populated by
-- sql/06_payloads (no live writer yet; NiFi fills the equivalents later).
CREATE OR REPLACE VIEW public.vw_shipment_detail AS
SELECT
    interchange_id      AS shipment_id,
    business_ref,
    partner,
    protocol,
    lob,
    channel,
    doc_type,
    direction,
    event_time,
    status,
    reason_category,
    error_code,
    control_number,
    value_usd,
    CASE WHEN direction = 'in' THEN edi_file ELSE json_file END        AS incoming_file,
    CASE WHEN direction = 'in' THEN json_file ELSE edi_file END        AS outgoing_file,
    CASE WHEN direction = 'in' THEN edi_payload ELSE json_payload END  AS incoming_payload,
    CASE WHEN direction = 'in' THEN json_payload ELSE edi_payload END  AS outgoing_payload
FROM (
    SELECT *,
      doc_type || '_' || pcode   || '_' || business_ref || '_'
        || to_char(event_time, 'YYYYMMDDHH24MISSMS') || '.edi'  AS edi_file,
      syscode  || '_' || doc_type || '_' || business_ref || '_'
        || to_char(event_time, 'YYYYMMDDHH24MISSMS') || '.json' AS json_file
    FROM (
        SELECT *,
          left(upper(regexp_replace(partner, '[^A-Za-z0-9]', '', 'g')), 10)        AS pcode,
          left(upper(regexp_replace(coalesce(lob,'INTSYS'), '[^A-Za-z0-9]', '', 'g')), 8) AS syscode
        FROM public.txn_events
    ) t
) f;
