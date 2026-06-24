-- 14_consignment_views.sql
-- ===========================================================================
-- SINGLE DATA WORLD: re-source the Shipment/Transaction tabs onto the SAME
-- transactions as every other tab (public.txn_events, the cockpit contract).
--
-- Background: the old Shipment view ran on edi_anomaly_dashboard_dataset
-- (a separate ~414k-row transport-only set: 204/990/214/210), while Home/EDI/
-- API/Exceptions run on public.txn_events (~300k rows: 850/810/856/204/214/
-- 990/997). Two different transaction populations -> totals never reconciled.
--
-- txn_events has no shipment_id and no message-correlation key, so a literal
-- "shipment" cannot be reconstructed. The one real grouping that exists is
-- interchange_id (the EDI transmission/consignment): ~30k interchanges, ~10
-- messages each. We therefore model the tab as a CONSIGNMENT (transmission)
-- view: per interchange, did the expected document set arrive and get a
-- functional acknowledgment (997) with no failed/rejected messages?
--
-- Completeness contract:
--   complete  := received a 997 functional ack AND zero failed/rejected msgs
--   missing ack := no 997 present (and no exceptions)
--   exceptions  := one or more failed/rejected messages
-- ===========================================================================

-- One row per CONSIGNMENT (interchange) ------------------------------------
CREATE OR REPLACE VIEW public.vw_consignment AS
SELECT
    interchange_id                                          AS consignment_id,
    mode() WITHIN GROUP (ORDER BY partner)                  AS partner,
    mode() WITHIN GROUP (ORDER BY protocol)                 AS protocol,
    mode() WITHIN GROUP (ORDER BY lob)                      AS lob,
    mode() WITHIN GROUP (ORDER BY channel)                  AS channel,
    min(event_time)                                         AS first_msg_ts,
    max(event_time)                                         AS last_msg_ts,
    count(*)                                                AS total_messages,
    count(DISTINCT doc_type)                                AS distinct_doc_types,
    count(DISTINCT partner)                                 AS partner_count,
    bool_or(doc_type = '850')                               AS has_order,
    bool_or(doc_type = '856')                               AS has_asn,
    bool_or(doc_type = '810')                               AS has_invoice,
    bool_or(doc_type = '204')                               AS has_tender,
    bool_or(doc_type = '990')                               AS has_response,
    bool_or(doc_type = '214')                               AS has_status,
    bool_or(doc_type = '997')                               AS has_ack,
    count(*) FILTER (WHERE status IN ('failed','rejected')) AS exception_cnt,
    count(*) FILTER (WHERE status = 'duplicate')            AS duplicate_cnt,
    count(*) FILTER (WHERE status = 'ok')                   AS ok_cnt,
    sum(value_usd)                                          AS value_usd,
    (bool_or(doc_type = '997')
       AND count(*) FILTER (WHERE status IN ('failed','rejected')) = 0)
                                                            AS complete,
    CASE
        WHEN count(*) FILTER (WHERE status IN ('failed','rejected')) > 0
            THEN 'exceptions'
        WHEN NOT bool_or(doc_type = '997')
            THEN 'missing ack'
        ELSE 'complete'
    END                                                     AS completeness_status
FROM public.txn_events
WHERE interchange_id IS NOT NULL
GROUP BY interchange_id;

-- Message-level detail (the SAME rows the rollup consolidates) --------------
-- Feeds BOTH the Consignment drill-down and the Transaction view, so the two
-- tabs are a rollup / detail pair on one source.
CREATE OR REPLACE VIEW public.vw_consignment_detail AS
SELECT
    interchange_id      AS consignment_id,
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
