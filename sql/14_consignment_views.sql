-- 14_consignment_views.sql
-- ===========================================================================
-- SHIPMENT = ORDER world.  One transaction population for every tab.
--
-- Per the data contract (gen_shipment_world.py): each ORDER (shipment_id =
-- ORD-NNNNNN, one interchange_id) has a clean lifecycle and only three
-- message types:
--   990  order confirmation   (exactly one, opens the order)
--   214  order update         (one or more)
--   210  invoice              (exactly one; absent while the order is open)
--
-- All dimensions are constant within an order and value_usd lives on the 210
-- invoice, so totals/splits reconcile: sum(value_usd) == total order value,
-- total messages == #990 + #214 + #210, and #orders == #990.
--
-- Completeness contract (lifecycle-based):
--   complete     := invoice (210) issued AND zero failed/rejected messages
--   in progress  := no invoice yet AND zero failed/rejected
--   exceptions   := one or more failed/rejected messages
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
    bool_or(doc_type = '990')                               AS has_confirmation,
    bool_or(doc_type = '210')                               AS has_invoice,
    count(*) FILTER (WHERE status IN ('failed','rejected')) AS exception_cnt,
    count(*) FILTER (WHERE status = 'duplicate')            AS duplicate_cnt,
    count(*) FILTER (WHERE status = 'ok')                   AS ok_cnt,
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
    value_usd
FROM public.txn_events;
