-- 16_sla_pairs.sql
-- ===========================================================================
-- PAIRWISE response-SLA on the single shipment world (public.txn_events).
--
-- Ports the reference "Response-SLA" model (cockpit_spec q10_sla_pairs: a
-- trigger doc_type -> response doc_type pair, an elapsed time, a threshold and a
-- met/at_risk/missed/pending state) onto the ORDER lifecycle. Every order
-- (interchange_id) emits one 204, one 990, 1..5 214 and (when closed) one 210,
-- so three response SLAs are well defined per order:
--
--   204 -> 990  order confirmation   threshold  240 min (4h)
--   204 -> 214  first order update   threshold 1440 min (24h)
--   204 -> 210  invoice (cycle time) threshold 4320 min (72h)
--
-- Long form: one row per (order, pair) so a single dataset feeds the pair KPIs,
-- the "compliance by pair type" stacked bar and the breach worklists. Reuses the
-- SAME now()-based overdue logic as the rest of the dashboard, so an open order
-- whose response is not yet due reads 'pending'/'at_risk' and a stale one 'missed'.
-- ===========================================================================
DROP VIEW IF EXISTS public.vw_sla_pairs CASCADE;

CREATE VIEW public.vw_sla_pairs AS
WITH o AS (
    SELECT interchange_id                                   AS shipment_id,
           mode() WITHIN GROUP (ORDER BY partner)           AS partner,
           mode() WITHIN GROUP (ORDER BY lob)               AS lob,
           mode() WITHIN GROUP (ORDER BY protocol)          AS protocol,
           min(event_time) FILTER (WHERE doc_type = '204')  AS t_order,
           min(event_time) FILTER (WHERE doc_type = '990')  AS t_conf,
           min(event_time) FILTER (WHERE doc_type = '214')  AS t_upd,
           min(event_time) FILTER (WHERE doc_type = '210')  AS t_inv,
           max(value_usd)                                   AS value_usd
    FROM public.txn_events
    WHERE interchange_id IS NOT NULL
    GROUP BY interchange_id
),
pairs AS (
    SELECT shipment_id, partner, lob, protocol, value_usd,
           '204→990 confirmation'::text AS pair, 1 AS pair_order,
           240  AS threshold_min, t_order AS trigger_at, t_conf AS response_at FROM o
    UNION ALL
    SELECT shipment_id, partner, lob, protocol, value_usd,
           '204→214 first update', 2,
           1440 AS threshold_min, t_order, t_upd  FROM o
    UNION ALL
    SELECT shipment_id, partner, lob, protocol, value_usd,
           '204→210 invoice', 3,
           4320 AS threshold_min, t_order, t_inv  FROM o
)
SELECT
    shipment_id, partner, lob, protocol, value_usd,
    pair, pair_order, threshold_min, trigger_at, response_at,
    round(extract(epoch FROM (response_at - trigger_at)) / 60)::int AS elapsed_min,
    CASE
        WHEN response_at IS NOT NULL
             AND extract(epoch FROM (response_at - trigger_at)) / 60 <= threshold_min
            THEN 'met'
        WHEN response_at IS NOT NULL
            THEN 'missed'
        WHEN now() - trigger_at > make_interval(mins => threshold_min)
            THEN 'missed'
        WHEN now() - trigger_at > make_interval(mins => (threshold_min * 0.8)::int)
            THEN 'at_risk'
        ELSE 'pending'
    END AS sla_state
FROM pairs
WHERE trigger_at IS NOT NULL;
